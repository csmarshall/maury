"""Rule engine: match conditions, classify fragments, produce traces.

Semantics (v1):

1. For each rule, check whether its conditions match the fragment text.
2. Among forbid rules that matched, compute the union of forbidden profiles
   (resolving symbolic targets `*`, `!<name>`, literal names against the
   set of registered profiles).
3. Among classify rules that matched, sort by priority descending (then by
   rule id for determinism). Pick the highest-priority one whose target
   profile is not forbidden. That becomes the chosen profile.
4. If the chosen rule has a `host_overlay`, apply it. Otherwise, if a
   matched scope rule is present, apply its `host_overlay`.
5. If no allowed classify rule fired, the result is `profile=None` —
   the fragment goes to the manual review queue.

This file deliberately contains no I/O; it operates on already-parsed
`Rule` objects.
"""

from __future__ import annotations

import re

from .schema import (
    Classification,
    Confidence,
    Rule,
    RuleConditions,
    RuleKind,
    TraceEntry,
)


def classify_fragment(
    fragment: str,
    rules: list[Rule],
    known_profiles: set[str],
) -> Classification:
    """Classify a fragment against a ruleset.

    Args:
        fragment: The text to classify.
        rules: The full ruleset (typically loaded from `.meta/rules.yaml`).
        known_profiles: The set of recognized profile identifiers. Per
            ADR-0015 this is the set of profile **names** OR **IDs** (the
            engine treats them as opaque strings — callers who want
            cross-validation against the manifest pass the union of both).
            Used to resolve symbolic forbid targets `*` and `!<name>`.
            May be empty during early bootstrap, in which case symbolic
            targets resolve to empty sets and literal targets still work.

    Returns:
        Classification with profile (or None for manual), host_overlay,
        confidence, full trace, and the IDs of any rules that forbade
        an otherwise-matched profile.
    """
    trace: list[TraceEntry] = []
    matched_classify: list[Rule] = []
    matched_forbid: list[Rule] = []
    matched_scope: list[Rule] = []

    for rule in rules:
        ok, detail = _conditions_match(fragment, rule.when)
        trace.append(TraceEntry(rule_id=rule.id, rule_kind=rule.kind, matched=ok, detail=detail))
        if not ok:
            continue
        if rule.kind == RuleKind.CLASSIFY:
            matched_classify.append(rule)
        elif rule.kind == RuleKind.FORBID:
            matched_forbid.append(rule)
        elif rule.kind == RuleKind.SCOPE:
            matched_scope.append(rule)

    forbidden: set[str] = set()
    forbidding_rule_ids: list[str] = []
    for rule in matched_forbid:
        targets = _resolve_targets(rule.then.forbid_profile, known_profiles)
        if targets:
            forbidden |= targets
            forbidding_rule_ids.append(rule.id)

    matched_classify.sort(key=lambda r: (-r.priority, r.id))

    chosen: Rule | None = None
    for rule in matched_classify:
        if rule.then.profile and rule.then.profile not in forbidden:
            chosen = rule
            break

    profile = chosen.then.profile if chosen else None
    confidence = chosen.confidence if chosen else Confidence.LOW

    host_overlay: str | None = None
    if chosen and chosen.then.host_overlay:
        host_overlay = chosen.then.host_overlay
    elif chosen and matched_scope:
        host_overlay = matched_scope[0].then.host_overlay

    return Classification(
        profile=profile,
        host_overlay=host_overlay,
        confidence=confidence,
        trace=tuple(trace),
        forbidden_by=tuple(forbidding_rule_ids),
    )


def _conditions_match(fragment: str, when: RuleConditions) -> tuple[bool, str]:
    """Return (matched, detail) for a single rule's conditions.

    All specified conditions must match (implicit AND). An empty
    RuleConditions never matches and is treated as a malformed rule.
    """
    if when.is_empty():
        return False, "no conditions specified"

    fragment_lower = fragment.lower()
    details: list[str] = []

    if when.pattern:
        try:
            if not re.search(when.pattern, fragment, flags=re.IGNORECASE):
                return False, f"pattern /{when.pattern}/ did not match"
            details.append(f"pattern /{when.pattern}/ matched")
        except re.error as e:
            return False, f"invalid regex /{when.pattern}/: {e}"

    if when.any_keyword:
        hits = [kw for kw in when.any_keyword if kw.lower() in fragment_lower]
        if not hits:
            return False, f"no any_keyword matched (looked for {list(when.any_keyword)})"
        details.append(f"any_keyword matched: {hits}")

    if when.all_keywords:
        misses = [kw for kw in when.all_keywords if kw.lower() not in fragment_lower]
        if misses:
            return False, f"all_keywords missing: {misses}"
        details.append(f"all_keywords matched: {list(when.all_keywords)}")

    return True, "; ".join(details)


def _resolve_targets(targets: tuple[str, ...], known_profiles: set[str]) -> set[str]:
    """Resolve a list of target spec strings to a concrete set of profiles.

    Supported syntaxes:
    - `*`         : all known profiles
    - `!<name>`   : all known profiles except <name>
    - `<name>`    : literal profile name
    """
    result: set[str] = set()
    for target in targets:
        if target == "*":
            result |= known_profiles
        elif target.startswith("!"):
            excluded = target[1:]
            result |= known_profiles - {excluded}
        else:
            result.add(target)
    return result


def validate_rules(rules: list[Rule], known_profiles: set[str]) -> list[str]:
    """Validate a ruleset against the profile registry.

    Returns a list of human-readable error strings (empty if valid).
    Catches:
    - duplicate rule IDs
    - rules with no conditions
    - classify rules referencing unknown profiles
    - forbid rules with literal targets referencing unknown profiles
    - scope rules with no host_overlay
    """
    errors: list[str] = []
    seen_ids: set[str] = set()

    for rule in rules:
        if rule.id in seen_ids:
            errors.append(f"duplicate rule id: {rule.id!r}")
        seen_ids.add(rule.id)

        if rule.when.is_empty():
            errors.append(f"rule {rule.id!r}: no conditions specified")

        if rule.kind == RuleKind.CLASSIFY:
            if not rule.then.profile:
                errors.append(f"rule {rule.id!r}: classify rule missing target profile")
            elif known_profiles and rule.then.profile not in known_profiles:
                errors.append(
                    f"rule {rule.id!r}: target profile {rule.then.profile!r} "
                    f"not in known profiles {sorted(known_profiles)}"
                )

        if rule.kind == RuleKind.FORBID:
            for target in rule.then.forbid_profile:
                if target == "*" or target.startswith("!"):
                    continue
                if known_profiles and target not in known_profiles:
                    errors.append(
                        f"rule {rule.id!r}: forbid target {target!r} not in known profiles {sorted(known_profiles)}"
                    )

        if rule.kind == RuleKind.SCOPE and not rule.then.host_overlay:
            errors.append(f"rule {rule.id!r}: scope rule missing host_overlay")

    return errors


__all__ = [
    "Classification",
    "Rule",
    "TraceEntry",
    "classify_fragment",
    "validate_rules",
]
