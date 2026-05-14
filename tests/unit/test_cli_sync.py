"""CLI-layer tests for `maury sync`.

The sync engine itself is exhaustively covered in `test_sync.py` and
the drift-engine + render integration in `test_drift.py` /
`test_render.py`. These tests cover the CLI wiring only:

- Flag parsing and mutual-exclusion enforcement.
- Manifest resolution path (flag → env → default).
- Error-path output formatting (SyncError → ClickException).
- The pre-flight echo lines (target / repos-root / dry-run banner).

Happy-path sync against real git remotes belongs in `test_sync.py`.
We rely on sync's pre-host-identification SyncError paths so these
tests never touch git or the network.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from maury.cli import main
from maury.ids import new_host_id, new_profile_id


def _write_manifest(path: Path, *, hostname: str = "definitely-not-this-machine-xyz") -> tuple[str, str]:
    """Write a minimal v2 manifest with one host. Default hostname is
    chosen so `socket.gethostname()` will not match, exercising the
    sync engine's host-not-in-manifest error path without git I/O."""
    pid = new_profile_id()
    hid = new_host_id()
    body = {
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
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body))
    return pid, hid


# ---- mutually-exclusive flags ------------------------------------------


def test_sync_force_and_non_interactive_are_mutually_exclusive(tmp_path: Path) -> None:
    mpath = tmp_path / "manifest.json"
    _write_manifest(mpath)
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "sync",
            "--manifest-file",
            str(mpath),
            "--target",
            str(tmp_path / "out"),
            "--repos-root",
            str(tmp_path / "repos"),
            "--force",
            "--non-interactive",
            "--check",
        ],
    )
    assert result.exit_code != 0
    combined = result.output + (result.stderr or "")
    assert "mutually exclusive" in combined


# ---- manifest resolution ------------------------------------------------


def test_sync_no_manifest_anywhere_fails_with_friendly_message(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With no --manifest-file, no env var, and no ./.meta/manifest.json,
    the CLI must error clearly rather than crashing on Path(None)."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MAURY_MANIFEST_FILE", raising=False)
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "sync",
            "--target",
            str(tmp_path / "out"),
            "--repos-root",
            str(tmp_path / "repos"),
            "--check",
        ],
    )
    assert result.exit_code != 0
    combined = result.output + (result.stderr or "")
    assert "manifest" in combined.lower()


def test_sync_resolves_manifest_via_env_var(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mpath = tmp_path / "manifest.json"
    _write_manifest(mpath)
    monkeypatch.setenv("MAURY_MANIFEST_FILE", str(mpath))
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "sync",
            "--target",
            str(tmp_path / "out"),
            "--repos-root",
            str(tmp_path / "repos"),
            "--check",
        ],
    )
    # We don't care about the exit code — only that the manifest path
    # got echoed in the pre-flight banner (proving env-var resolution
    # worked). Sync may then fail downstream on host identification.
    assert str(mpath) in result.output


# ---- pre-flight banner --------------------------------------------------


def test_sync_check_banner_announces_dry_run(tmp_path: Path) -> None:
    mpath = tmp_path / "manifest.json"
    _write_manifest(mpath)
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "sync",
            "--manifest-file",
            str(mpath),
            "--target",
            str(tmp_path / "out"),
            "--repos-root",
            str(tmp_path / "repos"),
            "--check",
        ],
    )
    assert "--check (dry-run" in result.output


def test_sync_force_banner_visible(tmp_path: Path) -> None:
    mpath = tmp_path / "manifest.json"
    _write_manifest(mpath)
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "sync",
            "--manifest-file",
            str(mpath),
            "--target",
            str(tmp_path / "out"),
            "--repos-root",
            str(tmp_path / "repos"),
            "--force",
            "--check",
        ],
    )
    assert "--force" in result.output


def test_sync_non_interactive_banner_visible(tmp_path: Path) -> None:
    mpath = tmp_path / "manifest.json"
    _write_manifest(mpath)
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "sync",
            "--manifest-file",
            str(mpath),
            "--target",
            str(tmp_path / "out"),
            "--repos-root",
            str(tmp_path / "repos"),
            "--non-interactive",
            "--check",
        ],
    )
    assert "--non-interactive" in result.output


# ---- SyncError → ClickException --------------------------------------


def test_sync_malformed_manifest_yields_click_exception(tmp_path: Path) -> None:
    """A garbage manifest must surface as a Click error, not a traceback."""
    mpath = tmp_path / "manifest.json"
    mpath.write_text("{ this is not: valid json")
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "sync",
            "--manifest-file",
            str(mpath),
            "--target",
            str(tmp_path / "out"),
            "--repos-root",
            str(tmp_path / "repos"),
            "--check",
        ],
    )
    assert result.exit_code != 0
    combined = result.output + (result.stderr or "")
    # Sync wraps ManifestError with "manifest failed to load"
    assert "manifest failed to load" in combined or "Error" in combined


def test_sync_unknown_host_yields_click_exception(tmp_path: Path) -> None:
    """No host_id_file and hostname-not-in-manifest → SyncError → exit 1.

    Uses a hostname guaranteed not to match the CI runner. The CLI
    must surface this as a ClickException (clean message, exit 1)
    rather than letting SyncError bubble up as a traceback.
    """
    mpath = tmp_path / "manifest.json"
    _write_manifest(mpath, hostname="definitely-not-this-machine-xyz")
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "sync",
            "--manifest-file",
            str(mpath),
            "--target",
            str(tmp_path / "out"),
            "--repos-root",
            str(tmp_path / "repos"),
            "--check",
        ],
    )
    # Click catches ClickException cleanly; raw SyncError would yield
    # an uncaught traceback (exit 1 with a Python error in stderr).
    assert result.exit_code == 1
    combined = result.output + (result.stderr or "")
    # The friendly text from `_identify_host`:
    assert "not in the manifest" in combined or "Run `maury init`" in combined


# ---- target / repos-root home expansion --------------------------------


def test_sync_target_default_help_shows_tilde() -> None:
    """`sync --help` must show `~/.claude` for `--target`, not the resolved path."""
    runner = CliRunner()
    result = runner.invoke(main, ["sync", "--help"])
    assert result.exit_code == 0
    # Defaults are displayed as `~/...` (no leakage of the runner's home dir).
    assert "~/.claude" in result.output
    assert "~/.config/maury/repos" in result.output
