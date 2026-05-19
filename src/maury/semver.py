"""Semver parsing + bumping for rules-repo tags.

Per ADR-0038, rules repos use semver tags (`v<MAJOR>.<MINOR>.<PATCH>`).
- `maury repo init` creates the initial tag and installs a post-commit
  hook that auto-bumps the patch on every commit.
- `maury repo bump --minor|--major` is the curator's explicit ladder
  for the non-patch bumps; the hook does not touch them.

This module owns the parsing + bumping logic. Git interaction lives in
repo_init.py and repo_bump.py.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Final

SEMVER_TAG_PATTERN: Final[re.Pattern[str]] = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


class SemverError(ValueError):
    """Raised when a tag string is not a valid v-prefixed semver."""


@dataclass(frozen=True, order=True)
class SemverTag:
    """Parsed `v<major>.<minor>.<patch>` tag."""

    major: int
    minor: int
    patch: int

    def __str__(self) -> str:
        return f"v{self.major}.{self.minor}.{self.patch}"

    def bump_patch(self) -> SemverTag:
        return SemverTag(self.major, self.minor, self.patch + 1)

    def bump_minor(self) -> SemverTag:
        """Bump minor; reset patch to 0 per semver convention."""
        return SemverTag(self.major, self.minor + 1, 0)

    def bump_major(self) -> SemverTag:
        """Bump major; reset minor + patch to 0 per semver convention."""
        return SemverTag(self.major + 1, 0, 0)


def parse_semver_tag(tag: str) -> SemverTag:
    """Parse `vMAJOR.MINOR.PATCH`. Raises SemverError on malformed input."""
    m = SEMVER_TAG_PATTERN.match(tag.strip())
    if m is None:
        raise SemverError(f"not a v-prefixed semver tag: {tag!r}")
    return SemverTag(int(m.group(1)), int(m.group(2)), int(m.group(3)))


def latest_semver_tag(repo_dir: Path) -> SemverTag | None:
    """Return the highest semver tag in the repo, or None if none exist."""
    try:
        proc = subprocess.run(
            ["git", "tag", "--list", "v*.*.*", "--sort=-v:refname"],
            capture_output=True,
            text=True,
            timeout=30.0,
            check=False,
            cwd=str(repo_dir),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    for raw in proc.stdout.splitlines():
        candidate = raw.strip()
        if not candidate:
            continue
        try:
            return parse_semver_tag(candidate)
        except SemverError:
            continue
    return None


__all__ = [
    "SEMVER_TAG_PATTERN",
    "SemverError",
    "SemverTag",
    "latest_semver_tag",
    "parse_semver_tag",
]
