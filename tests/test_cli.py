"""End-to-end CLI tests: invoke the real `retrier` entry point."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = str(ROOT / "src")
PY = sys.executable

ENV = dict(os.environ, PYTHONPATH=SRC)


def cli(*args):
    return subprocess.run(
        [PY, "-m", "retrier", *args],
        capture_output=True, text=True, env=ENV, timeout=30,
    )


def test_cli_success_exit_zero():
    proc = cli("--times", "2", "--", PY, "-c", "print('ok')")
    assert proc.returncode == 0
    assert "ok" in proc.stdout


def test_cli_failure_keeps_exit_code():
    proc = cli("--times", "2", "--delay", "10ms", "--", PY, "-c",
               "import sys; sys.exit(7)")
    assert proc.returncode == 7
    assert "attempt 1/2" in proc.stderr
    assert "attempt 2/2" in proc.stderr


def test_cli_no_command_is_usage_error():
    proc = cli()
    assert proc.returncode == 2
    assert "no command given" in proc.stderr


def test_cli_command_without_double_dash():
    proc = cli("--times", "1", PY, "-c", "print('nodash')")
    assert proc.returncode == 0
    assert "nodash" in proc.stdout


def test_cli_invalid_duration_is_usage_error():
    proc = cli("--delay", "1d", "--", PY, "-c", "print(1)")
    assert proc.returncode == 2
    assert "invalid duration" in proc.stderr


def test_cli_invalid_times_is_error():
    proc = cli("--times", "0", "--", PY, "-c", "print(1)")
    assert proc.returncode == 2


def test_cli_json_summary():
    proc = cli("--times", "2", "--delay", "10ms", "--json", "--",
               PY, "-c", "import sys; sys.exit(3)")
    assert proc.returncode == 3
    assert proc.stdout == ""  # failing command printed nothing
    summary = json.loads(proc.stderr)
    assert summary["success"] is False
    assert summary["exit_code"] == 3
    assert len(summary["attempts"]) == 2
    assert summary["attempts"][0]["reason"] == "exit-code"


def test_cli_json_success():
    proc = cli("--times", "3", "--json", "--", PY, "-c", "print('done')")
    assert proc.returncode == 0
    assert proc.stdout.strip() == "done"  # command output stays on stdout
    summary = json.loads(proc.stderr)  # JSON summary goes to stderr
    assert summary["success"] is True
    assert summary["attempts"][0]["exit_code"] == 0


def test_cli_quiet_suppresses_status_lines():
    proc = cli("-q", "--times", "2", "--delay", "10ms", "--",
               PY, "-c", "import sys; sys.exit(1)")
    assert proc.returncode == 1
    assert proc.stderr == ""


def test_cli_until_output(tmp_path):
    state = tmp_path / "count"
    script = (
        "import os,sys; p=%r; n=int(open(p).read()) if os.path.exists(p) "
        "else 0; open(p,'w').write(str(n+1)); "
        "print('READY' if n >= 1 else 'warming up')" % str(state)
    )
    proc = cli("--times", "3", "--delay", "10ms", "--until-output", "READY",
               "--", PY, "-c", script)
    assert proc.returncode == 0
    assert proc.stdout.count("warming up") == 1
    assert proc.stdout.count("READY") == 1


def test_cli_shell_mode():
    proc = cli("--times", "1", "--shell", "--", "echo a | grep a")
    assert proc.returncode == 0


def test_cli_env_and_timeout():
    proc = cli("--times", "1", "--timeout", "200ms", "--env", "FOO=bar",
               "--", PY, "-c",
               "import os,sys,time; time.sleep(5)")
    assert proc.returncode == 124
    assert "timeout" in proc.stderr


def test_cli_version():
    proc = cli("--version")
    assert proc.returncode == 0
    assert proc.stdout.startswith("retrier ")
