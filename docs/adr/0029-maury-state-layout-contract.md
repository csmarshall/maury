# ADR-0029: `~/.claude/maury-state/` layout contract

**Status:** Accepted
**Date:** 2026-05-07
**Amended:**
- 2026-05-07 — added `last-version-check.json` to the File
  inventory per [ADR-0031](0031-self-update-path.md) (cached
  PyPI version-check result; written at most once per day; used
  to gate the stale-version warning).
- 2026-05-07 — `audit.jsonl` row de-stubbed: was previously
  "TODO: Phase 10 ADR — not yet written"; now points at
  [ADR-0035](0035-audit-log.md) which fully specifies the
  audit log.

## Related tenets

- [Tenet 1 — First, do no harm](../tenets.md#1-first-do-no-harm)
- [Tenet 4 — Sensitive data stays local](../tenets.md#4-sensitive-data-stays-local)
- [Tenet 7 — Provenance is mandatory](../tenets.md#7-provenance-is-mandatory)

## Context

Multiple ADRs (0017, 0023, 0025, 0026) have introduced files
under `~/.claude/maury-state/` with no single doc that
enumerates the full set, defines ownership and lifecycle, or
states the cross-cutting invariants (never-sync-to-git,
host-local, etc.).

A new contributor or a doc-review reading any one of those ADRs
sees one or two files and has to assemble the full picture from
fragments. Worse, when a future ADR wants to add a new state
file, it has no canonical reference to align with — leading to
inconsistent naming (`.json` vs `.jsonl`), inconsistent
ownership notes, drift in the never-sync-to-git policy.

This ADR is the consolidation: the single canonical contract
for what lives under `~/.claude/maury-state/`, who writes it,
who reads it, when it's created/updated/pruned, what happens
when the dir is lost.

## Decision

### Directory: `~/.claude/maury-state/`

Per-host, host-local. **NEVER synced to git.** This is the
maury equivalent of `.git/` — operational state owned by the
running maury installation, not part of any user-facing or
synced content.

Defense-in-depth on never-sync-to-git:

1. The directory lives outside any synced repo path (synced
   repos are cloned under `repos_root`, typically
   `~/.config/maury/repos/`, NOT under `~/.claude/`). This is
   the load-bearing guarantee — even without any .gitignore,
   the path is structurally not in any git working tree maury
   manages.
2. Files in this directory may contain transcript content
   snippets, session IDs, host identifiers — sensitive per
   tenet 4. The maury-owned-only contract here makes that
   sensitivity explicit so contributors don't add a "let's
   sync this for convenience" feature later.

Followup: a future ADR could add `~/.claude/.gitignore`
rendering by maury as a third defense layer. Not in v1 — the
path-outside-tree guarantee is sufficient.

### File inventory

Every file maury writes under this directory, the ADR that
specifies its schema, its lifecycle, and its purpose.

| File | Specified in | Format | Purpose |
|---|---|---|---|
| `last-render.json` | [ADR-0017](0017-drift-detection-and-reconciliation.md) | JSON (single doc) | Path + sha256 of every file maury most recently rendered. Drift detection compares on-disk SHAs against this baseline. |
| `claude-writes.jsonl` | [ADR-0017](0017-drift-detection-and-reconciliation.md) §"Drift sources and treatment", schema locked in [ADR-0023 §6](0023-hook-installation-and-tool-resolution.md#6-log_tool_use-payload-schema-locked) | JSONL (append-only, ≤4 KB lines) | Every Claude Code Edit/Write/MultiEdit tool use, written by the `PostToolUse log_tool_use` hook. Drift attribution distinguishes Claude-writes from human hand-edits. |
| `active-context.json` | [ADR-0013 amendment](0013-active-in-session-capture.md) §"Active-context file" | JSON (single doc) | Current active mode + inheritance chain. Read by Claude via the `maury-stage` skill so it knows what's in scope for capture suggestions. Rewritten on every `maury init` and `maury mode use`. |
| `active-sessions.jsonl` | [ADR-0025](0025-profile-switching-session-safeguards.md) | JSONL (append-only, event log) | Live session lifecycle events (`session_start`, `tool_use`, `session_end`). Reduced on read into per-session-id state. Used for the active-session safety check on mode switch. |
| `session-history.jsonl` | [ADR-0026](0026-profile-aware-mining.md) | JSONL (append-only, one record per completed session) | Durable session-to-mode linkage. Written by `SessionEnd` hook (consumer 2 of the same hook ADR-0025 installs). Mining uses this for profile-aware filtering. |
| `mode-switches.jsonl` | [ADR-0025](0025-profile-switching-session-safeguards.md) §"On a clean switch" point 1 | JSONL (append-only) | Stub audit log for profile-switch events until Phase 10 audit log lands; gets folded into the audit log later. |
| `audit.jsonl` | [ADR-0035](0035-audit-log.md) | JSONL (append-only) | Comprehensive audit of every state-changing maury operation. Subsumes `mode-switches.jsonl` via one-shot migration. |
| `last-version-check.json` | [ADR-0031](0031-self-update-path.md) | JSON (single doc) | Cached result of the once-per-day PyPI version check (installed_version, latest_version, channel, checked_at). Used to gate the stale-version warning without re-probing PyPI on every command. |

### Cross-cutting invariants

These properties apply to every file in the directory:

1. **Maury-owned.** Maury writes; the user shouldn't hand-edit.
   Hand-editing is not actively prevented (no chmod tricks),
   but hand-edits will be silently overwritten on the next
   maury operation that touches the file. Documented as "do
   not edit by hand" in a comment at the top of single-doc
   files; for JSONL files the invariant is documented here.
2. **Host-local.** Each host has its own copy; no syncing.
3. **Never in git.** Defense-in-depth as listed above.
4. **Crash-safe writes.** Single-doc files use `tmp + rename`
   (POSIX atomic). JSONL files use `O_APPEND` with ≤4 KB lines
   for atomic concatenation per
   [`cc-contract:concurrent-sessions`](../claude-code-contract.md#cc-contractconcurrent-sessions).
   *Note: the `tmp + rename` requirement is specified here for
   the first time as a cross-cutting invariant. ADR-0017's
   `last-render.json` and ADR-0013-amendment's
   `active-context.json` did not previously call this out;
   both are amended-by-implication via this contract.
   Implementation must follow.*
5. **Recoverable from loss.** If `~/.claude/maury-state/` is
   deleted or corrupted, `maury init` (or the first
   `maury sync` on a host that's already initialized)
   reconstructs what it can:
   - `last-render.json` is rebuilt from the next render.
   - `active-context.json` is rewritten on next `maury init`
     or `maury mode use` per
     [ADR-0013 amendment](0013-active-in-session-capture.md).
   - The append-only logs (`claude-writes.jsonl`,
     `active-sessions.jsonl`, `session-history.jsonl`,
     `mode-switches.jsonl`) are **lost permanently** —
     there's no way to reconstruct historical events. Drift
     attribution and active-session detection will be
     functional going forward but blind to anything before
     the loss. Mining can still mine transcripts but loses
     the profile-attribution for past sessions (treats them as
     "unattributed" per ADR-0026's migration story).
   - `pending-mining/` is lost; offline-staged work is gone.
   The recovery story is "you can keep using maury, but you
   lose history."
6. **No file format negotiation.** Each file's format is
   pinned by its specifying ADR. Schema changes are
   ADR-amendments, not silent migrations.

### Naming convention

- **`.json`** — single-document files; full read-modify-write
  via `tmp + rename`.
- **`.jsonl`** — append-only event/record logs; one JSON object
  per line; lines ≤4 KB for atomic-append safety; reduced on
  read into the consumer's preferred shape.

A `.json` file that's actually appended to, or a `.jsonl` file
that's actually rewritten as a single doc, is a bug. Pick the
right extension up front.

### What does NOT live in this directory

For clarity by negative space:

- **The user's actual configuration** — `~/.claude/CLAUDE.md`,
  `~/.claude/settings.json`, `~/.claude/skills/`, etc. Those
  are *rendered output*; this directory is *operational state*.
- **The synced repos themselves** — they live at `repos_root`
  (typically `~/.config/maury/repos/`).
- **Maury's installed Python package** — managed by pip/uv.
- **Past Claude Code transcripts** — `~/.claude/projects/<hash>/...jsonl`,
  owned by Claude Code per
  [`cc-contract:past-transcripts-not-auto-loaded`](../claude-code-contract.md#cc-contractpast-transcripts-not-auto-loaded).
  Maury reads these but doesn't write into the `projects/`
  tree.
- **Auto-memory** — `~/.claude/memory/MEMORY.md` per
  [`cc-contract:startup-files-loaded`](../claude-code-contract.md#cc-contractstartup-files-loaded).
  Loaded by Claude Code at session start; rendered by maury
  like CLAUDE.md.
- **The `~/.maury-host-id` file** — lives at user `$HOME`, not
  under `~/.claude/`, per [ADR-0015](0015-surrogate-keys-for-hosts-and-profiles.md)
  and [ADR-0018](0018-minimum-bootstrap-ux.md). Identity, not
  state.
- **The maury-staging dir** — `~/.claude/maury-staging/`, per
  [ADR-0013](0013-active-in-session-capture.md). Conceptually
  related (also host-local, also never-sync-to-git) but a
  different directory because its lifecycle is different.
  maury-staging holds two kinds of content:
  1. **User-reviewable captures** at
     `~/.claude/maury-staging/captures.jsonl` (per ADR-0013)
     — the user reviews via `maury review` and clears as
     they're processed.
  2. **Maury-managed offline-mining material** at
     `~/.claude/maury-staging/pending-mining/` (per
     [ADR-0028](0028-offline-behavior.md)) — maury writes
     during `--store-only` (transcripts normalized + redacted
     into windows) and consumes during `--resume-pending`
     (LLM extraction runs over the staged windows).

  Both are user-visible work-in-progress, distinct from
  maury-state's strictly-bookkeeping role. Note specifically
  that **`pending-mining/` lives under maury-staging, NOT
  under maury-state** — it's offline work-in-progress, not
  bookkeeping state.

### Future additions

When a future ADR wants to add a new state file:

1. The new file lives under `~/.claude/maury-state/` only if
   it satisfies all six cross-cutting invariants above.
2. The ADR adding it MUST update this ADR's "File inventory"
   table (an `**Amended:**` entry on this ADR with the
   addition).
3. The new file MUST conform to the naming convention
   (`.json` for single-doc, `.jsonl` for append-only).

ADRs that introduce host-local-but-different-lifecycle state
(like the maury-staging dir) should put it in a sibling
directory, not under `maury-state/`. The clean separation
keeps the recovery story understandable.

## Consequences

- **Single canonical reference.** A new ADR author or
  contributor reads this ADR and knows the full state-file
  picture; no scavenger hunt across 0017/0025/0026.
- **Naming and lifecycle conventions are explicit.** No
  silent format drift; mismatched extensions become visible
  bugs.
- **Recovery story is documented and honest.** Append-only
  history is permanently lost on dir loss; the user knows
  what they lose. Backup recommendations (gap J, planned
  ADR) can target this directory specifically.
- **Defense-in-depth on never-sync-to-git.** Three
  independent mechanisms (path outside synced repos,
  `.gitignore`, this contract) all aligned.
- **Future ADRs are constrained.** Adding a new state file
  means updating this ADR's table — a forced consistency
  point. No new file silently appearing.
- **No new code.** Pure documentation + organizational
  contract. Implementation cost is zero (the files already
  exist or are spec'd in their respective ADRs).

## Alternatives considered

- **Document layout in concepts.md instead.** Rejected:
  concepts.md defines terms; this is operational specification.
  Different document type.
- **Document each file's contract only in its specifying ADR
  (no consolidation).** Rejected: status quo, exactly the
  scavenger-hunt problem this ADR solves.
- **Make this ADR the source of truth for individual file
  schemas (move them out of their specifying ADRs).**
  Rejected: each file's schema belongs with the design
  decision that introduces it. This ADR is the *index*, not
  the *content*.
- **Use a YAML manifest (e.g., `state-files.yaml`) instead of
  a markdown table.** Considered. Rejected: the audience is
  human readers (ADR authors, contributors); the table is
  hand-readable and the file count is small. A
  machine-readable manifest is over-engineering at this scale.

## Build-order placement

No code work. This ADR is pure consolidation; the files it
documents are spec'd elsewhere and ship per their respective
phases. The contract is enforced socially: doc-review agents
should check that any new state file mentioned in a future
ADR also gets added to this ADR's inventory table.

## Followups

- **Backup recommendations** (gap J, planned ADR): which
  files are worth backing up vs. which are recoverable. This
  ADR's recovery section is the input to that planning.
- **`maury state list` command.** A small CLI helper that
  prints the inventory table for the current host with file
  sizes and last-modified timestamps. Diagnostic; not v1.
- **`maury state prune`** for removing stale append-only log
  entries (e.g., session-history older than N years). Not v1
  — append-only growth is bounded by user activity, which is
  modest at single-user scale.

## Claude Code references

This ADR introduces no new Claude Code dependencies. It
references existing contract entries:

- [`cc-contract:concurrent-sessions`](../claude-code-contract.md#cc-contractconcurrent-sessions)
  — for the JSONL `O_APPEND` ≤4 KB atomic-append story.
- [`cc-contract:past-transcripts-not-auto-loaded`](../claude-code-contract.md#cc-contractpast-transcripts-not-auto-loaded)
  — for the "Claude Code owns `~/.claude/projects/`" boundary.
- [`cc-contract:startup-files-loaded`](../claude-code-contract.md#cc-contractstartup-files-loaded)
  — for the "auto-memory belongs to Claude Code, not maury-
  state" boundary.

## Amendment history

- 2026-05-11 — "profile" renamed to "mode" per ADR-0037 doctoral examination. `profile-switches.jsonl` → `mode-switches.jsonl`; `maury profile use` → `maury mode use`; table descriptions updated. No structural changes.
