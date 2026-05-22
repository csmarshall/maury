"""Promotion graph constraint per ADR-0045 §2 + §8.

Promotion follows inheritance edges only. A finding under source mode `A`
can be promoted to:

- **A itself** (no real move — lands in A's own repo/branch), or
- **any ancestor of A** in A's inheritance chain, up to the root (`base`).

It **cannot** be promoted to a *sibling* (a mode sharing A's parent but
not on A's ancestor path) or to an *unrelated* mode. To move content
between siblings, promote to their shared ancestor and let the render
engine flow it down by inheritance (ADR-0019). This keeps the promotion
graph identical to the inheritance graph — "what content can reach this
mode?" stays answerable from inheritance alone, with no hidden edges.

Vocabulary note: ADR-0045 and concepts.md say "mode"; the manifest data
model still keys these as `profiles` with `profile_id`s. The public
parameter names here use the canonical "mode" term; the values are the
manifest's profile IDs.

The graph check fires before any trust-boundary mechanics: a
graph-illegal promotion is refused without consulting repo access
(ADR-0045 §3, §7).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from maury.manifest import Manifest


class UnknownModeError(ValueError):
    """A promotion source/target mode id is absent from the manifest.

    Per ADR-0045's contract, an unknown id is a programming error (a
    typo), not a "no, that's not allowed" answer — so it raises rather
    than returning False.
    """


class PromotionGraphError(ValueError):
    """The manifest cannot support a promotion-graph query — most
    commonly a missing or non-unique root (`base`) mode. Per ADR-0045:
    treat a missing base as a manifest-integrity error and refuse all
    promotion until fixed."""


def base_mode_id(manifest: Manifest) -> str:
    """Return the id of the root (`base`) mode — the unique mode with no
    parent (`extends is None`).

    Raises `PromotionGraphError` if there is not exactly one root; a
    well-formed agency has a single root (the manifest validator
    guarantees this), so zero or multiple roots is an integrity error.
    """
    roots = [pid for pid, spec in manifest.profiles.items() if spec.extends is None]
    if len(roots) != 1:
        raise PromotionGraphError(
            f"expected exactly one root (base) mode with extends=None; found {len(roots)}: {sorted(roots)}"
        )
    return roots[0]


def is_valid_promotion_target(
    *,
    source_mode_id: str,
    target_mode_id: str,
    manifest: Manifest,
) -> bool:
    """Return True iff `target_mode_id` is a legal promotion destination
    for a finding from `source_mode_id`, per ADR-0045 §2.

    Legal targets: the source mode itself, or any ancestor of it up to
    `base`. Siblings and unrelated modes are illegal.

    Both ids MUST exist in the manifest (else `UnknownModeError`); the
    manifest MUST have a unique base mode (else `PromotionGraphError`).
    """
    if source_mode_id not in manifest.profiles:
        raise UnknownModeError(source_mode_id)
    if target_mode_id not in manifest.profiles:
        raise UnknownModeError(target_mode_id)

    if source_mode_id == target_mode_id:
        return True

    # Promoting to the root (base) is always legal for any source.
    if target_mode_id == base_mode_id(manifest):
        return True

    # `inheritance_chain` returns ids root-first INCLUDING the source
    # itself (e.g. [base, personal, acme] for acme). Ancestors-only is
    # the chain minus its final element.
    ancestors = manifest.inheritance_chain(source_mode_id)[:-1]
    return target_mode_id in ancestors


def lowest_common_ancestor(
    *,
    mode_a: str,
    mode_b: str,
    manifest: Manifest,
) -> str | None:
    """Return the deepest mode that is an ancestor (inclusive) of both
    `mode_a` and `mode_b`, or None if they share no common node.

    Used to power the "did you mean their shared parent?" suggestion when
    a promotion is graph-refused (ADR-0045 §8 + Followups). Single-parent
    inheritance (ADR-0001) keeps both chains linear, so this is
    O(depth_a + depth_b).

    Both ids MUST exist in the manifest (else `UnknownModeError`).
    """
    if mode_a not in manifest.profiles:
        raise UnknownModeError(mode_a)
    if mode_b not in manifest.profiles:
        raise UnknownModeError(mode_b)

    # Chains are root-first; the deepest common node is the last id in
    # mode_a's chain that also appears in mode_b's chain.
    chain_a = manifest.inheritance_chain(mode_a)
    chain_b = set(manifest.inheritance_chain(mode_b))
    common = [mid for mid in chain_a if mid in chain_b]
    return common[-1] if common else None


__all__ = [
    "PromotionGraphError",
    "UnknownModeError",
    "base_mode_id",
    "is_valid_promotion_target",
    "lowest_common_ancestor",
]
