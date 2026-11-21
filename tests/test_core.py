"""Tests for the core retry engine (run/call) using fast stub commands."""

import os
import sys
import time

import pytest

from retrier import RetryPolicy, call, run
from retrier.core import NOT_FOUND_EXIT_CODE, TIMEOUT_EXIT_CODE

PY = sys.executable


def _ok():
    return run([PY, "-c", "print('hi')"], policy=RetryPolicy(times=3),
               quiet=True)


def test_run_success_first_try():
    result = _ok()
    assert result.success is True
    assert result.exit_code == 0
    assert len(result.attempts) == 1
    assert result.attempts[0].reason == "ok"


def test_run_retries_until_success():
    # Fails twice (counter file), succeeds on the 3rd attempt.
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        state = os.path.join(tmp, "count")
        script = (
            "import os,sys; p=%r; n=int(open(p).read()) if os.path.exists(p) "
            "else 0; open(p,'w').write(str(n+1)); sys.exit(1 if n < 2 else 0)"
            % state
        )
        sleeps = []
        result = run([PY, "-c", script], policy=RetryPolicy(times=5, delay=0.01),
                     quiet=True, sleep=sleeps.append)
        assert result.success is True
        assert result.exit_code == 0
        assert len(result.attempts) == 3
        assert [a.exit_code for a in result.attempts] == [1, 1, 0]
        assert len(sleeps) == 2  # slept between attempts 1->2 and 2->3


def test_run_exhausts_attempts_and_keeps_last_exit_code():
    result = run([PY, "-c", "import sys; sys.exit(7)"],
                 policy=RetryPolicy(times=3, delay=0.01),
                 quiet=True, sleep=lambda s: None)
    assert result.success is False
    assert result.exit_code == 7
    assert len(result.attempts) == 3
    assert all(a.reason == "exit-code" for a in result.attempts)


def test_run_times_one_never_retries():
    calls = []
    result = run([PY, "-c", "import sys; sys.exit(1)"],
                 policy=RetryPolicy(times=1, delay=0.01),
                 quiet=True, sleep=calls.append)
    assert len(result.attempts) == 1
    assert calls == []


def test_run_retry_on_filters_exit_codes():
    # Exit 2 is not in the allow-list: no retry even with attempts left.
    result = run([PY, "-c", "import sys; sys.exit(2)"],
                 policy=RetryPolicy(times=5, delay=0.01, retry_on=(1,)),
                 quiet=True, sleep=lambda s: None)
    assert len(result.attempts) == 1
    assert result.exit_code == 2


def test_run_no_retry_on_filters_exit_codes():
    result = run([PY, "-c", "import sys; sys.exit(2)"],
                 policy=RetryPolicy(times=5, delay=0.01, no_retry_on=(2,)),
                 quiet=True, sleep=lambda s: None)
    assert len(result.attempts) == 1


def test_run_command_not_found_never_retries():
    result = run(["definitely-not-a-real-command-xyz"],
                 policy=RetryPolicy(times=5, delay=0.01),
                 quiet=True, sleep=lambda s: None)
    assert result.success is False
    assert result.exit_code == NOT_FOUND_EXIT_CODE
    assert len(result.attempts) == 1
    assert result.attempts[0].reason == "not-found"


def test_run_timeout_kills_attempt_and_retries():
    result = run([PY, "-c", "import time; time.sleep(5)"],
                 policy=RetryPolicy(times=2, delay=0.01, timeout=0.2),
                 quiet=True, sleep=lambda s: None)
    assert result.success is False
    assert result.exit_code == TIMEOUT_EXIT_CODE
    assert len(result.attempts) == 2
    assert all(a.timed_out for a in result.attempts)
    assert all(a.reason == "timeout" for a in result.attempts)


def test_run_timeout_eventually_succeeds():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        state = os.path.join(tmp, "count")
        script = (
            "import os,sys,time; p=%r; n=int(open(p).read()) if os.path.exists(p) "
            "else 0; open(p,'w').write(str(n+1)); "
            "time.sleep(5 if n == 0 else 0); sys.exit(0)" % state
        )
        result = run([PY, "-c", script],
                     policy=RetryPolicy(times=3, delay=0.01, timeout=0.3),
                     quiet=True, sleep=lambda s: None)
        assert result.success is True
        assert len(result.attempts) == 2


def test_run_until_output_retries_on_wrong_output(capsys):
    result = run([PY, "-c", "print('not ready yet')"],
                 policy=RetryPolicy(times=3, delay=0.01),
                 until_output="READY", quiet=True, sleep=lambda s: None)
    assert result.success is False
    assert len(result.attempts) == 3
    assert all(a.reason == "output-mismatch" for a in result.attempts)
    # Output is replayed, never swallowed.
    assert capsys.readouterr().out.count("not ready yet") == 3


def test_run_until_output_succeeds_on_match(capsys):
    result = run([PY, "-c", "print('READY to go')"],
                 policy=RetryPolicy(times=3, delay=0.01),
                 until_output="READY", quiet=True, sleep=lambda s: None)
    assert result.success is True
    assert len(result.attempts) == 1
    assert capsys.readouterr().out.count("READY to go") == 1


def test_run_until_output_beats_exit_zero(capsys):
    # Exit 0 alone is not success when --until-output is set and unmatched.
    result = run([PY, "-c", "print('hello')"],
                 policy=RetryPolicy(times=2, delay=0.01),
                 until_output="READY", quiet=True, sleep=lambda s: None)
    assert result.success is False


def test_run_max_time_stops_retrying():
    sleeps = []
    result = run([PY, "-c", "import sys; sys.exit(1)"],
                 policy=RetryPolicy(times=10, delay=5.0, max_time=0.05),
                 quiet=True, sleep=sleeps.append)
    assert result.success is False
    assert len(result.attempts) == 1  # budget already blown after attempt 1
    assert sleeps == []


def test_run_env_and_cwd(tmp_path):
    script = (
        "import os,sys; sys.exit(0 if os.environ.get('RETRIER_TEST') == 'yes' "
        "and os.path.basename(os.getcwd()) == 'workdir' else 3)"
    )
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    result = run([PY, "-c", script],
                 policy=RetryPolicy(times=1),
                 env={"RETRIER_TEST": "yes"}, cwd=str(workdir), quiet=True)
    assert result.success is True


def test_run_shell_mode():
    result = run("echo hello | grep hello",
                 policy=RetryPolicy(times=1), shell=True, quiet=True)
    assert result.success is True


def test_run_logs_attempts_to_stderr(capsys):
    run([PY, "-c", "import sys; sys.exit(1)"],
        policy=RetryPolicy(times=2, delay=0.01), sleep=lambda s: None)
    err = capsys.readouterr().err
    assert "attempt 1/2 failed" in err
    assert "retrying in" in err
    assert "attempt 2/2 failed" in err


def test_run_quiet_suppresses_logs(capsys):
    run([PY, "-c", "import sys; sys.exit(1)"],
        policy=RetryPolicy(times=2, delay=0.01), quiet=True,
        sleep=lambda s: None)
    assert capsys.readouterr().err == ""


def test_run_result_as_dict():
    result = _ok()
    data = result.as_dict()
    assert data["success"] is True
    assert data["exit_code"] == 0
    assert data["attempts"][0]["number"] == 1
    assert "total_duration_s" in data


# --- call() ---

def test_call_success():
    result = call(lambda: 42, policy=RetryPolicy(times=3), quiet=True)
    assert result.success is True
    assert result.value == 42
    assert len(result.attempts) == 1


def test_call_retries_on_exception():
    state = {"n": 0}

    def flaky():
        state["n"] += 1
        if state["n"] < 3:
            raise ValueError("boom %d" % state["n"])
        return "done"

    result = call(flaky, policy=RetryPolicy(times=5, delay=0.01),
                  quiet=True, sleep=lambda s: None)
    assert result.success is True
    assert result.value == "done"
    assert len(result.attempts) == 3


def test_call_gives_up_and_keeps_last_exception():
    def always():
        raise RuntimeError("nope")

    result = call(always, policy=RetryPolicy(times=2, delay=0.01),
                  quiet=True, sleep=lambda s: None)
    assert result.success is False
    assert isinstance(result.exception, RuntimeError)
    assert str(result.exception) == "nope"
    assert len(result.attempts) == 2


def test_call_retry_on_restricts_exception_types():
    def raises_key_error():
        raise KeyError("k")

    # KeyError is not a ValueError: no retry.
    result = call(raises_key_error, policy=RetryPolicy(times=4, delay=0.01),
                  retry_on=ValueError, quiet=True, sleep=lambda s: None)
    assert len(result.attempts) == 1
    assert isinstance(result.exception, KeyError)
