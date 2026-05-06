"""Tests for the manifest module (v2 schema with surrogate keys)."""

from __future__ import annotations

import pytest

from maury.ids import is_host_id, is_profile_id, new_host_id, new_profile_id
from maury.manifest import (
    HostSpec,
    Manifest,
    ManifestError,
    ProfileSpec,
    PushPolicy,
    RepoMode,
    RepoSpec,
    dump_manifest,
    known_profile_names_from,
    known_profiles_from,
    load_manifest,
    parse_manifest,
    validate_manifest,
)

# Stable IDs reused across tests so error messages are reproducible.
P_BASE = "profile_00000000000000000000000000000001"
P_HOME = "profile_00000000000000000000000000000002"
P_WORK = "profile_00000000000000000000000000000003"
H_TOAD = "host_00000000000000000000000000000010"
H_WORK = "host_00000000000000000000000000000011"


VALID_JSON = f"""
{{
  "version": 2,
  "profiles": {{
    "{P_BASE}": {{"name": "base", "extends": null, "description": "Universal"}},
    "{P_HOME}": {{"name": "home", "extends": null}},
    "{P_WORK}": {{"name": "work", "extends": null}}
  }},
  "hosts": {{
    "{H_TOAD}": {{
      "name": "toad",
      "profile": "{P_HOME}",
      "lock": false,
      "push_policy": "permissive",
      "repos": {{
        "base":     {{"url": "git@x:o/maury-base.git",     "mode": "rw"}},
        "personal": {{"url": "git@x:o/maury-personal.git", "mode": "rw"}}
      }},
      "owner": "charles@wozi.com",
      "added": "2026-05-06"
    }},
    "{H_WORK}": {{
      "name": "work-laptop",
      "profile": "{P_WORK}",
      "lock": true,
      "push_policy": "disabled",
      "repos": {{
        "base": {{"url": "git@y:o/maury-base.git", "mode": "ro"}},
        "work": {{"url": "git@y:o/maury-work.git", "mode": "rw"}}
      }}
    }}
  }}
}}
"""


# ---- ID generation -----------------------------------------------------


def test_id_generators_produce_recognized_format():
    hid = new_host_id()
    pid = new_profile_id()
    assert is_host_id(hid)
    assert is_profile_id(pid)
    assert not is_host_id(pid)
    assert not is_profile_id(hid)


def test_id_collision_resistance():
    """100 IDs should all be unique."""
    ids = {new_host_id() for _ in range(100)}
    assert len(ids) == 100


# ---- v2 parse ----------------------------------------------------------


def test_parse_valid_v2_manifest():
    m = parse_manifest(VALID_JSON)
    assert m.version == 2
    assert set(m.profiles) == {P_BASE, P_HOME, P_WORK}
    assert set(m.hosts) == {H_TOAD, H_WORK}


def test_parse_extracts_profile_metadata():
    m = parse_manifest(VALID_JSON)
    assert m.profiles[P_BASE].name == "base"
    assert m.profiles[P_BASE].description == "Universal"
    assert m.profiles[P_BASE].extends is None


def test_parse_extracts_host_with_profile_id():
    m = parse_manifest(VALID_JSON)
    toad = m.hosts[H_TOAD]
    assert toad.name == "toad"
    assert toad.profile == P_HOME  # ID, not "home"
    assert toad.push_policy == PushPolicy.PERMISSIVE
    assert toad.lock is False


def test_parse_locked_host():
    m = parse_manifest(VALID_JSON)
    work = m.hosts[H_WORK]
    assert work.name == "work-laptop"
    assert work.lock is True
    assert work.push_policy == PushPolicy.DISABLED


def test_parse_default_repo_backend_is_git():
    m = parse_manifest(VALID_JSON)
    assert m.hosts[H_TOAD].repos["base"].backend == "git"
    assert m.hosts[H_TOAD].repos["base"].backend_config is None


def test_parse_repo_with_backend():
    import json as _json

    payload = {
        "version": 2,
        "profiles": {P_HOME: {"name": "home", "extends": None}},
        "hosts": {
            H_TOAD: {
                "name": "toad",
                "profile": P_HOME,
                "repos": {"base": {"url": "git@x:o/r.git", "mode": "rw", "backend": "github"}},
            }
        },
    }
    m = parse_manifest(_json.dumps(payload))
    assert m.hosts[H_TOAD].repos["base"].backend == "github"


def test_parse_repo_with_backend_config():
    import json as _json

    payload = {
        "version": 2,
        "profiles": {P_WORK: {"name": "work", "extends": None}},
        "hosts": {
            H_WORK: {
                "name": "work-laptop",
                "profile": P_WORK,
                "repos": {
                    "work": {
                        "url": "ssh://p4.example.com:1666",
                        "mode": "rw",
                        "backend": "p4",
                        "backend_config": {"depot": "//maury/work"},
                    }
                },
            }
        },
    }
    m = parse_manifest(_json.dumps(payload))
    assert m.hosts[H_WORK].repos["work"].backend == "p4"
    assert m.hosts[H_WORK].repos["work"].backend_config == {"depot": "//maury/work"}


def test_parse_invalid_backend_config_type_raises():
    import json as _json

    payload = {
        "version": 2,
        "profiles": {P_HOME: {"name": "home", "extends": None}},
        "hosts": {
            H_TOAD: {
                "name": "toad",
                "profile": P_HOME,
                "repos": {
                    "base": {
                        "url": "git@x:o/r.git",
                        "mode": "rw",
                        "backend_config": "not an object",
                    }
                },
            }
        },
    }
    with pytest.raises(ManifestError) as ei:
        parse_manifest(_json.dumps(payload))
    assert "backend_config" in str(ei.value)


def test_load_from_file(tmp_path):
    p = tmp_path / "manifest.json"
    p.write_text(VALID_JSON)
    m = load_manifest(p)
    assert H_TOAD in m.hosts


# ---- v1 rejection ------------------------------------------------------


def test_v1_manifest_rejected_with_upgrade_message():
    v1 = '{"version": 1, "profiles": {}, "hosts": {}}'
    with pytest.raises(ManifestError) as ei:
        parse_manifest(v1, source="legacy.json")
    assert "v1" in str(ei.value)
    assert "upgrade" in str(ei.value)


def test_unsupported_version_rejected():
    bad = '{"version": 99, "profiles": {}, "hosts": {}}'
    with pytest.raises(ManifestError) as ei:
        parse_manifest(bad)
    assert "unsupported version" in str(ei.value)


# ---- key validation ----------------------------------------------------


def test_invalid_profile_id_raises():
    bad = '{"version": 2, "profiles": {"home": {"name": "home", "extends": null}}, "hosts": {}}'
    with pytest.raises(ManifestError) as ei:
        parse_manifest(bad)
    assert "valid profile ID" in str(ei.value)


def test_invalid_host_id_raises():
    bad = (
        f'{{"version": 2,'
        f' "profiles": {{"{P_HOME}": {{"name": "home", "extends": null}}}},'
        f' "hosts": {{"toad": {{"name": "toad", "profile": "{P_HOME}", "repos": {{}}}}}}}}'
    )
    with pytest.raises(ManifestError) as ei:
        parse_manifest(bad)
    assert "valid host ID" in str(ei.value)


def test_missing_name_raises():
    bad = f'{{"version": 2, "profiles": {{"{P_HOME}": {{"extends": null}}}}, "hosts": {{}}}}'
    with pytest.raises(ManifestError) as ei:
        parse_manifest(bad)
    assert "missing 'name'" in str(ei.value)


def test_missing_host_profile_raises():
    bad = (
        f'{{"version": 2,'
        f' "profiles": {{"{P_HOME}": {{"name": "home", "extends": null}}}},'
        f' "hosts": {{"{H_TOAD}": {{"name": "toad", "repos": {{}}}}}}}}'
    )
    with pytest.raises(ManifestError) as ei:
        parse_manifest(bad)
    assert "missing 'profile'" in str(ei.value)


def test_invalid_push_policy_raises():
    bad = (
        f'{{"version": 2,'
        f' "profiles": {{"{P_HOME}": {{"name": "home", "extends": null}}}},'
        f' "hosts": {{"{H_TOAD}": {{"name": "t", "profile": "{P_HOME}",'
        f' "push_policy": "yolo", "repos": {{}}}}}}}}'
    )
    with pytest.raises(ManifestError) as ei:
        parse_manifest(bad)
    assert "invalid push_policy" in str(ei.value)


def test_invalid_repo_mode_raises():
    bad = (
        f'{{"version": 2,'
        f' "profiles": {{"{P_HOME}": {{"name": "home", "extends": null}}}},'
        f' "hosts": {{"{H_TOAD}": {{"name": "t", "profile": "{P_HOME}",'
        f' "repos": {{"r": {{"url": "git@x:o/r.git", "mode": "wat"}}}}}}}}}}'
    )
    with pytest.raises(ManifestError) as ei:
        parse_manifest(bad)
    assert "invalid mode" in str(ei.value)


def test_unknown_top_level_keys_raise():
    bad = (
        f'{{"version": 2,'
        f' "profiles": {{"{P_HOME}": {{"name": "home", "extends": null, "weird": true}}}},'
        f' "hosts": {{}}}}'
    )
    with pytest.raises(ManifestError) as ei:
        parse_manifest(bad)
    assert "unknown keys" in str(ei.value)


# ---- cross-validation --------------------------------------------------


def test_validate_clean_manifest():
    m = parse_manifest(VALID_JSON)
    assert validate_manifest(m) == []


def test_validate_catches_host_referencing_unknown_profile():
    m = Manifest(
        version=2,
        profiles={P_HOME: ProfileSpec(name="home")},
        hosts={H_TOAD: HostSpec(name="toad", profile="profile_ghostghostghostghostghostghost00")},
    )
    errors = validate_manifest(m)
    assert any("profile_ghost" in e for e in errors)


def test_validate_catches_extends_unknown_profile():
    m = Manifest(
        version=2,
        profiles={P_HOME: ProfileSpec(name="home", extends="profile_ghostghostghostghostghostghost00")},
    )
    errors = validate_manifest(m)
    assert any("profile_ghost" in e for e in errors)


def test_validate_catches_duplicate_profile_names():
    m = Manifest(
        version=2,
        profiles={
            P_HOME: ProfileSpec(name="dup"),
            P_WORK: ProfileSpec(name="dup"),
        },
    )
    errors = validate_manifest(m)
    assert any("duplicate profile name" in e for e in errors)


def test_validate_catches_duplicate_host_names():
    m = Manifest(
        version=2,
        profiles={P_HOME: ProfileSpec(name="home")},
        hosts={
            H_TOAD: HostSpec(name="dup", profile=P_HOME),
            H_WORK: HostSpec(name="dup", profile=P_HOME),
        },
    )
    errors = validate_manifest(m)
    assert any("duplicate host name" in e for e in errors)


# ---- inheritance chain ------------------------------------------------


def test_inheritance_chain_flat():
    m = Manifest(version=2, profiles={P_HOME: ProfileSpec(name="home")})
    assert m.inheritance_chain(P_HOME) == [P_HOME]


def test_inheritance_chain_one_level():
    m = Manifest(
        version=2,
        profiles={
            P_BASE: ProfileSpec(name="base"),
            P_HOME: ProfileSpec(name="home", extends=P_BASE),
        },
    )
    assert m.inheritance_chain(P_HOME) == [P_BASE, P_HOME]


def test_inheritance_chain_cycle_raises():
    m = Manifest(
        version=2,
        profiles={
            P_HOME: ProfileSpec(name="a", extends=P_WORK),
            P_WORK: ProfileSpec(name="b", extends=P_HOME),
        },
    )
    with pytest.raises(ManifestError) as ei:
        m.inheritance_chain(P_HOME)
    assert "cycle" in str(ei.value)


# ---- name<->id resolution ---------------------------------------------


def test_profile_id_by_name():
    m = parse_manifest(VALID_JSON)
    assert m.profile_id_by_name("home") == P_HOME
    assert m.profile_id_by_name("nonexistent") is None


def test_host_id_by_name_exact():
    m = parse_manifest(VALID_JSON)
    assert m.host_id_by_name("toad") == H_TOAD


def test_host_id_by_name_strips_dotted_suffix():
    m = parse_manifest(VALID_JSON)
    assert m.host_id_by_name("toad.local") == H_TOAD


def test_resolve_profile_accepts_name_or_id():
    m = parse_manifest(VALID_JSON)
    assert m.resolve_profile("home") == P_HOME
    assert m.resolve_profile(P_HOME) == P_HOME
    assert m.resolve_profile("nonexistent") is None
    assert m.resolve_profile("profile_unknownunknownunknownunknown00") is None


def test_resolve_host_accepts_name_or_id():
    m = parse_manifest(VALID_JSON)
    assert m.resolve_host("toad") == H_TOAD
    assert m.resolve_host(H_TOAD) == H_TOAD
    assert m.resolve_host("nonexistent") is None


# ---- dump round-trip ---------------------------------------------------


def test_dump_round_trip():
    m = parse_manifest(VALID_JSON)
    dumped = dump_manifest(m)
    m2 = parse_manifest(dumped)
    assert m2.profiles == m.profiles
    assert m2.hosts == m.hosts


def test_dump_includes_backend_when_non_default():
    m = Manifest(
        version=2,
        profiles={P_HOME: ProfileSpec(name="home")},
        hosts={
            H_TOAD: HostSpec(
                name="toad",
                profile=P_HOME,
                repos={
                    "base": RepoSpec(url="git@x:o/r.git", mode=RepoMode.RW, backend="github"),
                },
            )
        },
    )
    dumped = dump_manifest(m)
    assert '"backend": "github"' in dumped


def test_dump_omits_backend_for_default_git():
    m = Manifest(
        version=2,
        profiles={P_HOME: ProfileSpec(name="home")},
        hosts={
            H_TOAD: HostSpec(
                name="toad",
                profile=P_HOME,
                repos={
                    "base": RepoSpec(url="git@x:o/r.git", mode=RepoMode.RW, backend="git"),
                },
            )
        },
    )
    dumped = dump_manifest(m)
    assert '"backend": "git"' not in dumped


# ---- known_profiles helpers --------------------------------------------


def test_known_profiles_from_returns_id_set():
    m = parse_manifest(VALID_JSON)
    assert known_profiles_from(m) == {P_BASE, P_HOME, P_WORK}


def test_known_profile_names_from_returns_name_set():
    m = parse_manifest(VALID_JSON)
    assert known_profile_names_from(m) == {"base", "home", "work"}


def test_known_profiles_from_none_returns_empty_set():
    assert known_profiles_from(None) == set()
    assert known_profile_names_from(None) == set()


# ---- seed manifest sanity ----------------------------------------------


def test_seed_manifest_is_valid(tmp_path):
    """The shipped seed manifest must parse and validate cleanly."""
    from pathlib import Path

    seed = Path(__file__).resolve().parents[2] / "base-template" / ".meta" / "manifest.json"
    if not seed.exists():
        pytest.skip(f"seed manifest not present at {seed}")
    m = load_manifest(seed)
    assert validate_manifest(m) == []
    assert "toad" in {s.name for s in m.hosts.values()}
    assert "work-laptop" in {s.name for s in m.hosts.values()}
