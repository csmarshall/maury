"""CLI-layer tests for `maury audit show`."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from maury.audit_log import AUDIT_REL_PATH, log
from maury.cli import main


def _seed(target_dir: Path) -> None:
    log(target_dir, "sync_started", host_id="host_a", repos=["base"])
    log(target_dir, "render_applied", host_id="host_a", files_written=["CLAUDE.md"])
    log(target_dir, "sync_completed", host_id="host_a")


def test_audit_show_text_format(tmp_path: Path) -> None:
    _seed(tmp_path)
    runner = CliRunner()
    result = runner.invoke(main, ["audit", "show", "--target", str(tmp_path)])
    assert result.exit_code == 0, result.output
    # Newest-first order.
    sync_completed_idx = result.output.index("sync_completed")
    sync_started_idx = result.output.index("sync_started")
    assert sync_completed_idx < sync_started_idx


def test_audit_show_json_format(tmp_path: Path) -> None:
    _seed(tmp_path)
    runner = CliRunner()
    result = runner.invoke(main, ["audit", "show", "--target", str(tmp_path), "--format", "json"])
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert len(parsed) == 3


def test_audit_show_kind_filter(tmp_path: Path) -> None:
    _seed(tmp_path)
    runner = CliRunner()
    result = runner.invoke(main, ["audit", "show", "--target", str(tmp_path), "--kind", "sync_started"])
    assert result.exit_code == 0, result.output
    assert "sync_started" in result.output
    assert "render_applied" not in result.output


def test_audit_show_limit(tmp_path: Path) -> None:
    _seed(tmp_path)
    runner = CliRunner()
    result = runner.invoke(main, ["audit", "show", "--target", str(tmp_path), "--limit", "1"])
    assert result.exit_code == 0, result.output
    # Only the most recent event should appear.
    assert "sync_completed" in result.output
    assert "render_applied" not in result.output


def test_audit_show_empty_log_message(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["audit", "show", "--target", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "no audit log" in result.output


def test_audit_show_no_matches_message(tmp_path: Path) -> None:
    _seed(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["audit", "show", "--target", str(tmp_path), "--kind", "nonexistent-kind"],
    )
    assert result.exit_code == 0, result.output
    assert "no events match" in result.output


def test_audit_show_actor_filter(tmp_path: Path) -> None:
    log(tmp_path, "sync_started", actor="maury")
    log(tmp_path, "tool_use_logged", actor="hook", tool="Edit")
    runner = CliRunner()
    result = runner.invoke(main, ["audit", "show", "--target", str(tmp_path), "--actor", "hook"])
    assert result.exit_code == 0, result.output
    assert "tool_use_logged" in result.output
    assert "sync_started" not in result.output


def test_audit_show_surfaces_details(tmp_path: Path) -> None:
    log(tmp_path, "render_applied", host_id="host_a", files_written=["CLAUDE.md", "settings.json"])
    runner = CliRunner()
    result = runner.invoke(main, ["audit", "show", "--target", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "files_written" in result.output


def test_audit_show_actor_label_omitted_for_default(tmp_path: Path) -> None:
    """Default actor (maury) doesn't get a `[maury]` label since it's the assumed case."""
    log(tmp_path, "sync_started")
    runner = CliRunner()
    result = runner.invoke(main, ["audit", "show", "--target", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "[maury]" not in result.output


def test_audit_show_actor_label_shown_for_non_default(tmp_path: Path) -> None:
    log(tmp_path, "tool_use_logged", actor="hook")
    runner = CliRunner()
    result = runner.invoke(main, ["audit", "show", "--target", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "[hook]" in result.output


def test_audit_log_directory_layout_per_adr_0029(tmp_path: Path) -> None:
    """ADR-0029 mandates ~/.claude/maury-state/audit.jsonl."""
    log(tmp_path, "sync_started")
    assert (tmp_path / AUDIT_REL_PATH).is_file()
    assert AUDIT_REL_PATH.parent.name == "maury-state"
    assert AUDIT_REL_PATH.name == "audit.jsonl"
