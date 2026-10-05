"""KLEE `.ktest` file parser and reference writer.

Byte layout (all integers are uint32 **big-endian**), verified against
klee/klee master ``lib/Basic/KTest.cpp`` on 2026-10-03 (input contract
frozen in docs/en/adr/0002-m1-input-contract.md):

    magic      : 5 bytes -- b"KTEST" (current) or legacy b"BOUT\\n";
                  both are accepted on read
    version    : uint32 BE; > 3 rejected; == 1 has no sym fields;
                  >= 2 carries symArgvs, symArgvLen
    numArgs    : uint32 BE
    args       : numArgs x string (uint32 BE length + raw bytes)
    [v>=2]     : symArgvs uint32 BE, symArgvLen uint32 BE
    numObjects : uint32 BE
    objects    : numObjects x { name: string; numBytes: uint32 BE;
                                data: numBytes bytes }

Semantics mirror KLEE's ``kTest_fromFile``: a bad magic, a version
greater than 3, or a truncated file raise :class:`KTestError` — a newer
version must never be silently misparsed.  Trailing bytes after the
last object are tolerated (KLEE does not read them), and zero-size
objects are legal.  Legacy v1 files parse fine and report
``sym_argvs == sym_argv_len == 0``.

Object names and argv strings are decoded with
``utf-8/surrogateescape`` so parsing never raises on arbitrary bytes
and every parsed value round-trips through :func:`dump`.

No KLEE installation is required at runtime; this module stays a
pure-Python, dependency-free reader (see docs/en/adr/0001-scope-and-ip-firewall.md).
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass

KTEST_MAGIC = b"KTEST"
BOUT_MAGIC = b"BOUT\n"

#: Size of the magic field in bytes.
MAGIC_SIZE = 5

#: Highest `.ktest` version this module accepts (KLEE's ``KTEST_VERSION``).
CURRENT_KTEST_VERSION = 3

_UINT32 = struct.Struct(">I")


class KTestError(ValueError):
    """Raised when a `.ktest` byte stream is malformed or unsupported."""


@dataclass(frozen=True)
class KTestObject:
    """One symbolic input object: its variable name and concrete bytes."""

    name: str
    data: bytes


@dataclass(frozen=True)
class KTest:
    """A parsed `.ktest` file: command-line args + symbolic input objects."""

    version: int
    args: tuple[str, ...]
    #: Number of symbolic argv entries recorded by KLEE (0 for v1 files).
    sym_argvs: int
    #: Length of each symbolic argv entry recorded by KLEE (0 for v1 files).
    sym_argv_len: int
    objects: tuple[KTestObject, ...]


def _decode(raw: bytes) -> str:
    return raw.decode("utf-8", errors="surrogateescape")


def _encode(text: str) -> bytes:
    return text.encode("utf-8", errors="surrogateescape")


class _Reader:
    """Cursor over a byte buffer with strict short-read (truncation) checks."""

    __slots__ = ("_data", "_pos")

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0

    @property
    def remaining(self) -> int:
        return len(self._data) - self._pos

    def take(self, count: int) -> bytes:
        if count > self.remaining:
            raise KTestError(
                f"truncated .ktest: need {count} byte(s) at offset "
                f"{self._pos}, only {self.remaining} left"
            )
        chunk = self._data[self._pos : self._pos + count]
        self._pos += count
        return chunk

    def uint32(self) -> int:
        return _UINT32.unpack(self.take(_UINT32.size))[0]

    def string(self) -> str:
        length = self.uint32()
        return _decode(self.take(length))


def parse(data: bytes) -> KTest:
    """Parse a `.ktest` byte string.

    Raises :class:`KTestError` on a bad magic, a version greater than
    :data:`CURRENT_KTEST_VERSION`, or truncation.  Trailing bytes after
    the last object are ignored, mirroring ``kTest_fromFile``.
    """
    reader = _Reader(data)

    magic = reader.take(MAGIC_SIZE)
    if magic not in (KTEST_MAGIC, BOUT_MAGIC):
        raise KTestError(f"not a KTEST file (bad magic {magic!r})")

    version = reader.uint32()
    if version > CURRENT_KTEST_VERSION:
        raise KTestError(
            f"unsupported .ktest version {version} (reader knows up to "
            f"{CURRENT_KTEST_VERSION}); refusing to misparse a newer file"
        )

    num_args = reader.uint32()
    args = tuple(reader.string() for _ in range(num_args))

    sym_argvs = 0
    sym_argv_len = 0
    if version >= 2:
        sym_argvs = reader.uint32()
        sym_argv_len = reader.uint32()

    num_objects = reader.uint32()
    objects: list[KTestObject] = []
    for _ in range(num_objects):
        name = reader.string()
        size = reader.uint32()
        objects.append(KTestObject(name=name, data=reader.take(size)))

    return KTest(
        version=version,
        args=args,
        sym_argvs=sym_argvs,
        sym_argv_len=sym_argv_len,
        objects=tuple(objects),
    )


def load(path: str | os.PathLike[str]) -> KTest:
    """Read and parse the `.ktest` file at ``path``.

    :class:`FileNotFoundError` propagates from the underlying ``open``.
    """
    with open(path, "rb") as f:
        return parse(f.read())


def dump(ktest: KTest) -> bytes:
    """Serialize ``ktest`` as a version-3 file (mirrors ``kTest_toFile``).

    The current ``KTEST`` magic is always written, and the
    ``sym_argvs``/``sym_argv_len`` fields are always emitted, exactly as
    ``kTest_toFile`` does regardless of the recorded version.
    """
    out = bytearray()
    out += KTEST_MAGIC
    out += _UINT32.pack(CURRENT_KTEST_VERSION)
    out += _UINT32.pack(len(ktest.args))
    for arg in ktest.args:
        raw = _encode(arg)
        out += _UINT32.pack(len(raw))
        out += raw
    out += _UINT32.pack(ktest.sym_argvs)
    out += _UINT32.pack(ktest.sym_argv_len)
    out += _UINT32.pack(len(ktest.objects))
    for obj in ktest.objects:
        raw_name = _encode(obj.name)
        out += _UINT32.pack(len(raw_name))
        out += raw_name
        out += _UINT32.pack(len(obj.data))
        out += obj.data
    return bytes(out)
