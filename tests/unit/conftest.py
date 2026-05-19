"""Test isolation: prevent tests from reading or writing the developer's
real `~/.maury-host-id` or `~/.claude/maury-state/` and crossing test
boundaries with real state.

Two autouse fixtures:
  - `_isolate_host_id_file`: points `maury.bootstrap.init_cmd.HOST_ID_FILE`
    at a per-test tmp path so the host-identity guard doesn't read the
    developer's real `~/.maury-host-id`.
  - `_isolate_audit_target_dir`: replaces `maury.audit_log.default_target_dir`
    with a function returning a per-test tmp path. Curator-side
    command sites (mode bootstrap/deregister, manifest resolve) that
    don't carry an explicit `target_dir` parameter still call into
    that helper to find `~/.claude/maury-state/audit.jsonl`; without
    this fixture they'd write to the developer's real audit log.

Both fixtures can be overridden from a test body via `monkeypatch`
(pytest honors the most recent setattr).
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_host_id_file(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Point HOST_ID_FILE at a tmp path so the guard doesn't read the
    developer's real `~/.maury-host-id` during tests."""
    iso = tmp_path_factory.mktemp("host_id_isolation") / ".maury-host-id"
    monkeypatch.setattr("maury.bootstrap.init_cmd.HOST_ID_FILE", iso)
    return iso


@pytest.fixture(autouse=True)
def _isolate_audit_target_dir(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Redirect `maury.audit_log.default_target_dir()` to a per-test
    tmp path so curator-side command sites don't write to the
    developer's real `~/.claude/maury-state/audit.jsonl`."""
    iso = tmp_path_factory.mktemp("audit_target")
    monkeypatch.setattr("maury.audit_log.default_target_dir", lambda: iso)
    return iso
