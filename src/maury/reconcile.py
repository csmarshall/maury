"""`maury reconcile` — interactive drift resolution.

Per [ADR-0017](../../docs/adr/0017-drift-detection-and-reconciliation.md)
"The five reconcile actions for hand-edits". This module owns the
hand-edit menu; the Claude-write drift menu (3 actions: soft-accept /
revert / promote) lands in a sibling slice once `claude-writes.jsonl`
attribution is wired.

v0 scope (this slice):

- ``adopt`` — capture the diff hunk to the maury-staging captures
  file (per ADR-0013 amendment), local file unchanged. The user's
  hand-edit propagates upward via the proposal pipeline; until it
  merges, drift on the same path will recur and the user will see
  the same prompt again. Honest about that.
- ``mark-managed`` — append the path to the host's
  ``hand-managed.json`` (schema specified below). Future render
  passes skip these paths so the user owns them entirely on this
  host.
- ``revert`` — overwrite the on-disk file with the rendered
  content (drops the user's hand-edit, restoring the baseline).
- ``skip-once`` — no action; drift will resurface on the next
  ``maury sync``.

Deferred:

- ``adapt`` — accepts the menu choice but warns it's not yet
  implemented and falls back to ``adopt``. Full implementation
  needs the rule-engine normalization pass per ADR-0011.
- Claude-write drift menu (3 actions) — needs ``claude-writes.jsonl``
  attribution from ADR-0023's hook to be wired.

The session is decoupled from the prompt mechanism: the public API
takes a ``prompter`` callable, so tests can pass a deterministic
answer-script while the CLI passes a Click-based interactive prompt.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from maury.drift import DriftEntry, DriftKind, DriftReport
from maury.render import RenderResult

# ---- public action enum -------------------------------------------------


class ReconcileAction(StrEnum):
    """The five hand-edit reconcile actions per ADR-0017."""

    ADOPT = "adopt"
    ADAPT = "adapt"
    MARK_MANAGED = "mark-managed"
    REVERT = "revert"
    SKIP_ONCE = "skip-once"


# All actions in the order shown to the user.
ALL_ACTIONS: tuple[ReconcileAction, ...] = (
    ReconcileAction.ADOPT,
    ReconcileAction.ADAPT,
    ReconcileAction.MARK_MANAGED,
    ReconcileAction.REVERT,
    ReconcileAction.SKIP_ONCE,
)


# ---- result types -------------------------------------------------------


@dataclass(frozen=True)
class ReconcileOutcome:
    """One file's outcome after the user picked an action."""

    path: str
    action: ReconcileAction
    detail: str = ""  # free-form (e.g., "captured to staging file")


@dataclass
class ReconcileSummary:
    """Aggregate of one reconcile session."""

    outcomes: list[ReconcileOutcome] = field(default_factory=list)
    captures_written: int = 0
    paths_marked_managed: int = 0
    paths_reverted: int = 0
    paths_skipped: int = 0
    paths_falling_back_to_adopt: int = 0
    errors: list[str] = field(default_factory=list)

    def has_errors(self) -> bool:
        return bool(self.errors)


# ---- staging file (per ADR-0013) ----------------------------------------

# Defined here for v0 — formal schema lives in ADR-0013 amendment, but
# this module is the first writer so the constants live here until a
# real `maury.staging` module emerges.
STAGING_DIR = "maury-staging"
CAPTURES_FILENAME = "captures.jsonl"


def staging_dir(target_dir: Path) -> Path:
    return target_dir / STAGING_DIR


def captures_path(target_dir: Path) -> Path:
    return staging_dir(target_dir) / CAPTURES_FILENAME


# ---- hand-managed registry ----------------------------------------------

# Schema (v0):
# {
#   "schema_version": 1,
#   "paths": ["CLAUDE.md", "skills/foo/SKILL.md"]
# }
#
# Lives at <base-repo>/profiles/<profile>/hosts/<host>/hand-managed.json
# per ADR-0017's "mark hand-managed" action ("synced to git so peer hosts
# know"). The renderer's integration (skipping these paths) is a separate
# render-engine slice.

HAND_MANAGED_FILENAME = "hand-managed.json"


def hand_managed_path(
    *,
    base_repo: Path,
    profile_name: str,
    host_name: str,
) -> Path:
    return base_repo / "profiles" / profile_name / "hosts" / host_name / HAND_MANAGED_FILENAME


def _read_hand_managed(path: Path) -> set[str]:
    """Read the hand-managed.json file's path set; return empty if absent."""
    if not path.is_file():
        return set()
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version", 1) != 1:
        # Strict on schema for now; later versions handled via ADR-0030 pattern.
        raise ReconcileError(
            f"unsupported hand-managed.json schema_version="
            f"{data.get('schema_version')!r} at {path}; this maury speaks v1."
        )
    return set(data.get("paths", []))


def _write_hand_managed(path: Path, paths: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "paths": sorted(paths),
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


# ---- error type ---------------------------------------------------------


class ReconcileError(ValueError):
    """Raised when reconcile inputs are invalid or hand-managed file is malformed."""


# ---- prompter protocol --------------------------------------------------

# A prompter takes a DriftEntry and returns the chosen ReconcileAction.
# CLI uses Click prompts; tests use a dict-driven script.
Prompter = Callable[[DriftEntry], ReconcileAction]


# ---- main entry point ---------------------------------------------------


def reconcile(
    *,
    drift_report: DriftReport,
    target_dir: Path,
    rendered: RenderResult,
    prompter: Prompter,
    base_repo: Path | None = None,
    profile_name: str | None = None,
    host_name: str | None = None,
    session_id: str = "",
) -> ReconcileSummary:
    """Walk drift entries and apply the user-chosen action to each.

    Args:
        drift_report: from ``maury.drift.detect_drift``.
        target_dir: e.g., ``~/.claude/``.
        rendered: in-memory render output. Used for the ``revert``
            action (overwrite on-disk file with rendered bytes) and
            for context when prompting.
        prompter: callable that returns a ``ReconcileAction`` per
            drift entry. The CLI passes a Click-based prompter;
            tests pass a deterministic one.
        base_repo: path to the synced base repo (where
            ``hand-managed.json`` lives). Required for ``mark-managed``.
        profile_name: active profile name. Required for ``mark-managed``.
        host_name: this host's name. Required for ``mark-managed``.
        session_id: optional Claude Code session id for
            captures.jsonl provenance.

    Returns:
        ReconcileSummary with per-entry outcomes and counts.
    """
    summary = ReconcileSummary()
    # Index rendered files by target_path for fast revert lookup.
    rendered_by_path = {f.target_path: f for f in rendered.files}

    # Only hand-edit drift kinds map to the 5-action menu; EXPECTED is
    # not drift; UNTRACKED and MISSING use the same menu but with
    # different prompt verbs (handled by the prompter — same enum,
    # since the underlying action vocabulary is shared).
    actionable = [e for e in drift_report.entries if e.kind != DriftKind.EXPECTED]
    if not actionable:
        return summary

    for entry in actionable:
        action = prompter(entry)
        try:
            outcome = _apply_action(
                action=action,
                entry=entry,
                target_dir=target_dir,
                rendered_by_path=rendered_by_path,
                base_repo=base_repo,
                profile_name=profile_name,
                host_name=host_name,
                session_id=session_id,
            )
        except ReconcileError as e:
            summary.errors.append(f"{entry.path}: {e}")
            continue

        summary.outcomes.append(outcome)
        if outcome.action == ReconcileAction.ADOPT:
            summary.captures_written += 1
        elif outcome.action == ReconcileAction.ADAPT:
            # v0 falls back to adopt; counted as both an adapt (intent)
            # and an adopt (action). Use the fallback counter for the
            # "this was a fallback, not a real adapt" surface.
            summary.captures_written += 1
            summary.paths_falling_back_to_adopt += 1
        elif outcome.action == ReconcileAction.MARK_MANAGED:
            summary.paths_marked_managed += 1
        elif outcome.action == ReconcileAction.REVERT:
            summary.paths_reverted += 1
        elif outcome.action == ReconcileAction.SKIP_ONCE:
            summary.paths_skipped += 1

    return summary


# ---- per-action handlers ------------------------------------------------


def _apply_action(
    *,
    action: ReconcileAction,
    entry: DriftEntry,
    target_dir: Path,
    rendered_by_path: dict,  # type: ignore[type-arg]  # pragma: no cover - covered via callers
    base_repo: Path | None,
    profile_name: str | None,
    host_name: str | None,
    session_id: str,
) -> ReconcileOutcome:
    if action == ReconcileAction.SKIP_ONCE:
        return ReconcileOutcome(path=entry.path, action=action, detail="left as-is")

    if action == ReconcileAction.REVERT:
        return _do_revert(entry=entry, target_dir=target_dir, rendered_by_path=rendered_by_path)

    if action == ReconcileAction.MARK_MANAGED:
        if base_repo is None or profile_name is None or host_name is None:
            raise ReconcileError(
                "mark-managed requires base_repo, profile_name, and host_name; "
                "callers must supply these for any session that may use this action"
            )
        return _do_mark_managed(
            entry=entry,
            base_repo=base_repo,
            profile_name=profile_name,
            host_name=host_name,
        )

    if action in (ReconcileAction.ADOPT, ReconcileAction.ADAPT):
        # v0: adapt falls back to adopt with a clear note. The
        # normalization pass (per ADR-0011) lands in slice 4.
        is_adapt_fallback = action == ReconcileAction.ADAPT
        return _do_adopt(
            entry=entry,
            target_dir=target_dir,
            session_id=session_id,
            is_adapt_fallback=is_adapt_fallback,
        )

    raise ReconcileError(f"unknown action {action!r}")


def _do_revert(
    *,
    entry: DriftEntry,
    target_dir: Path,
    rendered_by_path: dict,  # type: ignore[type-arg]
) -> ReconcileOutcome:
    """Restore the file to the rendered baseline content."""
    if entry.kind == DriftKind.UNTRACKED:
        # Untracked = file isn't in the render output. "Revert" means
        # "remove the file" since the baseline doesn't have it.
        full = target_dir / entry.path
        if full.is_file():
            full.unlink()
        return ReconcileOutcome(
            path=entry.path,
            action=ReconcileAction.REVERT,
            detail="untracked file removed",
        )

    rendered = rendered_by_path.get(entry.path)
    if rendered is None:
        # Should not happen: MODIFIED/MISSING means the path WAS in
        # last-render, which means a fresh render should produce it.
        # If render output diverges from last-render, that's a bug
        # we should surface rather than silently drop.
        raise ReconcileError(
            f"cannot revert {entry.path!r}: not present in current render output. "
            f"This indicates a render-vs-baseline mismatch; investigate before forcing."
        )

    full = target_dir / entry.path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_bytes(rendered.content)
    full.chmod(rendered.mode)
    return ReconcileOutcome(
        path=entry.path,
        action=ReconcileAction.REVERT,
        detail=f"restored from render ({len(rendered.content)} bytes)",
    )


def _do_mark_managed(
    *,
    entry: DriftEntry,
    base_repo: Path,
    profile_name: str,
    host_name: str,
) -> ReconcileOutcome:
    """Add this path to the host's hand-managed.json registry."""
    hm_path = hand_managed_path(
        base_repo=base_repo,
        profile_name=profile_name,
        host_name=host_name,
    )
    paths = _read_hand_managed(hm_path)
    paths.add(entry.path)
    _write_hand_managed(hm_path, paths)
    return ReconcileOutcome(
        path=entry.path,
        action=ReconcileAction.MARK_MANAGED,
        detail=f"appended to {hm_path.relative_to(base_repo) if hm_path.is_relative_to(base_repo) else hm_path}",
    )


def _do_adopt(
    *,
    entry: DriftEntry,
    target_dir: Path,
    session_id: str,
    is_adapt_fallback: bool,
) -> ReconcileOutcome:
    """Append a capture record to the staging file. Local file unchanged."""
    cp = captures_path(target_dir)
    cp.parent.mkdir(parents=True, exist_ok=True)

    # Compute a small diff-hunk preview. v0: just embed the actual
    # on-disk content (truncated). Real "diff hunk + 3-5 lines context"
    # per ADR-0017 §Q12 is a follow-up that depends on storing the
    # rendered baseline content somewhere reachable.
    full = target_dir / entry.path
    body_excerpt = ""
    if full.is_file():
        try:
            text = full.read_text(encoding="utf-8")
            body_excerpt = text[:2000] if len(text) <= 2000 else text[:2000] + "\n…(truncated)"
        except UnicodeDecodeError:
            body_excerpt = "(binary content; not embedded)"

    record = {
        "ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "drift-adopt-fallback" if is_adapt_fallback else "drift-adopt",
        "kind": "preference",  # default; user re-classifies during review
        "scope_hint": "current",
        "text": f"Hand-edit captured from {entry.path}",
        "rationale": (
            f"User adopted hand-edit during `maury reconcile`. Drift kind: {entry.kind.value}. Body excerpt below."
        ),
        "session_id": session_id,
        "drift_path": entry.path,
        "drift_kind": entry.kind.value,
        "body_excerpt": body_excerpt,
    }

    with cp.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    detail = f"captured to {cp.name}"
    if is_adapt_fallback:
        detail += " (adapt fallback: normalization pass not yet implemented)"
    return ReconcileOutcome(
        path=entry.path,
        action=ReconcileAction.ADAPT if is_adapt_fallback else ReconcileAction.ADOPT,
        detail=detail,
    )


__all__ = [
    "ALL_ACTIONS",
    "CAPTURES_FILENAME",
    "HAND_MANAGED_FILENAME",
    "STAGING_DIR",
    "Prompter",
    "ReconcileAction",
    "ReconcileError",
    "ReconcileOutcome",
    "ReconcileSummary",
    "captures_path",
    "hand_managed_path",
    "reconcile",
    "staging_dir",
]
