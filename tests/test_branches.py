"""Unit tests for branch-site selection and outcome inference.

Pure logic — synthetic instruction data, no debugger required.
"""

from __future__ import annotations

import pytest

from symrepl.branches import (
    BranchSite,
    infer_outcomes,
    is_conditional,
    parse_branch_target,
)

COND = 5  # stand-in for lldb.eInstructionControlFlowKindCondJump
JUMP = 4  # stand-in for eInstructionControlFlowKindJump (unconditional)
UNK = 2  # stand-in for eInstructionControlFlowKindUnknown


def site(address: int, size: int = 2, target: int | None = None) -> BranchSite:
    return BranchSite(
        address=address,
        function="f",
        mnemonic="jcc",
        size=size,
        target=target,
    )


# -------------------------------------------------------- is_conditional --


@pytest.mark.parametrize(
    "mnemonic",
    [
        "je", "jne", "jl", "jge", "jb", "jae", "js", "jns", "jp", "jo",
        "jnle", "jecxz", "loop", "loope", "loopne",
    ],
)
def test_x86_conditional_mnemonics(mnemonic: str) -> None:
    assert is_conditional(mnemonic, None, COND)


@pytest.mark.parametrize(
    "mnemonic",
    [
        "b.eq", "b.ne", "b.lt", "b.ge", "b.hs", "b.le",
        "cbz", "cbnz", "tbz", "tbnz",
    ],
)
def test_aarch64_conditional_mnemonics(mnemonic: str) -> None:
    assert is_conditional(mnemonic, None, COND)


@pytest.mark.parametrize(
    "mnemonic", ["jmp", "jmpq", "call", "callq", "ret", "br", "blr", "b", "mov"]
)
def test_unconditional_or_non_branch_mnemonics(mnemonic: str) -> None:
    assert not is_conditional(mnemonic, None, COND)


def test_known_kind_is_authoritative() -> None:
    # x86 jcc reported as CondJump
    assert is_conditional("jne", COND, COND)
    # unconditional jmp is never conditional, whatever its spelling
    assert not is_conditional("jmp", JUMP, COND)
    # any *known* non-CondJump kind wins over the mnemonic table
    assert not is_conditional("jne", JUMP, COND)
    assert not is_conditional("jne", UNK, COND)
    # unknown kind (None) -> fall back to the mnemonic table
    assert is_conditional("jge", None, COND)
    assert not is_conditional("jmp", None, COND)


# ------------------------------------------------------ parse_branch_target


def test_parse_direct_hex_target() -> None:
    assert parse_branch_target("0x588b2aa22208") == 0x588B2AA22208
    assert parse_branch_target("  0x10  ") == 0x10


@pytest.mark.parametrize(
    "operand", [None, "", "*%rax", "*0x3fcc(%rip)", "%rax", "edx"]
)
def test_parse_indirect_or_missing_target(operand: str | None) -> None:
    assert parse_branch_target(operand) is None


# -------------------------------------------------------------- fallthrough


def test_fallthrough_is_address_plus_size() -> None:
    assert site(0x100, size=2).fallthrough == 0x102
    assert site(0x100, size=6).fallthrough == 0x106


# ------------------------------------------------------------ infer_outcomes


def test_taken_when_next_hit_is_not_fallthrough() -> None:
    sites = {0x100: site(0x100, size=2, target=0x200)}
    outcomes = infer_outcomes(sites, [0x100, 0x200], run_ended=True)
    assert outcomes == ["taken", "not observed (run ended)"]


def test_fallthrough_when_next_hit_continues_linearly() -> None:
    sites = {0x100: site(0x100, size=2, target=0x900)}
    outcomes = infer_outcomes(sites, [0x100, 0x102], run_ended=True)
    assert outcomes == ["fallthrough", "not observed (run ended)"]


def test_taken_by_elimination_without_parseable_target() -> None:
    # next hit is neither fallthrough nor a parseable target — the branch
    # still cannot have fallen through
    sites = {0x100: site(0x100, size=2, target=None)}
    outcomes = infer_outcomes(sites, [0x100, 0x400], run_ended=True)
    assert outcomes == ["taken", "not observed (run ended)"]


def test_abnormal_termination_label() -> None:
    sites = {0x100: site(0x100)}
    outcomes = infer_outcomes(sites, [0x100], run_ended=False)
    assert outcomes == ["not observed (terminated abnormally)"]


def test_unknown_hit_pc_is_reported_as_unknown() -> None:
    # only possible defensively: the driver records hits exclusively at
    # known sites
    outcomes = infer_outcomes({}, [0xDEAD, 0xBEEF], run_ended=True)
    assert outcomes == ["unknown", "not observed (run ended)"]
