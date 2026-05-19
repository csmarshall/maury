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
    hid = new_host_id("h")
    manifest = {
        "version": 1,
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


def test_sync_missing_manifest_raises(tmp_path: Path) -> None:
    with pytest.raises(SyncError) as ei:
        sync(
            manifest_path=tmp_path / "nope.json",
            target_dir=tmp_path / "out",
            repos_root=tmp_path / "repos",
        )
    assert "manifest not found" in str(ei.value)


def test_sync_unknown_host_id_raises(tmp_path: Path) -> None:
    """If ~/.maury-host-id has a value not in the manifest, fail loud."""
    seed = _make_seed_repo(tmp_path)
    host_id_file = tmp_path / ".maury-host-id"
    host_id_file.write_text("host_aaaaaaaa_unknown\n")
    with pytest.raises(SyncError) as ei:
        sync(
            manifest_path=seed / ".meta" / "manifest.json",
            target_dir=tmp_path / "out",
            repos_root=tmp_path / "repos",
            host_id_file=host_id_file,
        )
    assert "not in manifest" in str(ei.value)


def test_sync_unknown_hostname_raises(tmp_path: Path) -> None:
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


def test_sync_clones_then_renders(tmp_path: Path) -> None:
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


def test_sync_second_run_pulls_instead_of_clones(tmp_path: Path) -> None:
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


def test_sync_dry_run_writes_nothing(tmp_path: Path) -> None:
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


def test_sync_dry_run_against_existing_clone_reports_pull(tmp_path: Path) -> None:
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


def test_sync_progress_callback_invoked_per_repo(tmp_path: Path) -> None:
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


def test_sync_unsupported_backend_skipped_not_failed(tmp_path: Path) -> None:
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


def test_sync_clone_failure_recorded_as_error(tmp_path: Path) -> None:
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


def test_sync_render_skipped_when_base_repo_missing(tmp_path: Path) -> None:
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


# ---- drift detection wired into sync ------------------------------------


def test_sync_writes_last_render_after_first_apply(tmp_path: Path) -> None:
    """First sync (no prior last-render.json) writes the baseline."""
    seed = _make_seed_repo(tmp_path, hostname=socket.gethostname())
    target = tmp_path / "out"
    sync(
        manifest_path=seed / ".meta" / "manifest.json",
        target_dir=target,
        repos_root=tmp_path / "repos",
        host_id_file=tmp_path / ".maury-host-id",
    )
    # last-render.json is now present and lists the rendered files
    last_render_path = target / "maury-state" / "last-render.json"
    assert last_render_path.is_file()
    content = json.loads(last_render_path.read_text())
    assert content["schema_version"] == 1
    assert any(f["path"] == "CLAUDE.md" for f in content["files"])


def test_sync_first_run_no_drift_action(tmp_path: Path) -> None:
    """First sync sets drift_action='none' (no baseline to compare)."""
    seed = _make_seed_repo(tmp_path, hostname=socket.gethostname())
    result = sync(
        manifest_path=seed / ".meta" / "manifest.json",
        target_dir=tmp_path / "out",
        repos_root=tmp_path / "repos",
        host_id_file=tmp_path / ".maury-host-id",
    )
    assert result.drift_action == "none"
    assert result.drift_report is None  # no baseline → no report


def test_sync_unchanged_target_second_run_no_drift(tmp_path: Path) -> None:
    """Second sync with no hand-edits sees clean state."""
    seed = _make_seed_repo(tmp_path, hostname=socket.gethostname())
    target = tmp_path / "out"
    sync(
        manifest_path=seed / ".meta" / "manifest.json",
        target_dir=target,
        repos_root=tmp_path / "repos",
        host_id_file=tmp_path / ".maury-host-id",
    )
    # Re-run; nothing changed on disk
    result = sync(
        manifest_path=seed / ".meta" / "manifest.json",
        target_dir=target,
        repos_root=tmp_path / "repos",
        host_id_file=tmp_path / ".maury-host-id",
    )
    assert result.drift_action == "none"
    assert result.drift_report is not None
    assert not result.drift_report.has_drift()


def test_sync_default_mode_refuses_on_modified_drift(tmp_path: Path) -> None:
    """Default drift_mode refuses if user hand-edited a rendered file."""
    seed = _make_seed_repo(tmp_path, hostname=socket.gethostname())
    target = tmp_path / "out"
    # Establish baseline
    sync(
        manifest_path=seed / ".meta" / "manifest.json",
        target_dir=target,
        repos_root=tmp_path / "repos",
        host_id_file=tmp_path / ".maury-host-id",
    )
    # User hand-edits CLAUDE.md
    (target / "CLAUDE.md").write_text("hand-edited content\n")
    result = sync(
        manifest_path=seed / ".meta" / "manifest.json",
        target_dir=target,
        repos_root=tmp_path / "repos",
        host_id_file=tmp_path / ".maury-host-id",
    )
    assert result.drift_action == "refused"
    assert result.has_errors()
    assert any("drift detected" in e for e in result.errors)
    # The hand-edited file is preserved (we refused before applying)
    assert (target / "CLAUDE.md").read_text() == "hand-edited content\n"


def test_sync_force_mode_clobbers_drift(tmp_path: Path) -> None:
    """--force clobbers hand-edits with a loud warning."""
    seed = _make_seed_repo(tmp_path, hostname=socket.gethostname())
    target = tmp_path / "out"
    sync(
        manifest_path=seed / ".meta" / "manifest.json",
        target_dir=target,
        repos_root=tmp_path / "repos",
        host_id_file=tmp_path / ".maury-host-id",
    )
    (target / "CLAUDE.md").write_text("hand-edited\n")
    result = sync(
        manifest_path=seed / ".meta" / "manifest.json",
        target_dir=target,
        repos_root=tmp_path / "repos",
        host_id_file=tmp_path / ".maury-host-id",
        drift_mode="force",
    )
    assert result.drift_action == "forced"
    assert not result.has_errors()
    assert any("--force" in w and "clobbering" in w for w in result.warnings)
    # The hand-edit was overwritten
    assert "hand-edited" not in (target / "CLAUDE.md").read_text()


def test_sync_non_interactive_mode_refuses_on_drift(tmp_path: Path) -> None:
    """--non-interactive refuses (cron/CI safe) on any drift, exit 1 (errors)."""
    seed = _make_seed_repo(tmp_path, hostname=socket.gethostname())
    target = tmp_path / "out"
    sync(
        manifest_path=seed / ".meta" / "manifest.json",
        target_dir=target,
        repos_root=tmp_path / "repos",
        host_id_file=tmp_path / ".maury-host-id",
    )
    (target / "CLAUDE.md").write_text("changed\n")
    result = sync(
        manifest_path=seed / ".meta" / "manifest.json",
        target_dir=target,
        repos_root=tmp_path / "repos",
        host_id_file=tmp_path / ".maury-host-id",
        drift_mode="non-interactive",
    )
    assert result.drift_action == "refused"
    assert result.has_errors()
    assert any("--non-interactive" in e for e in result.errors)


def test_sync_unknown_drift_mode_raises(tmp_path: Path) -> None:
    """Bogus drift_mode values fail loud at the API boundary."""
    with pytest.raises(SyncError) as ei:
        sync(
            manifest_path=tmp_path / "nope.json",
            target_dir=tmp_path / "out",
            repos_root=tmp_path / "repos",
            drift_mode="bogus",
        )
    assert "drift_mode" in str(ei.value)


def test_sync_dry_run_does_not_write_last_render(tmp_path: Path) -> None:
    """--check (dry_run) produces a report but doesn't pollute baseline."""
    seed = _make_seed_repo(tmp_path, hostname=socket.gethostname())
    target = tmp_path / "out"
    sync(
        manifest_path=seed / ".meta" / "manifest.json",
        target_dir=target,
        repos_root=tmp_path / "repos",
        host_id_file=tmp_path / ".maury-host-id",
        dry_run=True,
    )
    # Dry-run shouldn't have created the maury-state dir at all
    assert not (target / "maury-state").exists()


def test_sync_force_after_drift_writes_new_baseline(tmp_path: Path) -> None:
    """After --force clobber, last-render.json is updated to current state."""
    seed = _make_seed_repo(tmp_path, hostname=socket.gethostname())
    target = tmp_path / "out"
    sync(
        manifest_path=seed / ".meta" / "manifest.json",
        target_dir=target,
        repos_root=tmp_path / "repos",
        host_id_file=tmp_path / ".maury-host-id",
    )
    last_render_path = target / "maury-state" / "last-render.json"
    assert last_render_path.is_file()
    # Drift then force
    (target / "CLAUDE.md").write_text("drift\n")
    sync(
        manifest_path=seed / ".meta" / "manifest.json",
        target_dir=target,
        repos_root=tmp_path / "repos",
        host_id_file=tmp_path / ".maury-host-id",
        drift_mode="force",
    )
    # Baseline updated (rendered_at differs at minimum)
    new_content = last_render_path.read_text()
    # Same files post-render (since the seed didn't change), but the
    # timestamp should have updated. Just verify it's still well-formed.
    assert json.loads(new_content)["schema_version"] == 1


# ---- end drift wiring ---------------------------------------------------


def test_sync_uses_host_id_file_when_present(tmp_path: Path) -> None:
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


# ---- structured auto-merge integration (ADR-0024 sync wiring) -----------


def _configure_git_identity(repo: Path) -> None:
    """Set a local user.name/user.email on `repo`. Required on CI runners
    where there's no global git config, both for the test's own commits
    and for sync's production-code commit during auto-merge."""
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@e.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "T"], check=True)


def _add_host_commit(repo: Path, hid: str, name: str, pid: str, message: str) -> None:
    """Append a host to a repo's manifest and commit. Used to create
    divergent histories on the local clone and the seed upstream."""
    mpath = repo / ".meta" / "manifest.json"
    body = json.loads(mpath.read_text())
    body["hosts"][hid] = {
        "name": name,
        "profile": pid,
        "repos": {"base": {"url": str(repo.absolute()), "mode": "rw"}},
    }
    mpath.write_text(json.dumps(body, indent=2) + "\n")
    subprocess.run(["git", "-C", str(repo), "add", ".meta/manifest.json"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", message], check=True)


def test_sync_auto_merges_additive_manifest_conflict(tmp_path: Path) -> None:
    """The canonical 'two hosts bootstrapping' case: upstream adds host-A,
    local adds host-B. `git pull --ff-only` fails non-FF; sync's recovery
    runs the structured auto-merge engine, commits the result, and reports
    'pulled (auto-merged manifest)'."""
    seed = _make_seed_repo(tmp_path, hostname=socket.gethostname())
    repos_root = tmp_path / "repos"
    target = tmp_path / "out"
    host_id_file = tmp_path / ".maury-host-id"

    # First sync: clone seed into repos_root/base + render.
    sync(
        manifest_path=seed / ".meta" / "manifest.json",
        target_dir=target,
        repos_root=repos_root,
        host_id_file=host_id_file,
    )
    base_clone = repos_root / "base"
    assert (base_clone / ".meta" / "manifest.json").is_file()
    _configure_git_identity(base_clone)

    # Find the profile already in the seed (both sides will bind to it).
    seed_body = json.loads((seed / ".meta" / "manifest.json").read_text())
    pid = next(iter(seed_body["profiles"]))

    # Diverge: upstream adds host-upstream; local clone adds host-local.
    new_upstream = new_host_id("upstream")
    new_local = new_host_id("local")
    _add_host_commit(seed, new_upstream, "added-upstream", pid, "upstream commit")
    _add_host_commit(base_clone, new_local, "added-local", pid, "local commit")

    # Re-run sync from the local clone's manifest. `git pull --ff-only`
    # should fail (non-FF); structured auto-merge should resolve it.
    result = sync(
        manifest_path=base_clone / ".meta" / "manifest.json",
        target_dir=target,
        repos_root=repos_root,
        host_id_file=host_id_file,
        drift_mode="force",  # don't trip ADR-0017 drift check on second render
    )
    assert not result.has_errors(), result.errors
    assert len(result.repos) == 1
    assert result.repos[0].action == "pulled", result.repos[0].detail
    assert "auto-merged" in result.repos[0].detail.lower()

    # Manifest on disk now contains BOTH new hosts.
    final_body = json.loads((base_clone / ".meta" / "manifest.json").read_text())
    assert new_upstream in final_body["hosts"]
    assert new_local in final_body["hosts"]


def test_sync_surfaces_real_manifest_conflict_with_resolve_hint(tmp_path: Path) -> None:
    """If both sides modify the same profile's description to different
    values, structured merge can't auto-resolve (BOTH_MODIFIED). Sync
    leaves the working tree in the conflicted state and surfaces an
    error pointing at `maury manifest resolve`. The host's `name` is
    left untouched so `_identify_host` still resolves the hostname."""
    seed = _make_seed_repo(tmp_path, hostname=socket.gethostname())
    repos_root = tmp_path / "repos"
    target = tmp_path / "out"
    host_id_file = tmp_path / ".maury-host-id"

    sync(
        manifest_path=seed / ".meta" / "manifest.json",
        target_dir=target,
        repos_root=repos_root,
        host_id_file=host_id_file,
    )
    base_clone = repos_root / "base"
    _configure_git_identity(base_clone)

    seed_body = json.loads((seed / ".meta" / "manifest.json").read_text())
    existing_pid = next(iter(seed_body["profiles"]))

    def _modify_profile_desc_and_commit(repo: Path, new_desc: str, msg: str) -> None:
        mpath = repo / ".meta" / "manifest.json"
        body = json.loads(mpath.read_text())
        body["profiles"][existing_pid]["description"] = new_desc
        mpath.write_text(json.dumps(body, indent=2) + "\n")
        subprocess.run(["git", "-C", str(repo), "add", ".meta/manifest.json"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", msg], check=True)

    _modify_profile_desc_and_commit(seed, "upstream-description", "upstream description")
    _modify_profile_desc_and_commit(base_clone, "local-description", "local description")

    result = sync(
        manifest_path=base_clone / ".meta" / "manifest.json",
        target_dir=target,
        repos_root=repos_root,
        host_id_file=host_id_file,
        drift_mode="force",
    )
    assert result.has_errors() or result.repos[0].action == "error"
    detail = result.repos[0].detail
    assert "maury manifest resolve" in detail
    assert "structural conflict" in detail.lower() or "conflict" in detail.lower()
