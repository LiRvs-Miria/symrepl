"""symrepl command-line entry point.

Wires the M1 replay path together:

    symrepl <ktest> --target <binary> [--timeout SECONDS]

Exit codes (replay path):

- ``0``–``255``: the target's own exit status (the replay *is* the run);
- ``128``: the replay terminated without exiting (crash, signal);
- ``2``: symrepl-level failure — bad arguments, unreadable `.ktest`,
  missing target, unavailable LLDB, failed launch.

DAP server mode (M2-a, ADR-0003): ``symrepl dap [--port N]`` — ``dap``
is a reserved first word, dispatched before argparse so the replay
parser above stays untouched.  Exit codes in that mode: ``0`` clean
session end, ``2`` symrepl-level failure.
"""

from __future__ import annotations

import argparse
import sys

from .replay.ktest import KTest, KTestError, load
from .replay.replay import replay

_CLI_ERROR = 2
_NOT_EXITED = 128


def _describe_objects(ktest: KTest) -> list[str]:
    """One line per symbolic object: name, size, and a hex preview."""
    lines = []
    for obj in ktest.objects:
        if obj.data:
            preview = obj.data[:16].hex(" ")
            more = f" ... (+{len(obj.data) - 16} bytes)" if len(obj.data) > 16 else ""
            body = f"{preview}{more}"
        else:
            body = "(empty)"
        lines.append(f"  {obj.name}: {len(obj.data)} byte(s)  {body}")
    return lines


def main(argv: list[str] | None = None) -> int:
    tokens = sys.argv[1:] if argv is None else list(argv)
    if tokens[:1] == ["dap"]:
        return _dap_main(tokens[1:])
    parser = argparse.ArgumentParser(
        prog="symrepl",
        description="Replay symbolic-execution counterexamples under LLDB.",
    )
    parser.add_argument("ktest", nargs="?", help="path to a .ktest file")
    parser.add_argument(
        "--target",
        help="path to the debuggee binary (linked with -lkleeRuntest)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=60,
        metavar="SECONDS",
        help="seconds to wait for the replay to terminate (default: 60)",
    )
    parser.add_argument(
        "--scope",
        default="module",
        metavar="SCOPE",
        help='branch-tracing scope: "module" or a comma-separated list '
        'of function names (default: "module")',
    )
    parser.add_argument(
        "--no-trace",
        action="store_true",
        help="skip branch tracing (plain replay)",
    )
    args = parser.parse_args(argv)

    if not args.ktest:
        parser.print_help()
        return 0
    if not args.target:
        parser.error("--target is required when replaying a .ktest")

    try:
        ktest = load(args.ktest)
    except FileNotFoundError:
        print(f"symrepl: error: no such .ktest file: {args.ktest}", file=sys.stderr)
        return _CLI_ERROR
    except KTestError as exc:
        print(f"symrepl: error: {args.ktest}: {exc}", file=sys.stderr)
        return _CLI_ERROR

    # Flush as we go: stderr (kleeRuntest errors) is unbuffered, and
    # piped stdout would otherwise interleave out of order.
    print(f"symrepl: replaying {args.ktest} against {args.target}", flush=True)
    print(
        f"  recorded args: {list(ktest.args)}; {len(ktest.objects)} object(s)",
        flush=True,
    )
    for line in _describe_objects(ktest):
        print(line, flush=True)

    try:
        result = replay(
            ktest,
            args.target,
            timeout_seconds=args.timeout,
            trace=not args.no_trace,
            scope=args.scope,
        )
    except FileNotFoundError:
        print(
            f"symrepl: error: no such target binary: {args.target}",
            file=sys.stderr,
        )
        return _CLI_ERROR
    except (RuntimeError, OSError) as exc:
        print(f"symrepl: error: {exc}", file=sys.stderr)
        return _CLI_ERROR

    # kleeRuntest reports harness-contract violations here; they are the
    # most likely reason a replay went sideways, so surface them first.
    for msg in result.run_test_errors:
        print(f"symrepl: KLEE-RUNTEST: {msg}", file=sys.stderr)

    if result.state == "exited":
        status = result.exit_code if result.exit_code is not None else 1
        print(f"symrepl: replay exited with status {status}", flush=True)
        exit_status: int = max(0, min(255, status))
    else:
        print(
            f"symrepl: replay did not exit cleanly (state: {result.state}); "
            "inferior stderr follows",
            file=sys.stderr,
        )
        if result.stderr:
            for line in result.stderr.splitlines():
                print(f"  {line}", file=sys.stderr)
        exit_status = _NOT_EXITED

    # The ordered branch trace is the point of the tool: decisions and
    # the machine's view of each decision point, aligned.
    if result.trace is not None:
        print(result.trace.render(), flush=True)

    return exit_status


def _dap_main(argv: list[str]) -> int:
    """Run the M2-a DAP server skeleton (see :mod:`symrepl.dap.server`)."""
    from .dap import server as dap_server  # deferred: keeps the replay path lean

    code = dap_server.main(argv)
    return _CLI_ERROR if code != 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
