"""Active-session tracking and pruning for `~/.claude/maury-state/active-sessions.jsonl`.

Per ADR-0025, maury keeps an append-only event log of session lifecycle
events so the mode-switch safeguard can refuse switches during an active
session. This module owns:

- Parsing the jsonl event log into typed `SessionEvent`s.
- Collapsing the event stream into a `SessionState` map keyed by session_id.
- Identifying clearly-dead entries (session_end seen, older than a cutoff)
  for `maury sessions prune` to remove.
- Rewriting the log to drop pruned sessions while preserving append-order.

Conservative-by-design: a session without a `session_end` is NEVER pruned,
because long idle periods between tool calls are legitimate. Treating "no
activity for N minutes" as "ghost" would risk mis-classifying a live
session as gone — exactly the leakage the safeguard exists to prevent.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Final


class EventKind(Enum):
    """Event types maury-installed hooks write to active-sessions.jsonl."""

    SESSION_START = "session_start"
    TOOL_USE = "tool_use"
    SESSION_END = "session_end"


@dataclass(frozen=True)
class SessionEvent:
    """One parsed line of active-sessions.jsonl."""

    kind: EventKind
    ts: datetime
    session_id: str
    project_path: str | None = None
    """Set on SESSION_START events; cwd at session launch."""

    resumed_from: str | None = None
    """Set on SESSION_START events; prior session_id if `claude --resume`."""


@dataclass(frozen=True)
class SessionState:
    """Collapsed state for one session_id."""

    session_id: str
    started_at: datetime
    last_tool_use_at: datetime | None = None
    ended_at: datetime | None = None
    resumed_from: str | None = None
    project_path: str | None = None

    def is_ended(self) -> bool:
        """True iff a session_end event has been recorded."""
        return self.ended_at is not None


@dataclass(frozen=True)
class PruneResult:
    """Outcome of a prune operation."""

    pruned_session_ids: tuple[str, ...] = field(default_factory=tuple)
    """Session IDs whose events were removed from the log."""

    kept_session_ids: tuple[str, ...] = field(default_factory=tuple)
    """Session IDs whose events were retained."""

    lines_in: int = 0
    lines_out: int = 0
    skipped_unparseable: int = 0


# Default location of the active-sessions log relative to target dir.
ACTIVE_SESSIONS_REL_PATH: Final[Path] = Path("maury-state") / "active-sessions.jsonl"

# Default prune cutoff: session_end older than this is considered dead.
DEFAULT_MAX_AGE: Final[timedelta] = timedelta(hours=24)


# Parsing -------------------------------------------------------------------


def _parse_ts(raw: str) -> datetime:
    """Parse an RFC 3339 timestamp; require timezone awareness."""
    # `fromisoformat` accepts "...+00:00" in 3.11+; also accepts trailing Z in 3.11.
    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError(f"timestamp missing timezone: {raw!r}")
    return dt


def parse_event(line: str) -> SessionEvent:
    """Parse one JSONL line into a SessionEvent. Raises on malformed input."""
    obj = json.loads(line)
    kind_raw = obj.get("event")
    if not isinstance(kind_raw, str):
        raise ValueError(f"missing or non-string 'event' field: {line!r}")
    try:
        kind = EventKind(kind_raw)
    except ValueError as exc:
        raise ValueError(f"unknown event kind {kind_raw!r}") from exc
    ts_raw = obj.get("ts")
    if not isinstance(ts_raw, str):
        raise ValueError(f"missing or non-string 'ts' field: {line!r}")
    ts = _parse_ts(ts_raw)
    sid = obj.get("session_id")
    if not isinstance(sid, str) or not sid:
        raise ValueError(f"missing or empty 'session_id' field: {line!r}")
    return SessionEvent(
        kind=kind,
        ts=ts,
        session_id=sid,
        project_path=obj.get("project_path"),
        resumed_from=obj.get("resumed_from"),
    )


def read_events(path: Path) -> tuple[list[SessionEvent], int]:
    """Read the jsonl log; returns (events, skipped_unparseable_count).

    Unparseable lines are skipped, not raised — the log is append-only and
    a partial write at the tail must not block pruning the rest.
    """
    if not path.is_file():
        return [], 0
    events: list[SessionEvent] = []
    skipped = 0
    for raw in path.read_text().splitlines():
        if not raw.strip():
            continue
        try:
            events.append(parse_event(raw))
        except (json.JSONDecodeError, ValueError):
            skipped += 1
    return events, skipped


# State construction --------------------------------------------------------


def build_state_map(events: list[SessionEvent]) -> dict[str, SessionState]:
    """Collapse the event stream into a session_id → SessionState map.

    Events are processed in input order. A session's `started_at` comes
    from its first SESSION_START; `last_tool_use_at` is the most recent
    TOOL_USE; `ended_at` is the SESSION_END timestamp (if any).
    """
    pending: dict[str, dict[str, datetime | str | None]] = {}
    for ev in events:
        slot = pending.setdefault(
            ev.session_id,
            {
                "started_at": None,
                "last_tool_use_at": None,
                "ended_at": None,
                "resumed_from": None,
                "project_path": None,
            },
        )
        if ev.kind is EventKind.SESSION_START:
            # First wins for started_at; explicit assign in case of duplicate.
            if slot["started_at"] is None:
                slot["started_at"] = ev.ts
            if slot["resumed_from"] is None:
                slot["resumed_from"] = ev.resumed_from
            if slot["project_path"] is None:
                slot["project_path"] = ev.project_path
        elif ev.kind is EventKind.TOOL_USE:
            prior = slot["last_tool_use_at"]
            if prior is None or (isinstance(prior, datetime) and ev.ts > prior):
                slot["last_tool_use_at"] = ev.ts
        elif ev.kind is EventKind.SESSION_END:
            slot["ended_at"] = ev.ts
    states: dict[str, SessionState] = {}
    for sid, slot in pending.items():
        started_at = slot["started_at"]
        if not isinstance(started_at, datetime):
            # Orphan: TOOL_USE or SESSION_END without prior SESSION_START.
            # Skip; we cannot construct a SessionState without started_at.
            continue
        states[sid] = SessionState(
            session_id=sid,
            started_at=started_at,
            last_tool_use_at=slot["last_tool_use_at"] if isinstance(slot["last_tool_use_at"], datetime) else None,
            ended_at=slot["ended_at"] if isinstance(slot["ended_at"], datetime) else None,
            resumed_from=slot["resumed_from"] if isinstance(slot["resumed_from"], str) else None,
            project_path=slot["project_path"] if isinstance(slot["project_path"], str) else None,
        )
    return states


def is_active(state: SessionState) -> bool:
    """True iff session_start seen but no session_end."""
    return not state.is_ended()


# Prune logic ---------------------------------------------------------------


def select_prunable(
    states: dict[str, SessionState],
    *,
    now: datetime,
    max_age: timedelta,
) -> list[SessionState]:
    """Return sessions whose session_end is older than (now - max_age).

    Sessions without a session_end are never selected (conservative).
    The returned list is sorted by ended_at ascending for deterministic
    output.
    """
    cutoff = now - max_age
    ended = [s for s in states.values() if s.is_ended() and s.ended_at is not None and s.ended_at < cutoff]
    ended.sort(key=lambda s: s.ended_at or now)
    return ended


def rewrite_without_sessions(
    log_path: Path,
    sessions_to_remove: set[str],
) -> int:
    """Atomically rewrite the log to omit events for the named sessions.

    Returns the number of lines kept in the rewritten file. Uses
    tmp-file-plus-rename for atomicity; original file is replaced only
    after the new content is fully written and fsynced.

    Unparseable lines are kept (they may be partial writes from concurrent
    sessions; pruning shouldn't drop bytes the parser couldn't read).
    """
    if not log_path.is_file():
        return 0
    kept_lines: list[str] = []
    for raw in log_path.read_text().splitlines():
        if not raw.strip():
            continue
        try:
            ev = parse_event(raw)
        except (json.JSONDecodeError, ValueError):
            # Keep unparseable lines verbatim; partial writes are not ours to drop.
            kept_lines.append(raw)
            continue
        if ev.session_id in sessions_to_remove:
            continue
        kept_lines.append(raw)

    # Atomic write via tempfile in the same directory, then rename.
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=".active-sessions.",
        suffix=".tmp",
        dir=str(log_path.parent),
    )
    try:
        with os.fdopen(fd, "w") as fh:
            for ln in kept_lines:
                fh.write(ln + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, log_path)
    except Exception:
        # On failure, clean up the temp file rather than leave a half-written one.
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise
    return len(kept_lines)


def prune(
    log_path: Path,
    *,
    now: datetime | None = None,
    max_age: timedelta = DEFAULT_MAX_AGE,
    dry_run: bool = False,
) -> PruneResult:
    """End-to-end prune.

    Reads the log, identifies dead sessions, removes them (unless dry-run),
    and returns a PruneResult. Active sessions and sessions ended within
    `max_age` are preserved.
    """
    when = now if now is not None else datetime.now(UTC)
    events, skipped = read_events(log_path)
    states = build_state_map(events)
    prunable = select_prunable(states, now=when, max_age=max_age)
    pruned_ids = tuple(s.session_id for s in prunable)
    kept_ids = tuple(sid for sid in states if sid not in set(pruned_ids))

    lines_in = sum(1 for raw in (log_path.read_text().splitlines() if log_path.is_file() else []) if raw.strip())

    if dry_run or not pruned_ids:
        # No-op path: report the existing line count as both in and out.
        return PruneResult(
            pruned_session_ids=pruned_ids,
            kept_session_ids=kept_ids,
            lines_in=lines_in,
            lines_out=lines_in,
            skipped_unparseable=skipped,
        )

    lines_out = rewrite_without_sessions(log_path, set(pruned_ids))
    return PruneResult(
        pruned_session_ids=pruned_ids,
        kept_session_ids=kept_ids,
        lines_in=lines_in,
        lines_out=lines_out,
        skipped_unparseable=skipped,
    )
