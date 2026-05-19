"""Unit tests for `maury.bootstrap.deregister`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from maury.bootstrap.deregister import DeregisterError, deregister_host
from maury.ids import new_host_id, new_profile_id


def _write_manifest(path: Path, *, with_host_name: str = "workstation") -> tuple[str, str, str]:
    pid = new_profile_id()
    hid = new_host_id("h")
    other_hid = new_host_id("h")
    body: dict[str, object] = {
        "version": 1,
        "profiles": {pid: {"name": "home", "extends": None}},
        "hosts": {
            hid: {
                "name": with_host_name,
                "profile": pid,
                "repos": {"base": {"url": "git@x:o/r.git", "mode": "rw"}},
            },
            other_hid: {
                "name": "another-host",
                "profile": pid,
                "repos": {"base": {"url": "git@x:o/r.git", "mode": "ro"}},
            },
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body))
    return pid, hid, other_hid


def test_deregister_by_name_removes_host(tmp_path: Path) -> None:
    mpath = tmp_path / "manifest.json"
    pid, target_hid, other_hid = _write_manifest(mpath)
    result = deregister_host(manifest_path=mpath, host="workstation")
    assert result.host_id == target_hid
    assert result.name == "workstation"
    assert result.profile_id == pid
    assert result.profile_name == "home"
    assert result.dry_run is False

    body = json.loads(mpath.read_text())
    assert target_hid not in body["hosts"]
    assert other_hid in body["hosts"]  # other host preserved


def test_deregister_by_id_removes_host(tmp_path: Path) -> None:
    mpath = tmp_path / "manifest.json"
    _pid, target_hid, _other = _write_manifest(mpath)
    result = deregister_host(manifest_path=mpath, host=target_hid)
    assert result.host_id == target_hid
    body = json.loads(mpath.read_text())
    assert target_hid not in body["hosts"]


def test_deregister_dry_run_does_not_write(tmp_path: Path) -> None:
    mpath = tmp_path / "manifest.json"
    _pid, target_hid, _other = _write_manifest(mpath)
    before = mpath.read_text()
    result = deregister_host(manifest_path=mpath, host="workstation", dry_run=True)
    assert result.dry_run is True
    assert mpath.read_text() == before
    assert target_hid in json.loads(mpath.read_text())["hosts"]


def test_deregister_unknown_host_raises_with_known_list(tmp_path: Path) -> None:
    mpath = tmp_path / "manifest.json"
    _write_manifest(mpath)
    with pytest.raises(DeregisterError, match="not found in manifest"):
        deregister_host(manifest_path=mpath, host="does-not-exist")


def test_deregister_empty_host_raises(tmp_path: Path) -> None:
    mpath = tmp_path / "manifest.json"
    _write_manifest(mpath)
    with pytest.raises(DeregisterError, match="host must be non-empty"):
        deregister_host(manifest_path=mpath, host="   ")


def test_deregister_missing_manifest_raises(tmp_path: Path) -> None:
    with pytest.raises(DeregisterError, match="manifest not found"):
        deregister_host(manifest_path=tmp_path / "missing.json", host="x")


def test_deregister_preserves_profiles(tmp_path: Path) -> None:
    """Profile entries are untouched even when the only host of a profile is removed."""
    mpath = tmp_path / "manifest.json"
    pid, _hid, _other = _write_manifest(mpath)
    deregister_host(manifest_path=mpath, host="workstation")
    body = json.loads(mpath.read_text())
    assert pid in body["profiles"]
    assert body["profiles"][pid]["name"] == "home"


def test_deregister_actions_record_intent(tmp_path: Path) -> None:
    mpath = tmp_path / "manifest.json"
    _write_manifest(mpath)
    result = deregister_host(manifest_path=mpath, host="workstation")
    actions_text = "\n".join(result.actions)
    assert "will deregister host 'workstation'" in actions_text
    assert "wrote" in actions_text
