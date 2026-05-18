"""CLI-layer tests for `maury repo init`."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from maury.cli import main
from maury.repo_init import (
    DEFAULT_INITIAL_TAG,
    GOVERNANCE_REL_PATH,
    MARKER_REL_PATH,
)

AGENCY_ID = "11111111-2222-3333-4444-555555555555"


def _git_available() -> bool:
    try:
        return subprocess.run(["git", "--version"], capture_output=True, check=False).returncode == 0
    except OSError:
        return False


def test_repo_init_writes_meta_files(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "repo",
            "init",
            "--target",
            str(tmp_path),
            "--agency-id",
            AGENCY_ID,
            "--owner",
            "alice@example.com",
            "--no-git-init",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "initialized rules repo" in result.output
    marker = json.loads((tmp_path / MARKER_REL_PATH).read_text())
    assert marker["layer"] == "rules"
    assert marker["agency_id"] == AGENCY_ID
    gov = json.loads((tmp_path / GOVERNANCE_REL_PATH).read_text())
    assert gov["owners"] == ["alice@example.com"]


def test_repo_init_accepts_multiple_owners(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "repo",
            "init",
            "--target",
            str(tmp_path),
            "--agency-id",
            AGENCY_ID,
            "--owner",
            "alice@example.com",
            "--owner",
            "bob@example.com",
            "--pr-target",
            "trunk",
            "--min-reviewers",
            "2",
            "--no-git-init",
        ],
    )
    assert result.exit_code == 0, result.output
    gov = json.loads((tmp_path / GOVERNANCE_REL_PATH).read_text())
    assert gov["owners"] == ["alice@example.com", "bob@example.com"]
    assert gov["pr_target"] == "trunk"
    assert gov["min_reviewers"] == 2


def test_repo_init_refuses_existing_meta(tmp_path: Path) -> None:
    runner = CliRunner()
    runner.invoke(
        main,
        ["repo", "init", "--target", str(tmp_path), "--agency-id", AGENCY_ID, "--owner", "a@b.c", "--no-git-init"],
    )
    result = runner.invoke(
        main,
        ["repo", "init", "--target", str(tmp_path), "--agency-id", AGENCY_ID, "--owner", "a@b.c", "--no-git-init"],
    )
    assert result.exit_code != 0
    combined = result.output + (result.stderr or "")
    assert "already exists" in combined


def test_repo_init_force_overwrites(tmp_path: Path) -> None:
    runner = CliRunner()
    runner.invoke(
        main,
        ["repo", "init", "--target", str(tmp_path), "--agency-id", AGENCY_ID, "--owner", "a@b.c", "--no-git-init"],
    )
    new_aid = "ffffffff-ffff-ffff-ffff-ffffffffffff"
    result = runner.invoke(
        main,
        [
            "repo",
            "init",
            "--target",
            str(tmp_path),
            "--agency-id",
            new_aid,
            "--owner",
            "c@d.e",
            "--no-git-init",
            "--force",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "--force used" in result.output
    marker = json.loads((tmp_path / MARKER_REL_PATH).read_text())
    assert marker["agency_id"] == new_aid


def test_repo_init_requires_owner(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["repo", "init", "--target", str(tmp_path), "--agency-id", AGENCY_ID, "--no-git-init"],
    )
    assert result.exit_code != 0
    combined = result.output + (result.stderr or "")
    assert "owner" in combined.lower()


def test_repo_init_requires_agency_id(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["repo", "init", "--target", str(tmp_path), "--owner", "a@b.c", "--no-git-init"],
    )
    assert result.exit_code != 0
    combined = result.output + (result.stderr or "")
    assert "agency-id" in combined.lower()


def test_repo_init_skip_tag_when_empty(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "repo",
            "init",
            "--target",
            str(tmp_path),
            "--agency-id",
            AGENCY_ID,
            "--owner",
            "a@b.c",
            "--initial-tag",
            "",
            "--no-git-init",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "initial tag: skipped" in result.output


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_repo_init_default_tags_and_commits(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["repo", "init", "--target", str(tmp_path), "--agency-id", AGENCY_ID, "--owner", "a@b.c"],
    )
    assert result.exit_code == 0, result.output
    assert f"initial tag: {DEFAULT_INITIAL_TAG}" in result.output
    rc = subprocess.run(
        ["git", "rev-parse", f"refs/tags/{DEFAULT_INITIAL_TAG}"],
        cwd=tmp_path,
        capture_output=True,
        check=False,
    )
    assert rc.returncode == 0
