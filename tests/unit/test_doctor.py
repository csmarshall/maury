"""Tests for `maury doctor` checks.

The Anthropic rubric is the authoritative source; tests verify that each
check fires correctly on the explicit anti-patterns the doc names, and
does not fire on legitimate content.
"""

from __future__ import annotations

import json

from maury.doctor import Report, render_json, render_text, run_all
from maury.doctor.checks import (
    CHARS_ERROR,
    CHARS_WARN,
    LINES_ERROR,
    LINES_WARN,
)
from maury.doctor.report import Severity

# ---- length / over-specification -----------------------------------------


def test_short_file_no_length_finding():
    text = "# Code style\n- short and tight\n"
    findings = run_all(text, "test.md")
    assert not any(f.check_id == "over-specified" for f in findings)


def test_warn_at_lines_threshold():
    text = "\n".join(["- line"] * (LINES_WARN + 1)) + "\n"
    findings = run_all(text, "test.md")
    overspec = [f for f in findings if f.check_id == "over-specified"]
    assert len(overspec) == 1
    assert overspec[0].severity == Severity.WARN


def test_error_at_lines_threshold():
    text = "\n".join(["- line"] * (LINES_ERROR + 1)) + "\n"
    findings = run_all(text, "test.md")
    overspec = [f for f in findings if f.check_id == "over-specified"]
    assert len(overspec) == 1
    assert overspec[0].severity == Severity.ERROR


def test_warn_at_chars_threshold():
    # short on lines but long on chars
    text = "x" * (CHARS_WARN + 1)
    findings = run_all(text, "test.md")
    overspec = [f for f in findings if f.check_id == "over-specified"]
    assert any(f.severity == Severity.WARN for f in overspec)


def test_error_at_chars_threshold():
    text = "x" * (CHARS_ERROR + 1)
    findings = run_all(text, "test.md")
    overspec = [f for f in findings if f.check_id == "over-specified"]
    assert any(f.severity == Severity.ERROR for f in overspec)


# ---- platitudes ----------------------------------------------------------


def test_platitude_write_clean_code():
    text = "- Write clean code at all times.\n"
    findings = [f for f in run_all(text, "t.md") if f.check_id == "platitude"]
    assert len(findings) == 1
    assert findings[0].line == 1


def test_platitude_good_code():
    text = "- Always strive for good code.\n"
    findings = [f for f in run_all(text, "t.md") if f.check_id == "platitude"]
    assert len(findings) == 1


def test_platitude_be_careful():
    text = "- Be careful with database changes.\n"
    findings = [f for f in run_all(text, "t.md") if f.check_id == "platitude"]
    assert len(findings) == 1


def test_platitude_best_practices_unqualified():
    text = "- Follow best practices.\n"
    findings = [f for f in run_all(text, "t.md") if f.check_id == "platitude"]
    assert len(findings) == 1


def test_platitude_best_practices_qualified_does_not_fire():
    text = "- Use best practices for retry logic in @docs/retry.md\n"
    findings = [f for f in run_all(text, "t.md") if f.check_id == "platitude"]
    # "best practices for X" is not flagged as a platitude because it's qualified
    assert findings == []


def test_platitude_one_finding_per_line():
    """Multiple platitude phrases on one line should produce a single finding."""
    text = "- write clean code and follow best practices\n"
    findings = [f for f in run_all(text, "t.md") if f.check_id == "platitude"]
    assert len(findings) == 1


# ---- standard conventions ------------------------------------------------


def test_standard_convention_camelCase():  # noqa: N802 — testing camelCase detection by name
    text = "- Use camelCase in JavaScript.\n"
    findings = [f for f in run_all(text, "t.md") if f.check_id == "standard-convention"]
    assert len(findings) == 1


def test_standard_convention_snake_case():
    text = "- Use snake_case for variables.\n"
    findings = [f for f in run_all(text, "t.md") if f.check_id == "standard-convention"]
    assert len(findings) == 1


def test_standard_convention_pep8():
    text = "- Follow PEP 8.\n"
    findings = [f for f in run_all(text, "t.md") if f.check_id == "standard-convention"]
    assert len(findings) == 1


def test_standard_convention_kebab_case_for_url_does_not_fire():
    """kebab-case for URL paths is genuinely a project convention worth stating."""
    text = "- Use kebab-case for URL paths\n"
    findings = [f for f in run_all(text, "t.md") if f.check_id == "standard-convention"]
    assert findings == []


def test_standard_convention_quote_style_with_rationale_does_not_fire():
    text = "- Use single quotes when interpolating environment variables.\n"
    findings = [f for f in run_all(text, "t.md") if f.check_id == "standard-convention"]
    assert findings == []


# ---- tutorial-style ------------------------------------------------------


def test_tutorial_style_overview_header():
    text = "# Overview\n\nThis project does stuff.\n"
    findings = [f for f in run_all(text, "t.md") if f.check_id == "tutorial-style"]
    assert any(f.line == 1 for f in findings)


def test_tutorial_style_codebase_consists():
    text = "The codebase consists of three layers.\n"
    findings = [f for f in run_all(text, "t.md") if f.check_id == "tutorial-style"]
    assert len(findings) == 1


# ---- file-by-file --------------------------------------------------------


def test_file_by_file_run_of_four_or_more():
    text = """
# Architecture
- src/auth.py: handles authentication
- src/db.py: database connection
- src/api/users.py: user endpoints
- src/api/posts.py: post endpoints
"""
    findings = [f for f in run_all(text, "t.md") if f.check_id == "file-by-file"]
    assert len(findings) == 1


def test_file_by_file_short_run_does_not_fire():
    """Three or fewer file references in a row is not yet a 'description.'"""
    text = """
- src/auth.py: handles auth
- src/db.py: db
"""
    findings = [f for f in run_all(text, "t.md") if f.check_id == "file-by-file"]
    assert findings == []


# ---- clean files ---------------------------------------------------------


def test_canonical_short_clean_file():
    """A small, well-formed CLAUDE.md should produce zero findings."""
    text = """# Code style
- Use ES modules (import/export) syntax, not CommonJS (require)
- Destructure imports when possible (eg. import { foo } from 'bar')

# Workflow
- Be sure to typecheck when you're done making a series of code changes
- Prefer running single tests, and not the whole test suite, for performance
"""
    findings = run_all(text, "t.md")
    assert findings == [], f"unexpected findings on the canonical Anthropic example: {[f.check_id for f in findings]}"


# ---- renderers -----------------------------------------------------------


def test_render_text_with_no_findings():
    report = Report(source_path="x.md", line_count=10, char_count=200)
    out = render_text(report)
    assert "looks clean" in out
    assert "x.md" in out


def test_render_text_with_findings():
    text = "- write clean code\n- use camelCase\n"
    findings = run_all(text, "t.md")
    report = Report(
        source_path="t.md",
        line_count=2,
        char_count=len(text),
        findings=findings,
    )
    out = render_text(report)
    assert "platitude" in out
    assert "standard-convention" in out
    assert "rubric:" in out


def test_render_json_is_parseable():
    text = "- write clean code\n"
    findings = run_all(text, "t.md")
    report = Report(
        source_path="t.md",
        line_count=1,
        char_count=len(text),
        findings=findings,
    )
    out = render_json(report)
    data = json.loads(out)
    assert data["source_path"] == "t.md"
    assert data["summary"]["total"] == len(findings)
    assert data["findings"][0]["check_id"] == "platitude"
    # severity must be the string value, not the enum repr
    assert data["findings"][0]["severity"] in {"info", "warn", "error"}
