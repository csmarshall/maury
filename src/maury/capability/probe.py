"""Capability probe — runtime detection of host environment.

Designed to be testable: every OS / subprocess interaction goes through
a small set of helpers that can be monkey-patched in unit tests, and the
probe takes optional inject points so platform-specific tests can
exercise the BSD / GNU / managed-mac branches on any machine.
"""

from __future__ import annotations

import os
import platform
import shutil
import socket
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from .schema import (
    OS,
    Capabilities,
    NotificationMech,
    PrivilegedWrites,
    SecurityPosture,
    ToolPresence,
    UserlandFlavor,
)

# Tools we care about; absence of one is informative even when not blocking.
_TOOLS_TO_CHECK = (
    "sed",
    "gsed",
    "awk",
    "gawk",
    "jq",
    "rg",
    "fd",
    "fdfind",
    "osascript",
    "notify-send",
    "logger",
    "pbcopy",
    "pbpaste",
    "xclip",
    "wl-copy",
    "git",
    "gh",
    "uv",
    "python3",
)

# Substrings inside `sed --version` output that flag the GNU flavor.
_GNU_MARKERS = ("GNU coreutils", "GNU sed", "GNU Awk", "free software")


# ---- abstraction layer (patchable in tests) -------------------------------


def _system() -> str:
    """Return the OS name as reported by `platform.system()` (e.g., 'Darwin', 'Linux')."""
    return platform.system()


def _platform_release() -> str:
    """Return the kernel/release string per `platform.release()`."""
    return platform.release()


def _platform_mac_ver() -> str:
    """macOS short version, e.g., '14.5'. Empty string off macOS."""
    ver = platform.mac_ver()[0]
    return ver or ""


def _hostname() -> str:
    """Return this host's hostname per `socket.gethostname()`."""
    return socket.gethostname()


def _which(name: str) -> str | None:
    """Resolve a tool name to its absolute path, or None if not on PATH."""
    return shutil.which(name)


def _shell_path() -> str:
    """Return the user's shell from $SHELL, or empty string if unset."""
    return os.environ.get("SHELL", "")


def _env(name: str) -> str:
    """Read an environment variable, returning empty string if unset (test-injectable)."""
    return os.environ.get(name, "")


def _run(cmd: list[str], *, timeout: float = 2.0) -> tuple[int, str]:
    """Run a command and return (returncode, combined stdout/stderr).

    Returns (-1, "") on any exception (timeout, OSError, etc.) so callers
    can simply check `rc == 0` for success.
    """
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except (OSError, subprocess.TimeoutExpired):
        return -1, ""


def _path_exists(p: str | Path) -> bool:
    """Whether the given filesystem path exists (test-injectable)."""
    return Path(p).exists()


# ---- detection ------------------------------------------------------------


def detect_os() -> tuple[OS, str]:
    """Identify the OS family and version of this host."""
    sys = _system().lower()
    if sys == "darwin":
        return OS.DARWIN, _platform_mac_ver() or _platform_release()
    if sys == "linux":
        return OS.LINUX, _platform_release()
    if sys == "freebsd":
        return OS.FREEBSD, _platform_release()
    return OS.OTHER, _platform_release()


def detect_userland(os_kind: OS) -> UserlandFlavor:
    """Determine GNU vs BSD userland by checking `sed --version`.

    GNU sed responds to --version; BSD sed does not. We use the exit
    code as the primary signal and fall back to OS-default heuristics
    if probe fails.
    """
    rc, out = _run(["sed", "--version"])
    if rc == 0 and any(m in out for m in _GNU_MARKERS):
        return UserlandFlavor.GNU
    if rc != 0:
        # BSD sed errors out on --version
        return UserlandFlavor.BSD
    # OS default fallback
    if os_kind in (OS.LINUX,):
        return UserlandFlavor.GNU
    if os_kind in (OS.DARWIN, OS.FREEBSD):
        return UserlandFlavor.BSD
    return UserlandFlavor.UNKNOWN


def detect_tool(name: str, *, default_userland: UserlandFlavor) -> ToolPresence:
    """Probe one named tool: presence, path, flavor (GNU/BSD), version."""
    path = _which(name)
    if not path:
        return ToolPresence(path=None, flavor=UserlandFlavor.UNKNOWN, version=None)

    flavor = UserlandFlavor.UNKNOWN
    version: str | None = None

    # Try a cheap version probe; not every tool supports --version
    rc, out = _run([name, "--version"])
    if rc == 0 and out:
        first_line = out.splitlines()[0].strip()
        version = first_line[:80]  # cap to avoid huge strings
        if any(m in out for m in _GNU_MARKERS):
            flavor = UserlandFlavor.GNU
        elif "BSD" in out:
            flavor = UserlandFlavor.BSD

    if flavor == UserlandFlavor.UNKNOWN:
        # gsed/gawk/gfind/etc. are GNU regardless of host; everything else
        # falls back to the host's default userland flavor.
        flavor = UserlandFlavor.GNU if name.startswith("g") else default_userland

    return ToolPresence(path=path, flavor=flavor, version=version)


def detect_gui(os_kind: OS) -> bool:
    """Best-effort GUI presence detection.

    macOS is assumed GUI unless we explicitly know we're remote/headless;
    detecting that reliably is out of scope for v1.
    """
    if os_kind == OS.DARWIN:
        # SSH session? If SSH_CLIENT or SSH_TTY is set, treat as headless
        # (no local console). Imperfect but cheap.
        return not (_env("SSH_TTY") or _env("SSH_CLIENT"))
    if os_kind in (OS.LINUX, OS.FREEBSD):
        return bool(_env("DISPLAY") or _env("WAYLAND_DISPLAY"))
    return False


def detect_notifications(
    os_kind: OS,
    has_osascript: bool,
    has_notify_send: bool,
    has_logger: bool,
) -> NotificationMech:
    """Pick the best available notification mechanism for this host."""
    if os_kind == OS.DARWIN and has_osascript:
        return NotificationMech.OSASCRIPT
    if has_notify_send:
        return NotificationMech.NOTIFY_SEND
    if has_logger:
        return NotificationMech.LOGGER
    return NotificationMech.NONE


def detect_security_posture(os_kind: OS) -> tuple[SecurityPosture, str | None, list[str]]:
    """Detect MDM presence (macOS) and other posture flags.

    Returns (posture, managed_by, blocked_capabilities). Conservative:
    only flags `MANAGED` when there's hard evidence (Jamf/Kandji/Mosyle
    Application Support directory, or `profiles -P` shows enrollment).
    """
    if os_kind != OS.DARWIN:
        return SecurityPosture.TRUSTED, None, []

    # Hard evidence: vendor-specific directories.
    vendor_paths = {
        "jamf": "/Library/Application Support/JamfPro",
        "kandji": "/Library/Kandji",
        "mosyle": "/Library/Application Support/Mosyle",
    }
    for vendor, path in vendor_paths.items():
        if _path_exists(path):
            return (
                SecurityPosture.MANAGED,
                vendor,
                # Conservative: assume AppleScript automation may be
                # blocked under MDM. Render engine then prefers `logger`
                # over `osascript` for notifications even if osascript
                # is installed.
                ["applescript_automation"],
            )

    # Softer evidence: `profiles -P` shows configuration profiles.
    rc, out = _run(["profiles", "-P"], timeout=3.0)
    if rc == 0 and "There are no configuration profiles installed" not in out:
        # Some MDM is enrolled but we don't know vendor. Mark managed.
        return SecurityPosture.MANAGED, "unknown", ["applescript_automation"]

    return SecurityPosture.TRUSTED, None, []


def detect_privileged_writes(os_kind: OS) -> PrivilegedWrites:
    """How should the tool handle privileged writes?

    For v1 we default to AUTO everywhere except FreeBSD, where the common
    workflow is "the assistant writes scripts, the operator runs them with
    elevated privileges" — so privileged writes are surfaced for manual
    execution rather than attempted automatically.
    """
    if os_kind == OS.FREEBSD:
        return PrivilegedWrites.MANUAL
    return PrivilegedWrites.AUTO


# ---- top-level entry ------------------------------------------------------


def probe(*, hostname_override: str | None = None) -> Capabilities:
    """Run the full probe and return a Capabilities snapshot."""
    os_kind, os_version = detect_os()
    userland = detect_userland(os_kind)

    tools: dict[str, ToolPresence] = {}
    for name in _TOOLS_TO_CHECK:
        tools[name] = detect_tool(name, default_userland=userland)

    notifications = detect_notifications(
        os_kind,
        has_osascript=tools.get("osascript", ToolPresence(path=None)).path is not None,
        has_notify_send=tools.get("notify-send", ToolPresence(path=None)).path is not None,
        has_logger=tools.get("logger", ToolPresence(path=None)).path is not None,
    )

    posture, managed_by, blocked = detect_security_posture(os_kind)

    # If the security posture blocks AppleScript automation, downgrade
    # the chosen notification mechanism away from osascript.
    if notifications == NotificationMech.OSASCRIPT and "applescript_automation" in blocked:
        notifications = (
            NotificationMech.LOGGER if tools.get("logger", ToolPresence(path=None)).path else NotificationMech.NONE
        )

    return Capabilities(
        hostname=hostname_override or _hostname(),
        os=os_kind,
        os_version=os_version,
        shell=_shell_path(),
        userland=userland,
        gui=detect_gui(os_kind),
        notifications=notifications,
        security_posture=posture,
        privileged_writes=detect_privileged_writes(os_kind),
        tools=tools,
        blocked_capabilities=blocked,
        managed_by=managed_by,
        probed_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )


__all__ = [
    "detect_gui",
    "detect_notifications",
    "detect_os",
    "detect_privileged_writes",
    "detect_security_posture",
    "detect_tool",
    "detect_userland",
    "probe",
]
