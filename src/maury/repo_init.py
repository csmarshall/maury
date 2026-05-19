"""`maury repo init` — bootstrap a new `rules` repo per ADR-0038.

Per ADR-0038 §"`maury repo init`", this is an **owner-only**,
**idempotent** bootstrap command for a rules repo. It:

1. Refuses if either `.meta/maury-governance.json` or
   `.meta/maury-marker.json` already exists, unless `--force` is set.
2. Writes `.meta/maury-governance.json` with `owners` + `pr_target`
   (and optional `pr_standards`, `min_reviewers`).
3. Writes `.meta/maury-marker.json` with `layer: rules` + the current
   `agency_id`.
4. Creates an initial semver tag (`v1.0.0` by default).
5. Stages + commits the new `.meta/*.json` files.

Out of v1 scope (followups):
- Post-commit hook for auto-incrementing patch versions on each
  commit (ADR-0038 step 6). Tracked as a followup.
- Owner-only access validation against the agency's manifest
  (requires reading the agency's marker graph). Tracked as a
  followup; in practice users run this against their own repos.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from maury.agency import _run_git as run_git

# Filenames produced by repo_init. Mirrors the ADR-0038 schemas.
MARKER_REL_PATH: Final[Path] = Path(".meta") / "maury-marker.json"
GOVERNANCE_REL_PATH: Final[Path] = Path(".meta") / "maury-governance.json"

MARKER_SCHEMA_VERSION: Final[int] = 1
GOVERNANCE_SCHEMA_VERSION: Final[int] = 1
DEFAULT_INITIAL_TAG: Final[str] = "v1.0.0"
DEFAULT_PR_TARGET: Final[str] = "main"


class RepoInitError(RuntimeError):
    """Raised when repo init refuses to proceed."""


@dataclass(frozen=True)
class RepoInitSummary:
    """Outcome of a repo_init run."""

    target_dir: Path
    agency_id: str
    governance_path: Path
    marker_path: Path
    owners: tuple[str, ...] = field(default_factory=tuple)
    pr_target: str = DEFAULT_PR_TARGET
    pr_standards: str | None = None
    min_reviewers: int | None = None
    initial_tag: str | None = None
    """The tag created (e.g., 'v1.0.0'), or None if --no-tag was passed."""

    tag_already_existed: bool = False
    """True if the requested initial_tag already pointed somewhere."""

    git_commit_sha: str | None = None
    force_used: bool = False
    post_commit_hook_installed: bool = False
    """True iff the auto-patch-bump hook was written to .git/hooks/post-commit."""


def _build_marker(agency_id: str) -> dict[str, object]:
    return {
        "schema_version": MARKER_SCHEMA_VERSION,
        "layer": "rules",
        "agency_id": agency_id,
        "sublayers": [],
    }


def _build_governance(
    owners: list[str],
    pr_target: str,
    pr_standards: str | None,
    min_reviewers: int | None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": GOVERNANCE_SCHEMA_VERSION,
        "owners": owners,
        "pr_target": pr_target,
    }
    if pr_standards is not None:
        payload["pr_standards"] = pr_standards
    if min_reviewers is not None:
        payload["min_reviewers"] = min_reviewers
    return payload


def _write_json(path: Path, payload: dict[str, object]) -> None:
    """Write a json file with a trailing newline."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")


def _tag_exists(target_dir: Path, tag: str) -> bool:
    """True iff the given tag already exists in the repo."""
    rc, _ = run_git(["git", "rev-parse", f"refs/tags/{tag}"], cwd=target_dir)
    return rc == 0


def init_repo(
    target_dir: Path,
    *,
    agency_id: str,
    owners: list[str],
    pr_target: str = DEFAULT_PR_TARGET,
    pr_standards: str | None = None,
    min_reviewers: int | None = None,
    initial_tag: str | None = DEFAULT_INITIAL_TAG,
    force: bool = False,
    git_init: bool = True,
    install_hook: bool = True,
) -> RepoInitSummary:
    """Initialize a new rules repo rooted at target_dir.

    Args:
        target_dir: Where the rules repo lives (will be created if absent).
        agency_id: The agency's UUID (recorded in the marker).
        owners: At least one identity (email/handle) for PR review.
        pr_target: Branch/URL where PRs land. Default "main".
        pr_standards: Free-form description of PR requirements.
        min_reviewers: Reviewer-count expectation.
        initial_tag: Initial semver tag, or None to skip tagging.
        force: Allow re-init if either .meta/maury-marker.json or
            .meta/maury-governance.json exists. Also forces overwriting
            a pre-existing non-maury post-commit hook.
        git_init: Run git init + stage + commit. Skip with False.
        install_hook: Install the auto-patch-bump post-commit hook
            (ADR-0038 step 6). Requires git_init=True (no-op otherwise).

    Raises:
        RepoInitError if validation fails or git operations fail.
    """
    if not owners:
        raise RepoInitError("at least one --owner is required")
    if not agency_id.strip():
        raise RepoInitError("--agency-id must be non-empty")

    target_dir.mkdir(parents=True, exist_ok=True)
    marker_path = target_dir / MARKER_REL_PATH
    governance_path = target_dir / GOVERNANCE_REL_PATH

    if not force:
        if marker_path.exists():
            raise RepoInitError(
                f".meta/maury-marker.json already exists at {marker_path}. Use --force to re-initialize."
            )
        if governance_path.exists():
            raise RepoInitError(
                f".meta/maury-governance.json already exists at {governance_path}. Use --force to re-initialize."
            )

    _write_json(governance_path, _build_governance(owners, pr_target, pr_standards, min_reviewers))
    _write_json(marker_path, _build_marker(agency_id))

    tag_already_existed = False
    commit_sha: str | None = None
    tag_written: str | None = None

    if git_init:
        rc, _ = run_git(["git", "rev-parse", "--git-dir"], cwd=target_dir)
        if rc != 0:
            rc, out = run_git(["git", "init", "--quiet"], cwd=target_dir)
            if rc != 0:
                raise RepoInitError(f"git init failed: {out}")

        for rel in (GOVERNANCE_REL_PATH, MARKER_REL_PATH):
            rc, out = run_git(["git", "add", str(rel)], cwd=target_dir)
            if rc != 0:
                raise RepoInitError(f"git add {rel} failed: {out}")

        rc, out = run_git(
            [
                "git",
                "-c",
                "user.name=maury",
                "-c",
                "user.email=maury@example.invalid",
                "commit",
                "-m",
                f"chore(maury): initialize rules repo against agency {agency_id}",
            ],
            cwd=target_dir,
        )
        if rc == 0:
            rc2, sha = run_git(["git", "rev-parse", "HEAD"], cwd=target_dir)
            if rc2 == 0:
                commit_sha = sha.strip() or None
        # else: nothing-to-commit — fine on --force re-init.

        if initial_tag is not None:
            if _tag_exists(target_dir, initial_tag):
                tag_already_existed = True
            else:
                rc, out = run_git(["git", "tag", initial_tag], cwd=target_dir)
                if rc != 0:
                    raise RepoInitError(f"git tag {initial_tag} failed: {out}")
                tag_written = initial_tag

    hook_installed = False
    if git_init and install_hook:
        from maury.repo_bump import RepoBumpError, install_post_commit_hook

        try:
            install_post_commit_hook(target_dir, force=force)
            hook_installed = True
        except RepoBumpError as exc:
            raise RepoInitError(str(exc)) from exc

    return RepoInitSummary(
        target_dir=target_dir,
        agency_id=agency_id,
        governance_path=governance_path,
        marker_path=marker_path,
        owners=tuple(owners),
        pr_target=pr_target,
        pr_standards=pr_standards,
        min_reviewers=min_reviewers,
        initial_tag=tag_written,
        tag_already_existed=tag_already_existed,
        git_commit_sha=commit_sha,
        force_used=force,
        post_commit_hook_installed=hook_installed,
    )


__all__ = [
    "DEFAULT_INITIAL_TAG",
    "DEFAULT_PR_TARGET",
    "GOVERNANCE_REL_PATH",
    "GOVERNANCE_SCHEMA_VERSION",
    "MARKER_REL_PATH",
    "MARKER_SCHEMA_VERSION",
    "RepoInitError",
    "RepoInitSummary",
    "init_repo",
]
