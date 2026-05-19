"""Audit-log primitive per ADR-0035.

Append-only host-local event log at `~/.claude/maury-state/audit.jsonl`.
One event per line, ≤4 KB to preserve POSIX `O_APPEND` atomicity per
[`cc-contract:concurrent-sessions`](../docs/claude-code-contract.md).

This module ships the storage primitive (`append_event`, `read_events`,
`log`) without wiring concrete event kinds into every command — those
land per-command as the rest of Phase 10 ships. The schema enumeration
in ADR-0035 is the authoritative list; this module enforces shape but
not membership.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

AUDIT_REL_PATH: Final[Path] = Path("maury-state") / "audit.jsonl"
"""Audit-log location relative to the target dir (typically `~/.claude`)."""

SCHEMA_VERSION: Final[int] = 1
"""Per ADR-0035: locked at 1 for v1; bumps via per-version-reader pattern."""

MAX_LINE_BYTES: Final[int] = 4 * 1024
"""POSIX O_APPEND atomicity ceiling; lines longer than this are refused."""


# Valid `actor` values per ADR-0035.
ACTOR_MAURY: Final[str] = "maury"
ACTOR_USER: Final[str] = "user"
ACTOR_CLAUDE: Final[str] = "claude"
ACTOR_HOOK: Final[str] = "hook"
VALID_ACTORS: Final[frozenset[str]] = frozenset({ACTOR_MAURY, ACTOR_USER, ACTOR_CLAUDE, ACTOR_HOOK})

# Valid `result` values per ADR-0035.
RESULT_SUCCESS: Final[str] = "success"
RESULT_FAILURE: Final[str] = "failure"
VALID_RESULTS: Final[frozenset[str]] = frozenset({RESULT_SUCCESS, RESULT_FAILURE})


class AuditLogError(RuntimeError):
    """Raised when the audit log can't be written (oversize line, IO error)."""


@dataclass(frozen=True)
class AuditEvent:
    """One audit event per ADR-0035 §"Event schema (locked)"."""

    ts: str
    """UTC ISO-8601 timestamp."""

    event: str
    """Event kind. See ADR-0035 §"Event kinds (the full enumeration)"."""

    host_id: str | None
    """`host_<hex>` from `~/.maury-host-id`, or None if unresolved."""

    session_id: str | None
    """Claude Code session that triggered this, or None if not applicable."""

    active_profile: str | None
    """Profile name active when the event fired, or None if not applicable."""

    actor: str
    """Who initiated this. One of: maury / user / claude / hook."""

    details: dict[str, Any] = field(default_factory=dict)
    """Event-kind-specific payload. Bounded so the line stays ≤4 KB."""

    result: str = RESULT_SUCCESS
    """Terminal outcome — `success` or `failure`."""

    schema_version: int = SCHEMA_VERSION
    """Pinned at 1 for v1."""

    def to_json(self) -> str:
        """Render as a single-line JSON object."""
        return json.dumps(
            {
                "ts": self.ts,
                "event": self.event,
                "host_id": self.host_id,
                "session_id": self.session_id,
                "active_profile": self.active_profile,
                "actor": self.actor,
                "details": self.details,
                "result": self.result,
                "schema_version": self.schema_version,
            },
            separators=(",", ":"),
        )


def _utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def append_event(target_dir: Path, event: AuditEvent) -> None:
    """Append `event` to `<target_dir>/maury-state/audit.jsonl` atomically.

    Uses POSIX O_APPEND so concurrent writers from different processes
    interleave at line boundaries (per cc-contract:concurrent-sessions).
    Refuses to write lines longer than MAX_LINE_BYTES.
    """
    if event.actor not in VALID_ACTORS:
        raise AuditLogError(f"invalid actor {event.actor!r}; expected one of {sorted(VALID_ACTORS)}")
    if event.result not in VALID_RESULTS:
        raise AuditLogError(f"invalid result {event.result!r}; expected one of {sorted(VALID_RESULTS)}")
    line = event.to_json() + "\n"
    encoded = line.encode("utf-8")
    if len(encoded) > MAX_LINE_BYTES:
        raise AuditLogError(
            f"event line is {len(encoded)} bytes, exceeds {MAX_LINE_BYTES}-byte "
            f"O_APPEND atomicity ceiling. Trim the details payload."
        )

    log_path = target_dir / AUDIT_REL_PATH
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, encoded)
    finally:
        os.close(fd)


def log(
    target_dir: Path,
    event_kind: str,
    *,
    host_id: str | None = None,
    session_id: str | None = None,
    active_profile: str | None = None,
    actor: str = ACTOR_MAURY,
    result: str = RESULT_SUCCESS,
    **details: Any,
) -> None:
    """Convenience wrapper for `append_event`.

    Usage from a command site:
        from maury.audit_log import log
        log(target_dir, "sync_started", host_id=hid, repos=[...])

    Excess kwargs land in the event's `details` payload. The caller
    is responsible for keeping the resulting line ≤4 KB; oversize
    lines raise AuditLogError.
    """
    event = AuditEvent(
        ts=_utc_now_iso(),
        event=event_kind,
        host_id=host_id,
        session_id=session_id,
        active_profile=active_profile,
        actor=actor,
        details=dict(details),
        result=result,
    )
    append_event(target_dir, event)


def read_events(
    target_dir: Path,
    *,
    kinds: Iterable[str] | None = None,
    since: str | None = None,
    until: str | None = None,
    actor: str | None = None,
    limit: int | None = None,
) -> Iterator[AuditEvent]:
    """Read the audit log, yielding `AuditEvent`s newest-first.

    Args:
        target_dir: typically `~/.claude`.
        kinds: include only these event kinds (None = all).
        since: ISO-8601 UTC timestamp; exclude events with `ts < since`.
        until: ISO-8601 UTC timestamp; exclude events with `ts >= until`.
        actor: include only events from this actor (None = all).
        limit: cap the number of yielded events.

    Unparseable lines are skipped (forward-compat with future schema
    bumps and tolerant of partial-write residue).
    """
    log_path = target_dir / AUDIT_REL_PATH
    if not log_path.is_file():
        return
    kind_filter = set(kinds) if kinds is not None else None
    yielded = 0
    # Newest-first: read all lines, reverse.
    lines = log_path.read_text(encoding="utf-8").splitlines()
    for raw in reversed(lines):
        if limit is not None and yielded >= limit:
            return
        if not raw.strip():
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        try:
            event = _from_json_obj(obj)
        except (KeyError, TypeError, ValueError):
            continue
        if kind_filter is not None and event.event not in kind_filter:
            continue
        if since is not None and event.ts < since:
            continue
        if until is not None and event.ts >= until:
            continue
        if actor is not None and event.actor != actor:
            continue
        yield event
        yielded += 1


def _from_json_obj(obj: dict[str, Any]) -> AuditEvent:
    """Build an AuditEvent from a parsed dict. Strict on required fields."""
    return AuditEvent(
        ts=obj["ts"],
        event=obj["event"],
        host_id=obj.get("host_id"),
        session_id=obj.get("session_id"),
        active_profile=obj.get("active_profile"),
        actor=obj.get("actor", ACTOR_MAURY),
        details=obj.get("details") or {},
        result=obj.get("result", RESULT_SUCCESS),
        schema_version=obj.get("schema_version", SCHEMA_VERSION),
    )


__all__ = [
    "ACTOR_CLAUDE",
    "ACTOR_HOOK",
    "ACTOR_MAURY",
    "ACTOR_USER",
    "AUDIT_REL_PATH",
    "MAX_LINE_BYTES",
    "RESULT_FAILURE",
    "RESULT_SUCCESS",
    "SCHEMA_VERSION",
    "VALID_ACTORS",
    "VALID_RESULTS",
    "AuditEvent",
    "AuditLogError",
    "append_event",
    "log",
    "read_events",
]
