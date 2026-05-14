"""Tests for the manifest module (v2 schema with surrogate keys)."""

from __future__ import annotations

from pathlib import Path

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
H_WORKSTATION = "host_00000000000000000000000000000010"
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
    "{H_WORKSTATION}": {{
      "name": "workstation",
      "profile": "{P_HOME}",
      "lock": false,
      "push_policy": "permissive",
      "repos": {{
        "base":     {{"url": "git@x:o/maury-base.git",     "mode": "rw"}},
        "personal": {{"url": "git@x:o/maury-personal.git", "mode": "rw"}}
      }},
      "owner": "you@example.com",
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


def test_id_generators_produce_recognized_format() -> None:
    hid = new_host_id()
    pid = new_profile_id()
    assert is_host_id(hid)
    assert is_profile_id(pid)
    assert not is_host_id(pid)
    assert not is_profile_id(hid)


def test_id_collision_resistance() -> None:
    """100 IDs should all be unique."""
    ids = {new_host_id() for _ in range(100)}
    assert len(ids) == 100


# ---- v2 parse ----------------------------------------------------------


def test_parse_valid_v2_manifest() -> None:
    m = parse_manifest(VALID_JSON)
    assert m.version == 2
    assert set(m.profiles) == {P_BASE, P_HOME, P_WORK}
    assert set(m.hosts) == {H_WORKSTATION, H_WORK}


def test_parse_extracts_profile_metadata() -> None:
    m = parse_manifest(VALID_JSON)
    assert m.profiles[P_BASE].name == "base"
    assert m.profiles[P_BASE].description == "Universal"
    assert m.profiles[P_BASE].extends is None


def test_parse_extracts_host_with_profile_id() -> None:
    m = parse_manifest(VALID_JSON)
    workstation = m.hosts[H_WORKSTATION]
    assert workstation.name == "workstation"
    assert workstation.profile == P_HOME  # ID, not "home"
    assert workstation.push_policy == PushPolicy.PERMISSIVE
    assert workstation.lock is False


def test_parse_locked_host() -> None:
    m = parse_manifest(VALID_JSON)
    work = m.hosts[H_WORK]
    assert work.name == "work-laptop"
    assert work.lock is True
    assert work.push_policy == PushPolicy.DISABLED


def test_parse_default_repo_backend_is_git() -> None:
    m = parse_manifest(VALID_JSON)
    assert m.hosts[H_WORKSTATION].repos["base"].backend == "git"
    assert m.hosts[H_WORKSTATION].repos["base"].backend_config is None


def test_parse_repo_with_backend() -> None:
    import json as _json

    payload = {
        "version": 2,
        "profiles": {P_HOME: {"name": "home", "extends": None}},
        "hosts": {
            H_WORKSTATION: {
                "name": "workstation",
                "profile": P_HOME,
                "repos": {"base": {"url": "git@x:o/r.git", "mode": "rw", "backend": "github"}},
            }
        },
    }
    m = parse_manifest(_json.dumps(payload))
    assert m.hosts[H_WORKSTATION].repos["base"].backend == "github"


def test_parse_repo_with_backend_config() -> None:
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


def test_parse_invalid_backend_config_type_raises() -> None:
    import json as _json

    payload = {
        "version": 2,
        "profiles": {P_HOME: {"name": "home", "extends": None}},
        "hosts": {
            H_WORKSTATION: {
                "name": "workstation",
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


def test_load_from_file(tmp_path: Path) -> None:
    p = tmp_path / "manifest.json"
    p.write_text(VALID_JSON)
    m = load_manifest(p)
    assert H_WORKSTATION in m.hosts


# ---- v1 rejection ------------------------------------------------------


def test_v1_manifest_rejected_with_upgrade_message() -> None:
    v1 = '{"version": 1, "profiles": {}, "hosts": {}}'
    with pytest.raises(ManifestError) as ei:
        parse_manifest(v1, source="legacy.json")
    assert "v1" in str(ei.value)
    assert "upgrade" in str(ei.value)


def test_unsupported_version_rejected() -> None:
    bad = '{"version": 99, "profiles": {}, "hosts": {}}'
    with pytest.raises(ManifestError) as ei:
        parse_manifest(bad)
    assert "unsupported version" in str(ei.value)


# ---- key validation ----------------------------------------------------


def test_invalid_profile_id_raises() -> None:
    bad = '{"version": 2, "profiles": {"home": {"name": "home", "extends": null}}, "hosts": {}}'
    with pytest.raises(ManifestError) as ei:
        parse_manifest(bad)
    assert "valid profile ID" in str(ei.value)


def test_invalid_host_id_raises() -> None:
    bad = (
        f'{{"version": 2,'
        f' "profiles": {{"{P_HOME}": {{"name": "home", "extends": null}}}},'
        f' "hosts": {{"workstation": {{"name": "workstation", "profile": "{P_HOME}", "repos": {{}}}}}}}}'
    )
    with pytest.raises(ManifestError) as ei:
        parse_manifest(bad)
    assert "valid host ID" in str(ei.value)


def test_missing_name_raises() -> None:
    bad = f'{{"version": 2, "profiles": {{"{P_HOME}": {{"extends": null}}}}, "hosts": {{}}}}'
    with pytest.raises(ManifestError) as ei:
        parse_manifest(bad)
    assert "missing 'name'" in str(ei.value)


def test_missing_host_profile_raises() -> None:
    bad = (
        f'{{"version": 2,'
        f' "profiles": {{"{P_HOME}": {{"name": "home", "extends": null}}}},'
        f' "hosts": {{"{H_WORKSTATION}": {{"name": "workstation", "repos": {{}}}}}}}}'
    )
    with pytest.raises(ManifestError) as ei:
        parse_manifest(bad)
    assert "missing 'profile'" in str(ei.value)


def test_invalid_push_policy_raises() -> None:
    bad = (
        f'{{"version": 2,'
        f' "profiles": {{"{P_HOME}": {{"name": "home", "extends": null}}}},'
        f' "hosts": {{"{H_WORKSTATION}": {{"name": "t", "profile": "{P_HOME}",'
        f' "push_policy": "yolo", "repos": {{}}}}}}}}'
    )
    with pytest.raises(ManifestError) as ei:
        parse_manifest(bad)
    assert "invalid push_policy" in str(ei.value)


def test_invalid_repo_mode_raises() -> None:
    bad = (
        f'{{"version": 2,'
        f' "profiles": {{"{P_HOME}": {{"name": "home", "extends": null}}}},'
        f' "hosts": {{"{H_WORKSTATION}": {{"name": "t", "profile": "{P_HOME}",'
        f' "repos": {{"r": {{"url": "git@x:o/r.git", "mode": "wat"}}}}}}}}}}'
    )
    with pytest.raises(ManifestError) as ei:
        parse_manifest(bad)
    assert "invalid mode" in str(ei.value)


def test_unknown_top_level_keys_raise() -> None:
    bad = (
        f'{{"version": 2,'
        f' "profiles": {{"{P_HOME}": {{"name": "home", "extends": null, "weird": true}}}},'
        f' "hosts": {{}}}}'
    )
    with pytest.raises(ManifestError) as ei:
        parse_manifest(bad)
    assert "unknown keys" in str(ei.value)


# ---- cross-validation --------------------------------------------------


def test_validate_clean_manifest() -> None:
    m = parse_manifest(VALID_JSON)
    assert validate_manifest(m) == []


def test_validate_catches_host_referencing_unknown_profile() -> None:
    m = Manifest(
        version=2,
        profiles={P_HOME: ProfileSpec(name="home")},
        hosts={H_WORKSTATION: HostSpec(name="workstation", profile="profile_ghostghostghostghostghostghost00")},
    )
    errors = validate_manifest(m)
    assert any("profile_ghost" in e for e in errors)


def test_validate_catches_extends_unknown_profile() -> None:
    m = Manifest(
        version=2,
        profiles={P_HOME: ProfileSpec(name="home", extends="profile_ghostghostghostghostghostghost00")},
    )
    errors = validate_manifest(m)
    assert any("profile_ghost" in e for e in errors)


def test_validate_catches_duplicate_profile_names() -> None:
    m = Manifest(
        version=2,
        profiles={
            P_HOME: ProfileSpec(name="dup"),
            P_WORK: ProfileSpec(name="dup"),
        },
    )
    errors = validate_manifest(m)
    assert any("duplicate profile name" in e for e in errors)


def test_validate_catches_duplicate_host_names() -> None:
    m = Manifest(
        version=2,
        profiles={P_HOME: ProfileSpec(name="home")},
        hosts={
            H_WORKSTATION: HostSpec(name="dup", profile=P_HOME),
            H_WORK: HostSpec(name="dup", profile=P_HOME),
        },
    )
    errors = validate_manifest(m)
    assert any("duplicate host name" in e for e in errors)


# ---- inheritance chain ------------------------------------------------


def test_inheritance_chain_flat() -> None:
    m = Manifest(version=2, profiles={P_HOME: ProfileSpec(name="home")})
    assert m.inheritance_chain(P_HOME) == [P_HOME]


def test_inheritance_chain_one_level() -> None:
    m = Manifest(
        version=2,
        profiles={
            P_BASE: ProfileSpec(name="base"),
            P_HOME: ProfileSpec(name="home", extends=P_BASE),
        },
    )
    assert m.inheritance_chain(P_HOME) == [P_BASE, P_HOME]


def test_inheritance_chain_cycle_raises() -> None:
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


def test_profile_id_by_name() -> None:
    m = parse_manifest(VALID_JSON)
    assert m.profile_id_by_name("home") == P_HOME
    assert m.profile_id_by_name("nonexistent") is None


def test_host_id_by_name_exact() -> None:
    m = parse_manifest(VALID_JSON)
    assert m.host_id_by_name("workstation") == H_WORKSTATION


def test_host_id_by_name_strips_dotted_suffix() -> None:
    m = parse_manifest(VALID_JSON)
    assert m.host_id_by_name("workstation.local") == H_WORKSTATION


def test_resolve_profile_accepts_name_or_id() -> None:
    m = parse_manifest(VALID_JSON)
    assert m.resolve_profile("home") == P_HOME
    assert m.resolve_profile(P_HOME) == P_HOME
    assert m.resolve_profile("nonexistent") is None
    assert m.resolve_profile("profile_unknownunknownunknownunknown00") is None


def test_resolve_host_accepts_name_or_id() -> None:
    m = parse_manifest(VALID_JSON)
    assert m.resolve_host("workstation") == H_WORKSTATION
    assert m.resolve_host(H_WORKSTATION) == H_WORKSTATION
    assert m.resolve_host("nonexistent") is None


# ---- dump round-trip ---------------------------------------------------


def test_dump_round_trip() -> None:
    m = parse_manifest(VALID_JSON)
    dumped = dump_manifest(m)
    m2 = parse_manifest(dumped)
    assert m2.profiles == m.profiles
    assert m2.hosts == m.hosts


def test_dump_includes_backend_when_non_default() -> None:
    m = Manifest(
        version=2,
        profiles={P_HOME: ProfileSpec(name="home")},
        hosts={
            H_WORKSTATION: HostSpec(
                name="workstation",
                profile=P_HOME,
                repos={
                    "base": RepoSpec(url="git@x:o/r.git", mode=RepoMode.RW, backend="github"),
                },
            )
        },
    )
    dumped = dump_manifest(m)
    assert '"backend": "github"' in dumped


def test_dump_omits_backend_for_default_git() -> None:
    m = Manifest(
        version=2,
        profiles={P_HOME: ProfileSpec(name="home")},
        hosts={
            H_WORKSTATION: HostSpec(
                name="workstation",
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


def test_known_profiles_from_returns_id_set() -> None:
    m = parse_manifest(VALID_JSON)
    assert known_profiles_from(m) == {P_BASE, P_HOME, P_WORK}


def test_known_profile_names_from_returns_name_set() -> None:
    m = parse_manifest(VALID_JSON)
    assert known_profile_names_from(m) == {"base", "home", "work"}


def test_known_profiles_from_none_returns_empty_set() -> None:
    assert known_profiles_from(None) == set()
    assert known_profile_names_from(None) == set()


# ---- seed manifest sanity ----------------------------------------------


def test_seed_manifest_is_valid(tmp_path: Path) -> None:
    """The shipped seed manifest must parse and validate cleanly."""
    from pathlib import Path

    seed = Path(__file__).resolve().parents[2] / "base-template" / ".meta" / "manifest.json"
    if not seed.exists():
        pytest.skip(f"seed manifest not present at {seed}")
    m = load_manifest(seed)
    assert validate_manifest(m) == []
    # The seed ships placeholder example hosts (genericized 2026-05-13):
    # one home-mode host named `workstation` and one work-mode host named
    # `work-laptop`. If we ever rename either, update this test.
    host_names = {s.name for s in m.hosts.values()}
    assert "workstation" in host_names
    assert "work-laptop" in host_names


# ---- additional error-path coverage ------------------------------------
# Lifts manifest.py's coverage by exercising the remaining structural-
# validation error paths that the existing tests didn't cover.


def test_parse_invalid_json_raises_manifest_error() -> None:
    """Bare JSON parse failures get wrapped in ManifestError with source."""
    with pytest.raises(ManifestError, match="JSON parse error"):
        parse_manifest("{not valid json", source="bogus.json")


def test_parse_top_level_must_be_object() -> None:
    with pytest.raises(ManifestError, match="top-level must be an object"):
        parse_manifest("[1, 2, 3]")


def test_parse_v1_manifest_raises_with_migration_hint() -> None:
    """v1 manifests are rejected with a pointer at the migration command."""
    v1 = '{"version": 1, "profiles": {}, "hosts": {}}'
    with pytest.raises(ManifestError, match="surrogate-key migration required"):
        parse_manifest(v1, source="legacy.json")


def test_parse_unsupported_version_raises() -> None:
    v3 = '{"version": 3, "profiles": {}, "hosts": {}}'
    with pytest.raises(ManifestError, match="unsupported version 3"):
        parse_manifest(v3)


def test_parse_profiles_must_be_object() -> None:
    bad = '{"version": 2, "profiles": [], "hosts": {}}'
    with pytest.raises(ManifestError, match="'profiles' must be an object"):
        parse_manifest(bad)


def test_parse_profile_value_must_be_object() -> None:
    bad = f'{{"version": 2, "profiles": {{"{P_HOME}": "not-an-object"}}, "hosts": {{}}}}'
    with pytest.raises(ManifestError, match=r"value must be an object"):
        parse_manifest(bad)


def test_parse_hosts_must_be_object() -> None:
    bad = f'{{"version": 2, "profiles": {{"{P_HOME}": {{"name": "home"}}}}, "hosts": []}}'
    with pytest.raises(ManifestError, match="'hosts' must be an object"):
        parse_manifest(bad)


def test_parse_host_value_must_be_object() -> None:
    bad = (
        '{"version": 2, '
        f'"profiles": {{"{P_HOME}": {{"name": "home"}}}}, '
        f'"hosts": {{"{H_WORKSTATION}": "not-an-object"}}}}'
    )
    with pytest.raises(ManifestError, match=r"value must be an object"):
        parse_manifest(bad)


def test_parse_host_unknown_top_level_keys_raise() -> None:
    bad = (
        '{"version": 2, '
        f'"profiles": {{"{P_HOME}": {{"name": "home"}}}}, '
        f'"hosts": {{"{H_WORKSTATION}": {{'
        f'"name": "workstation", "profile": "{P_HOME}", "bogus_field": 1, "repos": {{}}'
        '}}}'
    )
    with pytest.raises(ManifestError, match=r"unknown keys"):
        parse_manifest(bad)


def test_parse_repos_must_be_object() -> None:
    bad = (
        '{"version": 2, '
        f'"profiles": {{"{P_HOME}": {{"name": "home"}}}}, '
        f'"hosts": {{"{H_WORKSTATION}": {{'
        f'"name": "workstation", "profile": "{P_HOME}", "repos": []'
        '}}}'
    )
    with pytest.raises(ManifestError, match="'repos' must be an object"):
        parse_manifest(bad)


def test_parse_repo_value_must_be_object() -> None:
    bad = (
        '{"version": 2, '
        f'"profiles": {{"{P_HOME}": {{"name": "home"}}}}, '
        f'"hosts": {{"{H_WORKSTATION}": {{'
        f'"name": "workstation", "profile": "{P_HOME}", '
        '"repos": {"base": "not-an-object"}}}}'
    )
    with pytest.raises(ManifestError, match=r"repo .base.+value must be an object"):
        parse_manifest(bad)


def test_parse_repo_unknown_keys_raise() -> None:
    bad = (
        '{"version": 2, '
        f'"profiles": {{"{P_HOME}": {{"name": "home"}}}}, '
        f'"hosts": {{"{H_WORKSTATION}": {{'
        f'"name": "workstation", "profile": "{P_HOME}", '
        '"repos": {"base": {"url": "x", "mode": "rw", "bogus": 1}}}}}'
    )
    with pytest.raises(ManifestError, match=r"repo .base.+unknown keys"):
        parse_manifest(bad)


def test_parse_repo_missing_url_raises() -> None:
    bad = (
        '{"version": 2, '
        f'"profiles": {{"{P_HOME}": {{"name": "home"}}}}, '
        f'"hosts": {{"{H_WORKSTATION}": {{'
        f'"name": "workstation", "profile": "{P_HOME}", '
        '"repos": {"base": {"mode": "rw"}}}}}'
    )
    with pytest.raises(ManifestError, match="missing 'url'"):
        parse_manifest(bad)


# ---- inheritance-chain edge cases --------------------------------------


def test_inheritance_chain_references_unknown_profile_raises() -> None:
    """If a profile's `extends` chain reaches an undefined profile,
    `inheritance_chain` raises with a helpful message."""
    m = Manifest(
        version=2,
        profiles={
            P_HOME: ProfileSpec(name="home", extends="profile_00000000000000000000000000000099"),
        },
        hosts={},
    )
    with pytest.raises(ManifestError, match="referenced but not defined"):
        m.inheritance_chain(P_HOME)


# ---- dump_manifest round-trip preserves backend_config -----------------


def test_dump_manifest_preserves_backend_config() -> None:
    """A RepoSpec with non-default backend AND backend_config round-trips
    through dump_manifest → parse_manifest cleanly."""
    repo = RepoSpec(
        url="custom://path",
        mode=RepoMode.RW,
        backend="s3-age",
        backend_config={"bucket": "my-bucket", "prefix": "config/"},
    )
    host = HostSpec(
        name="workstation",
        profile=P_HOME,
        repos={"base": repo},
    )
    original = Manifest(
        version=2,
        profiles={P_HOME: ProfileSpec(name="home", extends=None)},
        hosts={H_WORKSTATION: host},
    )
    text = dump_manifest(original)
    reparsed = parse_manifest(text)
    assert reparsed.hosts[H_WORKSTATION].repos["base"].backend == "s3-age"
    assert reparsed.hosts[H_WORKSTATION].repos["base"].backend_config == {
        "bucket": "my-bucket",
        "prefix": "config/",
    }


def test_dump_manifest_omits_default_backend() -> None:
    """When backend is the default 'git' and backend_config is empty,
    neither key appears in the dumped output (keeps the manifest tight)."""
    repo = RepoSpec(url="git@github.com:x/y.git", mode=RepoMode.RW)
    host = HostSpec(name="workstation", profile=P_HOME, repos={"base": repo})
    m = Manifest(
        version=2,
        profiles={P_HOME: ProfileSpec(name="home", extends=None)},
        hosts={H_WORKSTATION: host},
    )
    text = dump_manifest(m)
    # The repo dict for 'base' shouldn't carry backend/backend_config.
    assert '"backend"' not in text
    assert '"backend_config"' not in text
