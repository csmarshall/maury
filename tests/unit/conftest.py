"""Test isolation: prevent tests from reading or writing the developer's
real host-side state (`~/.config/maury/`, `~/.local/state/maury/`, the
legacy `~/.maury-host-id`, or `~/.claude/maury-state/`) and crossing test
boundaries with real state.

Primary isolation is **XDG-env-based** (`_isolate_xdg_roots`): every test
runs with `$XDG_CONFIG_HOME` and `$XDG_STATE_HOME` pointed at per-test tmp
dirs, so all of `maury.paths` (host-id, repos, lock, state, staging)
resolves under tmp. This is the post-ADR-0029 isolation seam and replaces
the older constant-monkeypatching as callers migrate onto `maury.paths`.

The host-id, repos, lock, and staging paths are now resolved through
`maury.paths` and isolated entirely by `_isolate_xdg_roots`. Tests that
want a host-id present write to `paths.host_id_file()`; absence is the
default (fresh tmp config root).

Transitional fixture (removed as its callers migrate onto `maury.paths`):
  - `_isolate_audit_target_dir`: replaces `maury.audit_log.default_target_dir`
    with a function returning a per-test tmp path, for curator-side command
    sites that still resolve `maury-state/audit.jsonl` under a target_dir.

All fixtures can be overridden from a test body via `monkeypatch`
(pytest honors the most recent setattr/setenv).
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


@pytest.fixture(autouse=True)
def _isolate_audit_target_dir(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Redirect `maury.audit_log.default_target_dir()` to a per-test
    tmp path so curator-side command sites don't write to the
    developer's real `~/.claude/maury-state/audit.jsonl`."""
    iso = tmp_path_factory.mktemp("audit_target")
    monkeypatch.setattr("maury.audit_log.default_target_dir", lambda: iso)
    return iso
