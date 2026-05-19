"""CLI-layer tests for `maury manifest resolve`."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from maury.cli import main
from maury.ids import new_host_id, new_profile_id


def _git_available() -> bool:
    try:
        return subprocess.run(["git", "--version"], capture_output=True, check=False).returncode == 0
    except OSError:
        return False


def _run_git(args: list[str], cwd: Path) -> None:
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


def _make_conflict_repo(
    tmp_path: Path,
    *,
    ancestor: dict[str, Any],
    ours: dict[str, Any],
    theirs: dict[str, Any],
) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run_git(["git", "init", "-q", "-b", "main"], cwd=repo)
    _run_git(["git", "config", "user.name", "t"], cwd=repo)
    _run_git(["git", "config", "user.email", "t@t.invalid"], cwd=repo)
    m = repo / "manifest.json"
    m.write_text(json.dumps(ancestor, indent=2) + "\n")
    _run_git(["git", "add", "manifest.json"], cwd=repo)
    _run_git(["git", "commit", "-q", "-m", "ancestor"], cwd=repo)
    _run_git(["git", "checkout", "-q", "-b", "theirs"], cwd=repo)
    m.write_text(json.dumps(theirs, indent=2) + "\n")
    _run_git(["git", "commit", "-q", "-am", "theirs"], cwd=repo)
    _run_git(["git", "checkout", "-q", "main"], cwd=repo)
    m.write_text(json.dumps(ours, indent=2) + "\n")
    _run_git(["git", "commit", "-q", "-am", "ours"], cwd=repo)
    subprocess.run(["git", "merge", "--no-edit", "theirs"], cwd=repo, capture_output=True, check=False)
    return m


def _seed_minimum_v2() -> dict[str, Any]:
    pid = new_profile_id()
    hid = new_host_id()
    return {
        "version": 2,
        "profiles": {pid: {"name": "home", "extends": None}},
        "hosts": {
            hid: {
                "name": "alice-laptop",
                "profile": pid,
                "repos": {"base": {"url": "git@x:o/r.git", "mode": "rw"}},
            }
        },
    }


# ---- happy path: auto-merge with no prompts ------------------------------


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_resolve_auto_merge_additive_case(tmp_path: Path) -> None:
    seed = _seed_minimum_v2()
    pid = next(iter(seed["profiles"].keys()))
    ancestor = seed
    ours = json.loads(json.dumps(ancestor))
    ours["hosts"][new_host_id()] = {
        "name": "bob-laptop",
        "profile": pid,
        "repos": {"base": {"url": "git@x:o/r.git", "mode": "rw"}},
    }
    theirs = json.loads(json.dumps(ancestor))
    theirs["hosts"][new_host_id()] = {
        "name": "carol-laptop",
        "profile": pid,
        "repos": {"base": {"url": "git@x:o/r.git", "mode": "rw"}},
    }
    manifest = _make_conflict_repo(tmp_path, ancestor=ancestor, ours=ours, theirs=theirs)

    runner = CliRunner()
    result = runner.invoke(main, ["manifest", "resolve", "--manifest-file", str(manifest)])
    assert result.exit_code == 0, result.output
    assert "auto-merged" in result.output
    assert "user resolved 0 conflict" in result.output
    body = json.loads(manifest.read_text())
    names = {h["name"] for h in body["hosts"].values()}
    assert {"alice-laptop", "bob-laptop", "carol-laptop"} <= names


# ---- conflict path: interactive prompter ---------------------------------


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_resolve_prompts_and_takes_a(tmp_path: Path) -> None:
    seed = _seed_minimum_v2()
    pid = next(iter(seed["profiles"].keys()))
    ancestor = seed
    ours = json.loads(json.dumps(ancestor))
    ours["profiles"][pid]["name"] = "renamed-ours"
    theirs = json.loads(json.dumps(ancestor))
    theirs["profiles"][pid]["name"] = "renamed-theirs"
    manifest = _make_conflict_repo(tmp_path, ancestor=ancestor, ours=ours, theirs=theirs)

    runner = CliRunner()
    # Reply to the prompt with 'a'.
    result = runner.invoke(main, ["manifest", "resolve", "--manifest-file", str(manifest)], input="a\n")
    assert result.exit_code == 0, result.output
    assert "user resolved 1 conflict" in result.output
    assert "A=1" in result.output
    body = json.loads(manifest.read_text())
    assert body["profiles"][pid]["name"] == "renamed-ours"


# ---- abort path ----------------------------------------------------------


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_resolve_abort_exits_2(tmp_path: Path) -> None:
    seed = _seed_minimum_v2()
    pid = next(iter(seed["profiles"].keys()))
    ancestor = seed
    ours = json.loads(json.dumps(ancestor))
    ours["profiles"][pid]["name"] = "x"
    theirs = json.loads(json.dumps(ancestor))
    theirs["profiles"][pid]["name"] = "y"
    manifest = _make_conflict_repo(tmp_path, ancestor=ancestor, ours=ours, theirs=theirs)

    runner = CliRunner()
    result = runner.invoke(main, ["manifest", "resolve", "--manifest-file", str(manifest)], input="q\n")
    assert result.exit_code == 2
    assert "aborted" in result.output


# ---- --non-interactive refusal -------------------------------------------


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_resolve_non_interactive_refuses_on_conflicts(tmp_path: Path) -> None:
    seed = _seed_minimum_v2()
    pid = next(iter(seed["profiles"].keys()))
    ancestor = seed
    ours = json.loads(json.dumps(ancestor))
    ours["profiles"][pid]["name"] = "x"
    theirs = json.loads(json.dumps(ancestor))
    theirs["profiles"][pid]["name"] = "y"
    manifest = _make_conflict_repo(tmp_path, ancestor=ancestor, ours=ours, theirs=theirs)

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["manifest", "resolve", "--manifest-file", str(manifest), "--non-interactive"],
    )
    assert result.exit_code != 0
    combined = result.output + (result.stderr or "")
    assert "refusing to prompt" in combined


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_resolve_non_interactive_auto_resolves_clean_case(tmp_path: Path) -> None:
    """When the merge is fully additive, --non-interactive succeeds quietly."""
    seed = _seed_minimum_v2()
    pid = next(iter(seed["profiles"].keys()))
    ancestor = seed
    ours = json.loads(json.dumps(ancestor))
    ours["hosts"][new_host_id()] = {
        "name": "bob",
        "profile": pid,
        "repos": {"base": {"url": "git@x:o/r.git", "mode": "rw"}},
    }
    theirs = json.loads(json.dumps(ancestor))
    theirs["hosts"][new_host_id()] = {
        "name": "carol",
        "profile": pid,
        "repos": {"base": {"url": "git@x:o/r.git", "mode": "rw"}},
    }
    manifest = _make_conflict_repo(tmp_path, ancestor=ancestor, ours=ours, theirs=theirs)
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["manifest", "resolve", "--manifest-file", str(manifest), "--non-interactive"],
    )
    assert result.exit_code == 0, result.output


# ---- no-conflict refusal -------------------------------------------------


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_resolve_refuses_clean_file(tmp_path: Path) -> None:
    """If the file isn't in a merge conflict, refuse with a clear error."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _run_git(["git", "init", "-q"], cwd=repo)
    _run_git(["git", "config", "user.name", "t"], cwd=repo)
    _run_git(["git", "config", "user.email", "t@t.invalid"], cwd=repo)
    manifest = repo / "manifest.json"
    manifest.write_text('{"version": 2, "profiles": {}, "hosts": {}}\n')
    _run_git(["git", "add", "manifest.json"], cwd=repo)
    _run_git(["git", "commit", "-q", "-m", "clean"], cwd=repo)

    runner = CliRunner()
    result = runner.invoke(main, ["manifest", "resolve", "--manifest-file", str(manifest)])
    assert result.exit_code != 0
    combined = result.output + (result.stderr or "")
    assert "no merge conflict" in combined
