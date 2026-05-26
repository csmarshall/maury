"""Focus — the lightweight intra-trust-boundary mode-switch engine (ADR-0052).

A **focus** is the active leaf of the host's trust-boundary subtree.
Structurally it's a child mode in the existing mode tree (per ADR-0037);
"focus" is the UX vocabulary for the lightweight switching verb's domain.
Switching focus stays within a trust boundary, so it doesn't require the
heavy `mode deregister` + `mode bootstrap` ceremony from ADR-0039.

This module is the pure-logic / state-management layer. The CLI verbs
(`maury focus use`, `maury focus current`, `maury focus list`) wrap it in
[src/maury/cli.py](cli.py). The audit-event wiring (`focus_switched` /
`focus_switch_refused`) lives in the CLI per the ADR-0035 pattern.

Engine surface:
  - `is_reachable(target, registered, manifest)` — trust-boundary check.
  - `active_focus_get()` — read the active-focus pointer.
  - `active_focus_set(focus)` — write/clear it (atomic).
  - `evaluate_focus_use(...)` — run the full precondition cascade,
    returning a `FocusUseResult` the caller maps to CLI output + audit
    events.
  - `list_reachable_modes(registered, manifest)` — enumerate the
    foci the operator can switch into (descendants of the registered
    mode, including the registered mode itself as the "no focus set"
    fallback).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from maury.active_sessions import (
    active_sessions_path,
    build_state_map,
    is_active,
    read_events,
)
from maury.host_identity import HostIdentityBaseline, baseline_path, read_baseline, write_baseline
from maury.manifest import Manifest, ManifestError


class FocusError(RuntimeError):
    """Raised when a focus operation cannot proceed (no baseline, write IO failure)."""


class FocusUseOutcome(StrEnum):
    """Result classes for `evaluate_focus_use`.

    The CLI maps these to (exit-code, message, audit-event-kind) at the
    boundary. The engine stays pure — no I/O beyond the baseline + the
    active-sessions log.
    """

    OK = "ok"
    """All preconditions pass. Caller writes the baseline + emits
    `focus_switched`."""

    NOT_REGISTERED = "not_registered"
    """No `host-identity.json` on disk; caller should point the user at
    `maury init` and emit `focus_switch_refused`."""

    UNKNOWN_FOCUS = "unknown_focus"
    """The target path doesn't resolve to any mode in the manifest."""

    NOT_REACHABLE = "not_reachable"
    """The target mode exists but isn't in the host's trust-boundary
    subtree. Caller surfaces the loud "use `mode deregister` +
    `mode bootstrap`" error and emits `focus_switch_refused`."""

    ACTIVE_SESSIONS = "active_sessions"
    """One or more active Claude Code sessions running. Caller refuses
    unless the user passed `--force-active-session`."""


@dataclass(frozen=True)
class FocusUseResult:
    """Outcome of evaluating a focus-use request.

    Carries enough context for the CLI to format both the user-facing
    message and the audit-event payload without re-resolving anything.
    """

    outcome: FocusUseOutcome
    from_focus: str | None
    """The active focus before the switch (or None if previously unset)."""

    to_focus: str
    """The dotted-path the user requested."""

    target_mode_id: str | None = None
    """Resolved mode_id of the target, when reachable. None on
    UNKNOWN_FOCUS."""

    active_session_ids: tuple[str, ...] = ()
    """Set when `outcome == ACTIVE_SESSIONS`; lists the running session
    IDs so the caller can show them in the refusal message."""

    detail: str = ""
    """Short human-readable note. Free-form; CLI formats around it."""


# ---- pure trust-boundary check -------------------------------------------


def is_reachable(
    *,
    target_mode_id: str,
    registered_mode_id: str,
    manifest: Manifest,
) -> bool:
    """True if `target_mode_id` is in the trust-boundary subtree rooted at
    `registered_mode_id`.

    Implementation: walk the target's `extends` chain. The registered mode
    must appear in that chain (single-parent + acyclic per ADR-0049
    means this terminates in O(tree depth); no graph traversal).
    """
    try:
        chain = manifest.inheritance_chain(target_mode_id)
    except ManifestError:
        return False
    return registered_mode_id in chain


# ---- active-focus pointer get / set --------------------------------------


def active_focus_get() -> str | None:
    """Return the active focus dotted-path, or None when no focus has been
    set (the registered mode is implicitly the active mode)."""
    baseline = read_baseline()
    if baseline is None:
        return None
    return baseline.active_focus


def active_focus_set(focus: str | None) -> None:
    """Update the `active_focus` field on the baseline atomically.

    Passing `None` clears the field (the registered mode resumes as the
    active mode). Preserves every other field on the baseline.

    Raises FocusError if no baseline exists (caller should `maury init`
    first); raises whatever `write_baseline` raises on IO failure.
    """
    baseline = read_baseline()
    if baseline is None:
        raise FocusError(f"no host-identity baseline at {baseline_path()}; run `maury init` first.")
    new = HostIdentityBaseline(
        schema_version=baseline.schema_version,
        host_id_hex=baseline.host_id_hex,
        registered_at=baseline.registered_at,
        mode_id=baseline.mode_id,
        mode_name_at_bootstrap=baseline.mode_name_at_bootstrap,
        active_focus=focus,
    )
    write_baseline(new)


# ---- precondition cascade for `maury focus use` --------------------------


def evaluate_focus_use(
    *,
    target_focus: str,
    manifest: Manifest,
    force_active_session: bool = False,
) -> FocusUseResult:
    """Run the full precondition cascade for `maury focus use <target_focus>`.

    Preconditions, in order:
      1. Host is registered (baseline exists). Otherwise NOT_REGISTERED.
      2. Target focus path resolves to a known mode. Otherwise UNKNOWN_FOCUS.
      3. Target is in the host's trust-boundary subtree. Otherwise
         NOT_REACHABLE.
      4. No active Claude Code sessions on this host (unless
         `force_active_session=True`). Otherwise ACTIVE_SESSIONS.

    On success, returns OK; the caller is responsible for calling
    `active_focus_set` to persist the change.
    """
    baseline = read_baseline()
    if baseline is None:
        return FocusUseResult(
            outcome=FocusUseOutcome.NOT_REGISTERED,
            from_focus=None,
            to_focus=target_focus,
            detail="no host-identity baseline; run `maury init` first",
        )

    target_mode_id = manifest.profile_id_by_name(target_focus)
    if target_mode_id is None:
        return FocusUseResult(
            outcome=FocusUseOutcome.UNKNOWN_FOCUS,
            from_focus=baseline.active_focus,
            to_focus=target_focus,
            detail=f"no mode named {target_focus!r} in the manifest",
        )

    if not is_reachable(
        target_mode_id=target_mode_id,
        registered_mode_id=baseline.mode_id,
        manifest=manifest,
    ):
        return FocusUseResult(
            outcome=FocusUseOutcome.NOT_REACHABLE,
            from_focus=baseline.active_focus,
            to_focus=target_focus,
            target_mode_id=target_mode_id,
            detail=(
                f"target {target_focus!r} is not in this host's trust-boundary "
                f"subtree (registered mode: {baseline.mode_name_at_bootstrap!r}). "
                f"Crossing a trust boundary requires `maury mode deregister` + "
                f"`maury mode bootstrap` (ADR-0039)."
            ),
        )

    if not force_active_session:
        active_ids = _live_session_ids()
        if active_ids:
            return FocusUseResult(
                outcome=FocusUseOutcome.ACTIVE_SESSIONS,
                from_focus=baseline.active_focus,
                to_focus=target_focus,
                target_mode_id=target_mode_id,
                active_session_ids=tuple(active_ids),
                detail=(
                    f"{len(active_ids)} active Claude Code session(s) running; "
                    f"focus switch would leave them with stale context. "
                    f"Pass --force-active-session to override."
                ),
            )

    return FocusUseResult(
        outcome=FocusUseOutcome.OK,
        from_focus=baseline.active_focus,
        to_focus=target_focus,
        target_mode_id=target_mode_id,
    )


# ---- list reachable modes ------------------------------------------------


def list_reachable_modes(
    *,
    registered_mode_id: str,
    manifest: Manifest,
) -> list[tuple[str, str]]:
    """Return (mode_id, mode_name) for every mode in the host's
    trust-boundary subtree (i.e., every descendant of `registered_mode_id`,
    plus the registered mode itself).

    Order: root-first by chain depth, then by name. Stable so CLI output
    is deterministic.
    """
    with_depth: list[tuple[int, str, str, str]] = []  # (depth, name, mode_id, name) for sort
    for mode_id, spec in manifest.profiles.items():
        try:
            chain = manifest.inheritance_chain(mode_id)
        except ManifestError:
            continue
        if registered_mode_id in chain:
            registered_at = chain.index(registered_mode_id)
            depth_to_target = len(chain) - 1 - registered_at
            with_depth.append((depth_to_target, spec.name, mode_id, spec.name))

    with_depth.sort()  # by depth ascending, then name
    return [(mode_id, name) for _depth, _sort_name, mode_id, name in with_depth]


# ---- internals -----------------------------------------------------------


def _live_session_ids() -> list[str]:
    """Read active-sessions.jsonl and return IDs of sessions still running.

    Returns an empty list if the log doesn't exist (no sessions ever, or
    fresh install). Per ADR-0025's carried-forward design: a session
    counts as "live" if it has a session_start event but no matching
    session_end.
    """
    log_path = active_sessions_path()
    if not log_path.is_file():
        return []
    events, _skipped = read_events(log_path)
    states = build_state_map(events)
    return [sid for sid, state in states.items() if is_active(state)]


__all__ = [
    "FocusError",
    "FocusUseOutcome",
    "FocusUseResult",
    "active_focus_get",
    "active_focus_set",
    "evaluate_focus_use",
    "is_reachable",
    "list_reachable_modes",
]
