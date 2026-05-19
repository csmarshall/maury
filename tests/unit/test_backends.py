"""Unit tests for `maury.backends` URL validators."""

from __future__ import annotations

import pytest

from maury.backends import (
    BACKEND_VALIDATORS,
    _extract_host,
    validate_git_url,
    validate_repo_url,
)

# ---- generic git URL shapes ----------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "git@github.com:owner/repo.git",
        "git@codeberg.org:jdoe/repo.git",
        "https://github.com/owner/repo.git",
        "https://gitlab.com/owner/repo",
        "http://internal.example.com/repo.git",
        "git://git.savannah.gnu.org/coreutils.git",
        "file:///home/user/repos/maury-base",
        "/home/user/repos/local",
        "./relative/path",
    ],
)
def test_validate_git_url_accepts_canonical_shapes(url: str) -> None:
    assert validate_git_url(url) is None


@pytest.mark.parametrize(
    "url",
    [
        "",
        "   ",
        "not a url at all",
        "github.com/owner/repo",  # missing protocol or SSH `:`
        "git@",
        "://no-scheme",
    ],
)
def test_validate_git_url_rejects_malformed(url: str) -> None:
    assert validate_git_url(url) is not None


def test_validate_git_url_rejects_whitespace_padding() -> None:
    assert validate_git_url(" git@github.com:o/r.git ") is not None


# ---- host-specific validators --------------------------------------------


def test_github_validator_requires_github_host() -> None:
    accept = "git@github.com:owner/repo.git"
    assert BACKEND_VALIDATORS["github"](accept) is None
    reject = "git@gitlab.com:owner/repo.git"
    err = BACKEND_VALIDATORS["github"](reject)
    assert err is not None
    assert "github.com" in err


def test_github_validator_accepts_https() -> None:
    assert BACKEND_VALIDATORS["github"]("https://github.com/o/r.git") is None


def test_gitlab_validator_requires_gitlab_host() -> None:
    assert BACKEND_VALIDATORS["gitlab"]("https://gitlab.com/o/r") is None
    err = BACKEND_VALIDATORS["gitlab"]("git@github.com:o/r.git")
    assert err is not None


def test_gitea_validator_accepts_codeberg() -> None:
    assert BACKEND_VALIDATORS["gitea"]("git@codeberg.org:jdoe/repo.git") is None


# ---- validate_repo_url dispatch ------------------------------------------


def test_validate_repo_url_unknown_backend_falls_through_to_shape_check() -> None:
    """A backend not in the registry uses the generic shape validator."""
    assert validate_repo_url("git@x:o/r.git", backend="unknown-backend") is None
    assert validate_repo_url("garbage", backend="unknown-backend") is not None


def test_validate_repo_url_known_backend_uses_registered_validator() -> None:
    err = validate_repo_url("git@gitlab.com:o/r.git", backend="github")
    assert err is not None
    assert "github.com" in err


# ---- _extract_host -------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected_host"),
    [
        ("git@github.com:owner/repo.git", "github.com"),
        ("https://github.com/owner/repo.git", "github.com"),
        ("https://user@gitlab.com/owner/repo", "gitlab.com"),
        ("https://gitlab.com:8443/owner/repo", "gitlab.com"),
        ("git://git.example.org/foo.git", "git.example.org"),
    ],
)
def test_extract_host(url: str, expected_host: str) -> None:
    assert _extract_host(url) == expected_host


def test_extract_host_returns_none_for_file_paths() -> None:
    assert _extract_host("/home/user/repo") is None
    assert _extract_host("file:///home/user/repo") is None
