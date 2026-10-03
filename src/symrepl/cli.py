"""symrepl command-line entry point (skeleton)."""

from __future__ import annotations

import argparse


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="symrepl",
        description="Replay symbolic-execution counterexamples under LLDB.",
    )
    parser.add_argument("ktest", nargs="?", help="path to a .ktest file")
    parser.add_argument("--target", help="path to the debuggee binary")
    args = parser.parse_args(argv)
    if not args.ktest:
        parser.print_help()
        return 0
    # M1: parse ktest + drive replay.py.
    print(f"symrepl: replay of {args.ktest} is not implemented yet (see docs/en/roadmap.md)")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
