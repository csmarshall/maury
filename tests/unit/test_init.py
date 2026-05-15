"""Tests for `maury init` (Phase 4 first slice)."""

from __future__ import annotations

import json
import tarfile
from pathlib import Path

import pytest

from maury.bootstrap import InitError, init
from maury.ids import new_host_id, new_profile_id

# ---- helpers ------------------------------------------------------------


def _make_minimal_repo(
    tmp_path: Path,
    *,
    hostname: str = "synthetic-host",
    host_id_file: Path | None = None,
) -> Path:
    """Create a minimal valid maury repo at tmp_path/repo.

    If `host_id_file` is provided, pre-writes the manifest's host_id
    to it so `init()` can self-identify against the manifest. Per
    ADR-0039 step 8 (post-2026-05-14), init no longer falls back to
    hostname matching — the host's identity comes only from the local
    `~/.maury-host-id` file. Tests that want the "host is registered"
    precondition must pre-write the file with the manifest's hid.
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
                "repos": {
                    "base": {"url": "git@x:o/r.git", "mode": "rw"},
                },
            }
        },
    }
    (repo / ".meta").mkdir()
    (repo / ".meta" / "manifest.json").write_text(json.dumps(manifest, indent=2))
    if host_id_file is not None:
        host_id_file.parent.mkdir(parents=True, exist_ok=True)
        host_id_file.write_text(hid + "\n")
    return repo


# ---- error inputs -------------------------------------------------------


def test_init_requires_exactly_one_source(tmp_path: Path) -> None:
    with pytest.raises(InitError) as ei:
        init(source_dir=None, source_tarball=None, target_dir=tmp_path / "out")
    assert "exactly one" in str(ei.value).lower()


def test_init_rejects_both_sources(tmp_path: Path) -> None:
    with pytest.raises(InitError):
        init(
            source_dir=tmp_path,
            source_tarball=tmp_path / "x.tar.gz",
            target_dir=tmp_path / "out",
        )


def test_init_missing_source_dir_raises(tmp_path: Path) -> None:
    with pytest.raises(InitError) as ei:
        init(source_dir=tmp_path / "nonexistent", target_dir=tmp_path / "out")
    assert "not found" in str(ei.value).lower()


def test_init_missing_manifest_raises(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "CLAUDE.md").write_text("nope\n")
    with pytest.raises(InitError) as ei:
        init(source_dir=repo, target_dir=tmp_path / "out")
    assert "manifest not found" in str(ei.value).lower()


# ---- happy paths -------------------------------------------------------


def test_init_writes_host_id_file_when_missing(tmp_path: Path) -> None:
    """Fresh init generates and writes a host_id. The new ID will not be
    in the manifest (curator hasn't added it yet), so host_registered=False
    is expected — that's the host-bootstrap flow from ADR-0039 step 8."""
    repo = _make_minimal_repo(tmp_path)
    host_id_file = tmp_path / ".maury-host-id"
    result = init(
        source_dir=repo,
        target_dir=tmp_path / "out",
        host_id_file=host_id_file,
    )
    assert host_id_file.is_file()
    assert host_id_file.read_text().strip().startswith("host_")
    assert result.host_id_created is True
    # Fresh ID isn't in the manifest yet → not registered.
    assert result.host_registered is False
    assert "not yet registered" in result.message


def test_init_reuses_existing_host_id_file(tmp_path: Path) -> None:
    repo = _make_minimal_repo(tmp_path)
    host_id_file = tmp_path / ".maury-host-id"
    existing = "host_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    host_id_file.write_text(existing + "\n")
    result = init(source_dir=repo, target_dir=tmp_path / "out", host_id_file=host_id_file)
    assert host_id_file.read_text().strip() == existing
    assert result.host_id_created is False


def test_init_renders_to_target_when_host_id_matches_manifest(tmp_path: Path) -> None:
    """Per ADR-0039 step 8: when ~/.maury-host-id contains a UUID present
    in the manifest, render proceeds. The fixture pre-writes the file with
    the manifest's hid to set up that precondition."""
    target = tmp_path / "out"
    host_id_file = tmp_path / ".maury-host-id"
    repo = _make_minimal_repo(tmp_path, host_id_file=host_id_file)
    result = init(source_dir=repo, target_dir=target, host_id_file=host_id_file)
    assert result.host_registered is True
    assert result.rendered is True
    assert (target / "CLAUDE.md").is_file()


def test_init_dry_run_writes_nothing(tmp_path: Path) -> None:
    target = tmp_path / "out"
    host_id_file = tmp_path / ".maury-host-id"
    repo = _make_minimal_repo(tmp_path, host_id_file=host_id_file)
    result = init(
        source_dir=repo,
        target_dir=target,
        host_id_file=host_id_file,
        dry_run=True,
    )
    assert result.host_registered is True
    assert not (target / "CLAUDE.md").exists()


# ---- unregistered host (returns clear message; doesn't raise) ----------


def test_init_unregistered_host_returns_friendly_message(tmp_path: Path) -> None:
    """Per ADR-0039 step 8 (host-bootstrap flow): when the locally-generated
    UUID isn't in the manifest, init returns host_registered=False with a
    structured 'add this entry' message — never silently falls back to
    hostname matching."""
    repo = _make_minimal_repo(tmp_path)
    target = tmp_path / "out"
    host_id_file = tmp_path / ".maury-host-id"
    result = init(source_dir=repo, target_dir=target, host_id_file=host_id_file)
    assert result.host_registered is False
    assert result.rendered is False
    assert "not yet registered" in result.message
    # Message includes the locally-generated host id verbatim so the user
    # can copy-paste it into their manifest entry.
    new_hid = host_id_file.read_text().strip()
    assert new_hid in result.message
    assert "Add the following entry" in result.message
    assert not (target / "CLAUDE.md").exists()


# ---- tarball ingestion -------------------------------------------------


def test_init_from_tarball_extracts_and_renders(tmp_path: Path) -> None:
    repo = _make_minimal_repo(tmp_path, host_id_file=tmp_path / ".maury-host-id")
    # Pack the repo into a tarball with a single top-level dir
    tarball = tmp_path / "maury-base.tar.gz"
    with tarfile.open(tarball, "w:gz") as tar:
        tar.add(repo, arcname="maury-base")

    target = tmp_path / "out"
    host_id_file = tmp_path / ".maury-host-id"
    result = init(source_tarball=tarball, target_dir=target, host_id_file=host_id_file)
    assert result.host_registered is True
    assert (target / "CLAUDE.md").is_file()


def test_init_tarball_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(InitError) as ei:
        init(source_tarball=tmp_path / "nope.tar.gz", target_dir=tmp_path / "out")
    assert "not found" in str(ei.value).lower()


# ---- drift preflight (per ADR-0017 + Tenet 1) --------------------------


def test_init_unknown_drift_mode_raises(tmp_path: Path) -> None:
    with pytest.raises(InitError) as ei:
        init(
            source_dir=tmp_path / "ignored",
            target_dir=tmp_path / "out",
            drift_mode="bogus",
        )
    assert "drift_mode" in str(ei.value)


def test_init_clean_target_writes_last_render(tmp_path: Path) -> None:
    """A successful init persists last-render.json so future syncs can detect drift."""
    repo = _make_minimal_repo(tmp_path, host_id_file=tmp_path / ".maury-host-id")
    target = tmp_path / "out"
    host_id_file = tmp_path / ".maury-host-id"
    result = init(source_dir=repo, target_dir=target, host_id_file=host_id_file)
    assert result.rendered is True
    assert result.drift_action == "none"
    last_render_path = target / "maury-state" / "last-render.json"
    assert last_render_path.is_file()
    content = json.loads(last_render_path.read_text())
    assert content["schema_version"] == 1
    assert any(f["path"] == "CLAUDE.md" for f in content["files"])


def test_init_dry_run_does_not_write_last_render(tmp_path: Path) -> None:
    """--check leaves no maury-state behind."""
    repo = _make_minimal_repo(tmp_path, host_id_file=tmp_path / ".maury-host-id")
    target = tmp_path / "out"
    host_id_file = tmp_path / ".maury-host-id"
    init(
        source_dir=repo,
        target_dir=target,
        host_id_file=host_id_file,
        dry_run=True,
    )
    assert not (target / "maury-state").exists()


def test_init_default_mode_refuses_pre_existing_collision(tmp_path: Path) -> None:
    """Bootstrap case: target dir already has a CLAUDE.md → refuse by default."""
    repo = _make_minimal_repo(tmp_path, host_id_file=tmp_path / ".maury-host-id")
    target = tmp_path / "out"
    target.mkdir()
    (target / "CLAUDE.md").write_text("user's pre-existing notes\n")
    host_id_file = tmp_path / ".maury-host-id"
    result = init(source_dir=repo, target_dir=target, host_id_file=host_id_file)
    assert result.rendered is False
    assert result.drift_action == "refused"
    assert result.has_errors()
    assert any("pre-existing" in e for e in result.errors)
    # Pre-existing content preserved
    assert (target / "CLAUDE.md").read_text() == "user's pre-existing notes\n"
    # No baseline written when we refused
    assert not (target / "maury-state").exists()


def test_init_force_mode_overwrites_pre_existing_collision(tmp_path: Path) -> None:
    """--force clobbers pre-existing content with a loud warning."""
    repo = _make_minimal_repo(tmp_path, host_id_file=tmp_path / ".maury-host-id")
    target = tmp_path / "out"
    target.mkdir()
    (target / "CLAUDE.md").write_text("hand-edited\n")
    host_id_file = tmp_path / ".maury-host-id"
    result = init(
        source_dir=repo,
        target_dir=target,
        host_id_file=host_id_file,
        drift_mode="force",
    )
    assert result.rendered is True
    assert result.drift_action == "forced"
    assert not result.has_errors()
    assert any("--force" in a and "overwriting" in a for a in result.actions)
    # Hand-edit gone; rendered content present
    assert "hand-edited" not in (target / "CLAUDE.md").read_text()
    # Baseline was written so next sync can detect future drift
    assert (target / "maury-state" / "last-render.json").is_file()


def test_init_non_interactive_mode_refuses_pre_existing_collision(tmp_path: Path) -> None:
    """--non-interactive refuses with a cron/CI-friendly error."""
    repo = _make_minimal_repo(tmp_path, host_id_file=tmp_path / ".maury-host-id")
    target = tmp_path / "out"
    target.mkdir()
    (target / "CLAUDE.md").write_text("pre-existing\n")
    host_id_file = tmp_path / ".maury-host-id"
    result = init(
        source_dir=repo,
        target_dir=target,
        host_id_file=host_id_file,
        drift_mode="non-interactive",
    )
    assert result.rendered is False
    assert result.drift_action == "refused"
    assert result.has_errors()
    assert any("--non-interactive" in e for e in result.errors)


def test_init_byte_identical_pre_existing_is_not_a_collision(tmp_path: Path) -> None:
    """If pre-existing files match what we'd render byte-for-byte, no collision.

    Strategy: render once into a throwaway target, copy that output into
    a fresh target dir without the maury-state baseline, then run init
    again. The bootstrap branch should see no collisions.
    """
    repo = _make_minimal_repo(tmp_path, host_id_file=tmp_path / ".maury-host-id")
    # First render produces the canonical content.
    seed_target = tmp_path / "seed-out"
    init(
        source_dir=repo,
        target_dir=seed_target,
        host_id_file=tmp_path / ".host-id-seed",
    )
    # Copy rendered files (NOT maury-state) into a fresh target, so init
    # will treat it as bootstrap-with-pre-existing-content.
    target = tmp_path / "out"
    target.mkdir()
    for src in seed_target.rglob("*"):
        if not src.is_file():
            continue
        rel = src.relative_to(seed_target)
        if rel.parts[0] == "maury-state":
            continue
        dst = target / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())

    host_id_file = tmp_path / ".maury-host-id"
    result = init(source_dir=repo, target_dir=target, host_id_file=host_id_file)
    assert result.rendered is True, result.errors
    assert result.drift_action == "none"
    assert not result.has_errors()


def test_init_re_init_clean_proceeds(tmp_path: Path) -> None:
    """Second init after a clean first init: no drift, proceeds normally."""
    repo = _make_minimal_repo(tmp_path, host_id_file=tmp_path / ".maury-host-id")
    target = tmp_path / "out"
    host_id_file = tmp_path / ".maury-host-id"
    init(source_dir=repo, target_dir=target, host_id_file=host_id_file)
    # Re-run; nothing changed on disk
    result = init(source_dir=repo, target_dir=target, host_id_file=host_id_file)
    assert result.rendered is True
    assert result.drift_action == "none"


def test_init_re_init_default_refuses_on_drift(tmp_path: Path) -> None:
    """Second init after user hand-edited a maury-rendered file: refuse."""
    repo = _make_minimal_repo(tmp_path, host_id_file=tmp_path / ".maury-host-id")
    target = tmp_path / "out"
    host_id_file = tmp_path / ".maury-host-id"
    init(source_dir=repo, target_dir=target, host_id_file=host_id_file)
    # User hand-edits the rendered file.
    (target / "CLAUDE.md").write_text("hand-edited after init\n")
    result = init(source_dir=repo, target_dir=target, host_id_file=host_id_file)
    assert result.rendered is False
    assert result.drift_action == "refused"
    assert result.has_errors()
    assert any("drift detected" in e for e in result.errors)
    # Hand-edit preserved
    assert (target / "CLAUDE.md").read_text() == "hand-edited after init\n"


def test_init_re_init_force_clobbers_drift(tmp_path: Path) -> None:
    """--force on re-init drift: clobber with warning, baseline updated."""
    repo = _make_minimal_repo(tmp_path, host_id_file=tmp_path / ".maury-host-id")
    target = tmp_path / "out"
    host_id_file = tmp_path / ".maury-host-id"
    init(source_dir=repo, target_dir=target, host_id_file=host_id_file)
    (target / "CLAUDE.md").write_text("drift\n")
    result = init(
        source_dir=repo,
        target_dir=target,
        host_id_file=host_id_file,
        drift_mode="force",
    )
    assert result.rendered is True
    assert result.drift_action == "forced"
    assert "drift" not in (target / "CLAUDE.md").read_text()
    assert any("--force" in a and "clobbering" in a for a in result.actions)


def test_init_dry_run_still_reports_collision(tmp_path: Path) -> None:
    """--check + pre-existing content: report the would-be refusal, write nothing."""
    repo = _make_minimal_repo(tmp_path, host_id_file=tmp_path / ".maury-host-id")
    target = tmp_path / "out"
    target.mkdir()
    (target / "CLAUDE.md").write_text("pre-existing\n")
    host_id_file = tmp_path / ".maury-host-id"
    result = init(
        source_dir=repo,
        target_dir=target,
        host_id_file=host_id_file,
        dry_run=True,
    )
    assert result.drift_action == "refused"
    assert result.has_errors()
    # Original content untouched even though we evaluated drift.
    assert (target / "CLAUDE.md").read_text() == "pre-existing\n"
