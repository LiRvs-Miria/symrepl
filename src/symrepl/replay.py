"""LLDB replay driver (skeleton).

Design notes for M1:

- Drive LLDB through its Python API (the `lldb` module), never by
  scraping CLI output.
- For each recorded path decision: set a breakpoint at the constraining
  branch, run, on hit dump frames/variables, compare against the
  recorded decision, then continue.
- Inputs from the `.ktest` objects are injected per the harness
  contract (stdin / argv / files, exactly as the target was built for
  KLEE).

Open contract question (blocks M1 implementation): how much of the
path condition is recoverable from `.ktest` objects alone vs. requiring
KLEE to also emit a sidecar decision log? Investigate KLEE's output
options and freeze the contract before implementing — see
docs/en/roadmap.md.
"""

from __future__ import annotations

from .ktest import KTest


def replay(ktest: KTest, target: str) -> int:
    """Replay `ktest` against `target` under LLDB. (Implemented in M1.)"""
    raise NotImplementedError("M1 — see docs/en/roadmap.md")
