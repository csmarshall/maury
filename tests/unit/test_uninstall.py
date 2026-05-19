"""Unit tests for `maury.uninstall` (the `maury uninstall` core logic)."""

from __future__ import annotations

import json
from pathlib import Path

from maury.uninstall import (
    MARKER,
    _is_maury_managed,
    run_uninstall,
    strip_marker_hooks,
)

# ---- _is_maury_managed ----------------------------------------------------


def test_is_maury_managed_top_level_command() -> None:
    entry = {"type": "command", "command": "/path/to/script.sh # maury-managed"}
    assert _is_maury_managed(entry)


def test_is_maury_managed_nested_command() -> None:
    """The Claude Code matcher shape nests under hooks[]."""
    entry = {
        "matcher": "*",
        "hooks": [
            {"type": "command", "command": "/path/to/maury-stage # maury-managed"},
        ],
    }
    assert _is_maury_managed(entry)


def test_is_maury_managed_user_hook_without_marker() -> None:
    entry = {"type": "command", "command": "/usr/local/bin/my-linter"}
    assert not _is_maury_managed(entry)


def test_is_maury_managed_non_dict_input() -> None:
    assert not _is_maury_managed("string")
    assert not _is_maury_managed(None)
    assert not _is_maury_managed([])


def test_is_maury_managed_marker_constant() -> None:
    assert MARKER == "# maury-managed"


# ---- strip_marker_hooks ---------------------------------------------------


def test_strip_marker_hooks_removes_only_marked_entries() -> None:
    settings = {
        "hooks": {
            "PostToolUse": [
                {"type": "command", "command": "user-linter.sh"},
                {"type": "command", "command": "/usr/local/bin/maury-log # maury-managed"},
            ],
            "Stop": [
                {"type": "command", "command": "/usr/local/bin/maury-stage # maury-managed"},
            ],
        }
    }
    new_settings, removed, kept = strip_marker_hooks(settings)
    assert removed == 2
    assert kept == 1
    # User hook survives in PostToolUse
    assert new_settings["hooks"]["PostToolUse"] == [{"type": "command", "command": "user-linter.sh"}]
    # Stop becomes empty → removed entirely from output
    assert "Stop" not in new_settings["hooks"]


def test_strip_marker_hooks_handles_nested_hook_shape() -> None:
    """ADR-0023 settings.json hooks use the nested matcher/hooks[] shape."""
    settings = {
        "hooks": {
            "Stop": [
                {
                    "matcher": "*",
                    "hooks": [
                        {"type": "command", "command": "/path/to/maury-stage # maury-managed"},
                    ],
                },
                {
                    "matcher": "*",
                    "hooks": [
                        {"type": "command", "command": "/path/to/user-hook.sh"},
                    ],
                },
            ]
        }
    }
    new_settings, removed, kept = strip_marker_hooks(settings)
    assert removed == 1
    assert kept == 1
    assert len(new_settings["hooks"]["Stop"]) == 1
    assert new_settings["hooks"]["Stop"][0]["hooks"][0]["command"] == "/path/to/user-hook.sh"


def test_strip_marker_hooks_no_hooks_block() -> None:
    settings = {"otherKey": "value"}
    new_settings, removed, kept = strip_marker_hooks(settings)
    assert removed == 0
    assert kept == 0
    assert new_settings == settings


def test_strip_marker_hooks_empties_block_when_all_marker() -> None:
    settings = {
        "hooks": {
            "Stop": [{"type": "command", "command": "/m # maury-managed"}],
        }
    }
    new_settings, removed, _kept = strip_marker_hooks(settings)
    assert removed == 1
    assert "hooks" not in new_settings


def test_strip_marker_hooks_preserves_top_level_keys() -> None:
    settings = {
        "permissions": {"allow": ["bash"]},
        "env": {"FOO": "bar"},
        "hooks": {
            "Stop": [{"type": "command", "command": "/m # maury-managed"}],
        },
    }
    new_settings, _r, _k = strip_marker_hooks(settings)
    assert new_settings["permissions"] == {"allow": ["bash"]}
    assert new_settings["env"] == {"FOO": "bar"}


# ---- run_uninstall (integration) -----------------------------------------


def _seed_full_install(target_dir: Path, home_dir: Path) -> None:
    """Create a representative ~/.claude/ + ~/.maury-host-id."""
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PostToolUse": [
                        {"type": "command", "command": "user-linter.sh"},
                        {
                            "type": "command",
                            "command": "/maury/bin/maury-log # maury-managed",
                        },
                    ],
                    "Stop": [
                        {"type": "command", "command": "/maury/bin/maury-stage # maury-managed"},
                    ],
                }
            }
        )
    )
    bin_dir = target_dir / "bin"
    bin_dir.mkdir()
    (bin_dir / "maury-log").write_text("#!/bin/sh\n")
    (bin_dir / "maury-stage").write_text("#!/bin/sh\n")
    (bin_dir / "maury-tools.sh").write_text("# tools\n")
    (bin_dir / "user-script").write_text("# user's own script\n")
    state = target_dir / "maury-state"
    state.mkdir()
    (state / "last-render.json").write_text("{}")
    (state / "claude-writes.jsonl").write_text("")
    # User content that must NOT be touched
    (target_dir / "CLAUDE.md").write_text("# user CLAUDE.md")
    (target_dir / "rules").mkdir()
    (target_dir / "rules" / "personal.md").write_text("# personal rule")
    # Host ID file
    home_dir.mkdir(parents=True, exist_ok=True)
    (home_dir / ".maury-host-id").write_text("host_abc12345_workstation\n")


def test_run_uninstall_strips_settings_hooks(tmp_path: Path) -> None:
    target = tmp_path / "claude"
    home = tmp_path / "home"
    _seed_full_install(target, home)
    summary = run_uninstall(target_dir=target, home_dir=home)
    assert summary.settings_hooks_removed == 2
    assert summary.settings_hooks_kept == 1
    loaded = json.loads((target / "settings.json").read_text())
    assert loaded["hooks"]["PostToolUse"][0]["command"] == "user-linter.sh"
    assert "Stop" not in loaded.get("hooks", {})


def test_run_uninstall_deletes_maury_bin_files_only(tmp_path: Path) -> None:
    target = tmp_path / "claude"
    home = tmp_path / "home"
    _seed_full_install(target, home)
    summary = run_uninstall(target_dir=target, home_dir=home)
    removed_names = {p.name for p in summary.bin_files_removed}
    assert removed_names == {"maury-log", "maury-stage", "maury-tools.sh"}
    # User script preserved
    assert (target / "bin" / "user-script").exists()


def test_run_uninstall_removes_state_dir_but_preserves_audit_log(tmp_path: Path) -> None:
    """Per ADR-0035, audit.jsonl is the forensic trail and survives uninstall.

    Everything else under maury-state/ goes; the audit log stays so the
    user (or a successor admin) can see when maury was removed.
    """
    target = tmp_path / "claude"
    home = tmp_path / "home"
    _seed_full_install(target, home)
    summary = run_uninstall(target_dir=target, home_dir=home)
    assert summary.state_dir_removed
    # The dir survives because audit.jsonl was preserved.
    assert (target / "maury-state").exists()
    assert (target / "maury-state" / "audit.jsonl").is_file()
    # last-render.json and claude-writes.jsonl were wiped.
    assert not (target / "maury-state" / "last-render.json").exists()
    assert not (target / "maury-state" / "claude-writes.jsonl").exists()
    # The preserved audit log contains the uninstall_completed event.
    body = (target / "maury-state" / "audit.jsonl").read_text()
    assert "uninstall_completed" in body


def test_run_uninstall_removes_host_id(tmp_path: Path) -> None:
    target = tmp_path / "claude"
    home = tmp_path / "home"
    _seed_full_install(target, home)
    summary = run_uninstall(target_dir=target, home_dir=home)
    assert summary.host_id_removed
    assert not (home / ".maury-host-id").exists()


def test_run_uninstall_leaves_user_content_untouched(tmp_path: Path) -> None:
    target = tmp_path / "claude"
    home = tmp_path / "home"
    _seed_full_install(target, home)
    run_uninstall(target_dir=target, home_dir=home)
    # User content survives
    assert (target / "CLAUDE.md").read_text() == "# user CLAUDE.md"
    assert (target / "rules" / "personal.md").read_text() == "# personal rule"
    assert (target / "bin" / "user-script").exists()


def test_run_uninstall_idempotent_when_already_clean(tmp_path: Path) -> None:
    """Running uninstall twice should not error.

    The second run still appends a new `uninstall_completed` event
    (idempotency is about the side effects on user content + maury
    installation, not about silencing audit-log writes).
    """
    target = tmp_path / "claude"
    home = tmp_path / "home"
    _seed_full_install(target, home)
    run_uninstall(target_dir=target, home_dir=home)
    summary = run_uninstall(target_dir=target, home_dir=home)
    assert summary.settings_hooks_removed == 0
    assert summary.bin_files_removed == ()
    # state_dir_removed is True because the dir still exists (audit.jsonl
    # was preserved through the first run); the second pass enters the
    # "delete-everything-except-preserved" branch again.
    assert summary.state_dir_removed
    assert not summary.host_id_removed
    # Second uninstall_completed event was appended.
    body = (target / "maury-state" / "audit.jsonl").read_text()
    assert body.count("uninstall_completed") == 2


def test_run_uninstall_handles_completely_clean_host(tmp_path: Path) -> None:
    """No settings.json, no bin dir, no state, no host-id → no errors.

    Even on a clean host, uninstall_completed lands in the audit log
    (the log dir + file are created on demand).
    """
    target = tmp_path / "claude"
    home = tmp_path / "home"
    target.mkdir()
    home.mkdir()
    summary = run_uninstall(target_dir=target, home_dir=home)
    assert summary.settings_hooks_removed == 0
    assert summary.bin_files_removed == ()
    assert not summary.host_id_removed
    assert not summary.settings_file_existed
    # state_dir_removed reflects that the freshly-created maury-state
    # (created by the audit-log write) was entered for the "delete
    # except preserved" pass. The audit log survives.
    assert summary.state_dir_removed
    assert (target / "maury-state" / "audit.jsonl").is_file()


def test_run_uninstall_skips_malformed_settings_json(tmp_path: Path) -> None:
    """Don't compound the user's problem if settings.json is broken."""
    target = tmp_path / "claude"
    home = tmp_path / "home"
    target.mkdir()
    home.mkdir()
    (target / "settings.json").write_text("{ this is not: valid json")
    summary = run_uninstall(target_dir=target, home_dir=home)
    assert summary.settings_file_existed
    assert summary.settings_hooks_removed == 0
    # File preserved verbatim
    assert (target / "settings.json").read_text() == "{ this is not: valid json"
