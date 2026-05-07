# ADR-0006: Capability probe + hook abstraction for cross-OS portability

**Status:** Accepted
**Date:** 2026-05-06

## Related tenets

- [Tenet 10 — Modularity over hardcoding](../tenets.md#10-modularity-over-hardcoding)

## Context and Problem Statement

Per [Claude Code's hooks documentation][cc-hooks], hooks are shell
commands that Claude Code runs at named events (`PreToolUse`,
`PostToolUse`, `SessionStart`, `SessionEnd`, `Stop`,
`UserPromptSubmit`, etc.). Hook configuration lives in the
[`settings.json` file][cc-settings] alongside other Claude Code
settings. The same hook config (rendered via maury's render engine
into each host's `~/.claude/settings.json`) must work on:

- **workstation / personal-laptop / work-laptop (macOS).** GNU tools prefixed
  with `g` (gsed, gfind), Homebrew under `/opt/homebrew/bin` on Apple
  Silicon. `osascript` available unless MDM blocks AppleScript.
- **linux-server (Ubuntu 25.10).** Native GNU userland. `notify-send` for
  desktop notifications. No `osascript`.
- **firewall (FreeBSD 14.2).** BSD userland (`sed -i ''` requires backup
  arg, `find` lacks `-printf`). No GUI, no `notify-send`, no
  `osascript`. `pkg` for installs. Operators may not have direct
  sudo access on hosts in this class — scripts that need
  privilege are written for manual execution.
- **work-laptop with MDM/EDR.** May block AppleScript notifications,
  may TCC-restrict directories, may require specific commit signing.

A raw-shell hook config that works on workstation will silently no-op or
actively error on at least three of these. Synced config that breaks
hooks defeats the value of sync.

## Decision Drivers

- **Tenet 10:** modularity over hardcoding — hook definitions
  should not bake in one OS's command syntax.
- **Tenet 1:** first, do no harm — a hook that errors loudly on
  the wrong host is bad, but a hook that *silently no-ops* is
  worse (the user thinks Claude Code is doing something it
  isn't).
- **Cross-OS portability is a v1 requirement:** maury must work
  on macOS, Linux, and FreeBSD on day one.
- **Render-time validation must be possible:** the render engine
  needs enough information to refuse to install hooks whose
  required capabilities are absent.

## Considered Options

- **Option A:** Raw shell + per-host hand-edits.
- **Option B:** Single monolithic shell wrapper that does
  everything internally.
- **Option C:** Conditional templating inside settings.json
  itself.
- **Option D (chosen):** Capability probe + named-action
  abstraction; render engine resolves actions per host.

## Decision Outcome

**Chosen option:** Option D — two layers:

1. **Capability probe.** Each host runs a probe that records what's
   actually present (tools + paths + flavors, GUI, notification
   mechanisms, shell, MDM presence, security posture) into
   `<repo>/profiles/<profile>/hosts/<host>/capabilities.json`. Probe
   runs on every sync.
2. **Hook abstraction.** Hooks in the synced repo are written against
   named **actions** (`notify`, `log_jsonl`, `run_script`,
   `append_session_state`), not raw shell. The render engine resolves
   each action to a host-specific command using the capability data,
   with declared `on_unavailable` fallbacks (`log` if the GUI
   notification path is missing, etc.).

This is the only option that lets one canonical hook definition
work correctly across BSD/Linux/macOS without per-host hand-
editing.

### Implementation details

For cases where raw shell logic is genuinely needed, POSIX-sh wrapper
scripts under `base-template/bin/` (rendered to `~/.claude/bin/`,
prepended to hook PATH) handle platform branching internally. Examples:
`claude-notify`, `claude-readlink-f`, `claude-clipboard`.

`render --check` validation refuses to install hooks whose required
capabilities are missing on the current host, with a clear report.

Capability detection of MDM on macOS uses `profiles list`, presence
of `/Library/Application Support/JamfPro/`, etc. Hosts with
`security_posture: managed` and a `blocked_capabilities` list get
those capabilities treated as hard exclusions during render.

The tool itself never escalates privilege; on hosts marked
`privileged_writes: manual` (firewall), it writes proposed scripts
under `~/.claude/proposed/` for the user to run.

### Consequences

- ✅ **Good:** One hook definition, all OSes — sync is meaningful
  for hooks, not just for static text.
- ✅ **Good:** Render-time validation catches missing
  capabilities before hooks land in `~/.claude/settings.json`,
  preventing silent no-ops.
- ⚖️ **Neutral:** Action vocabulary starts small and grows
  deliberately: `notify`, `log_jsonl`, `run_script`,
  `append_session_state`. New actions added only when a hook
  can't be expressed via existing primitives.
- ⚖️ **Neutral:** Wrapper scripts are POSIX-sh (not bash) for
  FreeBSD compatibility with `/bin/sh`.
- ❌ **Bad:** Adds an abstraction layer that hook authors must
  learn (named actions vs. raw shell).
- ❌ **Bad:** New OS-specific behavior requires touching the
  resolver, not just dropping in a shell snippet.

### Confirmation

- `src/maury/capability/` implements the per-OS probe and
  produces `capabilities.json`.
- `src/maury/render/hooks.py` resolves named actions to
  per-host commands.
- `maury render --check` (per `docs/operations.md`) refuses to
  apply hook configs whose required capabilities are absent.

## Pros and Cons of the Options

### Option A: Raw shell + per-host hand-edits

- ✅ **Good:** Maximum flexibility per host.
- ❌ **Bad:** Defeats sync — every host needs hand-tweaking.
- ❌ **Bad:** Drift is silent; "I forgot to update the work
  laptop's hook" is invisible until something breaks.

### Option B: Single monolithic shell wrapper

- ✅ **Good:** All branching logic in one place.
- ❌ **Bad:** A giant POSIX-sh switch statement that does
  everything is harder to maintain than a small typed Python
  resolver with declared capabilities.
- ⚖️ **Neutral:** The action-form abstraction is a thin
  Python layer on top — strictly better separation of concerns
  than the monolith approach.

### Option C: Conditional templating inside settings.json

- ✅ **Good:** Keeps everything declarative in settings.json.
- ❌ **Bad:** `settings.json` has no template engine; we'd be
  inventing one.
- ❌ **Bad:** Claude Code's settings parser would have to
  ignore our template syntax — fragile coupling to upstream
  schema changes.

### Option D (chosen): Capability probe + named actions

- ✅ **Good:** One hook definition works across all OSes; sync
  is meaningful for hooks.
- ✅ **Good:** Render-time validation prevents silent no-ops.
- ✅ **Good:** Capability data is structured and reusable
  (mining-host detection, doctor checks, etc.).
- ❌ **Bad:** Abstraction layer to learn; new OS support
  requires resolver changes, not just shell snippets.

## Build-order placement

Phase 2 (Manifest + capability probe) — the per-host probe and
`capabilities.json` ship in Phase 2. Phase 3 (Render engine)
adds the action-resolution layer. Phase 4 (Bootstrap) wires
the probe into the bootstrap flow.

## Followups

- **Action vocabulary growth** — start with `notify`,
  `log_jsonl`, `run_script`, `append_session_state`. Add new
  actions only when a hook can't be expressed via existing
  primitives.
- **Porting to new OSes** — the porting primer (planned at
  `docs/porting-to-new-os.md`) will document how to add a new
  OS to the probe and resolver.

## Claude Code references

Verified-as-of 2026-05-07 against Anthropic's official Claude
Code documentation:

- [`cc-hooks`][cc-hooks] — hook events, schema, when hooks fire.
- [`cc-settings`][cc-settings] — settings.json layout and
  precedence (project vs user vs system levels).

[cc-hooks]: https://code.claude.com/docs/en/hooks
[cc-settings]: https://code.claude.com/docs/en/settings
