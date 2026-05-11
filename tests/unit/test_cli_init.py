"""CLI-layer tests for `maury init` drift flags.

The drift policy itself is covered in `test_init.py` at the API level.
These tests cover only the click wiring: flag parsing, mutual exclusion,
exit codes.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest
from click.testing import CliRunner

from maury.cli import main
from maury.ids import new_host_id, new_profile_id


def _make_minimal_repo(tmp_path: Path, *, hostname: str) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "CLAUDE.md").write_text("# base\n")
    pid = new_profile_id()
    hid = new_host_id()
    manifest = {
        "version": 2,
        "profiles": {pid: {"name": "home", "extends": None}},
        "hosts": {
            hid: {
                "name": hostname,
                "profile": pid,
                "repos": {"base": {"url": "git@x:o/r.git", "mode": "rw"}},
            }
        },
    }
    (repo / ".meta").mkdir()
    (repo / ".meta" / "manifest.json").write_text(json.dumps(manifest))
    return repo


def test_init_cli_force_and_non_interactive_mutually_exclusive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))  # confine ~/.maury-host-id
    repo = _make_minimal_repo(tmp_path, hostname=socket.gethostname())
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "init",
            "--from-dir",
            str(repo),
            "--target",
            str(tmp_path / "out"),
            "--force",
            "--non-interactive",
        ],
    )
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


def test_init_cli_default_refuses_on_pre_existing_collision_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    repo = _make_minimal_repo(tmp_path, hostname=socket.gethostname())
    target = tmp_path / "out"
    target.mkdir()
    (target / "CLAUDE.md").write_text("pre-existing\n")
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["init", "--from-dir", str(repo), "--target", str(target)],
    )
    assert result.exit_code == 1
    assert "pre-existing" in (result.output + result.stderr)
    # File untouched
    assert (target / "CLAUDE.md").read_text() == "pre-existing\n"


def test_init_cli_force_overwrites_pre_existing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    repo = _make_minimal_repo(tmp_path, hostname=socket.gethostname())
    target = tmp_path / "out"
    target.mkdir()
    (target / "CLAUDE.md").write_text("hand-edited\n")
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["init", "--from-dir", str(repo), "--target", str(target), "--force"],
    )
    assert result.exit_code == 0, result.output
    assert "--force" in result.output
    assert "hand-edited" not in (target / "CLAUDE.md").read_text()


def test_init_cli_non_interactive_refuses_with_exit_1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    repo = _make_minimal_repo(tmp_path, hostname=socket.gethostname())
    target = tmp_path / "out"
    target.mkdir()
    (target / "CLAUDE.md").write_text("pre-existing\n")
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "init",
            "--from-dir",
            str(repo),
            "--target",
            str(target),
            "--non-interactive",
        ],
    )
    assert result.exit_code == 1
    assert "--non-interactive" in (result.output + result.stderr)


def test_init_cli_clean_target_succeeds_and_writes_baseline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    repo = _make_minimal_repo(tmp_path, hostname=socket.gethostname())
    target = tmp_path / "out"
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["init", "--from-dir", str(repo), "--target", str(target)],
    )
    assert result.exit_code == 0, result.output
    assert (target / "maury-state" / "last-render.json").is_file()
