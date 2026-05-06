"""Capability schema — what a host actually has installed and which
security/security-posture flags apply.

Output of `maury probe` lives at:
  <repo>/profiles/<profile>/hosts/<host>/capabilities.json

The render engine consumes this to resolve action-form hooks
(`notify`, `log_jsonl`, ...) into platform-specific commands, and to
skip hooks whose required capabilities are missing or blocked.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class OS(StrEnum):
    """The operating-system family the probe detected on this host."""

    DARWIN = "darwin"
    LINUX = "linux"
    FREEBSD = "freebsd"
    OTHER = "other"


class UserlandFlavor(StrEnum):
    """GNU vs BSD coreutils — affects flag syntax (e.g., `sed -i ''`)."""

    GNU = "gnu"
    BSD = "bsd"
    UNKNOWN = "unknown"


class NotificationMech(StrEnum):
    """The mechanism this host uses to surface user-facing notifications."""

    OSASCRIPT = "osascript"
    NOTIFY_SEND = "notify-send"
    LOGGER = "logger"
    NONE = "none"


class SecurityPosture(StrEnum):
    """Trusted (personal) vs managed (corporate MDM) — affects which capabilities are blocked."""

    TRUSTED = "trusted"
    MANAGED = "managed"


class PrivilegedWrites(StrEnum):
    """How the tool should handle operations that need privilege."""

    AUTO = "auto"  # tool may sudo (requires NOPASSWD or similar)
    MANUAL = "manual"  # tool writes scripts; user runs them


@dataclass(frozen=True)
class ToolPresence:
    """Whether a CLI tool is installed on this host, and if so, where and which flavor."""

    path: str | None  # absolute path if found, else None
    flavor: UserlandFlavor = UserlandFlavor.UNKNOWN
    version: str | None = None  # short version string if cheaply available


@dataclass
class Capabilities:
    """Snapshot of what's available on this host at probe time."""

    hostname: str
    os: OS
    os_version: str
    shell: str
    userland: UserlandFlavor
    gui: bool
    notifications: NotificationMech
    security_posture: SecurityPosture
    privileged_writes: PrivilegedWrites
    tools: dict[str, ToolPresence] = field(default_factory=dict)
    blocked_capabilities: list[str] = field(default_factory=list)
    managed_by: str | None = None  # e.g., "jamf", "kandji", "mosyle"
    probed_at: str = ""  # ISO timestamp

    def has_tool(self, name: str) -> bool:
        t = self.tools.get(name)
        return t is not None and t.path is not None


# ---- serialization --------------------------------------------------------


def to_dict(caps: Capabilities) -> dict[str, Any]:
    """Convert to a JSON-friendly dict (enums → string values)."""
    d = asdict(caps)
    d["os"] = caps.os.value
    d["userland"] = caps.userland.value
    d["notifications"] = caps.notifications.value
    d["security_posture"] = caps.security_posture.value
    d["privileged_writes"] = caps.privileged_writes.value
    d["tools"] = {
        name: {
            "path": t.path,
            "flavor": t.flavor.value,
            "version": t.version,
        }
        for name, t in caps.tools.items()
    }
    return d


def from_dict(d: dict[str, Any]) -> Capabilities:
    """Reconstruct a Capabilities snapshot from its JSON-friendly dict form."""
    return Capabilities(
        hostname=d["hostname"],
        os=OS(d["os"]),
        os_version=d.get("os_version", ""),
        shell=d.get("shell", ""),
        userland=UserlandFlavor(d.get("userland", "unknown")),
        gui=bool(d.get("gui", False)),
        notifications=NotificationMech(d.get("notifications", "none")),
        security_posture=SecurityPosture(d.get("security_posture", "trusted")),
        privileged_writes=PrivilegedWrites(d.get("privileged_writes", "auto")),
        tools={
            name: ToolPresence(
                path=t.get("path"),
                flavor=UserlandFlavor(t.get("flavor", "unknown")),
                version=t.get("version"),
            )
            for name, t in d.get("tools", {}).items()
        },
        blocked_capabilities=list(d.get("blocked_capabilities", []) or []),
        managed_by=d.get("managed_by"),
        probed_at=d.get("probed_at", ""),
    )


def dumps(caps: Capabilities, *, indent: int = 2) -> str:
    """Serialize a Capabilities snapshot to a JSON string."""
    return json.dumps(to_dict(caps), indent=indent)


def loads(text: str) -> Capabilities:
    """Parse a JSON string into a Capabilities snapshot."""
    return from_dict(json.loads(text))
