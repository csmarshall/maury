"""Tests for `src/maury/rules/trace.py` — the human-readable trace
rendering used by `maury rules trace`.

Pure-string-formatting code; we test the output shape against
constructed Classification fixtures.
"""

from __future__ import annotations

from maury.rules.schema import Classification, Confidence, RuleKind, TraceEntry
from maury.rules.trace import render_trace


def _entry(
    rule_id: str,
    kind: RuleKind = RuleKind.CLASSIFY,
    *,
    matched: bool = True,
    detail: str = "",
) -> TraceEntry:
    return TraceEntry(rule_id=rule_id, rule_kind=kind, matched=matched, detail=detail)


# ---- header line (classification / forbidden_by) -------------------------


def test_render_trace_classification_with_profile_only() -> None:
    c = Classification(
        profile="home",
        host_overlay=None,
        confidence=Confidence.HIGH,
        trace=(_entry("r1"),),
        forbidden_by=(),
    )
    out = render_trace(c)
    first_line = out.splitlines()[0]
    assert "classification: home" in first_line
    assert "confidence=high" in first_line
    # No host_overlay extras since host_overlay is None.
    assert "host_overlay=" not in first_line


def test_render_trace_classification_with_host_overlay() -> None:
    c = Classification(
        profile="home",
        host_overlay="server",
        confidence=Confidence.MEDIUM,
        trace=(_entry("r1"),),
        forbidden_by=(),
    )
    out = render_trace(c)
    first_line = out.splitlines()[0]
    assert "classification: home" in first_line
    assert "host_overlay=server" in first_line
    assert "confidence=medium" in first_line


def test_render_trace_manual_fallback_when_no_profile() -> None:
    """profile=None means no allowed classify rule fired — manual review."""
    c = Classification(
        profile=None,
        host_overlay=None,
        confidence=Confidence.LOW,
        trace=(),
        forbidden_by=(),
    )
    out = render_trace(c)
    first_line = out.splitlines()[0]
    assert "classification: manual" in first_line
    assert "no allowed classify rule fired" in first_line


def test_render_trace_forbidden_by_empty() -> None:
    c = Classification(
        profile="home",
        host_overlay=None,
        confidence=Confidence.HIGH,
        trace=(),
        forbidden_by=(),
    )
    out = render_trace(c)
    # forbidden_by line shows "-" when empty.
    assert "forbidden_by: -" in out


def test_render_trace_forbidden_by_populated() -> None:
    c = Classification(
        profile=None,
        host_overlay=None,
        confidence=Confidence.LOW,
        trace=(),
        forbidden_by=("redact-home-net", "redact-personal-domain"),
    )
    out = render_trace(c)
    assert "forbidden_by: redact-home-net, redact-personal-domain" in out


# ---- rules-considered section -------------------------------------------


def test_render_trace_no_rules_in_ruleset() -> None:
    c = Classification(
        profile=None,
        host_overlay=None,
        confidence=Confidence.LOW,
        trace=(),
        forbidden_by=(),
    )
    out = render_trace(c)
    assert "rules considered:" in out
    assert "(no rules in ruleset)" in out


def test_render_trace_shows_hits_and_misses_by_default() -> None:
    c = Classification(
        profile="home",
        host_overlay=None,
        confidence=Confidence.HIGH,
        trace=(
            _entry("hit-rule", matched=True, detail="pattern matched"),
            _entry("miss-rule", matched=False, detail="pattern did not match"),
        ),
        forbidden_by=(),
    )
    out = render_trace(c)
    assert "[hit ]" in out
    assert "[miss]" in out
    assert "hit-rule" in out
    assert "miss-rule" in out


def test_render_trace_hides_misses_when_show_misses_false() -> None:
    c = Classification(
        profile="home",
        host_overlay=None,
        confidence=Confidence.HIGH,
        trace=(
            _entry("hit-rule", matched=True, detail="hit"),
            _entry("miss-rule", matched=False, detail="miss"),
        ),
        forbidden_by=(),
    )
    out = render_trace(c, show_misses=False)
    assert "hit-rule" in out
    assert "miss-rule" not in out


def test_render_trace_rule_kinds_appear() -> None:
    """Each rule's kind (classify / forbid / scope) renders alongside the id."""
    c = Classification(
        profile="home",
        host_overlay=None,
        confidence=Confidence.HIGH,
        trace=(
            _entry("c1", kind=RuleKind.CLASSIFY, matched=True),
            _entry("f1", kind=RuleKind.FORBID, matched=False),
            _entry("s1", kind=RuleKind.SCOPE, matched=True),
        ),
        forbidden_by=(),
    )
    out = render_trace(c)
    assert "classify" in out
    assert "forbid" in out
    assert "scope" in out


def test_render_trace_aligns_rule_ids() -> None:
    """Trace lines should align the rule_id column based on the longest id."""
    c = Classification(
        profile="home",
        host_overlay=None,
        confidence=Confidence.HIGH,
        trace=(
            _entry("a", matched=True, detail="d1"),
            _entry("a-much-longer-id", matched=False, detail="d2"),
        ),
        forbidden_by=(),
    )
    out = render_trace(c)
    rule_lines = [line for line in out.splitlines() if line.startswith("  [")]
    assert len(rule_lines) == 2
    # The short-id line should be padded so the kind column aligns with
    # the long-id line's kind column.
    short_line, long_line = rule_lines
    # Find the position of `classify` in both lines.
    assert short_line.index("classify") == long_line.index("classify")


def test_render_trace_includes_detail_in_each_line() -> None:
    """Each trace entry's `detail` field appears in the rendered line."""
    c = Classification(
        profile="home",
        host_overlay=None,
        confidence=Confidence.HIGH,
        trace=(
            _entry("r1", matched=True, detail="pattern /foo/ matched"),
            _entry("r2", matched=False, detail="any_keyword had no matches"),
        ),
        forbidden_by=(),
    )
    out = render_trace(c)
    assert "pattern /foo/ matched" in out
    assert "any_keyword had no matches" in out


def test_render_trace_has_blank_line_before_rules_section() -> None:
    """Output structure: header / forbidden_by / blank / 'rules considered:' / entries."""
    c = Classification(
        profile="home",
        host_overlay=None,
        confidence=Confidence.HIGH,
        trace=(_entry("r1", matched=True),),
        forbidden_by=(),
    )
    out = render_trace(c)
    lines = out.splitlines()
    # Find the index of "rules considered:" — should be preceded by a blank line.
    idx = next(i for i, line in enumerate(lines) if line.startswith("rules considered:"))
    assert idx >= 2
    assert lines[idx - 1] == ""
