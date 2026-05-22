# ADR-0026: Mode-aware mining

**Status:** Accepted
**Date:** 2026-05-07

## Related tenets

- [Tenet 1 — First, do no harm](../tenets.md#1-first-do-no-harm)
- [Tenet 2 — Consistency within a mode](../tenets.md#2-consistency-within-a-mode-controlled-difference-across-modes)
- [Tenet 3 — Trust boundaries are physical](../tenets.md#3-trust-boundaries-are-physical-not-policy)
- [Tenet 4 — Sensitive data stays local](../tenets.md#4-sensitive-data-stays-local)

## TL;DR

Mining without mode-awareness produces work-context proposals from
personal-context transcripts (and vice versa) — exactly the cross-
boundary leakage Tenet 1 forbids. This ADR adds three pieces: a
durable **`session-history.jsonl`** companion to ADR-0025's transient
active-sessions log, one entry per completed session keyed by the
mode active when it *started*; mining filters that respect that
attribution (default: mine the active mode only); and bucketized
review so findings carry mode provenance into the curator's view.
Trade-off: extra hook + extra state file, and review UI must handle
N mode buckets instead of one flat list.

## Context

[ADR-0005](0005-local-only-mining.md) established that each host
mines its own `~/.claude/projects/<project>/<session-id>.jsonl`
transcripts locally. ADR-0005 said nothing about *which mode
was active when each transcript was created* — it implicitly
treated all transcripts on a host as equally minable.

That assumption breaks under [ADR-0025](0025-profile-switching-session-safeguards.md)'s
mode-switch model. Per
[`cc-contract:past-transcripts-not-auto-loaded`](../claude-code-contract.md#cc-contractpast-transcripts-not-auto-loaded),
past transcripts persist on disk regardless of which mode is
currently active. So a host that has been in `personal` for six
months and then switches to `work` retains six months of
personal-context transcripts on disk. If `maury mine` runs in
the new `work` context and walks every transcript, it produces
work-context proposals from personal-context conversations —
exactly the cross-boundary leakage tenet 1 forbids.

Also: when the user reviews findings from a mining run, they
need to see *which mode each finding came from* so they can
make informed decisions. A flat list of findings without mode
attribution is worse than no findings.

The fix has three pieces: durable session-to-mode linkage,
mining filters that respect mode, and bucketized review.

## Decision

### Durable session-to-mode linkage: `session-history.jsonl`

[ADR-0025](0025-profile-switching-session-safeguards.md)
introduced `~/.claude/maury-state/active-sessions.jsonl` as a
**transient** event log for the active-session safety check.
This ADR adds a **durable** companion:
`~/.claude/maury-state/session-history.jsonl` — one line per
completed session, written by a `SessionEnd` hook that reduces
the live event stream into an archived record.

Schema (one JSON object per line, append-only):

```json
{
  "session_id": "abc...",
  "host_id": "host_e3844a43...",
  "started_at": "2026-05-07T10:23:00Z",
  "ended_at": "2026-05-07T10:48:32Z",
  "mode_active_at_start": "personal",
  "profile_id_active_at_start": "mode_a3f9...",
  "transcript_path": "~/.claude/projects/abc.../def.jsonl",
  "tool_use_count": 12,
  "resumed_from": null
}
```

**`mode_active_at_start` is the load-bearing field.** The
mode a session ran *under* is determined by what was active
when it started — even if the user switches profiles afterward.
Per ADR-0025's safeguards, mode switch refuses on active
sessions by default, so a session's start-time mode ==
its end-time mode (a session cannot span a switch).

**Edge case: `--force --i-understand-cross-boundary-risk`.**
[ADR-0025](0025-profile-switching-session-safeguards.md)
provides an explicit escape hatch that allows mode switch
during an active session. A session whose mode was switched
mid-life via this escape hatch will record a stale
`mode_active_at_start`. The forensic backstop is the audit
log entry from the `--force` switch (per ADR-0025 §"On a clean
switch") — when reconstructing what content came from where, an
auditor reconciles `session-history.jsonl` against the
mode-switch audit log to spot mid-session crosses. Mining
treats the recorded `mode_active_at_start` as authoritative;
the user owned the choice when they typed the verbose flag.

To populate this field, the `SessionStart` hook (already added
in ADR-0025) reads `active-context.json` at fire time and
includes `mode_active_at_start` in the `session_start` event
written to `active-sessions.jsonl`. The `SessionEnd` hook
reduces the events for that `session_id` and writes the durable
record to `session-history.jsonl`.

### Mining filter: default to current mode

`maury mine` reads `session-history.jsonl` and, by default,
processes only transcripts whose `mode_active_at_start`
matches the host's currently-active mode (per
`active-context.json`).

```
maury mine                              # default: current mode only
maury mine --mode work               # explicit: only `work` transcripts
maury mine --include-other-modes     # opt-in: all profiles, bucketized
maury mine --mode-list               # opt-in: scan all, surface counts
```

The default-current-only behavior is **tenet 1 enforcement**:
mining cross-mode content silently produces proposals that
crossed boundaries the user didn't authorize. Making it opt-in
forces an explicit decision.

**Migration: transcripts pre-dating `session-history.jsonl`.**
Sessions that ran before this ADR's mechanism shipped have no
record in `session-history.jsonl` (no `SessionEnd` hook was
installed yet, so no archival). On a fresh `maury mine` run,
these unattributed transcripts are **skipped with a warning**
by default — they're treated as "mode unknown, treat as
out-of-scope." The user can opt in to mine them via:

- `maury mine --include-other-modes` — surfaces them in a
  dedicated "unattributed" bucket alongside mode buckets.
- `maury mine --backfill-unattributed <mode>` — assigns the
  given mode retroactively to unattributed transcripts and
  writes synthetic `session-history.jsonl` entries (tagged with
  `backfilled: true` for audit). Useful when the user knows
  "all my transcripts before today were under `personal`."

This composes naturally with the bulk-mining mode from
[ADR-0020](0020-two-mining-modes-bulk-and-incremental.md) —
the bulk mode is exactly when backfill is most useful (initial
onboarding of an existing host).

### Output: bucketized findings by source mode

Whether single-mode or multi-mode, mining presents
findings grouped by source mode so the curator sees:

```
host: <hostname>  (active mode: work)

Mining 47 transcripts across 3 historical profiles:

  work:        12 transcripts → 6 findings
  acme-client:  3 transcripts → 2 findings (extends work)

  personal:    32 transcripts → 14 findings (NOT mined; opt in
                                              with --include-other-modes)

To proceed:
  maury review work-2026-05-07-r1  (auto-created run branch)
```

Each finding's resulting commit (per
[ADR-0022](0022-branch-per-mining-run.md)) gets a new trailer
line, consistent with the `Promoted-From:` extension precedent
ADR-0022 already set (RFC 822 trailers are an open set; new keys
are additive):

```
Source-Mode: mode_a3f9...
Source-Mode-Name: personal
```

The promotion review ([ADR-0045](0045-cross-trust-boundary-promotion.md)
— planned) uses these to enforce the inheritance-graph constraint
on which target modes are valid for the proposal.

### Cross-host case (multi-host mining)

When two hosts mine overlapping content (e.g., conversations
that both hosts saw via Dropbox-synced `~/.claude/projects/`),
[ADR-0022's Content-Hash dedup](0022-branch-per-mining-run.md)
already handles the redundancy at proposal-creation time —
findings with the same `Content-Hash` from a different host's
prior mining run are suppressed. Mode-aware mining adds one
refinement: the dedup scan should also consider
`Source-Mode`, because the same finding from `personal` and
from `work` are conceptually different (one is a personal
preference, one is a work preference, even if the text is
similar). Practically: dedup scan checks `(Content-Hash,
Source-Mode)` tuples rather than `Content-Hash` alone.

### Anomaly detection (preserved from ADR-0005)

[ADR-0005](0005-local-only-mining.md) established that
cross-mode content emerging on the wrong host (e.g., a
`linux-server` reference in a work-laptop session) gets
quarantined locally rather than written to git. Mode-aware
mining preserves this and refines it: the rule engine
([ADR-0004](0004-rule-engine-classification.md)) runs against
each finding using the active mode's `forbid` rules at
classification time. A flagged finding goes to local quarantine
with a clear "anomaly: <mode> content from <mode> host"
tag.

## Consequences

- **Default-safe mining at session granularity.** Running
  `maury mine` after a mode switch doesn't silently produce
  cross-mode proposals — every transcript is attributed to
  the mode that was active when its session started. (Within
  a single session, the user could still write personal-context
  content during a work-mode session — see "Followups: mid-
  session mode classification" for the v1.1 plan to address
  finding-granular drift.)
- **Explicit cross-mode workflow exists.**
  `--include-other-modes` is the deliberate path for users
  who want to surface findings across profiles (e.g., post-hoc
  realizing "I had a great workflow tip in personal that should
  apply to work too" — surface it, route it through cross-context
  promotion per [ADR-0045](0045-cross-trust-boundary-promotion.md)).
- **Two new state files:** `session-history.jsonl` (durable) and
  the existing `active-sessions.jsonl` (transient). Naming is
  parallel to make the distinction obvious.
- **One new hook event consumer:** ADR-0025 installs `SessionEnd`
  for active-session tracking; this ADR adds a second consumer
  to that same installation. One installation in `settings.json`,
  two responsibilities — the consumer pattern composes via the
  marker-managed hook scheme from
  [ADR-0023](0023-hook-installation-and-tool-resolution.md).
- **Minor schema additions to `Source-Mode` and
  `Source-Mode-Name` trailers** on mining-run commits per
  ADR-0022. Backward-compatible: existing dedup grep doesn't care
  about these fields.
- **Cross-host dedup gets one tuple dimension wider.** Tracking
  `(Content-Hash, Source-Mode)` instead of just
  `Content-Hash` is a bounded change.
- **Reviewing across profiles is more cognitively expensive** —
  the curator sees buckets instead of a flat list. Worth it
  because flat-list cross-mode review is exactly how leakage
  happens.

## Alternatives considered

- **Mine all transcripts; let the rule engine sort it out.**
  Rejected: depends on the rule engine being perfect, which it
  isn't. Default-safe filtering at the mining layer is
  defense-in-depth (forbid rules + mode filter + review).
- **Tag transcripts at write time** (Claude Code annotates each
  transcript with the active mode). Per
  [`cc-contract:no-native-profile-tracking`](../claude-code-contract.md#cc-contractno-native-profile-tracking),
  Claude Code records nothing about maury profiles, so this
  isn't an option without external metadata — exactly what
  `session-history.jsonl` provides.
- **Separate `~/.claude/projects/` directories per mode.**
  Rejected: would conflict with Claude Code's directory
  derivation (per
  [`cc-contract:project-directory-derivation`](../claude-code-contract.md#cc-contractproject-directory-derivation),
  Claude Code derives the directory from the working-directory
  path; we don't control that).
- **Don't track session-to-mode linkage at all; require the
  user to specify mode on `maury mine` invocation.**
  Rejected: user error becomes silent leakage. Tracking by
  default is safer.
- **Use `git log` on the manifest's `active_mode` field
  history as the linkage record.** Considered. Rejected:
  manifest changes are coarse-grained (mode-switch events);
  individual sessions don't appear in git history. Per-session
  granularity requires `session-history.jsonl`.

## Build-order placement

Phase 6 — Mining + extraction. The ADR's mechanics (filter,
bucket, dedup-by-tuple) compose with the existing Phase 6
extractor + crossref work; mode-aware filtering is an
additive layer.

The `SessionStart` / `SessionEnd` hooks ride on Phase 5.x.c
(mode-switching mechanics from ADR-0025) — cannot ship
mode-aware mining before the hooks that produce
`active-sessions.jsonl` exist.

## Followups

- **Mid-session mode classification.** A user might be in
  mode `work` but write a personal preference into the
  conversation by accident. The rule engine's forbid rules
  catch some of these, but not all. A v1.1 enhancement: at
  finding-extraction time, the LLM classifier could re-evaluate
  whether the finding's content matches the session's active
  mode, and quarantine if mismatched.
- **Multi-host transcript reconciliation** (true cross-host
  Dropbox-synced case). v1: each host mines its own copy,
  Content-Hash dedup catches duplicates at the proposal layer.
  v1.1: a smarter pre-mining dedup that recognizes "this
  transcript was already mined by another host" via a shared
  manifest of mined transcripts.
- **`session-history.jsonl` size growth.** Append-only forever;
  long-running hosts will accumulate years of session records.
  v1: no pruning. v1.1: `maury sessions prune --older-than 1y`
  command (companion to the ghost-session pruner from ADR-0025).
- **Commit-trailer schema versioning.** This ADR introduces
  `Source-Mode` and `Source-Mode-Name` trailers on
  mining-run commits. If trailer semantics ever evolve (e.g.,
  a `Source-Mode` that means something different), the
  same per-version-rename rule from
  [ADR-0030](0030-manifest-schema-migrations.md) §"Schema-
  changes constraints" applies — rename the trailer rather
  than redefine its semantics. There's no manifest-style
  `version:` field on commit messages; per-trailer renaming
  is the equivalent.

## Claude Code references

Verified-as-of 2026-05-07 against Anthropic's official Claude
Code documentation:

- [`cc-contract:past-transcripts-not-auto-loaded`](../claude-code-contract.md#cc-contractpast-transcripts-not-auto-loaded)
  — past transcripts persist on disk regardless of active
  mode.
- [`cc-contract:no-native-profile-tracking`](../claude-code-contract.md#cc-contractno-native-profile-tracking)
  — Claude Code itself records no mode metadata; maury fills
  the gap via `session-history.jsonl`.
- [`cc-contract:event-firing-cadence`](../claude-code-contract.md#cc-contractevent-firing-cadence)
  — `SessionStart` and `SessionEnd` fire once per session each
  (the cadence required for the linkage record).
- [`cc-hooks`][cc-hooks] — hook event semantics (already cited
  by ADR-0025).

[cc-hooks]: https://code.claude.com/docs/en/hooks

## Amendment history

- 2026-05-11 — "profile" renamed to "mode" throughout per ADR-0037 doctoral examination. `Source-Profile` → `Source-Mode` trailers; `mode_active_at_start` schema field; CLI flags updated (`--mode`, `--include-other-modes`). No semantic changes to mining logic.
- 2026-05-22 — **`Source-Mode` trailer + mode-aware dedup shipped** (Phase 9 groundwork for promotion). `maury mine --write-run-branch` now tags each finding commit with a `Source-Mode` trailer (the host's effective mode = active focus, else registered mode). Dedup widened from a scalar Content-Hash set to mode-aware identity per the [ADR-0022 §dedup amendment](0022-branch-per-mining-run.md): `run_branch.FindingKeys` keys on `(Content-Hash, Source-Mode)`, so the same idea mined under a different mode is a distinct proposal and is not suppressed; commits predating the trailer contribute *wildcard* hashes (match any mode) for backward-compat. Rejection commits gained a `Rejected-Source-Mode` line so rejection memory is mode-scoped too. `maury promote` reads the trailer per finding for the ADR-0045 graph check.
