# ADR-0017: Hand-edits as first-class input — drift detection and reconciliation

**Status:** Accepted
**Date:** 2026-05-06
**Amended:**
- 2026-05-07 — added addendum acknowledging the
  `claude-writes.jsonl` schema extension (added `before_sha`,
  `after_sha`, `size_delta` alongside the original `diff_hint`)
  per [ADR-0023](0023-hook-installation-and-tool-resolution.md)
  §6, which became the authoritative reference for the schema.
- 2026-05-07 — promoted the `maury-status` skill from a
  one-line v1-scope mention to a §"The `maury-status` skill"
  full definition. Defines structure (SKILL.md location),
  contract (backing `maury status --json` CLI command + JSON
  schema), source-file dependencies (active-context.json,
  last-render.json, captures.jsonl, run-branch counts,
  active-sessions.jsonl), and the user-facing output spirit.
  Closes audit finding M2 (skill referenced in 4 docs without
  being defined).

## TL;DR

Maury keeps a per-host SHA log of what it rendered
(`~/.claude/maury-state/last-render.json`). Sync detects drift
between rendered SHAs and current files, distinguishes hand-edits
from Claude-tool writes via a `PostToolUse log_tool_use` hook, and
surfaces a 5-action reconcile menu (adopt / adapt / mark-managed /
revert / skip-once) instead of clobbering. `maury sync` is
interactive by default; `--non-interactive` refuses on drift
(cron-safe); `--force` clobbers (rare manual override).
Trade-off: ~500 LOC of drift+reconcile module plus a load-bearing
hook that must succeed on every supported OS — a non-trivial
implementation surface, justified by the safety story.

## Related tenets

- [Tenet 1 — First, do no harm](../tenets.md#1-first-do-no-harm)
- [Tenet 8 — Hand-edits are first-class input](../tenets.md#8-hand-edits-are-first-class-input)

## Context and Problem Statement

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

<details>
<summary><b>Decision drivers</b> (5 items — click to expand)</summary>

- **Tenet 1:** first, do no harm. `maury sync` must never
  clobber the user's hand-edits without an explicit
  reconciliation step.
- **Tenet 8:** hand-edits are first-class input. The drift
  framework must treat them as proposals to consider, not
  noise to suppress.
- **Multiple writers exist:** the user, Claude Code itself
  (via Write/Edit/MultiEdit tools), and the render engine
  all touch the same files. Attribution matters.
- **GitOps reconciliation is the right mental model.** The
  repo is desired state; `~/.claude/` is actual state; the
  user is the arbiter. Maury observes and proposes; never
  silently overwrites.
- **Cron/CI must be safe.** Non-interactive sync needs a
  refuse-on-drift mode that exits cleanly.

</details>

<details>
<summary><b>Considered options</b> (6 options — click to expand)</summary>

- **Option A:** Forbid hand-edits via read-only files / FS
  locks / permissions tricks.
- **Option B:** Pull-and-clobber by default with warnings.
- **Option C:** Treat Claude-writes as a privileged channel
  that bypasses reconcile entirely.
- **Option D:** Background watcher daemon as v1's primary
  drift-detection mechanism.
- **Option E:** Per-line provenance instead of per-file +
  diff-hunk.
- **Option F (chosen):** Per-host `last-render.json` SHA
  tracking + reconcile menu with five hand-edit actions and
  three Claude-write actions; sync detects drift and prompts
  by default, with `--non-interactive` (refuse) and
  `--force` (clobber) escape hatches.

</details>

## Decision Outcome

**Chosen option:** Option F — maury maintains a per-host
`last-render.json` (path + sha256 for every file it has
rendered) at `~/.claude/maury-state/last-render.json`. Drift
is the diff between actual file SHAs and the rendered SHAs.
This is the only option that preserves Tenet 1 and Tenet 8
together — every hand-edit is detected and surfaced; nothing
silently disappears; cron/CI users opt into refusal.

### Implementation details

#### Drift sources and treatment

Every drift carries a **source attribution**:

| Source | Detection mechanism | Default treatment |
|---|---|---|
| Human hand-edit (rendered file) | File modified, NOT in claude-writes log | **Blocking review** — surface in `maury status` / `maury reconcile` with full menu |
| Human hand-edit (cloned source repo) | Same as above; reconcile flow is unified | **Blocking review** — same path |
| Claude Code Write/Edit/MultiEdit | File modified, IS in claude-writes log | **Soft-accepted** — change persists, user gets `maury revert <id>` affordance |
| User-added file in managed dir | Path not in `last-render.json` at all | **Prompt to claim** — user picks: add to repo, mark hand-managed, or leave untracked |

The Claude-write log lives at `~/.claude/maury-state/claude-writes.jsonl`,
populated by a [`PostToolUse`][cc-hooks] hook (action `log_tool_use`)
that maury ships and the render engine installs. Each line:
`{ts, session_id, tool, path, diff_hint, before_sha, after_sha,
size_delta}` — see [ADR-0023](0023-hook-installation-and-tool-resolution.md)
§6 for the full schema and the stdin-JSON-payload mechanics by
which the hook script receives data from Claude Code.

#### The five reconcile actions for hand-edits

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

#### Sync flow with drift

Three flows from question Q10:

| Flag | Behavior |
|---|---|
| `maury sync` (default) | **Flow B**: detect drift, prompt per-file, then continue sync. Interactive. |
| `maury sync --non-interactive` | **Flow C**: refuse on any drift, exit 1. Cron/CI safe. |
| `maury sync --force` | **Flow A**: pull and clobber, with a loud warning to stderr. Rare manual override. |

> **Note on `--force` semantics across maury:** this ADR (and
> [ADR-0024](0024-manifest-concurrency-inclusive-merge.md)) treat
> `--force` as a single-flag override. [ADR-0025](0025-profile-switching-session-safeguards.md)
> §"Precondition 3" intentionally requires the obnoxious-by-design
> `--force --i-understand-cross-boundary-risk` flag pair for
> profile switch — its blast radius (cross-boundary context
> leakage) is qualitatively different from the local
> overwrite/refuse tradeoffs `--force` covers here. Reader who
> notices the inconsistency: it's deliberate.

#### Multi-source merge conflicts

When the same line is touched by hand-edit, mined fragment, AND
upstream peer-host change: **three-way merge with user as arbiter.**
No silent precedence rules. Reconcile UI presents all three sources
plus the common ancestor; the user picks one or composes a fourth.

#### Inheritance interaction (per ADR-0019)

When the reconcile pipeline captures a hand-edit as a proposal, it
needs to choose which layer to land it in (base, profile, host
overlay). Default routing: the layer where the *modified line* came
from per the render engine's provenance comments. The user can
override during review.

When the captured proposal would create a "child replaces parent"
situation, the renderer flags it and offers `maury refactor
promote-common` (v2 — see ADR-0019) as a follow-up.

#### What v1 ships vs. defers

v1 (in scope):
- `last-render.json` tracking
- Drift detection in `maury status` and `maury sync`
- Three sync flows + the `--force` / `--non-interactive` flags
- Reconcile menu (5 actions for hand-edits, 3 for Claude-writes)
- `hand-managed.json` per-host overlay
- `PostToolUse log_tool_use` hook (single hook, single log file —
  load-bearing for drift attribution)
- **`maury-status` skill** — see §"The `maury-status` skill"
  below for the definition.

#### The `maury-status` skill

Claude-invokable mid-session affordance that surfaces maury's
view of the current host's state. Distributed via the render
engine to `~/.claude/skills/maury-status/SKILL.md` (per
[Claude Code skills documentation][cc-skills]).

**Structure:**

```
~/.claude/skills/maury-status/
  └── SKILL.md
```

**SKILL.md content (in spirit):** instructs Claude that when the
user asks "what's pending in maury?" / "is anything out of sync?"
/ similar, it should run the `maury status` CLI command via the
Bash tool, parse the JSON output, and present a concise summary
to the user.

**Backing CLI: `maury status --json`**

Reads several state files and emits a single JSON document:

```json
{
  "host_id": "host_e3844a43...",
  "active_profile": "personal",
  "last_sync_at": "2026-05-07T14:22:00Z",
  "drift": {
    "modified_count": 0,
    "missing_count": 0,
    "untracked_count": 0
  },
  "pending_captures": 3,
  "pending_proposals": 1,
  "active_sessions": 2,
  "warnings": []
}
```

Sources:
- `host_id`, `active_profile` ← `~/.claude/maury-state/active-context.json` (ADR-0025).
- `last_sync_at`, `drift` counts ← `~/.claude/maury-state/last-render.json` (this ADR's Phase 5.x.a).
- `pending_captures` ← `~/.claude/maury-staging/captures.jsonl` line count (ADR-0013).
- `pending_proposals` ← count of un-reviewed run branches (ADR-0022).
- `active_sessions` ← reduce of `~/.claude/maury-state/active-sessions.jsonl` (ADR-0025).

**Claude's user-facing output (in spirit):**

> *"You're in the **personal** profile on `<host>`. Last sync 2 hours
> ago. 3 captures pending review (run `maury review`). No drift, no
> active sessions besides this one."*

**Why a skill rather than the user typing `maury status`
themselves:** Claude can opportunistically invoke it at relevant
moments (e.g., after the user mentions wanting to remember
something, Claude invokes `maury status` to confirm if there
are captures pending) without the user having to context-switch
to a terminal. The skill is also v1 scaffolding for the v1.1
auto-invocation `Stop` hook that surfaces "📌 N captures
pending" automatically.

**Implementation status:** skill SKILL.md content TBD as part of
Phase 5.x.a (drift detection). The `maury status` CLI command is
also a Phase 5.x.a deliverable.

v1.1 (deferred):
- The full `maury-stage` skill + base/CLAUDE.md fragment + staging
  file consumer (the "Claude proactively captures durable insights"
  pipeline from ADR-0013).
- Background drift-watcher daemon (auto-detect drift on a timer
  rather than only on `sync`/`status`).
- `maury refactor promote-common` (per ADR-0019).

### Consequences

- ✅ **Good:** No silent data loss. Every hand-edit and
  every Claude-write is either preserved-with-reversion-path
  or explicitly handled by the user.
- ✅ **Good:** Sync is safe by default. `maury sync` doesn't
  clobber; it asks. Cron/CI users opt into the
  non-interactive refusal mode.
- ✅ **Good:** `maury-status` skill is part of v1. Claude in
  any session can invoke it to remind the user of pending
  drift — gentle proactive nag without the full active-
  capture pipeline.
- ✅ **Good:** Cross-host coordination of overrides via
  synced `hand-managed.json`: if you mark a path hand-managed
  on one host, peer hosts know that's a host-local choice and
  don't try to render it there.
- ✅ **Good:** One shared reconcile pipeline for all drift
  sources — hand-edits, Claude-writes, mined fragments,
  active captures (when v1.1 lands) all flow through the
  same proposal queue + classifier + audit log.
- ❌ **Bad:** Implementation complexity is non-trivial — a
  ~500-line module for drift detection + reconcile UI, plus
  the `PostToolUse` hook ship, plus the `maury-status`
  skill. Justified by the safety story.

### Confirmation

- `~/.claude/maury-state/last-render.json` is written after
  every successful render; SHAs are sha256 per the schema in
  [ADR-0029](0029-maury-state-layout-contract.md).
- `src/maury/drift.py` (Phase 5.x.a slice 1, shipped) and
  `src/maury/reconcile.py` (slice 3, shipped) implement the
  detection + menu.
- `maury sync` (Phase 5, shipped) checks drift before render
  and respects `--non-interactive` / `--force` flags.

<details>
<summary><b>Pros and cons of the options</b> (per-option ✅/❌ — click to expand)</summary>

#### Option A: Forbid hand-edits via FS locks / permissions

- ✅ **Good:** No drift surface — files can't change.
- ❌ **Bad:** Friction kills adoption; users will find
  escape hatches anyway.
- ❌ **Bad:** Tenet 8 violation — hand-edits are first-class
  input; forbidding them inverts the principle.

#### Option B: Pull-and-clobber by default with warnings

- ✅ **Good:** Simple implementation; no reconcile UI.
- ❌ **Bad:** Violates Tenet 1 (first, do no harm). Users
  would lose data they didn't realize they could lose.

#### Option C: Privileged Claude-write channel

- ✅ **Good:** Slightly simpler default treatment (Claude
  writes auto-accept).
- ❌ **Bad:** Non-uniform; hard to audit; user wants the
  same drift framework for all writers with different
  default actions.

#### Option D: Background watcher daemon as v1

- ✅ **Good:** Proactive surfacing of drift the moment it
  appears.
- ❌ **Bad:** Daemon to install, monitor, restart, log;
  expensive for v1.
- ⚖️ **Neutral:** Deferred to v1.1; v1's "sync/status only"
  model is cheap, predictable, and easy to test.

#### Option E: Per-line provenance instead of per-file

- ✅ **Good:** Most precise routing for layer attribution.
- ❌ **Bad:** Heavyweight to implement and store.
- ⚖️ **Neutral:** Per-file with diff-hunk granularity is
  the right balance for v1; per-line is a v2 option if
  review-routing becomes a pain point.

#### Option F (chosen): SHA tracking + reconcile menu

- ✅ **Good:** No silent data loss; safe by default;
  cron/CI safe via `--non-interactive`.
- ✅ **Good:** Single pipeline for all drift sources.
- ❌ **Bad:** Non-trivial code surface (~500 LOC + hook +
  skill).

</details>

## Followups

- **Active capture (v1.1)** rides on top of this ADR — the staging
  file Claude writes to becomes another drift source consumed by
  reconcile.
- **Watch-mode drift detection (v1.1)** would add a background
  process or `SessionStart` hook that runs `maury status --quiet`
  and surfaces drift count proactively.
- **Refactor-from-replacement (v2)** per ADR-0019 — the renderer's
  warnings during reconcile become actionable via `maury refactor`.

<!-- The 2026-05-07 schema-extension addendum was retired here;
     its content is now captured in the **Amended:** field at
     the top of this ADR. See ADR-0023 §6 for the authoritative
     claude-writes.jsonl schema. -->

## Claude Code references

Verified-as-of 2026-05-07 against Anthropic's official Claude
Code documentation:

- [`cc-hooks`][cc-hooks] — `PostToolUse` event semantics and
  payload structure (full schema in ADR-0023's references).
- [`cc-skills`][cc-skills] — skill location and SKILL.md
  invocation pattern; used for the `maury-status` skill
  defined above.

[cc-hooks]: https://code.claude.com/docs/en/hooks
[cc-skills]: https://code.claude.com/docs/en/skills
