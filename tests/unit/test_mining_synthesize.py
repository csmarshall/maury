"""Tests for `maury.mining.synthesize` — Phase 8 path B rewrite generation.

The cross-reference classifies; this module generates the proposed
wording for the two states that need one. Uses a stub LLM.
"""

from __future__ import annotations

from pathlib import Path

from maury.mining.crossref import CrossRefResult
from maury.mining.extractor import ExtractionWindow, Finding
from maury.mining.synthesize import SYNTHESIS_ACTIONS, RewriteProposal, synthesize_rewrite
from maury.mining.transcripts import TranscriptMessage


def _finding(*, kind: str = "feedback", text: str = "prefer terse responses") -> Finding:
    msg = TranscriptMessage(
        project="p",
        transcript_path=Path("/tmp/p/s.jsonl"),
        session_id="s",
        timestamp="2026-05-21T16:00:00Z",
        text=text,
    )
    return Finding(
        kind=kind,
        scope_hint="base",
        text=text,
        evidence='user: "stop padding your answers"',
        confidence="high",
        source_window=ExtractionWindow(project="p", index=0, messages=(msg,)),
    )


def _xref(*, action: str, quote: str = "Keep answers concise.") -> CrossRefResult:
    state = {
        "rephrase-existing": "PRESENT_BUT_UNCLEAR",
        "investigate-why-not-followed": "PRESENT_AND_REINFORCED",
        "propose-new": "NEW",
        "suppress": "PRESENT_AND_CLEAR",
    }[action]
    return CrossRefResult(state=state, claude_md_quote=quote, rationale="r", suggested_action=action)


class _StubLLM:
    name = "stub"

    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[str] = []

    def call(self, prompt: str, *, timeout: float = 120.0) -> str:
        self.calls.append(prompt)
        return self.response


# ---- trigger gating -----------------------------------------------------


def test_synthesis_actions_constant() -> None:
    assert {"rephrase-existing", "investigate-why-not-followed"} == SYNTHESIS_ACTIONS


def test_propose_new_yields_no_rewrite() -> None:
    llm = _StubLLM('{"proposed_text": "x", "rationale": "y"}')
    assert synthesize_rewrite(_finding(), _xref(action="propose-new"), llm=llm) is None
    assert llm.calls == []  # no LLM call wasted on a non-trigger


def test_suppress_yields_no_rewrite() -> None:
    llm = _StubLLM('{"proposed_text": "x", "rationale": "y"}')
    assert synthesize_rewrite(_finding(), _xref(action="suppress"), llm=llm) is None


def test_no_existing_quote_yields_no_rewrite() -> None:
    llm = _StubLLM('{"proposed_text": "x", "rationale": "y"}')
    result = synthesize_rewrite(_finding(), _xref(action="rephrase-existing", quote="  "), llm=llm)
    assert result is None
    assert llm.calls == []  # nothing concrete to rewrite → skip before calling


# ---- generation ---------------------------------------------------------


def test_rephrase_existing_produces_proposal() -> None:
    llm = _StubLLM(
        '{"proposed_text": "Answer in 1-3 sentences. No preamble, no recap.", "rationale": "concrete and unambiguous"}'
    )
    result = synthesize_rewrite(_finding(), _xref(action="rephrase-existing"), llm=llm)
    assert isinstance(result, RewriteProposal)
    assert result.action == "rephrase-existing"
    assert result.existing_quote == "Keep answers concise."
    assert "1-3 sentences" in result.proposed_text
    assert result.rationale == "concrete and unambiguous"


def test_reinforced_produces_proposal_with_strengthen_framing() -> None:
    llm = _StubLLM('{"proposed_text": "ALWAYS keep answers concise.", "rationale": "more emphatic"}')
    result = synthesize_rewrite(_finding(), _xref(action="investigate-why-not-followed"), llm=llm)
    assert result is not None
    assert result.action == "investigate-why-not-followed"
    # The prompt for REINFORCED frames the goal as strengthening.
    assert "Strengthen" in llm.calls[0]


def test_prompt_includes_existing_quote_and_evidence() -> None:
    llm = _StubLLM('{"proposed_text": "p", "rationale": "r"}')
    synthesize_rewrite(_finding(), _xref(action="rephrase-existing", quote="Be terse."), llm=llm)
    prompt = llm.calls[0]
    assert "Be terse." in prompt
    assert "stop padding your answers" in prompt


# ---- robustness ---------------------------------------------------------


def test_unparseable_response_degrades_to_none() -> None:
    llm = _StubLLM("sorry, I cannot help with that")
    assert synthesize_rewrite(_finding(), _xref(action="rephrase-existing"), llm=llm) is None


def test_empty_proposed_text_degrades_to_none() -> None:
    llm = _StubLLM('{"proposed_text": "", "rationale": "nothing to add"}')
    assert synthesize_rewrite(_finding(), _xref(action="rephrase-existing"), llm=llm) is None
