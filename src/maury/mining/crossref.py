"""Cross-reference: classify each mined finding vs current + historical config.

Per ADR-0020 four-state model:

  NEW                    Pattern not in CLAUDE.md.
  PRESENT_AND_CLEAR      In CLAUDE.md, user mention is consistent. Suppress.
  PRESENT_BUT_UNCLEAR    In CLAUDE.md but wording may be unclear.
  PRESENT_AND_REINFORCED In CLAUDE.md AND user is correcting Claude. Investigate.

Temporal awareness: if the synced CLAUDE.md is in a git repo, this
module reconstructs CLAUDE.md as it was at the transcript's timestamp
via `git rev-list --before=<ts>` + `git show <sha>:<path>`. Without
git history (e.g., before the user has set up a synced repo), the
cross-reference falls back to current-CLAUDE.md-only mode and the LLM
is told to be conservative about REINFORCED classification.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from string import Template
from textwrap import dedent

from maury.llm import LLMClient

from .extractor import Finding

# ---- result type --------------------------------------------------------


@dataclass(frozen=True)
class CrossRefResult:
    """Output of one cross-reference call: state + rationale + action."""

    state: str  # NEW | PRESENT_AND_CLEAR | PRESENT_BUT_UNCLEAR | PRESENT_AND_REINFORCED
    claude_md_quote: str  # the matching CLAUDE.md excerpt, or empty for NEW
    rationale: str  # one-sentence explanation of why this state
    suggested_action: str  # propose-new | suppress | rephrase-existing | investigate-why-not-followed
    raw_response: str = ""  # full LLM response, for debugging


# ---- prompt -------------------------------------------------------------


_CROSSREF_PROMPT_HEAD = dedent(
    """\
    You are evaluating one mined preference candidate against a user's
    existing CLAUDE.md. Classify into one of four states.

    The four states:

      NEW                    Pattern is not represented in current CLAUDE.md.
      PRESENT_AND_CLEAR      Pattern IS in CLAUDE.md with clear phrasing; the
                             user mentioning it again is just consistent.
      PRESENT_BUT_UNCLEAR    Pattern IS in CLAUDE.md but wording may be
                             ambiguous, buried, or contradicted elsewhere.
      PRESENT_AND_REINFORCED Pattern IS in CLAUDE.md AND the surrounding
                             transcript context shows the user CORRECTING
                             Claude (telling it to do X or stop doing Y).
                             Strong signal the rule isn't being followed.

    Output ONE JSON object, NOTHING ELSE:

      {
        "state": "NEW" | "PRESENT_AND_CLEAR" | "PRESENT_BUT_UNCLEAR" | "PRESENT_AND_REINFORCED",
        "claude_md_quote": "<the line(s) from CLAUDE.md that match, or empty>",
        "rationale": "<one sentence on why you picked this state>",
        "suggested_action": "propose-new" | "suppress" | "rephrase-existing" | "investigate-why-not-followed"
      }

    """
)

_CROSSREF_PROMPT_BODY = Template(
    dedent(
        """\
        --- USER'S CURRENT CLAUDE.md ---
        $current_claude_md

        $historical_block
        --- MINED FINDING ---
        kind: $finding_kind
        scope_hint: $finding_scope
        text: $finding_text
        evidence (verbatim quote from transcript): $evidence
        transcript timestamp: $transcript_ts
        """
    )
)

_HISTORICAL_PRESENT = Template(
    dedent(
        """\
        --- CLAUDE.md AS IT WAS AT THE TRANSCRIPT TIMESTAMP ---
        $historical_claude_md

        IMPORTANT TEMPORAL CONTEXT: When deciding REINFORCED vs already-promoted,
        check whether the rule existed in the *historical* CLAUDE.md at the
        transcript timestamp. If the rule wasn't in the historical version
        but IS in the current version, the user was establishing the rule —
        suppress as already-promoted (PRESENT_AND_CLEAR), not REINFORCED.

        """
    )
)

_HISTORICAL_ABSENT = dedent(
    """\
    --- CLAUDE.md HISTORY: NOT AVAILABLE ---
    (No git history accessible. Be conservative about REINFORCED — without
    knowing whether the rule existed at transcript-time, prefer
    PRESENT_AND_CLEAR or PRESENT_BUT_UNCLEAR unless the transcript clearly
    shows the user pointing at an existing rule.)

    """
)


# ---- public API ---------------------------------------------------------


def crossref_finding(
    finding: Finding,
    *,
    llm: LLMClient,
    current_claude_md: str,
    historical_claude_md: str | None = None,
    timeout: float = 60.0,
) -> CrossRefResult:
    """Cross-reference one finding against current + (optionally) historical CLAUDE.md.

    Args:
        finding: A Finding from the extractor.
        llm: Backend (use `maury.llm.get_backend()`).
        current_claude_md: Full text of the current rendered CLAUDE.md.
        historical_claude_md: CLAUDE.md as it was at the transcript
            timestamp, if reconstructable from git history. None = no
            history available; the LLM is told to be conservative.
        timeout: Per-call LLM timeout.

    Returns:
        CrossRefResult.
    """
    if historical_claude_md is not None:
        historical_block = _HISTORICAL_PRESENT.substitute(historical_claude_md=historical_claude_md)
    else:
        historical_block = _HISTORICAL_ABSENT

    body = _CROSSREF_PROMPT_BODY.substitute(
        current_claude_md=current_claude_md,
        historical_block=historical_block,
        finding_kind=finding.kind,
        finding_scope=finding.scope_hint,
        finding_text=finding.text,
        evidence=finding.evidence,
        transcript_ts=finding.source_window.last_timestamp or "(unknown)",
    )
    prompt = _CROSSREF_PROMPT_HEAD + body
    raw = llm.call(prompt, timeout=timeout)
    return _parse_crossref(raw)


def _parse_crossref(raw_text: str) -> CrossRefResult:
    """Pull the single JSON object out of the LLM output."""
    start = raw_text.find("{")
    end = raw_text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return CrossRefResult(
            state="NEW",  # safe fallback so a parse failure doesn't suppress
            claude_md_quote="",
            rationale=f"crossref response unparseable: {raw_text[:200]!r}",
            suggested_action="propose-new",
            raw_response=raw_text,
        )
    try:
        obj = json.loads(raw_text[start : end + 1])
    except json.JSONDecodeError as e:
        return CrossRefResult(
            state="NEW",
            claude_md_quote="",
            rationale=f"crossref JSON invalid: {e}",
            suggested_action="propose-new",
            raw_response=raw_text,
        )
    return CrossRefResult(
        state=str(obj.get("state", "NEW")),
        claude_md_quote=str(obj.get("claude_md_quote", "")),
        rationale=str(obj.get("rationale", "")),
        suggested_action=str(obj.get("suggested_action", "propose-new")),
        raw_response=raw_text,
    )


# ---- temporal helper ---------------------------------------------------


def get_claude_md_at_timestamp(
    repo_path: Path,
    relative_path: str,
    timestamp: str,
) -> str | None:
    """Return CLAUDE.md as it was in `repo_path` at `timestamp`, or None.

    Implementation: find the latest commit whose author-date is at or
    before `timestamp` via `git rev-list --before=<ts> -1 HEAD`, then
    `git show <sha>:<relative_path>`.

    Returns None when:
      - repo_path isn't a git repo (no `.git` dir)
      - no commits exist at or before the timestamp
      - the file didn't exist at that commit
      - any git command fails
    """
    if not (repo_path / ".git").exists():
        return None
    try:
        rev = subprocess.run(
            ["git", "-C", str(repo_path), "rev-list", "--before", timestamp, "-1", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        ).stdout.strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return None
    if not rev:
        return None
    try:
        out = subprocess.run(
            ["git", "-C", str(repo_path), "show", f"{rev}:{relative_path}"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        ).stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return None
    return out


# ---- bulk operation -----------------------------------------------------


@dataclass
class CrossRefSummary:
    """Aggregated results across many findings."""

    by_state: dict[str, list[tuple[Finding, CrossRefResult]]]

    @classmethod
    def empty(cls) -> CrossRefSummary:
        return cls(
            by_state={
                "NEW": [],
                "PRESENT_AND_CLEAR": [],
                "PRESENT_BUT_UNCLEAR": [],
                "PRESENT_AND_REINFORCED": [],
            }
        )

    def add(self, finding: Finding, result: CrossRefResult) -> None:
        bucket = self.by_state.setdefault(result.state, [])
        bucket.append((finding, result))

    def total(self) -> int:
        return sum(len(v) for v in self.by_state.values())


def crossref_findings(
    findings: list[Finding],
    *,
    llm: LLMClient,
    current_claude_md: str,
    repo_path: Path | None = None,
    claude_md_relative_path: str = "CLAUDE.md",
    timeout: float = 60.0,
    on_progress: object | None = None,
) -> CrossRefSummary:
    """Cross-reference a list of findings; bucket results by state.

    `repo_path`, when provided and a git repo, enables temporal-aware
    historical CLAUDE.md lookup per finding's transcript timestamp.

    `on_progress(i, total, finding, result)` is called after each finding
    (optional callback for CLI progress output).
    """
    summary = CrossRefSummary.empty()
    total = len(findings)
    for i, finding in enumerate(findings, start=1):
        historical: str | None = None
        if repo_path is not None and finding.source_window.last_timestamp:
            historical = get_claude_md_at_timestamp(
                repo_path,
                claude_md_relative_path,
                finding.source_window.last_timestamp,
            )
        try:
            result = crossref_finding(
                finding,
                llm=llm,
                current_claude_md=current_claude_md,
                historical_claude_md=historical,
                timeout=timeout,
            )
        except Exception as e:
            result = CrossRefResult(
                state="NEW",
                claude_md_quote="",
                rationale=f"crossref failed: {e}",
                suggested_action="propose-new",
            )
        summary.add(finding, result)
        if on_progress is not None and callable(on_progress):
            on_progress(i, total, finding, result)
    return summary


__all__ = [
    "CrossRefResult",
    "CrossRefSummary",
    "crossref_finding",
    "crossref_findings",
    "get_claude_md_at_timestamp",
]
