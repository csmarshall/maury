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


# ---- help-text default rendering ---------------------------------------
#
# Per a 2026-05-13 doc-review finding, command --help should render
# home-relative path defaults in their universal `~/...` form, NOT
# leak the resolved /Users/<maintainer>/... or /home/<user>/... path
# of whoever is currently running the binary. This is a UX correctness
# check, not a security check — the binary doesn't have any specific
# home dir baked in; it's just that the resolved form looks like a
# hardcoded path to first-time readers.


@pytest.mark.parametrize(
    "command_path",
    [
        ["init", "--help"],
        ["render", "--help"],
        ["reconcile", "--help"],
        ["sync", "--help"],
        ["mine", "--help"],
        ["doctor", "--help"],
    ],
)
def test_help_text_uses_tilde_for_home_defaults(command_path: list[str]) -> None:
    """Help text must show `~/...` for home-relative defaults, never a
    resolved `/Users/...` or `/home/...` path."""
    runner = CliRunner()
    result = runner.invoke(main, command_path)
    assert result.exit_code == 0, result.output

    # No `/Users/` or `/home/` substrings should appear in the help text.
    # (These would indicate that a Click default was eagerly resolved to
    # the maintainer's home directory at decorator time and leaked into
    # `show_default=True` rendering.)
    assert "/Users/" not in result.output, (
        f"help for {command_path} leaks resolved /Users/... path:\n{result.output}"
    )
    assert "/home/" not in result.output, (
        f"help for {command_path} leaks resolved /home/... path:\n{result.output}"
    )


def test_init_help_shows_tilde_target_default() -> None:
    """`init --help` should render the --target default as `~/.claude`."""
    runner = CliRunner()
    result = runner.invoke(main, ["init", "--help"])
    assert result.exit_code == 0
    assert "~/.claude" in result.output, result.output
