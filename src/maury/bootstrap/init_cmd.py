"""`maury init` — first-run bootstrap on a new host.

Per ADR-0018 + ADR-0039 step 8 (host-bootstrap, not curator-bootstrap):
  1. Self-identify: generate `~/.maury-host-id` if not present. The
     locally-generated UUID is canonical — hostname is no longer
     consulted for resolution (per the 2026-05-14 ADR-0039 amendment).
  2. Ingest the base repo: from a local directory (--from-dir) or
     tarball (--from-tarball). Git clone deferred to a later sub-phase.
  3. Read the manifest from the ingested repo.
  4. Look up this host's locally-generated ID in the manifest. If
     present, render. If absent, return host_registered=False with a
     message guiding the user to add the manifest entry.
  5. Drift preflight (per ADR-0017 + Tenet 1): refuse to clobber
     pre-existing content the user might have hand-edited.
  6. Render base + profile chain + host overlay into the target dir.
  7. Persist `last-render.json` so subsequent syncs can detect drift.

Future sub-phases:
  - Git URL clone with SSH key generation
  - ADR-0042 host-identity baseline (`host-identity.json`) written
    alongside `~/.maury-host-id`
  - Deploy-key bootstrap for additional repos
"""

from __future__ import annotations

import tarfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from maury.drift import (
    DriftReport,
    FileFingerprint,
    LastRender,
    detect_drift,
    read_last_render,
    write_last_render,
)
from maury.host_identity import HostIdentityBaseline, write_baseline
from maury.ids import host_id_hex_prefix, new_host_id
from maury.manifest import HOST_ID_FILE, ManifestError, load_manifest
from maury.render import RenderError, RenderResult, apply_render, render
from maury.sync import (
    DRIFT_MODE_DEFAULT,
    DRIFT_MODE_FORCE,
    DRIFT_MODE_NON_INTERACTIVE,
    DRIFT_SCAN_DIRS,
)

_DRIFT_MODES = {DRIFT_MODE_DEFAULT, DRIFT_MODE_FORCE, DRIFT_MODE_NON_INTERACTIVE}


def current_host_id_file() -> Path:
    """Return the active host-id file path.

    Module-level `HOST_ID_FILE` is captured at import time and used as
    the default for `init()`'s parameter. Callers that need to query
    its *current* value at call time (e.g., to honor test monkeypatches
    on this module's `HOST_ID_FILE` attribute) should call this helper
    rather than capturing the attribute themselves.
    """
    return HOST_ID_FILE


@dataclass(frozen=True)
class InitResult:
    """Summary of what `init` did (or would do, in dry-run)."""

    actions: list[str] = field(default_factory=list)
    host_id_created: bool = False
    host_registered: bool = False
    rendered: bool = False
    message: str = ""
    drift_report: DriftReport | None = None
    # "" (no preflight ran), "none", "skipped", "forced", "refused"
    drift_action: str = ""
    errors: list[str] = field(default_factory=list)

    def has_errors(self) -> bool:
        return bool(self.errors)


class InitError(ValueError):
    """Raised when `maury init` fails before render."""


def init(
    *,
    source_dir: Path | None = None,
    source_tarball: Path | None = None,
    target_dir: Path,
    host_id_file: Path | None = None,
    dry_run: bool = False,
    drift_mode: str = DRIFT_MODE_DEFAULT,
    tag: str | None = None,
) -> InitResult:
    """Run the init flow against an already-ingested or to-be-ingested repo.

    Exactly one of `source_dir` or `source_tarball` must be provided.
    Returns an InitResult describing what happened (created host id?
    found the host? what was rendered?).

    `drift_mode` controls the preflight behavior when target_dir already
    has content (per ADR-0017's three sync flows, mirrored here):
      - "default": refuse on any pre-existing content collision (or, on
        re-init, on detected drift); points the user at --force.
      - "force": clobber with a loud warning. Hand-edits will be lost.
      - "non-interactive": refuse with errors set; exit-1 from the CLI.
    """
    if drift_mode not in _DRIFT_MODES:
        raise InitError(f"unknown drift_mode {drift_mode!r}; expected one of {sorted(_DRIFT_MODES)}")
    if (source_dir is None) == (source_tarball is None):
        raise InitError("provide exactly one of --from-dir or --from-tarball")

    # Defer the HOST_ID_FILE module lookup until call time so test
    # monkeypatches on `maury.bootstrap.init_cmd.HOST_ID_FILE` take
    # effect. Default-arg binding would freeze the path at definition
    # time and miss the patch.
    if host_id_file is None:
        host_id_file = HOST_ID_FILE

    actions: list[str] = []

    # 1. Ingest the repo.
    if source_dir is not None:
        repo = source_dir.resolve()
        if not repo.is_dir():
            raise InitError(f"source dir not found: {repo}")
        actions.append(f"using local repo at {repo}")
    else:
        assert source_tarball is not None
        if not source_tarball.is_file():
            raise InitError(f"tarball not found: {source_tarball}")
        repo = _extract_tarball(source_tarball)
        actions.append(f"extracted {source_tarball} -> {repo}")

    # 2. Self-identify (ADR-0039 step 8 + ADR-0015's tagged-ID amendment).
    if host_id_file.exists():
        host_id_value = host_id_file.read_text(encoding="utf-8").strip()
        actions.append(f"using existing host id from {host_id_file}: {host_id_value}")
        host_id_created = False
    else:
        host_id_value = new_host_id(tag=tag)
        if not dry_run:
            host_id_file.parent.mkdir(parents=True, exist_ok=True)
            host_id_file.write_text(host_id_value + "\n", encoding="utf-8")
        actions.append(f"{'would write' if dry_run else 'wrote'} new host id to {host_id_file}: {host_id_value}")
        host_id_created = True

    # 3. Read the manifest.
    manifest_path = repo / ".meta" / "manifest.json"
    if not manifest_path.is_file():
        raise InitError(f"manifest not found at {manifest_path}")
    try:
        manifest = load_manifest(manifest_path)
    except ManifestError as e:
        raise InitError(f"manifest at {manifest_path} failed to load: {e}") from e

    # 4. Resolve which host entry applies. Per ADR-0039 step 8: the
    # locally-generated UUID is canonical; hostname is NOT consulted as
    # a fallback. If this host's UUID isn't in the manifest, it's
    # because the curator (or the user themselves as their own curator)
    # hasn't yet added it. We return a structured "add this entry"
    # message rather than silently matching by hostname.
    if host_id_value not in manifest.hosts:
        return InitResult(
            actions=actions,
            host_id_created=host_id_created,
            host_registered=False,
            rendered=False,
            message=(
                f"this host's id ({host_id_value}) is not yet registered in the manifest.\n"
                f"\n"
                f"Add the following entry to the manifest's `hosts` map, commit, and push:\n"
                f"\n"
                f'  "{host_id_value}": {{\n'
                f'    "name": "<display label>",\n'
                f'    "profile": "<profile_id_or_name>",\n'
                f'    "repos": {{ "base": {{ "url": "<git-url>", "mode": "rw" }} }}\n'
                f"  }}\n"
                f"\n"
                f"Currently registered hosts: {sorted(s.name for s in manifest.hosts.values())}\n"
                f"Then re-run `maury init`."
            ),
        )
    matched_hid = host_id_value
    actions.append(f"identified as host {manifest.hosts[matched_hid].name!r} via host-id-file")
    profile_id = manifest.hosts[matched_hid].profile

    # 5. Render.
    try:
        result = render(
            repo_paths={"base": repo},
            manifest=manifest,
            profile_id=profile_id,
            host_id=matched_hid,
        )
    except RenderError as e:
        raise InitError(f"render failed: {e}") from e

    # 5a. Drift preflight (per ADR-0017 + Tenet 1).
    last = read_last_render(target_dir)
    drift_report: DriftReport | None = None
    drift_action: str = ""
    drift_errors: list[str] = []

    if last is None:
        # Bootstrap case. There's no maury baseline to compare against,
        # but the target dir might already contain content the user
        # hand-managed (e.g., a pre-maury `~/.claude/`). Refuse to
        # silently overwrite it.
        collisions = _detect_collisions(rendered=result, target_dir=target_dir)
        if collisions:
            drift_action, drift_errors, collision_warnings = _evaluate_collision_policy(
                collisions=collisions,
                target_dir=target_dir,
                drift_mode=drift_mode,
            )
            actions.extend(collision_warnings)
        else:
            drift_action = "none"
    else:
        # Re-init case. Same drift policy as `maury sync`.
        drift_report = detect_drift(
            target_dir=target_dir,
            last=last,
            untracked_scan_dirs=DRIFT_SCAN_DIRS,
        )
        if drift_report.has_drift():
            drift_action, drift_errors, drift_warnings = _evaluate_drift_policy(
                drift_report=drift_report,
                drift_mode=drift_mode,
            )
            actions.extend(drift_warnings)
        else:
            drift_action = "none"

    if drift_errors:
        return InitResult(
            actions=actions,
            host_id_created=host_id_created,
            host_registered=True,
            rendered=False,
            message=(
                f"refused to render onto {target_dir}: see errors. Use --force to overwrite or --check to inspect."
            ),
            drift_report=drift_report,
            drift_action=drift_action,
            errors=drift_errors,
        )

    apply_actions = apply_render(result, target_dir, dry_run=dry_run)
    actions.extend(apply_actions)
    if result.warnings:
        for w in result.warnings:
            actions.append(f"warning: {w}")

    # 5b. Persist last-render.json baseline so subsequent syncs can
    # detect drift. Skip on dry-run (don't pollute state with a
    # hypothetical render).
    if not dry_run:
        new_baseline = LastRender(
            schema_version=1,
            rendered_at=_now_iso(),
            host_id=matched_hid,
            profile_id=profile_id,
            files=[
                FileFingerprint.from_bytes(
                    path=f.target_path,
                    content=f.content,
                )
                for f in result.files
            ],
        )
        write_last_render(target_dir, new_baseline)

        # 5c. Per ADR-0042, write the host-identity baseline so subsequent
        # mode-scoped commands can detect accidental hex edits to
        # `~/.maury-host-id`. The hex prefix is the lookup primitive; the
        # mode info is audit metadata.
        identity_baseline = HostIdentityBaseline(
            schema_version=1,
            host_id_hex=host_id_hex_prefix(matched_hid),
            registered_at=_now_iso(),
            mode_id=profile_id,
            mode_name_at_bootstrap=manifest.profiles[profile_id].name,
        )
        write_baseline(target_dir, identity_baseline)

    return InitResult(
        actions=actions,
        host_id_created=host_id_created,
        host_registered=True,
        rendered=True,
        message=(
            f"initialized as host {manifest.hosts[matched_hid].name!r} "
            f"(profile {manifest.profiles[profile_id].name!r}); "
            f"{len(result.files)} file(s) {'would be ' if dry_run else ''}written to {target_dir}."
        ),
        drift_report=drift_report,
        drift_action=drift_action,
    )


def _now_iso() -> str:
    """UTC ISO-8601 timestamp matching the format used by sync's baseline writes."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _detect_collisions(*, rendered: RenderResult, target_dir: Path) -> list[str]:
    """Return target paths that already exist on disk with content
    different from what would be rendered.

    Used in the bootstrap branch (no last-render.json baseline). Files
    already on disk that match the render output byte-for-byte are not
    collisions — apply_render would no-op on them.
    """
    collisions: list[str] = []
    for f in rendered.files:
        full = target_dir / f.target_path
        if not full.is_file():
            continue
        if full.read_bytes() != f.content:
            collisions.append(f.target_path)
    return collisions


def _evaluate_collision_policy(
    *,
    collisions: list[str],
    target_dir: Path,
    drift_mode: str,
) -> tuple[str, list[str], list[str]]:
    """Decide what to do with bootstrap-case collisions.

    Returns (drift_action, errors, warnings). Errors signal refusal;
    warnings are advisory (e.g., "--force overwriting N files").
    """
    preview = ", ".join(collisions[:5]) + (", ..." if len(collisions) > 5 else "")
    if drift_mode == DRIFT_MODE_FORCE:
        return (
            "forced",
            [],
            [
                f"warning: --force overwriting {len(collisions)} pre-existing "
                f"file(s) in {target_dir} that maury has not seen before "
                f"({preview}). Hand-edits will be lost."
            ],
        )
    if drift_mode == DRIFT_MODE_NON_INTERACTIVE:
        return (
            "refused",
            [
                f"--non-interactive: refusing to overwrite {len(collisions)} "
                f"pre-existing file(s) in {target_dir} that maury has not "
                f"seen before ({preview}). Re-run interactively or with --force."
            ],
            [],
        )
    return (
        "refused",
        [
            f"target dir {target_dir} contains {len(collisions)} pre-existing "
            f"file(s) that maury would overwrite ({preview}). "
            f"Re-run with --force to overwrite (these will be lost) or "
            f"--check to inspect. After --force, future syncs use drift "
            f"detection to protect hand-edits."
        ],
        [],
    )


def _evaluate_drift_policy(
    *,
    drift_report: DriftReport,
    drift_mode: str,
) -> tuple[str, list[str], list[str]]:
    """Decide what to do with re-init drift (last-render.json present).

    Mirrors sync.py's drift policy. Returns (drift_action, errors, warnings).
    """
    counts = drift_report.summary_counts()
    summary = f"modified={counts['modified']}, missing={counts['missing']}, untracked={counts['untracked']}"
    if drift_mode == DRIFT_MODE_FORCE:
        return (
            "forced",
            [],
            [f"warning: --force clobbering drift ({summary}). Hand-edits will be overwritten."],
        )
    if drift_mode == DRIFT_MODE_NON_INTERACTIVE:
        return (
            "refused",
            [f"--non-interactive: refusing on drift ({summary}). Re-run interactively or with --force."],
            [],
        )
    return (
        "refused",
        [
            f"drift detected on this host's target dir ({summary}). "
            f"Re-run with --force to overwrite (hand-edits will be lost), "
            f"--check to inspect, or `maury reconcile` (interactive resolution)."
        ],
        [],
    )


def _extract_tarball(tarball_path: Path) -> Path:
    """Extract a maury-base tarball into a stable cache location.

    The cache location is `~/.cache/maury/repos/<tarball-basename>/`. Re-
    runs against the same tarball will overwrite this dir.
    """
    cache_root = Path.home() / ".cache" / "maury" / "repos"
    extract_into = cache_root / tarball_path.stem
    extract_into.mkdir(parents=True, exist_ok=True)

    with tarfile.open(tarball_path, "r:*") as tar:
        # Per Python 3.12+ recommendation; fall back for 3.11.
        try:
            tar.extractall(extract_into, filter="data")
        except TypeError:
            tar.extractall(extract_into)

    # If the tarball had a single top-level directory, descend into it.
    children = [p for p in extract_into.iterdir() if not p.name.startswith(".")]
    if len(children) == 1 and children[0].is_dir() and (children[0] / ".meta").is_dir():
        return children[0]
    return extract_into


__all__ = ["InitError", "InitResult", "init"]
