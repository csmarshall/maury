"""Tests for `src/maury/mining_state.py` — ADR-0043 watermark + reader/writer."""

from __future__ import annotations

from pathlib import Path

import pytest

from maury import paths
from maury.mining_state import (
    MINING_ALGORITHM_VERSION,
    SCHEMA_VERSION,
    MiningStateError,
    MiningWatermark,
    ProjectMiningRecord,
    load_or_init_watermark,
    read_watermark,
    watermark_path,
    write_watermark,
)


def _make_record(mtime: str = "2026-05-15T13:50:00Z") -> ProjectMiningRecord:
    return ProjectMiningRecord(
        last_mined_at="2026-05-15T14:00:00Z",
        last_jsonl_mtime=mtime,
        windows_processed=23,
        findings_count=8,
    )


# ---- schema + round-trip ------------------------------------------------


def test_watermark_round_trip(tmp_path: Path) -> None:
    wm = MiningWatermark()
    wm.update("-Users-jdoe-project-a", _make_record())
    wm.update("-Users-jdoe-project-b", _make_record(mtime="2026-05-14T12:00:00Z"))

    write_watermark(wm)
    loaded = read_watermark()
    assert loaded is not None
    assert loaded.schema_version == SCHEMA_VERSION
    assert loaded.mining_algorithm_version == MINING_ALGORITHM_VERSION
    assert len(loaded.projects) == 2
    assert loaded.record_for("-Users-jdoe-project-a") == _make_record()


def test_watermark_path_under_maury_state(tmp_path: Path) -> None:
    assert watermark_path() == paths.state_dir() / "last-mine.json"


def test_read_absent_returns_none(tmp_path: Path) -> None:
    assert read_watermark() is None


def test_unsupported_schema_version_raises(tmp_path: Path) -> None:
    wp = watermark_path()
    wp.parent.mkdir(parents=True, exist_ok=True)
    wp.write_text('{"schema_version": 99, "projects": {}}')
    with pytest.raises(MiningStateError, match="schema_version"):
        read_watermark()


def test_write_atomic_no_stale_tmp_file(tmp_path: Path) -> None:
    """Per ADR-0029 invariant #4: tmp+rename leaves no stale .tmp file."""
    wm = MiningWatermark()
    wm.update("-Users-jdoe-project", _make_record())
    write_watermark(wm)
    state_dir = paths.state_dir()
    files = sorted(p.name for p in state_dir.iterdir())
    assert files == ["last-mine.json"]


# ---- algorithm-version invalidation -------------------------------------


def test_stale_algorithm_version_returns_fresh_from_load_or_init(tmp_path: Path) -> None:
    """When the on-disk watermark uses an older mining_algorithm_version,
    `load_or_init_watermark()` discards it and returns an empty fresh
    watermark — caller will full-mine and rewrite with the current
    version."""
    wm = MiningWatermark(mining_algorithm_version=999)  # not current
    wm.update("-Users-jdoe-project", _make_record())
    write_watermark(wm)

    loaded = load_or_init_watermark()
    # Stale → returns fresh empty watermark.
    assert loaded.projects == {}
    assert loaded.mining_algorithm_version == MINING_ALGORITHM_VERSION


def test_is_stale_detects_mismatched_version() -> None:
    fresh = MiningWatermark()
    assert not fresh.is_stale()
    stale = MiningWatermark(mining_algorithm_version=MINING_ALGORITHM_VERSION + 1)
    assert stale.is_stale()


def test_load_or_init_returns_empty_when_absent(tmp_path: Path) -> None:
    """Common path on first-ever mining run."""
    loaded = load_or_init_watermark()
    assert loaded.projects == {}
    assert loaded.mining_algorithm_version == MINING_ALGORITHM_VERSION


def test_load_or_init_preserves_current_version_record(tmp_path: Path) -> None:
    wm = MiningWatermark()
    wm.update("-Users-jdoe-project", _make_record())
    write_watermark(wm)

    loaded = load_or_init_watermark()
    assert loaded.record_for("-Users-jdoe-project") is not None


# ---- update + record_for ------------------------------------------------


def test_update_replaces_existing_record(tmp_path: Path) -> None:
    wm = MiningWatermark()
    wm.update("-p", _make_record(mtime="2026-05-01T00:00:00Z"))
    assert wm.record_for("-p").last_jsonl_mtime == "2026-05-01T00:00:00Z"  # type: ignore[union-attr]
    wm.update("-p", _make_record(mtime="2026-05-15T00:00:00Z"))
    assert wm.record_for("-p").last_jsonl_mtime == "2026-05-15T00:00:00Z"  # type: ignore[union-attr]


def test_record_for_missing_returns_none() -> None:
    wm = MiningWatermark()
    assert wm.record_for("not-mined-yet") is None
