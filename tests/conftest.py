"""Shared test fixtures."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

TESTS = Path(__file__).parent
ROOT = TESTS.parent
#: Our own test-target sources (``src/``, ``gen.sh``) — see
#: ``tests/fixtures/README.md``.
FIXTURES = TESTS / "fixtures"
#: Vendored KLEE artifacts (runtime sources + committed `.ktest`
#: fixtures) — see ``third_party/klee/README.md``. Tests reference
#: these; they own none of them.
RUNTIME = ROOT / "third_party" / "klee" / "runtime"
KTESTS = ROOT / "third_party" / "klee" / "ktest"


@pytest.fixture(scope="session")
def get_sign_bin(tmp_path_factory) -> Path:
    """Build the get_sign target linked against the vendored kleeRuntest
    runtime (recipe proven manually against the committed .ktest files:
    the three replay exit codes are 0 / 1 / 255)."""
    clang = shutil.which("clang")
    clangxx = shutil.which("clang++")
    if clang is None or clangxx is None:
        pytest.skip("clang/clang++ not available")

    work = tmp_path_factory.mktemp("replay-build")
    inc = RUNTIME / "include"

    objects = []
    for src, cc, extra in (
        (RUNTIME / "intrinsics.c", clang, []),
        (RUNTIME / "KTest.cpp", clangxx, ["-std=c++17"]),
    ):
        obj = work / (src.stem + ".o")
        subprocess.run(
            [cc, "-O0", "-g", *extra, "-c", str(src), f"-I{inc}", "-o", str(obj)],
            check=True,
        )
        objects.append(obj)

    binary = work / "get_sign"
    subprocess.run(
        [
            clang,
            "-O0",
            "-g",
            str(FIXTURES / "src" / "get_sign.c"),
            f"-I{inc}",
            *(str(o) for o in objects),
            "-o",
            str(binary),
        ],
        check=True,
    )
    return binary
