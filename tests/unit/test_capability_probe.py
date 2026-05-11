"""Tests for the capability probe.

Mocks the OS interaction layer so the BSD/GNU/managed-mac branches
can be exercised on any host.
"""

from __future__ import annotations

from typing import Any

import pytest

import maury.capability.probe as probe_module
from maury.capability.probe import (
    detect_gui,
    detect_notifications,
    detect_os,
    detect_privileged_writes,
    detect_security_posture,
    detect_tool,
    detect_userland,
    probe,
)
from maury.capability.schema import (
    OS,
    Capabilities,
    NotificationMech,
    PrivilegedWrites,
    SecurityPosture,
    ToolPresence,
    UserlandFlavor,
    dumps,
    loads,
)

# ---- helpers --------------------------------------------------------------


def patch(monkeypatch: Any, **overrides: Any) -> None:
    """Patch the probe module's OS-interaction shims."""
    for name, value in overrides.items():
        monkeypatch.setattr(probe_module, name, value)


# ---- detect_os ------------------------------------------------------------


def test_detect_os_darwin(monkeypatch: pytest.MonkeyPatch) -> None:
    patch(monkeypatch, _system=lambda: "Darwin", _platform_mac_ver=lambda: "14.5")
    os_kind, ver = detect_os()
    assert os_kind == OS.DARWIN
    assert ver == "14.5"


def test_detect_os_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    patch(
        monkeypatch,
        _system=lambda: "Linux",
        _platform_release=lambda: "6.5.0-25-generic",
    )
    os_kind, ver = detect_os()
    assert os_kind == OS.LINUX
    assert "6.5" in ver


def test_detect_os_freebsd(monkeypatch: pytest.MonkeyPatch) -> None:
    patch(
        monkeypatch,
        _system=lambda: "FreeBSD",
        _platform_release=lambda: "14.2-RELEASE",
    )
    os_kind, ver = detect_os()
    assert os_kind == OS.FREEBSD
    assert "14.2" in ver


def test_detect_os_other(monkeypatch: pytest.MonkeyPatch) -> None:
    patch(
        monkeypatch,
        _system=lambda: "Haiku",
        _platform_release=lambda: "1.0",
    )
    os_kind, _ver = detect_os()
    assert os_kind == OS.OTHER


# ---- detect_userland ------------------------------------------------------


def test_userland_gnu_when_sed_version_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    patch(monkeypatch, _run=lambda cmd, timeout=2.0: (0, "GNU sed version 4.9"))
    assert detect_userland(OS.LINUX) == UserlandFlavor.GNU


def test_userland_bsd_when_sed_version_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    patch(monkeypatch, _run=lambda cmd, timeout=2.0: (1, "sed: illegal option"))
    assert detect_userland(OS.DARWIN) == UserlandFlavor.BSD


def test_userland_falls_back_to_os_default_on_unrecognized(monkeypatch: pytest.MonkeyPatch) -> None:
    """If sed --version exits 0 but we don't see GNU markers, fall back to OS default."""
    patch(monkeypatch, _run=lambda cmd, timeout=2.0: (0, "some other sed implementation"))
    assert detect_userland(OS.LINUX) == UserlandFlavor.GNU
    assert detect_userland(OS.DARWIN) == UserlandFlavor.BSD


# ---- detect_tool ----------------------------------------------------------


def test_detect_tool_present_with_gnu_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    patch(
        monkeypatch,
        _which=lambda name: f"/usr/bin/{name}",
        _run=lambda cmd, timeout=2.0: (0, "GNU sed version 4.9\nlicense info"),
    )
    t = detect_tool("sed", default_userland=UserlandFlavor.GNU)
    assert t.path == "/usr/bin/sed"
    assert t.flavor == UserlandFlavor.GNU
    assert t.version is not None
    assert "GNU sed" in t.version


def test_detect_tool_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    patch(monkeypatch, _which=lambda name: None)
    t = detect_tool("nonexistent", default_userland=UserlandFlavor.GNU)
    assert t.path is None
    assert t.flavor == UserlandFlavor.UNKNOWN


def test_detect_g_prefixed_tool_is_always_gnu(monkeypatch: pytest.MonkeyPatch) -> None:
    """gsed, gawk, etc. are always GNU regardless of host userland."""
    patch(
        monkeypatch,
        _which=lambda name: f"/opt/homebrew/bin/{name}" if name == "gsed" else None,
        _run=lambda cmd, timeout=2.0: (-1, ""),  # version probe fails
    )
    t = detect_tool("gsed", default_userland=UserlandFlavor.BSD)
    assert t.flavor == UserlandFlavor.GNU


# ---- detect_gui -----------------------------------------------------------


def test_gui_macos_local_session(monkeypatch: pytest.MonkeyPatch) -> None:
    patch(monkeypatch, _env=lambda name: "")
    assert detect_gui(OS.DARWIN) is True


def test_gui_macos_ssh_session(monkeypatch: pytest.MonkeyPatch) -> None:
    patch(monkeypatch, _env=lambda name: "x" if name == "SSH_TTY" else "")
    assert detect_gui(OS.DARWIN) is False


def test_gui_linux_with_display(monkeypatch: pytest.MonkeyPatch) -> None:
    patch(monkeypatch, _env=lambda name: ":0" if name == "DISPLAY" else "")
    assert detect_gui(OS.LINUX) is True


def test_gui_linux_with_wayland(monkeypatch: pytest.MonkeyPatch) -> None:
    patch(
        monkeypatch,
        _env=lambda name: "wayland-0" if name == "WAYLAND_DISPLAY" else "",
    )
    assert detect_gui(OS.LINUX) is True


def test_gui_linux_headless(monkeypatch: pytest.MonkeyPatch) -> None:
    patch(monkeypatch, _env=lambda name: "")
    assert detect_gui(OS.LINUX) is False


def test_gui_freebsd_headless_default(monkeypatch: pytest.MonkeyPatch) -> None:
    patch(monkeypatch, _env=lambda name: "")
    assert detect_gui(OS.FREEBSD) is False


# ---- detect_notifications -------------------------------------------------


def test_notifications_macos_picks_osascript() -> None:
    n = detect_notifications(OS.DARWIN, has_osascript=True, has_notify_send=False, has_logger=True)
    assert n == NotificationMech.OSASCRIPT


def test_notifications_linux_picks_notify_send() -> None:
    n = detect_notifications(OS.LINUX, has_osascript=False, has_notify_send=True, has_logger=True)
    assert n == NotificationMech.NOTIFY_SEND


def test_notifications_freebsd_falls_back_to_logger() -> None:
    n = detect_notifications(OS.FREEBSD, has_osascript=False, has_notify_send=False, has_logger=True)
    assert n == NotificationMech.LOGGER


def test_notifications_none_when_nothing_available() -> None:
    n = detect_notifications(OS.LINUX, has_osascript=False, has_notify_send=False, has_logger=False)
    assert n == NotificationMech.NONE


# ---- detect_security_posture ---------------------------------------------


def test_security_posture_non_macos_is_trusted(monkeypatch: pytest.MonkeyPatch) -> None:
    patch(monkeypatch, _path_exists=lambda p: False, _run=lambda cmd, timeout=2.0: (1, ""))
    posture, vendor, blocked = detect_security_posture(OS.LINUX)
    assert posture == SecurityPosture.TRUSTED
    assert vendor is None
    assert blocked == []


def test_security_posture_jamf_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    patch(
        monkeypatch,
        _path_exists=lambda p: str(p) == "/Library/Application Support/JamfPro",
        _run=lambda cmd, timeout=2.0: (1, ""),
    )
    posture, vendor, blocked = detect_security_posture(OS.DARWIN)
    assert posture == SecurityPosture.MANAGED
    assert vendor == "jamf"
    assert "applescript_automation" in blocked


def test_security_posture_kandji_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    patch(
        monkeypatch,
        _path_exists=lambda p: str(p) == "/Library/Kandji",
        _run=lambda cmd, timeout=2.0: (1, ""),
    )
    posture, vendor, _blocked = detect_security_posture(OS.DARWIN)
    assert posture == SecurityPosture.MANAGED
    assert vendor == "kandji"


def test_security_posture_macos_with_profiles_command(monkeypatch: pytest.MonkeyPatch) -> None:
    """`profiles -P` returning anything other than 'no profiles installed' = managed."""
    patch(
        monkeypatch,
        _path_exists=lambda p: False,
        _run=lambda cmd, timeout=2.0: (
            (0, "_computerlevel[1] attribute: profileIdentifier: com.example.policy")
            if cmd[0] == "profiles"
            else (1, "")
        ),
    )
    posture, vendor, _ = detect_security_posture(OS.DARWIN)
    assert posture == SecurityPosture.MANAGED
    assert vendor == "unknown"


def test_security_posture_macos_unmanaged(monkeypatch: pytest.MonkeyPatch) -> None:
    patch(
        monkeypatch,
        _path_exists=lambda p: False,
        _run=lambda cmd, timeout=2.0: (
            (0, "There are no configuration profiles installed") if cmd[0] == "profiles" else (1, "")
        ),
    )
    posture, vendor, blocked = detect_security_posture(OS.DARWIN)
    assert posture == SecurityPosture.TRUSTED
    assert vendor is None
    assert blocked == []


# ---- detect_privileged_writes --------------------------------------------


def test_privileged_writes_freebsd_is_manual() -> None:
    assert detect_privileged_writes(OS.FREEBSD) == PrivilegedWrites.MANUAL


def test_privileged_writes_macos_is_auto() -> None:
    assert detect_privileged_writes(OS.DARWIN) == PrivilegedWrites.AUTO


def test_privileged_writes_linux_is_auto() -> None:
    assert detect_privileged_writes(OS.LINUX) == PrivilegedWrites.AUTO


# ---- end-to-end probe ----------------------------------------------------


def test_probe_runs_against_real_host() -> None:
    """Smoke test: probe should never raise on the host we're running on."""
    caps = probe()
    assert caps.hostname  # non-empty
    assert caps.os in {OS.DARWIN, OS.LINUX, OS.FREEBSD, OS.OTHER}
    assert isinstance(caps.tools, dict)
    assert "sed" in caps.tools  # sed is checked on every host
    assert caps.probed_at  # ISO timestamp set


def test_probe_with_hostname_override() -> None:
    caps = probe(hostname_override="phantom-host")
    assert caps.hostname == "phantom-host"


def test_probe_simulated_managed_mac_downgrades_notifications(monkeypatch: pytest.MonkeyPatch) -> None:
    """A managed mac with osascript installed should downgrade notification mech to logger."""
    patch(
        monkeypatch,
        _system=lambda: "Darwin",
        _platform_mac_ver=lambda: "14.5",
        _platform_release=lambda: "23.5.0",
        _hostname=lambda: "work-laptop",
        _shell_path=lambda: "/bin/zsh",
        _which=lambda name: f"/usr/bin/{name}" if name in ("osascript", "logger", "sed") else None,
        _run=lambda cmd, timeout=2.0: (0, "GNU sed") if cmd == ["sed", "--version"] else (-1, ""),
        _env=lambda name: "",
        _path_exists=lambda p: str(p) == "/Library/Application Support/JamfPro",
    )
    caps = probe()
    assert caps.os == OS.DARWIN
    assert caps.security_posture == SecurityPosture.MANAGED
    assert caps.managed_by == "jamf"
    assert "applescript_automation" in caps.blocked_capabilities
    # osascript is installed but should be downgraded due to MDM block
    assert caps.notifications == NotificationMech.LOGGER


# ---- schema serialization -------------------------------------------------


def test_capabilities_round_trip() -> None:
    caps = Capabilities(
        hostname="test-host",
        os=OS.LINUX,
        os_version="6.5",
        shell="/bin/bash",
        userland=UserlandFlavor.GNU,
        gui=False,
        notifications=NotificationMech.NOTIFY_SEND,
        security_posture=SecurityPosture.TRUSTED,
        privileged_writes=PrivilegedWrites.AUTO,
        tools={
            "sed": ToolPresence(path="/usr/bin/sed", flavor=UserlandFlavor.GNU, version="GNU sed 4.9"),
            "rg": ToolPresence(path="/usr/bin/rg", flavor=UserlandFlavor.UNKNOWN, version="ripgrep 14"),
        },
        probed_at="2026-05-06T12:00:00+00:00",
    )
    serialized = dumps(caps)
    restored = loads(serialized)
    assert restored.hostname == caps.hostname
    assert restored.os == caps.os
    assert restored.notifications == caps.notifications
    assert restored.tools["sed"].flavor == UserlandFlavor.GNU
    assert restored.has_tool("sed")
    assert not restored.has_tool("ghost")
