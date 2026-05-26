"""Host-side path resolution (ADR-0029) — XDG Base Directory aware.

maury's host-side files live under three roots, split by **ownership**:

- **config** — `$XDG_CONFIG_HOME/maury` (default `~/.config/maury/`):
  the `host-id` file, synced repo clones (`repos/`), the `.lock` mutex.
- **state** — `$XDG_STATE_HOME/maury` (default `~/.local/state/maury/`):
  operational state (drift baseline, audit log, session logs, watermarks,
  identity baseline, …) plus the `staging/` subtree.
- **render output** — `~/.claude/` (Claude Code's directory, fixed by
  that tool): maury writes only its *rendered config* here. maury stores
  **none of its own state** under `~/.claude/` — that would couple maury's
  invariants to a foreign tool's directory lifecycle (a Claude Code
  reset/reinstall could wipe maury state that isn't Claude Code's).

This module is the single place those roots are resolved; callers must go
through it rather than hardcoding `Path.home() / ".claude" / ...` etc.

Per the XDG Base Directory spec, `$XDG_CONFIG_HOME` / `$XDG_STATE_HOME`
are honored only when set to an **absolute** path; otherwise the spec'd
default is used.
"""

from __future__ import annotations

import os
from pathlib import Path

_APP = "maury"


def _xdg_base(env_var: str, default: Path) -> Path:
    """Resolve an XDG base dir: honor `env_var` iff it's an absolute path,
    else fall back to `default` (per the XDG Base Directory spec, relative
    or unset values are ignored)."""
    val = os.environ.get(env_var)
    if val and os.path.isabs(val):
        return Path(val)
    return default


def config_dir() -> Path:
    """maury's config root — `$XDG_CONFIG_HOME/maury` (default `~/.config/maury`)."""
    return _xdg_base("XDG_CONFIG_HOME", Path.home() / ".config") / _APP


def state_dir() -> Path:
    """maury's state root — `$XDG_STATE_HOME/maury` (default `~/.local/state/maury`)."""
    return _xdg_base("XDG_STATE_HOME", Path.home() / ".local" / "state") / _APP


def host_id_file() -> Path:
    """The host's stable identity file (config root): `<config>/host-id`."""
    return config_dir() / "host-id"


def repos_root() -> Path:
    """Where synced repo clones live (config root): `<config>/repos`."""
    return config_dir() / "repos"


def lock_file() -> Path:
    """The fcntl mutex (config root): `<config>/.lock`."""
    return config_dir() / ".lock"


def staging_dir() -> Path:
    """Capture + offline-mining staging (state root): `<state>/staging`."""
    return state_dir() / "staging"


def render_target_dir() -> Path:
    """Claude Code's directory, where maury writes its rendered output
    (NOT maury's own state). Fixed at `~/.claude/` by Claude Code."""
    return Path.home() / ".claude"


__all__ = [
    "config_dir",
    "host_id_file",
    "lock_file",
    "render_target_dir",
    "repos_root",
    "staging_dir",
    "state_dir",
]
