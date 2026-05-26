"""CLI-layer tests for `maury status`.

Covers section happy-paths, graceful degradation when state files are
absent, JSON round-trip, identity-baseline mismatch reporting (the
non-enforcing variant of ADR-0042's guard), and repo+git-status
plumbing.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from maury import paths
from maury.cli import main
from maury.host_identity import HostIdentityBaseline, write_baseline
from maury.ids import new_host_id, new_profile_id


def _write_manifest(
    path: Path,
    *,
    hostname: str = "laptop",
    host_id: str | None = None,
) -> tuple[str, str]:
    """Write a minimal v2 manifest. Returns (profile_id, host_id)."""
    pid = new_profile_id()
    hid = host_id or new_host_id("h")
    body = {
        "version": 1,
        "profiles": {pid: {"name": "home", "extends": None}},
        "hosts": {
            hid: {
                "name": hostname,
                "profile": pid,
                "repos": {
                    "base": {
                        "url": "git@codeberg.org:user/maury-base.git",
                        "mode": "rw",
                    }
                },
            }
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body))
    return pid, hid


# ---- everything-absent baseline -----------------------------------------


def test_status_with_no_state_files_renders_all_degraded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fresh tmp_path with no host-id file, no baseline, no manifest:
    each section renders its `(not initialized)` note rather than aborting."""
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "status",
            "--target",
            str(tmp_path / "target"),
            "--repos-root",
            str(tmp_path / "repos"),
        ],
    )
    assert result.exit_code == 0, result.output
    out = result.output
    assert "host identity" in out
    assert "no host-id file" in out
    assert "identity baseline" in out
    assert "no baseline" in out
    assert "last render" in out
    assert "not yet rendered" in out
    assert "drift" in out
    assert "no baseline" in out
    assert "mining watermarks" in out
    assert "not yet mined" in out


# ---- host identity ------------------------------------------------------


def test_status_host_identity_resolves_against_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When the host id is registered in the manifest, status surfaces
    the human name + mode."""
    mpath = tmp_path / "manifest.json"
    _pid, hid = _write_manifest(mpath, hostname="my-workstation")

    host_id_file = paths.host_id_file()
    host_id_file.write_text(hid + "\n")

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "status",
            "--manifest-file",
            str(mpath),
            "--target",
            str(tmp_path / "target"),
            "--repos-root",
            str(tmp_path / "repos"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "my-workstation" in result.output
    assert "home" in result.output  # mode name


def test_status_host_identity_unregistered_shows_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fresh host_id_file with hid not in manifest → ⚠️ note + 'not in manifest'."""
    mpath = tmp_path / "manifest.json"
    _write_manifest(mpath)  # uses a fresh hid

    host_id_file = paths.host_id_file()
    host_id_file.write_text(new_host_id("h") + "\n")  # a *different* host_id

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "status",
            "--manifest-file",
            str(mpath),
            "--target",
            str(tmp_path / "target"),
            "--repos-root",
            str(tmp_path / "repos"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "not in manifest" in result.output
    assert "⚠️" in result.output


# ---- identity baseline (ADR-0042 — REPORT, don't enforce) ---------------


def test_status_baseline_match_reports_match(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "target"
    host_id_file = paths.host_id_file()
    host_id_file.write_text("host_24b2a0aa_laptop\n")
    write_baseline(
        HostIdentityBaseline(
            schema_version=1,
            host_id_hex="24b2a0aa",
            registered_at="2026-05-14T15:42:11Z",
            mode_id="mode_3f1a",
            mode_name_at_bootstrap="home",
        ),
    )
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "status",
            "--target",
            str(target),
            "--repos-root",
            str(tmp_path / "repos"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "✓ match" in result.output


def test_status_baseline_mismatch_reports_warning_but_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-0042 separation: status REPORTS identity mismatch with ⚠️ but
    does NOT abort. sync/reconcile/mine enforce; status diagnoses."""
    target = tmp_path / "target"
    host_id_file = paths.host_id_file()
    host_id_file.write_text("host_88ff77ee_new\n")  # different hex
    write_baseline(
        HostIdentityBaseline(
            schema_version=1,
            host_id_hex="24b2a0aa",
            registered_at="2026-05-14T15:42:11Z",
            mode_id="mode_3f1a",
            mode_name_at_bootstrap="home",
        ),
    )
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "status",
            "--target",
            str(target),
            "--repos-root",
            str(tmp_path / "repos"),
        ],
    )
    # Critical: exit 0 (not 1) — status doesn't enforce the guard.
    assert result.exit_code == 0, result.output
    out = result.output
    assert "mismatch" in out
    assert "⚠️" in out
    assert "24b2a0aa" in out  # baseline hex
    assert "88ff77ee" in out  # current hex
    assert "remediation" in out
    assert "maury sync --confirm-identity-change" in out
    assert "maury init --reset" in out


# ---- repos + git status -------------------------------------------------


def test_status_repos_section_lists_declared_remotes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The manifest's host entry has a `repos` dict; status surfaces
    each entry's URL + mode + clone path."""
    mpath = tmp_path / "manifest.json"
    _pid, hid = _write_manifest(mpath)

    host_id_file = paths.host_id_file()
    host_id_file.write_text(hid + "\n")

    repos_root = tmp_path / "repos"  # NOT pre-cloned

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "status",
            "--manifest-file",
            str(mpath),
            "--target",
            str(tmp_path / "target"),
            "--repos-root",
            str(repos_root),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "base" in result.output
    assert "git@codeberg.org:user/maury-base.git" in result.output
    assert "(not cloned" in result.output  # since we didn't actually clone


def test_status_repos_section_reports_git_status_for_cloned_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the repo is cloned and git is available, status shows the
    current branch + dirty flag."""
    mpath = tmp_path / "manifest.json"
    _pid, hid = _write_manifest(mpath)

    host_id_file = paths.host_id_file()
    host_id_file.write_text(hid + "\n")

    # Create a real git repo at the expected clone path
    repos_root = tmp_path / "repos"
    clone_path = repos_root / "base"
    clone_path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=clone_path, check=True)
    subprocess.run(["git", "-C", str(clone_path), "config", "user.email", "t@e.com"], check=True)
    subprocess.run(["git", "-C", str(clone_path), "config", "user.name", "T"], check=True)
    (clone_path / "README.md").write_text("hello\n")
    subprocess.run(["git", "-C", str(clone_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(clone_path), "commit", "-q", "-m", "initial"], check=True)

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "status",
            "--manifest-file",
            str(mpath),
            "--target",
            str(tmp_path / "target"),
            "--repos-root",
            str(repos_root),
        ],
    )
    assert result.exit_code == 0, result.output
    out = result.output
    assert "branch=main" in out
    assert "✓ clean" in out


def test_status_repos_section_reports_dirty_worktree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mpath = tmp_path / "manifest.json"
    _pid, hid = _write_manifest(mpath)

    host_id_file = paths.host_id_file()
    host_id_file.write_text(hid + "\n")

    repos_root = tmp_path / "repos"
    clone_path = repos_root / "base"
    clone_path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=clone_path, check=True)
    subprocess.run(["git", "-C", str(clone_path), "config", "user.email", "t@e.com"], check=True)
    subprocess.run(["git", "-C", str(clone_path), "config", "user.name", "T"], check=True)
    (clone_path / "README.md").write_text("hello\n")
    subprocess.run(["git", "-C", str(clone_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(clone_path), "commit", "-q", "-m", "initial"], check=True)
    # Now make it dirty.
    (clone_path / "README.md").write_text("hello modified\n")
    (clone_path / "NEW.md").write_text("untracked\n")

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "status",
            "--manifest-file",
            str(mpath),
            "--target",
            str(tmp_path / "target"),
            "--repos-root",
            str(repos_root),
        ],
    )
    assert result.exit_code == 0, result.output
    out = result.output
    assert "⚠️ dirty" in out
    assert "2 files" in out  # README modified + NEW untracked


# ---- last render + drift ------------------------------------------------


def test_status_reports_last_render_when_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from maury.drift import LastRender, write_last_render

    target = tmp_path / "target"
    write_last_render(
        LastRender(
            schema_version=1,
            rendered_at="2026-05-15T10:00:00Z",
            host_id="host_aaaaaaaa_dummy",
            profile_id="mode_3f1a",
            files=[],
        ),
    )
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "status",
            "--target",
            str(target),
            "--repos-root",
            str(tmp_path / "repos"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "rendered at:" in result.output
    assert "2026-05-15T10:00:00Z" in result.output


# ---- mining watermarks -------------------------------------------------


def test_status_reports_mining_watermarks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from maury.mining_state import MiningWatermark, ProjectMiningRecord, write_watermark

    target = tmp_path / "target"
    wm = MiningWatermark()
    wm.update(
        "-Users-jdoe-work-project",
        ProjectMiningRecord(
            last_mined_at="2026-05-15T14:00:00Z",
            last_jsonl_mtime="2026-05-15T13:50:00Z",
            windows_processed=23,
            findings_count=8,
        ),
    )
    write_watermark(wm)

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "status",
            "--target",
            str(target),
            "--repos-root",
            str(tmp_path / "repos"),
        ],
    )
    assert result.exit_code == 0, result.output
    out = result.output
    assert "mining watermarks" in out
    assert "-Users-jdoe-work-project" in out
    assert "findings=8" in out
    assert "windows=23" in out


# ---- JSON output --------------------------------------------------------


def test_status_json_round_trips(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`--format json` emits a parseable dict with every section keyed."""
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "status",
            "--target",
            str(tmp_path / "target"),
            "--repos-root",
            str(tmp_path / "repos"),
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    # Every section keyed under the same names as the text renderer
    # branches on. Order is not asserted (JSON is unordered).
    assert "host_identity" in payload
    assert "identity_baseline" in payload
    assert "repos" in payload
    assert "last_render" in payload
    assert "drift" in payload
    assert "mining_watermarks" in payload
    # The absence sections all have `present: False`.
    assert payload["host_identity"]["present"] is False
    assert payload["identity_baseline"]["present"] is False
    assert payload["last_render"]["present"] is False
    assert payload["drift"]["present"] is False
    assert payload["mining_watermarks"]["present"] is False
