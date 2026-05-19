"""Unit tests for `maury.manifest_resolve_cmd` (the resolve CLI driver)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from maury.ids import new_host_id, new_profile_id
from maury.manifest_merge import Conflict, ConflictKind
from maury.manifest_resolve_cmd import (
    ResolveError,
    fetch_three_way,
    render_path,
    resolve_manifest,
    write_atomic,
)


def _git_available() -> bool:
    try:
        return subprocess.run(["git", "--version"], capture_output=True, check=False).returncode == 0
    except OSError:
        return False


def _run_git(args: list[str], cwd: Path) -> None:
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


def _make_repo_in_conflict(
    tmp_path: Path,
    *,
    ancestor: dict[str, Any],
    ours: dict[str, Any],
    theirs: dict[str, Any],
) -> Path:
    """Create a git repo where manifest.json is in 3-way merge conflict.

    Layout:
      - Branch main:    ancestor
      - Branch ours:    ancestor + ours change (committed)
      - Merge theirs into ours → conflict on manifest.json
    Returns the manifest path.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _run_git(["git", "init", "-q", "-b", "main"], cwd=repo)
    _run_git(["git", "config", "user.name", "t"], cwd=repo)
    _run_git(["git", "config", "user.email", "t@t.invalid"], cwd=repo)

    manifest = repo / "manifest.json"
    manifest.write_text(json.dumps(ancestor, indent=2) + "\n")
    _run_git(["git", "add", "manifest.json"], cwd=repo)
    _run_git(["git", "commit", "-q", "-m", "ancestor"], cwd=repo)
    # Capture the ancestor commit.

    # Create 'theirs' branch from ancestor.
    _run_git(["git", "checkout", "-q", "-b", "theirs"], cwd=repo)
    manifest.write_text(json.dumps(theirs, indent=2) + "\n")
    _run_git(["git", "commit", "-q", "-am", "theirs"], cwd=repo)

    # Back to main; apply 'ours'.
    _run_git(["git", "checkout", "-q", "main"], cwd=repo)
    manifest.write_text(json.dumps(ours, indent=2) + "\n")
    _run_git(["git", "commit", "-q", "-am", "ours"], cwd=repo)

    # Merge theirs in, expecting a conflict.
    proc = subprocess.run(
        ["git", "merge", "--no-edit", "theirs"],
        cwd=repo,
        capture_output=True,
        check=False,
    )
    # Merge should have failed with a conflict; that's the state we want.
    if proc.returncode == 0:
        raise RuntimeError("expected merge conflict but git auto-resolved")
    return manifest


# ---- write_atomic ---------------------------------------------------------


def test_write_atomic_creates_file(tmp_path: Path) -> None:
    p = tmp_path / "out.json"
    write_atomic(p, '{"x": 1}\n')
    assert p.read_text() == '{"x": 1}\n'


def test_write_atomic_overwrites(tmp_path: Path) -> None:
    p = tmp_path / "out.json"
    p.write_text("old")
    write_atomic(p, "new")
    assert p.read_text() == "new"


def test_write_atomic_creates_parent_dir(tmp_path: Path) -> None:
    p = tmp_path / "deeply" / "nested" / "out.json"
    write_atomic(p, "{}")
    assert p.read_text() == "{}"


# ---- render_path ---------------------------------------------------------


def test_render_path_dots() -> None:
    assert render_path(("hosts", "host_a", "name")) == "hosts.host_a.name"


def test_render_path_empty() -> None:
    assert render_path(()) == ""


# ---- fetch_three_way -----------------------------------------------------


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_fetch_three_way_against_genuine_conflict(tmp_path: Path) -> None:
    """Reads stages 1/2/3 from an actual `git merge` conflict state."""
    ancestor: dict[str, Any] = {"version": 2, "profiles": {}, "hosts": {}}
    ours = {"version": 2, "profiles": {}, "hosts": {"h_a": {"name": "alice"}}}
    theirs = {"version": 2, "profiles": {}, "hosts": {"h_b": {"name": "bob"}}}
    manifest = _make_repo_in_conflict(tmp_path, ancestor=ancestor, ours=ours, theirs=theirs)
    anc, ours_read, theirs_read = fetch_three_way(manifest)
    assert anc == ancestor
    assert ours_read == ours
    assert theirs_read == theirs


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_fetch_three_way_refuses_when_no_conflict(tmp_path: Path) -> None:
    """If the file is clean, refuse with a specific error."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _run_git(["git", "init", "-q"], cwd=repo)
    _run_git(["git", "config", "user.name", "t"], cwd=repo)
    _run_git(["git", "config", "user.email", "t@t.invalid"], cwd=repo)
    manifest = repo / "manifest.json"
    manifest.write_text('{"version": 2}\n')
    _run_git(["git", "add", "manifest.json"], cwd=repo)
    _run_git(["git", "commit", "-q", "-m", "clean"], cwd=repo)
    with pytest.raises(ResolveError, match="no merge conflict detected"):
        fetch_three_way(manifest)


def test_fetch_three_way_refuses_outside_git_repo(tmp_path: Path) -> None:
    """A path that isn't inside a git repo gets a clear error."""
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}")
    with pytest.raises(ResolveError, match="not inside a git repository"):
        fetch_three_way(manifest)


# ---- resolve_manifest (end-to-end with mocked git layer) -----------------


def _seed_minimum_v2(name_prefix: str = "alice") -> dict[str, Any]:
    """A minimal v2 manifest that passes load_manifest + validate_manifest."""
    pid = new_profile_id()
    hid = new_host_id()
    return {
        "version": 2,
        "profiles": {pid: {"name": f"{name_prefix}-home", "extends": None}},
        "hosts": {
            hid: {
                "name": f"{name_prefix}-laptop",
                "profile": pid,
                "repos": {"base": {"url": "git@x:o/r.git", "mode": "rw"}},
            }
        },
    }


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_resolve_manifest_auto_merges_additive_case(tmp_path: Path) -> None:
    """The canonical 'two hosts bootstrapping' case → zero prompts."""
    seed = _seed_minimum_v2()
    pid = next(iter(seed["profiles"].keys()))

    ancestor = seed
    ours = json.loads(json.dumps(ancestor))
    new_hid_a = new_host_id()
    ours["hosts"][new_hid_a] = {
        "name": "added-by-ours",
        "profile": pid,
        "repos": {"base": {"url": "git@x:o/r.git", "mode": "rw"}},
    }
    theirs = json.loads(json.dumps(ancestor))
    new_hid_b = new_host_id()
    theirs["hosts"][new_hid_b] = {
        "name": "added-by-theirs",
        "profile": pid,
        "repos": {"base": {"url": "git@x:o/r.git", "mode": "rw"}},
    }
    manifest = _make_repo_in_conflict(tmp_path, ancestor=ancestor, ours=ours, theirs=theirs)

    def prompt_should_not_fire(_c: Conflict) -> str:
        raise AssertionError("prompter should not be invoked on a clean additive merge")

    summary = resolve_manifest(manifest, prompter=prompt_should_not_fire)
    assert summary.user_resolved_paths == 0
    assert not summary.aborted

    # File now contains BOTH host additions.
    body = json.loads(manifest.read_text())
    assert new_hid_a in body["hosts"]
    assert new_hid_b in body["hosts"]


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_resolve_manifest_prompts_on_both_modified(tmp_path: Path) -> None:
    """Both sides rename the same profile → user chooses A."""
    seed = _seed_minimum_v2()
    pid = next(iter(seed["profiles"].keys()))

    ancestor = seed
    ours = json.loads(json.dumps(ancestor))
    ours["profiles"][pid]["name"] = "renamed-by-ours"
    theirs = json.loads(json.dumps(ancestor))
    theirs["profiles"][pid]["name"] = "renamed-by-theirs"
    manifest = _make_repo_in_conflict(tmp_path, ancestor=ancestor, ours=ours, theirs=theirs)

    calls: list[Conflict] = []

    def take_a(conflict: Conflict) -> str:
        calls.append(conflict)
        return "a"

    summary = resolve_manifest(manifest, prompter=take_a)
    assert len(calls) == 1
    assert calls[0].kind is ConflictKind.BOTH_MODIFIED
    assert summary.user_resolved_paths == 1
    assert summary.chosen_sides == ("A",)
    body = json.loads(manifest.read_text())
    assert body["profiles"][pid]["name"] == "renamed-by-ours"


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_resolve_manifest_take_b(tmp_path: Path) -> None:
    seed = _seed_minimum_v2()
    pid = next(iter(seed["profiles"].keys()))
    ancestor = seed
    ours = json.loads(json.dumps(ancestor))
    ours["profiles"][pid]["name"] = "ours-name"
    theirs = json.loads(json.dumps(ancestor))
    theirs["profiles"][pid]["name"] = "theirs-name"
    manifest = _make_repo_in_conflict(tmp_path, ancestor=ancestor, ours=ours, theirs=theirs)

    summary = resolve_manifest(manifest, prompter=lambda _c: "b")
    assert summary.chosen_sides == ("B",)
    body = json.loads(manifest.read_text())
    assert body["profiles"][pid]["name"] == "theirs-name"


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_resolve_manifest_skip_keeps_ancestor(tmp_path: Path) -> None:
    seed = _seed_minimum_v2()
    pid = next(iter(seed["profiles"].keys()))
    ancestor = seed
    ours = json.loads(json.dumps(ancestor))
    ours["profiles"][pid]["name"] = "ours-name"
    theirs = json.loads(json.dumps(ancestor))
    theirs["profiles"][pid]["name"] = "theirs-name"
    manifest = _make_repo_in_conflict(tmp_path, ancestor=ancestor, ours=ours, theirs=theirs)

    resolve_manifest(manifest, prompter=lambda _c: "s")
    body = json.loads(manifest.read_text())
    # Ancestor name preserved.
    assert body["profiles"][pid]["name"] == seed["profiles"][pid]["name"]


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_resolve_manifest_quit_aborts_without_writing(tmp_path: Path) -> None:
    seed = _seed_minimum_v2()
    pid = next(iter(seed["profiles"].keys()))
    ancestor = seed
    ours = json.loads(json.dumps(ancestor))
    ours["profiles"][pid]["name"] = "ours-name"
    theirs = json.loads(json.dumps(ancestor))
    theirs["profiles"][pid]["name"] = "theirs-name"
    manifest = _make_repo_in_conflict(tmp_path, ancestor=ancestor, ours=ours, theirs=theirs)
    before = manifest.read_text()

    summary = resolve_manifest(manifest, prompter=lambda _c: "q")
    assert summary.aborted is True
    # File on disk is whatever git left there (with conflict markers); not our resolved output.
    after = manifest.read_text()
    assert after == before


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_resolve_manifest_rejects_unknown_prompter_choice(tmp_path: Path) -> None:
    seed = _seed_minimum_v2()
    pid = next(iter(seed["profiles"].keys()))
    ancestor = seed
    ours = json.loads(json.dumps(ancestor))
    ours["profiles"][pid]["name"] = "x"
    theirs = json.loads(json.dumps(ancestor))
    theirs["profiles"][pid]["name"] = "y"
    manifest = _make_repo_in_conflict(tmp_path, ancestor=ancestor, ours=ours, theirs=theirs)

    with pytest.raises(ResolveError, match="unrecognised choice"):
        resolve_manifest(manifest, prompter=lambda _c: "zzz")


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_resolve_manifest_validates_after_resolution(tmp_path: Path) -> None:
    """A user-chosen resolution that breaks cross-references must refuse to write."""
    seed = _seed_minimum_v2()
    pid = next(iter(seed["profiles"].keys()))
    # Force a same-key BOTH_MODIFIED conflict on the profile so the user
    # gets a prompt; both sides rename, plus 'ours' also drops the only
    # profile entirely. Picking side A then produces a manifest with a
    # host pointing at a missing profile.
    ancestor = seed
    ours = json.loads(json.dumps(ancestor))
    # Rename the existing profile + drop it after rename means we still
    # need the conflict on the same key. Use a same-key text overlap:
    # both sides rename the profile, but ours ALSO appends a 'note' field
    # at the same level (a second key change to keep git's text merge
    # from being clean). Then on prompt, take side B (rename only).
    ours["profiles"][pid]["name"] = "renamed-ours"
    ours["profiles"][pid]["extends"] = "profile_nonexistent"  # dangling ref
    theirs = json.loads(json.dumps(ancestor))
    theirs["profiles"][pid]["name"] = "renamed-theirs"
    manifest = _make_repo_in_conflict(tmp_path, ancestor=ancestor, ours=ours, theirs=theirs)
    # Take A → manifest's profile.extends points at a nonexistent profile.
    with pytest.raises(ResolveError, match="validation"):
        resolve_manifest(manifest, prompter=lambda _c: "a")


# ---- semantic_summary ----------------------------------------------------


def test_semantic_summary_profile_delete_vs_modify_counts_bound_hosts() -> None:
    """Side A deletes a profile; side B has hosts referencing it."""
    from maury.manifest_merge import DELETE_SENTINEL, Conflict, ConflictKind
    from maury.manifest_resolve_cmd import semantic_summary

    pid = "profile_abc"
    ancestor: dict[str, Any] = {
        "profiles": {pid: {"name": "home", "extends": None}},
        "hosts": {"host_a": {"profile": pid, "repos": {}}},
    }
    side_a: dict[str, Any] = {"profiles": {}, "hosts": ancestor["hosts"]}  # deleted profile
    side_b: dict[str, Any] = {
        "profiles": {pid: {"name": "home", "extends": None}},
        "hosts": {
            "host_a": {"profile": pid, "repos": {}},
            "host_b": {"profile": pid, "repos": {}},
        },
    }
    conflict = Conflict(
        path=("profiles", pid),
        kind=ConflictKind.DELETE_VS_MODIFY,
        ancestor=ancestor["profiles"][pid],
        side_a=DELETE_SENTINEL,
        side_b=side_b["profiles"][pid],
    )
    summary = semantic_summary(conflict, ancestor=ancestor, side_a=side_a, side_b=side_b)
    assert summary is not None
    assert "side A" in summary
    assert "2 host(s)" in summary  # both host_a and host_b on B side reference it


def test_semantic_summary_profile_both_modified_counts_hosts_per_side() -> None:
    from maury.manifest_merge import Conflict, ConflictKind
    from maury.manifest_resolve_cmd import semantic_summary

    pid = "profile_abc"
    ancestor: dict[str, Any] = {
        "profiles": {pid: {"name": "home", "extends": None}},
        "hosts": {"host_a": {"profile": pid, "repos": {}}},
    }
    side_a: dict[str, Any] = {
        "profiles": {pid: {"name": "renamed-A", "extends": None}},
        "hosts": {"host_a": {"profile": pid, "repos": {}}},
    }
    side_b: dict[str, Any] = {
        "profiles": {pid: {"name": "renamed-B", "extends": None}},
        "hosts": {
            "host_a": {"profile": pid, "repos": {}},
            "host_b": {"profile": pid, "repos": {}},
        },
    }
    conflict = Conflict(
        path=("profiles", pid),
        kind=ConflictKind.BOTH_MODIFIED,
        ancestor=ancestor["profiles"][pid],
        side_a=side_a["profiles"][pid],
        side_b=side_b["profiles"][pid],
    )
    summary = semantic_summary(conflict, ancestor=ancestor, side_a=side_a, side_b=side_b)
    assert summary is not None
    assert "1 host(s) on A" in summary
    assert "2 on B" in summary


def test_semantic_summary_host_delete_vs_modify() -> None:
    from maury.manifest_merge import DELETE_SENTINEL, Conflict, ConflictKind
    from maury.manifest_resolve_cmd import semantic_summary

    hid = "host_abc"
    conflict = Conflict(
        path=("hosts", hid),
        kind=ConflictKind.DELETE_VS_MODIFY,
        ancestor={"name": "old"},
        side_a=DELETE_SENTINEL,
        side_b={"name": "renamed"},
    )
    summary = semantic_summary(conflict, ancestor={}, side_a={}, side_b={})
    assert summary is not None
    assert "side A retires" in summary


def test_semantic_summary_returns_none_for_unknown_paths() -> None:
    from maury.manifest_merge import Conflict, ConflictKind
    from maury.manifest_resolve_cmd import semantic_summary

    conflict = Conflict(
        path=("version",),
        kind=ConflictKind.BOTH_MODIFIED,
        ancestor=1,
        side_a=2,
        side_b=3,
    )
    assert semantic_summary(conflict, ancestor={}, side_a={}, side_b={}) is None


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_resolve_manifest_populates_semantic_summary_in_prompts(tmp_path: Path) -> None:
    """End-to-end: a profile-rename conflict surfaces a summary to the prompter."""
    seed = _seed_minimum_v2()
    pid = next(iter(seed["profiles"].keys()))
    ancestor = seed
    ours = json.loads(json.dumps(ancestor))
    ours["profiles"][pid]["name"] = "renamed-ours"
    theirs = json.loads(json.dumps(ancestor))
    theirs["profiles"][pid]["name"] = "renamed-theirs"
    manifest = _make_repo_in_conflict(tmp_path, ancestor=ancestor, ours=ours, theirs=theirs)

    seen: list[str | None] = []

    def take_a(conflict: Conflict) -> str:
        seen.append(conflict.semantic_summary)
        return "a"

    resolve_manifest(manifest, prompter=take_a)
    assert seen, "prompter should have been invoked"
    assert any("host(s) on A" in s for s in seen if s)
