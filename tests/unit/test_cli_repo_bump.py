"""CLI-layer tests for `maury repo bump` + repo-init hook integration."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from maury.cli import main
from maury.repo_bump import MARKER, POST_COMMIT_HOOK_REL


def _git_available() -> bool:
    try:
        return subprocess.run(["git", "--version"], capture_output=True, check=False).returncode == 0
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not _git_available(), reason="git not on PATH")

AGENCY_ID = "11111111-2222-3333-4444-555555555555"


# ---- repo init installs the hook by default ------------------------------


def test_repo_init_installs_post_commit_hook(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["repo", "init", "--target", str(tmp_path), "--agency-id", AGENCY_ID, "--owner", "a@b.c"],
    )
    assert result.exit_code == 0, result.output
    assert "post-commit hook: installed" in result.output
    hook = tmp_path / POST_COMMIT_HOOK_REL
    assert hook.is_file()
    assert MARKER in hook.read_text()


def test_repo_init_no_hook_flag_skips_hook(tmp_path: Path) -> None:
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
            "--no-hook",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "post-commit hook: skipped" in result.output
    assert not (tmp_path / POST_COMMIT_HOOK_REL).exists()


def test_repo_init_no_git_init_implies_no_hook(tmp_path: Path) -> None:
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
            "--no-git-init",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "post-commit hook" not in result.output  # neither installed nor skipped


def test_repo_init_refuses_when_user_hook_present(tmp_path: Path) -> None:
    """A pre-existing non-maury post-commit hook blocks init without --force."""
    # Pre-create the user's hook by initializing the git repo manually + adding the hook.
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    hook = tmp_path / POST_COMMIT_HOOK_REL
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\necho 'user hook'\n")
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["repo", "init", "--target", str(tmp_path), "--agency-id", AGENCY_ID, "--owner", "a@b.c"],
    )
    assert result.exit_code != 0
    combined = result.output + (result.stderr or "")
    assert "not maury-managed" in combined


# ---- maury repo bump ----------------------------------------------------


def _seed_repo_with_tag(tmp_path: Path, tag: str) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.x"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("# r\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)
    subprocess.run(["git", "tag", tag], cwd=tmp_path, check=True)


def test_repo_bump_minor(tmp_path: Path) -> None:
    _seed_repo_with_tag(tmp_path, "v1.2.3")
    runner = CliRunner()
    result = runner.invoke(main, ["repo", "bump", "--target", str(tmp_path), "--minor"])
    assert result.exit_code == 0, result.output
    assert "v1.2.3 → v1.3.0" in result.output


def test_repo_bump_major(tmp_path: Path) -> None:
    _seed_repo_with_tag(tmp_path, "v1.2.3")
    runner = CliRunner()
    result = runner.invoke(main, ["repo", "bump", "--target", str(tmp_path), "--major"])
    assert result.exit_code == 0, result.output
    assert "v1.2.3 → v2.0.0" in result.output


def test_repo_bump_requires_one_kind(tmp_path: Path) -> None:
    """Passing both or neither fails with a clear error."""
    runner = CliRunner()
    result = runner.invoke(main, ["repo", "bump", "--target", str(tmp_path), "--major", "--minor"])
    assert result.exit_code != 0
    combined = result.output + (result.stderr or "")
    assert "exactly one" in combined

    result = runner.invoke(main, ["repo", "bump", "--target", str(tmp_path)])
    assert result.exit_code != 0


def test_repo_bump_refuses_no_baseline(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    runner = CliRunner()
    result = runner.invoke(main, ["repo", "bump", "--target", str(tmp_path), "--minor"])
    assert result.exit_code != 0
    combined = result.output + (result.stderr or "")
    assert "no v" in combined.lower()


def test_repo_init_then_bump_end_to_end(tmp_path: Path) -> None:
    """init creates v1.0.0; subsequent `repo bump --minor` produces v1.1.0."""
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["repo", "init", "--target", str(tmp_path), "--agency-id", AGENCY_ID, "--owner", "a@b.c"],
    )
    assert result.exit_code == 0, result.output
    result = runner.invoke(main, ["repo", "bump", "--target", str(tmp_path), "--minor"])
    assert result.exit_code == 0, result.output
    assert "v1.0.0 → v1.1.0" in result.output
