"""`maury bootstrap host` — curator-side host registration.

Per ADR-0018, `maury bootstrap` is the curator's set of fleet-side
ops, distinct from `maury init` (which is the new-host's first
command). This module ships the `host` subverb: register a new host
in the manifest so that when the user runs `maury init` on that
host, the hostname fallback resolves to the registered entry.

v0 scope:
  - Append a HostSpec to the manifest with a fresh `host_<hex>` ID.
  - Validate inputs: profile exists, name is unique.
  - Default the host's `repos` to a single `base` entry whose URL
    is inferred from another host's `base` URL (the manifest is
    its own source-of-truth for the base URL once any host is
    registered).
  - Write the manifest back via `dump_manifest`.

Deferred to later sub-phases:
  - `--commit` / `--push` flags that wrap the manifest write in a
    git commit + push to the base repo.
  - Multi-repo defaults (additional repos beyond base).
  - Deploy-key generation for the new host (today the curator/host
    handle that out-of-band, per ADR-0003).
  - Concurrency-safe inclusive merge per ADR-0024 — v0 just writes
    the file; concurrent curator edits race the same way any
    file edit does.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from maury.ids import new_host_id, short
from maury.manifest import (
    HostSpec,
    Manifest,
    ManifestError,
    PushPolicy,
    RepoMode,
    RepoSpec,
    dump_manifest,
    load_manifest,
)


@dataclass(frozen=True)
class BootstrapHostResult:
    """Summary of what `bootstrap host` did (or would do, in dry-run)."""

    actions: list[str] = field(default_factory=list)
    host_id: str = ""
    name: str = ""
    profile_id: str = ""
    message: str = ""


class BootstrapHostError(ValueError):
    """Raised when the bootstrap inputs are invalid or the manifest is malformed."""


def bootstrap_host(
    *,
    manifest_path: Path,
    name: str,
    profile: str,
    base_url: str | None = None,
    base_mode: RepoMode = RepoMode.RO,
    push_policy: PushPolicy = PushPolicy.OWN_PROFILE_ONLY,
    owner: str | None = None,
    dry_run: bool = False,
) -> BootstrapHostResult:
    """Register a new host in the manifest.

    Args:
        manifest_path: path to the canonical manifest.json (typically
            `<base-clone>/.meta/manifest.json`).
        name: the host's display label. Should match what
            `socket.gethostname()` returns on that host so the init
            hostname-fallback resolves cleanly.
        profile: profile name OR profile ID. Resolved against the
            manifest's profile registry.
        base_url: URL of the base repo for this new host's `repos["base"]`.
            If None, copy the URL from another already-registered host's
            base repo (manifest is self-sufficient once any host is
            registered). Errors if no other host is registered AND
            `base_url` was not provided.
        base_mode: access mode for the new host's base repo entry.
            Defaults to RO (safest — the new host pulls but doesn't
            push). Curator can pass RW or PR explicitly.
        push_policy: per-host push policy. Defaults to OWN_PROFILE_ONLY
            (cannot push outside its profile boundary).
        owner: optional owner identifier (email, handle). Stored as a
            display field; not used for access control in v0.
        dry_run: when True, validate everything but don't write the
            manifest.

    Returns:
        BootstrapHostResult describing what happened.

    Raises:
        BootstrapHostError if inputs are invalid (profile not found,
        name already taken, etc.).
    """
    if not name.strip():
        raise BootstrapHostError("name must be non-empty")
    if not profile.strip():
        raise BootstrapHostError("profile must be non-empty")
    if not manifest_path.is_file():
        raise BootstrapHostError(f"manifest not found: {manifest_path}")

    actions: list[str] = []

    # 1. Load manifest.
    try:
        manifest = load_manifest(manifest_path)
    except ManifestError as e:
        raise BootstrapHostError(f"manifest at {manifest_path} failed to load: {e}") from e

    # 2. Resolve profile (accepts name or ID).
    profile_id = manifest.resolve_profile(profile)
    if profile_id is None:
        known = sorted(s.name for s in manifest.profiles.values())
        raise BootstrapHostError(f"profile {profile!r} not found in manifest. Known profiles: {known}")
    actions.append(f"resolved profile {profile!r} -> {profile_id}")

    # 3. Validate name is unique.
    if manifest.host_id_by_name(name) is not None:
        existing_hid = manifest.host_id_by_name(name)
        raise BootstrapHostError(
            f"host name {name!r} already taken by {existing_hid}. Pick a different name or use `maury host rename`."
        )

    # 4. Resolve base_url.
    resolved_base_url = base_url or _infer_base_url(manifest)
    if resolved_base_url is None:
        raise BootstrapHostError(
            "no base_url provided and no other host has a `base` repo "
            "entry to copy from. Pass --base-url <url> explicitly."
        )
    if base_url is None:
        actions.append(f"inferred base URL from existing manifest: {resolved_base_url}")

    # 5. Generate fresh host_id.
    new_hid = new_host_id()

    # 6. Build HostSpec.
    new_spec = HostSpec(
        name=name,
        profile=profile_id,
        repos={"base": RepoSpec(url=resolved_base_url, mode=base_mode)},
        push_policy=push_policy,
        owner=owner,
        added=_today(),
    )

    actions.append(
        f"will register host {name!r} (id={short(new_hid)}, "
        f"profile={manifest.profiles[profile_id].name!r}, "
        f"base_mode={base_mode.value}, push_policy={push_policy.value})"
    )

    # 7. Write manifest.
    new_manifest = Manifest(
        version=manifest.version,
        profiles=dict(manifest.profiles),
        hosts={**manifest.hosts, new_hid: new_spec},
    )
    if not dry_run:
        manifest_path.write_text(dump_manifest(new_manifest), encoding="utf-8")
        actions.append(f"wrote {manifest_path}")
    else:
        actions.append(f"(--check; would write {manifest_path})")

    return BootstrapHostResult(
        actions=actions,
        host_id=new_hid,
        name=name,
        profile_id=profile_id,
        message=(
            f"registered host {name!r} as {new_hid} "
            f"in profile {manifest.profiles[profile_id].name!r}. "
            f"Next steps: commit + push the manifest, then the user "
            f"on {name!r} can run `maury init`."
        ),
    )


def _infer_base_url(manifest: Manifest) -> str | None:
    """Return the base URL from any already-registered host's `repos["base"]`,
    or None if no host is registered or none has a base entry.

    Manifest is self-sufficient once any host is registered: every host
    points at the same base URL, so we can copy from any of them.
    """
    for spec in manifest.hosts.values():
        base = spec.repos.get("base")
        if base is not None:
            return base.url
    return None


def _today() -> str:
    """ISO-8601 date string for the `added` field."""
    return datetime.now(UTC).strftime("%Y-%m-%d")


__all__ = [
    "BootstrapHostError",
    "BootstrapHostResult",
    "bootstrap_host",
]
