"""Tests for the render engine.

Each test builds a small synthetic repo tree under tmp_path so the
test is hermetic (no dependency on `base-template/` real content).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from maury.ids import new_host_id, new_profile_id
from maury.manifest import HostSpec, Manifest, ProfileSpec
from maury.render import RenderError, apply_render, render

# ---- fixtures -------------------------------------------------------------


def _make_repo(tmp_path: Path, layout: dict[str, str]) -> Path:
    """Create a fake repo at tmp_path with the given file layout.

    Keys are relative paths; values are file contents. Parent directories
    are created automatically.
    """
    repo = tmp_path / "repo"
    for relpath, content in layout.items():
        target = repo / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    return repo


def _basic_manifest() -> tuple[Manifest, str, str]:
    """A minimal manifest: one profile (home) with one host (toad)."""
    pid = new_profile_id()
    hid = new_host_id()
    m = Manifest(
        version=2,
        profiles={pid: ProfileSpec(name="home")},
        hosts={hid: HostSpec(name="toad", profile=pid)},
    )
    return m, pid, hid


# ---- rendering --------------------------------------------------------


def test_render_with_only_base_claude_md(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, {"CLAUDE.md": "# base content\nrule one\n"})
    m, pid, hid = _basic_manifest()

    result = render(repo_paths={"base": repo}, manifest=m, profile_id=pid, host_id=hid)

    files = result.by_path()
    assert "CLAUDE.md" in files
    content = files["CLAUDE.md"].content.decode()
    assert "base content" in content
    assert "rule one" in content
    # Provenance header should mention base
    assert "base " in content
    # No file-overlay subdirs present, so no other files
    assert list(files) == ["CLAUDE.md"]


def test_render_concatenates_profile_fragment(tmp_path: Path) -> None:
    repo = _make_repo(
        tmp_path,
        {
            "CLAUDE.md": "# base\nbase rule\n",
            "profiles/home/CLAUDE.md.fragment": "## home\nhome rule\n",
        },
    )
    m, pid, hid = _basic_manifest()
    result = render(repo_paths={"base": repo}, manifest=m, profile_id=pid, host_id=hid)
    content = result.by_path()["CLAUDE.md"].content.decode()
    assert "base rule" in content
    assert "home rule" in content
    # Order: base before home
    assert content.index("base rule") < content.index("home rule")


def test_render_concatenates_host_overlay(tmp_path: Path) -> None:
    repo = _make_repo(
        tmp_path,
        {
            "CLAUDE.md": "base rule\n",
            "profiles/home/CLAUDE.md.fragment": "home rule\n",
            "profiles/home/hosts/toad/CLAUDE.md.fragment": "toad rule\n",
        },
    )
    m, pid, hid = _basic_manifest()
    result = render(repo_paths={"base": repo}, manifest=m, profile_id=pid, host_id=hid)
    content = result.by_path()["CLAUDE.md"].content.decode()
    assert "base rule" in content
    assert "home rule" in content
    assert "toad rule" in content
    # Provenance header lists all three layers
    assert "host-overlay:toad" in content


def test_render_provenance_header_includes_sha(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, {"CLAUDE.md": "x\n"})
    m, pid, hid = _basic_manifest()
    result = render(repo_paths={"base": repo}, manifest=m, profile_id=pid, host_id=hid)
    content = result.by_path()["CLAUDE.md"].content.decode()
    assert "sha " in content  # provenance header has sha excerpts


def test_render_skips_when_no_claude_md(tmp_path: Path) -> None:
    """No CLAUDE.md anywhere -> no CLAUDE.md in the render output."""
    repo = _make_repo(tmp_path, {"settings.json": "{}\n"})
    m, pid, hid = _basic_manifest()
    result = render(repo_paths={"base": repo}, manifest=m, profile_id=pid, host_id=hid)
    assert "CLAUDE.md" not in result.by_path()


# ---- file-overlay (agents/, skills/, bin/) ----------------------------


def test_render_agents_file_overlay(tmp_path: Path) -> None:
    repo = _make_repo(
        tmp_path,
        {
            "agents/security-reviewer.md": "base reviewer\n",
            "profiles/home/agents/security-reviewer.md": "home reviewer\n",
        },
    )
    m, pid, hid = _basic_manifest()
    result = render(repo_paths={"base": repo}, manifest=m, profile_id=pid, host_id=hid)
    files = result.by_path()
    # Later layer (home) wins
    assert files["agents/security-reviewer.md"].content.decode() == "home reviewer\n"
    # Warning emitted
    assert any("agents/security-reviewer.md" in w for w in result.warnings)


def test_render_skills_file_overlay_no_conflict(tmp_path: Path) -> None:
    repo = _make_repo(
        tmp_path,
        {
            "skills/base-skill/SKILL.md": "base only\n",
            "profiles/home/skills/home-skill/SKILL.md": "home only\n",
        },
    )
    m, pid, hid = _basic_manifest()
    result = render(repo_paths={"base": repo}, manifest=m, profile_id=pid, host_id=hid)
    files = result.by_path()
    assert "skills/base-skill/SKILL.md" in files
    assert "skills/home-skill/SKILL.md" in files
    # No warnings because nothing was shadowed
    assert not result.warnings


def test_render_bin_files_get_executable_mode(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, {"bin/claude-notify": "#!/bin/sh\necho hi\n"})
    m, pid, hid = _basic_manifest()
    result = render(repo_paths={"base": repo}, manifest=m, profile_id=pid, host_id=hid)
    files = result.by_path()
    assert files["bin/claude-notify"].mode == 0o755


# ---- inheritance --------------------------------------------------------


def test_render_walks_inheritance_chain(tmp_path: Path) -> None:
    """profile 'leaf' extends 'mid' extends 'root'; all three contribute CLAUDE.md."""
    repo = _make_repo(
        tmp_path,
        {
            "CLAUDE.md": "base content\n",
            "profiles/root/CLAUDE.md.fragment": "root content\n",
            "profiles/mid/CLAUDE.md.fragment": "mid content\n",
            "profiles/leaf/CLAUDE.md.fragment": "leaf content\n",
        },
    )
    pid_root = new_profile_id()
    pid_mid = new_profile_id()
    pid_leaf = new_profile_id()
    hid = new_host_id()
    m = Manifest(
        version=2,
        profiles={
            pid_root: ProfileSpec(name="root"),
            pid_mid: ProfileSpec(name="mid", extends=pid_root),
            pid_leaf: ProfileSpec(name="leaf", extends=pid_mid),
        },
        hosts={hid: HostSpec(name="h", profile=pid_leaf)},
    )
    result = render(repo_paths={"base": repo}, manifest=m, profile_id=pid_leaf, host_id=hid)
    content = result.by_path()["CLAUDE.md"].content.decode()
    # Base, then chain root -> mid -> leaf
    for snippet in ("base content", "root content", "mid content", "leaf content"):
        assert snippet in content
    assert (
        content.index("base content")
        < content.index("root content")
        < content.index("mid content")
        < content.index("leaf content")
    )


# ---- input validation --------------------------------------------------


def test_render_unknown_profile_raises(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, {"CLAUDE.md": "x\n"})
    m, _pid, hid = _basic_manifest()
    with pytest.raises(RenderError) as ei:
        render(
            repo_paths={"base": repo},
            manifest=m,
            profile_id="profile_nonexistentnonexistentnonexis00",
            host_id=hid,
        )
    assert "not in manifest" in str(ei.value)


def test_render_unknown_host_raises(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, {"CLAUDE.md": "x\n"})
    m, pid, _hid = _basic_manifest()
    with pytest.raises(RenderError) as ei:
        render(
            repo_paths={"base": repo},
            manifest=m,
            profile_id=pid,
            host_id="host_nonexistentnonexistentnonexisten00",
        )
    assert "not in manifest" in str(ei.value)


def test_render_host_profile_mismatch_raises(tmp_path: Path) -> None:
    """Passing a profile that doesn't match the host's assigned profile raises."""
    repo = _make_repo(tmp_path, {"CLAUDE.md": "x\n"})
    pid_a = new_profile_id()
    pid_b = new_profile_id()
    hid = new_host_id()
    m = Manifest(
        version=2,
        profiles={pid_a: ProfileSpec(name="a"), pid_b: ProfileSpec(name="b")},
        hosts={hid: HostSpec(name="h", profile=pid_a)},
    )
    with pytest.raises(RenderError) as ei:
        render(repo_paths={"base": repo}, manifest=m, profile_id=pid_b, host_id=hid)
    assert "bound to profile" in str(ei.value)


def test_render_unknown_profile_repo_raises(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, {"CLAUDE.md": "x\n"})
    m, pid, hid = _basic_manifest()
    with pytest.raises(RenderError) as ei:
        render(
            repo_paths={"base": repo},
            manifest=m,
            profile_id=pid,
            host_id=hid,
            profile_repo="ghost",
        )
    assert "ghost" in str(ei.value)


# ---- apply -------------------------------------------------------------


def test_apply_writes_files_to_target(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, {"CLAUDE.md": "hello\n"})
    m, pid, hid = _basic_manifest()
    result = render(repo_paths={"base": repo}, manifest=m, profile_id=pid, host_id=hid)
    target = tmp_path / "out"
    actions = apply_render(result, target, dry_run=False)
    assert (target / "CLAUDE.md").is_file()
    assert any("wrote" in a for a in actions)


def test_apply_dry_run_writes_nothing(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, {"CLAUDE.md": "hello\n"})
    m, pid, hid = _basic_manifest()
    result = render(repo_paths={"base": repo}, manifest=m, profile_id=pid, host_id=hid)
    target = tmp_path / "out"
    actions = apply_render(result, target, dry_run=True)
    assert not (target / "CLAUDE.md").exists()
    assert any("would write" in a for a in actions)


def test_apply_reports_unchanged_for_identical_content(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, {"CLAUDE.md": "hello\n"})
    m, pid, hid = _basic_manifest()
    result = render(repo_paths={"base": repo}, manifest=m, profile_id=pid, host_id=hid)
    target = tmp_path / "out"
    apply_render(result, target, dry_run=False)
    actions = apply_render(result, target, dry_run=False)  # second pass
    assert all("unchanged" in a for a in actions)


def test_apply_creates_subdirectories(tmp_path: Path) -> None:
    repo = _make_repo(
        tmp_path,
        {
            "CLAUDE.md": "x\n",
            "skills/sub/SKILL.md": "skill\n",
        },
    )
    m, pid, hid = _basic_manifest()
    result = render(repo_paths={"base": repo}, manifest=m, profile_id=pid, host_id=hid)
    target = tmp_path / "out"
    apply_render(result, target, dry_run=False)
    assert (target / "skills" / "sub" / "SKILL.md").is_file()


# ---- end-to-end against the real seed manifest -----------------------


def test_render_against_seed_template(tmp_path: Path) -> None:
    """Render the shipped base-template against its shipped manifest."""
    seed_root = Path(__file__).resolve().parents[2] / "base-template"
    if not (seed_root / ".meta" / "manifest.json").exists():
        pytest.skip("seed manifest not present")

    from maury.manifest import load_manifest

    m = load_manifest(seed_root / ".meta" / "manifest.json")
    # Pick toad as the test host
    hid = m.host_id_by_name("toad")
    assert hid is not None
    pid = m.hosts[hid].profile

    result = render(repo_paths={"base": seed_root}, manifest=m, profile_id=pid, host_id=hid)
    files = result.by_path()
    # Seed template ships a CLAUDE.md
    assert "CLAUDE.md" in files
    content = files["CLAUDE.md"].content.decode()
    # Content from base, profile, and host overlay should all appear
    assert "Code style" in content  # from base CLAUDE.md
    assert "Home context" in content  # from profile fragment
    assert "Host: toad" in content  # from host overlay
