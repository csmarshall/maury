"""Test isolation: prevent tests from reading or writing the developer's
real host-side state (`~/.config/maury/`, `~/.local/state/maury/`, the
legacy `~/.maury-host-id`, or `~/.claude/maury-state/`) and crossing test
boundaries with real state.

Isolation is **XDG-env-based** (`_isolate_xdg_roots`): every test runs
with `$XDG_CONFIG_HOME` and `$XDG_STATE_HOME` pointed at per-test tmp
dirs, so all of `maury.paths` (host-id, repos, lock, state, staging,
audit log, baselines, watermarks, …) resolves under tmp. This is the
post-ADR-0029 isolation seam; it replaced the older
constant-monkeypatching as callers migrated onto `maury.paths`.

Tests that want a host-id or state file present write to the resolver
(`paths.host_id_file()`, `audit_log_path()`, `baseline_path()`, …);
absence is the default (fresh tmp roots). Override from a test body via
`monkeypatch` (pytest honors the most recent setattr/setenv).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from maury import paths


@pytest.fixture(autouse=True)
def _isolate_xdg_roots(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """Point `$XDG_CONFIG_HOME` / `$XDG_STATE_HOME` at per-test tmp dirs so
    everything resolved through `maury.paths` (host-id, repos, lock, state,
    staging) lands under tmp instead of the developer's real roots.

    The `maury/` config and state roots are pre-created so tests can write
    `paths.host_id_file()` / state files without an explicit mkdir (the
    real `init`/`sync` flows create them too)."""
    config_root = tmp_path_factory.mktemp("xdg_config")
    state_root = tmp_path_factory.mktemp("xdg_state")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_root))
    monkeypatch.setenv("XDG_STATE_HOME", str(state_root))
    paths.config_dir().mkdir(parents=True, exist_ok=True)
    paths.state_dir().mkdir(parents=True, exist_ok=True)
    return config_root, state_root
