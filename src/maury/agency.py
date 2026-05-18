"""`maury agency init` — generate an agency_id and bootstrap the base repo.

Per ADR-0039 §"`maury agency init`" + ADR-0050 + ADR-0051:

1. Generate a fresh UUID — the **agency_id**. This identifier is
   recorded in every layer's marker file from this moment forward and
   never changes.
2. Initialize a fresh git repo (locally) that will be the agency's
   `base`.
3. Write `.meta/maury-marker.json` with the canonical schema
   (`layer: "base"`, `agency_id`, `sublayers: []`).
4. Stage + commit the marker (pushing to a remote is out of v1 scope).

Idempotent: refuses if `.meta/maury-marker.json` already exists.
`--force` is the escape hatch for genuine resets; it requires explicit
acknowledgment because reusing the same repo root with a new
`agency_id` severs all existing host registrations.
"""

from __future__ import annotations

import json
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Final

# Filename for the per-repo marker. Mirrors ADR-0051 §"Schema".
MARKER_REL_PATH: Final[Path] = Path(".meta") / "maury-marker.json"

# Schema version this code writes. Bumps per ADR-0030.
SCHEMA_VERSION: Final[int] = 1


class AgencyInitError(RuntimeError):
    """Raised when agency init refuses to proceed."""


@dataclass(frozen=True)
class AgencyInitSummary:
    """Outcome of an agency_init run."""

    agency_id: str
    marker_path: Path
    git_initialized: bool
    git_commit_sha: str | None
    """The commit SHA of the marker commit, if a git repo was created and a commit was made."""

    force_used: bool


def _build_base_marker(agency_id: str) -> dict[str, object]:
    """Construct the canonical base marker payload."""
    return {
        "schema_version": SCHEMA_VERSION,
        "layer": "base",
        "agency_id": agency_id,
        "sublayers": [],
    }


def _write_marker(marker_path: Path, payload: dict[str, object]) -> None:
    """Write the marker file with a trailing newline (POSIX convention)."""
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.write_text(json.dumps(payload, indent=2) + "\n")


def _run_git(cmd: list[str], *, cwd: Path) -> tuple[int, str]:
    """Run a git command in cwd; return (rc, combined output)."""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30.0,
            check=False,
            cwd=str(cwd),
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return -1, str(e)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def init_agency(
    target_dir: Path,
    *,
    agency_id: str | None = None,
    force: bool = False,
    git_init: bool = True,
) -> AgencyInitSummary:
    """Initialize a new agency rooted at target_dir.

    Args:
        target_dir: Where the base repo will live (will be created if absent).
        agency_id: Force a specific UUID (tests only). Default: fresh uuid4.
        force: Allow re-init even if `.meta/maury-marker.json` exists.
        git_init: If True, run `git init`, stage, and commit the marker.

    Raises:
        AgencyInitError: if a marker already exists and --force is not set.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    marker_path = target_dir / MARKER_REL_PATH

    if marker_path.exists() and not force:
        raise AgencyInitError(
            f".meta/maury-marker.json already exists at {marker_path}. "
            f"Use --force to re-initialize (this generates a new agency_id "
            f"and severs all existing host registrations)."
        )

    new_agency_id = agency_id if agency_id is not None else str(uuid.uuid4())
    payload = _build_base_marker(new_agency_id)
    _write_marker(marker_path, payload)

    git_initialized = False
    commit_sha: str | None = None

    if git_init:
        # Only run `git init` if the target dir isn't already a git repo.
        # Reuse an existing repo (common during `--force` reset of a
        # well-established working tree).
        rc, _ = _run_git(["git", "rev-parse", "--git-dir"], cwd=target_dir)
        is_repo = rc == 0
        if not is_repo:
            rc, out = _run_git(["git", "init", "--quiet"], cwd=target_dir)
            if rc != 0:
                raise AgencyInitError(f"git init failed: {out}")
        git_initialized = True

        rc, out = _run_git(["git", "add", str(MARKER_REL_PATH)], cwd=target_dir)
        if rc != 0:
            raise AgencyInitError(f"git add failed: {out}")

        # Commit. If nothing changed (force re-init wrote identical bytes),
        # `git commit` exits non-zero with "nothing to commit"; treat as OK.
        rc, out = _run_git(
            [
                "git",
                "-c",
                "user.name=maury",
                "-c",
                "user.email=maury@example.invalid",
                "commit",
                "-m",
                f"chore(maury): initialize agency {new_agency_id}",
            ],
            cwd=target_dir,
        )
        if rc == 0:
            rc2, sha = _run_git(["git", "rev-parse", "HEAD"], cwd=target_dir)
            if rc2 == 0:
                commit_sha = sha.strip() or None
        # else: "nothing to commit" — leave commit_sha as None.

    return AgencyInitSummary(
        agency_id=new_agency_id,
        marker_path=marker_path,
        git_initialized=git_initialized,
        git_commit_sha=commit_sha,
        force_used=force,
    )
