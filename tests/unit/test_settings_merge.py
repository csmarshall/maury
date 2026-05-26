"""Tests for settings.json deep-merge semantics (ADR-0019)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from maury.render.settings_merge import (
    SettingsMergeError,
    collect_settings_layers,
    deep_merge,
    merge_layers,
)

# ---- deep_merge primitive ------------------------------------------------


def test_merge_dicts_child_overrides_keys() -> None:
    parent = {"theme": "dark", "alwaysThinkingEnabled": True}
    child = {"theme": "dark-daltonized"}
    out = deep_merge(parent, child)
    assert out == {"theme": "dark-daltonized", "alwaysThinkingEnabled": True}


def test_merge_dicts_child_adds_keys() -> None:
    parent = {"theme": "dark"}
    child = {"newSetting": 42}
    out = deep_merge(parent, child)
    assert out == {"theme": "dark", "newSetting": 42}


def test_merge_dicts_recurses_into_nested() -> None:
    parent = {"editor": {"font": "Menlo", "size": 12}}
    child = {"editor": {"size": 14, "ligatures": True}}
    out = deep_merge(parent, child)
    assert out == {"editor": {"font": "Menlo", "size": 14, "ligatures": True}}


def test_merge_arrays_appends_unique() -> None:
    parent = ["a", "b"]
    child = ["c"]
    out = deep_merge(parent, child)
    assert out == ["a", "b", "c"]


def test_merge_arrays_dedupes_scalars() -> None:
    parent = ["a", "b"]
    child = ["b", "c"]
    out = deep_merge(parent, child)
    assert out == ["a", "b", "c"]


def test_merge_arrays_dedupes_objects_by_deep_equality() -> None:
    parent = [{"tool": "ruff"}, {"tool": "mypy"}]
    child = [{"tool": "ruff"}, {"tool": "pytest"}]
    out = deep_merge(parent, child)
    assert out == [{"tool": "ruff"}, {"tool": "mypy"}, {"tool": "pytest"}]


def test_merge_replace_marker_replaces_array() -> None:
    parent = ["a", "b", "c"]
    child = [{"$replace": ["x", "y"]}]
    out = deep_merge(parent, child)
    assert out == ["x", "y"]


def test_merge_replace_marker_must_wrap_a_list() -> None:
    parent = ["a"]
    child = [{"$replace": "not a list"}]
    with pytest.raises(SettingsMergeError) as ei:
        deep_merge(parent, child)
    assert "$replace" in str(ei.value)


def test_merge_scalar_child_wins() -> None:
    assert deep_merge("dark", "dark-daltonized") == "dark-daltonized"
    assert deep_merge(True, False) is False
    assert deep_merge(1, 2) == 2


def test_merge_type_mismatch_child_wins() -> None:
    """Child value of a different type from parent: child takes over."""
    assert deep_merge({"a": 1}, ["replaced"]) == ["replaced"]
    assert deep_merge(["a"], {"replaced": True}) == {"replaced": True}


def test_merge_pure_function_does_not_mutate() -> None:
    parent = {"a": [1, 2], "b": {"x": 1}}
    child = {"a": [3], "b": {"y": 2}}
    parent_copy = json.loads(json.dumps(parent))
    child_copy = json.loads(json.dumps(child))
    deep_merge(parent, child)
    assert parent == parent_copy
    assert child == child_copy


# ---- merge_layers (multi-layer) -----------------------------------------


def test_merge_layers_three_layers() -> None:
    layers = [
        ("base", '{"theme": "dark", "permissions": ["a"]}'),
        ("profile:home", '{"theme": "dark-daltonized", "permissions": ["b"]}'),
        ("host:workstation", '{"permissions": ["c"]}'),
    ]
    out = merge_layers(layers)
    assert out == {"theme": "dark-daltonized", "permissions": ["a", "b", "c"]}


def test_merge_layers_empty_returns_empty_dict() -> None:
    assert merge_layers([]) == {}


def test_merge_layers_invalid_json_raises() -> None:
    layers = [
        ("base", '{"valid": true}'),
        ("profile:home", "not json{"),
    ]
    with pytest.raises(SettingsMergeError) as ei:
        merge_layers(layers)
    assert "JSON parse error" in str(ei.value)
    assert "profile:home" in str(ei.value)


def test_merge_layers_non_object_top_level_raises() -> None:
    layers = [("base", "[1, 2, 3]")]
    with pytest.raises(SettingsMergeError) as ei:
        merge_layers(layers)
    assert "must be an object at top level" in str(ei.value)


# ---- collect_settings_layers ---------------------------------------------


def test_collect_finds_settings_json(tmp_path: Path) -> None:
    (tmp_path / "settings.json").write_text('{"theme": "dark"}')
    out = collect_settings_layers([("base", tmp_path)])
    assert out == [("base", '{"theme": "dark"}')]


def test_collect_finds_fragment_when_settings_json_absent(tmp_path: Path) -> None:
    (tmp_path / "settings.fragment.json").write_text('{"theme": "darker"}')
    out = collect_settings_layers([("profile:home", tmp_path)])
    assert out == [("profile:home", '{"theme": "darker"}')]


def test_collect_prefers_settings_json_over_fragment(tmp_path: Path) -> None:
    (tmp_path / "settings.json").write_text('{"a": 1}')
    (tmp_path / "settings.fragment.json").write_text('{"b": 2}')
    out = collect_settings_layers([("layer", tmp_path)])
    assert out == [("layer", '{"a": 1}')]


def test_collect_skips_layers_with_no_settings(tmp_path: Path) -> None:
    (tmp_path / "irrelevant.txt").write_text("nothing")
    out = collect_settings_layers([("layer", tmp_path)])
    assert out == []


# ---- end-to-end via render engine ---------------------------------------


def test_render_engine_emits_merged_settings(tmp_path: Path) -> None:
    """The render engine wires settings_merge correctly."""
    from maury.ids import new_host_id, new_profile_id
    from maury.manifest import HostSpec, Manifest, ProfileSpec
    from maury.render import render

    repo = tmp_path / "repo"
    (repo).mkdir()
    (repo / "settings.json").write_text('{"theme": "dark", "permissions": ["read"]}')
    (repo / "profiles" / "home").mkdir(parents=True, exist_ok=True)
    (repo / "profiles" / "home" / "settings.fragment.json").write_text(
        '{"theme": "dark-daltonized", "permissions": ["write"]}'
    )

    pid = new_profile_id()
    hid = new_host_id("h")
    m = Manifest(
        version=2,
        profiles={pid: ProfileSpec(name="home")},
        hosts={hid: HostSpec(name="workstation", profile=pid)},
    )

    result = render(repo_paths={"base": repo}, manifest=m, profile_id=pid, host_id=hid)
    settings = result.by_path()["settings.json"]
    parsed = json.loads(settings.content.decode())
    assert parsed == {
        "theme": "dark-daltonized",  # child overrode
        "permissions": ["read", "write"],  # appended unique
    }


def test_render_engine_skips_settings_when_no_layers_provide(tmp_path: Path) -> None:
    """No settings.json anywhere -> render output has none."""
    from maury.ids import new_host_id, new_profile_id
    from maury.manifest import HostSpec, Manifest, ProfileSpec
    from maury.render import render

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "CLAUDE.md").write_text("just markdown\n")

    pid = new_profile_id()
    hid = new_host_id("h")
    m = Manifest(
        version=2,
        profiles={pid: ProfileSpec(name="home")},
        hosts={hid: HostSpec(name="workstation", profile=pid)},
    )

    result = render(repo_paths={"base": repo}, manifest=m, profile_id=pid, host_id=hid)
    assert "settings.json" not in result.by_path()


def test_render_engine_records_settings_error_on_bad_json(tmp_path: Path) -> None:
    """Malformed settings.json layer surfaces as a render error."""
    from maury.ids import new_host_id, new_profile_id
    from maury.manifest import HostSpec, Manifest, ProfileSpec
    from maury.render import render

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "settings.json").write_text("{ this is not valid json")

    pid = new_profile_id()
    hid = new_host_id("h")
    m = Manifest(
        version=2,
        profiles={pid: ProfileSpec(name="home")},
        hosts={hid: HostSpec(name="workstation", profile=pid)},
    )

    result = render(repo_paths={"base": repo}, manifest=m, profile_id=pid, host_id=hid)
    assert any("settings.json" in e for e in result.errors)
    assert "settings.json" not in result.by_path()
