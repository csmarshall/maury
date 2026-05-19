"""`maury repo bump --minor|--major` and the auto-patch-bump post-commit hook.

Per ADR-0038 step 6, `maury repo init` installs a git `post-commit` hook
in the rules repo that auto-increments the patch component on every
commit. Major and minor bumps remain explicit curator actions via this
command.

The hook script is POSIX sh, marked with `# maury-managed` so
`maury uninstall` (or a manual sweep) can strip it cleanly.
"""

from __future__ import annotations

import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from maury.agency import _run_git as run_git
from maury.semver import SemverTag, latest_semver_tag, parse_semver_tag

POST_COMMIT_HOOK_REL: Final[Path] = Path(".git") / "hooks" / "post-commit"
MARKER: Final[str] = "# maury-managed: auto-patch-bump"

POST_COMMIT_HOOK_SCRIPT: Final[str] = """\
#!/bin/sh
# maury-managed: auto-patch-bump
#
# Installed by `maury repo init` per ADR-0038 step 6. On every commit,
# finds the highest existing v<major>.<minor>.<patch> tag, bumps the
# patch component, and tags HEAD with the new version. Skips when no
# baseline tag exists yet (run `maury repo init` first to establish one).

set -eu

# Find the highest semver tag.
latest=$(git tag --list 'v*.*.*' --sort=-v:refname | head -n1)
if [ -z "$latest" ]; then
    exit 0  # no baseline; nothing to bump.
fi

# Parse v<maj>.<min>.<patch>.
ver=${latest#v}
maj=$(printf '%s' "$ver" | cut -d. -f1)
min=$(printf '%s' "$ver" | cut -d. -f2)
patch=$(printf '%s' "$ver" | cut -d. -f3)

# Validate the three components are integers (skip if not — corrupt tag).
case "$maj$min$patch" in
    ''|*[!0-9]*) exit 0 ;;
esac

next_patch=$((patch + 1))
new_tag="v${maj}.${min}.${next_patch}"

# Defensive: skip if the new tag somehow already exists.
if git tag --list "$new_tag" | grep -qx "$new_tag"; then
    exit 0
fi

git tag "$new_tag" HEAD
"""


class RepoBumpError(RuntimeError):
    """Raised when a bump or hook install cannot proceed."""


@dataclass(frozen=True)
class BumpSummary:
    """Outcome of a repo bump run."""

    repo_dir: Path
    prior_tag: SemverTag
    new_tag: SemverTag
    kind: str
    """One of 'major', 'minor', 'patch'."""


@dataclass(frozen=True)
class InstallHookSummary:
    """Outcome of install_post_commit_hook."""

    hook_path: Path
    overwrote_existing: bool


def install_post_commit_hook(repo_dir: Path, *, force: bool = False) -> InstallHookSummary:
    """Install the auto-patch-bump hook at `.git/hooks/post-commit`.

    Refuses if a non-maury hook is already present, unless `force` is set.
    A pre-existing maury-managed hook is silently overwritten (idempotent
    re-init is a normal flow).
    """
    git_dir = repo_dir / ".git"
    if not git_dir.is_dir():
        raise RepoBumpError(f"not a git repo: {repo_dir}")

    hook_path = repo_dir / POST_COMMIT_HOOK_REL
    overwrote = False
    if hook_path.exists():
        existing = hook_path.read_text()
        if MARKER in existing:
            overwrote = True  # idempotent overwrite of our own hook
        elif not force:
            raise RepoBumpError(
                f"a post-commit hook already exists at {hook_path} that is not "
                f"maury-managed. Re-run with --force to overwrite (this discards "
                f"the existing hook), or remove it manually first."
            )
        else:
            overwrote = True

    hook_path.parent.mkdir(parents=True, exist_ok=True)
    hook_path.write_text(POST_COMMIT_HOOK_SCRIPT)
    # chmod +x for the owner; group/other read-execute too (mirrors git's defaults).
    mode = hook_path.stat().st_mode
    hook_path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return InstallHookSummary(hook_path=hook_path, overwrote_existing=overwrote)


def bump(
    repo_dir: Path,
    *,
    kind: str,
) -> BumpSummary:
    """Compute the next tag and create it via `git tag`.

    Args:
        repo_dir: the rules-repo working tree.
        kind: 'major' or 'minor'. 'patch' bumps are the post-commit
            hook's job, not this command's.

    Raises:
        RepoBumpError if no baseline tag exists, the tag already exists,
        or git refuses.
    """
    if kind not in ("major", "minor"):
        raise RepoBumpError(f"bump kind must be 'major' or 'minor'; got {kind!r}")

    rc, _ = run_git(["git", "rev-parse", "--git-dir"], cwd=repo_dir)
    if rc != 0:
        raise RepoBumpError(f"not a git repo: {repo_dir}")

    prior = latest_semver_tag(repo_dir)
    if prior is None:
        raise RepoBumpError(
            f"no v<major>.<minor>.<patch> tag in {repo_dir}. Run `maury repo init` to establish a baseline first."
        )

    new_tag = prior.bump_major() if kind == "major" else prior.bump_minor()

    # Refuse if it already exists (e.g., concurrent curator action).
    rc, _ = run_git(["git", "rev-parse", f"refs/tags/{new_tag}"], cwd=repo_dir)
    if rc == 0:
        raise RepoBumpError(
            f"tag {new_tag} already exists in {repo_dir}; not overwriting. "
            f"If this is a recovery operation, delete the tag manually first."
        )

    rc, out = run_git(["git", "tag", str(new_tag)], cwd=repo_dir)
    if rc != 0:
        raise RepoBumpError(f"git tag {new_tag} failed: {out}")

    return BumpSummary(
        repo_dir=repo_dir,
        prior_tag=prior,
        new_tag=new_tag,
        kind=kind,
    )


def parse_existing_tag_for_summary(tag: str) -> SemverTag:
    """Public re-export for callers wanting the same parsing logic."""
    return parse_semver_tag(tag)


__all__ = [
    "MARKER",
    "POST_COMMIT_HOOK_REL",
    "POST_COMMIT_HOOK_SCRIPT",
    "BumpSummary",
    "InstallHookSummary",
    "RepoBumpError",
    "bump",
    "install_post_commit_hook",
]
