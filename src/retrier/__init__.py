"""retrier — run any command with smart retries.

Public API:
    run()          run a subprocess command with retries
    call()         call a Python callable with retries
    RetryPolicy    the retry policy (delays, backoff, jitter, exit codes)
    RunResult      result of run(): attempts, success, exit code
    CallResult     result of call(): attempts, success, value
    parse_duration / format_duration helpers
"""
from __future__ import annotations

from .core import CallResult, RunResult, call, run
from .policy import (
    Backoff,
    Jitter,
    RetryPolicy,
    format_duration,
    parse_duration,
)

__all__ = [
    "Backoff",
    "CallResult",
    "Jitter",
    "RetryPolicy",
    "RunResult",
    "call",
    "format_duration",
    "parse_duration",
    "run",
]

__version__ = "1.0.0"
