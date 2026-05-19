"""Test isolation: prevent tests from reading the developer's real
`~/.maury-host-id` or `~/.claude/maury-state/` and accidentally crossing
test boundaries with real state.

The autouse fixture below points `maury.bootstrap.init_cmd.HOST_ID_FILE`
at a per-test tmp path. Tests that need a specific host-id present (or
absent at a known location) can override the patch from within the
test body — pytest's `monkeypatch` fixture honors the most recent
setattr.
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
