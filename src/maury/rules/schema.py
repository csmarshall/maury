"""Rule schema for the maury classification engine.

A rule has conditions (`when`) and actions (`then`). Three shapes:

- **classify** — assign a profile when conditions match.
- **forbid**   — exclude one or more profiles when conditions match.
- **scope**    — narrow further to a host overlay.

The shape is inferred from which fields of `then` are populated.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Confidence(StrEnum):
    """How sure a rule is about its classification — affects auto-apply thresholds."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RuleKind(StrEnum):
    """The shape of a rule, derived from which `then` fields are populated."""

    CLASSIFY = "classify"
    FORBID = "forbid"
    SCOPE = "scope"


@dataclass(frozen=True)
class RuleConditions:
    """Conditions under which a rule fires.

    Multiple conditions on a single rule are combined with implicit AND
    (all must match). At least one condition must be specified.
    """

    pattern: str | None = None
    any_keyword: tuple[str, ...] = ()
    all_keywords: tuple[str, ...] = ()

    def is_empty(self) -> bool:
        return not (self.pattern or self.any_keyword or self.all_keywords)


@dataclass(frozen=True)
class RuleActions:
    """What happens when a rule's conditions match.

    The shape (classify / forbid / scope) is derived from which fields are set.
    """

    profile: str | None = None
    host_overlay: str | None = None
    forbid_profile: tuple[str, ...] = ()


@dataclass(frozen=True)
class Rule:
    """One classification rule: id + conditions + action + metadata (priority, provenance)."""

    id: str
    when: RuleConditions
    then: RuleActions
    confidence: Confidence = Confidence.MEDIUM
    priority: int = 0
    added: str | None = None
    reason: str | None = None

    @property
    def kind(self) -> RuleKind:
        if self.then.forbid_profile:
            return RuleKind.FORBID
        if self.then.host_overlay and not self.then.profile:
            return RuleKind.SCOPE
        return RuleKind.CLASSIFY


@dataclass(frozen=True)
class TraceEntry:
    """One step in the classification trace."""

    rule_id: str
    rule_kind: RuleKind
    matched: bool
    detail: str


@dataclass(frozen=True)
class Classification:
    """The result of classifying a fragment.

    `profile=None` means no rule produced an allowed classification — the
    fragment goes to the manual review queue.
    """

    profile: str | None
    host_overlay: str | None
    confidence: Confidence
    trace: tuple[TraceEntry, ...]
    forbidden_by: tuple[str, ...]
