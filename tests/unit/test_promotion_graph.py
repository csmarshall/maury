"""Tests for `maury.promotion.graph` — the ADR-0045 §2/§8 promotion graph
constraint (`is_valid_promotion_target`, `lowest_common_ancestor`, base
detection). Pure logic against a constructed manifest.

Tree under test:

    base
    ├── personal
    │   └── consulting            (personal:consulting)
    │       └── acme              (personal:consulting:acme)
    └── work
        ├── acme_client           (work:acme-client)
        └── globex_client         (work:globex-client)
"""

from __future__ import annotations

import pytest

from maury.manifest import Manifest, ProfileSpec
from maury.promotion.graph import (
    PromotionGraphError,
    UnknownModeError,
    base_mode_id,
    is_valid_promotion_target,
    lowest_common_ancestor,
)

# Mode ids (surrogate keys) → ProfileSpec(name, extends).
_BASE = "p_base"
_PERSONAL = "p_personal"
_CONSULTING = "p_consulting"
_ACME = "p_acme"
_WORK = "p_work"
_ACME_CLIENT = "p_acme_client"
_GLOBEX_CLIENT = "p_globex_client"


def _manifest() -> Manifest:
    return Manifest(
        version=1,
        profiles={
            _BASE: ProfileSpec(name="base", extends=None),
            _PERSONAL: ProfileSpec(name="personal", extends=_BASE),
            _CONSULTING: ProfileSpec(name="personal:consulting", extends=_PERSONAL),
            _ACME: ProfileSpec(name="personal:consulting:acme", extends=_CONSULTING),
            _WORK: ProfileSpec(name="work", extends=_BASE),
            _ACME_CLIENT: ProfileSpec(name="work:acme-client", extends=_WORK),
            _GLOBEX_CLIENT: ProfileSpec(name="work:globex-client", extends=_WORK),
        },
        hosts={},
    )


# ---- base_mode_id -------------------------------------------------------


def test_base_mode_id_is_the_unique_root() -> None:
    assert base_mode_id(_manifest()) == _BASE


def test_base_mode_id_raises_when_no_root() -> None:
    m = Manifest(version=1, profiles={"a": ProfileSpec(name="a", extends="b")}, hosts={})
    with pytest.raises(PromotionGraphError, match="found 0"):
        base_mode_id(m)


def test_base_mode_id_raises_when_multiple_roots() -> None:
    m = Manifest(
        version=1,
        profiles={
            "a": ProfileSpec(name="a", extends=None),
            "b": ProfileSpec(name="b", extends=None),
        },
        hosts={},
    )
    with pytest.raises(PromotionGraphError, match="found 2"):
        base_mode_id(m)


# ---- is_valid_promotion_target: legal -----------------------------------


def test_promote_to_self_is_valid() -> None:
    assert is_valid_promotion_target(source_mode_id=_ACME, target_mode_id=_ACME, manifest=_manifest()) is True


def test_promote_to_direct_parent_is_valid() -> None:
    assert is_valid_promotion_target(source_mode_id=_ACME, target_mode_id=_CONSULTING, manifest=_manifest()) is True


def test_promote_to_grandparent_is_valid() -> None:
    assert is_valid_promotion_target(source_mode_id=_ACME, target_mode_id=_PERSONAL, manifest=_manifest()) is True


def test_promote_to_base_is_valid() -> None:
    assert is_valid_promotion_target(source_mode_id=_ACME, target_mode_id=_BASE, manifest=_manifest()) is True


def test_promote_leaf_to_base_from_other_subtree_is_valid() -> None:
    assert is_valid_promotion_target(source_mode_id=_ACME_CLIENT, target_mode_id=_BASE, manifest=_manifest()) is True


# ---- is_valid_promotion_target: illegal ---------------------------------


def test_promote_to_sibling_is_refused() -> None:
    # acme-client and globex-client share parent `work` but neither is an
    # ancestor of the other.
    assert (
        is_valid_promotion_target(source_mode_id=_ACME_CLIENT, target_mode_id=_GLOBEX_CLIENT, manifest=_manifest())
        is False
    )


def test_promote_to_top_level_sibling_is_refused() -> None:
    # personal and work are siblings under base.
    assert is_valid_promotion_target(source_mode_id=_PERSONAL, target_mode_id=_WORK, manifest=_manifest()) is False


def test_promote_to_unrelated_other_subtree_is_refused() -> None:
    # acme (personal subtree) → work (other subtree) is not an ancestor.
    assert is_valid_promotion_target(source_mode_id=_ACME, target_mode_id=_WORK, manifest=_manifest()) is False


def test_promote_to_descendant_is_refused() -> None:
    # personal → acme is downward, not a promotion.
    assert is_valid_promotion_target(source_mode_id=_PERSONAL, target_mode_id=_ACME, manifest=_manifest()) is False


# ---- is_valid_promotion_target: unknown ids -----------------------------


def test_unknown_source_raises() -> None:
    with pytest.raises(UnknownModeError, match="nope"):
        is_valid_promotion_target(source_mode_id="nope", target_mode_id=_BASE, manifest=_manifest())


def test_unknown_target_raises() -> None:
    with pytest.raises(UnknownModeError, match="nope"):
        is_valid_promotion_target(source_mode_id=_ACME, target_mode_id="nope", manifest=_manifest())


# ---- lowest_common_ancestor ---------------------------------------------


def test_lca_of_siblings_is_their_parent() -> None:
    assert lowest_common_ancestor(mode_a=_ACME_CLIENT, mode_b=_GLOBEX_CLIENT, manifest=_manifest()) == _WORK


def test_lca_across_subtrees_is_base() -> None:
    assert lowest_common_ancestor(mode_a=_ACME, mode_b=_ACME_CLIENT, manifest=_manifest()) == _BASE


def test_lca_when_one_is_ancestor_of_other_is_the_ancestor() -> None:
    assert lowest_common_ancestor(mode_a=_ACME, mode_b=_PERSONAL, manifest=_manifest()) == _PERSONAL


def test_lca_of_self_is_self() -> None:
    assert lowest_common_ancestor(mode_a=_ACME, mode_b=_ACME, manifest=_manifest()) == _ACME


def test_lca_unknown_mode_raises() -> None:
    with pytest.raises(UnknownModeError):
        lowest_common_ancestor(mode_a=_ACME, mode_b="nope", manifest=_manifest())
