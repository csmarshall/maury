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

from maury import paths
from maury.cli import main
from maury.ids import new_host_id, new_profile_id


def _write_manifest(path: Path, *, hostname: str = "definitely-not-this-machine-xyz") -> tuple[str, str]:
    """Write a minimal v2 manifest with one host. Default hostname is
    chosen so `socket.gethostname()` will not match, exercising the
    sync engine's host-not-in-manifest error path without git I/O."""
    pid = new_profile_id()
    hid = new_host_id("h")
    body = {
        "version": 1,
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


# ---- post-host-id output paths -----------------------------------------


def test_sync_check_dry_run_prints_warnings_and_host_lines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end --check dry-run with `_identify_host` monkeypatched so
    sync proceeds past host identification. Covers cli.py post-host-id
    output: host/profile lines, _progress callback echo, warnings block,
    and the --check footer.

    No git I/O happens because --check skips _sync_one_repo's network
    calls; render is also skipped (base path absent under repos_root)
    and surfaced as a warning.

    Monkeypatching `_identify_host` is necessary because the default
    `host_id_file=HOST_ID_FILE` is bound at definition time; patching
    `maury.sync.HOST_ID_FILE` after the fact does nothing.
    """
    mpath = tmp_path / "manifest.json"
    _pid, hid = _write_manifest(mpath, hostname="fixture-host")
    # Build the host_spec we want _identify_host to return, then patch.
    from maury.manifest import load_manifest

    manifest = load_manifest(mpath)
    host_spec = manifest.hosts[hid]

    def fake_identify_host(manifest_arg: object, host_id_file: object) -> tuple[str, object]:
        return hid, host_spec

    monkeypatch.setattr("maury.sync._identify_host", fake_identify_host)

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
    assert result.exit_code == 0, result.output
    # host: / profile: lines appear post-_identify_host
    assert "host:" in result.output
    assert "profile:" in result.output
    # _progress callback fired once per repo (the seed manifest has one "base" repo)
    assert "base" in result.output
    # Dry-run + missing base clone → warning block
    assert "warnings:" in result.output
    # Final --check footer
    assert "no remote I/O performed and no files written" in result.output


def test_sync_no_host_id_no_hostname_match_exits_1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """If `_identify_host` raises SyncError (host id stale or hostname
    not in manifest), the CLI surfaces it as a ClickException with the
    friendly `Run `maury init`` pointer."""
    from maury.sync import SyncError

    def fake_identify_host(*_a: object, **_kw: object) -> tuple[str, object]:
        raise SyncError(
            "this host (hostname='ghost-host') is not in the manifest "
            "and no /nonexistent/.maury-host-id exists. Run `maury init` first."
        )

    monkeypatch.setattr("maury.sync._identify_host", fake_identify_host)

    mpath = tmp_path / "manifest.json"
    _write_manifest(mpath, hostname="other-host")
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
    assert result.exit_code == 1
    combined = result.output + (result.stderr or "")
    assert "Run `maury init`" in combined


# ---- ADR-0042 host-identity guard --------------------------------------


def test_sync_identity_guard_refuses_on_hex_change(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When `~/.maury-host-id` hex differs from the baseline, sync aborts
    with the verbose change message and exit 1."""
    from maury.host_identity import HostIdentityBaseline, write_baseline

    host_id_file = paths.host_id_file()
    host_id_file.write_text("host_88ff77ee_new\n")

    target = tmp_path / "out"
    write_baseline(
        target,
        HostIdentityBaseline(
            schema_version=1,
            host_id_hex="24b2a0aa",  # different hex → REFUSED
            registered_at="2026-05-14T15:42:11Z",
            mode_id="mode_home",
            mode_name_at_bootstrap="home",
        ),
    )

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
            str(target),
            "--repos-root",
            str(tmp_path / "repos"),
            "--check",
        ],
    )
    assert result.exit_code == 1
    combined = result.output + (result.stderr or "")
    assert "host identity changed" in combined
    assert "24b2a0aa" in combined  # baseline hex
    assert "88ff77ee" in combined  # current hex
    assert "--confirm-identity-change" in combined
    assert "maury init --reset" in combined


def test_sync_confirm_identity_change_acks_and_rewrites_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--confirm-identity-change` lets sync proceed past the guard and
    rewrites the baseline with the current hex."""
    from maury.host_identity import HostIdentityBaseline, read_baseline, write_baseline
    from maury.sync import SyncError

    host_id_file = paths.host_id_file()
    host_id_file.write_text("host_88ff77ee_new\n")

    target = tmp_path / "out"
    write_baseline(
        target,
        HostIdentityBaseline(
            schema_version=1,
            host_id_hex="24b2a0aa",
            registered_at="2026-05-14T15:42:11Z",
            mode_id="mode_home",
            mode_name_at_bootstrap="home",
        ),
    )

    # Make sync's downstream identity flow fail predictably so we just
    # exercise the guard wiring (not a full sync). Either:
    # - the manifest exists but host isn't in it → SyncError
    # - we patch _identify_host to raise something
    def fake_identify_host(*_a: object, **_kw: object) -> tuple[str, object]:
        raise SyncError("post-guard SyncError (test stub)")

    monkeypatch.setattr("maury.sync._identify_host", fake_identify_host)

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
            str(target),
            "--repos-root",
            str(tmp_path / "repos"),
            "--check",
            "--confirm-identity-change",
        ],
    )
    # The acknowledgement message should appear in output before the
    # SyncError stub fails the operation.
    assert "--confirm-identity-change accepted" in result.output
    # Baseline got rewritten with the new hex.
    new_baseline = read_baseline(target)
    assert new_baseline is not None
    assert new_baseline.host_id_hex == "88ff77ee"


def test_sync_baseline_missing_raises_state_corruption(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`~/.maury-host-id` present but `host-identity.json` absent is
    state corruption (init writes both atomically). The pre-release
    "silently auto-upgrade" path was retired 2026-05-19; the guard
    now refuses and points the user at `maury init --reset`."""
    from maury.host_identity import baseline_path

    host_id_file = paths.host_id_file()
    host_id_file.write_text("host_24b2a0aa_laptop\n")

    target = tmp_path / "out"
    assert not baseline_path(target).exists()

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
            str(target),
            "--repos-root",
            str(tmp_path / "repos"),
            "--check",
        ],
    )
    assert result.exit_code != 0
    combined = result.output + (result.stderr or "")
    assert "baseline missing" in combined
    assert "maury init --reset" in combined


def test_sync_no_host_id_file_skips_guard_silently(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When `~/.maury-host-id` doesn't exist, the guard returns silently
    (downstream code surfaces the 'no host id' error its own way)."""
    # Not creating the host-id file (absent by default under the isolated
    # XDG config root), so the guard has nothing to compare and returns.
    mpath = tmp_path / "manifest.json"
    _write_manifest(mpath, hostname="will-not-match")
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
    # Exit code is whatever downstream returns; the point is the guard
    # didn't fire (no host-identity-changed text).
    combined = result.output + (result.stderr or "")
    assert "host identity changed" not in combined


# ---- target / repos-root home expansion --------------------------------


def test_sync_target_default_help_shows_tilde() -> None:
    """`sync --help` must show `~/.claude` for `--target`, not the resolved path."""
    runner = CliRunner()
    result = runner.invoke(main, ["sync", "--help"])
    assert result.exit_code == 0
    # Defaults are displayed as `~/...` (no leakage of the runner's home dir).
    assert "~/.claude" in result.output
    assert "~/.config/maury/repos" in result.output
