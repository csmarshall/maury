"""Anti-pattern checks for CLAUDE.md.

Each check function takes the file text (and pre-split lines) and returns
a list of Findings. A check should fire only when it has high confidence
the rule applies — false positives erode user trust faster than false
negatives.

References to the Anthropic best-practices doc are quoted in each
finding's `rubric_quote` so the user can trace any flag back to the
source.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable

from .report import Finding, Severity

# Soft / hard length thresholds.
# The rubric does not give specific numbers; these are conservative defaults
# operators can tune. The point is to flag growth, not enforce a hard cap.
LINES_WARN = 200
LINES_ERROR = 400
CHARS_WARN = 8_000
CHARS_ERROR = 16_000

# Phrases the doc explicitly calls out as platitudes, plus close variants.
PLATITUDE_PATTERNS = [
    (r"\bwrite\s+clean\s+code\b", "write clean code"),
    (r"\bclean\s+code\b", "clean code"),
    (r"\bgood\s+code\b", "good code"),
    (r"\bwrite\s+good\s+code\b", "write good code"),
    (r"\bfollow\s+best\s+practices\b", "follow best practices"),
    (r"\bbest\s+practices\b(?!\s+for\b)", "best practices"),  # "best practices for X" is OK
    (r"\bbe\s+careful\b", "be careful"),
    (r"\bbe\s+cautious\b", "be cautious"),
    (r"\bproper\s+naming\b", "proper naming"),
    (r"\buse\s+meaningful\s+names\b", "use meaningful names"),
]

# Standard conventions Claude already knows. Match phrases that restate
# them as if they were project-specific style.
STANDARD_CONVENTION_PATTERNS = [
    (r"\buse\s+camelCase\b", "use camelCase (Claude knows JS/TS conventions)"),
    (r"\buse\s+snake_case\b", "use snake_case (Claude knows Python conventions)"),
    (r"\buse\s+PascalCase\b", "use PascalCase (Claude knows class-name conventions)"),
    (r"\buse\s+kebab-case\b(?!\s+for\s+(URL|api))", "use kebab-case (Claude knows the convention)"),
    (r"\bfollow\s+PEP[\s-]?8\b", "follow PEP 8 (Claude already does)"),
    (r"\buse\s+(single|double)\s+quotes\b(?!\s+(for|because|when))", "quote-style restated without rationale"),
    (r"\bdon'?t\s+use\s+tabs\b", "don't use tabs (Claude defaults to spaces in most languages)"),
    (r"\b(end|finish)\s+(files?\s+)?with\s+a?\s*newline\b", "end with newline (universal convention)"),
]

# Tutorial-style openings. Often signal long expository content that
# belongs in README, not CLAUDE.md.
TUTORIAL_OPENERS = [
    r"^\s*#+\s+(overview|introduction|about|getting\s+started)\b",
    r"^\s*the\s+codebase\s+(consists|is\s+structured)",
    r"^\s*this\s+(project|codebase|repository)\s+(is|contains|provides|implements)",
]


def _check_length(text: str, lines: list[str], path: str) -> list[Finding]:
    """Flag if CLAUDE.md exceeds the soft (warn) or hard (error) line/char thresholds."""
    line_count = len(lines)
    char_count = len(text)
    findings: list[Finding] = []

    if line_count >= LINES_ERROR or char_count >= CHARS_ERROR:
        findings.append(
            Finding(
                check_id="over-specified",
                severity=Severity.ERROR,
                message=(
                    f"CLAUDE.md is very long ({line_count} lines / "
                    f"{char_count:,} chars). The rubric warns this dilutes "
                    f"every rule."
                ),
                rubric_quote=(
                    "If your CLAUDE.md is too long, Claude ignores half of "
                    "it because important rules get lost in the noise."
                ),
                fix_suggestion=(
                    "Prune ruthlessly. For each line ask: 'Would removing "
                    "this cause Claude to make mistakes?' If not, cut it. "
                    "Move domain knowledge to skills, deterministic actions to hooks."
                ),
            )
        )
    elif line_count >= LINES_WARN or char_count >= CHARS_WARN:
        findings.append(
            Finding(
                check_id="over-specified",
                severity=Severity.WARN,
                message=(
                    f"CLAUDE.md is approaching the size where rules start "
                    f"getting lost ({line_count} lines / {char_count:,} chars)."
                ),
                rubric_quote=("Bloated CLAUDE.md files cause Claude to ignore your actual instructions!"),
                fix_suggestion=(
                    "Consider extracting domain knowledge into skills "
                    "(loaded on demand) and deterministic actions into hooks."
                ),
            )
        )

    return findings


def _check_platitudes(text: str, lines: list[str], path: str) -> list[Finding]:
    """Flag lines containing self-evident platitudes the rubric calls out."""
    findings: list[Finding] = []
    for line_num, line in enumerate(lines, start=1):
        for pattern, label in PLATITUDE_PATTERNS:
            if re.search(pattern, line, flags=re.IGNORECASE):
                findings.append(
                    Finding(
                        check_id="platitude",
                        severity=Severity.INFO,
                        message=f"line contains the platitude {label!r}",
                        rubric_quote=("Self-evident practices like 'write clean code'"),
                        fix_suggestion=(
                            "Delete this line, or replace it with a specific, "
                            "verifiable rule (e.g., a hook that runs a linter)."
                        ),
                        line=line_num,
                        excerpt=_truncate(line.strip(), 120),
                    )
                )
                break  # one finding per line is enough
    return findings


def _check_standard_conventions(text: str, lines: list[str], path: str) -> list[Finding]:
    """Flag lines restating well-known language conventions Claude already knows."""
    findings: list[Finding] = []
    for line_num, line in enumerate(lines, start=1):
        for pattern, label in STANDARD_CONVENTION_PATTERNS:
            if re.search(pattern, line, flags=re.IGNORECASE):
                findings.append(
                    Finding(
                        check_id="standard-convention",
                        severity=Severity.INFO,
                        message=f"line restates a standard convention: {label}",
                        rubric_quote=("Standard language conventions Claude already knows"),
                        fix_suggestion=(
                            "Delete this line. Claude defaults to the standard "
                            "convention for the language. Only mention it if "
                            "you deviate from the standard."
                        ),
                        line=line_num,
                        excerpt=_truncate(line.strip(), 120),
                    )
                )
                break
    return findings


def _check_tutorial_style(text: str, lines: list[str], path: str) -> list[Finding]:
    """Flag opening lines that look like README/tutorial content rather than rules."""
    findings: list[Finding] = []
    for line_num, line in enumerate(lines, start=1):
        for pattern in TUTORIAL_OPENERS:
            if re.search(pattern, line, flags=re.IGNORECASE):
                findings.append(
                    Finding(
                        check_id="tutorial-style",
                        severity=Severity.INFO,
                        message=(
                            "line opens like tutorial / overview content, "
                            "which usually belongs in README rather than CLAUDE.md"
                        ),
                        rubric_quote=("Long explanations or tutorials"),
                        fix_suggestion=(
                            "Move tutorial content to README.md or a skill. CLAUDE.md should be short and rule-shaped."
                        ),
                        line=line_num,
                        excerpt=_truncate(line.strip(), 120),
                    )
                )
                break
    return findings


def _check_file_by_file(text: str, lines: list[str], path: str) -> list[Finding]:
    """Heuristic: if there's a run of 4+ consecutive bullet lines that each
    reference a file path or extension, the user likely wrote a file-by-file
    description.
    """
    findings: list[Finding] = []
    bullet_re = re.compile(r"^\s*[-*+]\s+")
    file_ref_re = re.compile(r"\.(py|js|ts|tsx|jsx|rs|go|rb|java|kt|swift|md|sh|yml|yaml|json|toml)\b|/[a-z_-]+/")

    run_start: int | None = None
    run_count = 0

    def maybe_emit(end_line: int) -> None:
        if run_start is not None and run_count >= 4:
            findings.append(
                Finding(
                    check_id="file-by-file",
                    severity=Severity.INFO,
                    message=(
                        f"a run of {run_count} bullet lines mentioning files "
                        f"starting near line {run_start} looks like a "
                        f"file-by-file description"
                    ),
                    rubric_quote=("File-by-file descriptions of the codebase"),
                    fix_suggestion=(
                        "Delete the file-by-file list. Claude can read the "
                        "directory tree itself; this content takes context "
                        "without earning it."
                    ),
                    line=run_start,
                )
            )

    for line_num, line in enumerate(lines, start=1):
        if bullet_re.match(line) and file_ref_re.search(line):
            if run_start is None:
                run_start = line_num
            run_count += 1
        else:
            maybe_emit(line_num - 1)
            run_start = None
            run_count = 0
    maybe_emit(len(lines))

    return findings


# Public registry of all checks. Order matters only for output stability;
# each check is independent.
CheckFn = Callable[[str, list[str], str], list[Finding]]

_REGISTRY: list[tuple[str, CheckFn]] = [
    ("over-specified", _check_length),
    ("platitude", _check_platitudes),
    ("standard-convention", _check_standard_conventions),
    ("tutorial-style", _check_tutorial_style),
    ("file-by-file", _check_file_by_file),
]


def all_checks() -> Iterable[tuple[str, CheckFn]]:
    """Return the registry of all checks `(check_id, fn)`, in canonical order."""
    return list(_REGISTRY)


def run_all(text: str, source_path: str) -> list[Finding]:
    """Run every registered check against the given CLAUDE.md text."""
    lines = text.splitlines()
    findings: list[Finding] = []
    for _name, check in _REGISTRY:
        findings.extend(check(text, lines, source_path))
    return findings


def _truncate(s: str, n: int) -> str:
    """Truncate a string to at most n characters, appending an ellipsis if cut."""
    return s if len(s) <= n else s[: n - 1] + "…"
