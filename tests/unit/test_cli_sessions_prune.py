"""CLI-layer tests for `maury sessions prune`."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from maury.cli import main


def _line(**fields: object) -> str:
    return json.dumps(fields)


def _seed(target_dir: Path, *, old_age_hours: float = 48.0) -> Path:
    """Create active-sessions.jsonl with one ended-old + one open session."""
    log = target_dir / "maury-state" / "active-sessions.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    # Times relative to "now" — both clearly old enough to be prunable.
    log.write_text(
        _line(event="session_start", ts="2024-01-01T10:00:00Z", session_id="old")
        + "\n"
        + _line(event="session_end", ts="2024-01-01T11:00:00Z", session_id="old")
        + "\n"
        + _line(event="session_start", ts="2099-12-31T11:00:00Z", session_id="open")
        + "\n"
    )
    return log


def test_sessions_prune_removes_old_session(tmp_path: Path) -> None:
    log = _seed(tmp_path)
    runner = CliRunner()
    result = runner.invoke(main, ["sessions", "prune", "--target", str(tmp_path), "--max-age-hours", "24"])
    assert result.exit_code == 0, result.output
    assert "pruned 1 session(s)" in result.output
    assert "old" in result.output
    contents = log.read_text()
    assert '"old"' not in contents
    assert '"open"' in contents


def test_sessions_prune_dry_run_does_not_modify(tmp_path: Path) -> None:
    log = _seed(tmp_path)
    before = log.read_text()
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["sessions", "prune", "--target", str(tmp_path), "--max-age-hours", "24", "--dry-run"],
    )
    assert result.exit_code == 0, result.output
    assert "would prune 1 session(s)" in result.output
    assert log.read_text() == before


def test_sessions_prune_clean_when_log_missing(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["sessions", "prune", "--target", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "clean: no active-sessions.jsonl" in result.output


def test_sessions_prune_keeps_recent_ended_sessions(tmp_path: Path) -> None:
    """If max-age is very large, no sessions should be pruned."""
    _seed(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["sessions", "prune", "--target", str(tmp_path), "--max-age-hours", "1000000"],
    )
    assert result.exit_code == 0, result.output
    assert "pruned 0 session(s)" in result.output
