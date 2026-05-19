"""Manifest schema migrations.

Per ADR-0015 §"Migration" + ADR-0030, manifest schema version bumps are
breaking changes that require a one-shot upgrade. This module owns the
v1 → v2 migration:

- v1 keyed hosts and profiles by their display **name** (a textbook
  natural-key-as-primary-key violation; see ADR-0015 TL;DR for the
  problem statement).
- v2 keys them by surrogate IDs (`host_<32 hex>` / `profile_<32 hex>`)
  and demotes names to a mutable `name` field on the entity.

The upgrade generates fresh IDs for every host and profile, rewrites
inheritance references (`extends`) to use IDs, rewrites each host's
`profile` reference to use the new profile ID, and emits a mapping
report so the user can correlate old names to new IDs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from maury.ids import new_host_id, new_profile_id


class MigrationError(ValueError):
    """Raised when a manifest cannot be migrated as-is."""


@dataclass(frozen=True)
class V1ToV2Mapping:
    """Old-name → new-id mapping captured by the upgrade."""

    profile_name_to_id: dict[str, str] = field(default_factory=dict)
    host_name_to_id: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class V1ToV2Result:
    """Outcome of a v1→v2 upgrade run."""

    in_path: Path
    out_path: Path
    mapping: V1ToV2Mapping
    dry_run: bool = False


def _validate_v1_shape(raw: dict[str, Any], in_path: Path) -> None:
    """Sanity-check that we're looking at a v1 manifest, not something else."""
    version = raw.get("version")
    if version == 2:
        raise MigrationError(f"{in_path}: already version 2; nothing to upgrade.")
    if version not in (1, None):
        raise MigrationError(f"{in_path}: unknown manifest version {version!r}. Expected 1 (or absent → assumed 1).")
    if "hosts" in raw and not isinstance(raw["hosts"], dict):
        raise MigrationError(f"{in_path}: 'hosts' must be a JSON object (dict).")
    if "profiles" in raw and not isinstance(raw["profiles"], dict):
        raise MigrationError(f"{in_path}: 'profiles' must be a JSON object (dict).")


def _migrate_profiles(
    v1_profiles: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """Return (new_profiles_keyed_by_id, name→id mapping).

    Generates fresh `profile_<hex>` IDs, embeds the original name as a
    `name` field, and rewrites `extends` from old-name to new-id.
    """
    name_to_id: dict[str, str] = {name: new_profile_id() for name in v1_profiles}
    new_profiles: dict[str, dict[str, Any]] = {}
    for name, body in v1_profiles.items():
        if not isinstance(body, dict):
            raise MigrationError(f"profile {name!r}: body must be a JSON object.")
        pid = name_to_id[name]
        rewritten: dict[str, Any] = dict(body)
        rewritten["name"] = name
        old_extends = rewritten.get("extends")
        if isinstance(old_extends, str) and old_extends:
            if old_extends not in name_to_id:
                raise MigrationError(f"profile {name!r}: extends {old_extends!r} which does not exist in the manifest.")
            rewritten["extends"] = name_to_id[old_extends]
        new_profiles[pid] = rewritten
    return new_profiles, name_to_id


def _migrate_hosts(
    v1_hosts: dict[str, Any],
    profile_name_to_id: dict[str, str],
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """Return (new_hosts_keyed_by_id, name→id mapping).

    Generates fresh `host_<hex>` IDs, embeds the original name as a
    `name` field, and rewrites each host's `profile` reference from
    old-name to new-id.
    """
    name_to_id: dict[str, str] = {name: new_host_id() for name in v1_hosts}
    new_hosts: dict[str, dict[str, Any]] = {}
    for name, body in v1_hosts.items():
        if not isinstance(body, dict):
            raise MigrationError(f"host {name!r}: body must be a JSON object.")
        hid = name_to_id[name]
        rewritten: dict[str, Any] = dict(body)
        rewritten["name"] = name
        profile_ref = rewritten.get("profile")
        if not isinstance(profile_ref, str) or not profile_ref:
            raise MigrationError(f"host {name!r}: missing 'profile' field.")
        if profile_ref not in profile_name_to_id:
            raise MigrationError(f"host {name!r}: profile {profile_ref!r} does not exist in the manifest.")
        rewritten["profile"] = profile_name_to_id[profile_ref]
        new_hosts[hid] = rewritten
    return new_hosts, name_to_id


def upgrade_v1_to_v2(
    *,
    in_path: Path,
    out_path: Path | None = None,
    dry_run: bool = False,
) -> V1ToV2Result:
    """Migrate a v1 manifest to v2.

    Args:
        in_path: Path to the v1 manifest.json.
        out_path: Where to write the v2 manifest. Defaults to in_path
            (in-place upgrade).
        dry_run: Compute the mapping but don't write.

    Raises:
        MigrationError on invalid v1 shape, dangling references, or
        unsupported version values.
    """
    if not in_path.is_file():
        raise MigrationError(f"manifest not found: {in_path}")
    try:
        raw = json.loads(in_path.read_text())
    except json.JSONDecodeError as e:
        raise MigrationError(f"{in_path}: invalid JSON: {e}") from e
    if not isinstance(raw, dict):
        raise MigrationError(f"{in_path}: top-level must be a JSON object.")

    _validate_v1_shape(raw, in_path)

    v1_profiles = raw.get("profiles") or {}
    v1_hosts = raw.get("hosts") or {}

    new_profiles, profile_name_to_id = _migrate_profiles(v1_profiles)
    new_hosts, host_name_to_id = _migrate_hosts(v1_hosts, profile_name_to_id)

    upgraded: dict[str, Any] = dict(raw)
    upgraded["version"] = 2
    upgraded["profiles"] = new_profiles
    upgraded["hosts"] = new_hosts

    target = out_path if out_path is not None else in_path
    if not dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(upgraded, indent=2) + "\n")

    return V1ToV2Result(
        in_path=in_path,
        out_path=target,
        mapping=V1ToV2Mapping(
            profile_name_to_id=profile_name_to_id,
            host_name_to_id=host_name_to_id,
        ),
        dry_run=dry_run,
    )


__all__ = [
    "MigrationError",
    "V1ToV2Mapping",
    "V1ToV2Result",
    "upgrade_v1_to_v2",
]
