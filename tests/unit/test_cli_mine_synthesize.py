"""Tests for `_crossref_and_rewrite_maps` (maury mine --synthesize glue,
Phase 8 path B slice 3).

Covers both the synthesis wiring and the crossref-state index mapping
(which previously read a non-existent `.entries` attr and silently
produced no states — fixed in this slice).
"""

from __future__ import annotations

from pathlib import Path

from maury.cli import _crossref_and_rewrite_maps
from maury.mining.crossref import CrossRefResult, CrossRefSummary
from maury.mining.extractor import ExtractionWindow, Finding
from maury.mining.transcripts import TranscriptMessage


def _finding(text: str, *, kind: str = "feedback") -> Finding:
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
        evidence="ev",
        confidence="high",
        source_window=ExtractionWindow(project="p", index=0, messages=(msg,)),
    )


def _xref(state: str, action: str, *, quote: str = "Be concise.") -> CrossRefResult:
    return CrossRefResult(state=state, claude_md_quote=quote, rationale="r", suggested_action=action)


class _StubLLM:
    name = "stub"

    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[str] = []

    def call(self, prompt: str, *, timeout: float = 120.0) -> str:
        self.calls.append(prompt)
        return self.response


def test_no_summary_yields_empty_maps() -> None:
    states, rewrites = _crossref_and_rewrite_maps([_finding("x")], None, synthesize=False, llm=_StubLLM(""))
    assert states == {}
    assert rewrites == {}


def test_crossref_states_mapped_by_index() -> None:
    """Regression: states must populate (the old .entries path was dead)."""
    f0, f1 = _finding("first"), _finding("second", kind="preference")
    summary = CrossRefSummary.empty()
    summary.add(f0, _xref("NEW", "propose-new"))
    summary.add(f1, _xref("PRESENT_AND_CLEAR", "suppress"))
    states, rewrites = _crossref_and_rewrite_maps([f0, f1], summary, synthesize=False, llm=_StubLLM(""))
    assert states == {0: "NEW", 1: "PRESENT_AND_CLEAR"}
    assert rewrites == {}  # synthesize off


def test_synthesize_off_makes_no_llm_call() -> None:
    f0 = _finding("x")
    summary = CrossRefSummary.empty()
    summary.add(f0, _xref("PRESENT_BUT_UNCLEAR", "rephrase-existing"))
    llm = _StubLLM('{"proposed_text": "p", "rationale": "r"}')
    _crossref_and_rewrite_maps([f0], summary, synthesize=False, llm=llm)
    assert llm.calls == []


def test_synthesize_on_generates_rewrite_for_triggers_only() -> None:
    f0 = _finding("clarity one")  # rephrase-existing → rewrite
    f1 = _finding("brand new", kind="preference")  # propose-new → no rewrite
    f2 = _finding("ignored rule", kind="workflow")  # reinforced → rewrite
    summary = CrossRefSummary.empty()
    summary.add(f0, _xref("PRESENT_BUT_UNCLEAR", "rephrase-existing"))
    summary.add(f1, _xref("NEW", "propose-new"))
    summary.add(f2, _xref("PRESENT_AND_REINFORCED", "investigate-why-not-followed"))
    llm = _StubLLM('{"proposed_text": "clearer wording", "rationale": "better"}')

    states, rewrites = _crossref_and_rewrite_maps([f0, f1, f2], summary, synthesize=True, llm=llm)
    assert states == {0: "PRESENT_BUT_UNCLEAR", 1: "NEW", 2: "PRESENT_AND_REINFORCED"}
    # Only the two trigger findings get rewrites, keyed by their index.
    assert set(rewrites) == {0, 2}
    assert rewrites[0] == "clearer wording"
    assert len(llm.calls) == 2  # one call per trigger finding
