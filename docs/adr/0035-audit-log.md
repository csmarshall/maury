# ADR-0035: Audit log (Phase 10)

**Status:** Accepted
**Date:** 2026-05-07

## Related tenets

- [Tenet 1 — First, do no harm](../tenets.md#1-first-do-no-harm)
- [Tenet 4 — Sensitive data stays local](../tenets.md#4-sensitive-data-stays-local)
- [Tenet 7 — Provenance is mandatory](../tenets.md#7-provenance-is-mandatory)

## TL;DR

Six prior ADRs reference a "Phase 10 audit log" without specifying
it — every one of them is making promises against a contract that
didn't exist. This ADR is that contract: **one file**,
`~/.claude/maury-state/audit.jsonl`, append-only, host-local
(never synced to git), JSONL with ≤4 KB records to maintain POSIX
`O_APPEND` atomicity per the concurrent-sessions CC contract. Every
state-changing maury operation writes one event with a fixed
schema (`ts`, `host_id`, `kind`, payload). Subsumes the stub
`mode-switches.jsonl` introduced in ADR-0025 (with a documented
one-shot migration). Trade-off: a new logging concern in every
state-changing code path, but a single cross-cutting record makes
"what happened on this host?" a `jq` query.

## Context

Six ADRs reference a "Phase 10 audit log" without specifying it:

- [ADR-0017](0017-drift-detection-and-reconciliation.md) — reconcile
  actions (adopt / adapt / mark-managed / revert / skip-once)
  should be auditable.
- [ADR-0023](0023-hook-installation-and-tool-resolution.md) —
  `claude-writes.jsonl` is a per-event log; the audit log is its
  cross-cutting consumer for "what changed, when, by whom."
- [ADR-0024](0024-manifest-concurrency-inclusive-merge.md) —
  manifest merges (auto-resolved or interactive) need an audit
  trail.
- [ADR-0025](0025-profile-switching-session-safeguards.md) —
  profile-switch events are explicitly written to a stub
  `mode-switches.jsonl` "until Phase 10 audit log lands."
- [ADR-0029](0029-maury-state-layout-contract.md) — reserves an
  `audit.jsonl` slot in the maury-state inventory with a TODO
  ("Phase 10 ADR — not yet written") and notes it subsumes
  `mode-switches.jsonl`.
- [ADR-0032](0032-backup-and-disaster-recovery.md) — includes
  `audit.jsonl` (alongside the stub profile-switches log) in
  the default backup set.

The first ADR landscape audit (2026-05-07) flagged this as the
single largest coverage gap: *"every ADR that references 'Phase
10' is making promises against a contract that doesn't exist."*
This ADR specifies that contract.

## Decision

### One file, append-only, host-local

`~/.claude/maury-state/audit.jsonl` per
[ADR-0029](0029-maury-state-layout-contract.md)'s File
inventory. JSONL: one event record per line, ≤4 KB to maintain
POSIX `O_APPEND` atomicity per
[`cc-contract:concurrent-sessions`](../claude-code-contract.md#cc-contractconcurrent-sessions).
Append-only — never edited, never pruned (by maury) except via
explicit `maury audit prune --older-than <duration>` (v1.1).

Host-local per the
[ADR-0029](0029-maury-state-layout-contract.md) cross-cutting
invariants: never synced to git, sensitive content possible.

### Event schema (locked)

Every event record:

```json
{
  "ts": "2026-05-07T14:32:00Z",
  "event": "<event-kind>",
  "host_id": "host_e3844a43...",
  "session_id": "abc...",
  "active_profile": "personal",
  "actor": "maury|user|claude",
  "details": { ... event-kind-specific ... },
  "result": "success|failure",
  "schema_version": 1
}
```

- `ts` — UTC ISO-8601.
- `event` — one of the kinds enumerated below.
- `host_id` — from `~/.maury-host-id`.
- `session_id` — Claude Code session that triggered this, if
  applicable; null otherwise.
- `active_profile` — profile active when the event fired (per
  `active-context.json`).
- `actor` — who initiated this event. Values:
  - `maury` — a maury CLI command invoked by the user or
    triggered internally (e.g., a sync sub-step).
  - `user` — direct user action outside maury's CLI (rare;
    e.g., manual hand-edit captured by drift detection).
  - `claude` — Claude Code initiated via a tool call
    (typically `tool_use_logged`).
  - `hook` — a maury-installed hook script fired
    out-of-band of any maury invocation (e.g., `PostToolUse
    log_tool_use` from ADR-0023, `SessionStart` from
    ADR-0025). Hooks are maury-managed but run as standalone
    shell processes; the `hook` value distinguishes their
    audit entries from `maury` (which means a maury CLI
    command).
- `details` — event-kind-specific payload. Bounded so the line
  stays ≤4 KB.
- `result` — terminal outcome. Useful for filtering failed
  events.
- `schema_version` — pinned at 1 for v1; bumps via the same
  per-version-reader pattern as
  [ADR-0030](0030-manifest-schema-migrations.md).

### Event kinds (the full enumeration)

This ADR is the single authoritative list. New event kinds
require an `**Amended:**` entry on this ADR.

| Event kind | Source ADR | When it fires | `details` shape |
|---|---|---|---|
| `sync_started` | [ADR-0005](0005-local-only-mining.md), [phase-5 sync](../status.md) | At the start of `maury sync` | `{repos: [...]}` |
| `sync_completed` | sync | When sync finishes successfully | `{repos_pulled: [...], render_actions: [...]}` |
| `sync_aborted` | sync | When sync refuses (drift, network, etc.) | `{reason: "...", at_step: "..."}` |
| `render_applied` | [ADR-0019](0019-inheritance-semantics-refine-by-default.md) | When `apply_render` writes any file | `{files_written: [...], files_unchanged: int}` |
| `drift_detected` | [ADR-0017](0017-drift-detection-and-reconciliation.md) | When drift is found during sync | `{drift_count: int, kinds: {...}}` |
| `reconcile_action` | [ADR-0017](0017-drift-detection-and-reconciliation.md) | For each user choice in the reconcile menu | `{path: "...", action: "adopt|adapt|mark-managed|revert|skip-once"}` |
| `claude_revert` | [ADR-0017](0017-drift-detection-and-reconciliation.md) | When user runs `maury revert <id>` | `{reverted_change_id: "...", path: "..."}` |
| `manifest_mutated` | [ADR-0024](0024-manifest-concurrency-inclusive-merge.md) | When maury writes a new manifest version | `{changes: [...], git_commit: "..."}` |
| `manifest_merge_resolved` | [ADR-0024](0024-manifest-concurrency-inclusive-merge.md) | When `maury manifest resolve` completes | `{conflicts_resolved: [...], commit: "..."}` |
| `mode_switched` | [ADR-0025](0025-profile-switching-session-safeguards.md) | When `maury mode use` succeeds (subsumes the stub `mode-switches.jsonl`) | `{from_mode: "...", to_mode: "...", sessions_state: {...}, forced: bool}` — preserves all fields from ADR-0025's stub schema (`force_flag_used` renamed to `forced` for the new schema; `sessions_state` carried verbatim; `ts`/`host_id` are top-level event fields) |
| `mode_switch_refused` | [ADR-0025](0025-profile-switching-session-safeguards.md) | When `maury mode use` refuses (any precondition fails) | `{reason: "...", precondition: "..."}` |
| `init_completed` | [ADR-0018](0018-minimum-bootstrap-ux.md) | When `maury init` finishes (first-host-setup or update) | `{from: "dir|tarball", source: "...", host_registered: bool}` |
| `manifest_upgraded` | [ADR-0030](0030-manifest-schema-migrations.md) | When a `maury manifest upgrade-vN-to-vN+1` completes | `{from_version: int, to_version: int, backup_path: "...", changes: [...]}` |
| `sessions_pruned` | [ADR-0025](0025-profile-switching-session-safeguards.md) | When `maury sessions prune` removes ghost-session entries | `{pruned_count: int, threshold_age: "..."}` |
| `uninstall_completed` | [ADR-0023](0023-hook-installation-and-tool-resolution.md) | When `maury uninstall` finishes — written **before** maury-state itself is deleted, so the final forensic trail "who removed maury" is captured. The audit.jsonl is preserved alongside the user content per ADR-0023 §8's "leaves user content alone" guarantee. | `{user_hooks_kept: int, scripts_removed: int, repos_left_alone: [...]}` |
| `migration_completed` | This ADR | One-shot marker when the `mode-switches.jsonl` → `audit.jsonl` migration completes (see §"Migration: subsuming `mode-switches.jsonl`" below) | `{migrated_event_count: int, source_file: "...", archive_renamed_to: "..."}` |
| `mining_run_created` | [ADR-0022](0022-branch-per-mining-run.md), [ADR-0026](0026-profile-aware-mining.md) | When `maury mine` produces a run branch | `{run_id: "...", branch: "...", commit_count: int}` |
| `review_completed` | [ADR-0022](0022-branch-per-mining-run.md) | When `maury review` finishes | `{run_id: "...", accepted: int, rejected: int, merged_to: "..."}` |
| `promotion_started` | [ADR-0009](0009-promotion-only-cross-boundary.md) | When `maury promote` begins | `{from_repo: "...", to_repo: "..."}` |
| `promotion_completed` | [ADR-0009](0009-promotion-only-cross-boundary.md), [ADR-0027](0027-cross-context-promotion-via-shared-root.md) | When promotion succeeds | `{commits_promoted: int, target_pr: "...|null"}` |
| `pr_opened` | [ADR-0033](0033-pr-repo-mode.md) | When `pr`-mode review pushes a PR | `{repo: "...", pr_url: "...", commit_count: int}` |
| `tool_use_logged` | [ADR-0023](0023-hook-installation-and-tool-resolution.md) | One per Claude Edit/Write/MultiEdit call (mirror of `claude-writes.jsonl`) | `{tool: "...", path: "...", before_sha: "...", after_sha: "..."}` |
| `subscription_added` | [ADR-0034](0034-published-subscribed-profiles.md) | When `maury subscribe` succeeds | `{repo_url: "...", as_profile: "..."}` |
| `subscription_pinned` | [ADR-0034](0034-published-subscribed-profiles.md) | When `--pin` or `subscription update` runs | `{repo: "...", ref: "..."}` |
| `backup_created` | [ADR-0032](0032-backup-and-disaster-recovery.md) | When `maury backup` finishes | `{tarball_path: "...", bytes: int, includes_transcripts: bool}` |
| `restore_completed` | [ADR-0032](0032-backup-and-disaster-recovery.md) | When `maury restore` finishes | `{tarball_path: "...", files_restored: int}` |
| `error` | (any) | When any maury operation surfaces a user-facing error | `{command: "...", error: "...", traceback: "..."}` |

The list grows additively; existing event kinds are stable.
Removing or renaming an event kind is a schema-version bump
(per [ADR-0030](0030-manifest-schema-migrations.md)'s
"always rename when semantics change" rule).

### Relationship to other state files

The audit log is **the cross-cutting record**, not the only
record. Other state files in `~/.claude/maury-state/` per
[ADR-0029](0029-maury-state-layout-contract.md) serve their
own per-domain purposes; the audit log captures the
state-changing *events*.

| State file | Per-domain purpose | Audit log overlap |
|---|---|---|
| `last-render.json` | Drift baseline (current SHAs) | Audit log records `render_applied`; last-render is the *result*, audit is the *event*. |
| `claude-writes.jsonl` | Drift attribution (which writes are Claude's) | Audit log mirrors as `tool_use_logged` events for cross-cutting queries; claude-writes remains the operational log per ADR-0017's drift-detection mechanics. |
| `active-context.json` | Current binding | Audit log records `mode_switched`; active-context is the *current state*, audit is the *transition event*. |
| `active-sessions.jsonl` | Live session lifecycle | Not mirrored — too high-frequency and short-lived. The session-end event is implicitly captured by `session-history.jsonl`. |
| `session-history.jsonl` | Durable per-session record | Not mirrored — that file IS the per-session audit, audit log records cross-session events. |
| `mode-switches.jsonl` | Stub for this ADR | **Subsumed.** Once this ADR ships, `mode_switched` events go into `audit.jsonl`; the stub is retired (kept as historical archive but no new writes). |

### Migration: subsuming `mode-switches.jsonl`

When this ADR's implementation lands (Phase 10):

1. The first `maury sync` after upgrade migrates existing
   `mode-switches.jsonl` entries into `audit.jsonl` as
   `mode_switched` events with `schema_version: 1`.
2. The migration writes a single `migration_completed` event
   marking the cutover.
3. `mode-switches.jsonl` is renamed to
   `mode-switches.jsonl.migrated-<ts>` and left in place
   as historical archive.
4. From this point forward, only `audit.jsonl` is written.

The migration is one-shot; if a host already has
`audit.jsonl` (because it was a fresh install post-Phase 10),
it skips the migration.

### Querying the audit log

`maury audit` command surfaces the log:

```sh
maury audit                              # tail -20 of recent events, human-readable
maury audit --since <duration>           # events in the last <duration>
maury audit --kind <event-kind>          # filter by event kind
maury audit --json                       # machine-readable
maury audit --for-path <path>            # all events touching this path (across kinds)
maury audit --grep <pattern>             # JSONL grep
```

These are read-only over the JSONL file; no special index.
Sub-second on years of audit data because audit events are
small and the file grows slowly (low-frequency events
dominate; only `tool_use_logged` is high-frequency, and
that's already cap-rate-limited by Claude Code's own
session pacing).

### Backup integration

[ADR-0032](0032-backup-and-disaster-recovery.md) §"What
`maury backup` includes by default" already lists `audit.jsonl`
(via the inclusion of the maury-state directory). No
change to ADR-0032 needed; the audit log rides backup as
part of maury-state.

### What the audit log does NOT do

- **Push remotely.** Audit is host-local per
  [ADR-0029](0029-maury-state-layout-contract.md). Aggregating
  audit data across hosts (e.g., for an org-wide audit
  dashboard) is out of scope; that's a future "audit shipping"
  ADR if ever needed.
- **Block on writes.** Audit-log appends are best-effort: if
  the disk is full or the file is locked, the underlying maury
  operation continues and prints a warning. The audit log
  shouldn't be a single point of failure for operations.
- **Tamper-proof.** A user with write access to `~/.claude/`
  can edit or delete the audit log. Maury doesn't sign or
  hash-chain the entries. If an org needs tamper-evidence,
  that's an external concern (e.g., ship the file to an
  append-only log service).

## Consequences

- **Six ADRs' "Phase 10 audit log" promises are now backed
  by a concrete contract.** Implementers have a single source
  of truth for event schema, kinds, and lifecycle.
- **`mode-switches.jsonl` retires cleanly.** Migration
  preserves history; the per-domain stub from ADR-0025
  collapses into the cross-cutting log.
- **One new state file** added to ADR-0029's inventory:
  `audit.jsonl`. ADR-0029 already reserved a slot; this ADR
  makes the slot real.
- **One new CLI command** (`maury audit`) for querying.
  Trivial wrapper over JSONL grep + format; ~100 LOC.
- **Backup story doesn't change.** ADR-0032 already includes
  the maury-state dir.
- **Event-kind list is the contract surface.** Future ADRs
  that add new kinds amend this ADR's enumeration. Doc-review
  agents should check that any new event-emitting code
  corresponds to an event kind in this list.
- **Schema versioning follows ADR-0030's pattern.** v1 ships
  with this ADR; future bumps are per-version readers.

## Alternatives considered

- **Per-event-kind separate files** (`mode-switches.jsonl`,
  `reconcile-actions.jsonl`, `promotions.jsonl`, etc.).
  Rejected: ADR-0025's stub already showed this pattern
  doesn't compose — the cross-cutting "what happened on this
  host this week" question requires merging across files.
  One file is the right abstraction.
- **A SQLite database for queryability.** Considered.
  Rejected: JSONL is the right storage for append-only
  bounded-cardinality event logs; SQLite adds a binary
  format dependency, query complexity, and breaks the
  consistency with the rest of maury-state's `.jsonl`
  conventions per
  [ADR-0029](0029-maury-state-layout-contract.md). For
  queryability, `maury audit --kind <X>` + `--grep` covers
  the v1 use cases; a SQLite indexer could land later as a
  v1.1 enhancement (`maury audit --rebuild-index`) if
  performance demands it.
- **Hash-chained tamper-evidence** (each event includes the
  SHA of the previous event's record). Rejected for v1: the
  threat model is "user reads their own audit log"; no
  adversarial scenario justifies the complexity. v1.1
  enhancement if there's user demand for org-grade audit.
- **Push audit events to a remote store.** Rejected: violates
  ADR-0029's host-local invariant. Aggregation is a future
  concern; v1 is single-host.
- **Auto-prune old events.** Rejected: silent data loss;
  contradicts the never-prune-without-explicit-command
  pattern from
  [ADR-0029](0029-maury-state-layout-contract.md). The
  follow-up `maury audit prune --older-than` exists for v1.1
  but requires explicit user invocation.

## Build-order placement

Phase 10 — its own phase per `docs/status.md`. The audit log
**core** (file management, append helpers, query CLI, schema
validator, migration script) is a small slice (~150 LOC +
tests). The **wiring** — emitting events from every
state-changing operation across sync/render/reconcile/
profile/manifest/mining/review/promote/PR/subscription/backup/
restore/uninstall — is plausibly larger (~300-500 LOC
distributed across consumers, in line with ADR-0024's
similar-shaped estimate). Total Phase 10: ~500-700 LOC + tests. That wiring is sprinkled across
existing modules (sync, render, reconcile, profile-switch,
mining, review, promote, backup, restore) and lands as part
of each module's normal completion.

The migration from `mode-switches.jsonl` is a one-shot
function in Phase 10's slice; idempotent (skip if
`audit.jsonl` already exists).

## Followups

- **`maury audit prune --older-than <duration>`.** Bounded
  retention for users who want it. v1.1.
- **`maury audit --rebuild-index` (SQLite).** If `--grep`
  becomes too slow on multi-year logs, build an opt-in
  index. v1.1+.
- **Org-grade tamper-evidence.** Hash-chained events,
  optional signing. Out of scope for v1; future ADR if
  user demand emerges.
- **Cross-host audit aggregation.** Some teams may want to
  pull all hosts' audit logs into a central store for
  compliance review. Out of scope for v1; would need a new
  shipping mechanism (the current host-local invariant
  forbids it without explicit user intervention).

## Claude Code references

This ADR introduces no new Claude Code dependencies. The
`tool_use_logged` event mirrors data already collected by
the `claude-writes.jsonl` hook per
[ADR-0023](0023-hook-installation-and-tool-resolution.md);
the audit log doesn't add new Claude Code surface area.

References used:

- [`cc-contract:concurrent-sessions`](../claude-code-contract.md#cc-contractconcurrent-sessions)
  — for the JSONL `O_APPEND` ≤4 KB atomic-append story.

## Amendment history

- 2026-05-11 — "profile" vocabulary renamed to "mode" per ADR-0037 doctoral examination. References to "profile" in this ADR now read "mode"; no semantic changes.
- 2026-05-13 — event names renamed for vocabulary consistency: `profile_switched` → `mode_switched`, `profile_switch_refused` → `mode_switch_refused`, `from_profile`/`to_profile` payload fields → `from_mode`/`to_mode`. References to `maury profile use` updated to `maury mode use`. Migration archive filename corrected to `mode-switches.jsonl.migrated-<ts>`. No structural changes.
