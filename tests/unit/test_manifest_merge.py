"""Unit tests for `maury.manifest_merge` (the structured 3-way merge engine)."""

from __future__ import annotations

from typing import Any

import pytest

from maury.manifest_merge import (
    DELETE_SENTINEL,
    ConflictKind,
    apply_resolutions,
    compute_merge,
)

# ---- additive case (the inclusive auto-resolve from ADR-0024) ------------


def test_both_sides_add_different_keys_at_same_path_keeps_all() -> None:
    """The canonical 'two hosts bootstrapping' case."""
    ancestor: dict[str, Any] = {"hosts": {}}
    a = {"hosts": {"host_a": {"name": "alice"}}}
    b = {"hosts": {"host_b": {"name": "bob"}}}
    result = compute_merge(ancestor, a, b)
    assert result.conflicts == ()
    assert result.resolved == {"hosts": {"host_a": {"name": "alice"}, "host_b": {"name": "bob"}}}


def test_both_sides_add_top_level_keys() -> None:
    """Additive merge at the top level."""
    result = compute_merge({"version": 1}, {"version": 1, "x": 1}, {"version": 1, "y": 2})
    assert result.conflicts == ()
    assert result.resolved == {"version": 1, "x": 1, "y": 2}


def test_both_sides_add_same_key_same_content_no_conflict() -> None:
    """If both sides added the same key with identical content, no conflict."""
    result = compute_merge({}, {"profiles": {"home": {"extends": None}}}, {"profiles": {"home": {"extends": None}}})
    assert result.conflicts == ()
    assert result.resolved == {"profiles": {"home": {"extends": None}}}


def test_both_sides_add_same_key_different_scalar_value_conflicts() -> None:
    result = compute_merge({}, {"note": "alpha"}, {"note": "beta"})
    assert len(result.conflicts) == 1
    c = result.conflicts[0]
    assert c.kind is ConflictKind.BOTH_ADDED_SAME_KEY_DIFFERENT_VALUE
    assert c.path == ("note",)
    assert c.ancestor is DELETE_SENTINEL
    assert c.side_a == "alpha"
    assert c.side_b == "beta"
    # Conflict path is omitted from resolved.
    assert "note" not in result.resolved


# ---- modify cases ---------------------------------------------------------


def test_one_modified_other_untouched_takes_modification() -> None:
    ancestor = {"profiles": {"home": {"extends": None}}}
    a = {"profiles": {"home": {"extends": None, "added": True}}}
    b = ancestor  # untouched
    result = compute_merge(ancestor, a, b)
    assert result.conflicts == ()
    assert result.resolved == a


def test_both_sides_modify_same_key_to_different_values_conflicts() -> None:
    ancestor = {"profiles": {"home": {"name": "home"}}}
    a = {"profiles": {"home": {"name": "personal"}}}
    b = {"profiles": {"home": {"name": "work-personal"}}}
    result = compute_merge(ancestor, a, b)
    assert len(result.conflicts) == 1
    c = result.conflicts[0]
    assert c.kind is ConflictKind.BOTH_MODIFIED
    assert c.path == ("profiles", "home", "name")
    assert c.ancestor == "home"
    assert c.side_a == "personal"
    assert c.side_b == "work-personal"


def test_both_sides_make_same_modification_no_conflict() -> None:
    """If both sides made the identical change, no conflict."""
    ancestor = {"note": "old"}
    a = {"note": "new"}
    b = {"note": "new"}
    result = compute_merge(ancestor, a, b)
    assert result.conflicts == ()
    assert result.resolved == {"note": "new"}


def test_disjoint_modifications_to_same_dict_auto_merge() -> None:
    """Both sides modify different keys inside the same dict → auto-merge."""
    ancestor = {"profiles": {"home": {"a": 1, "b": 2}}}
    a = {"profiles": {"home": {"a": 99, "b": 2}}}
    b = {"profiles": {"home": {"a": 1, "b": 99}}}
    result = compute_merge(ancestor, a, b)
    assert result.conflicts == ()
    assert result.resolved == {"profiles": {"home": {"a": 99, "b": 99}}}


# ---- delete cases ---------------------------------------------------------


def test_both_sides_delete_same_key_no_conflict() -> None:
    result = compute_merge({"x": 1}, {}, {})
    assert result.conflicts == ()
    assert result.resolved == {}


def test_one_side_deletes_other_modifies_conflicts() -> None:
    ancestor = {"profiles": {"home": {"name": "home"}}}
    a: dict[str, Any] = {"profiles": {}}  # deleted home
    b = {"profiles": {"home": {"name": "personal"}}}
    result = compute_merge(ancestor, a, b)
    assert len(result.conflicts) == 1
    c = result.conflicts[0]
    assert c.kind is ConflictKind.DELETE_VS_MODIFY
    assert c.path == ("profiles", "home")
    assert c.side_a is DELETE_SENTINEL
    assert c.side_b == {"name": "personal"}


def test_one_side_deletes_other_untouched_takes_deletion() -> None:
    ancestor = {"profiles": {"home": {"name": "home"}}}
    a: dict[str, Any] = {"profiles": {}}  # deleted
    b = ancestor
    result = compute_merge(ancestor, a, b)
    assert result.conflicts == ()
    assert "home" not in result.resolved["profiles"]


def test_one_side_deletes_other_no_op_modification_takes_deletion() -> None:
    """If B 'modified' to the same value as ancestor, deletion still wins."""
    ancestor = {"profiles": {"home": {"name": "home"}}}
    a: dict[str, Any] = {"profiles": {}}  # deleted
    b = {"profiles": {"home": {"name": "home"}}}  # no actual change
    result = compute_merge(ancestor, a, b)
    assert result.conflicts == ()
    assert "home" not in result.resolved["profiles"]


# ---- recursive dict merge ------------------------------------------------


def test_nested_additive_in_existing_dict() -> None:
    """Both sides add different keys inside the same already-existing dict."""
    ancestor = {"hosts": {"host_existing": {"name": "old"}}}
    a = {
        "hosts": {
            "host_existing": {"name": "old"},
            "host_a": {"name": "alice"},
        }
    }
    b = {
        "hosts": {
            "host_existing": {"name": "old"},
            "host_b": {"name": "bob"},
        }
    }
    result = compute_merge(ancestor, a, b)
    assert result.conflicts == ()
    assert set(result.resolved["hosts"].keys()) == {"host_existing", "host_a", "host_b"}


def test_deep_nested_conflict() -> None:
    """Conflict path is captured deeply."""
    ancestor = {"hosts": {"host_a": {"repos": {"base": {"mode": "ro"}}}}}
    a = {"hosts": {"host_a": {"repos": {"base": {"mode": "rw"}}}}}
    b = {"hosts": {"host_a": {"repos": {"base": {"mode": "pr"}}}}}
    result = compute_merge(ancestor, a, b)
    assert len(result.conflicts) == 1
    c = result.conflicts[0]
    assert c.path == ("hosts", "host_a", "repos", "base", "mode")
    assert c.kind is ConflictKind.BOTH_MODIFIED


# ---- apply_resolutions ----------------------------------------------------


def test_apply_resolutions_take_side_a() -> None:
    ancestor = {"v": 1}
    a = {"v": 2}
    b = {"v": 3}
    merge = compute_merge(ancestor, a, b)
    final = apply_resolutions(merge, {("v",): 2})
    assert final == {"v": 2}


def test_apply_resolutions_take_side_b() -> None:
    ancestor = {"v": 1}
    a = {"v": 2}
    b = {"v": 3}
    merge = compute_merge(ancestor, a, b)
    final = apply_resolutions(merge, {("v",): 3})
    assert final == {"v": 3}


def test_apply_resolutions_take_deletion_via_sentinel() -> None:
    """User picks the delete side of DELETE_VS_MODIFY → final omits the key."""
    ancestor = {"profiles": {"home": {"name": "home"}}}
    a: dict[str, Any] = {"profiles": {}}
    b = {"profiles": {"home": {"name": "personal"}}}
    merge = compute_merge(ancestor, a, b)
    final = apply_resolutions(merge, {("profiles", "home"): DELETE_SENTINEL})
    assert final == {"profiles": {}}


def test_apply_resolutions_missing_path_raises() -> None:
    ancestor = {"v": 1}
    a = {"v": 2}
    b = {"v": 3}
    merge = compute_merge(ancestor, a, b)
    with pytest.raises(ValueError, match="resolutions missing"):
        apply_resolutions(merge, {})


def test_apply_resolutions_preserves_auto_merged_state() -> None:
    """User's resolution must not blow away the additive auto-merge result."""
    ancestor: dict[str, Any] = {"hosts": {}, "v": 1}
    a = {"hosts": {"host_a": {}}, "v": 2}
    b = {"hosts": {"host_b": {}}, "v": 3}
    merge = compute_merge(ancestor, a, b)
    # `v` is a conflict; resolve it.
    final = apply_resolutions(merge, {("v",): 3})
    assert final["v"] == 3
    # Both additive hosts survived.
    assert set(final["hosts"].keys()) == {"host_a", "host_b"}


# ---- input validation ----------------------------------------------------


def test_compute_merge_rejects_non_dict_inputs() -> None:
    with pytest.raises(TypeError):
        compute_merge([], {}, {})  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        compute_merge({}, "bad", {})  # type: ignore[arg-type]


# ---- realistic shape (ADR-0024 example) ----------------------------------


def test_two_hosts_bootstrapping_concurrently_auto_merges() -> None:
    """The canonical example from the ADR."""
    ancestor: dict[str, Any] = {
        "version": 1,
        "profiles": {"profile_p1": {"name": "home", "extends": None}},
        "hosts": {},
    }
    side_alice = {
        "version": 1,
        "profiles": {"profile_p1": {"name": "home", "extends": None}},
        "hosts": {
            "host_alice": {
                "name": "alice-laptop",
                "profile": "profile_p1",
                "repos": {"base": {"url": "git@x:o/r.git", "mode": "rw"}},
            }
        },
    }
    side_bob = {
        "version": 1,
        "profiles": {"profile_p1": {"name": "home", "extends": None}},
        "hosts": {
            "host_bob": {
                "name": "bob-laptop",
                "profile": "profile_p1",
                "repos": {"base": {"url": "git@x:o/r.git", "mode": "rw"}},
            }
        },
    }
    result = compute_merge(ancestor, side_alice, side_bob)
    assert result.conflicts == ()
    assert "host_alice" in result.resolved["hosts"]
    assert "host_bob" in result.resolved["hosts"]
