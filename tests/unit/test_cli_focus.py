"""CLI-layer tests for `maury focus use/current/list` per ADR-0052.

The engine is tested in `test_focus.py`. These tests cover the
click wiring, output format, exit codes, and the cross-trust-boundary
refusal message.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from maury import paths
from maury.cli import main
from maury.host_identity import SCHEMA_VERSION, HostIdentityBaseline, write_baseline
from maury.ids import new_host_id, new_profile_id


def _seed_tree(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    """Write a manifest + baseline laying out:

        base
        ├── personal (registered)
        │   └── personal:consulting
        │       └── personal:consulting:acme
        └── work

    Returns (manifest_path, target_dir, name_to_pid_map).
    """
    base_pid = new_profile_id()
    personal_pid = new_profile_id()
    consulting_pid = new_profile_id()
    acme_pid = new_profile_id()
    work_pid = new_profile_id()
    hid = new_host_id("h")

    body = {
        "version": 1,
        "profiles": {
            base_pid: {"name": "base", "extends": None},
            personal_pid: {"name": "personal", "extends": base_pid},
            consulting_pid: {"name": "personal:consulting", "extends": personal_pid},
            acme_pid: {"name": "personal:consulting:acme", "extends": consulting_pid},
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
    mpath = tmp_path / "manifest.json"
    mpath.write_text(json.dumps(body, indent=2))

    target = tmp_path / "out"
    write_baseline(
        HostIdentityBaseline(
            schema_version=SCHEMA_VERSION,
            host_id_hex="24b2a0aa",
            registered_at="2026-05-20T10:00:00Z",
            mode_id=personal_pid,
            mode_name_at_bootstrap="personal",
        ),
    )

    return (
        mpath,
        target,
        {
            "base": base_pid,
            "personal": personal_pid,
            "personal:consulting": consulting_pid,
            "personal:consulting:acme": acme_pid,
            "work": work_pid,
        },
    )


# ---- focus use happy paths ----------------------------------------------


def test_focus_use_descendant_succeeds(tmp_path: Path) -> None:
    mpath, target, _ = _seed_tree(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["focus", "use", "--manifest-file", str(mpath), "--target", str(target), "personal:consulting:acme"],
    )
    assert result.exit_code == 0, result.output
    assert "personal:consulting:acme" in result.output
    # Check the baseline was updated.
    from maury.host_identity import read_baseline

    baseline = read_baseline()
    assert baseline is not None
    assert baseline.active_focus == "personal:consulting:acme"


def test_focus_use_no_argument_clears_active_focus(tmp_path: Path) -> None:
    mpath, target, _ = _seed_tree(tmp_path)
    runner = CliRunner()
    # First set a focus.
    runner.invoke(
        main,
        ["focus", "use", "--manifest-file", str(mpath), "--target", str(target), "personal:consulting:acme"],
    )
    # Then clear it.
    result = runner.invoke(
        main,
        ["focus", "use", "--manifest-file", str(mpath), "--target", str(target)],
    )
    assert result.exit_code == 0, result.output
    assert "cleared" in result.output or "registered mode is active" in result.output

    from maury.host_identity import read_baseline

    baseline = read_baseline()
    assert baseline is not None
    assert baseline.active_focus is None


def test_focus_use_clearing_when_already_unset_is_no_op(tmp_path: Path) -> None:
    mpath, target, _ = _seed_tree(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["focus", "use", "--manifest-file", str(mpath), "--target", str(target)],
    )
    assert result.exit_code == 0, result.output
    assert "already unset" in result.output


# ---- focus use refusal paths --------------------------------------------


def test_focus_use_unknown_target_errors(tmp_path: Path) -> None:
    mpath, target, _ = _seed_tree(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["focus", "use", "--manifest-file", str(mpath), "--target", str(target), "personal:does-not-exist"],
    )
    assert result.exit_code != 0
    combined = result.output + (result.stderr or "")
    assert "does-not-exist" in combined


def test_focus_use_cross_trust_boundary_refused_with_pointer(tmp_path: Path) -> None:
    """The load-bearing safety property: switching to `work` (sibling
    subtree) must refuse and point at `mode deregister` + `mode bootstrap`."""
    mpath, target, _ = _seed_tree(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["focus", "use", "--manifest-file", str(mpath), "--target", str(target), "work"],
    )
    assert result.exit_code != 0
    combined = result.output + (result.stderr or "")
    assert "trust-boundary" in combined
    assert "mode deregister" in combined
    assert "ADR-0039" in combined


def test_focus_use_no_baseline_yet_errors(tmp_path: Path) -> None:
    mpath, _target, _ = _seed_tree(tmp_path)
    # Per ADR-0029 the baseline is global (paths.state_dir()), so remove the
    # one _seed_tree wrote to exercise the "no baseline yet" path.
    from maury.host_identity import baseline_path

    baseline_path().unlink(missing_ok=True)
    runner = CliRunner()
    # Target dir without a baseline.
    bare_target = tmp_path / "bare"
    result = runner.invoke(
        main,
        ["focus", "use", "--manifest-file", str(mpath), "--target", str(bare_target), "personal:consulting:acme"],
    )
    assert result.exit_code != 0
    combined = result.output + (result.stderr or "")
    assert "maury init" in combined


def test_focus_use_active_session_blocks_by_default(tmp_path: Path) -> None:
    mpath, target, _ = _seed_tree(tmp_path)
    # Inject a live session.
    log = paths.state_dir() / "active-sessions.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text('{"event": "session_start", "session_id": "live", "ts": "2026-05-20T10:00:00Z"}\n')

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["focus", "use", "--manifest-file", str(mpath), "--target", str(target), "personal:consulting:acme"],
    )
    assert result.exit_code != 0
    combined = result.output + (result.stderr or "")
    assert "active Claude Code session" in combined
    assert "--force-active-session" in combined


def test_focus_use_force_active_session_overrides(tmp_path: Path) -> None:
    mpath, target, _ = _seed_tree(tmp_path)
    log = paths.state_dir() / "active-sessions.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text('{"event": "session_start", "session_id": "live", "ts": "2026-05-20T10:00:00Z"}\n')

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "focus",
            "use",
            "--manifest-file",
            str(mpath),
            "--target",
            str(target),
            "--force-active-session",
            "personal:consulting:acme",
        ],
    )
    assert result.exit_code == 0, result.output


# ---- focus current ------------------------------------------------------


def test_focus_current_shows_registered_mode_when_no_focus(tmp_path: Path) -> None:
    _mpath, target, _ = _seed_tree(tmp_path)
    runner = CliRunner()
    result = runner.invoke(main, ["focus", "current", "--target", str(target)])
    assert result.exit_code == 0, result.output
    assert "personal" in result.output
    assert "no focus set" in result.output


def test_focus_current_shows_active_focus_when_set(tmp_path: Path) -> None:
    mpath, target, _ = _seed_tree(tmp_path)
    runner = CliRunner()
    runner.invoke(
        main,
        ["focus", "use", "--manifest-file", str(mpath), "--target", str(target), "personal:consulting:acme"],
    )
    result = runner.invoke(main, ["focus", "current", "--target", str(target)])
    assert result.exit_code == 0, result.output
    assert "personal:consulting:acme" in result.output


def test_focus_current_errors_when_no_baseline(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["focus", "current", "--target", str(tmp_path / "bare")])
    assert result.exit_code != 0
    combined = result.output + (result.stderr or "")
    assert "maury init" in combined


# ---- focus list ---------------------------------------------------------


def test_focus_list_shows_reachable_modes_marks_active(tmp_path: Path) -> None:
    mpath, target, _ = _seed_tree(tmp_path)
    runner = CliRunner()
    result = runner.invoke(main, ["focus", "list", "--manifest-file", str(mpath), "--target", str(target)])
    assert result.exit_code == 0, result.output
    out = result.output
    # All three reachable modes present.
    assert "personal" in out
    assert "personal:consulting" in out
    assert "personal:consulting:acme" in out
    # Sibling subtree NOT in the list.
    assert "work" not in out
    # Registered mode is marked active (no focus set yet).
    assert "*" in out


def test_focus_list_marker_follows_active_focus(tmp_path: Path) -> None:
    mpath, target, _ = _seed_tree(tmp_path)
    runner = CliRunner()
    runner.invoke(
        main,
        ["focus", "use", "--manifest-file", str(mpath), "--target", str(target), "personal:consulting:acme"],
    )
    result = runner.invoke(main, ["focus", "list", "--manifest-file", str(mpath), "--target", str(target)])
    assert result.exit_code == 0, result.output
    # Find the line with the active marker — must be the acme leaf, not the
    # registered mode.
    starred = [line for line in result.output.splitlines() if "*" in line]
    assert len(starred) == 1
    assert "personal:consulting:acme" in starred[0]


# ---- audit-log integration (ADR-0035 + ADR-0052) -----------------------


def test_focus_use_emits_focus_switched_on_success(tmp_path: Path) -> None:
    """A successful focus use writes `focus_switched` to audit.jsonl."""
    from maury.audit_log import read_events

    mpath, target, _ = _seed_tree(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["focus", "use", "--manifest-file", str(mpath), "--target", str(target), "personal:consulting:acme"],
    )
    assert result.exit_code == 0, result.output

    events = list(read_events())
    kinds = [e.event for e in events]
    assert "focus_switched" in kinds
    event = next(e for e in events if e.event == "focus_switched")
    assert event.details["to_focus"] == "personal:consulting:acme"
    assert event.details["from_focus"] is None
    assert event.details["forced_active_session"] is False
    assert event.result == "success"


def test_focus_use_clear_emits_focus_switched(tmp_path: Path) -> None:
    """Clearing an existing focus (no-arg) emits focus_switched with
    to_focus=None and the previous focus path as from_focus."""
    from maury.audit_log import read_events

    mpath, target, _ = _seed_tree(tmp_path)
    runner = CliRunner()
    # Set then clear.
    runner.invoke(
        main,
        ["focus", "use", "--manifest-file", str(mpath), "--target", str(target), "personal:consulting:acme"],
    )
    # Wipe to isolate the second event.
    (paths.state_dir() / "audit.jsonl").unlink()

    result = runner.invoke(
        main,
        ["focus", "use", "--manifest-file", str(mpath), "--target", str(target)],
    )
    assert result.exit_code == 0, result.output

    events = list(read_events())
    assert len(events) == 1
    event = events[0]
    assert event.event == "focus_switched"
    assert event.details["from_focus"] == "personal:consulting:acme"
    assert event.details["to_focus"] is None


def test_focus_use_no_audit_when_clearing_already_unset(tmp_path: Path) -> None:
    """No-op clear (focus was already unset) emits no audit event —
    nothing changed."""
    from maury.audit_log import read_events

    mpath, target, _ = _seed_tree(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["focus", "use", "--manifest-file", str(mpath), "--target", str(target)],
    )
    assert result.exit_code == 0, result.output
    assert list(read_events()) == []


def test_focus_use_refused_emits_focus_switch_refused(tmp_path: Path) -> None:
    """Cross-trust-boundary refusal emits focus_switch_refused with
    result=failure + precondition payload."""
    from maury.audit_log import read_events

    mpath, target, _ = _seed_tree(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["focus", "use", "--manifest-file", str(mpath), "--target", str(target), "work"],
    )
    assert result.exit_code != 0

    events = list(read_events())
    kinds = [e.event for e in events]
    assert "focus_switch_refused" in kinds
    event = next(e for e in events if e.event == "focus_switch_refused")
    assert event.result == "failure"
    assert event.details["precondition"] == "not_reachable"
    assert event.details["target_focus"] == "work"


def test_focus_use_active_session_refusal_emits_event(tmp_path: Path) -> None:
    from maury.audit_log import read_events

    mpath, target, _ = _seed_tree(tmp_path)
    log = paths.state_dir() / "active-sessions.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text('{"event": "session_start", "session_id": "live", "ts": "2026-05-20T10:00:00Z"}\n')

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["focus", "use", "--manifest-file", str(mpath), "--target", str(target), "personal:consulting:acme"],
    )
    assert result.exit_code != 0

    events = list(read_events())
    event = next(e for e in events if e.event == "focus_switch_refused")
    assert event.details["precondition"] == "active_sessions"
