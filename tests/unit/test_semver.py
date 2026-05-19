"""Unit tests for `maury.semver`."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from maury.semver import (
    SemverError,
    SemverTag,
    latest_semver_tag,
    parse_semver_tag,
)


def _git_available() -> bool:
    try:
        return subprocess.run(["git", "--version"], capture_output=True, check=False).returncode == 0
    except OSError:
        return False


# ---- parsing -------------------------------------------------------------


def test_parse_semver_tag_happy_path() -> None:
    assert parse_semver_tag("v1.2.3") == SemverTag(1, 2, 3)


def test_parse_semver_tag_strips_whitespace() -> None:
    assert parse_semver_tag("  v1.2.3  ") == SemverTag(1, 2, 3)


def test_parse_semver_tag_supports_zero_components() -> None:
    assert parse_semver_tag("v0.0.0") == SemverTag(0, 0, 0)


def test_parse_semver_tag_supports_large_components() -> None:
    assert parse_semver_tag("v100.200.300") == SemverTag(100, 200, 300)


@pytest.mark.parametrize(
    "bad",
    [
        "1.2.3",  # missing leading v
        "v1.2",  # missing patch
        "v1.2.3.4",  # too many components
        "v1.2.x",  # non-numeric
        "vmajor.minor.patch",
        "v1.2.3-rc.1",  # prerelease not supported in v1
        "",
        "v",
    ],
)
def test_parse_semver_tag_rejects_malformed(bad: str) -> None:
    with pytest.raises(SemverError):
        parse_semver_tag(bad)


# ---- bumping -------------------------------------------------------------


def test_bump_patch_increments_only_patch() -> None:
    assert SemverTag(1, 2, 3).bump_patch() == SemverTag(1, 2, 4)


def test_bump_minor_resets_patch() -> None:
    assert SemverTag(1, 2, 3).bump_minor() == SemverTag(1, 3, 0)


def test_bump_major_resets_minor_and_patch() -> None:
    assert SemverTag(1, 2, 3).bump_major() == SemverTag(2, 0, 0)


def test_semver_tag_string_renders_v_prefix() -> None:
    assert str(SemverTag(1, 2, 3)) == "v1.2.3"


def test_semver_tag_ordering_is_componentwise() -> None:
    """Sorted ascending: lower major < higher major; ties broken by minor, then patch."""
    tags = [SemverTag(1, 2, 0), SemverTag(1, 1, 99), SemverTag(2, 0, 0), SemverTag(1, 1, 100)]
    assert sorted(tags) == [
        SemverTag(1, 1, 99),
        SemverTag(1, 1, 100),
        SemverTag(1, 2, 0),
        SemverTag(2, 0, 0),
    ]


# ---- latest_semver_tag ---------------------------------------------------


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_latest_semver_tag_returns_highest(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.x"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("# r\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)
    for tag in ("v1.0.0", "v1.0.5", "v1.0.10", "v0.9.99"):
        subprocess.run(["git", "tag", tag], cwd=tmp_path, check=True)
    latest = latest_semver_tag(tmp_path)
    assert latest == SemverTag(1, 0, 10)


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_latest_semver_tag_none_when_no_tags(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    assert latest_semver_tag(tmp_path) is None


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_latest_semver_tag_skips_malformed_tags(tmp_path: Path) -> None:
    """Non-conforming tags don't block discovery of valid ones."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.x"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("# r\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)
    # Real semver tags plus a couple of non-conforming ones — note that
    # `git tag --list 'v*.*.*'` is wildcard-matched, so we use tags that
    # are caught by the glob but rejected by our parser.
    subprocess.run(["git", "tag", "v1.0.0"], cwd=tmp_path, check=True)
    subprocess.run(["git", "tag", "v1.0.alpha"], cwd=tmp_path, check=True)
    subprocess.run(["git", "tag", "v1.0.beta"], cwd=tmp_path, check=True)
    assert latest_semver_tag(tmp_path) == SemverTag(1, 0, 0)


def test_latest_semver_tag_returns_none_for_non_repo(tmp_path: Path) -> None:
    assert latest_semver_tag(tmp_path) is None
