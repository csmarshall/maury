"""CLI-layer tests for `maury agency init`."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from maury.agency import MARKER_REL_PATH
from maury.cli import main


def _git_available() -> bool:
    try:
        return subprocess.run(["git", "--version"], capture_output=True, check=False).returncode == 0
    except OSError:
        return False


def test_agency_init_writes_marker_no_git_init(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["agency", "init", "--target", str(tmp_path), "--no-git-init"],
    )
    assert result.exit_code == 0, result.output
    assert "initialized agency:" in result.output
    marker = json.loads((tmp_path / MARKER_REL_PATH).read_text())
    assert marker["layer"] == "base"
    assert marker["sublayers"] == []


def test_agency_init_refuses_existing_marker(tmp_path: Path) -> None:
    runner = CliRunner()
    runner.invoke(main, ["agency", "init", "--target", str(tmp_path), "--no-git-init"])
    result = runner.invoke(main, ["agency", "init", "--target", str(tmp_path), "--no-git-init"])
    assert result.exit_code != 0
    combined = result.output + (result.stderr or "")
    assert "already exists" in combined


def test_agency_init_force_overwrites(tmp_path: Path) -> None:
    runner = CliRunner()
    runner.invoke(main, ["agency", "init", "--target", str(tmp_path), "--no-git-init"])
    first_id = json.loads((tmp_path / MARKER_REL_PATH).read_text())["agency_id"]
    result = runner.invoke(
        main,
        ["agency", "init", "--target", str(tmp_path), "--no-git-init", "--force"],
    )
    assert result.exit_code == 0, result.output
    assert "--force used" in result.output
    second_id = json.loads((tmp_path / MARKER_REL_PATH).read_text())["agency_id"]
    assert first_id != second_id


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_agency_init_default_runs_git_init_and_commits(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["agency", "init", "--target", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert (tmp_path / ".git").is_dir()
    assert "commit:" in result.output
    # Marker file is tracked
    rc = subprocess.run(
        ["git", "ls-files", "--error-unmatch", str(MARKER_REL_PATH)],
        cwd=tmp_path,
        capture_output=True,
        check=False,
    )
    assert rc.returncode == 0


def test_agency_init_default_target_is_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When --target is omitted, default '.' resolves to cwd."""
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(main, ["agency", "init", "--no-git-init"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / MARKER_REL_PATH).is_file()
