"""Classify-rule synthesis — propose a `rules.yaml` routing rule from an
operator reclassification (ADR-0053 / ADR-0004 Phase 8 path A).

When the operator overrides the rule engine's target for a finding during
`maury review` (a *reclassification*), that correction is the signal to
grow the routing table: synthesize a `classify` rule whose `when`
condition would route fragments like this one to the chosen mode. The LLM
proposes the `when` (the fuzzy part); everything else
(`then.profile`/`host_overlay`, `added`, `reason`, serialization) is
deterministic, and the result is validated by round-tripping through the
rule loader before it can be appended to `.meta/rules.yaml`. The LLM
never gains authority over routing — it proposes; the operator approves
(ADR-0004 Tenet 5).

This is the `rules.yaml` (routing) counterpart to `mining/synthesize.py`,
which rewrites `CLAUDE.md` *content* (Phase 8 path B). Different artifact,
different module.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from string import Template
from textwrap import dedent
from typing import TYPE_CHECKING

from maury.rules.loader import RuleParseError, dump_rules, parse_rules
from maury.rules.schema import Confidence, Rule, RuleActions, RuleConditions

if TYPE_CHECKING:
    from maury.llm import LLMClient

# Synthesized rules are reviewed, not auto-applied, so a middling default.
_SYNTH_PRIORITY = 5
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


@dataclass(frozen=True)
class RuleProposal:
    """A synthesized, validated `classify` rule ready to append + approve."""

    rule: Rule
    yaml_block: str
    """The rule serialized as a `version: 1` rules doc (for preview/append)."""

    raw_response: str = ""


_PROMPT = Template(
    dedent(
        """\
        You are writing ONE maury classification rule. maury routes mined
        preference fragments to a "mode" (a content namespace) using a
        deterministic rule engine over rules.yaml. The operator has decided
        the fragment below belongs in mode "$target". Propose a `when`
        condition that would route fragments LIKE this one to that mode.

        Fragment kind: $kind
        Fragment text:
        $text

        Prefer `any_keyword` — a few distinctive lowercase keywords or short
        phrases that show up when this topic comes up — over a regex
        `pattern`, unless a pattern is clearly better. Keep it specific
        enough not to over-route unrelated content.

        Output ONE JSON object, NOTHING ELSE:

          {
            "id": "<short-kebab-case-id>",
            "when": {"any_keyword": ["keyword one", "keyword two"]},
            "reason": "<one sentence on what this rule captures>"
          }

        (Use {"pattern": "<regex>"} or {"all_keywords": [...]} instead of
        any_keyword if more appropriate.)
        """
    )
)


def synthesize_classify_rule(
    *,
    finding_text: str,
    finding_kind: str,
    target_profile: str,
    source_run: str,
    llm: LLMClient,
    host_overlay: str | None = None,
    added: str | None = None,
    timeout: float = 60.0,
) -> RuleProposal | None:
    """Synthesize a validated `classify` rule routing fragments like this
    one to `target_profile`, or None if the LLM yields nothing usable.

    The proposed rule is validated by serializing it and re-parsing
    through the rule loader (the same path `maury rules validate` uses);
    a rule that won't round-trip is discarded, never returned.
    """
    prompt = _PROMPT.substitute(target=target_profile, kind=finding_kind, text=finding_text.strip())
    raw = llm.call(prompt, timeout=timeout)
    parsed = _parse_proposal(raw)
    if parsed is None:
        return None
    proposed_id, pattern, any_keyword, all_keywords, reason = parsed

    conditions = RuleConditions(pattern=pattern, any_keyword=any_keyword, all_keywords=all_keywords)
    if conditions.is_empty():
        return None

    rule = Rule(
        id=proposed_id or _fallback_id(target_profile, finding_text),
        when=conditions,
        then=RuleActions(profile=target_profile, host_overlay=host_overlay),
        confidence=Confidence.MEDIUM,
        priority=_SYNTH_PRIORITY,
        added=added or datetime.now(UTC).strftime("%Y-%m-%d"),
        reason=reason or f"synthesized from a reclassification during {source_run}",
    )

    yaml_block = dump_rules([rule])
    try:
        parse_rules(yaml_block, source="<synthesized>")
    except RuleParseError:
        return None
    return RuleProposal(rule=rule, yaml_block=yaml_block, raw_response=raw)


def _parse_proposal(
    raw_text: str,
) -> tuple[str, str | None, tuple[str, ...], tuple[str, ...], str] | None:
    """Pull (id, pattern, any_keyword, all_keywords, reason) from the LLM
    JSON, or None on any parse failure."""
    start = raw_text.find("{")
    end = raw_text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        obj = json.loads(raw_text[start : end + 1])
    except json.JSONDecodeError:
        return None
    when_raw = obj.get("when")
    if not isinstance(when_raw, dict):
        return None

    pattern = when_raw["pattern"] if isinstance(when_raw.get("pattern"), str) else None

    def _keywords(key: str) -> tuple[str, ...]:
        val = when_raw.get(key)
        if isinstance(val, list):
            return tuple(str(k).strip() for k in val if str(k).strip())
        return ()

    raw_id = str(obj.get("id", "")).strip().lower()
    proposed_id = raw_id if _ID_RE.match(raw_id) else ""
    return proposed_id, pattern, _keywords("any_keyword"), _keywords("all_keywords"), str(obj.get("reason", "")).strip()


def _fallback_id(target_profile: str, finding_text: str) -> str:
    """A deterministic rule id when the LLM proposes none/an invalid one."""
    import hashlib

    slug = re.sub(r"[^a-z0-9]+", "-", target_profile.lower()).strip("-") or "mode"
    digest = hashlib.sha256(finding_text.encode()).hexdigest()[:8]
    return f"synth-{slug}-{digest}"


__all__ = [
    "RuleProposal",
    "synthesize_classify_rule",
]
