"""Unit tests for `maury.focus` — the ADR-0052 focus engine.

The CLI surface is tested separately in `test_cli_focus.py`. These tests
hit the pure engine: is_reachable, the precondition cascade, the
active-focus pointer, and the reachable-modes enumerator.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from maury import paths
from maury.focus import (
    FocusError,
    FocusUseOutcome,
    active_focus_get,
    active_focus_set,
    evaluate_focus_use,
    is_reachable,
    list_reachable_modes,
)
from maury.host_identity import (
    SCHEMA_VERSION,
    HostIdentityBaseline,
    write_baseline,
)
from maury.ids import new_host_id, new_profile_id
from maury.manifest import parse_manifest

# ---- manifest fixture ----------------------------------------------------


def _make_tree_manifest() -> tuple[str, dict[str, str], Any]:
    """Build a manifest with this mode tree:

        base
        ├── personal
        │   └── personal:consulting
        │       ├── personal:consulting:acme
        │       └── personal:consulting:exampleco
        └── work

    Returns (registered_host_id, name_to_id_map, parsed Manifest).
    The registered host is bound to `personal` (so foci under personal
    are reachable; `work` is not).
    """
    base_pid = new_profile_id()
    personal_pid = new_profile_id()
    consulting_pid = new_profile_id()
    acme_pid = new_profile_id()
    exampleco_pid = new_profile_id()
    work_pid = new_profile_id()
    hid = new_host_id("h")

    raw = {
        "version": 1,
        "profiles": {
            base_pid: {"name": "base", "extends": None},
            personal_pid: {"name": "personal", "extends": base_pid},
            consulting_pid: {"name": "personal:consulting", "extends": personal_pid},
            acme_pid: {"name": "personal:consulting:acme", "extends": consulting_pid},
            exampleco_pid: {"name": "personal:consulting:exampleco", "extends": consulting_pid},
            work_pid: {"name": "work", "extends": base_pid},
        },
        "hosts": {
            hid: {
                "name": "alice-laptop",
                "profile": personal_pid,
                "repos": {"base": {"url": "git@x:o/r.git", "mode": "rw"}},
            }
        },
    }
    manifest = parse_manifest(json.dumps(raw))
    name_to_id = {
        "base": base_pid,
        "personal": personal_pid,
        "personal:consulting": consulting_pid,
        "personal:consulting:acme": acme_pid,
        "personal:consulting:exampleco": exampleco_pid,
        "work": work_pid,
    }
    return hid, name_to_id, manifest


def _write_baseline_for(target_dir: Path, *, mode_id: str, mode_name: str, active_focus: str | None = None) -> None:
    baseline = HostIdentityBaseline(
        schema_version=SCHEMA_VERSION,
        host_id_hex="24b2a0aa",
        registered_at="2026-05-20T10:00:00Z",
        mode_id=mode_id,
        mode_name_at_bootstrap=mode_name,
        active_focus=active_focus,
    )
    write_baseline(baseline)


# ---- is_reachable --------------------------------------------------------


def test_is_reachable_target_is_descendant_of_registered() -> None:
    """Target deeper in the registered subtree → reachable."""
    _, ids, manifest = _make_tree_manifest()
    assert is_reachable(
        target_mode_id=ids["personal:consulting:acme"],
        registered_mode_id=ids["personal"],
        manifest=manifest,
    )


def test_is_reachable_target_equals_registered() -> None:
    """Target IS the registered mode → reachable (degenerate but valid)."""
    _, ids, manifest = _make_tree_manifest()
    assert is_reachable(
        target_mode_id=ids["personal"],
        registered_mode_id=ids["personal"],
        manifest=manifest,
    )


def test_is_reachable_target_in_sibling_subtree_not_reachable() -> None:
    """Crossing to a sibling subtree (different trust boundary) → not reachable."""
    _, ids, manifest = _make_tree_manifest()
    assert not is_reachable(
        target_mode_id=ids["work"],
        registered_mode_id=ids["personal"],
        manifest=manifest,
    )


def test_is_reachable_target_is_ancestor_not_reachable() -> None:
    """Target is base (an ancestor); the registered mode isn't in
    base's chain (base has no ancestors). Not reachable — the
    operator can't move 'up' across a trust boundary."""
    _, ids, manifest = _make_tree_manifest()
    assert not is_reachable(
        target_mode_id=ids["base"],
        registered_mode_id=ids["personal"],
        manifest=manifest,
    )


def test_is_reachable_unknown_target_mode_id() -> None:
    """Unknown mode_id → not reachable (chain walk fails cleanly)."""
    _, ids, manifest = _make_tree_manifest()
    assert not is_reachable(
        target_mode_id="profile_" + "0" * 32,
        registered_mode_id=ids["personal"],
        manifest=manifest,
    )


# ---- active_focus_get / set ----------------------------------------------


def test_active_focus_get_returns_none_when_no_baseline(tmp_path: Path) -> None:
    assert active_focus_get() is None


def test_active_focus_get_returns_none_when_field_unset(tmp_path: Path) -> None:
    _, ids, _ = _make_tree_manifest()
    _write_baseline_for(tmp_path, mode_id=ids["personal"], mode_name="personal")
    assert active_focus_get() is None


def test_active_focus_set_persists_value(tmp_path: Path) -> None:
    _, ids, _ = _make_tree_manifest()
    _write_baseline_for(tmp_path, mode_id=ids["personal"], mode_name="personal")
    active_focus_set("personal:consulting:acme")
    assert active_focus_get() == "personal:consulting:acme"


def test_active_focus_set_to_none_clears_field(tmp_path: Path) -> None:
    _, ids, _ = _make_tree_manifest()
    _write_baseline_for(
        tmp_path, mode_id=ids["personal"], mode_name="personal", active_focus="personal:consulting:acme"
    )
    active_focus_set(None)
    assert active_focus_get() is None


def test_active_focus_set_raises_when_no_baseline(tmp_path: Path) -> None:
    with pytest.raises(FocusError, match="no host-identity baseline"):
        active_focus_set("personal:consulting:acme")


def test_active_focus_set_preserves_other_baseline_fields(tmp_path: Path) -> None:
    """Writing active_focus must not stomp host_id_hex, registered_at, mode_id, etc."""
    from maury.host_identity import read_baseline

    _, ids, _ = _make_tree_manifest()
    _write_baseline_for(tmp_path, mode_id=ids["personal"], mode_name="personal")
    before = read_baseline()
    assert before is not None

    active_focus_set("personal:consulting:acme")
    after = read_baseline()
    assert after is not None
    assert after.host_id_hex == before.host_id_hex
    assert after.registered_at == before.registered_at
    assert after.mode_id == before.mode_id
    assert after.mode_name_at_bootstrap == before.mode_name_at_bootstrap
    assert after.active_focus == "personal:consulting:acme"


# ---- evaluate_focus_use: precondition cascade ----------------------------


def test_evaluate_returns_not_registered_when_baseline_missing(tmp_path: Path) -> None:
    _, _, manifest = _make_tree_manifest()
    result = evaluate_focus_use(
        target_focus="personal:consulting:acme",
        manifest=manifest,
    )
    assert result.outcome is FocusUseOutcome.NOT_REGISTERED
    assert result.from_focus is None
    assert result.to_focus == "personal:consulting:acme"


def test_evaluate_returns_unknown_focus_when_target_not_in_manifest(tmp_path: Path) -> None:
    _, ids, manifest = _make_tree_manifest()
    _write_baseline_for(tmp_path, mode_id=ids["personal"], mode_name="personal")
    result = evaluate_focus_use(
        target_focus="personal:does-not-exist",
        manifest=manifest,
    )
    assert result.outcome is FocusUseOutcome.UNKNOWN_FOCUS
    assert "does-not-exist" in result.detail


def test_evaluate_returns_not_reachable_when_target_crosses_trust_boundary(tmp_path: Path) -> None:
    """Switching from personal to work (sibling subtree) must be refused."""
    _, ids, manifest = _make_tree_manifest()
    _write_baseline_for(tmp_path, mode_id=ids["personal"], mode_name="personal")
    result = evaluate_focus_use(
        target_focus="work",
        manifest=manifest,
    )
    assert result.outcome is FocusUseOutcome.NOT_REACHABLE
    assert result.target_mode_id == ids["work"]
    assert "trust-boundary" in result.detail
    assert "mode deregister" in result.detail
    assert "ADR-0039" in result.detail


def test_evaluate_returns_ok_when_target_is_reachable_descendant(tmp_path: Path) -> None:
    _, ids, manifest = _make_tree_manifest()
    _write_baseline_for(tmp_path, mode_id=ids["personal"], mode_name="personal")
    result = evaluate_focus_use(
        target_focus="personal:consulting:acme",
        manifest=manifest,
    )
    assert result.outcome is FocusUseOutcome.OK
    assert result.target_mode_id == ids["personal:consulting:acme"]
    assert result.from_focus is None
    assert result.to_focus == "personal:consulting:acme"


def test_evaluate_captures_from_focus_when_replacing_existing_focus(tmp_path: Path) -> None:
    """The 'from' side of the switch should be the current active_focus."""
    _, ids, manifest = _make_tree_manifest()
    _write_baseline_for(
        tmp_path,
        mode_id=ids["personal"],
        mode_name="personal",
        active_focus="personal:consulting:acme",
    )
    result = evaluate_focus_use(
        target_focus="personal:consulting:exampleco",
        manifest=manifest,
    )
    assert result.outcome is FocusUseOutcome.OK
    assert result.from_focus == "personal:consulting:acme"
    assert result.to_focus == "personal:consulting:exampleco"


def test_evaluate_returns_active_sessions_when_sessions_running(tmp_path: Path) -> None:
    """An active-sessions.jsonl entry with session_start but no session_end → refuse."""
    _, ids, manifest = _make_tree_manifest()
    _write_baseline_for(tmp_path, mode_id=ids["personal"], mode_name="personal")

    # Write a fake active-sessions.jsonl with one live session.
    log = paths.state_dir() / "active-sessions.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text('{"event": "session_start", "session_id": "sess-running", "ts": "2026-05-20T10:00:00Z"}\n')

    result = evaluate_focus_use(
        target_focus="personal:consulting:acme",
        manifest=manifest,
    )
    assert result.outcome is FocusUseOutcome.ACTIVE_SESSIONS
    assert "sess-running" in result.active_session_ids
    assert "--force-active-session" in result.detail


def test_evaluate_force_active_session_overrides_refusal(tmp_path: Path) -> None:
    """`force_active_session=True` bypasses precondition 4."""
    _, ids, manifest = _make_tree_manifest()
    _write_baseline_for(tmp_path, mode_id=ids["personal"], mode_name="personal")
    log = paths.state_dir() / "active-sessions.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text('{"event": "session_start", "session_id": "sess-running", "ts": "2026-05-20T10:00:00Z"}\n')
    result = evaluate_focus_use(
        target_focus="personal:consulting:acme",
        manifest=manifest,
        force_active_session=True,
    )
    assert result.outcome is FocusUseOutcome.OK


def test_evaluate_active_sessions_ignores_ended_sessions(tmp_path: Path) -> None:
    """A session with session_end logged isn't 'active' and doesn't block."""
    _, ids, manifest = _make_tree_manifest()
    _write_baseline_for(tmp_path, mode_id=ids["personal"], mode_name="personal")
    log = paths.state_dir() / "active-sessions.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        '{"event": "session_start", "session_id": "sess-old", "ts": "2026-05-20T09:00:00Z"}\n'
        '{"event": "session_end", "session_id": "sess-old", "ts": "2026-05-20T09:30:00Z"}\n'
    )
    result = evaluate_focus_use(
        target_focus="personal:consulting:acme",
        manifest=manifest,
    )
    assert result.outcome is FocusUseOutcome.OK


# ---- list_reachable_modes ------------------------------------------------


def test_list_reachable_includes_registered_and_descendants(tmp_path: Path) -> None:
    _, ids, manifest = _make_tree_manifest()
    pairs = list_reachable_modes(registered_mode_id=ids["personal"], manifest=manifest)
    names = {name for _mid, name in pairs}
    assert names == {
        "personal",
        "personal:consulting",
        "personal:consulting:acme",
        "personal:consulting:exampleco",
    }
    # Excludes the sibling subtree.
    assert "work" not in names
    assert "base" not in names


def test_list_reachable_orders_root_first_by_depth(tmp_path: Path) -> None:
    _, ids, manifest = _make_tree_manifest()
    pairs = list_reachable_modes(registered_mode_id=ids["personal"], manifest=manifest)
    names = [name for _mid, name in pairs]
    # `personal` is depth 0; `personal:consulting` is depth 1; the two leaves are depth 2.
    assert names[0] == "personal"
    assert names[1] == "personal:consulting"
    # Leaves come last; order between them is alphabetic by tie-break.
    assert names[2:] == ["personal:consulting:acme", "personal:consulting:exampleco"]
