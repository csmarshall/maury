"""Tests for `maury init` (Phase 4 first slice)."""

from __future__ import annotations

import json
import socket
import tarfile
from pathlib import Path

import pytest

from maury.bootstrap import InitError, init
from maury.ids import new_host_id, new_profile_id

# ---- helpers ------------------------------------------------------------


def _make_minimal_repo(tmp_path: Path, *, hostname: str = "synthetic-host") -> Path:
    """Create a minimal valid maury repo at tmp_path/repo."""
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
    return repo


# ---- error inputs -------------------------------------------------------


def test_init_requires_exactly_one_source(tmp_path):
    with pytest.raises(InitError) as ei:
        init(source_dir=None, source_tarball=None, target_dir=tmp_path / "out")
    assert "exactly one" in str(ei.value).lower()


def test_init_rejects_both_sources(tmp_path):
    with pytest.raises(InitError):
        init(
            source_dir=tmp_path,
            source_tarball=tmp_path / "x.tar.gz",
            target_dir=tmp_path / "out",
        )


def test_init_missing_source_dir_raises(tmp_path):
    with pytest.raises(InitError) as ei:
        init(source_dir=tmp_path / "nonexistent", target_dir=tmp_path / "out")
    assert "not found" in str(ei.value).lower()


def test_init_missing_manifest_raises(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "CLAUDE.md").write_text("nope\n")
    with pytest.raises(InitError) as ei:
        init(source_dir=repo, target_dir=tmp_path / "out")
    assert "manifest not found" in str(ei.value).lower()


# ---- happy paths -------------------------------------------------------


def test_init_writes_host_id_file_when_missing(tmp_path):
    repo = _make_minimal_repo(tmp_path, hostname=socket.gethostname())
    host_id_file = tmp_path / ".maury-host-id"
    result = init(
        source_dir=repo,
        target_dir=tmp_path / "out",
        host_id_file=host_id_file,
    )
    assert host_id_file.is_file()
    assert host_id_file.read_text().strip().startswith("host_")
    assert result.host_id_created is True


def test_init_reuses_existing_host_id_file(tmp_path):
    repo = _make_minimal_repo(tmp_path, hostname=socket.gethostname())
    host_id_file = tmp_path / ".maury-host-id"
    existing = "host_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    host_id_file.write_text(existing + "\n")
    result = init(source_dir=repo, target_dir=tmp_path / "out", host_id_file=host_id_file)
    assert host_id_file.read_text().strip() == existing
    assert result.host_id_created is False


def test_init_renders_to_target_when_host_resolved_by_hostname(tmp_path):
    """Manifest hostname matches socket.gethostname() -> render proceeds."""
    repo = _make_minimal_repo(tmp_path, hostname=socket.gethostname())
    target = tmp_path / "out"
    host_id_file = tmp_path / ".maury-host-id"
    result = init(source_dir=repo, target_dir=target, host_id_file=host_id_file)
    assert result.host_registered is True
    assert result.rendered is True
    assert (target / "CLAUDE.md").is_file()


def test_init_dry_run_writes_nothing(tmp_path):
    repo = _make_minimal_repo(tmp_path, hostname=socket.gethostname())
    target = tmp_path / "out"
    host_id_file = tmp_path / ".maury-host-id"
    result = init(
        source_dir=repo,
        target_dir=target,
        host_id_file=host_id_file,
        dry_run=True,
    )
    assert result.host_registered is True
    assert not (target / "CLAUDE.md").exists()
    # host id file also not created in dry-run
    assert not host_id_file.is_file()


# ---- unregistered host (returns clear message; doesn't raise) ----------


def test_init_unregistered_host_returns_friendly_message(tmp_path):
    """Hostname not in manifest -> result.host_registered=False with guidance."""
    repo = _make_minimal_repo(tmp_path, hostname="some-other-host")
    target = tmp_path / "out"
    host_id_file = tmp_path / ".maury-host-id"
    result = init(source_dir=repo, target_dir=target, host_id_file=host_id_file)
    assert result.host_registered is False
    assert result.rendered is False
    assert "not registered" in result.message
    assert "some-other-host" in result.message
    assert not (target / "CLAUDE.md").exists()


# ---- tarball ingestion -------------------------------------------------


def test_init_from_tarball_extracts_and_renders(tmp_path):
    repo = _make_minimal_repo(tmp_path, hostname=socket.gethostname())
    # Pack the repo into a tarball with a single top-level dir
    tarball = tmp_path / "maury-base.tar.gz"
    with tarfile.open(tarball, "w:gz") as tar:
        tar.add(repo, arcname="maury-base")

    target = tmp_path / "out"
    host_id_file = tmp_path / ".maury-host-id"
    result = init(source_tarball=tarball, target_dir=target, host_id_file=host_id_file)
    assert result.host_registered is True
    assert (target / "CLAUDE.md").is_file()


def test_init_tarball_missing_raises(tmp_path):
    with pytest.raises(InitError) as ei:
        init(source_tarball=tmp_path / "nope.tar.gz", target_dir=tmp_path / "out")
    assert "not found" in str(ei.value).lower()
