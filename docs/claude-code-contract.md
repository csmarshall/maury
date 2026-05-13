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

**Orthogonal per-entry field — 📌 `Tracked upstream:`.** Many
behaviors maury depends on are also discussed in open issues on
[`anthropics/claude-code`](https://github.com/anthropics/claude-code/issues).
An entry in any tier may carry a `Tracked upstream:` field listing
the relevant issue(s). This is independent of evidence strength —
an entry can be empirically verified by us AND tracked upstream
(strongest signal: we have a working reproduction *and* Anthropic
knows). When Anthropic resolves an upstream issue by documenting
the behavior, the entry moves to ✅; when they fix the behavior
itself, the entry's claim updates and we re-verify.

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

The entries here were promoted from "❓ Assumed but unverified"
after running maury's empirical-test harness on a real macOS host
with Claude Code installed. The harness source lives at
[`src/maury/empirical_tests.py`](../src/maury/empirical_tests.py);
re-verification commands are documented in
[`docs/status.md`](status.md). Every verifier runs in isolation
(no risk to the user's real `~/.claude/` content, though
`verify-cc-projects-dir` does create and clean up its own test
buckets under `~/.claude/projects/`).

| Verifier command | Promotes | Verified |
|---|---|---|
| `maury verify-cc-hooks` | `cc-contract:hook-shell-execution`, `cc-contract:hook-subprocess-path`, `cc-contract:hook-file-io-permissions` | 2026-05-07 (claude 2.1.x, macOS) |
| `maury verify-cc-projects-dir` | `cc-contract:project-directory-derivation` | 2026-05-13 (claude 2.1.140, macOS) |

### `cc-contract:hook-shell-execution`

**Verified 2026-05-07** (macOS, Claude Code per `claude --version`
in CI; re-verify on FreeBSD + Linux hosts when first available).

**Behavior observed:** Hook `command` strings are executed by a
POSIX shell. Trailing `# maury-managed-empirical-probe` comment
was stripped cleanly; the script preceding it executed and wrote
its expected output.

**Implication:** ADR-0023 §1's `# maury-managed` marker scheme
is safe. Marker-based ownership / uninstall path holds.

**Re-verification:** `maury verify-cc-hooks` re-runs the probe
end-to-end. Re-run after any Claude Code minor-version bump
that touches hooks.

---

### `cc-contract:hook-subprocess-path`

**Verified 2026-05-07** (macOS).

**Behavior observed:** Hook subprocess inherits PATH from the
process that invoked `claude` — the full system PATH including
the user's venv bin (when launched via `uv run`), Homebrew,
system paths, etc. HOME inherited. PWD = the directory passed
as `cwd` to the `claude` subprocess.

**Two new findings worth recording:**

- **`CLAUDE_HOOK_EVENT` is NOT set in the hook subprocess env.**
  Hooks that need to know which event fired must read it from
  the stdin JSON payload (the documented `hook_event_name` field
  per `cc-contract:hooks-stdin-payload`), not from an env var.
  This contradicts a tempting but wrong "just check `$CLAUDE_HOOK_EVENT`"
  shortcut.
- **`CLAUDE_PROJECT_DIR` IS set** to the resolved (symlink-followed)
  realpath of the project directory. On macOS this means
  `/var/...` → `/private/var/...` resolution happens automatically.
  Hooks comparing CLAUDE_PROJECT_DIR to other paths must
  realpath their comparand or the equality check will fail.

**Implication for ADR-0023 §5:** absolute paths in
`settings.json` are still the right call (it's a "works in
both PATH worlds" choice), but PATH is in fact reliable on
this host. The absolute-path requirement could be relaxed in
a future ADR if portability across hosts is shown to be the
same — but it costs nothing to keep.

**Implication for hook authors:** read `hook_event_name` from
stdin JSON, not from env. Realpath any path you compare to
`CLAUDE_PROJECT_DIR`.

---

### `cc-contract:project-directory-derivation`

**Verified 2026-05-13** (claude 2.1.140, macOS 14.5).

**Behavior observed:** Claude Code derives the directory under
`~/.claude/projects/<X>/` from the cwd at launch via a three-step
algorithm:

1. **Resolve symlinks** on the cwd (`realpath` / `Path.resolve`).
   On macOS this means `/var/foo` → `/private/var/foo` before any
   substitution.
2. **Iterate the resolved path as UTF-16 code units** (matching
   the JS regex semantics — the CLI is Node).
3. **Per code unit:** keep iff it matches `[A-Za-z0-9]`; else
   substitute `-`. No collapse of consecutive substitutions.

Non-BMP codepoints (e.g., 🚀 U+1F680) are encoded as UTF-16
surrogate pairs; neither surrogate is alphanumeric, so each
non-BMP codepoint produces **two** hyphens, not one. This is
the JS-runtime fingerprint and the encoded predictor matches
empirically.

**The algorithm is non-injective.** Distinct cwds collide:
`/a/b/c` and `/a-b-c` both produce `-a-b-c`. The verifier
exercises this directly via a declared collision-group pair and
asserts they bucket together.

**Implication for ADR-0005, ADR-0020:** mining must not assume
project-dir uniquely identifies cwd. A cwd-keyed view computed
from project-dir alone is lossy. (Per-session JSONL records
carry `cwd` directly; that field is the authoritative cwd.)

**Re-verification:** `maury verify-cc-projects-dir` re-runs the
14-case corpus and asserts every prediction matches observation.
Re-run after any Claude Code minor-version bump.

**📌 Tracked upstream:**
- [anthropics/claude-code #54865](https://github.com/anthropics/claude-code/issues/54865)
  — *open*. Quotes `fh()` from cli.js source; documents the
  same non-injectivity from a Windows path-form angle.
- [anthropics/claude-code #46522](https://github.com/anthropics/claude-code/issues/46522)
  — *open*. `/resume` hides sessions after project dir rename/move.
- [anthropics/claude-code #57920](https://github.com/anthropics/claude-code/issues/57920)
  — *open*. `--resume <id>` can't find transcript after worktree
  path removed.

---

### `cc-contract:hook-file-io-permissions`

**Verified 2026-05-07** (macOS).

**Behavior observed:** Hook subprocess can `mkdir -p` a directory
that didn't exist and write a file inside it, with no sandbox
or permission restrictions. The HIGH-risk concern about TCC or
similar interfering with `~/.claude/maury-state/` writes did
not materialize.

**Implication:** ADR-0017's `claude-writes.jsonl`, ADR-0025's
`active-sessions.jsonl`, and any future maury hook that
records state under `~/.claude/maury-state/` can rely on this.
The HIGH risk level on this item is **closed**.

**Re-verification:** `maury verify-cc-hooks`. Worth re-running
on managed/MDM-restricted hosts when first encountered, since
TCC + MDM combinations can introduce permission boundaries
this verification didn't see.

---

## ❓ Assumed but unverified behaviors

These behaviors are NOT documented by Anthropic AND we have not
empirically tested them. Each entry says what breaks if the
assumption is wrong.



### `cc-contract:hook-execution-timing`

**Assumption:** Hooks execute synchronously and complete before
Claude Code proceeds. Failures (non-zero exit) are surfaced.
Timeouts have a default behavior we haven't verified. **Multiple
hooks registered on the same event fire in declaration order.**

**What breaks if wrong:** ADR-0023's `log_tool_use` hook racing
the next tool call, claude-writes.jsonl with out-of-order
events, drift attribution failing.

**📌 Tracked upstream:**
- [anthropics/claude-code #57800](https://github.com/anthropics/claude-code/issues/57800)
  — *open*. Documentation contradiction: multi-hook firing
  order is documented as "parallel" in the Agent SDK hooks
  reference but "sequential with short-circuit on exit code 2"
  in the hooks guide. This directly impacts ADR-0023's
  drift-attribution chain, which depends on deterministic
  ordering. Resolution of the contradiction (either way)
  would tell us whether the current design works as written.
- [anthropics/claude-code #23747](https://github.com/anthropics/claude-code/issues/23747)
  — *closed (duplicate)*. SessionStart hooks hang indefinitely
  on Windows even with `timeout: 5` configured. Confirms
  timeout-handling is fragile in at least one platform path.
- [anthropics/claude-code #50160](https://github.com/anthropics/claude-code/issues/50160)
  — *closed (duplicate of #23747)*. Hook entries without a
  `timeout` field block the SDK/CLI forever when the command
  doesn't exit. Confirms there is no useful built-in default
  timeout for hooks.
- [anthropics/claude-code #37135](https://github.com/anthropics/claude-code/issues/37135)
  — *open*. Stop hooks can hang indefinitely on large JSON
  block responses since 2.1.78.
- [anthropics/claude-code #38162](https://github.com/anthropics/claude-code/issues/38162)
  — *closed*. Async hooks receive empty stdin on macOS but
  work on Linux — platform-specific hook semantics.

**Maury contribution:** add a maury-use-case voice to #57800
(the ordering contradiction is the most load-bearing of the
above for ADR-0023) rather than filing a new duplicate.

**How to verify:** Test hooks that sleep and observe whether
Claude Code blocks. Test hooks that fail and observe whether
Claude Code surfaces the error. Two hooks on the same event —
do they run in declared order or in parallel?

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

**📌 Tracked upstream:**
- [anthropics/claude-code #53516](https://github.com/anthropics/claude-code/issues/53516)
  — *open feature request*. Stable, documented schema for
  `~/.claude/projects/<encoded-cwd>/<uuid>.jsonl` line types,
  filed by the `aims.dashboard` VS Code extension team (whose
  use case overlaps maury's: file-watch transcripts they
  didn't spawn). Resolution would promote this entry to ✅.
- [anthropics/claude-code #49400](https://github.com/anthropics/claude-code/issues/49400)
  — *open docs request*. Publish the JSONL session schema —
  the same ask from a different angle.

**Maury contribution:** add a maury-use-case voice to the
existing issues (#53516 + #49400) rather than filing a third
duplicate. More downstream voices = higher upstream priority.

**Fallback:** Pin maury to a tested CC version range; bump
deliberately. Long-term: ship a maury-side JSON Schema lock
+ strict parser so a silent CC schema drift fails loud
instead of silently mis-parsing.

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
