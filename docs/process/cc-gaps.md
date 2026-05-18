# Claude Code gaps — maury wishlist

This document tracks Claude Code behaviors that maury works around today
because the feature doesn't exist upstream. Each entry records:

- **The gap** — what's missing
- **Maury's workaround** — what we do instead
- **What we'd gain** — how maury's design or UX would improve if CC added it
- **Upstream status** — known issue, filed, or not yet filed

The intent is twofold: inform maury design decisions (so workarounds can be
simplified when gaps close), and feed into upstream feature requests to
Anthropic.

Cross-reference: `docs/claude-code-contract.md` tracks *documented* CC
behaviors. This file tracks *absent* behaviors.

---

## Lifecycle hooks

### GAP-1: No `ContextCompression` hook — **CLOSED**

**Status.** Closed. Claude Code ships a `PostCompact` hook that fires after
the context window is summarized. Empirical verification against the current
release is needed before relying on it in production; label any maury code
using this as "empirical (verified vX.Y.Z)."

**Original gap.** Claude Code had no hook that fired when the context window
was summarized mid-session. Compression silently discarded raw conversation
history and replaced it with a summary. Maury had no way to detect this happened.

**Resolution.** The `PostCompact` hook fires after compression completes. Maury
can now:
- Write an automatic `session-state.md` checkpoint at compression time — the
  highest-value moment for a state write, since the session just lost raw history.
- Re-validate that precept layer content is still represented in the compressed
  summary; if compressed away, nudge the user or re-inject.
- Record compression events in `active-sessions.jsonl` for mining (ADR-0026).

**Upstream status.** Closed upstream — `PostCompact` hook ships in Claude Code.

---

### GAP-1b: No `PreCompact` hook (fires BEFORE summarization)

**The gap.** `PostCompact` fires *after* the context window has been summarized
and raw history is already gone. There is no `PreCompact` hook that would fire
*before* summarization runs — the last moment when raw conversation history is
still intact and could be persisted by a hook script.

**Maury's workaround.** Assistant-side discipline only — the assistant is
supposed to proactively write `session-state.md` after meaningful events.
Works most of the time, fails exactly when context pressure is highest
because the model is least likely to volunteer overhead at that moment.
PostCompact (GAP-1) catches "compression just happened" but cannot save the
detail that compression destroyed.

**What we'd gain.**
- A deterministic save point at the last moment full transcript is available.
- The structural answer to "save before the context disappears" that the
  current discipline only approximates.
- Pairs with the planned `$CLAUDE_CONTEXT_PERCENT` field in hook stdin
  (GAP-7) for budget-aware persistence decisions.

**Upstream status.** Filed and discussed at
[anthropics/claude-code#54580](https://github.com/anthropics/claude-code/issues/54580).
Maury voiced as a second downstream consumer ([read-side / write-side framing](https://github.com/anthropics/claude-code/issues/54580#issuecomment-4481758459))
alongside [claude-session-handoff](https://github.com/yacb2/claude-session-handoff).
The request is also part of the consolidation RFC at
[anthropics/claude-code#59492](https://github.com/anthropics/claude-code/issues/59492)
where maury endorsed the three-primitive set (`UserIdle` + `PreCompact` +
`$CLAUDE_CONTEXT_PERCENT`).

---

### GAP-2: No `SessionResume` hook (distinct from `SessionStart`)

**The gap.** `SessionStart` fires once per session — but it fires the same way
for a *fresh* session and for a *resumed compacted session*. There is no event
that distinguishes "new context window" from "continuing a previously compressed
conversation."

**Maury's workaround.** ADR-0025 records a `resumed_from` field in
`active-sessions.jsonl` when a session is launched via `claude --resume <id>`,
so maury *can* tell resumed sessions from fresh ones at the data level. The
remaining limitation: there is no hook that fires *only* on resume, so logic
that should run only on resume (e.g., precept re-validation, drift check) must
run on every `SessionStart` and self-filter based on `resumed_from`.

**What we'd gain.**
- A dedicated `SessionResume` hook that fires only when resuming would let
  maury scope precept re-validation and drift checks to resume-only, avoiding
  redundant work on fresh sessions.
- Cleaner hook scripts: no `resumed_from` null-check needed inside the hook.

**Upstream status.** Not filed.

---

### GAP-3: No `LowContext` / token-warning hook

**The gap.** There is no hook that fires when the session is approaching the
context window limit, before compression is triggered.

**Maury's workaround.** None. Users hit compression without warning.

**What we'd gain.**
- Automatic state export before the window fills — the last moment where
  raw history is available.
- User-facing warning so they can wrap up a thought or explicitly export state
  before continuity breaks.

**Upstream status.** Not filed.

---

### GAP-4: No `PreSession` hook (before CLAUDE.md is read)

**The gap.** The earliest hook that fires is `SessionStart`. Based on the CC
hook firing model (`cc-contract:event-firing-cadence`), `SessionStart` fires
once the session is open — but CC documentation does not explicitly state
whether this is before or after CLAUDE.md is loaded into context. *(Assumed:
CLAUDE.md loads before any hook fires, since hooks are defined in settings.json
which is read at startup alongside CLAUDE.md. Label: assumed — needs
empirical verification.)* If correct, there is no hook that runs before config
is read.

**Maury's workaround.** Users must run `maury sync` manually before starting a
session. If they forget, they work with a stale CLAUDE.md until the next sync.
There is no way to auto-sync at session open.

**What we'd gain.**
- `maury sync --quiet` could run at session open, ensuring config is always
  current at session start without user intervention.
- Precept layers (ADR-0037, when drafted) could be fetched and rendered
  just-in-time before the session loads them.

**Upstream status.** Not filed.

---

## Session and config

### GAP-5: CLAUDE.md is not hot-reloaded — **PARTIALLY ADDRESSED**

**Status.** Partially addressed. `.claude/rules/` files and `.claude/skills/`
live-reload within a session without restart. `CLAUDE.md` itself still does not
hot-reload. Maury's render architecture should route content that benefits from
live-reload into rules/skills files rather than CLAUDE.md.

**Original gap.** CLAUDE.md is read at session start and not re-read if the
file changes on disk. Running `maury sync` mid-session updates the file but the
current session never sees the new content.

**Partial resolution.** Claude Code live-reloads:
- `.claude/rules/*.md` — rules files reload on change within a session
- `.claude/skills/` — skill definitions reload on change within a session
- `~/.claude/keybindings.json` — keybindings reload on change

This means maury can route frequently-updated content (mode rules, domain
rules) into rules files, where a mid-session `maury sync` will take effect
immediately without restarting. Universal base preferences in `CLAUDE.md`
still require a new session to pick up changes.

**Remaining gap.** CLAUDE.md itself still does not hot-reload. Content maury
writes there is session-static. No upstream fix known.

**Maury's revised workaround.** Route mutable content to `.claude/rules/`
(live-reload). Reserve `CLAUDE.md` for stable universal preferences that
rarely change and don't require mid-session updates.

**Upstream status.** Not filed (for the remaining CLAUDE.md gap).

---

### GAP-6: No native profile / external config tracking

**The gap.** Claude Code records session transcripts and auto-memory but has no
concept of "which external config was active during this session." There is no
way to query CC to find out which CLAUDE.md content was loaded for a given
session ID. This is documented in
[`cc-contract:no-native-profile-tracking`](../claude-code-contract.md#cc-contractno-native-profile-tracking).

**Maury's workaround.** ADR-0025's hooks-and-state-file mechanism: maury writes
its own `active-sessions.jsonl` at `SessionStart` / `SessionEnd`, recording
which profile and host were active.

**What we'd gain.**
- If CC recorded a content-hash or path of the CLAUDE.md it loaded, maury
  could verify drift without a separate state file.
- Cross-session mining ([ADR-0026](../adr/0026-profile-aware-mining.md)) could
  correlate findings to the config that was active without relying on maury's
  own session records.

**Upstream status.** Not filed.

---

### GAP-7: Hook stdin payload doesn't include context-window metadata

**The gap.** Hook stdin payloads (per
[`cc-contract:hooks-stdin-payload`](../claude-code-contract.md#cc-contracthooks-stdin-payload))
include tool name, input, output, session ID, and transcript path. They do not
include current token count, context window utilization, or whether compression
has occurred in this session.

**Maury's workaround.** None. Hooks cannot make decisions based on how full the
context window is.

**What we'd gain.**
- Token count in the payload would let maury's `Stop` hook decide whether to
  export state (near-full → export; plenty of room → skip).
- Compression flag in the payload would let any hook downstream of compression
  know the context has changed.

**Upstream status.** Not filed.

---

## Action item

See GitHub issue [#4](https://github.com/csmarshall/maury/issues/4) — "Research upstream feature request
process for Claude Code gaps." Before filing any of the above with Anthropic,
we need to determine the right channel (GitHub discussions, feedback form,
developer forum) and the right format (reproducer steps? use-case framing?
both?). The issue tracks that research and will link back here as gaps are
filed.
