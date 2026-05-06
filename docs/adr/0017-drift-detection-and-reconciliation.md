# ADR-0017: Hand-edits as first-class input — drift detection and reconciliation

**Status:** Accepted
**Date:** 2026-05-06

## Related tenets

- [Tenet 1 — First, do no harm](../tenets.md#1-first-do-no-harm)
- [Tenet 8 — Hand-edits are first-class input](../tenets.md#8-hand-edits-are-first-class-input)

## Context

Earlier ADRs implicitly assumed config flows in one direction: from
synced repos → render engine → `~/.claude/`. Reality is messier:

- The user opens `~/.claude/CLAUDE.md` in vim and adds a line.
- Claude Code itself uses the Write tool to modify the file during
  a session.
- The user creates a new skill in `~/.claude/skills/` that maury
  never put there.
- The user pulls the cloned source repo locally and edits a file
  there before pushing.

Forbidding any of these (read-only files, file-system locks,
permissions tricks) creates more problems than it solves: friction
kills adoption, and there's always an escape hatch a user could find.
Accepting them silently is worse — `maury sync` would then clobber
the user's edits with the rendered output.

Per tenet #1 (first, do no harm) and tenet #8 (hand-edits are
first-class input), we need a deliberate model for **drift detection
and reconciliation**. This is a GitOps-style reconciliation loop
where the synced repo + manifest is the desired state, the host's
actual `~/.claude/` is the actual state, and the user is the arbiter
for differences.

## Decision

Maury maintains a per-host `last-render.json` (path + sha256 for every
file it has rendered) at `~/.claude/maury-state/last-render.json`.
Drift is the diff between actual file SHAs and the rendered SHAs.

### Drift sources and treatment

Every drift carries a **source attribution**:

| Source | Detection mechanism | Default treatment |
|---|---|---|
| Human hand-edit (rendered file) | File modified, NOT in claude-writes log | **Blocking review** — surface in `maury status` / `maury reconcile` with full menu |
| Human hand-edit (cloned source repo) | Same as above; reconcile flow is unified | **Blocking review** — same path |
| Claude Code Write/Edit/MultiEdit | File modified, IS in claude-writes log | **Soft-accepted** — change persists, user gets `maury revert <id>` affordance |
| User-added file in managed dir | Path not in `last-render.json` at all | **Prompt to claim** — user picks: add to repo, mark hand-managed, or leave untracked |

The Claude-write log lives at `~/.claude/maury-state/claude-writes.jsonl`,
populated by a `PostToolUse` hook (action `log_tool_use`) that maury
ships and the render engine installs. Each line:
`{ts, session_id, tool, path, diff_hint}`.

### The five reconcile actions for hand-edits

When `maury reconcile` surfaces a hand-edit, the user picks one:

| Action | What happens |
|---|---|
| **adopt** | Capture the edit as a proposal (diff hunk + ~3-5 lines context per Q12); classify via the rule engine; queue for `maury review`. Local file remains as edited. |
| **adapt** | Same as adopt, but maury first runs the edit through best-practice normalization (e.g., raw shell hook → action-form; rule against the Anthropic rubric) before classification. |
| **mark hand-managed** | Add the path to `<repo>/profiles/<profile>/hosts/<host>/hand-managed.json` (synced to git so peer hosts know). Maury never renders this path again on this host. |
| **revert** | Restore the file to the last-rendered state. The hand-edit is discarded; the user gets one chance to back out (audit log records the revert). |
| **skip-once** | Leave as-is for this sync; will resurface as drift next time. |

For Claude-write drift, the affordance is shorter:

| Action | What happens |
|---|---|
| (default) | The change persists; `maury status` lists it as `soft-accepted, last from session abc...`. |
| **revert** | `maury revert <change-id>`, `maury revert --session <sid>`, or `maury revert --last`. |
| **promote** | `maury promote <change-id>` — convert the soft-accepted change into a proposal (same path as `adopt`). Useful when Claude wrote something good and you want to propagate it. |

### Sync flow with drift

Three flows from question Q10:

| Flag | Behavior |
|---|---|
| `maury sync` (default) | **Flow B**: detect drift, prompt per-file, then continue sync. Interactive. |
| `maury sync --non-interactive` | **Flow C**: refuse on any drift, exit 1. Cron/CI safe. |
| `maury sync --force` | **Flow A**: pull and clobber, with a loud warning to stderr. Rare manual override. |

### Multi-source merge conflicts (per Q11)

When the same line is touched by hand-edit, mined fragment, AND
upstream peer-host change: **three-way merge with user as arbiter.**
No silent precedence rules. Reconcile UI presents all three sources
plus the common ancestor; the user picks one or composes a fourth.

### Inheritance interaction (per ADR-0019)

When the reconcile pipeline captures a hand-edit as a proposal, it
needs to choose which layer to land it in (base, profile, host
overlay). Default routing: the layer where the *modified line* came
from per the render engine's provenance comments. The user can
override during review.

When the captured proposal would create a "child replaces parent"
situation, the renderer flags it and offers `maury refactor
promote-common` (v2 — see ADR-0019) as a follow-up.

### What v1 ships vs. defers

v1 (in scope):
- `last-render.json` tracking
- Drift detection in `maury status` and `maury sync`
- Three sync flows + the `--force` / `--non-interactive` flags
- Reconcile menu (5 actions for hand-edits, 3 for Claude-writes)
- `hand-managed.json` per-host overlay
- `PostToolUse log_tool_use` hook (single hook, single log file —
  load-bearing for drift attribution)
- **`maury-status` skill** that Claude can invoke mid-session to
  surface drift / pending captures / pending proposals
  (per Q&A — the project owner's specific requirement to ship in v1)

v1.1 (deferred):
- The full `maury-stage` skill + base/CLAUDE.md fragment + staging
  file consumer (the "Claude proactively captures durable insights"
  pipeline from ADR-0013).
- Background drift-watcher daemon (auto-detect drift on a timer
  rather than only on `sync`/`status`).
- `maury refactor promote-common` (per ADR-0019).

## Consequences

- **No silent data loss.** Every hand-edit and every Claude-write is
  either preserved-with-reversion-path or explicitly handled by the
  user.
- **Sync is safe by default.** `maury sync` doesn't clobber; it asks.
  Cron/CI users opt into the non-interactive refusal mode.
- **`maury-status` skill is part of v1.** Claude in any session can
  invoke it to remind the user of pending drift — gentle proactive
  nag without the full active-capture pipeline.
- **Cross-host coordination of overrides** via synced
  `hand-managed.json`: if you mark a path hand-managed on workstation, linux-server
  knows that's a workstation-local choice and doesn't try to render it
  there.
- **One shared reconcile pipeline** for all drift sources —
  hand-edits, Claude-writes, mined fragments, active captures (when
  v1.1 lands) all flow through the same proposal queue + classifier
  + audit log.
- **Implementation complexity is non-trivial:** a ~500-line module
  for drift detection + reconcile UI, plus the `PostToolUse` hook
  ship, plus the `maury-status` skill. Justified by the safety story.

## Alternatives considered

- **Read-only files / file-system locks.** Rejected: friction kills
  adoption; users will find escape hatches anyway.
- **Pull-and-clobber by default with warnings.** Rejected: violates
  tenet #1 (first, do no harm). Users would lose data they didn't
  realize they could lose.
- **Treat Claude-writes as a privileged channel that bypasses
  reconcile entirely.** Rejected (per Q8): non-uniform; hard to
  audit; user wants the same drift framework for all writers with
  different default actions.
- **Background watcher daemon as v1.** Considered, deferred to v1.1.
  v1's "sync/status only" model is cheap, predictable, and easy to
  test.
- **Per-line provenance instead of per-file.** Considered for the
  layer-routing question. Per-file with diff-hunk granularity (Q12)
  is the right balance for v1; per-line provenance is a v2 option
  if review-routing becomes a pain point.

## Followups

- **Active capture (v1.1)** rides on top of this ADR — the staging
  file Claude writes to becomes another drift source consumed by
  reconcile.
- **Watch-mode drift detection (v1.1)** would add a background
  process or `SessionStart` hook that runs `maury status --quiet`
  and surfaces drift count proactively.
- **Refactor-from-replacement (v2)** per ADR-0019 — the renderer's
  warnings during reconcile become actionable via `maury refactor`.
