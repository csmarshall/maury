"""Load and dump rule files.

Rule files are YAML with this shape:

```yaml
version: 1

rules:
  - id: hostname-workstation
    when:
      pattern: '\\bworkstation\\b'
    then:
      profile: home
      host_overlay: workstation
    confidence: high
    priority: 10
    added: 2026-05-06
    reason: "Example: a home-network host"
```

Loading is strict: unknown keys raise. Round-tripping preserves comments
via ruamel.yaml so user hand-edits aren't clobbered when the tool writes
back (e.g., when rule synthesis appends a new rule).
"""

from __future__ import annotations

from io import StringIO
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from .schema import Confidence, Rule, RuleActions, RuleConditions

_RULE_KEYS = {"id", "when", "then", "confidence", "priority", "added", "reason"}
_WHEN_KEYS = {"pattern", "any_keyword", "all_keywords"}
_THEN_KEYS = {"profile", "host_overlay", "forbid_profile"}


class RuleParseError(ValueError):
    """Raised when a rules file fails to parse or validate at load time."""


def _yaml() -> YAML:
    """Configured ruamel YAML instance — preserves quotes and uses a generous line width."""
    y = YAML()
    y.preserve_quotes = True
    y.width = 120
    return y


def load_rules(path: str | Path) -> list[Rule]:
    """Load rules from a YAML file. Raises RuleParseError on malformed input."""
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    return parse_rules(text, source=str(p))


def parse_rules(text: str, source: str = "<string>") -> list[Rule]:
    """Parse rules from a YAML string."""
    try:
        data = _yaml().load(text)
    except Exception as e:  # ruamel raises a few different exception types
        raise RuleParseError(f"{source}: YAML parse error: {e}") from e

    if data is None:
        return []
    if not isinstance(data, dict):
        raise RuleParseError(f"{source}: top-level must be a mapping")

    raw_rules = data.get("rules", [])
    if not isinstance(raw_rules, list):
        raise RuleParseError(f"{source}: 'rules' must be a list")

    rules: list[Rule] = []
    for i, raw in enumerate(raw_rules):
        if not isinstance(raw, dict):
            raise RuleParseError(f"{source}: rule #{i} is not a mapping")
        rules.append(_parse_one(raw, source=source, index=i))

    return rules


def _parse_one(raw: dict[str, Any], *, source: str, index: int) -> Rule:
    """Parse one rule dict into a Rule, validating required fields and types."""
    extra = set(raw) - _RULE_KEYS
    if extra:
        raise RuleParseError(f"{source}: rule #{index}: unknown top-level keys {sorted(extra)}")
    if "id" not in raw:
        raise RuleParseError(f"{source}: rule #{index}: missing 'id'")
    if "when" not in raw:
        raise RuleParseError(f"{source}: rule {raw['id']!r}: missing 'when'")
    if "then" not in raw:
        raise RuleParseError(f"{source}: rule {raw['id']!r}: missing 'then'")

    when = _parse_when(raw["when"], rule_id=raw["id"], source=source)
    then = _parse_then(raw["then"], rule_id=raw["id"], source=source)

    confidence_raw = raw.get("confidence", "medium")
    try:
        confidence = Confidence(confidence_raw)
    except ValueError as e:
        raise RuleParseError(
            f"{source}: rule {raw['id']!r}: invalid confidence "
            f"{confidence_raw!r}; expected one of {[c.value for c in Confidence]}"
        ) from e

    priority = raw.get("priority", 0)
    if not isinstance(priority, int):
        raise RuleParseError(f"{source}: rule {raw['id']!r}: priority must be an integer")

    return Rule(
        id=raw["id"],
        when=when,
        then=then,
        confidence=confidence,
        priority=priority,
        added=raw.get("added"),
        reason=raw.get("reason"),
    )


def _parse_when(raw: dict[str, Any], *, rule_id: str, source: str) -> RuleConditions:
    """Parse the `when:` block into a RuleConditions; raise on unknown keys."""
    if not isinstance(raw, dict):
        raise RuleParseError(f"{source}: rule {rule_id!r}: 'when' must be a mapping")
    extra = set(raw) - _WHEN_KEYS
    if extra:
        raise RuleParseError(f"{source}: rule {rule_id!r}: unknown 'when' keys {sorted(extra)}")
    return RuleConditions(
        pattern=raw.get("pattern"),
        any_keyword=tuple(raw.get("any_keyword", []) or []),
        all_keywords=tuple(raw.get("all_keywords", []) or []),
    )


def _parse_then(raw: dict[str, Any], *, rule_id: str, source: str) -> RuleActions:
    """Parse the `then:` block into a RuleActions; raise on unknown keys."""
    if not isinstance(raw, dict):
        raise RuleParseError(f"{source}: rule {rule_id!r}: 'then' must be a mapping")
    extra = set(raw) - _THEN_KEYS
    if extra:
        raise RuleParseError(f"{source}: rule {rule_id!r}: unknown 'then' keys {sorted(extra)}")
    return RuleActions(
        profile=raw.get("profile"),
        host_overlay=raw.get("host_overlay"),
        forbid_profile=tuple(raw.get("forbid_profile", []) or []),
    )


def dump_rules(rules: list[Rule]) -> str:
    """Serialize rules back to YAML.

    Preserves the canonical key order. Used when the tool appends a
    synthesized rule.
    """
    out = {
        "version": 1,
        "rules": [_rule_to_dict(r) for r in rules],
    }
    buf = StringIO()
    _yaml().dump(out, buf)
    return buf.getvalue()


def _rule_to_dict(rule: Rule) -> dict[str, Any]:
    """Serialize a Rule to dict; omit empty optional fields and default values."""
    when: dict[str, Any] = {}
    if rule.when.pattern is not None:
        when["pattern"] = rule.when.pattern
    if rule.when.any_keyword:
        when["any_keyword"] = list(rule.when.any_keyword)
    if rule.when.all_keywords:
        when["all_keywords"] = list(rule.when.all_keywords)

    then: dict[str, Any] = {}
    if rule.then.profile is not None:
        then["profile"] = rule.then.profile
    if rule.then.host_overlay is not None:
        then["host_overlay"] = rule.then.host_overlay
    if rule.then.forbid_profile:
        then["forbid_profile"] = list(rule.then.forbid_profile)

    out: dict[str, Any] = {"id": rule.id, "when": when, "then": then}
    if rule.confidence != Confidence.MEDIUM:
        out["confidence"] = rule.confidence.value
    if rule.priority:
        out["priority"] = rule.priority
    if rule.added:
        out["added"] = rule.added
    if rule.reason:
        out["reason"] = rule.reason
    return out
