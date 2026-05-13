# ADR-0025: Mode switching with session-boundary safeguards

**Status:** Accepted
**Date:** 2026-05-07

## Related tenets

- [Tenet 1 — First, do no harm](../tenets.md#1-first-do-no-harm)
- [Tenet 3 — Trust boundaries are physical](../tenets.md#3-trust-boundaries-are-physical-not-policy)
- [Tenet 5 — The user arbitrates ambiguity](../tenets.md#5-the-user-arbitrates-ambiguity)

## Context

[ADR-0001](0001-n-profiles.md) established that a host has one
*active* mode at a time, with the option to switch via
`maury mode use <name>`. That CLI surface was named but never
specified. This ADR fills the gap.

The risk mode-switching introduces is real and specific:

> If a host can switch from `personal` to `work` (or any
> cross-boundary pair) while a Claude Code session is running, the
> running session has the **old** mode's CLAUDE.md and skills
> loaded into its working memory. The user, having run
> `maury mode use work`, may believe the assistant is operating
> under work-context constraints — but the session is not. Tools
> the user kicks off in that session can write personal-context
> content into work-tracked files (or the reverse, which is worse).

Claude Code's session boundary is otherwise clean. Per [Claude
Code's "How Claude Code works" documentation][cc-how-it-works] and
the [memory documentation][cc-memory], a fresh `claude` invocation
reads `~/.claude/CLAUDE.md`, `~/.claude/memory/MEMORY.md`,
`settings.json`, hooks, agents, and skills at startup, and "each
new session starts with a fresh context window, without the
conversation history from previous sessions." Per [the sessions
documentation][cc-sessions], past transcripts at
`~/.claude/projects/<project>/<session-id>.jsonl` are data-at-rest
and are **not** auto-loaded into a new session unless the user
explicitly invokes [`claude --resume <id>` or `claude --continue`][cc-cli-resume]
(they are a mining-time concern, not a session-context concern;
cross-host coordination of mining is gap E in the gaps walkthrough).
Terminal-emulator scrollback is a display artifact and is unrelated
to what Claude loads.

So the only cross-boundary leakage path that exists is **active
sessions whose memory predates the switch**. Maury's job is to
prevent that path from opening.

## Decision

`maury mode use <name>` refuses to act unless three preconditions
hold. Each precondition has a deliberate "loud override" affordance
for advanced users.

### Precondition 0 — Host's mode is not locked

If the manifest entry for this host has `lock: true` (per
[ADR-0001](0001-n-profiles.md)'s opt-out), `maury mode use`
refuses outright with a message pointing at the lock setting. The
remaining preconditions don't apply because the operation isn't
permitted on this host at all. To switch on a locked host, edit
the manifest to set `lock: false` first (and accept the
implication that mode changes on this host are now possible).

### Precondition 1 — No active Claude Code sessions on this host

Active-session detection lives at
`~/.claude/maury-state/active-sessions.jsonl`, an append-only event
log maintained by hooks shipped under
[ADR-0023](0023-hook-installation-and-tool-resolution.md)'s
marker scheme.

Per [Claude Code hooks][cc-hooks], events fire at different
cadences:

- `SessionStart` — **once per session.** Use this for "session
  began on this host."
- `SessionEnd` — **once per session.** Use this for "session
  ended on this host." NOT `Stop` — `Stop` fires once per *turn*
  (each user-prompt → assistant-response cycle), so it would
  fire many times per session and cannot serve as a session-
  lifecycle marker.
- `PostToolUse` — fires after **every** tool call (not once per
  session). Useful as a recency heuristic only; not a lifecycle
  event.

The same `SessionStart` hook is also referenced by
[ADR-0017](0017-drift-detection-and-reconciliation.md)'s deferred
"watch-mode drift detection" followup — there is **one**
SessionStart installation, multiple consumers (active-session
tracking here, drift-watch later).

Event schema (one JSON object per line, ≤4 KB):

```json
{"event":"session_start","ts":"...","session_id":"abc...",
 "project_path":"~/work/foo","resumed_from":null}
{"event":"tool_use","ts":"...","session_id":"abc..."}
{"event":"session_end","ts":"...","session_id":"abc..."}
```

`resumed_from` is the prior `session_id` if this session was
launched via [`claude --resume <id>`][cc-cli-resume]; null
otherwise. Resumption matters because a resumed session inherits
the prior session's working memory — see §"What maury cannot
prevent."

Append-only with ≤4 KB lines preserves the same POSIX `O_APPEND`
atomicity story as `claude-writes.jsonl` (multiple sessions can
write concurrently without locking). Per [Claude Code
sessions][cc-sessions], multiple concurrent Claude Code sessions
on one host are explicitly supported, so concurrent writes to
this log are expected.

**On read:** walk the log, build a map `session_id → {started_at,
last_tool_use_at, ended_at, resumed_from}`. A session is
considered **active** if it has a `session_start` event with no
matching `session_end`, regardless of how recently it had a
tool use. The precondition check errs conservative on the safety
path: if the bookkeeping says a session is open, refuse the
switch.

**Ghost-session cleanup is a separate, opt-in operation.** Long-
running sessions can sit idle between tool calls for arbitrary
periods (the user is reading, thinking, or away from keyboard);
treating "no activity for N minutes" as "ghost" risks
mis-classifying a live session as gone — exactly the leakage path
this ADR closes. Cleanup of clearly-dead entries (e.g., older than
24h with no `session_end`) happens via an explicit
`maury sessions prune` command, never as a side effect of a
safety check.

### Precondition 2 — No unresolved drift on `~/.claude/`

Per [ADR-0017](0017-drift-detection-and-reconciliation.md), drift =
file content on disk differs from `last-render.json`. If a mode
switch were allowed with unresolved drift, the user's hand-edits
made under mode A would either:

- Be overwritten by mode B's render (silent data loss → tenet 1 violation), or
- Be carried into mode B's namespace (cross-mode leakage of
  edits the user explicitly made in A's context).

Neither is acceptable. Mode switch refuses with a pointer to
`maury reconcile`. The user resolves drift first (adopt as
proposal on the OLD mode's run branch, mark hand-managed under
the OLD mode, or revert), THEN switches.

### Precondition 3 — `--force` is rejected unless paired with the explicit-acknowledgement flag

The escape hatch is deliberately obnoxious to type:

```
maury mode use work --force --i-understand-cross-boundary-risk
```

Both flags are required. `--force` alone exits 1 with:

```
ERROR: --force is not sufficient for mode switch.
This command can leak context across trust boundaries when used
carelessly. Re-run with both:
  --force --i-understand-cross-boundary-risk
```

The verbose flag's purpose is to make the user pause and think
before crossing a boundary — friction by design. There is no shorter
form, no env var, no config option to disable the requirement.

This is **inconsistent with `--force` usage in
[ADR-0017](0017-drift-detection-and-reconciliation.md) and
[ADR-0024](0024-manifest-concurrency-inclusive-merge.md)**, which
treat `--force` as sufficient on its own. Mode switch warrants
the extra friction because its blast radius — cross-boundary
context leakage — is qualitatively different from the local
overwrite/refuse tradeoffs `--force` covers in those ADRs. The
inconsistency is intentional and called out here so a reader who
notices it knows it's deliberate, not an oversight.

### On a clean switch (all preconditions pass), maury also:

1. **Writes an audit log entry** (Phase 10):
   `{ts, host_id, from_mode, to_mode, sessions_state, force_flag_used}`.
2. **Renders the new CLAUDE.md with a mode banner.** Two layers,
   both using [ADR-0023](0023-hook-installation-and-tool-resolution.md)'s
   `maury-managed` sentinel string for cross-tooling consistency
   (drift detection, doc-review agents, and any future maury-aware
   tooling all recognize the same marker word):
   - A structured HTML-comment marker at the top:
     `<!-- maury-managed: active mode work (mode_<short>...) -->`
   - A human-visible sentence near the top of CLAUDE.md:
     *"You are operating in the **work** mode context."*
   This lets Claude's first response in any new session confirm the
   active mode back to the user — a verification surface beyond
   what's written to disk.
3. **Surfaces `maury status`** (the v1 skill from ADR-0017) which
   returns the active mode + last-switch timestamp + any pending
   captures or drift. The user can invoke this mid-session to
   confirm boundary state.
4. **Updates `last-render.json`** with the new mode's content
   SHAs, so subsequent drift detection compares against the new
   baseline.

### What maury cannot prevent

Honest acknowledgements:

- **A user who suppresses both safeguards** (`--force
  --i-understand-cross-boundary-risk` with active sessions) gets
  what they asked for. The audit log records the choice; if
  leakage happens, that record is the forensic trail.
- **A user who manually edits `~/.claude/CLAUDE.md` and `settings.json`**
  to a different mode's content, bypassing `maury mode use`,
  defeats every safeguard in this ADR. Drift detection
  ([ADR-0017](0017-drift-detection-and-reconciliation.md)) will
  catch this on the next sync, but until then the running session
  is mismatched. Documented as an out-of-scope failure mode.
- **A session resumed via `claude --resume <id>`** inherits the
  prior session's working memory. The `resumed_from` field in
  the active-sessions log makes this visible, but the active-
  session check only catches running sessions, not the resumption
  *of* a past session whose lifetime spanned a mode switch.
  Documented; the audit log of past mode switches lets a
  curious user reconstruct whether a given resumed session is at
  risk.
- **Past transcripts under `~/.claude/projects/<hash>/`** persist
  on disk after a mode switch. They are visible to mining
  ([ADR-0005](0005-local-only-mining.md)). Mode-aware mining is
  a separate concern (gap E).

## Consequences

- **The cross-boundary leakage path is closed by default.** A
  no-flag `maury mode use` cannot cause a running session to
  see content from the new mode or vice versa, because it
  refuses to run while sessions are active.
- **The escape hatch exists but costs you a long flag.** Power
  users who know they're switching contexts in a way that's safe
  for their workflow can do it; accidental drive-by switches are
  prevented.
- **The drift refusal aligns with tenet 1.** Hand-edits made
  under one mode are explicitly handled before being carried
  across.
- **Two new hooks join the marker-managed set:** `SessionStart`
  and `SessionEnd`, alongside the existing `PostToolUse log_tool_use`
  from ADR-0017. (NOT `Stop` — that fires per-turn, not per-session,
  per [Claude Code hooks][cc-hooks].) Cost: small. The shared
  `active-sessions.jsonl` file uses the same POSIX `O_APPEND`
  ≤4 KB atomicity story as `claude-writes.jsonl` (append-only
  event records, reduced on read into a per-session-id state map).
- **The mode banner is double-coverage:** structured marker
  for tooling + human-readable sentence for the user. If Claude
  responds about something that contradicts the banner, the user
  has an immediate signal that something is off.
- **Implementation cost is bounded.** ~150 LOC for the precondition
  checker + the two new hooks + the banner-injection step in the
  CLAUDE.md renderer. Plus tests.

## Alternatives considered

- **No safeguards; trust the user.** Rejected: the harm scenario
  is concrete and the user explicitly flagged it. Tenet 1.
- **Auto-kill active sessions on switch.** Rejected: violates the
  user's session state without consent. The user might have
  unsaved work in the assistant's response queue.
- **Lockfile during sessions, blocking ALL maury operations.**
  Rejected: too aggressive — `maury status`, `maury sync`
  (read-only path), `maury mine` are all safe to run with active
  sessions.
- **PostToolUse-only detection** (no SessionStart/SessionEnd hooks).
  Considered. Rejected because a freshly-started session that has
  not yet made a tool call would not appear in
  active-sessions.jsonl, and a quick mode switch in that window
  would silently succeed — opening the exact leakage path we're
  trying to close. `SessionStart` fires before any tool use.
- **Use `Stop` instead of `SessionEnd`.** Rejected on
  [hooks documentation][cc-hooks] verification: `Stop` fires once
  per *turn* (each user-prompt → assistant-response cycle), not
  once per session. Tracking "session ended" with `Stop` would
  mark sessions as ended after the first assistant response,
  silently treating live sessions as gone — exactly the leakage
  path this ADR closes.
- **Single `--force` flag without the verbose acknowledgement.**
  Rejected: `--force` is a casual flag in many tools; users type
  it without thinking. A mode switch is not a casual operation
  when a session is active. The verbose flag is friction-by-design.
- **Banner only as a comment, no human-visible line.** Rejected:
  the user reads CLAUDE.md too. A line of prose anchored at the
  top is more reliable as a verification surface than a comment a
  reader's eye skips over.

## Build-order placement

- **Active-session detection** (the two new hooks +
  `active-sessions.jsonl` + the precondition checker) lands in
  Phase 5.x.c — extending the 5.x.a (drift) / 5.x.b (manifest
  merge) partitioning [ADR-0024](0024-manifest-concurrency-inclusive-merge.md)
  introduced. All three are about "detecting state conditions
  that gate maury operations and surfacing them clearly."
- **Mode banner injection** is a small extension to the render
  engine (Phase 3 — already shipped). Lands as a render-engine
  patch when 5.x.c does, since they ship together as the
  mode-switch slice.
- **Audit log entry on switch** depends on Phase 10 (audit log)
  being available. Until Phase 10 lands, the switch will write to
  a stub log file at `~/.claude/maury-state/mode-switches.jsonl`
  that gets folded into the audit log later.

## Followups

- **Mode banner styling.** The first cut is *"You are operating
  in the **work** mode context."* — this should be tested with
  Claude in real sessions to see if the model actually reflects
  the banner back when asked about its mode context. If not,
  iterate (maybe the banner needs to be in a system-reminder-style
  block, or in the rendered CLAUDE.md's first heading).
- **Per-host `--remember-force-acknowledgement` setting.** If a
  power user switches profiles 10x a day for a legitimate reason,
  re-typing the long flag each time becomes hostile. A
  per-host opt-in to "I've acknowledged the risk; let me use plain
  `--force` for the next 24 hours" might be worth adding. Not v1.
- **Banner-mismatch detection skill.** A `maury check-mode`
  skill that Claude can invoke mid-session to compare what it
  thinks the active mode is (from CLAUDE.md banner it loaded
  at startup) against what maury's state file says now. If they
  differ, alert the user. v1.1.

## Claude Code references

Verified-as-of 2026-05-07 against Anthropic's official Claude
Code documentation:

- [`cc-hooks`][cc-hooks] — hook event names, firing cadence
  (SessionStart/SessionEnd once per session; Stop once per turn;
  PostToolUse on every tool call), and JSON shape.
- [`cc-memory`][cc-memory] — fresh-session reading of CLAUDE.md
  and MEMORY.md.
- [`cc-how-it-works`][cc-how-it-works] — fresh-context-window
  guarantee on each new session.
- [`cc-sessions`][cc-sessions] — transcript JSONL location,
  `--resume`/`--continue` semantics, multiple concurrent sessions
  on one host.
- [`cc-cli-resume`][cc-cli-resume] — explicit session resumption
  via CLI flag.

[cc-hooks]: https://code.claude.com/docs/en/hooks
[cc-memory]: https://code.claude.com/docs/en/memory
[cc-how-it-works]: https://code.claude.com/docs/en/how-claude-code-works
[cc-sessions]: https://code.claude.com/docs/en/sessions
[cc-cli-resume]: https://code.claude.com/docs/en/cli-reference

## Amendment history

- 2026-05-11 — "profile" renamed to "mode" throughout per ADR-0037 doctoral examination. `maury profile use` → `maury mode use`; `profile-switches.jsonl` → `mode-switches.jsonl`; audit schema fields updated (`from_profile`/`to_profile` → `from_mode`/`to_mode`). No semantic changes to safeguard logic.
