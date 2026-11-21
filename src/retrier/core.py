"""Core retry engine: run subprocess commands and callables with retries."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field

from .policy import RetryPolicy

__all__ = ["Attempt", "CallResult", "RunResult", "call", "run"]

# Conventional exit code when an attempt is killed for exceeding its timeout.
TIMEOUT_EXIT_CODE = 124
# Exit code when the command cannot be found at all (never retried).
NOT_FOUND_EXIT_CODE = 127


@dataclass
class Attempt:
    """One attempt of a retried operation."""

    number: int
    exit_code: int | None
    duration: float
    timed_out: bool = False
    reason: str = "ok"  # ok | exit-code | timeout | output-mismatch | not-found | exception
    detail: str = ""

    def as_dict(self):
        return {
            "number": self.number,
            "exit_code": self.exit_code,
            "duration_s": round(self.duration, 3),
            "timed_out": self.timed_out,
            "reason": self.reason,
            "detail": self.detail,
        }


@dataclass
class RunResult:
    """Result of :func:`run`."""

    command: list
    success: bool
    exit_code: int
    attempts: list = field(default_factory=list)

    @property
    def total_duration(self):
        return sum(a.duration for a in self.attempts)

    def as_dict(self):
        return {
            "command": self.command,
            "success": self.success,
            "exit_code": self.exit_code,
            "total_duration_s": round(self.total_duration, 3),
            "attempts": [a.as_dict() for a in self.attempts],
        }


@dataclass
class CallResult:
    """Result of :func:`call`."""

    success: bool
    value: object = None
    exception: BaseException | None = None
    attempts: list = field(default_factory=list)

    @property
    def total_duration(self):
        return sum(a.duration for a in self.attempts)

    def as_dict(self):
        return {
            "success": self.success,
            "attempts": [a.as_dict() for a in self.attempts],
        }


def _default_log(message, quiet=False):
    if not quiet:
        print("[retrier] " + message, file=sys.stderr, flush=True)


def run(
    command,
    policy=None,
    until_output=None,
    env=None,
    cwd=None,
    shell=False,
    quiet=False,
    verbose=False,
    log=None,
    sleep=time.sleep,
    clock=time.monotonic,
):
    """Run ``command`` with retries per ``policy``.

    Returns a :class:`RunResult`. The command's own stdout/stderr pass
    straight through to this process (unless ``until_output`` is given, in
    which case output is captured, replayed, and matched against the regex).

    Success means exit code 0 — and, when ``until_output`` is set, output
    matching that regex. A command that cannot be found exits 127 at once
    and is never retried. A timed-out attempt counts as exit code 124.
    """
    policy = policy or RetryPolicy()
    # With shell=True a string command stays a string (sh -c "...");
    # otherwise normalise to a list of strings.
    if not (shell and isinstance(command, str)):
        command = [str(c) for c in command]
    pattern = re.compile(until_output) if until_output else None
    log = log or (lambda msg: _default_log(msg, quiet=quiet))
    started = clock()
    attempts = []
    last_exit = 0

    full_env = dict(os.environ)
    if env:
        full_env.update(env)

    for number in range(1, policy.times + 1):
        attempt_started = clock()
        timed_out = False
        exit_code = None
        reason = "ok"
        detail = ""
        captured = b""

        try:
            if pattern is not None:
                proc = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    env=full_env,
                    cwd=cwd,
                    shell=shell,
                )
                try:
                    captured, _ = proc.communicate(timeout=policy.timeout)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    captured, _ = proc.communicate()
                    timed_out = True
                # Replay what the command printed, so output is never swallowed.
                if captured:
                    sys.stdout.buffer.write(captured)
                    sys.stdout.buffer.flush()
                exit_code = proc.returncode
            else:
                exit_code = subprocess.run(
                    command, env=full_env, cwd=cwd, shell=shell,
                    timeout=policy.timeout,
                ).returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            exit_code = TIMEOUT_EXIT_CODE
        except FileNotFoundError as exc:
            exit_code = NOT_FOUND_EXIT_CODE
            reason = "not-found"
            detail = str(exc)
        except PermissionError as exc:
            exit_code = NOT_FOUND_EXIT_CODE
            reason = "not-found"
            detail = str(exc)

        duration = clock() - attempt_started

        if timed_out:
            reason = "timeout"
            detail = "attempt exceeded timeout of %ss" % (policy.timeout,)
            exit_code = TIMEOUT_EXIT_CODE
        elif reason == "ok" and exit_code != 0:
            reason = "exit-code"
            detail = "exit code %d" % exit_code
        elif reason == "ok" and pattern is not None:
            text = captured.decode("utf-8", "replace")
            if not pattern.search(text):
                reason = "output-mismatch"
                detail = "output did not match /%s/" % until_output
            else:
                reason = "ok"
                detail = "output matched /%s/" % until_output
        elif reason == "ok":
            detail = "exit code 0"

        attempt = Attempt(
            number=number,
            exit_code=exit_code,
            duration=duration,
            timed_out=timed_out,
            reason=reason,
            detail=detail,
        )
        attempts.append(attempt)
        last_exit = exit_code if exit_code is not None else 1

        if reason == "ok":
            if verbose:
                log("attempt %d/%d succeeded in %.2fs" % (number, policy.times, duration))
            return RunResult(command=command, success=True, exit_code=0, attempts=attempts)

        # Decide whether another attempt is allowed at all.
        is_last = number >= policy.times
        if reason == "not-found":
            log("attempt %d/%d: command not found (%s) — not retrying"
                % (number, policy.times, detail))
            return RunResult(command=command, success=False, exit_code=last_exit,
                             attempts=attempts)
        retryable = policy.should_retry_exit_code(exit_code) if reason == "exit-code" \
            else reason in ("timeout", "output-mismatch")
        if is_last or not retryable:
            why = "no attempts left" if is_last else "exit code %d is not retryable" % exit_code
            log("attempt %d/%d failed (%s) — %s" % (number, policy.times, detail, why))
            return RunResult(command=command, success=False, exit_code=last_exit,
                             attempts=attempts)

        delay = policy.delay_for(number)
        if policy.max_time is not None:
            elapsed = clock() - started
            if elapsed + delay > policy.max_time:
                log("attempt %d/%d failed (%s) — time budget of %ss exceeded"
                    % (number, policy.times, detail, policy.max_time))
                return RunResult(command=command, success=False, exit_code=last_exit,
                                 attempts=attempts)
        log("attempt %d/%d failed (%s) — retrying in %.2fs"
            % (number, policy.times, detail, delay))
        sleep(delay)

    return RunResult(command=command, success=False, exit_code=last_exit, attempts=attempts)


def call(fn, *args, policy=None, retry_on=Exception, quiet=False, log=None,
         sleep=time.sleep, clock=time.monotonic, **kwargs):
    """Call ``fn(*args, **kwargs)`` with retries per ``policy``.

    Retries when ``fn`` raises an exception matching ``retry_on`` (an
    exception class or tuple of classes). Returns a :class:`CallResult`
    with ``.value`` on success or ``.exception`` (the last one raised)
    when attempts run out.
    """
    policy = policy or RetryPolicy()
    log = log or (lambda msg: _default_log(msg, quiet=quiet))
    started = clock()
    attempts = []
    last_exc = None

    for number in range(1, policy.times + 1):
        attempt_started = clock()
        try:
            value = fn(*args, **kwargs)
        except Exception as exc:
            last_exc = exc
            duration = clock() - attempt_started
            attempts.append(Attempt(
                number=number, exit_code=None, duration=duration,
                reason="exception", detail="%s: %s" % (type(exc).__name__, exc),
            ))
            if not isinstance(exc, retry_on):
                # Not a retryable exception type: give up at once, but
                # still report it instead of propagating.
                log("attempt %d/%d raised %s (not retryable) — giving up"
                    % (number, policy.times, type(exc).__name__))
                return CallResult(success=False, exception=last_exc, attempts=attempts)
            is_last = number >= policy.times
            if is_last:
                log("attempt %d/%d raised %s — no attempts left"
                    % (number, policy.times, type(exc).__name__))
                return CallResult(success=False, exception=last_exc, attempts=attempts)
            delay = policy.delay_for(number)
            if policy.max_time is not None:
                elapsed = clock() - started
                if elapsed + delay > policy.max_time:
                    log("attempt %d/%d raised %s — time budget of %ss exceeded"
                        % (number, policy.times, type(exc).__name__, policy.max_time))
                    return CallResult(success=False, exception=last_exc, attempts=attempts)
            log("attempt %d/%d raised %s — retrying in %.2fs"
                % (number, policy.times, type(exc).__name__, delay))
            sleep(delay)
            continue
        duration = clock() - attempt_started
        attempts.append(Attempt(
            number=number, exit_code=None, duration=duration, reason="ok",
            detail="returned normally",
        ))
        return CallResult(success=True, value=value, attempts=attempts)

    return CallResult(success=False, exception=last_exc, attempts=attempts)
