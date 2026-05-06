"""Human-readable trace rendering for `maury rules trace`.

Output format (concise, one rule per line):

    classification: home (host_overlay=rosa, confidence=high)
    forbidden_by: -

    rules considered:
      [hit ] hostname-rosa     classify  pattern /\\brosa\\b/ matched
      [miss] hostname-kamek    classify  pattern /\\bkamek\\b/ did not match
      [miss] redact-home-net   forbid    pattern /\\b192\\.168...\\b/ did not match

A `--verbose` mode (planned) will also dump the rule's reason/added date.
"""

from __future__ import annotations

from .schema import Classification


def render_trace(classification: Classification, *, show_misses: bool = True) -> str:
    """Format a Classification as a human-readable string."""
    lines: list[str] = []

    if classification.profile:
        bits = [f"classification: {classification.profile}"]
        extras = []
        if classification.host_overlay:
            extras.append(f"host_overlay={classification.host_overlay}")
        extras.append(f"confidence={classification.confidence.value}")
        bits.append(f"({', '.join(extras)})")
        lines.append(" ".join(bits))
    else:
        lines.append("classification: manual (no allowed classify rule fired)")

    if classification.forbidden_by:
        lines.append(f"forbidden_by: {', '.join(classification.forbidden_by)}")
    else:
        lines.append("forbidden_by: -")

    lines.append("")
    lines.append("rules considered:")
    if not classification.trace:
        lines.append("  (no rules in ruleset)")
    else:
        # Compute alignment widths
        id_w = max(len(t.rule_id) for t in classification.trace)
        kind_w = max(len(t.rule_kind.value) for t in classification.trace)
        for entry in classification.trace:
            if not entry.matched and not show_misses:
                continue
            mark = "hit " if entry.matched else "miss"
            lines.append(f"  [{mark}] {entry.rule_id:<{id_w}}  {entry.rule_kind.value:<{kind_w}}  {entry.detail}")

    return "\n".join(lines)
