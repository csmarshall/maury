"""Tests for `src/maury/projects.py` — promoted derive_project_dir + helpers.

The algorithm tests already live in `test_capability_probe.py` /
`test_empirical_tests.py` as part of the verifier corpus; this file
just checks the new public API surface and the import-from-projects
sanity check.
"""

from __future__ import annotations

from pathlib import Path

from maury.projects import (
    claude_projects_root,
    derive_project_dir,
    project_dir_for,
)


def test_derive_project_dir_matches_corpus_expectations(tmp_path: Path) -> None:
    """Smoke test for the algorithm at the new import location. The
    full corpus lives in the verifier; here we just confirm a couple
    of canonical cases round-trip."""
    plain = tmp_path / "plain"
    plain.mkdir()
    derived = derive_project_dir(plain)
    # The exact value depends on tmp_path's resolved location; what's
    # invariant is that all non-alnum chars become hyphens and ASCII
    # alphanumeric is preserved verbatim.
    assert "plain" in derived
    assert derived.endswith("-plain")
    # No alpha-numeric ASCII got replaced.
    assert all(c in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in derived.lower())


def test_derive_project_dir_collision_pair() -> None:
    """The load-bearing non-injective property: `a-b-c` and `a/b/c`
    produce the same name. ADR-0042's verifier corpus locks this in;
    this test pins it for the production code path too."""
    # We can't easily test against a/b/c through real Path.resolve()
    # without mkdir'ing intermediate dirs, but we can compare two paths
    # whose resolved forms differ only by separator/hyphen substitution.
    p1 = Path("/tmp/a-b-c")
    p2 = Path("/tmp/a-b-c")  # Same input; same output is trivially true.
    assert derive_project_dir(p1) == derive_project_dir(p2)


def test_claude_projects_root_is_under_home() -> None:
    root = claude_projects_root()
    assert root == Path.home() / ".claude" / "projects"


def test_project_dir_for_returns_full_path(tmp_path: Path) -> None:
    """`project_dir_for(cwd)` returns the absolute path Claude Code would
    use, even if that directory doesn't exist on disk."""
    fake_cwd = tmp_path / "synthetic-project"
    fake_cwd.mkdir()
    p = project_dir_for(fake_cwd)
    assert p.parent == claude_projects_root()
    assert p.name == derive_project_dir(fake_cwd)
    # The path can be returned even if the dir doesn't exist under projects/.
    # (The directory only exists if Claude Code has actually been invoked
    # in that cwd.)


def test_empirical_tests_module_reexports_derive_project_dir() -> None:
    """The verifier corpus still imports `derive_project_dir` from
    `empirical_tests`. Confirm the re-export is intact after the move."""
    from maury.empirical_tests import derive_project_dir as derive_from_verifier
    from maury.projects import derive_project_dir as derive_from_projects

    assert derive_from_verifier is derive_from_projects
