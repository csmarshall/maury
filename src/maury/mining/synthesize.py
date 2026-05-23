"""Rule synthesis — propose CLAUDE.md wording for findings the
cross-reference flagged as already-present-but-not-working (Phase 8,
path B per the 2026-05-22 design pass).

The four-state cross-reference (ADR-0020, `crossref.py`) already
*classifies* a finding and, for two of the states, says the existing
rule needs attention:

  PRESENT_BUT_UNCLEAR    → `rephrase-existing`            (reword for clarity)
  PRESENT_AND_REINFORCED → `investigate-why-not-followed` (Claude ignored a
                                                           clear rule → strengthen)

This module adds the *generation* step crossref stops short of: given the
existing rule text crossref matched (`claude_md_quote`) plus the mined
evidence, ask the LLM to produce a revised wording. The output is
**advisory** — it rides in the finding's `mining-findings.md` block as a
"proposed rewrite" section; the operator applies it to the right source
file by hand (V1 staging-file model per ADR-0022). We do not auto-locate
the source rule or emit an applied diff.

`classify`-rule synthesis into `rules.yaml` (ADR-0004's literal Phase 8)
is deferred until per-mode auto-placement exists — see the 2026-05-22
design pass in session-state.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from string import Template
from textwrap import dedent
from typing import TYPE_CHECKING

from maury.llm import BackendUnavailableError

if TYPE_CHECKING:
    from maury.llm import LLMClient
    from maury.mining.crossref import CrossRefResult
    from maury.mining.extractor import Finding

# The two cross-reference actions that warrant a generated rewrite.
SYNTHESIS_ACTIONS = frozenset({"rephrase-existing", "investigate-why-not-followed"})

# Per-action framing: what the rewrite is trying to achieve.
_GOALS = {
    "rephrase-existing": (
        "The rule's current wording may be ambiguous, buried, or contradicted "
        "elsewhere, so Claude isn't reliably following it. Reword it to be clear "
        "and unambiguous. Keep the same intent; do not invent new requirements."
    ),
    "investigate-why-not-followed": (
        "The rule is present and clear, yet the transcript shows the user having "
        "to correct Claude for not following it. Strengthen the wording so it is "
        "harder to overlook (e.g. more emphatic, more specific, better placed). "
        "Keep the same intent; do not invent new requirements."
    ),
}


@dataclass(frozen=True)
class RewriteProposal:
    """An advisory proposed rewrite of an existing CLAUDE.md rule."""

    action: str
    """The crossref `suggested_action` that triggered synthesis."""

    existing_quote: str
    """The existing CLAUDE.md text crossref matched (what we're rewriting)."""

    proposed_text: str
    """The LLM's proposed replacement wording."""

    rationale: str
    """One sentence on why the new wording is better."""

    raw_response: str = ""


_PROMPT = Template(
    dedent(
        """\
        You are improving ONE rule in a user's CLAUDE.md (the standing
        instructions an AI coding assistant reads). The rule already exists.

        Goal: $goal

        Existing rule text (from the user's CLAUDE.md):
        ---
        $existing_quote
        ---

        The mined preference that surfaced this (kind=$finding_kind):
        $finding_text

        Verbatim evidence from the transcript:
        $evidence

        Output ONE JSON object, NOTHING ELSE:

          {
            "proposed_text": "<the revised rule wording, ready to paste into CLAUDE.md>",
            "rationale": "<one sentence on why this wording is better>"
          }
        """
    )
)


def synthesize_rewrite(
    finding: Finding,
    crossref_result: CrossRefResult,
    *,
    llm: LLMClient,
    timeout: float = 60.0,
) -> RewriteProposal | None:
    """Generate an advisory rewrite for a finding crossref flagged as
    needing one, or None if it doesn't qualify.

    Returns None when the finding's `suggested_action` is not a synthesis
    trigger (`propose-new`/`suppress`), when there's no existing rule text
    to rewrite, or when the LLM yields no usable proposal. Synthesis never
    aborts mining — a parse failure degrades to None.
    """
    action = crossref_result.suggested_action
    if action not in SYNTHESIS_ACTIONS:
        return None
    existing = crossref_result.claude_md_quote.strip()
    if not existing:
        # Nothing concrete to rewrite (crossref matched the substance but
        # didn't quote a line); skip rather than hallucinate a target.
        return None

    prompt = _PROMPT.substitute(
        goal=_GOALS[action],
        existing_quote=existing,
        finding_kind=finding.kind,
        finding_text=finding.text,
        evidence=finding.evidence or "(no evidence captured)",
    )
    try:
        raw = llm.call(prompt, timeout=timeout)
    except (BackendUnavailableError, OSError, subprocess.SubprocessError):
        return None  # backend unusable at call time → no rewrite (best-effort)
    proposed, rationale = _parse_rewrite(raw)
    if not proposed:
        return None
    return RewriteProposal(
        action=action,
        existing_quote=existing,
        proposed_text=proposed,
        rationale=rationale,
        raw_response=raw,
    )


def _parse_rewrite(raw_text: str) -> tuple[str, str]:
    """Pull (proposed_text, rationale) from the LLM JSON. Returns
    ('', '') on any parse failure so the caller degrades to None."""
    start = raw_text.find("{")
    end = raw_text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return "", ""
    try:
        obj = json.loads(raw_text[start : end + 1])
    except json.JSONDecodeError:
        return "", ""
    return str(obj.get("proposed_text", "")).strip(), str(obj.get("rationale", "")).strip()


__all__ = [
    "SYNTHESIS_ACTIONS",
    "RewriteProposal",
    "synthesize_rewrite",
]
