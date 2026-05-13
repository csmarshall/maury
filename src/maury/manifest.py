"""Manifest: the source of truth for profiles, hosts, and repos.

Lives at `<base-repo>/.meta/manifest.json` and is replicated to every
host that has read access to the base repo. Updates are curator actions.

Schema (v2 — see ADR-0015):

```json
{
  "version": 2,
  "profiles": {
    "profile_<32hex>": {
      "name": "base",
      "extends": null,
      "description": "Universal preferences"
    }
  },
  "hosts": {
    "host_<32hex>": {
      "name": "workstation",
      "profile": "profile_<32hex>",
      "lock":    false,
      "push_policy": "permissive",
      "repos": {
        "base":     { "url": "git@codeberg.org:<your-username>/maury-base.git", "mode": "rw" },
        "personal": { "url": "git@codeberg.org:<your-username>/maury-personal.git", "mode": "rw" }
      },
      "owner": "you@example.com",
      "added": "2026-05-06"
    }
  }
}
```

`push_policy` values:
- `permissive`        : may write anywhere within rw repos (curator hosts)
- `own_profile_only`  : may write only to `profiles/<own>/` and `proposals/`
- `disabled`          : read-only consumer; no pushes at all (paranoid mode)

`mode` values:
- `ro` : tool refuses any write to this repo before invoking git
- `rw` : writes allowed (subject to push_policy)

Per ADR-0015, hosts/profiles are keyed by opaque IDs (`host_<hex>`,
`profile_<hex>`); the `name` field is mutable display metadata.
Internal cross-references (e.g., `host.profile`, `profile.extends`)
use IDs.
"""

from __future__ import annotations

import json
import socket
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from .ids import is_host_id, is_profile_id

CURRENT_VERSION = 2
HOST_ID_FILE = Path.home() / ".maury-host-id"


class PushPolicy(StrEnum):
    """How aggressively this host may push to the repos it has rw access to."""

    PERMISSIVE = "permissive"
    OWN_PROFILE_ONLY = "own_profile_only"
    DISABLED = "disabled"


class RepoMode(StrEnum):
    """Whether maury treats a repo as read-only or read-write on this host."""

    RO = "ro"
    RW = "rw"


@dataclass(frozen=True)
class ProfileSpec:
    """One profile in the manifest. Identified by a surrogate ID; `name` is the display label."""

    name: str  # mutable display label
    extends: str | None = None  # parent profile ID (or None)
    description: str = ""


@dataclass(frozen=True)
class RepoSpec:
    """One repo entry inside a host's `repos` dict — URL plus access mode and backend selection."""

    url: str
    mode: RepoMode
    backend: str = "git"  # see ADR-0016: "git" | "github" | "gitlab" | "gitea" | "p4" | ...
    backend_config: dict[str, Any] | None = None  # backend-specific opaque config


@dataclass(frozen=True)
class HostSpec:
    """One host in the manifest. Identified by a surrogate ID; `name` is the display label."""

    name: str  # mutable display label
    profile: str  # profile ID, NOT name
    repos: dict[str, RepoSpec] = field(default_factory=dict)
    lock: bool = False
    push_policy: PushPolicy = PushPolicy.OWN_PROFILE_ONLY
    owner: str | None = None
    added: str | None = None


@dataclass
class Manifest:
    """The full manifest: schema version + profiles registry + hosts registry."""

    version: int
    profiles: dict[str, ProfileSpec] = field(default_factory=dict)  # keyed by ID
    hosts: dict[str, HostSpec] = field(default_factory=dict)  # keyed by ID

    # ---- name<->id resolution -----------------------------------------

    def profile_id_by_name(self, name: str) -> str | None:
        for pid, spec in self.profiles.items():
            if spec.name == name:
                return pid
        return None

    def host_id_by_name(self, name: str) -> str | None:
        for hid, spec in self.hosts.items():
            if spec.name == name:
                return hid
        # Tolerant: try bare hostname (strip .local etc.)
        bare = name.split(".")[0]
        if bare != name:
            for hid, spec in self.hosts.items():
                if spec.name == bare:
                    return hid
        return None

    def resolve_profile(self, name_or_id: str) -> str | None:
        """Resolve a name or ID to a canonical profile ID, or None."""
        if is_profile_id(name_or_id):
            return name_or_id if name_or_id in self.profiles else None
        return self.profile_id_by_name(name_or_id)

    def resolve_host(self, name_or_id: str) -> str | None:
        """Resolve a name or ID to a canonical host ID, or None."""
        if is_host_id(name_or_id):
            return name_or_id if name_or_id in self.hosts else None
        return self.host_id_by_name(name_or_id)

    # ---- queries ------------------------------------------------------

    def profile_ids(self) -> set[str]:
        return set(self.profiles)

    def profile_names(self) -> set[str]:
        return {spec.name for spec in self.profiles.values()}

    def host_for_current_machine(self) -> tuple[str, HostSpec] | None:
        """Look up the manifest entry for the host this code is running on.

        Resolution order (per ADR-0015):
        1. Read `~/.maury-host-id` if present — that's the canonical ID.
        2. Fall back to `socket.gethostname()` matched against `name`.
        Returns None if the current host isn't registered.
        """
        if HOST_ID_FILE.exists():
            host_id = HOST_ID_FILE.read_text(encoding="utf-8").strip()
            if host_id in self.hosts:
                return host_id, self.hosts[host_id]
            return None
        # Bootstrap fallback — match by hostname.
        hid = self.host_id_by_name(socket.gethostname())
        if hid:
            return hid, self.hosts[hid]
        return None

    def inheritance_chain(self, profile_id: str) -> list[str]:
        """Return inheritance chain (profile IDs) root-first.

        For `extends: null`, returns `[profile_id]`. For
        `extends: parent_id`, returns `[parent_id, profile_id]`.
        Raises if the chain cycles or references an unknown profile.
        """
        seen: list[str] = []
        current: str | None = profile_id
        while current is not None:
            if current in seen:
                raise ManifestError(f"profile inheritance cycle detected: {' -> '.join([*seen, current])}")
            if current not in self.profiles:
                raise ManifestError(
                    f"profile {current!r} referenced but not defined (chain so far: {' -> '.join(seen) or '<start>'})"
                )
            seen.append(current)
            current = self.profiles[current].extends
        return list(reversed(seen))


class ManifestError(ValueError):
    """Raised when manifest data is invalid."""


# ---- loading / parsing ---------------------------------------------------

_VALID_HOST_KEYS = {
    "name",
    "profile",
    "lock",
    "push_policy",
    "repos",
    "owner",
    "added",
}
_VALID_PROFILE_KEYS = {"name", "extends", "description"}
_VALID_REPO_KEYS = {"url", "mode", "backend", "backend_config"}


def load_manifest(path: str | Path) -> Manifest:
    """Load a manifest from a JSON file."""
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    return parse_manifest(text, source=str(p))


def parse_manifest(text: str, source: str = "<string>") -> Manifest:
    """Parse a manifest from a JSON string. Strict; unknown keys raise.

    Only schema v2 is accepted. v1 manifests must be upgraded via
    `maury manifest upgrade-v1-to-v2`.
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ManifestError(f"{source}: JSON parse error: {e}") from e

    if not isinstance(data, dict):
        raise ManifestError(f"{source}: top-level must be an object")

    version = data.get("version", 1)
    if version == 1:
        raise ManifestError(
            f"{source}: manifest is v1; surrogate-key migration required. "
            f"Run `maury manifest upgrade-v1-to-v2 --in {source}`."
        )
    if version != CURRENT_VERSION:
        raise ManifestError(f"{source}: unsupported version {version}")

    raw_profiles = data.get("profiles", {})
    if not isinstance(raw_profiles, dict):
        raise ManifestError(f"{source}: 'profiles' must be an object")
    profiles: dict[str, ProfileSpec] = {}
    for pid, raw in raw_profiles.items():
        if not is_profile_id(pid):
            raise ManifestError(
                f"{source}: profile key {pid!r} is not a valid profile ID (expected 'profile_<32 hex chars>')"
            )
        if not isinstance(raw, dict):
            raise ManifestError(f"{source}: profile {pid!r} value must be an object")
        extra = set(raw) - _VALID_PROFILE_KEYS
        if extra:
            raise ManifestError(f"{source}: profile {pid!r}: unknown keys {sorted(extra)}")
        if "name" not in raw:
            raise ManifestError(f"{source}: profile {pid!r}: missing 'name'")
        profiles[pid] = ProfileSpec(
            name=raw["name"],
            extends=raw.get("extends"),
            description=raw.get("description", ""),
        )

    raw_hosts = data.get("hosts", {})
    if not isinstance(raw_hosts, dict):
        raise ManifestError(f"{source}: 'hosts' must be an object")
    hosts: dict[str, HostSpec] = {}
    for hid, raw in raw_hosts.items():
        if not is_host_id(hid):
            raise ManifestError(f"{source}: host key {hid!r} is not a valid host ID (expected 'host_<32 hex chars>')")
        if not isinstance(raw, dict):
            raise ManifestError(f"{source}: host {hid!r} value must be an object")
        extra = set(raw) - _VALID_HOST_KEYS
        if extra:
            raise ManifestError(f"{source}: host {hid!r}: unknown keys {sorted(extra)}")
        for required in ("name", "profile"):
            if required not in raw:
                raise ManifestError(f"{source}: host {hid!r}: missing {required!r}")

        try:
            push_policy = PushPolicy(raw.get("push_policy", "own_profile_only"))
        except ValueError as e:
            raise ManifestError(
                f"{source}: host {hid!r}: invalid push_policy "
                f"{raw.get('push_policy')!r}; expected one of "
                f"{[p.value for p in PushPolicy]}"
            ) from e

        repos = _parse_repos(raw.get("repos", {}), host=hid, source=source)

        hosts[hid] = HostSpec(
            name=raw["name"],
            profile=raw["profile"],
            repos=repos,
            lock=bool(raw.get("lock", False)),
            push_policy=push_policy,
            owner=raw.get("owner"),
            added=raw.get("added"),
        )

    return Manifest(version=version, profiles=profiles, hosts=hosts)


def _parse_repos(raw: dict[str, Any], *, host: str, source: str) -> dict[str, RepoSpec]:
    """Parse the `repos` block of a host entry into a dict of RepoSpec."""
    if not isinstance(raw, dict):
        raise ManifestError(f"{source}: host {host!r}: 'repos' must be an object")
    repos: dict[str, RepoSpec] = {}
    for repo_name, repo_raw in raw.items():
        if not isinstance(repo_raw, dict):
            raise ManifestError(f"{source}: host {host!r}: repo {repo_name!r} value must be an object")
        extra = set(repo_raw) - _VALID_REPO_KEYS
        if extra:
            raise ManifestError(f"{source}: host {host!r}: repo {repo_name!r}: unknown keys {sorted(extra)}")
        if "url" not in repo_raw:
            raise ManifestError(f"{source}: host {host!r}: repo {repo_name!r}: missing 'url'")
        try:
            mode = RepoMode(repo_raw.get("mode", "ro"))
        except ValueError as e:
            raise ManifestError(
                f"{source}: host {host!r}: repo {repo_name!r}: invalid mode "
                f"{repo_raw.get('mode')!r}; expected one of "
                f"{[m.value for m in RepoMode]}"
            ) from e
        backend = repo_raw.get("backend", "git")
        if not isinstance(backend, str):
            raise ManifestError(f"{source}: host {host!r}: repo {repo_name!r}: 'backend' must be a string")
        backend_config = repo_raw.get("backend_config")
        if backend_config is not None and not isinstance(backend_config, dict):
            raise ManifestError(
                f"{source}: host {host!r}: repo {repo_name!r}: 'backend_config' must be an object or null"
            )
        repos[repo_name] = RepoSpec(
            url=repo_raw["url"],
            mode=mode,
            backend=backend,
            backend_config=backend_config,
        )
    return repos


def validate_manifest(manifest: Manifest) -> list[str]:
    """Return a list of validation error strings (empty if valid).

    Cross-checks:
    - every host.profile must be a registered profile ID
    - every profile.extends must be null or a registered profile ID
    - inheritance chains must not cycle
    - no two profiles share the same `name`
    - no two hosts share the same `name`
    """
    errors: list[str] = []
    profile_ids = set(manifest.profiles)

    seen_profile_names: dict[str, str] = {}
    for pid, profile in manifest.profiles.items():
        if profile.name in seen_profile_names:
            errors.append(
                f"duplicate profile name {profile.name!r}: "
                f"used by both {seen_profile_names[profile.name]!r} and {pid!r}"
            )
        seen_profile_names[profile.name] = pid

        if profile.extends is not None and profile.extends not in profile_ids:
            errors.append(f"profile {pid!r} ({profile.name!r}): extends unknown profile {profile.extends!r}")
        else:
            try:
                manifest.inheritance_chain(pid)
            except ManifestError as e:
                errors.append(str(e))

    seen_host_names: dict[str, str] = {}
    for hid, host in manifest.hosts.items():
        if host.name in seen_host_names:
            errors.append(f"duplicate host name {host.name!r}: used by both {seen_host_names[host.name]!r} and {hid!r}")
        seen_host_names[host.name] = hid

        if host.profile not in profile_ids:
            errors.append(
                f"host {hid!r} ({host.name!r}): profile {host.profile!r} not in registry {sorted(profile_ids)}"
            )

    return errors


# ---- dumping --------------------------------------------------------------


def dump_manifest(manifest: Manifest, *, indent: int = 2) -> str:
    """Serialize a manifest back to JSON."""
    payload = {
        "version": manifest.version,
        "profiles": {pid: _profile_to_dict(spec) for pid, spec in manifest.profiles.items()},
        "hosts": {hid: _host_to_dict(spec) for hid, spec in manifest.hosts.items()},
    }
    return json.dumps(payload, indent=indent)


def _repo_to_dict(spec: RepoSpec) -> dict[str, Any]:
    """Serialize a RepoSpec to dict; omit `backend` when default ('git')."""
    out: dict[str, Any] = {"url": spec.url, "mode": spec.mode.value}
    if spec.backend != "git":
        out["backend"] = spec.backend
    if spec.backend_config:
        out["backend_config"] = spec.backend_config
    return out


def _profile_to_dict(spec: ProfileSpec) -> dict[str, Any]:
    """Serialize a ProfileSpec to dict for JSON output."""
    out: dict[str, Any] = {"name": spec.name, "extends": spec.extends}
    if spec.description:
        out["description"] = spec.description
    return out


def _host_to_dict(spec: HostSpec) -> dict[str, Any]:
    """Serialize a HostSpec to dict for JSON output."""
    out: dict[str, Any] = {"name": spec.name, "profile": spec.profile}
    if spec.lock:
        out["lock"] = True
    out["push_policy"] = spec.push_policy.value
    if spec.repos:
        out["repos"] = {name: _repo_to_dict(r) for name, r in spec.repos.items()}
    if spec.owner:
        out["owner"] = spec.owner
    if spec.added:
        out["added"] = spec.added
    return out


# ---- helpers for callers --------------------------------------------------


def known_profiles_from(manifest: Manifest | None) -> set[str]:
    """Helper for the rules engine: extract the set of profile IDs.

    Returns empty set if manifest is None.
    """
    return manifest.profile_ids() if manifest else set()


def known_profile_names_from(manifest: Manifest | None) -> set[str]:
    """Variant returning profile names — used when rules reference profiles by name."""
    return manifest.profile_names() if manifest else set()


__all__ = [
    "CURRENT_VERSION",
    "HOST_ID_FILE",
    "HostSpec",
    "Manifest",
    "ManifestError",
    "ProfileSpec",
    "PushPolicy",
    "RepoMode",
    "RepoSpec",
    "dump_manifest",
    "known_profile_names_from",
    "known_profiles_from",
    "load_manifest",
    "parse_manifest",
    "validate_manifest",
]
