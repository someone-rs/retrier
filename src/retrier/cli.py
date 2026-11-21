"""Command-line interface for retrier."""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .core import run
from .policy import BACKOFFS, JITTERS, RetryPolicy, parse_duration

__all__ = ["build_parser", "main"]


def _duration(value):
    try:
        return parse_duration(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc))


def _exit_codes(value):
    codes = []
    for part in str(value).split(","):
        part = part.strip()
        if not part:
            continue
        try:
            codes.append(int(part))
        except ValueError:
            raise argparse.ArgumentTypeError(
                "invalid exit code %r (expected comma-separated integers)" % part
            )
    return codes


def _env_pair(value):
    if "=" not in value:
        raise argparse.ArgumentTypeError(
            "--env expects KEY=VALUE, got %r" % value
        )
    key, val = value.split("=", 1)
    if not key:
        raise argparse.ArgumentTypeError("--env expects KEY=VALUE, got %r" % value)
    return key, val


def build_parser():
    parser = argparse.ArgumentParser(
        prog="retrier",
        description=(
            "Run any command with smart retries: exponential backoff, jitter, "
            "exit-code filtering, output matching and per-attempt timeouts."
        ),
        epilog=(
            "Examples:\n"
            "  retrier --times 5 --delay 2s -- curl -f https://example.com/health\n"
            "  retrier --until-output READY --timeout 10s -- ./server --port 8000\n"
            "  retrier --times 10 --delay 500ms --backoff constant -- pg_isready\n"
            "  retrier --retry-on 1,7 --no-retry-on 2 -- deploy.sh\n"
            "\nExit code: 0 on success, otherwise the last attempt's exit code "
            "(124 on timeout, 127 when the command is not found)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version",
                        version="retrier %s" % __version__)
    parser.add_argument("-n", "--times", type=int, default=3, metavar="N",
                        help="total attempts (default: 3; 1 = run once, never retry)")
    parser.add_argument("--delay", type=_duration, default=1.0, metavar="DURATION",
                        help="delay before the first retry, e.g. 500ms, 2s, 1m "
                             "(default: 1s)")
    parser.add_argument("--backoff", choices=BACKOFFS, default="exponential",
                        help="how the delay grows: constant, linear, exponential "
                             "(default: exponential)")
    parser.add_argument("--factor", type=float, default=2.0, metavar="F",
                        help="multiplier for exponential backoff (default: 2.0)")
    parser.add_argument("--max-delay", type=_duration, default=30.0,
                        metavar="DURATION",
                        help="cap on the delay between attempts (default: 30s)")
    parser.add_argument("--jitter", choices=JITTERS, default="none",
                        help="decorrelate retries: none, full, equal (default: none)")
    parser.add_argument("--timeout", type=_duration, default=None,
                        metavar="DURATION",
                        help="kill each attempt after this long (e.g. 10s); "
                             "a timed-out attempt counts as exit code 124")
    parser.add_argument("--max-time", type=_duration, default=None,
                        metavar="DURATION",
                        help="total wall-clock budget for all attempts (e.g. 2m)")
    parser.add_argument("--retry-on", type=_exit_codes, default=[], metavar="CODES",
                        help="only retry these exit codes, comma-separated "
                             "(default: retry any non-zero exit code)")
    parser.add_argument("--no-retry-on", type=_exit_codes, default=[],
                        metavar="CODES",
                        help="never retry these exit codes, comma-separated")
    parser.add_argument("--until-output", default=None, metavar="REGEX",
                        help="retry until the command's combined output matches "
                             "this regex, even if it exits 0")
    parser.add_argument("--shell", action="store_true",
                        help="run the command through the shell (allows pipes, "
                             "redirects and && chains)")
    parser.add_argument("--env", action="append", type=_env_pair, default=[],
                        metavar="KEY=VALUE",
                        help="extra environment variable for the command "
                             "(repeatable)")
    parser.add_argument("--cwd", default=None, metavar="DIR",
                        help="run the command in this directory")
    parser.add_argument("-q", "--quiet", action="store_true",
                        help="suppress per-attempt status lines")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="also log successful attempts")
    parser.add_argument("--json", action="store_true",
                        help="print a JSON summary of all attempts to stderr "
                             "when finished (implies --quiet)")
    parser.add_argument("command", nargs=argparse.REMAINDER, metavar="-- COMMAND...",
                        help="the command to run (use -- to separate it from "
                             "retrier's own options)")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_usage(sys.stderr)
        print("retrier: error: no command given — "
              "usage: retrier [OPTIONS] -- COMMAND [ARGS...]", file=sys.stderr)
        return 2
    if args.command[0] == "--":
        args.command = args.command[1:]
    if not args.command:
        parser.print_usage(sys.stderr)
        print("retrier: error: no command given after --", file=sys.stderr)
        return 2
    if args.times < 1:
        print("retrier: error: --times must be >= 1", file=sys.stderr)
        return 2
    if args.factor < 1:
        print("retrier: error: --factor must be >= 1", file=sys.stderr)
        return 2

    policy = RetryPolicy(
        times=args.times,
        delay=args.delay,
        backoff=args.backoff,
        factor=args.factor,
        max_delay=args.max_delay,
        jitter=args.jitter,
        retry_on=tuple(args.retry_on),
        no_retry_on=tuple(args.no_retry_on),
        timeout=args.timeout,
        max_time=args.max_time,
    )

    command = " ".join(args.command) if args.shell else args.command
    result = run(
        command,
        policy=policy,
        until_output=args.until_output,
        env=dict(args.env),
        cwd=args.cwd,
        shell=args.shell,
        quiet=args.quiet or args.json,
        verbose=args.verbose,
    )

    if args.json:
        # stderr keeps stdout clean for the command's own output, so the
        # summary stays parseable even when the command prints a lot.
        print(json.dumps(result.as_dict(), indent=2), file=sys.stderr)

    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
