"""KLEE `.ktest` file parser (skeleton).

The `.ktest` format is a small, stable binary format written by KLEE
(the symbolic executor) for each generated test case:

    magic      : 5 bytes, b"KTEST"   (legacy files: b"BOUT", version 1)
    version    : uint32 LE (2 or 3)
    [v>=2]     : numArgs uint32 LE, then numArgs x (len uint32 LE + bytes)
    numObjects : uint32 LE
    per object : nameLen uint32 LE, name bytes, size uint32 LE, data bytes

VERIFY the exact byte layout (including what version 3 adds) against
the KLEE sources before implementing the reader:
  - writer: klee/lib/Core/  (KTest writer)
  - reader: klee/tools/klee-replay/

No KLEE installation is required at runtime; this module must stay a
pure-Python, dependency-free reader (see docs/adr/0001-scope-and-ip-firewall.md).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

KTEST_MAGIC = b"KTEST"


@dataclass(frozen=True)
class KTestObject:
    """One symbolic input object: its variable name and concrete bytes."""

    name: str
    data: bytes


@dataclass(frozen=True)
class KTest:
    """A parsed .ktest file: command-line args + symbolic input objects."""

    version: int
    args: tuple[str, ...]
    objects: tuple[KTestObject, ...]


def parse(data: bytes) -> KTest:
    """Parse a `.ktest` byte string.

    Implemented in M1 — see docs/roadmap.md. The layout above must be
    verified against the KLEE sources first; golden fixtures (version 2
    and 3) are generated with real KLEE output before this lands.
    """
    if len(data) < 9 or data[:5] != KTEST_MAGIC:
        raise ValueError("not a KTEST file (bad magic)")
    (version,) = struct.unpack_from("<I", data, 5)
    if version < 2:
        raise NotImplementedError("legacy BOUT files (version 1) are out of scope for M1")
    raise NotImplementedError(
        "M1: confirm version 2/3 field order against klee/tools/klee-replay, then implement"
    )
