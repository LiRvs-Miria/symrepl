"""Trace record model and text report rendering (pure logic).

The driver collects :class:`Hit` records in execution order; this
module turns them (plus the instrumented :class:`BranchSite` set) into
the human-readable M1 branch report.

Honesty rules baked into the report (ADR-0002): dynamic tracing is a
*superset* of the path decisions (it includes input-independent,
concrete-only branches), and the outcome of the final hit is never
observable.  Constant/varying annotations are per-run observations,
not claims about symbolic dependence — precise mapping of the
KLEE-fork "constraining" subset is deferred to M3 (IR/DWARF).
"""

from __future__ import annotations

from dataclasses import dataclass

from .branches import BranchSite


@dataclass(frozen=True)
class Hit:
    """One breakpoint stop on a branch site, in execution order."""

    order: int
    site: BranchSite
    #: ``(name, value)`` for the frame's arguments and locals.
    variables: tuple[tuple[str, str], ...]
    outcome: str


@dataclass(frozen=True)
class TraceReport:
    """Result of one traced replay run."""

    scope: str
    sites: tuple[BranchSite, ...]
    hits: tuple[Hit, ...]

    def render(self) -> str:
        lines = [
            f"branch trace: scope={self.scope}, "
            f"{len(self.sites)} conditional site(s) instrumented, "
            f"{len(self.hits)} hit(s) in execution order:"
        ]
        for hit in self.hits:
            state = (
                ", ".join(f"{name}={value}" for name, value in hit.variables)
                or "(no locals)"
            )
            lines.append(
                f"  #{hit.order} 0x{hit.site.address:x} "
                f"<{hit.site.function}> {hit.site.mnemonic}"
                f"  -> {hit.outcome}  [{state}]"
            )
        lines.extend(self._site_summary())
        lines.append(
            "note: dynamic tracing is a superset of the recorded path "
            "decisions (concrete-only branches included); mapping the "
            "KLEE-fork constraining subset is deferred to M3 (IR/DWARF)."
        )
        return "\n".join(lines)

    def _site_summary(self) -> list[str]:
        by_site: dict[int, list[Hit]] = {}
        for hit in self.hits:
            by_site.setdefault(hit.site.address, []).append(hit)
        lines: list[str] = []
        if by_site:
            lines.append("per-site summary (hit sites only):")
        for address in sorted(by_site):
            hits = by_site[address]
            outcomes = sorted({hit.outcome for hit in hits})
            states = sorted({hit.variables for hit in hits})
            tag = "constant within this run" if len(states) == 1 else "varying"
            lines.append(
                f"  0x{address:x} <{hits[0].site.function}> "
                f"{hits[0].site.mnemonic}: {len(hits)} hit(s) "
                f"({', '.join(outcomes)}; {tag})"
            )
        return lines
