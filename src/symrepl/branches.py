"""Conditional-branch site selection and outcome inference (pure logic).

No LLDB imports: the driver feeds this module immutable instruction
snapshots and hit sequences, so every rule here is unit-testable
without a debugger.

Conditional-branch recognition (ADR-0002: ``DoesBranch()`` plus
per-architecture mnemonic filtering): when LLDB reports a control-flow
kind it is authoritative; when the kind is unknown (older LLDB,
non-x86 architectures) the mnemonic table decides.  Tables cover
x86/x86_64 (both AT&T and Intel spellings) and aarch64; other
architectures rely on the LLDB control-flow kind alone.

Outcome inference is exact for the observed stop sequence: every
conditional branch in scope is instrumented, so the PC of the *next*
hit is reached either by falling through this site or by taking it —
``next == fallthrough`` proves fallthrough, anything else proves the
branch was taken.  The outcome of the final hit is never observable
(the run ends before another stop) and is reported as such.
"""

from __future__ import annotations

from dataclasses import dataclass

#: x86/x86_64 conditional mnemonics (AT&T and Intel spellings coincide
#: except for size suffixes, which only appear on ``jmp``).
_X86_CONDITIONAL = frozenset(
    {
        "ja", "jae", "jb", "jbe", "jc", "je", "jg", "jge", "jl", "jle",
        "jna", "jnae", "jnb", "jnbe", "jnc", "jne", "jng", "jnge",
        "jnl", "jnle", "jno", "jnp", "jns", "jo", "jp", "js", "jz",
        "jecxz", "jrcxz",
        "loop", "loope", "loopne", "loopnz", "loopz",
    }
)

#: aarch64 conditional mnemonics: ``b.<cond>`` plus the compare/test-
#: and-branch family.  (``b.al`` is architecturally unconditional.)
_AARCH64_CONDITIONAL = frozenset(
    {
        *(f"b.{c}" for c in
          ("eq", "ne", "cs", "hs", "cc", "lo", "mi", "pl", "vs", "vc",
           "hi", "ls", "ge", "lt", "gt", "le")),
        "cbz", "cbnz", "tbz", "tbnz",
    }
)


def is_conditional(
    mnemonic: str, control_flow_kind: int | None, cond_jump_kind: int
) -> bool:
    """Decide whether one instruction is a conditional branch.

    ``control_flow_kind`` is the LLDB ``GetControlFlowKind()`` value, or
    ``None`` when unknown/unavailable; ``cond_jump_kind`` is the value
    of LLDB's ``eInstructionControlFlowKindCondJump`` on this build.
    """
    if control_flow_kind is not None:
        return control_flow_kind == cond_jump_kind
    mnemonic = mnemonic.strip().lower()
    return mnemonic in _X86_CONDITIONAL or mnemonic in _AARCH64_CONDITIONAL


def parse_branch_target(operand: str | None) -> int | None:
    """Extract a direct branch target from disassembly operand text.

    Both AT&T and Intel spellings render direct targets as a bare hex
    literal (``0x...``).  Indirect operands (``*%rax``, ``*(%rax,...)``)
    yield ``None``.
    """
    if not operand:
        return None
    text = operand.strip()
    if text.startswith("*"):
        text = text[1:]
    text = text.split(",")[0].strip()
    if text.lower().startswith("0x"):
        try:
            return int(text, 16)
        except ValueError:
            return None
    return None


@dataclass(frozen=True)
class BranchSite:
    """One instrumented conditional-branch instruction."""

    address: int
    function: str
    mnemonic: str
    #: Instruction length in bytes — fall-through is ``address + size``.
    size: int
    #: Direct branch target, or ``None`` when indirect/unparseable.
    target: int | None

    @property
    def fallthrough(self) -> int:
        return self.address + self.size


def infer_outcomes(
    sites: dict[int, BranchSite],
    hit_pcs: list[int],
    *,
    run_ended: bool,
) -> list[str]:
    """Infer taken/fall-through per hit, in order.

    ``hit_pcs`` are the recorded stop PCs (each a key of ``sites``).
    The final hit's outcome is unobservable and labelled as such.
    """
    outcomes: list[str] = []
    for i, pc in enumerate(hit_pcs):
        if i + 1 >= len(hit_pcs):
            outcomes.append(
                "not observed (run ended)"
                if run_ended
                else "not observed (terminated abnormally)"
            )
            continue
        site = sites.get(pc)
        if site is None:
            outcomes.append("unknown")
            continue
        nxt = hit_pcs[i + 1]
        outcomes.append("fallthrough" if nxt == site.fallthrough else "taken")
    return outcomes
