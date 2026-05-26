"""`maury mode deregister` — retire a host's mode registration.

Per ADR-0039 §"Mode change process", mode change is two operations:
deregister, then bootstrap. Never a single atomic switch — the two-step
form keeps the trust boundary explicit (the host is briefly registered
to no mode, then to its new one).

This module owns the manifest-side removal. Today it operates against
the v2-flat manifest (`manifest.json`'s `hosts` dict); when the
marker-based migration per ADR-0030 ships, the equivalent operation
will write to the relevant mode's `.meta/maury-marker.json` `hosts`
dict instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from maury.manifest import (
    Manifest,
    ManifestError,
    dump_manifest,
    load_manifest,
)


class DeregisterError(ValueError):
    """Raised when deregister cannot proceed."""


@dataclass(frozen=True)
class DeregisterResult:
    """Outcome of a deregister call."""

    host_id: str
    """The `host_<hex>` ID that was removed."""

    name: str
    """The display name of the host that was removed."""

    profile_id: str
    """The profile/mode the host was registered against."""

    profile_name: str
    """The display name of the profile/mode."""

    dry_run: bool = False
    actions: tuple[str, ...] = field(default_factory=tuple)


def deregister_host(
    *,
    manifest_path: Path,
    host: str,
    dry_run: bool = False,
) -> DeregisterResult:
    """Remove a host entry from the manifest.

    Args:
        manifest_path: Path to the canonical manifest.json.
        host: Host display name OR `host_<hex>` ID. Both shapes accepted.
        dry_run: Validate everything but don't write the manifest.

    Raises:
        DeregisterError if the host is not found or the manifest is broken.
    """
    if not host.strip():
        raise DeregisterError("host must be non-empty")
    if not manifest_path.is_file():
        raise DeregisterError(f"manifest not found: {manifest_path}")

    try:
        manifest = load_manifest(manifest_path)
    except ManifestError as e:
        raise DeregisterError(f"manifest at {manifest_path} failed to load: {e}") from e

    actions: list[str] = []

    # Accept either a host_<hex> ID directly or a display name.
    if host in manifest.hosts:
        target_hid = host
    else:
        looked_up = manifest.host_id_by_name(host)
        if looked_up is None:
            known = sorted(spec.name for spec in manifest.hosts.values())
            raise DeregisterError(f"host {host!r} not found in manifest. Known hosts (by name): {known}")
        target_hid = looked_up

    spec = manifest.hosts[target_hid]
    profile_spec = manifest.profiles.get(spec.profile)
    profile_name = profile_spec.name if profile_spec else spec.profile

    actions.append(f"will deregister host {spec.name!r} (id={target_hid}, profile={profile_name!r})")

    new_hosts = {hid: hspec for hid, hspec in manifest.hosts.items() if hid != target_hid}
    new_manifest = Manifest(
        version=manifest.version,
        profiles=dict(manifest.profiles),
        hosts=new_hosts,
    )

    if not dry_run:
        manifest_path.write_text(dump_manifest(new_manifest), encoding="utf-8")
        actions.append(f"wrote {manifest_path}")
        # Audit-log: manifest_mutated. Per ADR-0035, fires after any
        # operation that writes a new manifest version.
        import contextlib

        from maury.audit_log import AuditLogError, log

        with contextlib.suppress(AuditLogError):
            log(
                "manifest_mutated",
                changes=[f"deregistered host {target_hid} ({spec.name!r})"],
                manifest_path=str(manifest_path),
            )
    else:
        actions.append(f"(--check; would write {manifest_path})")

    return DeregisterResult(
        host_id=target_hid,
        name=spec.name,
        profile_id=spec.profile,
        profile_name=profile_name,
        dry_run=dry_run,
        actions=tuple(actions),
    )


__all__ = [
    "DeregisterError",
    "DeregisterResult",
    "deregister_host",
]
