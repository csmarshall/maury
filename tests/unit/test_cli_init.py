"""CLI-layer tests for `maury init` drift flags.

The drift policy itself is covered in `test_init.py` at the API level.
These tests cover only the click wiring: flag parsing, mutual exclusion,
exit codes.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from maury.cli import main
from maury.ids import new_host_id, new_profile_id


def _make_minimal_repo(
    tmp_path: Path,
    *,
    hostname: str = "synthetic-host",
    monkeypatch: pytest.MonkeyPatch | None = None,
) -> Path:
    """Create a fixture base repo + pre-write the host-id file.

    Per ADR-0039 step 8 (post-2026-05-14), init no longer falls back to
    hostname matching; identity comes only from `~/.maury-host-id`.

    `HOST_ID_FILE` is captured at module import time as `Path.home() /
    ".maury-host-id"`, so `monkeypatch.setenv("HOME", ...)` after import
    doesn't redirect it. To confine init's reads to tmp_path, the test
    must monkeypatch `maury.bootstrap.init_cmd.HOST_ID_FILE` directly.
    Pass `monkeypatch=monkeypatch` and this helper does that + writes the
    manifest's hid into the redirected location.
    """
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
    if monkeypatch is not None:
        host_id_file = tmp_path / ".maury-host-id"
        host_id_file.write_text(hid + "\n")
        monkeypatch.setattr("maury.bootstrap.init_cmd.HOST_ID_FILE", host_id_file)
    return repo


def test_init_cli_force_and_non_interactive_mutually_exclusive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))  # confine ~/.maury-host-id
    repo = _make_minimal_repo(tmp_path, monkeypatch=monkeypatch)
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
    repo = _make_minimal_repo(tmp_path, monkeypatch=monkeypatch)
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
    repo = _make_minimal_repo(tmp_path, monkeypatch=monkeypatch)
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
    repo = _make_minimal_repo(tmp_path, monkeypatch=monkeypatch)
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
    repo = _make_minimal_repo(tmp_path, monkeypatch=monkeypatch)
    target = tmp_path / "out"
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["init", "--from-dir", str(repo), "--target", str(target)],
    )
    assert result.exit_code == 0, result.output
    assert (target / "maury-state" / "last-render.json").is_file()


# ---- --tag flag + interactive prompt (ADR-0039 §"Tag UX at bootstrap") --


def test_init_cli_tag_flag_produces_tagged_host_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`--tag laptop` on a fresh host produces a `host_<8 hex>_laptop` ID."""
    monkeypatch.setenv("HOME", str(tmp_path))
    host_id_file = tmp_path / ".maury-host-id"
    monkeypatch.setattr("maury.bootstrap.init_cmd.HOST_ID_FILE", host_id_file)
    # Repo with a *different* hid in manifest so result is "not registered"
    # but the host-id file gets written with the tagged form.
    repo = _make_minimal_repo(tmp_path, hostname="other-host")
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["init", "--from-dir", str(repo), "--target", str(tmp_path / "out"), "--tag", "laptop"],
    )
    # Exit 2 (not registered) is expected on a fresh host; the test cares
    # about the format of the written host_id file.
    assert result.exit_code == 2, result.output
    written = host_id_file.read_text().strip()
    assert re.match(r"^host_[0-9a-f]{8}_laptop$", written), written


def test_init_cli_tag_flag_normalizes_and_notifies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A lossy `--tag` value gets normalized; the CLI surfaces the change."""
    monkeypatch.setenv("HOME", str(tmp_path))
    host_id_file = tmp_path / ".maury-host-id"
    monkeypatch.setattr("maury.bootstrap.init_cmd.HOST_ID_FILE", host_id_file)
    repo = _make_minimal_repo(tmp_path, hostname="other-host")
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["init", "--from-dir", str(repo), "--target", str(tmp_path / "out"), "--tag", "XADAM___"],
    )
    assert "normalized to 'xadam---'" in result.output, result.output
    written = host_id_file.read_text().strip()
    assert written.endswith("_xadam---"), written


def test_init_cli_no_tag_in_non_tty_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CliRunner gives a non-TTY stdin. Without `--tag` and without an
    existing host-id file, init must refuse rather than silently default."""
    monkeypatch.setenv("HOME", str(tmp_path))
    host_id_file = tmp_path / ".maury-host-id"
    monkeypatch.setattr("maury.bootstrap.init_cmd.HOST_ID_FILE", host_id_file)
    repo = _make_minimal_repo(tmp_path, hostname="other-host")
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["init", "--from-dir", str(repo), "--target", str(tmp_path / "out")],
    )
    assert result.exit_code != 0
    combined = result.output + (result.stderr or "")
    assert "stdin is not a TTY" in combined or "non-interactive" in combined


def test_init_cli_existing_host_id_file_skips_tag_prompt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When `~/.maury-host-id` exists, the tag prompt is irrelevant —
    init reads the existing file and uses it as-is. Non-TTY without
    `--tag` should NOT error in this case."""
    monkeypatch.setenv("HOME", str(tmp_path))
    repo = _make_minimal_repo(tmp_path, monkeypatch=monkeypatch)
    target = tmp_path / "out"
    runner = CliRunner()
    # No --tag, no input on stdin — would error if tag-prompt fired.
    result = runner.invoke(main, ["init", "--from-dir", str(repo), "--target", str(target)])
    assert result.exit_code == 0, result.output


# ---- _resolve_init_tag helper-function tests ---------------------------


def test_resolve_init_tag_explicit_value_normalizes(monkeypatch: pytest.MonkeyPatch) -> None:
    from maury.cli import _resolve_init_tag

    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    # Lossy input: should still return the normalized form (no error).
    assert _resolve_init_tag(explicit_tag="LAPTOP") == "laptop"
    assert _resolve_init_tag(explicit_tag="weird name!") == "weird-name-"


def test_init_cli_reset_deletes_existing_host_id_and_baseline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`--reset` deletes both `~/.maury-host-id` and `host-identity.json`
    before running the normal init flow. Per ADR-0042's re-anchor path."""
    from maury.host_identity import HostIdentityBaseline, baseline_path, write_baseline

    monkeypatch.setenv("HOME", str(tmp_path))
    host_id_file = tmp_path / ".maury-host-id"
    monkeypatch.setattr("maury.bootstrap.init_cmd.HOST_ID_FILE", host_id_file)

    # Pre-populate both files (simulating a host that's already anchored).
    host_id_file.write_text("host_aaaaaaaa_old-laptop\n")
    target = tmp_path / "out"
    write_baseline(
        target,
        HostIdentityBaseline(
            schema_version=1,
            host_id_hex="aaaaaaaa",
            registered_at="2026-01-01T00:00:00Z",
            mode_id="mode_old",
            mode_name_at_bootstrap="old",
        ),
    )
    assert baseline_path(target).exists()

    # Repo with a *different* hid so result after reset is "not registered".
    repo = _make_minimal_repo(tmp_path, hostname="other-host")
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "init",
            "--from-dir",
            str(repo),
            "--target",
            str(target),
            "--reset",
            "--tag",
            "new-laptop",
        ],
    )
    assert result.exit_code == 2, result.output  # not registered after reset
    # The host_id_file was rewritten with a *different* hex (fresh UUID + new tag).
    new_id = host_id_file.read_text().strip()
    assert new_id.endswith("_new-laptop")
    assert not new_id.startswith("host_aaaaaaaa")
    # Baseline was deleted (and not re-created since unregistered).
    assert not baseline_path(target).exists()
    # Reset messages surfaced.
    assert "--reset: removing" in result.output


def test_resolve_init_tag_explicit_empty_value_errors() -> None:
    """An explicitly-passed empty/all-special tag should ClickException
    rather than silently fall back to interactive prompt."""
    from maury.cli import _resolve_init_tag

    with pytest.raises(click.ClickException):
        _resolve_init_tag(explicit_tag="")  # normalize_tag raises ValueError


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
    assert "/Users/" not in result.output, f"help for {command_path} leaks resolved /Users/... path:\n{result.output}"
    assert "/home/" not in result.output, f"help for {command_path} leaks resolved /home/... path:\n{result.output}"


def test_init_help_shows_tilde_target_default() -> None:
    """`init --help` should render the --target default as `~/.claude`."""
    runner = CliRunner()
    result = runner.invoke(main, ["init", "--help"])
    assert result.exit_code == 0
    assert "~/.claude" in result.output, result.output
