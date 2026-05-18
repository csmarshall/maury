"""Unit tests for `maury.active_sessions`."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from maury.active_sessions import (
    DEFAULT_MAX_AGE,
    EventKind,
    SessionEvent,
    build_state_map,
    is_active,
    parse_event,
    prune,
    read_events,
    rewrite_without_sessions,
    select_prunable,
)


def _ts(s: str) -> str:
    """Helper to build RFC 3339 timestamps from a short suffix."""
    return f"2026-05-18T{s}Z"


def _line(**fields: object) -> str:
    return json.dumps(fields)


# ---- parse_event ----------------------------------------------------------


def test_parse_event_session_start() -> None:
    line = _line(
        event="session_start",
        ts="2026-05-18T10:00:00Z",
        session_id="abc",
        project_path="~/work/foo",
        resumed_from=None,
    )
    ev = parse_event(line)
    assert ev.kind is EventKind.SESSION_START
    assert ev.session_id == "abc"
    assert ev.project_path == "~/work/foo"
    assert ev.resumed_from is None
    assert ev.ts == datetime(2026, 5, 18, 10, 0, 0, tzinfo=UTC)


def test_parse_event_tool_use() -> None:
    line = _line(event="tool_use", ts="2026-05-18T10:05:00Z", session_id="abc")
    ev = parse_event(line)
    assert ev.kind is EventKind.TOOL_USE
    assert ev.session_id == "abc"


def test_parse_event_session_end() -> None:
    line = _line(event="session_end", ts="2026-05-18T11:00:00Z", session_id="abc")
    ev = parse_event(line)
    assert ev.kind is EventKind.SESSION_END


def test_parse_event_resumed_from_carried() -> None:
    line = _line(
        event="session_start",
        ts="2026-05-18T10:00:00Z",
        session_id="def",
        resumed_from="abc",
    )
    ev = parse_event(line)
    assert ev.resumed_from == "abc"


def test_parse_event_rejects_unknown_kind() -> None:
    line = _line(event="garbage", ts="2026-05-18T10:00:00Z", session_id="abc")
    with pytest.raises(ValueError, match="unknown event kind"):
        parse_event(line)


def test_parse_event_rejects_missing_session_id() -> None:
    line = _line(event="session_start", ts="2026-05-18T10:00:00Z")
    with pytest.raises(ValueError, match="missing or empty 'session_id'"):
        parse_event(line)


def test_parse_event_rejects_naive_timestamp() -> None:
    line = _line(event="session_start", ts="2026-05-18T10:00:00", session_id="abc")
    with pytest.raises(ValueError, match="missing timezone"):
        parse_event(line)


def test_parse_event_rejects_malformed_json() -> None:
    with pytest.raises(json.JSONDecodeError):
        parse_event("{not valid json")


# ---- read_events ----------------------------------------------------------


def test_read_events_missing_file_returns_empty(tmp_path: Path) -> None:
    events, skipped = read_events(tmp_path / "no-such.jsonl")
    assert events == []
    assert skipped == 0


def test_read_events_skips_unparseable(tmp_path: Path) -> None:
    p = tmp_path / "log.jsonl"
    p.write_text(
        _line(event="session_start", ts="2026-05-18T10:00:00Z", session_id="abc") + "\n"
        "{this is not json\n"
        "\n"  # blank line
         + _line(event="session_end", ts="2026-05-18T11:00:00Z", session_id="abc") + "\n"
    )
    events, skipped = read_events(p)
    assert len(events) == 2
    assert skipped == 1


# ---- build_state_map ------------------------------------------------------


def test_build_state_map_active_session() -> None:
    events = [
        parse_event(_line(event="session_start", ts="2026-05-18T10:00:00Z", session_id="abc")),
        parse_event(_line(event="tool_use", ts="2026-05-18T10:05:00Z", session_id="abc")),
    ]
    states = build_state_map(events)
    assert "abc" in states
    s = states["abc"]
    assert s.started_at == datetime(2026, 5, 18, 10, 0, 0, tzinfo=UTC)
    assert s.last_tool_use_at == datetime(2026, 5, 18, 10, 5, 0, tzinfo=UTC)
    assert s.ended_at is None
    assert is_active(s)


def test_build_state_map_ended_session() -> None:
    events = [
        parse_event(_line(event="session_start", ts="2026-05-18T10:00:00Z", session_id="abc")),
        parse_event(_line(event="session_end", ts="2026-05-18T11:00:00Z", session_id="abc")),
    ]
    states = build_state_map(events)
    s = states["abc"]
    assert s.ended_at == datetime(2026, 5, 18, 11, 0, 0, tzinfo=UTC)
    assert not is_active(s)


def test_build_state_map_tool_use_picks_latest() -> None:
    events = [
        parse_event(_line(event="session_start", ts="2026-05-18T10:00:00Z", session_id="abc")),
        parse_event(_line(event="tool_use", ts="2026-05-18T10:30:00Z", session_id="abc")),
        parse_event(_line(event="tool_use", ts="2026-05-18T10:15:00Z", session_id="abc")),
        parse_event(_line(event="tool_use", ts="2026-05-18T10:45:00Z", session_id="abc")),
    ]
    states = build_state_map(events)
    assert states["abc"].last_tool_use_at == datetime(2026, 5, 18, 10, 45, 0, tzinfo=UTC)


def test_build_state_map_orphan_events_skipped() -> None:
    """SESSION_END without prior SESSION_START is dropped (cannot reconstruct started_at)."""
    events = [
        parse_event(_line(event="session_end", ts="2026-05-18T11:00:00Z", session_id="orphan")),
    ]
    states = build_state_map(events)
    assert "orphan" not in states


def test_build_state_map_handles_multiple_sessions() -> None:
    events = [
        parse_event(_line(event="session_start", ts="2026-05-18T10:00:00Z", session_id="a")),
        parse_event(_line(event="session_start", ts="2026-05-18T10:05:00Z", session_id="b")),
        parse_event(_line(event="session_end", ts="2026-05-18T10:30:00Z", session_id="a")),
    ]
    states = build_state_map(events)
    assert is_active(states["b"])
    assert not is_active(states["a"])


# ---- select_prunable ------------------------------------------------------


def test_select_prunable_only_ended_sessions() -> None:
    events = [
        parse_event(_line(event="session_start", ts="2026-05-17T10:00:00Z", session_id="old")),
        parse_event(_line(event="session_end", ts="2026-05-17T11:00:00Z", session_id="old")),
        parse_event(_line(event="session_start", ts="2026-05-18T09:00:00Z", session_id="recent")),
        parse_event(_line(event="session_end", ts="2026-05-18T10:00:00Z", session_id="recent")),
        parse_event(_line(event="session_start", ts="2026-05-18T11:00:00Z", session_id="open")),
    ]
    states = build_state_map(events)
    now = datetime(2026, 5, 18, 12, 0, 0, tzinfo=UTC)
    prunable = select_prunable(states, now=now, max_age=timedelta(hours=24))
    ids = {s.session_id for s in prunable}
    # "old" ended 25h ago → prunable.
    # "recent" ended 2h ago → too recent.
    # "open" has no session_end → conservative, not prunable.
    assert ids == {"old"}


def test_select_prunable_never_prunes_active_session() -> None:
    """Even very old sessions without session_end are kept."""
    events = [
        parse_event(_line(event="session_start", ts="2025-01-01T00:00:00Z", session_id="ancient")),
    ]
    states = build_state_map(events)
    now = datetime(2026, 5, 18, 12, 0, 0, tzinfo=UTC)
    prunable = select_prunable(states, now=now, max_age=timedelta(hours=24))
    assert prunable == []


def test_select_prunable_returns_sorted_by_ended_at() -> None:
    events: list[SessionEvent] = []
    for i, ts in enumerate(["09:00:00", "08:00:00", "10:00:00"]):
        sid = f"s{i}"
        events.append(parse_event(_line(event="session_start", ts="2026-05-17T07:00:00Z", session_id=sid)))
        events.append(parse_event(_line(event="session_end", ts=f"2026-05-17T{ts}Z", session_id=sid)))
    states = build_state_map(events)
    now = datetime(2026, 5, 18, 12, 0, 0, tzinfo=UTC)
    prunable = select_prunable(states, now=now, max_age=timedelta(hours=24))
    assert [s.session_id for s in prunable] == ["s1", "s0", "s2"]  # by ended_at ascending


# ---- rewrite_without_sessions ---------------------------------------------


def test_rewrite_without_sessions_drops_events_for_named_sessions(tmp_path: Path) -> None:
    p = tmp_path / "log.jsonl"
    p.write_text(
        _line(event="session_start", ts="2026-05-17T10:00:00Z", session_id="old")
        + "\n"
        + _line(event="session_end", ts="2026-05-17T11:00:00Z", session_id="old")
        + "\n"
        + _line(event="session_start", ts="2026-05-18T11:00:00Z", session_id="open")
        + "\n"
    )
    kept = rewrite_without_sessions(p, {"old"})
    assert kept == 1
    remaining = p.read_text().splitlines()
    assert all('"open"' in ln for ln in remaining if ln.strip())


def test_rewrite_without_sessions_preserves_unparseable_lines(tmp_path: Path) -> None:
    """Partial writes from concurrent sessions must not be silently dropped."""
    p = tmp_path / "log.jsonl"
    p.write_text(
        _line(event="session_start", ts="2026-05-17T10:00:00Z", session_id="old")
        + "\n"
        + "{partial wri\n"
        + _line(event="session_end", ts="2026-05-17T11:00:00Z", session_id="old")
        + "\n"
    )
    kept = rewrite_without_sessions(p, {"old"})
    # Two events for "old" dropped; the partial line is kept.
    assert kept == 1
    assert "partial wri" in p.read_text()


def test_rewrite_without_sessions_handles_missing_file(tmp_path: Path) -> None:
    p = tmp_path / "no-such.jsonl"
    assert rewrite_without_sessions(p, {"any"}) == 0


# ---- prune (end-to-end) ---------------------------------------------------


def _seed_log(p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        _line(event="session_start", ts="2026-05-17T10:00:00Z", session_id="old")
        + "\n"
        + _line(event="session_end", ts="2026-05-17T11:00:00Z", session_id="old")
        + "\n"
        + _line(event="session_start", ts="2026-05-18T09:00:00Z", session_id="recent")
        + "\n"
        + _line(event="session_end", ts="2026-05-18T10:00:00Z", session_id="recent")
        + "\n"
        + _line(event="session_start", ts="2026-05-18T11:00:00Z", session_id="open")
        + "\n"
    )


def test_prune_actually_writes_when_not_dry_run(tmp_path: Path) -> None:
    p = tmp_path / "log.jsonl"
    _seed_log(p)
    now = datetime(2026, 5, 18, 12, 0, 0, tzinfo=UTC)
    result = prune(p, now=now, max_age=timedelta(hours=24), dry_run=False)
    assert result.pruned_session_ids == ("old",)
    assert "open" in result.kept_session_ids
    assert "recent" in result.kept_session_ids
    # Log should no longer contain "old"
    contents = p.read_text()
    assert '"old"' not in contents
    assert '"recent"' in contents
    assert '"open"' in contents


def test_prune_dry_run_leaves_file_untouched(tmp_path: Path) -> None:
    p = tmp_path / "log.jsonl"
    _seed_log(p)
    before = p.read_text()
    now = datetime(2026, 5, 18, 12, 0, 0, tzinfo=UTC)
    result = prune(p, now=now, max_age=timedelta(hours=24), dry_run=True)
    assert result.pruned_session_ids == ("old",)
    assert p.read_text() == before


def test_prune_no_prunable_sessions_is_noop(tmp_path: Path) -> None:
    p = tmp_path / "log.jsonl"
    p.write_text(_line(event="session_start", ts="2026-05-18T11:00:00Z", session_id="open") + "\n")
    now = datetime(2026, 5, 18, 12, 0, 0, tzinfo=UTC)
    result = prune(p, now=now, max_age=timedelta(hours=24), dry_run=False)
    assert result.pruned_session_ids == ()
    assert "open" in result.kept_session_ids


def test_default_max_age_is_24h() -> None:
    assert timedelta(hours=24) == DEFAULT_MAX_AGE
