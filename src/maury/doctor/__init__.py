"""maury doctor — content-quality evaluator for CLAUDE.md.

Implements the rubric documented at:
  https://code.claude.com/docs/en/best-practices

Hard-coded anti-pattern checks for v1. See ADR-0011 for the rationale
behind not using the classification rule engine for this.
"""

from .checks import all_checks, run_all
from .report import Finding, Report, Severity, render_json, render_text

__all__ = [
    "Finding",
    "Report",
    "Severity",
    "all_checks",
    "render_json",
    "render_text",
    "run_all",
]
