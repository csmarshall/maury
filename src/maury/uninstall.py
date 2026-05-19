"""`maury uninstall` — strip maury's footprint from a host.

Per ADR-0023 §8, uninstall must:

1. Strip every `# maury-managed`-marked entry from `settings.json`'s
   `hooks` block; leave non-marked entries (user hooks) untouched.
2. Delete `~/.claude/bin/maury-*` and `~/.claude/bin/maury-tools.sh`.
3. Delete `~/.claude/maury-state/`.
4. Delete `~/.maury-host-id`.
5. Leave repo clones alone (the user may still want their git history).

This module owns the file-system mutations; the CLI subcommand in
cli.py wires it up and prints the summary.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

MARKER: Final[str] = "# maury-managed"


@dataclass(frozen=True)
class UninstallSummary:
    """Result of an uninstall pass."""

    settings_hooks_removed: int = 0
    """Number of marker-bearing hook entries stripped from settings.json."""

    settings_hooks_kept: int = 0
    """Number of non-marker hook entries left in place."""

    bin_files_removed: tuple[Path, ...] = field(default_factory=tuple)
    """maury-* binaries deleted from ~/.claude/bin/."""

    state_dir_removed: bool = False
    """Whether ~/.claude/maury-state/ existed and was removed."""

    host_id_removed: bool = False
    """Whether ~/.maury-host-id existed and was removed."""

    settings_file_existed: bool = False
    """Whether settings.json was present to be edited."""


def _is_maury_managed(entry: Any) -> bool:
    """True iff a hooks-array entry carries the `# maury-managed` marker.

    A hooks entry looks like:
      {"matcher": "...", "hooks": [{"type": "command", "command": "... # maury-managed"}]}
    OR (legacy / direct shape):
      {"type": "command", "command": "... # maury-managed"}

    The marker is searched in either the entry's own command field or in
    any nested `hooks[].command`.
    """
    if not isinstance(entry, dict):
        return False
    cmd = entry.get("command")
    if isinstance(cmd, str) and MARKER in cmd:
        return True
    nested = entry.get("hooks")
    if isinstance(nested, list):
        for sub in nested:
            if isinstance(sub, dict):
                sub_cmd = sub.get("command")
                if isinstance(sub_cmd, str) and MARKER in sub_cmd:
                    return True
    return False


def strip_marker_hooks(settings: dict[str, Any]) -> tuple[dict[str, Any], int, int]:
    """Return (new_settings, removed_count, kept_count).

    Walks every event-name key under `settings["hooks"]`. For each
    event's list, drops entries that contain the marker; keeps the rest.
    Events whose list becomes empty are removed entirely (cleaner output).
    """
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return settings, 0, 0

    removed = 0
    kept = 0
    new_hooks: dict[str, Any] = {}
    for event_name, event_list in hooks.items():
        if not isinstance(event_list, list):
            new_hooks[event_name] = event_list
            continue
        kept_entries: list[Any] = []
        for entry in event_list:
            if _is_maury_managed(entry):
                removed += 1
            else:
                kept_entries.append(entry)
                kept += 1
        if kept_entries:
            new_hooks[event_name] = kept_entries

    new_settings = dict(settings)
    if new_hooks:
        new_settings["hooks"] = new_hooks
    else:
        new_settings.pop("hooks", None)
    return new_settings, removed, kept


def _rewrite_settings(settings_path: Path) -> tuple[int, int, bool]:
    """Rewrite settings.json with marker-bearing hooks removed.

    Returns (removed, kept, file_existed).
    """
    if not settings_path.is_file():
        return 0, 0, False
    try:
        loaded = json.loads(settings_path.read_text())
    except json.JSONDecodeError:
        # Malformed settings.json: leave untouched. The user will see this
        # in maury status / doctor; we don't compound the problem.
        return 0, 0, True
    if not isinstance(loaded, dict):
        return 0, 0, True
    new_settings, removed, kept = strip_marker_hooks(loaded)
    if removed > 0:
        settings_path.write_text(json.dumps(new_settings, indent=2) + "\n")
    return removed, kept, True


def _delete_bin_files(bin_dir: Path) -> tuple[Path, ...]:
    """Delete every `maury-*` file (and `maury-tools.sh`) under bin_dir."""
    if not bin_dir.is_dir():
        return ()
    removed: list[Path] = []
    for child in sorted(bin_dir.iterdir()):
        if child.is_file() and (child.name.startswith("maury-") or child.name == "maury-tools.sh"):
            child.unlink()
            removed.append(child)
    return tuple(removed)


def _delete_state_dir(state_dir: Path, *, preserve: tuple[str, ...] = ()) -> bool:
    """Delete the maury-state dir if present, except for `preserve` filenames.

    Returns whether the state dir existed at entry. Per ADR-0023 §8 +
    ADR-0035, `audit.jsonl` is preserved as a forensic trail of "who
    removed maury" — the directory is left behind iff anything was
    preserved.
    """
    if not state_dir.is_dir():
        return False
    preserve_set = set(preserve)
    for child in sorted(state_dir.iterdir()):
        if child.name in preserve_set:
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
    # If we preserved at least one file, keep the directory; otherwise
    # remove it entirely.
    remaining = list(state_dir.iterdir())
    if not remaining:
        state_dir.rmdir()
    return True


def _delete_host_id(host_id_path: Path) -> bool:
    """Delete ~/.maury-host-id if present. Returns whether it existed."""
    if host_id_path.is_file():
        host_id_path.unlink()
        return True
    return False


def run_uninstall(
    *,
    target_dir: Path,
    home_dir: Path,
) -> UninstallSummary:
    """Execute the full uninstall sequence.

    `target_dir` is the maury-managed Claude Code directory (typically
    `~/.claude`); `home_dir` is the user's home (`~`) so `~/.maury-host-id`
    can be located. Splitting the two enables hermetic testing.

    Per ADR-0035 §"`uninstall_completed`": writes the audit event
    BEFORE deleting state, and `audit.jsonl` is preserved alongside
    the user content per ADR-0023 §8's "leaves user content alone"
    guarantee — the forensic trail of "who removed maury" survives.
    """
    import contextlib

    from maury.audit_log import AuditLogError, log

    settings_path = target_dir / "settings.json"
    removed_hooks, kept_hooks, settings_existed = _rewrite_settings(settings_path)

    bin_dir = target_dir / "bin"
    removed_bins = _delete_bin_files(bin_dir)

    # Write the audit event BEFORE wiping state (per ADR-0035), and
    # preserve audit.jsonl through the wipe. Don't block uninstall on
    # an audit-log write failure.
    with contextlib.suppress(AuditLogError):
        log(
            target_dir,
            "uninstall_completed",
            user_hooks_kept=kept_hooks,
            scripts_removed=len(removed_bins),
        )

    state_dir = target_dir / "maury-state"
    state_removed = _delete_state_dir(state_dir, preserve=("audit.jsonl",))

    host_id_path = home_dir / ".maury-host-id"
    host_id_removed = _delete_host_id(host_id_path)

    return UninstallSummary(
        settings_hooks_removed=removed_hooks,
        settings_hooks_kept=kept_hooks,
        bin_files_removed=removed_bins,
        state_dir_removed=state_removed,
        host_id_removed=host_id_removed,
        settings_file_existed=settings_existed,
    )
