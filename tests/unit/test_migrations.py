"""Unit tests for `maury.migrations` (the v1→v2 manifest upgrade)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from maury.migrations import MigrationError, upgrade_v1_to_v2

# ---- fixtures -------------------------------------------------------------


def _v1_simple(tmp_path: Path) -> Path:
    """A v1 manifest with one profile and one host, no extends."""
    p = tmp_path / "manifest.json"
    p.write_text(
        json.dumps(
            {
                "version": 1,
                "profiles": {
                    "home": {"extends": None},
                },
                "hosts": {
                    "workstation": {
                        "profile": "home",
                        "repos": {"base": {"url": "git@x:o/r.git", "mode": "rw"}},
                    }
                },
            }
        )
    )
    return p


def _v1_inheritance(tmp_path: Path) -> Path:
    """A v1 manifest where one profile extends another."""
    p = tmp_path / "manifest.json"
    p.write_text(
        json.dumps(
            {
                "version": 1,
                "profiles": {
                    "base": {"extends": None},
                    "work": {"extends": "base"},
                    "work-acme": {"extends": "work"},
                },
                "hosts": {
                    "alice-laptop": {
                        "profile": "work-acme",
                        "repos": {"base": {"url": "git@x:o/r.git", "mode": "rw"}},
                    }
                },
            }
        )
    )
    return p


# ---- happy path -----------------------------------------------------------


def test_upgrade_v1_to_v2_changes_version_and_keys(tmp_path: Path) -> None:
    p = _v1_simple(tmp_path)
    result = upgrade_v1_to_v2(in_path=p)
    body = json.loads(p.read_text())
    assert body["version"] == 2
    # Profile is now keyed by surrogate id
    profile_keys = list(body["profiles"].keys())
    assert len(profile_keys) == 1
    assert profile_keys[0].startswith("profile_")
    assert body["profiles"][profile_keys[0]]["name"] == "home"
    # Host is keyed by surrogate id, embedding name
    host_keys = list(body["hosts"].keys())
    assert len(host_keys) == 1
    assert host_keys[0].startswith("host_")
    assert body["hosts"][host_keys[0]]["name"] == "workstation"
    # Host's profile reference is rewritten to the new id
    assert body["hosts"][host_keys[0]]["profile"] == profile_keys[0]
    # Mapping captures original names → new ids
    assert result.mapping.profile_name_to_id == {"home": profile_keys[0]}
    assert result.mapping.host_name_to_id == {"workstation": host_keys[0]}


def test_upgrade_v1_to_v2_rewrites_extends_chain(tmp_path: Path) -> None:
    p = _v1_inheritance(tmp_path)
    result = upgrade_v1_to_v2(in_path=p)
    body = json.loads(p.read_text())
    base_pid = result.mapping.profile_name_to_id["base"]
    work_pid = result.mapping.profile_name_to_id["work"]
    work_acme_pid = result.mapping.profile_name_to_id["work-acme"]
    assert body["profiles"][base_pid]["extends"] is None
    assert body["profiles"][work_pid]["extends"] == base_pid
    assert body["profiles"][work_acme_pid]["extends"] == work_pid
    # Host points at the deepest profile's new id
    host_keys = list(body["hosts"].keys())
    assert body["hosts"][host_keys[0]]["profile"] == work_acme_pid


def test_upgrade_v1_to_v2_out_path_does_not_clobber_input(tmp_path: Path) -> None:
    p = _v1_simple(tmp_path)
    out = tmp_path / "upgraded.json"
    upgrade_v1_to_v2(in_path=p, out_path=out)
    # Input untouched; output is v2.
    assert json.loads(p.read_text())["version"] == 1
    assert json.loads(out.read_text())["version"] == 2


def test_upgrade_v1_to_v2_dry_run_does_not_write(tmp_path: Path) -> None:
    p = _v1_simple(tmp_path)
    before = p.read_text()
    result = upgrade_v1_to_v2(in_path=p, dry_run=True)
    assert result.dry_run is True
    assert p.read_text() == before
    # But mapping is still computed.
    assert "workstation" in result.mapping.host_name_to_id


def test_upgrade_v1_to_v2_handles_missing_optional_dicts(tmp_path: Path) -> None:
    """A v1 manifest with no hosts (or no profiles) should still upgrade."""
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps({"version": 1, "profiles": {"home": {"extends": None}}}))
    upgrade_v1_to_v2(in_path=p)
    body = json.loads(p.read_text())
    assert body["version"] == 2
    assert body["hosts"] == {}


# ---- error paths ----------------------------------------------------------


def test_upgrade_v1_to_v2_refuses_v2_input(tmp_path: Path) -> None:
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps({"version": 2, "profiles": {}, "hosts": {}}))
    with pytest.raises(MigrationError, match="already version 2"):
        upgrade_v1_to_v2(in_path=p)


def test_upgrade_v1_to_v2_refuses_unknown_version(tmp_path: Path) -> None:
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps({"version": 99, "profiles": {}, "hosts": {}}))
    with pytest.raises(MigrationError, match="unknown manifest version"):
        upgrade_v1_to_v2(in_path=p)


def test_upgrade_v1_to_v2_treats_missing_version_as_v1(tmp_path: Path) -> None:
    """Early manifests may have lacked an explicit version field."""
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps({"profiles": {"home": {"extends": None}}}))
    upgrade_v1_to_v2(in_path=p)
    assert json.loads(p.read_text())["version"] == 2


def test_upgrade_v1_to_v2_refuses_missing_file(tmp_path: Path) -> None:
    with pytest.raises(MigrationError, match="manifest not found"):
        upgrade_v1_to_v2(in_path=tmp_path / "missing.json")


def test_upgrade_v1_to_v2_refuses_malformed_json(tmp_path: Path) -> None:
    p = tmp_path / "manifest.json"
    p.write_text("{ not valid")
    with pytest.raises(MigrationError, match="invalid JSON"):
        upgrade_v1_to_v2(in_path=p)


def test_upgrade_v1_to_v2_refuses_non_object_root(tmp_path: Path) -> None:
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps(["not", "an", "object"]))
    with pytest.raises(MigrationError, match="top-level must be a JSON object"):
        upgrade_v1_to_v2(in_path=p)


def test_upgrade_v1_to_v2_refuses_extends_pointing_at_unknown(tmp_path: Path) -> None:
    p = tmp_path / "manifest.json"
    p.write_text(
        json.dumps(
            {
                "version": 1,
                "profiles": {"work": {"extends": "does-not-exist"}},
                "hosts": {},
            }
        )
    )
    with pytest.raises(MigrationError, match="extends 'does-not-exist'"):
        upgrade_v1_to_v2(in_path=p)


def test_upgrade_v1_to_v2_refuses_host_pointing_at_unknown_profile(tmp_path: Path) -> None:
    p = tmp_path / "manifest.json"
    p.write_text(
        json.dumps(
            {
                "version": 1,
                "profiles": {"home": {"extends": None}},
                "hosts": {"ws": {"profile": "ghost", "repos": {}}},
            }
        )
    )
    with pytest.raises(MigrationError, match="profile 'ghost'"):
        upgrade_v1_to_v2(in_path=p)


def test_upgrade_v1_to_v2_refuses_host_missing_profile_field(tmp_path: Path) -> None:
    p = tmp_path / "manifest.json"
    p.write_text(
        json.dumps(
            {
                "version": 1,
                "profiles": {"home": {"extends": None}},
                "hosts": {"ws": {"repos": {}}},
            }
        )
    )
    with pytest.raises(MigrationError, match="missing 'profile' field"):
        upgrade_v1_to_v2(in_path=p)


# ---- ID format check -----------------------------------------------------


def test_upgrade_v1_to_v2_generates_unique_ids(tmp_path: Path) -> None:
    p = tmp_path / "manifest.json"
    p.write_text(
        json.dumps(
            {
                "version": 1,
                "profiles": {"a": {"extends": None}, "b": {"extends": None}},
                "hosts": {
                    "h1": {"profile": "a", "repos": {}},
                    "h2": {"profile": "b", "repos": {}},
                },
            }
        )
    )
    result = upgrade_v1_to_v2(in_path=p)
    profile_ids = set(result.mapping.profile_name_to_id.values())
    host_ids = set(result.mapping.host_name_to_id.values())
    assert len(profile_ids) == 2
    assert len(host_ids) == 2
    assert profile_ids.isdisjoint(host_ids)
