"""Tests for `maury.paths` — XDG-aware host-side root resolution (ADR-0029)."""

from __future__ import annotations

from pathlib import Path

import pytest

from maury import paths


def test_config_dir_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("/home/jdoe")))
    assert paths.config_dir() == Path("/home/jdoe/.config/maury")


def test_state_dir_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("/home/jdoe")))
    assert paths.state_dir() == Path("/home/jdoe/.local/state/maury")


def test_config_dir_honors_absolute_xdg(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", "/custom/cfg")
    assert paths.config_dir() == Path("/custom/cfg/maury")


def test_state_dir_honors_absolute_xdg(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", "/custom/state")
    assert paths.state_dir() == Path("/custom/state/maury")


def test_relative_xdg_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """Per the XDG spec, a relative XDG_* value is ignored → default used."""
    monkeypatch.setenv("XDG_CONFIG_HOME", "relative/path")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("/home/jdoe")))
    assert paths.config_dir() == Path("/home/jdoe/.config/maury")


def test_derived_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", "/c")
    monkeypatch.setenv("XDG_STATE_HOME", "/s")
    assert paths.host_id_file() == Path("/c/maury/host-id")
    assert paths.repos_root() == Path("/c/maury/repos")
    assert paths.lock_file() == Path("/c/maury/.lock")
    assert paths.staging_dir() == Path("/s/maury/staging")


def test_render_target_is_claude_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("/home/jdoe")))
    # The one path maury does NOT own — Claude Code's, fixed.
    assert paths.render_target_dir() == Path("/home/jdoe/.claude")


def test_state_and_config_roots_are_disjoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """State must not live under config or under the render target — the
    ADR-0029 ownership split."""
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("/home/jdoe")))
    state = paths.state_dir()
    assert not state.is_relative_to(paths.config_dir())
    assert not state.is_relative_to(paths.render_target_dir())
