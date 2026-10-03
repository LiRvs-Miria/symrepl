"""Tests for the `.ktest` parser and reference writer.

The byte layout is pinned by a hand-computed golden fixture (independent
of :func:`symrepl.ktest.dump`) so endianness and field order cannot
drift, plus exhaustive truncation cases and hypothesis round-trips.
Layout reference: klee/klee master ``lib/Basic/KTest.cpp`` (2026-10-03).
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from symrepl.ktest import (
    BOUT_MAGIC,
    KTEST_MAGIC,
    KTest,
    KTestError,
    KTestObject,
    dump,
    load,
    parse,
)


def u32(value: int) -> bytes:
    return struct.pack(">I", value)


def string(raw: bytes) -> bytes:
    return u32(len(raw)) + raw


# v3: numArgs=1 ["prog"], symArgvs=1, symArgvLen=4, numObjects=1, "a"=b"\x01"
GOLDEN_V3 = (
    KTEST_MAGIC
    + u32(3)
    + u32(1)
    + string(b"prog")
    + u32(1)
    + u32(4)
    + u32(1)
    + string(b"a")
    + u32(1)
    + b"\x01"
)

GOLDEN_OBJECTS = (KTestObject(name="a", data=b"\x01"),)


def make_v3(version: int = 3) -> bytes:
    """A well-formed file like GOLDEN_V3 but with an arbitrary version."""
    return KTEST_MAGIC + u32(version) + GOLDEN_V3[len(KTEST_MAGIC) + 4 :]


def make_v1() -> bytes:
    """Legacy v1: BOUT magic, version 1, no symArgvs/symArgvLen fields."""
    return (
        BOUT_MAGIC
        + u32(1)
        + u32(1)
        + string(b"prog")
        + u32(1)
        + string(b"a")
        + u32(1)
        + b"\x01"
    )


# ---------------------------------------------------------------- golden --


def test_parses_golden_v3() -> None:
    assert parse(GOLDEN_V3) == KTest(
        version=3,
        args=("prog",),
        sym_argvs=1,
        sym_argv_len=4,
        objects=GOLDEN_OBJECTS,
    )


def test_dump_round_trips_golden_exactly() -> None:
    assert dump(parse(GOLDEN_V3)) == GOLDEN_V3


# ------------------------------------------------------------- versions --


def test_parses_v2_with_sym_fields() -> None:
    parsed = parse(make_v3(version=2))
    assert parsed.version == 2
    assert parsed.args == ("prog",)
    assert parsed.sym_argvs == 1
    assert parsed.sym_argv_len == 4
    assert parsed.objects == GOLDEN_OBJECTS


def test_tolerates_legacy_v1_bout_file() -> None:
    parsed = parse(make_v1())
    assert parsed.version == 1
    assert parsed.args == ("prog",)
    assert parsed.sym_argvs == 0
    assert parsed.sym_argv_len == 0
    assert parsed.objects == GOLDEN_OBJECTS


@pytest.mark.parametrize("version", [4, 5, 2**32 - 1])
def test_rejects_version_greater_than_three(version: int) -> None:
    with pytest.raises(KTestError, match="unsupported .ktest version"):
        parse(make_v3(version=version))


def test_dump_always_writes_current_version_and_sym_fields() -> None:
    legacy = KTest(
        version=1, args=("p",), sym_argvs=0, sym_argv_len=0, objects=()
    )
    reparsed = parse(dump(legacy))
    assert reparsed.version == 3
    assert reparsed.args == ("p",)
    assert reparsed.objects == ()


# --------------------------------------------------------------- shapes --


def test_multi_object_and_empty_args() -> None:
    blob = (
        KTEST_MAGIC
        + u32(3)
        + u32(0)  # numArgs
        + u32(0)  # symArgvs
        + u32(0)  # symArgvLen
        + u32(2)  # numObjects
        + string(b"arr") + u32(3) + b"\x00\x01\x02"
        + string(b"empty") + u32(0)  # zero-size object is legal
    )
    parsed = parse(blob)
    assert parsed.args == ()
    assert parsed.objects == (
        KTestObject("arr", b"\x00\x01\x02"),
        KTestObject("empty", b""),
    )


def test_non_utf8_name_round_trips_via_surrogateescape() -> None:
    blob = (
        KTEST_MAGIC + u32(3) + u32(0) + u32(0) + u32(0)
        + u32(1)
        + string(b"\xff\xfe") + u32(1) + b"x"
    )
    parsed = parse(blob)
    assert parsed.objects[0].name.encode("utf-8", "surrogateescape") == b"\xff\xfe"
    assert parse(dump(parsed)) == parsed


# ---------------------------------------------------------------- errors --


@pytest.mark.parametrize(
    "blob",
    [
        b"KTESt" + u32(3),  # wrong magic
        b"BOUT\x00" + u32(1),  # wrong legacy magic
    ],
)
def test_rejects_wrong_magic(blob: bytes) -> None:
    with pytest.raises(KTestError, match="bad magic"):
        parse(blob)


@pytest.mark.parametrize("blob", [b"", b"KTES"])
def test_rejects_short_header(blob: bytes) -> None:
    # A header shorter than MAGIC_SIZE is a truncation, not a magic mismatch.
    with pytest.raises(KTestError, match="truncated"):
        parse(blob)


@pytest.mark.parametrize("cut", range(len(GOLDEN_V3)))
def test_rejects_truncation_at_every_offset(cut: int) -> None:
    with pytest.raises(KTestError, match="truncated"):
        parse(GOLDEN_V3[:cut])


def test_trailing_bytes_are_tolerated() -> None:
    assert parse(GOLDEN_V3 + b"\xde\xad\xbe\xef") == parse(GOLDEN_V3)


# ------------------------------------------------------------------ I/O --


def test_load_reads_file(tmp_path) -> None:
    path = tmp_path / "test000001.ktest"
    path.write_bytes(GOLDEN_V3)
    assert load(path) == parse(GOLDEN_V3)
    assert load(str(path)) == parse(GOLDEN_V3)


def test_load_missing_file_raises_filenotfound(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        load(tmp_path / "missing.ktest")


# ------------------------------------------------- real-KLEE fixtures --
#
# Byte-for-byte artifacts from a real KLEE 3.1 run (provenance in
# tests/fixtures/README.md). These pin the parser against KLEE itself,
# not just against our reading of the format sources.

FIXTURES_DIR = Path(__file__).parent / "fixtures"
REAL_FIXTURES = sorted(FIXTURES_DIR.glob("*.ktest"))


def test_real_fixture_inventory() -> None:
    assert [p.name for p in REAL_FIXTURES] == [
        "get_sign_test000001.ktest",
        "get_sign_test000002.ktest",
        "get_sign_test000003.ktest",
        "two_objs_test000001.ktest",
        "two_objs_test000002.ktest",
        "two_objs_test000003.ktest",
    ]


@pytest.mark.parametrize("path", REAL_FIXTURES, ids=lambda p: p.stem)
def test_real_fixture_round_trips_byte_exactly(path: Path) -> None:
    raw = path.read_bytes()
    parsed = parse(raw)
    assert parsed.version == 3
    # KLEE writes no trailing bytes, so re-serialization must be exact.
    assert dump(parsed) == raw


def test_real_fixture_get_sign_shape() -> None:
    for path in REAL_FIXTURES:
        if not path.name.startswith("get_sign"):
            continue
        ktest = load(path)
        assert ktest.args == ("get_sign.ll",)
        assert ktest.sym_argvs == 0
        assert ktest.sym_argv_len == 0
        assert ktest.objects == (KTestObject("a", ktest.objects[0].data),)
        assert len(ktest.objects[0].data) == 4  # sizeof(int)
    datas = {
        load(p).objects[0].data
        for p in REAL_FIXTURES
        if p.name.startswith("get_sign")
    }
    assert len(datas) == 3  # three paths, three distinct concrete inputs


def test_real_fixture_two_objs_shape() -> None:
    for path in REAL_FIXTURES:
        if not path.name.startswith("two_objs"):
            continue
        ktest = load(path)
        assert ktest.args == ("two_objs.ll",)
        assert [(o.name, len(o.data)) for o in ktest.objects] == [
            ("arr", 4),
            ("b", 2),
        ]


# ------------------------------------------------------ property tests ---

_surrogate_text = st.binary(max_size=16).map(
    lambda raw: raw.decode("utf-8", "surrogateescape")
)


@given(
    args=st.lists(_surrogate_text, max_size=4),
    sym_argvs=st.integers(min_value=0, max_value=2**16),
    sym_argv_len=st.integers(min_value=0, max_value=2**16),
    names=st.lists(_surrogate_text, max_size=5, unique=True),
    datas=st.lists(st.binary(max_size=32), min_size=0, max_size=5),
)
def test_dump_parse_round_trip(
    args: list[str],
    sym_argvs: int,
    sym_argv_len: int,
    names: list[str],
    datas: list[bytes],
) -> None:
    ktest = KTest(
        version=3,
        args=tuple(args),
        sym_argvs=sym_argvs,
        sym_argv_len=sym_argv_len,
        objects=tuple(KTestObject(n, d) for n, d in zip(names, datas)),
    )
    assert parse(dump(ktest)) == ktest
