"""Tests for duration parsing, formatting and the backoff policy."""

import random

import pytest

from retrier.policy import (
    RetryPolicy,
    format_duration,
    parse_duration,
)


@pytest.mark.parametrize("text,seconds", [
    ("0", 0.0),
    ("1", 1.0),
    ("2s", 2.0),
    ("1.5s", 1.5),
    ("500ms", 0.5),
    ("250ms", 0.25),
    ("2m", 120.0),
    ("1.5m", 90.0),
    ("1h", 3600.0),
    ("  3s  ", 3.0),
    ("10MS", 0.01),
])
def test_parse_duration_valid(text, seconds):
    assert parse_duration(text) == pytest.approx(seconds)


@pytest.mark.parametrize("bad", ["", "abc", "-1s", "1d", "1.2.3s", "s", "1 s 2"])
def test_parse_duration_invalid(bad):
    with pytest.raises(ValueError):
        parse_duration(bad)


def test_parse_duration_numeric_input():
    assert parse_duration(5) == 5.0
    assert parse_duration(0.25) == 0.25
    with pytest.raises(ValueError):
        parse_duration(-1)


@pytest.mark.parametrize("seconds,text", [
    (0.5, "500ms"),
    (0.001, "1ms"),
    (1, "1s"),
    (2.5, "2.5s"),
    (90, "1m30s"),
    (60, "1m"),
    (3600, "1h"),
    (3661, "1h1m1s"),
])
def test_format_duration(seconds, text):
    assert format_duration(seconds) == text


def test_policy_validation():
    with pytest.raises(ValueError):
        RetryPolicy(times=0)
    with pytest.raises(ValueError):
        RetryPolicy(delay=-1)
    with pytest.raises(ValueError):
        RetryPolicy(backoff="weird")
    with pytest.raises(ValueError):
        RetryPolicy(factor=0.5)
    with pytest.raises(ValueError):
        RetryPolicy(jitter="wild")
    with pytest.raises(ValueError):
        RetryPolicy(timeout=0)
    with pytest.raises(ValueError):
        RetryPolicy(max_time=-5)


def test_delay_constant():
    p = RetryPolicy(delay=2.0, backoff="constant", max_delay=100)
    assert [p.delay_for(i) for i in (1, 2, 3)] == [2.0, 2.0, 2.0]


def test_delay_linear():
    p = RetryPolicy(delay=2.0, backoff="linear", max_delay=100)
    assert [p.delay_for(i) for i in (1, 2, 3)] == [2.0, 4.0, 6.0]


def test_delay_exponential_default_factor():
    p = RetryPolicy(delay=1.0, backoff="exponential", max_delay=100)
    assert [p.delay_for(i) for i in (1, 2, 3, 4)] == [1.0, 2.0, 4.0, 8.0]


def test_delay_exponential_custom_factor():
    p = RetryPolicy(delay=3.0, backoff="exponential", factor=3.0, max_delay=100)
    assert [p.delay_for(i) for i in (1, 2, 3)] == [3.0, 9.0, 27.0]


def test_delay_capped_by_max_delay():
    p = RetryPolicy(delay=10.0, backoff="exponential", factor=10.0, max_delay=25.0)
    assert p.delay_for(3) == 25.0


def test_jitter_full_stays_in_range():
    p = RetryPolicy(delay=10.0, backoff="constant", jitter="full",
                    rng=random.Random(42))
    for _ in range(50):
        assert 0 <= p.delay_for(1) <= 10.0


def test_jitter_equal_stays_in_range():
    p = RetryPolicy(delay=10.0, backoff="constant", jitter="equal",
                    rng=random.Random(7))
    for _ in range(50):
        assert 5.0 <= p.delay_for(1) <= 10.0


def test_jitter_none_is_deterministic():
    p = RetryPolicy(delay=4.0, backoff="constant")
    assert p.delay_for(1) == p.delay_for(1) == 4.0


@pytest.mark.parametrize("exit_code,retry_on,no_retry_on,expected", [
    (1, (), (), True),        # any non-zero retries by default
    (0, (), (), False),       # zero never retries
    (1, (1, 7), (), True),    # listed code retries
    (2, (1, 7), (), False),   # unlisted code does not
    (1, (1, 7), (1,), False),  # deny list wins over allow list
    (7, (), (7,), False),     # deny list blocks the default too
    (3, (), (7,), True),
])
def test_should_retry_exit_code(exit_code, retry_on, no_retry_on, expected):
    p = RetryPolicy(retry_on=retry_on, no_retry_on=no_retry_on)
    assert p.should_retry_exit_code(exit_code) is expected
