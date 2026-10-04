"""Tests for the command-line entry point (:mod:`symrepl.cli`).

The L1 tests exercise argument handling and error paths without LLDB;
the L2 tests drive real replays and are skipped like the rest of the
lldb-dependent suite when the toolchain is unavailable.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from symrepl.cli import main
from symrepl.ktest import KTEST_MAGIC

FIXTURES = Path(__file__).parent / "fixtures"

requires_lldb = pytest.mark.skipif(
    shutil.which("lldb") is None, reason="lldb not available"
)


# ------------------------------------------------------------------ L1 --


def test_no_arguments_prints_help(capsys) -> None:
    assert main([]) == 0
    assert "usage" in capsys.readouterr().out


def test_ktest_without_target_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main([str(FIXTURES / "get_sign_test000001.ktest")])
    assert excinfo.value.code == 2


def test_missing_ktest_file_reports_error(capsys, tmp_path) -> None:
    code = main([str(tmp_path / "nope.ktest"), "--target", "whatever"])
    assert code == 2
    assert "no such .ktest file" in capsys.readouterr().err


def test_malformed_ktest_reports_error(capsys, tmp_path) -> None:
    bad = tmp_path / "bad.ktest"
    bad.write_bytes(KTEST_MAGIC + b"\x00\x00\x00\x09")  # unsupported version 9
    code = main([str(bad), "--target", "whatever"])
    assert code == 2
    assert "unsupported .ktest version" in capsys.readouterr().err


def test_missing_target_reports_error(capsys, tmp_path) -> None:
    ktest = tmp_path / "stub.ktest"
    ktest.write_bytes(KTEST_MAGIC + b"\x00\x00\x00\x03\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00")
    code = main([str(ktest), "--target", str(tmp_path / "no-such-binary")])
    assert code == 2
    assert "no such target binary" in capsys.readouterr().err


# ------------------------------------------------------------------ L2 --


@requires_lldb
def test_cli_exit_code_passthrough(get_sign_bin: Path, capsys) -> None:
    # a > 0 in the recorded input -> get_sign returns 1.
    code = main(
        [
            str(FIXTURES / "get_sign_test000002.ktest"),
            "--target",
            str(get_sign_bin),
        ]
    )
    assert code == 1
    out = capsys.readouterr()
    assert "replay exited with status 1" in out.out
    assert "a: 4 byte(s)  01 01 01 01" in out.out


@requires_lldb
def test_cli_reports_kleeruntest_errors(get_sign_bin: Path, capsys) -> None:
    # two_objs ktest against get_sign: name mismatch -> runtest exit 1.
    code = main(
        [
            str(FIXTURES / "two_objs_test000001.ktest"),
            "--target",
            str(get_sign_bin),
        ]
    )
    assert code == 1
    err = capsys.readouterr().err
    assert "KLEE-RUNTEST: object name mismatch" in err


@requires_lldb
def test_cli_zero_exit_path(get_sign_bin: Path) -> None:
    code = main(
        [
            str(FIXTURES / "get_sign_test000001.ktest"),
            "--target",
            str(get_sign_bin),
        ]
    )
    assert code == 0


# --------------------------------------------------- branch tracing (L2) --


@requires_lldb
def test_cli_renders_branch_trace(get_sign_bin: Path, capsys) -> None:
    code = main(
        [
            str(FIXTURES / "get_sign_test000002.ktest"),
            "--target",
            str(get_sign_bin),
            "--scope",
            "get_sign",
        ]
    )
    assert code == 1
    out = capsys.readouterr().out
    assert "branch trace: scope=get_sign" in out
    assert "#1" in out
    assert "-> taken" in out


@requires_lldb
def test_cli_no_trace_flag(get_sign_bin: Path, capsys) -> None:
    code = main(
        [
            str(FIXTURES / "get_sign_test000002.ktest"),
            "--target",
            str(get_sign_bin),
            "--no-trace",
        ]
    )
    assert code == 1
    assert "branch trace" not in capsys.readouterr().out
