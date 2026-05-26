# ADR-0023: Hook installation lifecycle and capability-driven tool resolution

**Status:** Accepted
**Date:** 2026-05-07

## Related tenets

- [Tenet 1 — First, do no harm](../tenets.md#1-first-do-no-harm)
- [Tenet 2 — Consistency within a mode](../tenets.md#2-consistency-within-a-mode-controlled-difference-across-modes)
- [Tenet 8 — Hand-edits are first-class input](../tenets.md#8-hand-edits-are-first-class-input)

## TL;DR

Maury claims its hook entries via a `# maury-managed` shell-comment
marker in the command string; sync re-renders all marked entries and
leaves user-added hooks untouched. Hooks ride the existing render
pipeline (no separate `install` command). Capability probe writes a
`resolved_tools` map (logical name → absolute path) that gets sourced
into hook scripts as `$MAURY_SED` etc., collapsing platform branching
to one layer. Missing tools fail loud at render time — except
`log_tool_use`, which has a no-deps POSIX-shell guarantee because
drift attribution can't gracefully degrade. Trade-off: eight
load-bearing decisions to test together. Three empirical claims
about Claude Code shell behavior were unverified at write time;
verified ✅ on 2026-05-07 via `maury verify-cc-hooks` (see
"Empirical-test debt — RESOLVED" section below). A fourth
load-bearing assumption — that `log_tool_use` completes before
the next tool call — was contradicted empirically on 2026-05-13
via `maury verify-cc-hook-timing`; the amendment below formalizes
drift attribution as **eventually-consistent within
~hook-completion-time** rather than synchronous, with no code
changes required.

## Context and Problem Statement

[ADR-0006](0006-capability-probe-hook-abstraction.md) established
that hooks are written against named **actions** (`notify`,
`log_tool_use`, `run_script`) and that the render engine resolves
those actions to platform-specific commands using each host's
capability probe.

[ADR-0017](0017-drift-detection-and-reconciliation.md) makes the
`PostToolUse log_tool_use` hook **load-bearing**: without it, maury
can't distinguish a Claude-tool write from a hand-edit, and drift
attribution falls apart.

Both ADRs hand-wave over the install lifecycle: when does maury
write the hook into `~/.claude/settings.json`? What happens if the
user already has hand-added hooks? What about uninstall? What about
profile switch? What happens if the host is missing a tool one of
the rendered scripts needs?

ADR-0006 also stops one level too high in its abstraction: it talks
about platform branching at the *hook action* level, but most of
the platform pain in cross-OS scripting is actually at the
*command-line tool* level — `sed` vs `gsed`, `notify-send` vs
`osascript`, `readlink -f` vs `greadlink -f`. Without a layer below
hooks, every shipped script ends up with `if [ "$OS" = "Darwin" ]`
branches.

<details>
<summary><b>Decision drivers</b> (5 items — click to expand)</summary>

- **Tenet 1:** first, do no harm. Coexistence with user-set
  hooks is mandatory — clobbering hand-added hooks is a
  data-loss event.
- **Tenet 2:** consistency within a mode. Hooks should
  install identically on every host through normal sync; no
  host-specific install ceremony.
- **Tenet 8:** hand-edits are first-class input. A hand-
  edited maury hook becomes a drift signal like any other
  edited file, not a special case.
- **Drift attribution depends on `log_tool_use`.** Without
  it, the entire ADR-0017 reconcile flow has nothing to
  distinguish Claude writes from hand-edits.
- **Cross-OS pain is at the *tool* layer**, not the *action*
  layer — `sed`/`gsed`, `notify-send`/`osascript`,
  `readlink -f`/`greadlink -f` need resolution before
  scripts run.

</details>

<details>
<summary><b>Considered options</b> (8 options — click to expand)</summary>

- **Option A:** Replace the entire `hooks` block on render.
- **Option B:** Refuse to render if unfamiliar hooks are
  present.
- **Option C:** Separate `maury hooks install` command.
- **Option D:** PATH-relative script references in hook
  commands.
- **Option E:** Render-time string substitution for tool
  names (`{{sed_gnu}}`).
- **Option F:** File lock on `claude-writes.jsonl`.
- **Option G:** Per-tool sentinel comments inside scripts
  (`# maury:tool=sed_gnu`).
- **Option H (chosen):** Marker-based hook ownership +
  hooks-as-render-output + capability-driven tool resolution
  via `resolved_tools` map + render-time refusal on missing
  tools (with a no-deps exception for `log_tool_use`).

</details>

## Decision Outcome

**Chosen option:** Option H — eight load-bearing decisions,
one composite design. The marker scheme makes coexistence
with user hooks safe; rendering hooks as part of the normal
sync pipeline removes a separate install ceremony;
capability-driven tool resolution collapses platform
branching to one layer; render-time refusal makes missing-
tool failures loud. The `log_tool_use` no-deps exception is
the only graceful-degradation carve-out, justified by
drift-attribution being load-bearing.

### Implementation details

#### 1. Marker-based hook ownership

Maury claims ownership of every hook entry it places by adding a
sentinel comment to the command string itself. Per [Claude Code's
hooks documentation][cc-hooks], the schema groups hook entries by
event name and matcher with three levels of nesting; maury's marker
lives on the inner command string:

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Edit|Write|MultiEdit",
        "hooks": [
          {
            "type": "command",
            "command": "/Users/<user>/.claude/bin/maury-log-tool-use # maury-managed"
          }
        ]
      }
    ]
  }
}
```

The trailing `# maury-managed` is intended to be a shell comment
that runs the maury script and is stripped by the shell. Render
finds maury entries by string-matching the marker; user-added
entries without the marker pass through untouched, including their
`matcher` grouping context.

> **Empirically verified 2026-05-07** (macOS, via `maury verify-cc-hooks`):
> the hook subprocess shell strips the trailing
> `# maury-managed-empirical-probe` comment cleanly, so the marker
> scheme is safe. See
> [`cc-contract:hook-shell-execution`](../claude-code-contract.md#cc-contracthook-shell-execution)
> for the verification record. Re-verify on FreeBSD + Linux when
> first encountered.

On render, maury computes:

```
new_hooks = (existing_hooks_without_marker) + (current_render's_maury_hooks)
```

Hand-editing a maury-marked hook becomes a hand-edit drift like any
other file (per [ADR-0017](0017-drift-detection-and-reconciliation.md)),
not a special case.

#### 2. Hooks are part of the render pipeline

Hooks live in `settings.json`. `settings.json` is already a render
output. Therefore: hooks install on every `maury sync`, and there
is no separate `maury hooks install` command.

This means:

- Adding a hook to the synced base/profile: lands on every host on
  next sync. No bespoke install step.
- Profile switch: `maury profile use work` calls the same render
  pipeline, which writes a new `settings.json` with the work
  profile's hooks. The marker scheme means we cleanly replace
  maury's previous entries without touching user entries.
- Uninstall: `maury uninstall` strips marker-bearing entries from
  `settings.json` and removes maury-installed scripts under
  `~/.claude/bin/maury-*`. User content is untouched.

#### 3. Capability-driven tool resolution

The capability probe today writes a `tools` map keyed by **real
binary names** (`sed`, `gsed`, `awk`, `gawk`, `osascript`, …) — see
`src/maury/capability/probe.py`. We extend that probe with a second
map, `resolved_tools`, keyed by **logical tool names** with values
that are concrete absolute paths derived from the existing `tools`
map:

```json
{
  "tools": {
    "sed":         { "present": true,  "path": "/usr/bin/sed" },
    "gsed":        { "present": true,  "path": "/opt/homebrew/bin/gsed" },
    "gawk":        { "present": true,  "path": "/opt/homebrew/bin/gawk" },
    "greadlink":   { "present": true,  "path": "/opt/homebrew/bin/greadlink" },
    "osascript":   { "present": true,  "path": "/usr/bin/osascript" }
  },
  "resolved_tools": {
    "sed_gnu":     "/opt/homebrew/bin/gsed",
    "awk_gnu":     "/opt/homebrew/bin/gawk",
    "readlink_f":  "/opt/homebrew/bin/greadlink",
    "notify_cli":  "/usr/bin/osascript",
    "log_writer":  "/bin/sh"
  }
}
```

A logical tool with value `null` in `resolved_tools` means no
acceptable implementation was found on this host. The two maps
coexist deliberately: `tools` is the raw "what's installed where"
inventory; `resolved_tools` is the "which of those should we use
for logical operation X" decision. Renaming `tools` would churn
existing probe consumers; adding `resolved_tools` alongside is
non-breaking.

Render generates a per-host wrapper at `~/.claude/bin/maury-tools.sh`:

```sh
# maury-managed — do not hand-edit; regenerated on every sync
export MAURY_SED=/opt/homebrew/bin/gsed
export MAURY_AWK=/opt/homebrew/bin/gawk
export MAURY_READLINK_F=/opt/homebrew/bin/greadlink
export MAURY_NOTIFY=/usr/bin/osascript
```

Maury-shipped scripts source `maury-tools.sh` and use `"$MAURY_SED"`
instead of bare `sed`. User-written scripts in `bin/` can do the
same. Platform branching collapses to one resolution layer.

Logical tool names are a vocabulary maintained in
`base-template/.meta/tools.yaml` so users can extend the catalog
without forking maury.

#### 4. Capability failure = render-time refusal (with one exception)

Per [ADR-0006](0006-capability-probe-hook-abstraction.md): if a
shipped script declares it needs `sed_gnu` and the probe says
`null`, render fails loud with a message like:

```
ERROR: hook 'notify_on_drift' requires logical tool 'sed_gnu' but
this host's capability probe found no acceptable implementation.
Install GNU sed (e.g., `brew install gnu-sed` on macOS, `pkg install
gsed` on FreeBSD) or remove the hook from the host's profile.
```

**Exception: `log_tool_use` has a no-deps guarantee.** It uses only
POSIX shell constructs (`>>` append, `printf`, `sha256sum` or
`shasum`) so it always installs successfully on every supported
host. Without this guarantee, a missing tool on one host would
disable drift attribution everywhere — and drift attribution is the
one feature where graceful degradation is the wrong answer.

#### 5. Absolute paths in `settings.json`

Hook commands in the rendered `settings.json` are absolute paths
resolved at render time per host. We do not embed `$HOME` or `~`
in the command strings.

> **Empirically verified 2026-05-07** (macOS, via `maury verify-cc-hooks`):
> hook subprocess inherits the full system PATH (including the
> launching process's venv bin, Homebrew, system paths). HOME is
> inherited. Absolute paths in `settings.json` remain the right
> conservative choice, but PATH is in fact reliable on this host.
> See [`cc-contract:hook-subprocess-path`](../claude-code-contract.md#cc-contracthook-subprocess-path)
> for the verification record + two adjacent findings (`CLAUDE_HOOK_EVENT`
> is NOT set in env; `CLAUDE_PROJECT_DIR` IS set + symlink-resolved).

Since `settings.json` is already host-specific (rendered from
host-tagged sections per [ADR-0037](0037-layer-taxonomy-and-repo-discovery.md)),
this is not a portability regression — the file is regenerated on
every sync.

#### 6. `log_tool_use` payload schema (locked)

#### How the hook receives data from Claude Code

Per [Claude Code hooks documentation][cc-hooks], hook subprocesses
receive their event payload via **stdin as a single JSON object**,
not via environment variables. For `PostToolUse`, the input JSON
contains:

```json
{
  "session_id": "abc...",
  "hook_event_name": "PostToolUse",
  "tool_name": "Edit",
  "tool_input":  {"file_path": "...", "old_string": "...", ...},
  "tool_result": {"...": "..."}
}
```

The maury `log_tool_use` script reads stdin, extracts the fields
it needs (typically with `jq`), computes derived values (file
SHA before/after, size delta), and appends one JSON line to
`~/.local/state/maury/claude-writes.jsonl`.

#### What gets written to claude-writes.jsonl

Extends the schema from
[ADR-0017](0017-drift-detection-and-reconciliation.md) §"Drift
sources and treatment". 0017 specified `{ts, session_id, tool, path,
diff_hint}` as the goal shape. This ADR locks down the concrete
fields needed to make drift attribution work:

```json
{"ts":"2026-05-07T17:42:11Z","session_id":"abc...","tool":"Edit",
 "path":"CLAUDE.md","before_sha":"sha256:...","after_sha":"sha256:...",
 "size_delta":47,"diff_hint":"L42: 'old' → 'new'"}
```

Field-name mapping from the Claude Code stdin payload:

| Stdin field (from CC) | Logged field (in claude-writes.jsonl) |
|---|---|
| `session_id` | `session_id` (passthrough) |
| `tool_name` | `tool` (renamed for terseness) |
| `tool_input.file_path` | `path` |
| (computed before hook fires) | `before_sha`, `size_delta` |
| (computed after hook fires) | `after_sha` |
| (truncated diff line) | `diff_hint` |

Rationale per logged field:

- `before_sha` / `after_sha` let drift detection cleanly attribute
  "this file's current SHA matches a Claude-write event's
  `after_sha`" without re-reading the file. **The load-bearing pair
  for drift attribution.**
- `size_delta` (signed integer) is for terse human status output.
- `diff_hint` (preserved from ADR-0017) is a short human-readable
  preview — typically `"L<line>: <truncated old> → <truncated new>"`
  — for `maury status` so the user can recognize the change at a
  glance without `git show`.

ADR-0017's "diff_hint" goal is satisfied by the field with that
name retained verbatim. The `before_sha` / `after_sha` / `size_delta`
fields are additive — they enable the SHA-equality attribution
that ADR-0017 §"Drift sources and treatment" implies but never
specified the mechanism for.

**Caveat:** lines must be ≤ 4 KB to ensure POSIX `O_APPEND` atomic-
write guarantees on collision (multiple Claude Code sessions writing
simultaneously, which is explicitly supported per
[CC sessions documentation][cc-sessions]). `diff_hint` must be
truncated to keep this bound; the convention is "≤ 200 bytes." Any
path that exceeds 4 KB is its own pathology we don't try to handle.

#### 7. Concurrent sessions

Multiple Claude Code sessions appending to `claude-writes.jsonl` is
expected. POSIX guarantees `O_APPEND` writes ≤ `PIPE_BUF` (4 KB on
Linux/macOS) are atomic. Per the schema in §6, every line stays
under that bound. No file lock is needed.

#### 8. Uninstall command

`maury uninstall` does:

1. Strip every `# maury-managed`-marked entry from `settings.json`'s
   `hooks` block. Leave non-marked entries untouched.
2. Delete `~/.claude/bin/maury-*` and `~/.claude/bin/maury-tools.sh`.
3. Delete `~/.local/state/maury/`.
4. Print "removed maury hooks; left N user hooks intact; clones
   under <repos_root> were not touched (delete them manually if
   desired)".
5. Exit 0.

The clones aren't auto-deleted because the user may still want
their git history.

### Consequences

- ✅ **Good:** Hooks ride the render pipeline. No second
  code path; tested alongside everything else `maury sync`
  does.
- ✅ **Good:** Coexistence with hand-set hooks is safe. The
  marker scheme means a user with their own `PostToolUse`
  linter hook keeps it.
- ✅ **Good:** Profile switch is just a re-render. No
  special teardown logic to test or maintain.
- ✅ **Good:** Tool resolution is one layer. Adding FreeBSD
  support means adding probe entries, not editing every
  script. Same for any future BusyBox / Alpine / WSL host.
- ✅ **Good:** Uninstall is real. A user can revert the
  host to "no maury here" with one command. This matters
  for adoption — irreversible setup is a barrier.
- ⚖️ **Neutral:** `log_tool_use` is the one hook with a
  no-deps contract. This is the price of making drift
  attribution reliable everywhere. Other hooks fail loud
  per [ADR-0006](0006-capability-probe-hook-abstraction.md)
  if their tools are missing.
- ❌ **Bad:** The `tools` vocabulary becomes a versioned
  schema. Adding `tools.yaml` entries is a
  manifest-schema-version event (per
  [ADR-0015](0015-surrogate-keys-for-hosts-and-profiles.md)
  v2/v3 convention).

### Confirmation

- Hook installation is part of `settings.json` rendering;
  no separate `maury hooks install` command exists.
- Marker scheme uses the `# maury-managed` comment;
  empirical-test debt below tracks the unverified shell-
  comment behavior.
- `resolved_tools` map ships in `capabilities.json`
  alongside the existing `tools` inventory.
- `claude-writes.jsonl` lines stay ≤ 4 KB to satisfy
  POSIX `O_APPEND` atomicity (per the schema).

<details>
<summary><b>Pros and cons of the options</b> (per-option ✅/❌ — click to expand)</summary>

#### Option A: Replace entire `hooks` block on render

- ✅ **Good:** Trivial implementation.
- ❌ **Bad:** Destroys user hand-set hooks. Tenet 1 violation.

#### Option B: Refuse to render if unfamiliar hooks present

- ✅ **Good:** Forces explicit acknowledgement.
- ❌ **Bad:** Too aggressive — users have legitimate
  reasons to hand-add hooks for one-off debugging or for
  tools maury doesn't know about.

#### Option C: Separate `maury hooks install` command

- ✅ **Good:** Explicit lifecycle event.
- ❌ **Bad:** Ceremony for no benefit; sync already does
  this work.

#### Option D: PATH-relative script references

- ✅ **Good:** Less verbose hook commands.
- ❌ **Bad:** Claude Code's hook subprocess PATH is not
  guaranteed to include `~/.claude/bin` (empirical claim;
  see empirical-test debt below).

#### Option E: Render-time `{{sed_gnu}}` substitution

- ✅ **Good:** Could template hook commands at render.
- ❌ **Bad:** Forces every script through the maury
  renderer; the env-var approach lets user-written scripts
  in `bin/` use the same resolution by sourcing
  `maury-tools.sh`.

#### Option F: File lock on `claude-writes.jsonl`

- ✅ **Good:** Explicit concurrency control.
- ❌ **Bad:** POSIX `O_APPEND` + ≤4 KB lines is sufficient
  and lock-free. Locks would also be fragile across
  Claude Code processes that don't know about each other.

#### Option G: Per-tool sentinel comments inside scripts

- ✅ **Good:** Resolution metadata travels with the script.
- ❌ **Bad:** Scripts would need to be parsed by the
  resolver before each run; env-var resolution is cheaper
  and the standard Unix idiom.

#### Option H (chosen): Marker + render-pipeline + resolved_tools

- ✅ **Good:** Coexistence safe; sync-rendered; tool
  resolution centralized; uninstall real.
- ❌ **Bad:** Eight load-bearing decisions to test
  together; tools.yaml versioning becomes a manifest
  schema event.

</details>

## Build-order placement

- **Marker scheme + sync-rendered hooks** land in Phase 3
  (render engine extension; settings.json renderer already exists).
- **Tool resolution** extends the existing capability probe (already
  shipped) with a new `resolved_tools` map alongside the existing
  `tools` inventory. Probe gains a logical-name resolution pass that
  reads `tools.yaml` and writes `resolved_tools` into the host's
  `capabilities.json`. Plus the `maury-tools.sh` generator at render
  time. New work in this ADR — not free off the existing probe.
- **`log_tool_use` script + payload schema** lands as part of
  Phase 5.x (drift detection — currently in progress) since drift
  detection is the consumer.
- **`maury uninstall`** is a small Phase 4-adjacent task; can land
  any time after the marker scheme is in.

## Followups

- **`tools.yaml` initial vocabulary.** Seed with: `sed_gnu`,
  `awk_gnu`, `readlink_f`, `find_gnu`, `notify_cli`, `clipboard_cli`,
  `sha256_cli`. Extend as scripts demand.
- **Per-tool fallback chains.** A script could declare it'd take
  `sed_gnu` OR `sed_bsd_with_dash_i_workaround`. Not in v1 — wait
  for a real script that needs it.
- **A `maury hooks doctor` command** (or extension of `maury doctor`)
  to validate that every installed hook's commands exist + every
  `MAURY_*` env var resolves to a real binary on this host.

## Empirical-test debt — RESOLVED 2026-05-07

The three load-bearing claims that originally lived here have all
been verified empirically by `maury verify-cc-hooks` (the
`src/maury/empirical_tests.py` harness):

1. ✅ **Shell + comment stripping** (§1) — verified. Trailing
   `# maury-managed` comment is stripped cleanly by the hook
   subprocess shell. Marker scheme is safe.
2. ✅ **Hook subprocess PATH + env** (§5) — verified. PATH and HOME
   inherited. PWD = the directory passed as `cwd` to `claude`.
   Two new findings extracted: `CLAUDE_HOOK_EVENT` is NOT set in
   the env (read event name from stdin JSON, not env), and
   `CLAUDE_PROJECT_DIR` IS set (symlink-resolved to realpath).
3. ✅ **Hook file-I/O permissions** (§6, §7) — verified. Hook
   subprocess can `mkdir -p` + write under arbitrary paths;
   no sandbox or permission interference observed.

Verification was on macOS. Re-run `maury verify-cc-hooks` on
FreeBSD + Linux when those hosts first come online, and on
managed/MDM-restricted macOS hosts when first encountered (TCC +
MDM combinations may introduce permission boundaries this
verification didn't see).

Full records in
[`docs/claude-code-contract.md` §"Empirically verified behaviors"](../claude-code-contract.md#empirically-verified-behaviors).

## Hook timing model: eventually-consistent attribution (amended 2026-05-14)

A fourth empirical claim — not flagged in the original
"Empirical-test debt" because it was implicit rather than
declared — was contradicted by `maury verify-cc-hook-timing`
on 2026-05-13. The schema in §6 and the concurrency analysis
in §7 originally assumed — without saying so — that
`PostToolUse log_tool_use` completes before Claude Code
proceeds to the next tool call or surfaces the rendered effect
to the user. **That assumption does not hold.** Per
[`cc-contract:hook-execution-timing`](../claude-code-contract.md#cc-contracthook-execution-timing)
(verified 2026-05-13 on claude 2.1.141 / macOS 14.5),
Claude Code's hook execution model is **ordered fire-and-forget**:
hooks fire in declaration order but Claude Code does NOT wait for
hook N to finish before firing hook N+1, and exit code 2 does NOT
short-circuit subsequent hooks. **Neither** of the two documented
readings — the
[Agent SDK hooks reference][cc-agent-hooks] ("parallel") nor the
[hooks guide][cc-hooks] ("sequential, short-circuit on exit 2") —
matches this empirical reading; the contradiction is tracked
upstream in [#57800](https://github.com/anthropics/claude-code/issues/57800).

**Implication for drift attribution.** A `claude-writes.jsonl` line
for tool call T may not be present on disk at the instant tool call
T+1 begins. The write is *eventually* there — typically within a
few hundred milliseconds, bounded by the hook subprocess's wall
time — but a synchronous read immediately after the tool call can
miss it.

**Why this is fine for maury's primary consumer.** `maury sync`,
`maury reconcile`, and `maury status` all run as between-sessions
operations invoked by the user from a shell. By the time any of
them open `claude-writes.jsonl` for read, every hook subprocess
from the prior session has long since exited (the session ended
before the user's next shell command). The race window is bounded
by hook-completion-time and does not overlap with maury's read
points.

**What this changes:**

- No code changes. The `log_tool_use` script's append semantics
  (POSIX `O_APPEND`, ≤ 4 KB lines) and the consumer-side read
  semantics (open + parse) are already correct under the
  eventually-consistent model.
- No schema changes. The fields locked in §6 (`before_sha`,
  `after_sha`, `size_delta`, `diff_hint`) remain load-bearing for
  attribution; the SHA-equality test still uniquely identifies
  a Claude-write irrespective of write ordering.
- Drift attribution is now formally **eventually-consistent within
  ~hook-completion-time** rather than synchronous. Downstream
  consumers MUST NOT read `claude-writes.jsonl` mid-session and
  assume it reflects every tool call up to "now"; cross-session
  reads (maury's normal path) are safe.

**Why not switch to a different observation mechanism.** A `Stop`-
hook drain at session-end was considered. It would give synchronous
attribution at the cost of losing intra-session visibility. Maury's
primary consumers don't need intra-session visibility, so the price
isn't worth paying. Pinning to a specific CC version is a
backwards-compatibility trap; waiting for upstream blocks all
dependent design indefinitely.

The framing is the standard distributed-systems tradeoff: of
{speed, consistency, fault-tolerance} you pick two. Hook
execution is already optimizing for speed and fault-tolerance
(parallelism + no inter-hook coupling); insisting on synchronous
consistency would require giving up one of those, which is
Anthropic's decision to make in upstream
[#57800](https://github.com/anthropics/claude-code/issues/57800),
not maury's to enforce downstream.

## Claude Code references

Verified-as-of 2026-05-07 (cc-hooks, cc-sessions) and 2026-05-13
(cc-agent-hooks, added with the eventually-consistent attribution
amendment) against Anthropic's official Claude Code documentation:

- [`cc-hooks`][cc-hooks] — hook event names, JSON shape (event →
  matcher group → `{type, command}`), stdin payload structure
  (`session_id`, `hook_event_name`, `tool_name`, `tool_input`,
  `tool_result`).
- [`cc-sessions`][cc-sessions] — multiple concurrent Claude Code
  sessions on one host are explicitly supported.
- [`cc-agent-hooks`][cc-agent-hooks] — Agent SDK TypeScript
  reference; cited in the eventually-consistent attribution
  amendment for the "parallel" documentation reading that, with
  the hooks guide's contradictory "sequential, short-circuit on
  exit 2" reading, is the upstream contradiction
  ([#57800](https://github.com/anthropics/claude-code/issues/57800))
  that `maury verify-cc-hook-timing` resolves empirically.

[cc-hooks]: https://code.claude.com/docs/en/hooks
[cc-sessions]: https://code.claude.com/docs/en/sessions
[cc-agent-hooks]: https://docs.claude.com/en/api/agent-sdk/typescript

## Amendment history

- 2026-05-11 — "profile" vocabulary renamed to "mode" per ADR-0037 doctoral examination. References to "profile" in this ADR now read "mode"; no semantic changes.
- 2026-05-14 — added "Hook timing model: eventually-consistent attribution" section per `maury verify-cc-hook-timing` empirical findings (`cc-contract:hook-execution-timing`). §6/§7's implicit synchronicity assumption was wrong; the amendment formalizes the eventually-consistent model. No code changes required because `maury sync` reads `claude-writes.jsonl` between sessions, not mid-session.
