"""Unit tests for `maury.audit_log` (ADR-0035)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from maury.audit_log import (
    ACTOR_HOOK,
    ACTOR_MAURY,
    MAX_LINE_BYTES,
    RESULT_SUCCESS,
    AuditEvent,
    AuditLogError,
    append_event,
    audit_log_path,
    log,
    read_events,
)


def _make_event(**overrides: object) -> AuditEvent:
    defaults: dict[str, object] = {
        "ts": "2026-05-19T12:00:00Z",
        "event": "sync_started",
        "host_id": "host_abc",
        "session_id": "session_def",
        "active_profile": "home",
        "actor": ACTOR_MAURY,
        "details": {"repos": ["base"]},
        "result": RESULT_SUCCESS,
    }
    defaults.update(overrides)
    return AuditEvent(**defaults)  # type: ignore[arg-type]


# ---- append_event --------------------------------------------------------


def test_append_event_writes_jsonl_line(tmp_path: Path) -> None:
    append_event(_make_event())
    log_path = audit_log_path()
    body = log_path.read_text()
    lines = [ln for ln in body.splitlines() if ln.strip()]
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["event"] == "sync_started"
    assert parsed["host_id"] == "host_abc"
    assert parsed["details"] == {"repos": ["base"]}


def test_append_event_appends_to_existing(tmp_path: Path) -> None:
    append_event(_make_event(event="sync_started"))
    append_event(_make_event(event="sync_completed"))
    body = (audit_log_path()).read_text()
    events = [json.loads(ln) for ln in body.splitlines() if ln.strip()]
    assert [e["event"] for e in events] == ["sync_started", "sync_completed"]


def test_append_event_creates_parent_directory(tmp_path: Path) -> None:
    tmp_path / "deeply" / "nested"
    append_event(_make_event())
    assert (audit_log_path()).is_file()


def test_append_event_rejects_invalid_actor(tmp_path: Path) -> None:
    with pytest.raises(AuditLogError, match="invalid actor"):
        append_event(_make_event(actor="bogus"))


def test_append_event_rejects_invalid_result(tmp_path: Path) -> None:
    with pytest.raises(AuditLogError, match="invalid result"):
        append_event(_make_event(result="unknown"))


def test_append_event_rejects_oversize_line(tmp_path: Path) -> None:
    """Lines exceeding MAX_LINE_BYTES break POSIX O_APPEND atomicity."""
    bloat = "x" * (MAX_LINE_BYTES * 2)
    with pytest.raises(AuditLogError, match="exceeds"):
        append_event(_make_event(details={"bloat": bloat}))


def test_audit_event_to_json_is_single_line(tmp_path: Path) -> None:
    """No embedded newlines in the serialised event (POSIX O_APPEND
    atomicity assumes one event = one line)."""
    event = _make_event(details={"multi": "line\nwith\nnewlines"})
    rendered = event.to_json()
    assert "\n" not in rendered
    # The newline characters in the multiline value are JSON-escaped, not literal.
    assert "\\n" in rendered


# ---- log() convenience ---------------------------------------------------


def test_log_writes_event(tmp_path: Path) -> None:
    log("render_applied", host_id="host_abc", files_written=["a", "b"])
    events = [json.loads(ln) for ln in (audit_log_path()).read_text().splitlines() if ln.strip()]
    assert len(events) == 1
    assert events[0]["event"] == "render_applied"
    assert events[0]["details"] == {"files_written": ["a", "b"]}


def test_log_default_actor_is_maury(tmp_path: Path) -> None:
    log("anything")
    event = next(iter(json.loads(ln) for ln in (audit_log_path()).read_text().splitlines() if ln.strip()))
    assert event["actor"] == "maury"


def test_log_with_hook_actor(tmp_path: Path) -> None:
    log("tool_use_logged", actor=ACTOR_HOOK, tool="Edit")
    event = json.loads((audit_log_path()).read_text().splitlines()[0])
    assert event["actor"] == "hook"
    assert event["event"] == "tool_use_logged"


def test_log_ts_is_iso_utc(tmp_path: Path) -> None:
    """Timestamps round-trip through datetime.fromisoformat after stripping Z."""
    log("sync_started")
    event = json.loads((audit_log_path()).read_text().splitlines()[0])
    parsed = datetime.fromisoformat(event["ts"].replace("Z", "+00:00"))
    assert parsed.tzinfo == UTC


# ---- read_events ---------------------------------------------------------


def _seed_log(tmp_path: Path) -> None:
    """Three events spanning two kinds + two actors."""
    append_event(
        _make_event(ts="2026-05-19T10:00:00Z", event="sync_started", actor=ACTOR_MAURY),
    )
    append_event(
        _make_event(ts="2026-05-19T10:05:00Z", event="tool_use_logged", actor=ACTOR_HOOK),
    )
    append_event(
        _make_event(ts="2026-05-19T10:10:00Z", event="sync_completed", actor=ACTOR_MAURY),
    )


def test_read_events_yields_newest_first(tmp_path: Path) -> None:
    _seed_log(tmp_path)
    events = list(read_events())
    assert [e.event for e in events] == ["sync_completed", "tool_use_logged", "sync_started"]


def test_read_events_kind_filter(tmp_path: Path) -> None:
    _seed_log(tmp_path)
    events = list(read_events(kinds=["sync_started", "sync_completed"]))
    assert {e.event for e in events} == {"sync_started", "sync_completed"}
    assert "tool_use_logged" not in {e.event for e in events}


def test_read_events_since_filter(tmp_path: Path) -> None:
    _seed_log(tmp_path)
    events = list(read_events(since="2026-05-19T10:07:00Z"))
    assert [e.event for e in events] == ["sync_completed"]


def test_read_events_until_filter(tmp_path: Path) -> None:
    _seed_log(tmp_path)
    events = list(read_events(until="2026-05-19T10:07:00Z"))
    assert {e.event for e in events} == {"sync_started", "tool_use_logged"}


def test_read_events_actor_filter(tmp_path: Path) -> None:
    _seed_log(tmp_path)
    events = list(read_events(actor=ACTOR_HOOK))
    assert [e.event for e in events] == ["tool_use_logged"]


def test_read_events_limit(tmp_path: Path) -> None:
    _seed_log(tmp_path)
    events = list(read_events(limit=2))
    assert len(events) == 2


def test_read_events_missing_log_returns_empty(tmp_path: Path) -> None:
    assert list(read_events()) == []


def test_read_events_skips_malformed_lines(tmp_path: Path) -> None:
    """Partial-write residue or future-schema lines are skipped, not raised."""
    log_path = audit_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        _make_event(event="sync_started").to_json() + "\n"
        "{ partial wri\n"
        "\n"  # blank line
         + _make_event(event="sync_completed").to_json() + "\n"
    )
    events = list(read_events())
    assert {e.event for e in events} == {"sync_started", "sync_completed"}


# ---- concurrency / atomicity --------------------------------------------


def test_concurrent_appends_interleave_at_line_boundaries(tmp_path: Path) -> None:
    """O_APPEND guarantees no torn writes when total line is ≤ pipe-write atomic.

    We can't fully reproduce concurrent processes in a unit test, but we
    can verify that two sequential appends produce two well-formed lines
    (a regression guard against accidentally truncating or overwriting
    on append).
    """
    append_event(_make_event(event="sync_started", host_id="host_a"))
    append_event(_make_event(event="sync_started", host_id="host_b"))
    body = (audit_log_path()).read_text()
    lines = [ln for ln in body.splitlines() if ln.strip()]
    assert len(lines) == 2
    parsed = [json.loads(ln) for ln in lines]
    assert {p["host_id"] for p in parsed} == {"host_a", "host_b"}
