"""Structured three-way merge for the manifest.

Per ADR-0024, manifest concurrency between two hosts produces git
conflicts that look textually identical to two different intents
(both adding different keys vs. both editing the same key) — but only
one resolution is safe in each case. This module owns the pure logic:
walk a three-way diff, classify each path, auto-resolve the additive
case, surface every other ambiguity as a Conflict for the interactive
resolver to display.

The engine is JSON-shape-aware (recurses into dicts, treats other
values as scalars) and stateless: ancestor/A/B in, (auto-resolved
dict, conflict list) out. Git plumbing and the interactive prompter
live in the CLI layer.

Resolution rules from ADR-0024:

| Diff pattern | Resolution |
|---|---|
| Both sides added different keys at same path | Auto: keep all of them |
| Both deleted the same key | Auto: delete |
| One modified, other untouched | Auto: take the modification |
| Both modified the same key | Conflict: BOTH_MODIFIED |
| One deletes, other modifies the same key | Conflict: DELETE_VS_MODIFY |
| Both added the same key with different content | Conflict: BOTH_ADDED_SAME_KEY_DIFFERENT_VALUE |
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ConflictKind(Enum):
    """Per-path classification of an unresolvable three-way diff."""

    BOTH_MODIFIED = "both_modified"
    """Both sides changed the ancestor's value to different new values."""

    DELETE_VS_MODIFY = "delete_vs_modify"
    """One side deleted a key the other side modified."""

    BOTH_ADDED_SAME_KEY_DIFFERENT_VALUE = "both_added_same_key_different_value"
    """Both sides introduced the same key (absent in ancestor) with different content."""


# Tuple of JSON-path segments, e.g. ("hosts", "host_abc...").
PathT = tuple[str, ...]


@dataclass(frozen=True)
class Conflict:
    """One unresolved diff path that the user must arbitrate."""

    path: PathT
    kind: ConflictKind
    ancestor: Any
    """The ancestor's value at this path. May be a sentinel `_MISSING`
    when the path didn't exist in the ancestor (BOTH_ADDED_SAME_KEY case)."""

    side_a: Any
    """Side A's value, or `_MISSING` if A deleted the key."""

    side_b: Any
    """Side B's value, or `_MISSING` if B deleted the key."""

    semantic_summary: str | None = None
    """Optional consequence-aware annotation (e.g., 'N hosts bound to
    this profile would lose their binding'). Populated by the resolver,
    not by the merge engine; the engine itself stays manifest-shape-
    agnostic. Free-form text intended to be surfaced verbatim in the
    interactive prompt."""


@dataclass(frozen=True)
class MergeResult:
    """Outcome of compute_merge."""

    resolved: dict[str, Any]
    """The auto-resolved partial manifest. Conflicted paths are present
    with their *ancestor* value (or omitted entirely if the ancestor
    didn't have the key, for the BOTH_ADDED_SAME_KEY case). Callers must
    apply each Conflict's chosen value to produce the final manifest."""

    conflicts: tuple[Conflict, ...] = field(default_factory=tuple)


# Sentinel for "key absent at this path." Use `is` to compare.
_MISSING: Any = object()


def compute_merge(
    ancestor: dict[str, Any],
    side_a: dict[str, Any],
    side_b: dict[str, Any],
) -> MergeResult:
    """Compute a structured three-way merge of three manifests.

    Returns a MergeResult whose `resolved` dict contains the auto-merged
    portion and whose `conflicts` tuple lists every path that needs
    user arbitration. Resolved values at conflicted paths default to
    the ancestor's value (or are absent for both-added-same-key cases)
    so the caller can apply the user's chosen resolution.
    """
    if not isinstance(ancestor, dict) or not isinstance(side_a, dict) or not isinstance(side_b, dict):
        raise TypeError("compute_merge expects dict inputs at the top level")
    resolved: dict[str, Any] = {}
    conflicts: list[Conflict] = []
    _merge_dicts(
        path=(),
        ancestor=ancestor,
        side_a=side_a,
        side_b=side_b,
        out=resolved,
        conflicts=conflicts,
    )
    return MergeResult(resolved=resolved, conflicts=tuple(conflicts))


def apply_resolutions(
    merge_result: MergeResult,
    choices: dict[PathT, Any],
) -> dict[str, Any]:
    """Build the final manifest by applying the user's choices.

    `choices` maps each conflict's path to the chosen value. To delete
    a key in the final manifest (e.g., the user picked the deletion
    side of a DELETE_VS_MODIFY conflict), set the value to `_MISSING`
    via the `DELETE_SENTINEL` exported below.

    Raises:
        ValueError if any conflict path is missing from `choices`.
    """
    missing_paths = [c.path for c in merge_result.conflicts if c.path not in choices]
    if missing_paths:
        raise ValueError(f"resolutions missing for paths: {missing_paths}")

    final: dict[str, Any] = _deep_copy(merge_result.resolved)
    for conflict in merge_result.conflicts:
        chosen = choices[conflict.path]
        _set_path(final, conflict.path, chosen)
    return final


# Public re-export — callers use this sentinel rather than constructing _MISSING.
DELETE_SENTINEL: Any = _MISSING


# ---- helpers --------------------------------------------------------------


def _merge_dicts(
    *,
    path: PathT,
    ancestor: dict[str, Any],
    side_a: dict[str, Any],
    side_b: dict[str, Any],
    out: dict[str, Any],
    conflicts: list[Conflict],
) -> None:
    """Recursive walker. Mutates `out` and `conflicts` in place."""
    keys = sorted(_all_keys(ancestor, side_a, side_b))
    for key in keys:
        sub_path = (*path, key)
        anc = ancestor.get(key, _MISSING)
        a = side_a.get(key, _MISSING)
        b = side_b.get(key, _MISSING)
        anc_present = key in ancestor
        a_present = key in side_a
        b_present = key in side_b

        # Case: both sides deleted (ancestor had it, neither side does).
        if anc_present and not a_present and not b_present:
            continue  # omit from output

        # Case: ancestor absent + both sides added.
        if not anc_present and a_present and b_present:
            # Same content → fine.
            if _values_equal(a, b):
                out[key] = _deep_copy(a)
                continue
            # Both added a dict at the same path → merge inside (an additive
            # interleave at the next level). This is the canonical "two hosts
            # adding different keys under hosts {...}" case.
            if isinstance(a, dict) and isinstance(b, dict):
                sub_out: dict[str, Any] = {}
                _merge_dicts(
                    path=sub_path,
                    ancestor={},
                    side_a=a,
                    side_b=b,
                    out=sub_out,
                    conflicts=conflicts,
                )
                out[key] = sub_out
                continue
            # Both added the same key with different scalar content → conflict.
            conflicts.append(
                Conflict(
                    path=sub_path,
                    kind=ConflictKind.BOTH_ADDED_SAME_KEY_DIFFERENT_VALUE,
                    ancestor=_MISSING,
                    side_a=a,
                    side_b=b,
                )
            )
            # Don't emit anything for this path; apply_resolutions fills it.
            continue

        # Case: ancestor absent, only one side added.
        if not anc_present and a_present and not b_present:
            out[key] = _deep_copy(a)
            continue
        if not anc_present and b_present and not a_present:
            out[key] = _deep_copy(b)
            continue

        # From here on, the ancestor had the key.

        # Case: both sides deleted (already covered above).
        # Case: one side deleted, the other side present.
        if anc_present and not a_present and b_present:
            if _values_equal(anc, b):
                # B didn't actually change it; honour A's deletion.
                continue
            conflicts.append(
                Conflict(
                    path=sub_path,
                    kind=ConflictKind.DELETE_VS_MODIFY,
                    ancestor=anc,
                    side_a=_MISSING,
                    side_b=b,
                )
            )
            continue
        if anc_present and not b_present and a_present:
            if _values_equal(anc, a):
                continue
            conflicts.append(
                Conflict(
                    path=sub_path,
                    kind=ConflictKind.DELETE_VS_MODIFY,
                    ancestor=anc,
                    side_a=a,
                    side_b=_MISSING,
                )
            )
            continue

        # Case: ancestor present, both sides present.
        a_changed = not _values_equal(anc, a)
        b_changed = not _values_equal(anc, b)

        if not a_changed and not b_changed:
            out[key] = _deep_copy(anc)
            continue
        if a_changed and not b_changed:
            out[key] = _deep_copy(a)
            continue
        if b_changed and not a_changed:
            out[key] = _deep_copy(b)
            continue

        # Both changed.
        if _values_equal(a, b):
            # Same change on both sides — no conflict.
            out[key] = _deep_copy(a)
            continue
        if isinstance(anc, dict) and isinstance(a, dict) and isinstance(b, dict):
            # Recurse: maybe the dicts changed in disjoint ways.
            sub_out_existing: dict[str, Any] = {}
            _merge_dicts(
                path=sub_path,
                ancestor=anc,
                side_a=a,
                side_b=b,
                out=sub_out_existing,
                conflicts=conflicts,
            )
            out[key] = sub_out_existing
            continue
        # Two scalar (or scalar/list) modifications of the same value.
        conflicts.append(
            Conflict(
                path=sub_path,
                kind=ConflictKind.BOTH_MODIFIED,
                ancestor=anc,
                side_a=a,
                side_b=b,
            )
        )


def _all_keys(*dicts: dict[str, Any]) -> Iterable[str]:
    """Union of all keys across the given dicts, preserving determinism."""
    seen: set[str] = set()
    for d in dicts:
        for k in d:
            if k not in seen:
                seen.add(k)
                yield k


def _values_equal(a: Any, b: Any) -> bool:
    """JSON-value equality. Lists compare element-wise; dicts recursively."""
    if a is _MISSING or b is _MISSING:
        return a is b  # _MISSING is _MISSING is True
    return bool(a == b)


def _deep_copy(value: Any) -> Any:
    """Deep-copy a JSON-shaped value (dicts, lists, scalars)."""
    if isinstance(value, dict):
        return {k: _deep_copy(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_deep_copy(v) for v in value]
    return value


def _set_path(target: dict[str, Any], path: PathT, value: Any) -> None:
    """Set `target[path[0]][path[1]]…` to `value` (or delete on sentinel).

    Creates intermediate dicts as needed. If `value is DELETE_SENTINEL`,
    deletes the final key (and prunes empty intermediate dicts).
    """
    if not path:
        raise ValueError("path must be non-empty")
    cursor: dict[str, Any] = target
    for segment in path[:-1]:
        existing = cursor.get(segment)
        if not isinstance(existing, dict):
            existing = {}
            cursor[segment] = existing
        cursor = existing
    final_key = path[-1]
    if value is DELETE_SENTINEL:
        cursor.pop(final_key, None)
    else:
        cursor[final_key] = _deep_copy(value)


__all__ = [
    "DELETE_SENTINEL",
    "Conflict",
    "ConflictKind",
    "MergeResult",
    "PathT",
    "apply_resolutions",
    "compute_merge",
]
