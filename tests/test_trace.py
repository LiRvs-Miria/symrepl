"""Unit tests for the trace report model and rendering (pure logic)."""

from __future__ import annotations

from symrepl.branches import BranchSite
from symrepl.trace import Hit, TraceReport

SITE_JNE = BranchSite(
    address=0x1149, function="get_sign", mnemonic="jne", size=2, target=0x1152
)
SITE_JGE = BranchSite(
    address=0x1158, function="get_sign", mnemonic="jge", size=2, target=0x1161
)


def report() -> TraceReport:
    return TraceReport(
        scope="get_sign",
        sites=(SITE_JNE, SITE_JGE),
        hits=(
            Hit(1, SITE_JNE, (("x", "4198449"),), "taken"),
            Hit(2, SITE_JGE, (("x", "4198449"),), "not observed (run ended)"),
        ),
    )


def test_render_header_counts() -> None:
    text = report().render()
    assert "scope=get_sign" in text
    assert "2 conditional site(s) instrumented" in text
    assert "2 hit(s) in execution order" in text


def test_render_hit_lines_in_order() -> None:
    text = report().render()
    assert "#1 0x1149 <get_sign> jne  -> taken  [x=4198449]" in text
    assert "#2 0x1158 <get_sign> jge" in text
    assert text.index("#1") < text.index("#2")


def test_render_per_site_summary_and_note() -> None:
    text = report().render()
    assert "per-site summary (hit sites only):" in text
    assert "0x1149 <get_sign> jne: 1 hit(s) (taken; constant within this run)" in text
    assert "deferred to M3" in text


def test_render_empty_trace() -> None:
    text = TraceReport(scope="module", sites=(SITE_JNE,), hits=()).render()
    assert "0 hit(s) in execution order" in text
    assert "per-site summary" not in text


def test_varying_annotation_on_divergent_states() -> None:
    rep = TraceReport(
        scope="get_sign",
        sites=(SITE_JNE,),
        hits=(
            Hit(1, SITE_JNE, (("x", "0"),), "fallthrough"),
            Hit(2, SITE_JNE, (("x", "5"),), "taken"),
        ),
    )
    assert "varying" in rep.render()
