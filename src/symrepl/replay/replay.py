"""Replay driver: turn a parsed `.ktest` into a debugger-observed run.

M1-a implements the replay *path*: materialize the ktest, launch the
target under LLDB with ``KTEST_FILE`` injected (the official kleeRuntest
harness contract, ADR-0002), run to exit, and report the outcome along
with any ``KLEE_RUN_TEST_ERROR`` messages.  Branch tracing and
per-decision state dumps are M1-c (see docs/en/roadmap.md).
"""

from __future__ import annotations

import os
import tempfile

from .ktest import KTest, dump
from .driver import ReplayResult, run

__all__ = ["ReplayResult", "replay"]


def replay(
    ktest: KTest,
    target: str,
    timeout_seconds: int = 60,
    trace: bool = True,
    scope: str = "module",
) -> ReplayResult:
    """Replay ``ktest`` against ``target`` under LLDB.

    ``scope`` names the tracing scope: ``"module"`` (every code symbol
    of the main module) or a comma-separated list of function names.
    ``trace=False`` skips branch tracing entirely.

    The parsed (and therefore validated) :class:`KTest` model is the
    source of truth: it is re-serialized with :func:`symrepl.ktest.dump`
    into a temporary file for the harness — a byte-exact round trip, as
    pinned by the real-KLEE fixture tests.

    Raises :class:`FileNotFoundError` if ``target`` does not exist, and
    :class:`RuntimeError` if the LLDB Python module is unavailable or
    the launch fails.
    """
    if not os.path.exists(target):
        raise FileNotFoundError(target)

    fd, path = tempfile.mkstemp(suffix=".ktest", prefix="symrepl-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(dump(ktest))
        return run(
            path,
            target,
            timeout_seconds=timeout_seconds,
            trace=trace,
            scope=scope,
        )
    finally:
        os.unlink(path)
