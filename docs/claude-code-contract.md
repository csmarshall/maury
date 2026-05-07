# Claude Code interaction contract

This file is maury's **single source of truth** for what we
depend on Claude Code doing. When Claude Code changes — new
version, deprecated event, schema migration — we grep this file
to figure out what in maury is affected.

Each entry is in one of three categories:

- ✅ **Documented** — Anthropic's official docs establish the
  behavior. Cite the URL and quote the relevant text. Lowest
  risk.
- 🧪 **Empirically verified** — Behavior we tested and observed,
  but Anthropic doesn't document. Note the date, Claude Code
  version, and a reproduction command. Medium risk: future CC
  versions could change without warning.
- ❓ **Assumed but unverified** — Behavior we depend on but
  haven't tested AND that isn't documented. High risk. Each
  entry lists what breaks if the assumption is wrong, so we
  know what to fix when reality contradicts us.

Entries here are referenced by ID (e.g., `cc-contract:hooks-stdin-payload`)
from individual ADRs. The ADR cites the abstract behavior; this
file owns the verification status and the "what if we're wrong"
analysis.

**Last verification pass:** 2026-05-07 against Claude Code
documentation at `https://code.claude.com/docs/en/`.

**Reference rot mitigation:** every cited Anthropic page also has
an in-repo snapshot at
[`docs/claude-code-snapshots/2026-05-07/`](claude-code-snapshots/2026-05-07/)
with `MANIFEST.txt` listing names, byte counts, and sha256 hashes.
If a live URL ever changes content, the snapshot is the canonical
record of what the page said when we verified it. See
[`docs/claude-code-snapshots/README.md`](claude-code-snapshots/README.md)
for the refresh process and how to detect drift.

---

## ✅ Documented behaviors

### `cc-contract:fresh-session-context`

A fresh `claude` invocation (no `--resume`) starts with a fresh
context window: the previous session's conversation history is
not loaded.

> *"Each new session starts with a fresh context window, without
> the conversation history from previous sessions."*
> — [How Claude Code Works](https://code.claude.com/docs/en/how-claude-code-works)

**Maury depends on this for:** ADR-0025's session-isolation
guarantee. If this changed (e.g., implicit auto-resume), the
profile-switch safeguards would not protect against cross-
boundary leakage.

---

### `cc-contract:startup-files-loaded`

A fresh `claude` invocation loads `~/.claude/CLAUDE.md`,
`~/.claude/memory/MEMORY.md`, `~/.claude/settings.json`, hooks,
agents, and skills at startup.

> *"Each Claude Code session begins with a fresh context window."
> CLAUDE.md files and settings load at start.*
> — [Memory documentation](https://code.claude.com/docs/en/memory)
> and [Claude Directory documentation](https://code.claude.com/docs/en/claude-directory)

**Maury depends on this for:** ADR-0017 (drift detection assumes
`~/.claude/` content is what Claude reads), ADR-0023 (hook
installation via settings.json), ADR-0025 (profile banner
injection in CLAUDE.md is read by Claude on next session start),
ADR-0011 (`maury doctor` evaluates `~/.claude/CLAUDE.md`).

---

### `cc-contract:past-transcripts-not-auto-loaded`

Past transcripts at `~/.claude/projects/<project>/<session-id>.jsonl`
are stored on disk but NOT auto-loaded into a new session unless
the user explicitly invokes `claude --resume <session-id>` or
`claude --continue`.

> *"Transcripts are stored as JSONL at
> `~/.claude/projects/<project>/<session-id>.jsonl` … Resuming
> a session with `claude --continue` or `claude --resume`
> reopens it under the same session ID and appends new messages
> to the existing conversation."*
> — [Sessions documentation](https://code.claude.com/docs/en/sessions)

**Maury depends on this for:** ADR-0025's session-isolation
story (cross-boundary leakage guarded by no-active-session,
not by transcript-non-existence). ADR-0005's local-only mining
operates on these transcripts safely because no current session
is reading them.

---

### `cc-contract:resume-flag`

`claude --resume <session-id>` resumes a specific past session
and carries forward its conversation context.

> *"`--resume` Resume a specific session by ID or name."*
> — [CLI Reference](https://code.claude.com/docs/en/cli-reference)

**Maury depends on this for:** ADR-0025's `resumed_from` field
on session_start events. A resumed session inherits the prior
session's working memory; if that prior session spanned a
profile switch, the resumption can carry old-profile context
into the new active profile. ADR-0025 §"What maury cannot
prevent" documents this honestly.

---

### `cc-contract:hooks-json-shape`

Hook configuration in `settings.json` has three levels of nesting:

```json
{
  "hooks": {
    "<EventName>": [
      {
        "matcher": "<regex-or-glob>",
        "hooks": [
          {"type": "command", "command": "<shell-string>"}
        ]
      }
    ]
  }
}
```

> *"Hooks are configured in JSON with three levels of nesting"*
> — [Hooks Reference](https://code.claude.com/docs/en/hooks)

**Maury depends on this for:** ADR-0023's marker scheme
(maury-managed entries live in the innermost `command` string
alongside user-added entries; replace by string-matching the
marker, leave non-marked entries untouched).

---

### `cc-contract:hooks-stdin-payload`

Hook subprocesses receive their event payload as a single JSON
object on stdin. For `PostToolUse`:

```json
{
  "session_id": "...",
  "hook_event_name": "PostToolUse",
  "tool_name": "...",
  "tool_input": {...},
  "tool_result": {...}
}
```

> The "PostToolUse Input" section of the
> [Hooks Reference](https://code.claude.com/docs/en/hooks)
> documents the JSON payload structure.

**Maury depends on this for:** ADR-0023 §6's `log_tool_use`
script reads stdin (typically with `jq`), extracts `tool_name`,
`tool_input.file_path`, `session_id`, computes derived values
(SHAs, size delta), and appends to claude-writes.jsonl.

---

### `cc-contract:event-firing-cadence`

Per [Hooks Reference](https://code.claude.com/docs/en/hooks):

| Event | Cadence |
|---|---|
| `SessionStart` | Once per session |
| `SessionEnd` | Once per session |
| `UserPromptSubmit` | Once per turn |
| `Stop` | Once per turn |
| `StopFailure` | Once per turn |
| `PreToolUse` | Every tool call |
| `PostToolUse` | Every tool call |

**Maury depends on this for:** ADR-0025 uses `SessionStart` /
`SessionEnd` for session-lifecycle tracking (NOT `Stop`, which
fires per-turn). ADR-0013 uses `Stop` for the pending-capture
nudge (per-turn cadence is exactly what's wanted for that
notification). Earlier ADR-0025 draft incorrectly used `Stop`
as the session-end event; corrected.

---

### `cc-contract:concurrent-sessions`

Multiple Claude Code sessions can run concurrently against the
same `~/.claude/` directory. Resuming the same session in two
terminals interleaves messages into one transcript.

> *"If you resume the same session in two terminals without
> forking, messages from both interleave into one transcript."*
> — [Sessions documentation](https://code.claude.com/docs/en/sessions)

**Maury depends on this for:** ADR-0023's `~/.claude/maury-state/
claude-writes.jsonl` and ADR-0025's `active-sessions.jsonl` are
both written by hooks running in concurrent sessions. We rely on
POSIX `O_APPEND` atomic-write guarantees on lines ≤ `PIPE_BUF`
(4 KB on Linux/macOS) to handle this.

---

### `cc-contract:no-native-profile-tracking`

Claude Code does NOT itself record any concept of a "maury
profile" or other external metadata about which configuration
was active when a session ran.

> The Sessions docs and Memory docs describe what Claude Code
> records (session ID, transcript, project path, auto memory)
> with no mention of external configuration metadata.

**Maury depends on this for:** ADR-0025's session-history
tracking exists *because* this gap exists. Maury fills it via
its own hooks-and-state-file mechanism. If Claude Code ever
adds native profile/config tracking, maury could potentially
delegate this to CC and simplify.

---

## 🧪 Empirically verified behaviors

*(None yet — empirical-test debt items below need to be moved
here once tested.)*

---

## ❓ Assumed but unverified behaviors

These behaviors are NOT documented by Anthropic AND we have not
empirically tested them. Each entry says what breaks if the
assumption is wrong.

### `cc-contract:hook-shell-execution`

**Assumption:** Hook `command` strings are executed by `/bin/sh`
(or equivalent POSIX shell). Trailing shell comments like
`# maury-managed` are stripped by the shell and don't affect
execution.

**What breaks if wrong:** ADR-0023 §1's marker-ownership scheme.
The `# maury-managed` sentinel relies on the shell stripping it
cleanly so the script before it executes normally. If hooks run
under a non-shell interpreter, or under a shell that doesn't
strip comments, the marker either errors out or becomes part of
the command argument list.

**How to verify:**
1. Install a hook with `command: "/bin/echo hi # marker-test"`
2. Trigger the hook event in Claude Code
3. Check whether the command executes successfully and the
   `# marker-test` text doesn't appear in any error output

**Fallback if wrong:** Move marker into a parallel `_maury_marked:
true` map within `settings.json` (which itself depends on Claude
Code ignoring unknown JSON keys — itself a separate empirical
question).

**Risk level:** Medium. Marker is load-bearing for ownership and
uninstall, but a fallback exists.

---

### `cc-contract:hook-subprocess-path`

**Assumption:** Hook subprocesses inherit a PATH that is NOT
guaranteed to include `~/.claude/bin/`, so commands must be
absolute paths.

**What breaks if wrong (in either direction):**
- If PATH actually IS reliable and includes `~/.claude/bin/`,
  the absolute-path requirement becomes unnecessary friction
  but isn't broken.
- If we trusted a relative path and PATH actually DOESN'T
  include `~/.claude/bin/`, hooks would fail silently or with
  cryptic "command not found" errors.

**How to verify:**
1. Install a hook with `command: "echo $PATH > /tmp/maury-hook-path-test"`
2. Trigger the hook
3. Inspect `/tmp/maury-hook-path-test`

**Fallback if wrong (lenient case):** If PATH is in fact
reliable, relax the absolute-path requirement in a future ADR.

**Risk level:** Low. Absolute paths are conservative and work
either way; this verification is "can we relax later," not "are
we broken now."

---

### `cc-contract:hook-file-io-permissions`

**Assumption:** Hook subprocesses can freely write to
`~/.claude/maury-state/` via append (`>>`). No sandbox or
permission restrictions interfere.

**What breaks if wrong:** ADR-0017's drift attribution
(`claude-writes.jsonl`), ADR-0025's session tracking
(`active-sessions.jsonl`), and any future maury hook that
records state. Hooks would fire but their writes would
silently fail, making maury's bookkeeping worthless.

**How to verify:**
1. Install a hook with `command: "echo test >> ~/.claude/maury-state/test.txt"`
2. Trigger the hook
3. Check whether the file was created/appended

**Fallback if wrong:** Significantly more complex — would need
to find an alternate write path (maybe `~/.config/maury/`?) and
update three ADRs. No clean fallback.

**Risk level:** **High.** Three ADRs depend on this. Verify
EARLY in the implementation phase.

---

### `cc-contract:project-directory-derivation`

**Assumption:** `~/.claude/projects/<project>/<session-id>.jsonl`'s
`<project>` segment is derived from the working directory path
in some stable, reproducible way (likely a hash, possibly a
slug).

**What breaks if wrong:** Mining (ADR-0005, ADR-0020) walks
this directory tree expecting the structure. If the derivation
isn't stable, mining could miss transcripts or double-count
them across worktrees.

**How to verify:**
1. Run `claude` in directory `/tmp/test-foo`, send a message,
   exit. Note the directory created under `~/.claude/projects/`.
2. Run `claude` in `/tmp/test-foo` worktree (e.g., `git
   worktree add /tmp/test-foo-2`), repeat.
3. Compare directory names — same? different?

**Fallback if wrong:** ADR-0005 needs an addendum on how mining
handles ambiguous cases.

**Risk level:** Medium. Affects mining correctness but not
sync/render safety.

---

### `cc-contract:hook-execution-timing`

**Assumption:** Hooks execute synchronously and complete before
Claude Code proceeds. Failures (non-zero exit) are surfaced.
Timeouts have a default behavior we haven't verified.

**What breaks if wrong:** ADR-0023's `log_tool_use` hook racing
the next tool call, claude-writes.jsonl with out-of-order
events, drift attribution failing.

**How to verify:** Test hooks that sleep and observe whether
Claude Code blocks. Test hooks that fail and observe whether
Claude Code surfaces the error.

**Risk level:** Medium. Drift attribution depends on event
ordering; out-of-order events would still mostly work but
edge cases get weird.

---

### `cc-contract:transcript-jsonl-stability`

**Assumption:** The transcript JSONL line schema (one JSON
object per message, with documented fields) is stable across
Claude Code versions, or at least version transitions are
documented.

**What breaks if wrong:** Mining breaks silently on a CC update.
A user upgrades Claude Code, runs `maury mine`, and gets an
error or worse (silently mis-parsed transcripts).

**Fallback:** Pin maury to a tested CC version range; bump
deliberately.

**Risk level:** Medium-long-term. Won't bite us today; will
absolutely bite us in a year if we don't watch for it.

---

## How to use this file

**For ADR authors:** when an ADR makes a claim about Claude
Code behavior, cite the corresponding `cc-contract:<id>` here
plus the Anthropic URL inline. Example:

```markdown
Per [`cc-contract:event-firing-cadence`][cc-contract], `Stop`
fires once per turn (citing [Claude Code hooks][cc-hooks]).

[cc-contract]: ../claude-code-contract.md#cc-contractevent-firing-cadence
[cc-hooks]: https://code.claude.com/docs/en/hooks
```

**For maintainers when CC updates:** scan this file's `❓` and
`🧪` sections. Re-test anything that might be affected. Move
verified behaviors from `❓` to `🧪` with the version they
were tested under.

**For the periodic verification pass:** every 6 months, re-run
the claude-code-guide verification agent against the documented
section. If a documented behavior has been removed or changed,
update this file AND any ADRs that depend on it.
