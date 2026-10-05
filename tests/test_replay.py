"""Integration tests for the M1-a replay path.

These exercise the real kleeRuntest harness contract: the vendored
runtime sources in ``third_party/klee/runtime/`` are compiled against a
target from ``tests/fixtures/src/`` and replayed under LLDB via
:func:`symrepl.replay`.  Everything skips cleanly when lldb or clang is
unavailable (CI has neither; the developer machine has both).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from symrepl.replay.ktest import KTest, load
from symrepl.replay.replay import replay

KTESTS = Path(__file__).parents[1] / "third_party" / "klee" / "ktest"

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
    result = replay(load(KTESTS / name), str(get_sign_bin))
    assert result.state == "exited"
    assert result.exit_code == code
    assert result.run_test_errors == ()


def test_replay_name_mismatch_is_reported(get_sign_bin) -> None:
    # get_sign requests object "a"; the two_objs ktest carries ("arr", "b").
    # kleeRuntest reports the name mismatch to stderr and exits 1.
    result = replay(load(KTESTS / "two_objs_test000001.ktest"), str(get_sign_bin))
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


# ------------------------------------------------- branch tracing (M1-c) --
#
# get_sign traces with a function-scoped prescan: exactly two conditional
# sites (the ``x == 0`` and ``x < 0`` checks), and the hit sequence
# follows the recorded path of each committed ktest.

TRACE_CASES = [
    # (fixture, exit code, hit count, outcome of the first hit)
    # a == 0: the first jcc falls through (return 0); the second is never reached
    ("get_sign_test000001.ktest", 0, 1, "not observed (run ended)"),
    # a > 0: first jcc taken (x != 0), second one hit (last hit -> unobserved)
    ("get_sign_test000002.ktest", 1, 2, "taken"),
    # a = INT_MIN: first jcc taken (x != 0), second hit falls through (unobserved)
    ("get_sign_test000003.ktest", 255, 2, "taken"),
]


@pytest.mark.parametrize(
    ("name", "code", "hits", "first_outcome"),
    TRACE_CASES,
    ids=[case[0] for case in TRACE_CASES],
)
def test_replay_trace_get_sign(
    get_sign_bin, name: str, code: int, hits: int, first_outcome: str
) -> None:
    result = replay(load(KTESTS / name), str(get_sign_bin), scope="get_sign")
    assert result.exit_code == code
    assert result.trace is not None
    assert len(result.trace.sites) == 2
    assert {s.function for s in result.trace.sites} == {"get_sign"}
    assert len(result.trace.hits) == hits
    assert result.trace.hits[0].outcome == first_outcome
    # the traced frame sees the injected symbolic value
    first_vars = dict(result.trace.hits[0].variables)
    assert first_vars.get("x")


def test_replay_trace_module_scope_smoke(get_sign_bin) -> None:
    # module scope instruments every code symbol (runtime included);
    # get_sign's two decisions still appear, after the runtime noise
    result = replay(
        load(KTESTS / "get_sign_test000002.ktest"),
        str(get_sign_bin),
        scope="module",
    )
    assert result.trace is not None
    assert len(result.trace.sites) >= 2
    get_sign_hits = [
        h for h in result.trace.hits if h.site.function == "get_sign"
    ]
    assert len(get_sign_hits) == 2


def test_replay_trace_disabled(get_sign_bin) -> None:
    result = replay(
        load(KTESTS / "get_sign_test000001.ktest"),
        str(get_sign_bin),
        trace=False,
    )
    assert result.trace is None
    assert result.exit_code == 0


def test_replay_trace_empty_scope(get_sign_bin) -> None:
    # "traced, matched nothing" must stay distinguishable from
    # "tracing disabled": the report structure exists, contents empty.
    result = replay(
        load(KTESTS / "get_sign_test000002.ktest"),
        str(get_sign_bin),
        scope="no_such_fn",
    )
    assert result.trace is not None
    assert result.trace.scope == "no_such_fn"
    assert result.trace.sites == ()
    assert result.trace.hits == ()
    assert result.exit_code == 1
