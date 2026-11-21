# retrier

[![PyPI](https://img.shields.io/pypi/v/retrier.svg)](https://pypi.org/project/retrier/)
[![Python](https://img.shields.io/pypi/pyversions/retrier.svg)](https://pypi.org/project/retrier/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Run any command with smart retries — exponential backoff, jitter, exit-code
filtering, output matching, and per-attempt timeouts.**

`retrier` is the retry wrapper you reach for instead of writing the same
`for i in 1 2 3; do ... sleep ...; done` shell loop for the hundredth time —
except this one gets the details right: capped exponential backoff, jitter so
a fleet of clients doesn't retry in lockstep, a total time budget, and an
honest exit code when everything fails.

```bash
pip install retrier
```

## Quickstart

Wait for a flaky service to come up:

```bash
$ retrier --times 5 --delay 2s -- curl -f https://example.com/health
```

Retry a deploy script, but never retry a usage error (exit code 2):

```bash
$ retrier --times 5 --delay 500ms --no-retry-on 2 -- deploy.sh
```

Wait until a server prints `READY` (even if it exits 0 before that):

```bash
$ retrier --times 10 --timeout 10s --until-output READY -- ./server --port 8000
```

Real output — a command that fails twice, then succeeds
([`demo/flaky.py`](demo/flaky.py)):

```console
$ retrier --times 5 --delay 200ms -- python3 demo/flaky.py
flaky.py: attempt 1 — simulated failure (connection refused)
[retrier] attempt 1/5 failed (exit code 1) — retrying in 0.20s
flaky.py: attempt 2 — simulated failure (connection refused)
[retrier] attempt 2/5 failed (exit code 1) — retrying in 0.40s
flaky.py: attempt 3 — success!
```

Note the backoff: 0.20s, then 0.40s — the delay doubles after each failure
(exponential backoff with factor 2, the default).

## How retries work

| Option | What it does |
|---|---|
| `-n, --times N` | Total attempts (default 3; `1` = run once, never retry) |
| `--delay DURATION` | Delay before the first retry: `500ms`, `2s`, `1.5m`, `1h` (default `1s`) |
| `--backoff constant\|linear\|exponential` | How the delay grows (default `exponential`) |
| `--factor F` | Multiplier for exponential backoff (default `2.0`) |
| `--max-delay DURATION` | Cap on the delay between attempts (default `30s`) |
| `--jitter none\|full\|equal` | Decorrelate retries: `full` = uniform 0..delay, `equal` = AWS-style half + uniform half (default `none`) |
| `--timeout DURATION` | Kill each attempt after this long; a timed-out attempt counts as exit code 124 |
| `--max-time DURATION` | Total wall-clock budget for all attempts |
| `--retry-on 1,7` | Only retry these exit codes (default: retry any non-zero exit code) |
| `--no-retry-on 2` | Never retry these exit codes (a usage error is not transient) |
| `--until-output REGEX` | Retry until the command's combined output matches this regex, even if it exits 0 |
| `--shell` | Run through the shell (pipes, redirects, `&&` chains) |
| `--env KEY=VALUE` | Extra environment variable for the command (repeatable) |
| `--cwd DIR` | Run the command in this directory |
| `-q, --quiet` | Suppress per-attempt status lines |
| `-v, --verbose` | Also log successful attempts |
| `--json` | Print a JSON summary of all attempts to stderr (implies `--quiet`) |

Exit code: **0** on success, otherwise the last attempt's exit code —
**124** when an attempt timed out, **127** when the command is not found
(a missing command is never retried).

## Examples

Retry only on the exit codes you know are transient:

```console
$ retrier --times 2 --delay 100ms -- python3 -c "import sys; sys.exit(2)"
[retrier] attempt 1/2 failed (exit code 2) — retrying in 0.10s
[retrier] attempt 2/2 failed (exit code 2) — no attempts left
```

```console
$ retrier --times 5 --delay 100ms --no-retry-on 2 -- python3 -c "import sys; sys.exit(2)"
[retrier] attempt 1/5 failed (exit code 2) — exit code 2 is not retryable
```

Machine-readable summary for CI scripts (`--json` goes to stderr, so the
command's own stdout stays clean and pipeable):

```console
$ retrier --times 3 --delay 100ms --json -- python3 -c "import sys; sys.exit(3)"
{
  "command": ["python3", "-c", "import sys; sys.exit(3)"],
  "success": false,
  "exit_code": 3,
  "total_duration_s": 0.075,
  "attempts": [
    {"number": 1, "exit_code": 3, "duration_s": 0.025,
     "timed_out": false, "reason": "exit-code", "detail": "exit code 3"},
    {"number": 2, "exit_code": 3, "duration_s": 0.024,
     "timed_out": false, "reason": "exit-code", "detail": "exit code 3"},
    {"number": 3, "exit_code": 3, "duration_s": 0.026,
     "timed_out": false, "reason": "exit-code", "detail": "exit code 3"}
  ]
}
```

## Use it as a library

```python
from retrier import RetryPolicy, call, run

# Retry a subprocess command.
result = run(
    ["curl", "-f", "https://example.com/health"],
    policy=RetryPolicy(times=5, delay=2.0, max_delay=30.0),
)
print(result.success, result.exit_code)   # True 0
print(result.as_dict())                   # JSON-friendly summary

# Retry a Python callable on exception.
result = call(
    fetch_something,
    policy=RetryPolicy(times=4, delay=0.5, backoff="linear"),
    retry_on=(ConnectionError, TimeoutError),
)
if result.success:
    print(result.value)
else:
    print("gave up:", result.exception)
```

`RetryPolicy` is a dataclass: `times`, `delay`, `backoff`, `factor`,
`max_delay`, `jitter`, `retry_on`, `no_retry_on`, `timeout`, `max_time`.
`policy.delay_for(n)` computes the delay after failed attempt `n`, so you
can unit-test your backoff curve without sleeping.

## Why not just a shell loop?

| | `for i in ...; do ...; done` | `retrier` |
|---|---|---|
| Exponential backoff with cap | hand-rolled arithmetic | `--backoff exponential --max-delay 30s` |
| Jitter | no | `--jitter full` / `equal` |
| Per-attempt timeout | `timeout(1)` + bookkeeping | `--timeout 10s` |
| Total time budget | no | `--max-time 2m` |
| Retry only some exit codes | `if` chains | `--retry-on` / `--no-retry-on` |
| Retry until output appears | `grep` + loops | `--until-output REGEX` |
| JSON summary for CI | no | `--json` |
| Library use from Python | no | `retrier.run` / `retrier.call` |

## Development

```bash
git clone https://github.com/someone-rs/retrier
cd retrier
pip install -e ".[dev]"   # or: pip install -e . && pip install pytest
pytest                    # 78 tests
```

Python 3.9+, zero dependencies — the standard library only.

## License

MIT — see [LICENSE](LICENSE).
