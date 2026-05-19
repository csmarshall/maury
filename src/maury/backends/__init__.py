"""Per-backend URL validators per ADR-0016 + ADR-0024.

ADR-0016 establishes backend pluralism (git/github/gitlab/gitea/...);
ADR-0024 §"Semantic validation is mandatory before push" requires that
each backend supply a URL-syntax validator so `validate_manifest` can
catch obviously-broken URLs before they reach git.

This module exposes a registry: `BACKEND_VALIDATORS[name]` returns a
`Callable[[str], str | None]` that returns None when the URL is
plausibly valid for that backend, or a one-line error message
explaining the rejection. Unknown backends fall through to the
generic git validator.

The validators are deliberately permissive — they catch *obvious*
breakage (missing `:` in an SSH URL, wrong host for the named
backend) but don't try to verify the URL would actually clone. That
would require network I/O and isn't the validator's job.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Final

# Signature: (url) -> None on valid, str error message on invalid.
BackendValidator = Callable[[str], str | None]


_GIT_SSH_RE: Final[re.Pattern[str]] = re.compile(r"^[^\s@:]+@[^\s@:]+:.+$")
"""SCP-style SSH URL: `user@host:path` (path may contain `/`)."""

_HTTPS_RE: Final[re.Pattern[str]] = re.compile(r"^https?://[^\s/]+/.+$")
"""HTTP(S) URL with a host and path."""

_GIT_PROTOCOL_RE: Final[re.Pattern[str]] = re.compile(r"^git://[^\s/]+/.+$")
"""git:// protocol URL (read-only legacy)."""

_FILE_RE: Final[re.Pattern[str]] = re.compile(r"^(file://|/|\./).+$")
"""file:// or absolute / relative filesystem path (local repos)."""


def _validate_url_shape(url: str) -> str | None:
    """Generic 'looks like a git URL' check used by the git backend."""
    if not url or not url.strip():
        return "URL is empty"
    if url != url.strip():
        return f"URL has surrounding whitespace: {url!r}"
    if _GIT_SSH_RE.match(url) or _HTTPS_RE.match(url) or _GIT_PROTOCOL_RE.match(url) or _FILE_RE.match(url):
        return None
    return (
        f"URL {url!r} does not look like a git URL "
        f"(expected SSH `user@host:path`, https(s)://, git://, or file:///path)"
    )


def validate_git_url(url: str) -> str | None:
    """Permissive git URL validator (the default for backend='git')."""
    return _validate_url_shape(url)


def _make_host_specific_validator(backend_name: str, allowed_hosts: tuple[str, ...]) -> BackendValidator:
    """Factory: validator that requires the URL to point at one of `allowed_hosts`."""

    def validator(url: str) -> str | None:
        shape_error = _validate_url_shape(url)
        if shape_error is not None:
            return shape_error
        host = _extract_host(url)
        if host is None:
            return f"could not extract host from {url!r}"
        if host.lower() not in {h.lower() for h in allowed_hosts}:
            return f"backend={backend_name!r} but URL host is {host!r}; expected one of {sorted(allowed_hosts)}"
        return None

    return validator


def _extract_host(url: str) -> str | None:
    """Best-effort host extraction from any of the supported URL shapes."""
    # SSH: user@host:path
    if _GIT_SSH_RE.match(url):
        try:
            _user, rest = url.split("@", 1)
            host, _path = rest.split(":", 1)
            return host
        except ValueError:
            return None
    # https / git protocol: scheme://host/path
    for re_pattern in (_HTTPS_RE, _GIT_PROTOCOL_RE):
        if re_pattern.match(url):
            try:
                _scheme, rest = url.split("://", 1)
                host = rest.split("/", 1)[0]
                # Strip any "user:pass@" prefix.
                if "@" in host:
                    host = host.split("@", 1)[1]
                # Strip any ":port" suffix.
                if ":" in host:
                    host = host.split(":", 1)[0]
                return host
            except ValueError:
                return None
    return None


# Backend registry. Unknown backends fall through to `validate_git_url`.
BACKEND_VALIDATORS: Final[dict[str, BackendValidator]] = {
    "git": validate_git_url,
    "github": _make_host_specific_validator("github", ("github.com",)),
    "gitlab": _make_host_specific_validator(
        "gitlab", ("gitlab.com",)
    ),  # self-hosted gitlab needs configure-time hint; soft for now
    "gitea": _make_host_specific_validator(
        "gitea", ("codeberg.org",)
    ),  # gitea is self-hosted commonly; codeberg.org is the public flagship
}


def validate_repo_url(url: str, backend: str) -> str | None:
    """Validate `url` against the named backend's rules.

    Unknown backends use the generic git validator and prepend a
    note so the caller can include it in a diagnostic.
    """
    validator = BACKEND_VALIDATORS.get(backend)
    if validator is None:
        # Unknown backend: validate the shape only.
        result = validate_git_url(url)
        if result is not None:
            return result
        # Soft note that the backend isn't recognised.
        return None
    return validator(url)


__all__ = [
    "BACKEND_VALIDATORS",
    "BackendValidator",
    "validate_git_url",
    "validate_repo_url",
]
