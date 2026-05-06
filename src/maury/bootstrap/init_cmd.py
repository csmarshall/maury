"""`maury init` — first-run bootstrap on a new host.

Per ADR-0018. v1 minimum:
  1. Self-identify: generate ~/.maury-host-id if not present.
  2. Ingest the base repo: from a local directory (--from-dir) or
     tarball (--from-tarball). Git clone deferred to a later sub-phase.
  3. Read the manifest from the ingested repo.
  4. Look up this host: by ID file, then by hostname fallback.
  5. Render base + profile chain + host overlay into the target dir.

Future sub-phases:
  - Git URL clone with SSH key generation
  - New-host registration (when manifest doesn't contain the host)
  - Deploy-key bootstrap for additional repos
"""

from __future__ import annotations

import socket
import tarfile
from dataclasses import dataclass, field
from pathlib import Path

from maury.ids import new_host_id
from maury.manifest import HOST_ID_FILE, ManifestError, load_manifest
from maury.render import RenderError, apply_render, render


@dataclass(frozen=True)
class InitResult:
    """Summary of what `init` did (or would do, in dry-run)."""

    actions: list[str] = field(default_factory=list)
    host_id_created: bool = False
    host_registered: bool = False
    rendered: bool = False
    message: str = ""


class InitError(ValueError):
    """Raised when `maury init` fails before render."""


def init(
    *,
    source_dir: Path | None = None,
    source_tarball: Path | None = None,
    target_dir: Path,
    host_id_file: Path = HOST_ID_FILE,
    dry_run: bool = False,
) -> InitResult:
    """Run the init flow against an already-ingested or to-be-ingested repo.

    Exactly one of `source_dir` or `source_tarball` must be provided.
    Returns an InitResult describing what happened (created host id?
    found the host? what was rendered?).
    """
    if (source_dir is None) == (source_tarball is None):
        raise InitError("provide exactly one of --from-dir or --from-tarball")

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

    # 2. Self-identify.
    if host_id_file.exists():
        host_id_value = host_id_file.read_text(encoding="utf-8").strip()
        actions.append(f"using existing host id from {host_id_file}: {host_id_value}")
        host_id_created = False
    else:
        host_id_value = new_host_id()
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

    # 4. Resolve which host entry applies.
    matched_hid: str | None = None
    matched_via: str = ""
    if host_id_value in manifest.hosts:
        matched_hid = host_id_value
        matched_via = "host-id-file"
    else:
        # Hostname fallback (with .local stripping).
        hostname = socket.gethostname()
        matched_hid = manifest.host_id_by_name(hostname)
        if matched_hid:
            matched_via = f"hostname({hostname!r})"

    if matched_hid is None:
        # New-host registration not yet implemented in v1. Surface a
        # clear message describing what to do next.
        return InitResult(
            actions=actions,
            host_id_created=host_id_created,
            host_registered=False,
            rendered=False,
            message=(
                f"this host (id={host_id_value}, hostname={socket.gethostname()!r}) "
                f"is not registered in the manifest yet. "
                f"Available hosts: {sorted(s.name for s in manifest.hosts.values())}. "
                f"Have a curator add this host to the manifest, then re-run `maury init`."
            ),
        )

    actions.append(f"identified as host {manifest.hosts[matched_hid].name!r} via {matched_via}")
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

    apply_actions = apply_render(result, target_dir, dry_run=dry_run)
    actions.extend(apply_actions)
    if result.warnings:
        for w in result.warnings:
            actions.append(f"warning: {w}")

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
