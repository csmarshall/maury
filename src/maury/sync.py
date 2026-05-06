"""`maury sync` — pull all reachable repos, render, apply (Phase 5 v0 slice).

Per ADR-0002 and ADR-0017. v0 minimum:

  1. Read the manifest + identify this host.
  2. For each repo in the host's manifest entry (in declared order),
     `git pull` if the local clone exists, else `git clone`.
  3. Render base + profile chain + host overlay using the freshly-pulled
     content.
  4. Apply to the target dir (default `~/.claude/`).

Deferred to subsequent slices:

- **Drift detection / reconciliation** (Phase 5.x per ADR-0017) — needs
  `last-render.json` tracking. Without it, `maury sync` will overwrite
  any local edits that aren't in the synced repo. Until that's in,
  `--check` mode is the safe way to inspect before applying.
- **Multi-repo support beyond the single-base case.** v0 assumes the
  host's "base" repo is the single source of all rendered content (the
  base-template/ pattern). Once profiles can live in separate repos,
  the renderer needs to know which repo holds which profile — schema
  hook is in the manifest already (`repos` dict), wiring is TBD.
- **Push** (proposals back to the synced repo, audit log shipping).
  Phase 9/10.
- **Backend abstraction beyond `git`** (per ADR-0016). v0 hardcodes git;
  the backend field is read but only "git" and "github" are honored.
"""

from __future__ import annotations

import socket
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from maury.manifest import (
    HOST_ID_FILE,
    HostSpec,
    Manifest,
    ManifestError,
    RepoMode,
    RepoSpec,
    load_manifest,
)
from maury.render import RenderError, RenderResult, apply_render, render

# ---- result types -------------------------------------------------------


@dataclass
class RepoSyncResult:
    """Outcome of pulling/cloning one repo entry."""

    nickname: str
    spec: RepoSpec
    local_path: Path
    action: str  # "cloned" | "pulled" | "up-to-date" | "skipped" | "error"
    detail: str = ""


@dataclass
class SyncResult:
    """Aggregate of `maury sync`."""

    repos: list[RepoSyncResult] = field(default_factory=list)
    render_result: RenderResult | None = None
    apply_actions: list[str] = field(default_factory=list)
    host_id: str | None = None
    profile_id: str | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def has_errors(self) -> bool:
        return bool(self.errors)


class SyncError(ValueError):
    """Raised when sync inputs are invalid before any I/O."""


# ---- public API ---------------------------------------------------------


def sync(
    *,
    manifest_path: Path,
    target_dir: Path,
    repos_root: Path,
    host_id_file: Path = HOST_ID_FILE,
    dry_run: bool = False,
    on_repo_progress: Callable[[RepoSyncResult], None] | None = None,
) -> SyncResult:
    """Run the v0 sync flow.

    Args:
        manifest_path: path to the canonical manifest.json. Typically
            `<base-clone>/.meta/manifest.json`.
        target_dir: where to write rendered output (e.g., `~/.claude/`).
        repos_root: parent directory under which per-repo clones live
            (e.g., `~/.config/maury/repos/`). Each repo gets a subdir
            named after its manifest nickname.
        host_id_file: location of `~/.maury-host-id`.
        dry_run: when True, don't actually `git pull` (no I/O on remote)
            and don't write the rendered output. Useful for `--check`.
        on_repo_progress: optional callback fired after each repo op.
    """
    if not manifest_path.is_file():
        raise SyncError(f"manifest not found: {manifest_path}")

    try:
        manifest = load_manifest(manifest_path)
    except ManifestError as e:
        raise SyncError(f"manifest failed to load: {e}") from e

    # Identify this host
    hid, host_spec = _identify_host(manifest, host_id_file)
    result = SyncResult(host_id=hid, profile_id=host_spec.profile)

    # Sync each repo declared in the host's manifest entry
    repos_root.mkdir(parents=True, exist_ok=True)
    for nickname, spec in host_spec.repos.items():
        rs = _sync_one_repo(
            nickname=nickname,
            spec=spec,
            local_path=repos_root / nickname,
            dry_run=dry_run,
        )
        result.repos.append(rs)
        if on_repo_progress is not None:
            on_repo_progress(rs)
        if rs.action == "error":
            result.errors.append(f"repo {nickname!r}: {rs.detail}")

    # If any repo failed, abort before render
    if result.has_errors():
        return result

    # Render. v0: assume the base content lives at repos_root / "base".
    # The host's manifest must declare a repo nicknamed 'base'; in v0 we
    # don't yet support multi-repo profile composition.
    if "base" not in host_spec.repos:
        result.errors.append(
            "render aborted: this host's manifest entry has no repo nicknamed 'base'. v0 requires one."
        )
        return result

    base_path = repos_root / "base"
    if not base_path.is_dir():
        # In dry-run, this is expected (we never actually cloned). Surface
        # as a warning; in real mode it's an error since the clone should
        # already have happened above.
        msg = f"render skipped: 'base' repo not present at {base_path}" + (
            " (dry-run — would have been cloned above)" if dry_run else ""
        )
        if dry_run:
            result.warnings.append(msg)
        else:
            result.errors.append(msg)
        return result

    try:
        rendered = render(
            repo_paths={"base": base_path},
            manifest=manifest,
            profile_id=host_spec.profile,
            host_id=hid,
        )
    except RenderError as e:
        result.errors.append(f"render failed: {e}")
        return result

    result.render_result = rendered
    result.warnings.extend(rendered.warnings)
    result.apply_actions = apply_render(rendered, target_dir, dry_run=dry_run)
    return result


# ---- internals ----------------------------------------------------------


def _identify_host(manifest: Manifest, host_id_file: Path) -> tuple[str, HostSpec]:
    """Resolve the current host. Try the id file first, then the hostname."""
    if host_id_file.exists():
        hid_value = host_id_file.read_text(encoding="utf-8").strip()
        if hid_value in manifest.hosts:
            return hid_value, manifest.hosts[hid_value]
        raise SyncError(
            f"host id {hid_value!r} from {host_id_file} not in manifest. "
            f"Run `maury init` to register, or remove {host_id_file} to "
            f"re-bootstrap."
        )
    # No id file → fallback to hostname
    hostname = socket.gethostname()
    hid = manifest.host_id_by_name(hostname)
    if hid is None:
        raise SyncError(
            f"this host (hostname={hostname!r}) is not in the manifest "
            f"and no {host_id_file} exists. Run `maury init` first."
        )
    return hid, manifest.hosts[hid]


def _sync_one_repo(
    *,
    nickname: str,
    spec: RepoSpec,
    local_path: Path,
    dry_run: bool,
) -> RepoSyncResult:
    """Pull or clone one repo. Returns a structured result; never raises."""
    # v0: only git/github backends. Other backends will land in a Phase 4
    # backend-adapter sweep per ADR-0016.
    if spec.backend not in ("git", "github"):
        return RepoSyncResult(
            nickname=nickname,
            spec=spec,
            local_path=local_path,
            action="skipped",
            detail=(
                f"backend {spec.backend!r} not yet implemented in v0; only 'git' and 'github' supported. Skipping."
            ),
        )

    if dry_run:
        # Don't touch the remote in dry-run mode — just report what we would do
        if local_path.is_dir() and (local_path / ".git").exists():
            return RepoSyncResult(
                nickname=nickname,
                spec=spec,
                local_path=local_path,
                action="up-to-date",
                detail="dry-run: would `git pull` (no remote I/O)",
            )
        return RepoSyncResult(
            nickname=nickname,
            spec=spec,
            local_path=local_path,
            action="cloned",
            detail=f"dry-run: would `git clone {spec.url}` (no remote I/O)",
        )

    if local_path.is_dir() and (local_path / ".git").exists():
        # Pull
        rc, out = _run_git(["git", "-C", str(local_path), "pull", "--ff-only"])
        if rc != 0:
            return RepoSyncResult(
                nickname=nickname,
                spec=spec,
                local_path=local_path,
                action="error",
                detail=f"git pull failed (rc={rc}): {out[:300]}",
            )
        if "Already up to date" in out or "Already up-to-date" in out:
            return RepoSyncResult(
                nickname=nickname,
                spec=spec,
                local_path=local_path,
                action="up-to-date",
                detail="",
            )
        return RepoSyncResult(
            nickname=nickname,
            spec=spec,
            local_path=local_path,
            action="pulled",
            detail=out.strip().splitlines()[-1] if out.strip() else "",
        )

    # Clone
    if local_path.exists():
        return RepoSyncResult(
            nickname=nickname,
            spec=spec,
            local_path=local_path,
            action="error",
            detail=f"path {local_path} exists but is not a git repo",
        )
    rc, out = _run_git(["git", "clone", spec.url, str(local_path)])
    if rc != 0:
        return RepoSyncResult(
            nickname=nickname,
            spec=spec,
            local_path=local_path,
            action="error",
            detail=f"git clone failed (rc={rc}): {out[:300]}",
        )
    return RepoSyncResult(
        nickname=nickname,
        spec=spec,
        local_path=local_path,
        action="cloned",
        detail=spec.url,
    )


def _run_git(cmd: list[str], *, timeout: float = 60.0) -> tuple[int, str]:
    """Run a git command; return (rc, combined stdout+stderr)."""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return -1, str(e)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


# Re-export for convenience: callers may want RepoMode for type checks
__all__ = [
    "RepoMode",  # re-exported for typing-friendly imports
    "RepoSyncResult",
    "SyncError",
    "SyncResult",
    "sync",
]
