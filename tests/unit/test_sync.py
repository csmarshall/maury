"""Tests for the sync module (Phase 5 v0)."""

from __future__ import annotations

import json
import socket
import subprocess
from pathlib import Path

import pytest

from maury.ids import new_host_id, new_profile_id
from maury.sync import RepoSyncResult, SyncError, sync

# ---- helpers ------------------------------------------------------------


def _make_seed_repo(tmp_path: Path, *, hostname: str = "synthetic-host") -> Path:
    """Create a 'base' git repo with a manifest + minimal CLAUDE.md.

    Uses git init + an initial commit so subsequent `git pull` calls
    against it would work (we don't actually pull; we just test the
    sync command's plumbing).
    """
    seed = tmp_path / "seed-base"
    seed.mkdir()
    pid = new_profile_id()
    hid = new_host_id()
    manifest = {
        "version": 2,
        "profiles": {pid: {"name": "home", "extends": None}},
        "hosts": {
            hid: {
                "name": hostname,
                "profile": pid,
                "repos": {
                    # Single 'base' repo whose URL points to itself for cloning
                    # in the local-only test scenario
                    "base": {
                        "url": str(seed.absolute()),
                        "mode": "rw",
                    },
                },
            }
        },
    }
    (seed / ".meta").mkdir()
    (seed / ".meta" / "manifest.json").write_text(json.dumps(manifest, indent=2))
    (seed / "CLAUDE.md").write_text("# base\nbase rule\n")

    # Initialize git
    subprocess.run(["git", "init", "-q"], cwd=seed, check=True)
    subprocess.run(["git", "-C", str(seed), "config", "user.email", "t@e.com"], check=True)
    subprocess.run(["git", "-C", str(seed), "config", "user.name", "T"], check=True)
    subprocess.run(["git", "-C", str(seed), "add", "."], check=True)
    subprocess.run(["git", "-C", str(seed), "commit", "-q", "-m", "init"], check=True)
    return seed


# ---- error inputs -------------------------------------------------------


def test_sync_missing_manifest_raises(tmp_path):
    with pytest.raises(SyncError) as ei:
        sync(
            manifest_path=tmp_path / "nope.json",
            target_dir=tmp_path / "out",
            repos_root=tmp_path / "repos",
        )
    assert "manifest not found" in str(ei.value)


def test_sync_unknown_host_id_raises(tmp_path):
    """If ~/.maury-host-id has a value not in the manifest, fail loud."""
    seed = _make_seed_repo(tmp_path)
    host_id_file = tmp_path / ".maury-host-id"
    host_id_file.write_text("host_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n")
    with pytest.raises(SyncError) as ei:
        sync(
            manifest_path=seed / ".meta" / "manifest.json",
            target_dir=tmp_path / "out",
            repos_root=tmp_path / "repos",
            host_id_file=host_id_file,
        )
    assert "not in manifest" in str(ei.value)


def test_sync_unknown_hostname_raises(tmp_path):
    """No id file + hostname not in manifest → SyncError."""
    seed = _make_seed_repo(tmp_path, hostname="some-other-host")
    host_id_file = tmp_path / ".maury-host-id"  # doesn't exist
    with pytest.raises(SyncError) as ei:
        sync(
            manifest_path=seed / ".meta" / "manifest.json",
            target_dir=tmp_path / "out",
            repos_root=tmp_path / "repos",
            host_id_file=host_id_file,
        )
    assert "not in the manifest" in str(ei.value)


# ---- happy path: clone + render -----------------------------------------


def test_sync_clones_then_renders(tmp_path):
    seed = _make_seed_repo(tmp_path, hostname=socket.gethostname())
    repos_root = tmp_path / "repos"
    target = tmp_path / "out"
    host_id_file = tmp_path / ".maury-host-id"

    result = sync(
        manifest_path=seed / ".meta" / "manifest.json",
        target_dir=target,
        repos_root=repos_root,
        host_id_file=host_id_file,
    )
    assert not result.has_errors(), result.errors
    # First time: cloned
    assert len(result.repos) == 1
    assert result.repos[0].action == "cloned"
    assert (repos_root / "base" / ".git").exists()
    # Render produced ~/.claude/CLAUDE.md
    assert (target / "CLAUDE.md").is_file()
    content = (target / "CLAUDE.md").read_text()
    assert "base rule" in content


def test_sync_second_run_pulls_instead_of_clones(tmp_path):
    seed = _make_seed_repo(tmp_path, hostname=socket.gethostname())
    repos_root = tmp_path / "repos"
    target = tmp_path / "out"
    host_id_file = tmp_path / ".maury-host-id"

    # First run: clones
    sync(
        manifest_path=seed / ".meta" / "manifest.json",
        target_dir=target,
        repos_root=repos_root,
        host_id_file=host_id_file,
    )
    # Second run: pulls (no new commits in seed → up-to-date)
    result = sync(
        manifest_path=seed / ".meta" / "manifest.json",
        target_dir=target,
        repos_root=repos_root,
        host_id_file=host_id_file,
    )
    assert not result.has_errors()
    assert result.repos[0].action == "up-to-date"


def test_sync_dry_run_writes_nothing(tmp_path):
    seed = _make_seed_repo(tmp_path, hostname=socket.gethostname())
    repos_root = tmp_path / "repos"
    target = tmp_path / "out"
    host_id_file = tmp_path / ".maury-host-id"

    result = sync(
        manifest_path=seed / ".meta" / "manifest.json",
        target_dir=target,
        repos_root=repos_root,
        host_id_file=host_id_file,
        dry_run=True,
    )
    # No actual clone happened
    assert not (repos_root / "base").exists()
    # No render output written
    assert not (target / "CLAUDE.md").exists()
    # But result describes what *would* happen
    assert result.repos[0].action == "cloned"
    assert "dry-run" in result.repos[0].detail


def test_sync_dry_run_against_existing_clone_reports_pull(tmp_path):
    """If the clone already exists, dry-run should say 'would pull'."""
    seed = _make_seed_repo(tmp_path, hostname=socket.gethostname())
    repos_root = tmp_path / "repos"
    target = tmp_path / "out"
    host_id_file = tmp_path / ".maury-host-id"

    # First do a real sync to establish the clone
    sync(
        manifest_path=seed / ".meta" / "manifest.json",
        target_dir=target,
        repos_root=repos_root,
        host_id_file=host_id_file,
    )
    # Now dry-run
    result = sync(
        manifest_path=seed / ".meta" / "manifest.json",
        target_dir=target,
        repos_root=repos_root,
        host_id_file=host_id_file,
        dry_run=True,
    )
    assert result.repos[0].action == "up-to-date"
    assert "dry-run" in result.repos[0].detail


def test_sync_progress_callback_invoked_per_repo(tmp_path):
    seed = _make_seed_repo(tmp_path, hostname=socket.gethostname())
    seen: list[RepoSyncResult] = []
    sync(
        manifest_path=seed / ".meta" / "manifest.json",
        target_dir=tmp_path / "out",
        repos_root=tmp_path / "repos",
        host_id_file=tmp_path / ".maury-host-id",
        on_repo_progress=lambda rs: seen.append(rs),
        dry_run=True,
    )
    assert len(seen) == 1
    assert seen[0].nickname == "base"


# ---- error paths in repo sync -------------------------------------------


def test_sync_unsupported_backend_skipped_not_failed(tmp_path):
    """Backends maury doesn't yet support are skipped with a warning, not errored."""
    seed = _make_seed_repo(tmp_path, hostname=socket.gethostname())
    # Mutate the manifest to add a p4 backend
    mpath = seed / ".meta" / "manifest.json"
    m = json.loads(mpath.read_text())
    host_key = next(iter(m["hosts"]))
    m["hosts"][host_key]["repos"]["unsupported"] = {
        "url": "p4://example.com",
        "mode": "ro",
        "backend": "p4",
    }
    mpath.write_text(json.dumps(m, indent=2))

    result = sync(
        manifest_path=mpath,
        target_dir=tmp_path / "out",
        repos_root=tmp_path / "repos",
        host_id_file=tmp_path / ".maury-host-id",
        dry_run=True,
    )
    # 'base' repo: cloned (dry-run). 'unsupported' repo: skipped (no error).
    actions = {r.nickname: r.action for r in result.repos}
    assert actions["base"] == "cloned"
    assert actions["unsupported"] == "skipped"
    assert not result.has_errors()


def test_sync_clone_failure_recorded_as_error(tmp_path):
    """A bad URL → repo result has action='error', sync stops before render."""
    seed = _make_seed_repo(tmp_path, hostname=socket.gethostname())
    mpath = seed / ".meta" / "manifest.json"
    m = json.loads(mpath.read_text())
    host_key = next(iter(m["hosts"]))
    m["hosts"][host_key]["repos"]["base"]["url"] = "/path/that/does/not/exist"
    mpath.write_text(json.dumps(m, indent=2))

    result = sync(
        manifest_path=mpath,
        target_dir=tmp_path / "out",
        repos_root=tmp_path / "repos",
        host_id_file=tmp_path / ".maury-host-id",
    )
    assert result.has_errors()
    assert result.repos[0].action == "error"
    # Render did NOT run
    assert result.render_result is None


def test_sync_render_skipped_when_base_repo_missing(tmp_path):
    """If the host's manifest doesn't declare a 'base' repo, render is skipped."""
    seed = _make_seed_repo(tmp_path, hostname=socket.gethostname())
    mpath = seed / ".meta" / "manifest.json"
    m = json.loads(mpath.read_text())
    host_key = next(iter(m["hosts"]))
    # Rename 'base' to something else
    base_repo = m["hosts"][host_key]["repos"].pop("base")
    m["hosts"][host_key]["repos"]["personal"] = base_repo
    mpath.write_text(json.dumps(m, indent=2))

    result = sync(
        manifest_path=mpath,
        target_dir=tmp_path / "out",
        repos_root=tmp_path / "repos",
        host_id_file=tmp_path / ".maury-host-id",
    )
    # Repo cloned successfully; render aborts with a clear message
    assert any("no repo nicknamed 'base'" in e for e in result.errors)
    assert result.render_result is None


# ---- host id file fallback ----------------------------------------------


def test_sync_uses_host_id_file_when_present(tmp_path):
    seed = _make_seed_repo(tmp_path, hostname="not-this-hostname")
    mpath = seed / ".meta" / "manifest.json"
    m = json.loads(mpath.read_text())
    # Use the manifest's host id directly
    real_hid = next(iter(m["hosts"]))
    host_id_file = tmp_path / ".maury-host-id"
    host_id_file.write_text(real_hid + "\n")

    result = sync(
        manifest_path=mpath,
        target_dir=tmp_path / "out",
        repos_root=tmp_path / "repos",
        host_id_file=host_id_file,
        dry_run=True,
    )
    assert result.host_id == real_hid
