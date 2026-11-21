"""Retry policy: delays, backoff strategies, jitter, duration parsing."""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field

__all__ = [
    "Backoff",
    "Jitter",
    "RetryPolicy",
    "format_duration",
    "parse_duration",
]

# Backoff strategies.
Backoff = str  # "constant" | "linear" | "exponential"
Jitter = str  # "none" | "full" | "equal"

BACKOFFS = ("constant", "linear", "exponential")
JITTERS = ("none", "full", "equal")

_DURATION_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(ms|s|m|h)?\s*$", re.IGNORECASE)

_UNIT_SECONDS = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}


def parse_duration(value):
    """Parse a human duration into seconds (float).

    Accepts ``500ms``, ``2s``, ``1.5m``, ``1h`` or a bare number (seconds).
    Raises ValueError on anything else, including negatives.
    """
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds < 0:
            raise ValueError("duration must not be negative: %r" % (value,))
        return seconds
    match = _DURATION_RE.match(str(value))
    if not match:
        raise ValueError(
            "invalid duration %r (expected e.g. 500ms, 2s, 1.5m, 1h)" % (value,)
        )
    amount, unit = match.groups()
    return float(amount) * _UNIT_SECONDS[(unit or "s").lower()]


def format_duration(seconds):
    """Format seconds back into a short human duration, e.g. 90 -> '1m30s'."""
    seconds = float(seconds)
    if seconds < 1:
        millis = seconds * 1000
        return "%gms" % millis if millis != int(millis) else "%dms" % int(millis)
    if seconds < 60:
        return "%gs" % seconds if seconds != int(seconds) else "%ds" % int(seconds)
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        if secs:
            return "%dm%gs" % (minutes, secs)
        return "%dm" % minutes
    hours, minutes = divmod(minutes, 60)
    if secs:
        return "%dh%dm%gs" % (hours, minutes, secs)
    if minutes:
        return "%dh%dm" % (hours, minutes)
    return "%dh" % hours


@dataclass
class RetryPolicy:
    """Everything that decides *whether* and *how long* to wait before retrying.

    ``times`` is the total number of attempts (1 = run once, never retry).
    ``delay`` is the base delay before the first retry.
    ``backoff`` picks how the delay grows: ``constant``, ``linear``
    (delay * attempt) or ``exponential`` (delay * factor ** (attempt - 1)).
    ``max_delay`` caps the computed delay. ``jitter`` decorrelates retries
    across parallel clients: ``none``, ``full`` (uniform 0..delay) or
    ``equal`` (delay/2 + uniform 0..delay/2, the AWS-style compromise).
    ``retry_on`` / ``no_retry_on`` are exit-code allow/deny lists; an empty
    ``retry_on`` means "retry any non-zero exit code". ``timeout`` is the
    per-attempt wall-clock limit in seconds (None = no limit). ``max_time``
    is the total wall-clock budget for all attempts (None = no limit).
    """

    times: int = 3
    delay: float = 1.0
    backoff: Backoff = "exponential"
    factor: float = 2.0
    max_delay: float = 30.0
    jitter: Jitter = "none"
    retry_on: tuple = ()
    no_retry_on: tuple = ()
    timeout: float | None = None
    max_time: float | None = None
    rng: random.Random = field(default_factory=random.Random, repr=False)

    def __post_init__(self):
        if self.times < 1:
            raise ValueError("times must be >= 1, got %r" % (self.times,))
        if self.delay < 0:
            raise ValueError("delay must not be negative, got %r" % (self.delay,))
        if self.backoff not in BACKOFFS:
            raise ValueError("backoff must be one of %s, got %r" % (BACKOFFS, self.backoff))
        if self.factor < 1:
            raise ValueError("factor must be >= 1, got %r" % (self.factor,))
        if self.max_delay < 0:
            raise ValueError("max_delay must not be negative, got %r" % (self.max_delay,))
        if self.jitter not in JITTERS:
            raise ValueError("jitter must be one of %s, got %r" % (JITTERS, self.jitter))
        if self.timeout is not None and self.timeout <= 0:
            raise ValueError("timeout must be positive, got %r" % (self.timeout,))
        if self.max_time is not None and self.max_time <= 0:
            raise ValueError("max_time must be positive, got %r" % (self.max_time,))
        self.retry_on = tuple(self.retry_on)
        self.no_retry_on = tuple(self.no_retry_on)

    def delay_for(self, failed_attempt):
        """Delay (seconds) to wait after failed attempt number ``failed_attempt``.

        ``failed_attempt`` is 1-based: the delay before attempt 2 is
        ``delay_for(1)``. Jitter is applied after the max_delay cap.
        """
        if self.backoff == "constant":
            base = self.delay
        elif self.backoff == "linear":
            base = self.delay * failed_attempt
        else:  # exponential
            base = self.delay * (self.factor ** (failed_attempt - 1))
        base = min(base, self.max_delay)
        if self.jitter == "full":
            return self.rng.uniform(0, base)
        if self.jitter == "equal":
            return base / 2 + self.rng.uniform(0, base / 2)
        return base

    def should_retry_exit_code(self, exit_code):
        """True if a failed attempt with this exit code may be retried."""
        if exit_code in self.no_retry_on:
            return False
        if self.retry_on:
            return exit_code in self.retry_on
        return exit_code != 0
