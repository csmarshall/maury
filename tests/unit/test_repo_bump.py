"""Unit tests for `maury.repo_bump` (the post-commit hook + bump core)."""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path

import pytest

from maury.repo_bump import (
    MARKER,
    POST_COMMIT_HOOK_REL,
    RepoBumpError,
    bump,
    install_post_commit_hook,
)
from maury.semver import SemverTag


def _git_available() -> bool:
    try:
        return subprocess.run(["git", "--version"], capture_output=True, check=False).returncode == 0
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not _git_available(), reason="git not on PATH")


def _init_repo_with_commit(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.x"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("# r\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)


# ---- install_post_commit_hook --------------------------------------------


def test_install_post_commit_hook_writes_marker(tmp_path: Path) -> None:
    _init_repo_with_commit(tmp_path)
    summary = install_post_commit_hook(tmp_path)
    hook = tmp_path / POST_COMMIT_HOOK_REL
    assert hook.is_file()
    assert summary.overwrote_existing is False
    assert MARKER in hook.read_text()


def test_install_post_commit_hook_is_executable(tmp_path: Path) -> None:
    _init_repo_with_commit(tmp_path)
    install_post_commit_hook(tmp_path)
    hook = tmp_path / POST_COMMIT_HOOK_REL
    mode = hook.stat().st_mode
    assert mode & stat.S_IXUSR
    assert mode & stat.S_IXGRP
    assert mode & stat.S_IXOTH


def test_install_post_commit_hook_refuses_to_overwrite_non_maury(tmp_path: Path) -> None:
    _init_repo_with_commit(tmp_path)
    hook = tmp_path / POST_COMMIT_HOOK_REL
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\necho 'user-installed hook'\n")
    with pytest.raises(RepoBumpError, match="not maury-managed"):
        install_post_commit_hook(tmp_path)


def test_install_post_commit_hook_force_overwrites_non_maury(tmp_path: Path) -> None:
    _init_repo_with_commit(tmp_path)
    hook = tmp_path / POST_COMMIT_HOOK_REL
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\necho 'user hook'\n")
    summary = install_post_commit_hook(tmp_path, force=True)
    assert summary.overwrote_existing is True
    assert "user hook" not in hook.read_text()
    assert MARKER in hook.read_text()


def test_install_post_commit_hook_silently_overwrites_own_marker(tmp_path: Path) -> None:
    """Re-init of a maury-installed hook is fine without --force."""
    _init_repo_with_commit(tmp_path)
    install_post_commit_hook(tmp_path)
    summary = install_post_commit_hook(tmp_path)
    assert summary.overwrote_existing is True


def test_install_post_commit_hook_rejects_non_git(tmp_path: Path) -> None:
    with pytest.raises(RepoBumpError, match="not a git repo"):
        install_post_commit_hook(tmp_path)


# ---- the hook script's actual behavior ------------------------------------


def test_post_commit_hook_bumps_patch_on_next_commit(tmp_path: Path) -> None:
    """End-to-end: tag v1.2.3, commit, expect v1.2.4."""
    _init_repo_with_commit(tmp_path)
    subprocess.run(["git", "tag", "v1.2.3"], cwd=tmp_path, check=True)
    install_post_commit_hook(tmp_path)
    # New commit should fire the hook.
    (tmp_path / "newfile.txt").write_text("x")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "bump test"], cwd=tmp_path, check=True)
    tags = subprocess.run(
        ["git", "tag", "--list", "v*.*.*", "--sort=-v:refname"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    assert "v1.2.4" in tags


def test_post_commit_hook_noop_when_no_baseline_tag(tmp_path: Path) -> None:
    """No baseline → hook exits 0, no tag created."""
    _init_repo_with_commit(tmp_path)
    install_post_commit_hook(tmp_path)
    (tmp_path / "newfile.txt").write_text("x")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "no-baseline commit"], cwd=tmp_path, check=True)
    tags = subprocess.run(
        ["git", "tag", "--list"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert tags == ""  # no tags created


# ---- bump command -------------------------------------------------------


def test_bump_minor_creates_new_tag(tmp_path: Path) -> None:
    _init_repo_with_commit(tmp_path)
    subprocess.run(["git", "tag", "v1.2.3"], cwd=tmp_path, check=True)
    summary = bump(tmp_path, kind="minor")
    assert summary.prior_tag == SemverTag(1, 2, 3)
    assert summary.new_tag == SemverTag(1, 3, 0)
    assert summary.kind == "minor"
    tags = subprocess.run(["git", "tag", "--list"], cwd=tmp_path, capture_output=True, text=True, check=True).stdout
    assert "v1.3.0" in tags


def test_bump_major_resets_minor_and_patch(tmp_path: Path) -> None:
    _init_repo_with_commit(tmp_path)
    subprocess.run(["git", "tag", "v1.2.3"], cwd=tmp_path, check=True)
    summary = bump(tmp_path, kind="major")
    assert summary.new_tag == SemverTag(2, 0, 0)


def test_bump_refuses_unknown_kind(tmp_path: Path) -> None:
    _init_repo_with_commit(tmp_path)
    subprocess.run(["git", "tag", "v1.2.3"], cwd=tmp_path, check=True)
    with pytest.raises(RepoBumpError, match="bump kind"):
        bump(tmp_path, kind="patch")


def test_bump_refuses_when_no_baseline(tmp_path: Path) -> None:
    _init_repo_with_commit(tmp_path)
    with pytest.raises(RepoBumpError, match="no v"):
        bump(tmp_path, kind="minor")


def test_bump_refuses_when_target_tag_already_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Defensive: if the computed new tag already exists, refuse without overwriting.

    This collision path isn't reachable via normal flow (`latest_semver_tag`
    always picks the highest, so the next bump can't collide with what's
    already there). It IS reachable when two curators race: both call
    `repo bump --minor` against the same baseline, one wins the tag, and
    the second one's `git tag` would otherwise overwrite — which we refuse.
    Force the scenario by stubbing `latest_semver_tag` to a lower value.
    """
    _init_repo_with_commit(tmp_path)
    # Both tags exist; we'll lie about which is "latest".
    subprocess.run(["git", "tag", "v1.2.3"], cwd=tmp_path, check=True)
    subprocess.run(["git", "tag", "v1.3.0"], cwd=tmp_path, check=True)
    monkeypatch.setattr(
        "maury.repo_bump.latest_semver_tag",
        lambda _repo: SemverTag(1, 2, 3),
    )
    with pytest.raises(RepoBumpError, match="already exists"):
        bump(tmp_path, kind="minor")


def test_bump_refuses_non_git(tmp_path: Path) -> None:
    with pytest.raises(RepoBumpError, match="not a git repo"):
        bump(tmp_path, kind="minor")
