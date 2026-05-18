"""Unit tests for `maury.agency` (the `maury agency init` core)."""

from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path

import pytest

from maury.agency import (
    MARKER_REL_PATH,
    SCHEMA_VERSION,
    AgencyInitError,
    init_agency,
)


def _git_available() -> bool:
    try:
        return subprocess.run(["git", "--version"], capture_output=True, check=False).returncode == 0
    except OSError:
        return False


# ---- pure marker logic (no git) ------------------------------------------


def test_init_agency_writes_marker_no_git(tmp_path: Path) -> None:
    summary = init_agency(tmp_path, git_init=False)
    marker = json.loads((tmp_path / MARKER_REL_PATH).read_text())
    assert marker["schema_version"] == SCHEMA_VERSION
    assert marker["layer"] == "base"
    assert marker["agency_id"] == summary.agency_id
    assert marker["sublayers"] == []


def test_init_agency_generates_valid_uuid(tmp_path: Path) -> None:
    summary = init_agency(tmp_path, git_init=False)
    # Should parse as UUID and be version 4.
    parsed = uuid.UUID(summary.agency_id)
    assert parsed.version == 4


def test_init_agency_creates_target_dir_if_missing(tmp_path: Path) -> None:
    target = tmp_path / "agency-root"
    assert not target.exists()
    init_agency(target, git_init=False)
    assert (target / MARKER_REL_PATH).is_file()


def test_init_agency_refuses_if_marker_exists(tmp_path: Path) -> None:
    init_agency(tmp_path, git_init=False)
    with pytest.raises(AgencyInitError, match="already exists"):
        init_agency(tmp_path, git_init=False)


def test_init_agency_force_overwrites_existing_marker(tmp_path: Path) -> None:
    first = init_agency(tmp_path, git_init=False)
    second = init_agency(tmp_path, force=True, git_init=False)
    assert first.agency_id != second.agency_id
    assert second.force_used is True
    marker = json.loads((tmp_path / MARKER_REL_PATH).read_text())
    assert marker["agency_id"] == second.agency_id


def test_init_agency_accepts_provided_agency_id(tmp_path: Path) -> None:
    forced = "11111111-2222-3333-4444-555555555555"
    summary = init_agency(tmp_path, agency_id=forced, git_init=False)
    assert summary.agency_id == forced


def test_init_agency_summary_records_marker_path(tmp_path: Path) -> None:
    summary = init_agency(tmp_path, git_init=False)
    assert summary.marker_path == tmp_path / MARKER_REL_PATH


def test_init_agency_skips_git_when_disabled(tmp_path: Path) -> None:
    summary = init_agency(tmp_path, git_init=False)
    assert summary.git_initialized is False
    assert summary.git_commit_sha is None
    assert not (tmp_path / ".git").exists()


# ---- git-init behavior ---------------------------------------------------


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_init_agency_initializes_git_repo(tmp_path: Path) -> None:
    summary = init_agency(tmp_path, git_init=True)
    assert summary.git_initialized is True
    assert (tmp_path / ".git").is_dir()


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_init_agency_commits_marker(tmp_path: Path) -> None:
    summary = init_agency(tmp_path, git_init=True)
    assert summary.git_commit_sha is not None
    assert len(summary.git_commit_sha) >= 7
    # Verify the marker is tracked.
    rc = subprocess.run(
        ["git", "ls-files", "--error-unmatch", str(MARKER_REL_PATH)],
        cwd=tmp_path,
        capture_output=True,
        check=False,
    )
    assert rc.returncode == 0


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_init_agency_reuses_existing_git_repo(tmp_path: Path) -> None:
    """If target_dir is already a git repo, don't `git init` again."""
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)
    # Create an unrelated file + commit so we can assert history is preserved.
    (tmp_path / "README.md").write_text("# preexisting\n")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t.invalid",
            "commit",
            "-m",
            "initial",
        ],
        cwd=tmp_path,
        check=True,
    )
    summary = init_agency(tmp_path, git_init=True)
    assert summary.git_initialized is True
    # The README commit should still be in history.
    log = subprocess.run(
        ["git", "log", "--oneline"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "initial" in log.stdout
    assert "initialize agency" in log.stdout
