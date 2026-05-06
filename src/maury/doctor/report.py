"""Report types and renderers for `maury doctor`."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class Severity(StrEnum):
    """How loud a doctor finding is — info / warn / error, with `--fail-on` thresholds."""

    INFO = "info"
    WARN = "warn"
    ERROR = "error"


@dataclass(frozen=True)
class Finding:
    """One anti-pattern hit in the evaluated CLAUDE.md."""

    check_id: str  # e.g., "over-specified", "platitude", "standard-convention"
    severity: Severity
    message: str  # one-line description of what was found
    rubric_quote: str  # excerpt from the Anthropic doc that motivated the check
    fix_suggestion: str  # what to do about it
    line: int | None = None  # 1-indexed; None if check is whole-file
    excerpt: str | None = None  # the offending text, truncated


@dataclass
class Report:
    """The full doctor output for one CLAUDE.md file: source path, sizes, findings list."""

    source_path: str
    line_count: int
    char_count: int
    findings: list[Finding] = field(default_factory=list)
    rubric_url: str = "https://code.claude.com/docs/en/best-practices"


def render_text(report: Report) -> str:
    """Human-readable text rendering."""
    lines: list[str] = []
    lines.append("maury doctor — CLAUDE.md content quality evaluation")
    lines.append(f"rubric: {report.rubric_url}")
    lines.append("")
    lines.append(f"evaluating: {report.source_path}")
    lines.append(f"size: {report.line_count} lines, {report.char_count:,} chars")
    lines.append("")

    if not report.findings:
        lines.append("findings: none — looks clean against the v1 rubric checks.")
        return "\n".join(lines)

    by_severity = {Severity.ERROR: 0, Severity.WARN: 0, Severity.INFO: 0}
    for f in report.findings:
        by_severity[f.severity] = by_severity.get(f.severity, 0) + 1

    summary = (
        f"findings: {len(report.findings)} total — "
        f"{by_severity[Severity.ERROR]} error, "
        f"{by_severity[Severity.WARN]} warn, "
        f"{by_severity[Severity.INFO]} info"
    )
    lines.append(summary)
    lines.append("")

    for f in report.findings:
        tag = f"[{f.severity.value.upper()}] {f.check_id}"
        if f.line is not None:
            tag += f" (line {f.line})"
        lines.append(tag)
        if f.excerpt:
            lines.append(f"    excerpt: {f.excerpt}")
        lines.append(f"    {f.message}")
        lines.append(f'    rubric: "{f.rubric_quote}"')
        lines.append(f"    fix:    {f.fix_suggestion}")
        lines.append("")

    return "\n".join(lines).rstrip()


def render_json(report: Report) -> str:
    """Machine-readable JSON rendering."""
    payload: dict[str, Any] = {
        "source_path": report.source_path,
        "rubric_url": report.rubric_url,
        "line_count": report.line_count,
        "char_count": report.char_count,
        "findings": [{**asdict(f), "severity": f.severity.value} for f in report.findings],
        "summary": {
            "total": len(report.findings),
            "error": sum(1 for f in report.findings if f.severity == Severity.ERROR),
            "warn": sum(1 for f in report.findings if f.severity == Severity.WARN),
            "info": sum(1 for f in report.findings if f.severity == Severity.INFO),
        },
    }
    return json.dumps(payload, indent=2)
