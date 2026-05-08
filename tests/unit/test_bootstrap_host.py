"""Tests for `maury bootstrap host` (Phase 4 curator-side ops, per ADR-0018)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from maury.bootstrap import BootstrapHostError, bootstrap_host
from maury.ids import is_host_id, new_host_id, new_profile_id
from maury.manifest import PushPolicy, RepoMode, load_manifest

# ---- helpers ------------------------------------------------------------


def _make_manifest(tmp_path: Path, *, with_existing_host: bool = True) -> Path:
    """Write a minimal manifest at tmp_path/manifest.json. By default it has
    one already-registered host so `bootstrap host` can infer the base URL."""
    pid = new_profile_id()
    payload: dict = {
        "version": 2,
        "profiles": {pid: {"name": "home", "extends": None}},
        "hosts": {},
    }
    if with_existing_host:
        existing_hid = new_host_id()
        payload["hosts"][existing_hid] = {
            "name": "old-laptop",
            "profile": pid,
            "repos": {
                "base": {"url": "git@example:org/clyde-config.git", "mode": "rw"},
            },
        }
    mpath = tmp_path / "manifest.json"
    mpath.write_text(json.dumps(payload, indent=2))
    return mpath


# ---- error inputs -------------------------------------------------------


def test_bootstrap_host_missing_manifest_raises(tmp_path: Path) -> None:
    with pytest.raises(BootstrapHostError) as ei:
        bootstrap_host(
            manifest_path=tmp_path / "nope.json",
            name="x",
            profile="home",
        )
    assert "manifest not found" in str(ei.value)


def test_bootstrap_host_empty_name_raises(tmp_path: Path) -> None:
    mpath = _make_manifest(tmp_path)
    with pytest.raises(BootstrapHostError) as ei:
        bootstrap_host(manifest_path=mpath, name="   ", profile="home")
    assert "name" in str(ei.value).lower()


def test_bootstrap_host_unknown_profile_raises(tmp_path: Path) -> None:
    mpath = _make_manifest(tmp_path)
    with pytest.raises(BootstrapHostError) as ei:
        bootstrap_host(manifest_path=mpath, name="newhost", profile="ghost-profile")
    assert "ghost-profile" in str(ei.value)
    assert "home" in str(ei.value)  # error mentions known profiles


def test_bootstrap_host_duplicate_name_raises(tmp_path: Path) -> None:
    mpath = _make_manifest(tmp_path)
    with pytest.raises(BootstrapHostError) as ei:
        bootstrap_host(manifest_path=mpath, name="old-laptop", profile="home")
    assert "already taken" in str(ei.value)


def test_bootstrap_host_no_base_url_inferable_raises(tmp_path: Path) -> None:
    """First-host case: nothing to infer from, --base-url is required."""
    mpath = _make_manifest(tmp_path, with_existing_host=False)
    with pytest.raises(BootstrapHostError) as ei:
        bootstrap_host(manifest_path=mpath, name="firsthost", profile="home")
    assert "base_url" in str(ei.value)


# ---- happy path --------------------------------------------------------


def test_bootstrap_host_happy_path_writes_manifest(tmp_path: Path) -> None:
    mpath = _make_manifest(tmp_path)
    result = bootstrap_host(manifest_path=mpath, name="newlaptop", profile="home")
    assert result.name == "newlaptop"
    assert is_host_id(result.host_id)
    # Manifest now has both hosts
    after = load_manifest(mpath)
    assert len(after.hosts) == 2
    assert after.hosts[result.host_id].name == "newlaptop"
    assert after.hosts[result.host_id].repos["base"].url == "git@example:org/clyde-config.git"
    assert after.hosts[result.host_id].repos["base"].mode == RepoMode.RO


def test_bootstrap_host_dry_run_writes_nothing(tmp_path: Path) -> None:
    mpath = _make_manifest(tmp_path)
    before = mpath.read_text()
    result = bootstrap_host(
        manifest_path=mpath,
        name="newlaptop",
        profile="home",
        dry_run=True,
    )
    assert result.name == "newlaptop"
    assert is_host_id(result.host_id)
    assert mpath.read_text() == before


def test_bootstrap_host_explicit_base_url_overrides_inference(tmp_path: Path) -> None:
    mpath = _make_manifest(tmp_path)
    explicit = "git@example:org/different.git"
    result = bootstrap_host(
        manifest_path=mpath,
        name="newlaptop",
        profile="home",
        base_url=explicit,
    )
    after = load_manifest(mpath)
    assert after.hosts[result.host_id].repos["base"].url == explicit


def test_bootstrap_host_base_mode_rw(tmp_path: Path) -> None:
    mpath = _make_manifest(tmp_path)
    result = bootstrap_host(
        manifest_path=mpath,
        name="newlaptop",
        profile="home",
        base_mode=RepoMode.RW,
    )
    after = load_manifest(mpath)
    assert after.hosts[result.host_id].repos["base"].mode == RepoMode.RW


def test_bootstrap_host_push_policy_and_owner_persisted(tmp_path: Path) -> None:
    mpath = _make_manifest(tmp_path)
    result = bootstrap_host(
        manifest_path=mpath,
        name="newlaptop",
        profile="home",
        push_policy=PushPolicy.PERMISSIVE,
        owner="alice@example.com",
    )
    after = load_manifest(mpath)
    spec = after.hosts[result.host_id]
    assert spec.push_policy == PushPolicy.PERMISSIVE
    assert spec.owner == "alice@example.com"
    assert spec.added is not None  # ISO date set


def test_bootstrap_host_resolves_profile_by_id(tmp_path: Path) -> None:
    """The `profile` arg accepts either name or ID."""
    mpath = _make_manifest(tmp_path)
    m = load_manifest(mpath)
    pid = next(iter(m.profiles))
    result = bootstrap_host(manifest_path=mpath, name="newlaptop", profile=pid)
    assert result.profile_id == pid


def test_bootstrap_host_first_host_with_explicit_base_url(tmp_path: Path) -> None:
    """First-host case works if --base-url is provided."""
    mpath = _make_manifest(tmp_path, with_existing_host=False)
    result = bootstrap_host(
        manifest_path=mpath,
        name="firsthost",
        profile="home",
        base_url="git@example:org/seed.git",
    )
    after = load_manifest(mpath)
    assert len(after.hosts) == 1
    assert after.hosts[result.host_id].repos["base"].url == "git@example:org/seed.git"


# ---- CLI wiring smoke test ---------------------------------------------


def test_bootstrap_host_cli_smoke(tmp_path: Path) -> None:
    """Invoke the click command end-to-end to confirm the CLI plumbing works."""
    from click.testing import CliRunner

    from maury.cli import main

    mpath = _make_manifest(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "bootstrap",
            "host",
            "--manifest-file",
            str(mpath),
            "--name",
            "newlaptop",
            "--profile",
            "home",
        ],
    )
    assert result.exit_code == 0, result.output
    after = load_manifest(mpath)
    assert any(s.name == "newlaptop" for s in after.hosts.values())
