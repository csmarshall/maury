"""Tests for `maury.rules.synthesize` — classify-rule synthesis from a
reclassification (ADR-0053 / ADR-0004 Phase 8 path A). Stub LLM."""

from __future__ import annotations

from maury.llm import LLMClient
from maury.rules.loader import parse_rules
from maury.rules.schema import RuleKind
from maury.rules.synthesize import RuleProposal, synthesize_classify_rule


class _StubLLM:
    name = "stub"

    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[str] = []

    def call(self, prompt: str, *, timeout: float = 120.0) -> str:
        self.calls.append(prompt)
        return self.response


def _synth(
    llm: LLMClient,
    *,
    finding_text: str = "always run ruff and mypy before committing",
    finding_kind: str = "workflow",
    target_profile: str = "base",
    source_run: str = "maury/run/r1",
    host_overlay: str | None = None,
) -> RuleProposal | None:
    return synthesize_classify_rule(
        llm=llm,
        finding_text=finding_text,
        finding_kind=finding_kind,
        target_profile=target_profile,
        source_run=source_run,
        host_overlay=host_overlay,
        added="2026-05-22",
    )


# ---- happy path ---------------------------------------------------------


def test_any_keyword_proposal_builds_valid_classify_rule() -> None:
    llm = _StubLLM('{"id": "code-quality-gate", "when": {"any_keyword": ["ruff", "mypy"]}, "reason": "lint gate"}')
    p = _synth(llm)
    assert isinstance(p, RuleProposal)
    assert p.rule.id == "code-quality-gate"
    assert p.rule.then.profile == "base"
    assert p.rule.kind is RuleKind.CLASSIFY
    assert p.rule.when.any_keyword == ("ruff", "mypy")
    assert p.rule.added == "2026-05-22"
    # The serialized block round-trips through the loader.
    parsed = parse_rules(p.yaml_block)
    assert len(parsed) == 1
    assert parsed[0].then.profile == "base"


def test_pattern_proposal_supported() -> None:
    llm = _StubLLM('{"id": "host-acme", "when": {"pattern": "\\\\bacme\\\\b"}, "reason": "acme host"}')
    p = _synth(llm, target_profile="work")
    assert p is not None
    assert p.rule.when.pattern == r"\bacme\b"
    assert p.rule.then.profile == "work"


def test_host_overlay_threaded_into_then() -> None:
    llm = _StubLLM('{"id": "r", "when": {"any_keyword": ["k"]}, "reason": "x"}')
    p = _synth(llm, target_profile="work", host_overlay="laptop")
    assert p is not None
    assert p.rule.then.host_overlay == "laptop"


def test_prompt_includes_finding_and_target() -> None:
    llm = _StubLLM('{"id": "r", "when": {"any_keyword": ["k"]}, "reason": "x"}')
    _synth(llm, target_profile="personal")
    assert "personal" in llm.calls[0]
    assert "ruff and mypy" in llm.calls[0]


def test_fallback_id_when_llm_omits_or_invalid() -> None:
    llm = _StubLLM('{"id": "Not A Valid ID!", "when": {"any_keyword": ["k"]}, "reason": "x"}')
    p = _synth(llm)
    assert p is not None
    assert p.rule.id.startswith("synth-base-")


def test_default_reason_references_run() -> None:
    llm = _StubLLM('{"id": "r", "when": {"any_keyword": ["k"]}, "reason": ""}')
    p = _synth(llm, source_run="maury/run/abc")
    assert p is not None
    assert "maury/run/abc" in (p.rule.reason or "")


# ---- robustness ---------------------------------------------------------


def test_empty_when_yields_none() -> None:
    llm = _StubLLM('{"id": "r", "when": {}, "reason": "x"}')
    assert _synth(llm) is None


def test_when_missing_yields_none() -> None:
    llm = _StubLLM('{"id": "r", "reason": "x"}')
    assert _synth(llm) is None


def test_unparseable_response_yields_none() -> None:
    assert _synth(_StubLLM("no json here")) is None


def test_backend_failure_at_call_time_yields_none() -> None:
    """A backend configured but unusable at call time (e.g. `claude` not on
    PATH) degrades to None — synthesis is best-effort."""
    from maury.llm import BackendUnavailableError

    class _BrokenLLM:
        name = "broken"

        def call(self, prompt: str, *, timeout: float = 120.0) -> str:
            raise BackendUnavailableError("claude not found")

    assert _synth(_BrokenLLM()) is None


def test_blank_keywords_filtered_to_none() -> None:
    llm = _StubLLM('{"id": "r", "when": {"any_keyword": ["", "  "]}, "reason": "x"}')
    assert _synth(llm) is None  # all keywords blank → empty conditions → None
