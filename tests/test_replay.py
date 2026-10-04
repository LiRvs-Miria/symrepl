"""Integration tests for the M1-a replay path.

These exercise the real kleeRuntest harness contract: the vendored
runtime sources in ``tests/fixtures/klee-runtime/`` are compiled
against a target from ``tests/fixtures/src/`` and replayed under LLDB
via :func:`symrepl.replay`.  Everything skips cleanly when lldb or
clang is unavailable (CI has neither; the developer machine has both).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from symrepl.ktest import KTest, load
from symrepl.replay import replay

FIXTURES = Path(__file__).parent / "fixtures"

pytestmark = pytest.mark.skipif(
    shutil.which("lldb") is None, reason="lldb not available"
)


# Expected main() return per recorded path: a == 0 / a > 0 (0x01010101) /
# a < 0 (INT_MIN, little-endian in the fixture).  main's return value is
# the process exit status; -1 surfaces as 255.
GET_SIGN_EXIT_CODES = [
    ("get_sign_test000001.ktest", 0),
    ("get_sign_test000002.ktest", 1),
    ("get_sign_test000003.ktest", 255),
]


@pytest.mark.parametrize(("name", "code"), GET_SIGN_EXIT_CODES, ids=lambda v: v)
def test_replay_get_sign_three_paths(get_sign_bin, name: str, code: int) -> None:
    result = replay(load(FIXTURES / name), str(get_sign_bin))
    assert result.state == "exited"
    assert result.exit_code == code
    assert result.run_test_errors == ()


def test_replay_name_mismatch_is_reported(get_sign_bin) -> None:
    # get_sign requests object "a"; the two_objs ktest carries ("arr", "b").
    # kleeRuntest reports the name mismatch to stderr and exits 1.
    result = replay(load(FIXTURES / "two_objs_test000001.ktest"), str(get_sign_bin))
    assert result.state == "exited"
    assert result.exit_code == 1
    assert any("object name mismatch" in e for e in result.run_test_errors)


def test_replay_out_of_inputs(get_sign_bin) -> None:
    # An object-less ktest exercises replay's materialization path and
    # kleeRuntest's "out of inputs" error (exit 1, not a signal).
    empty = KTest(version=3, args=(), sym_argvs=0, sym_argv_len=0, objects=())
    result = replay(empty, str(get_sign_bin))
    assert result.state == "exited"
    assert result.exit_code == 1
    assert any("out of inputs" in e for e in result.run_test_errors)
