# ADR-0006: Capability probe + hook abstraction for cross-OS portability

**Status:** Accepted
**Date:** 2026-05-06

## Related tenets

- [Tenet 10 — Modularity over hardcoding](../tenets.md#10-modularity-over-hardcoding)

## Context

Hooks are shell commands that Claude Code runs on tool events. The same
hook config must work on:

- **workstation / personal-laptop / work-laptop (macOS).** GNU tools prefixed
  with `g` (gsed, gfind), Homebrew under `/opt/homebrew/bin` on Apple
  Silicon. `osascript` available unless MDM blocks AppleScript.
- **linux-server (Ubuntu 25.10).** Native GNU userland. `notify-send` for
  desktop notifications. No `osascript`.
- **firewall (FreeBSD 14.2).** BSD userland (`sed -i ''` requires backup
  arg, `find` lacks `-printf`). No GUI, no `notify-send`, no
  `osascript`. `pkg` for installs. the project owner cannot sudo directly here —
  scripts that need privilege are written and run manually.
- **work-laptop with MDM/EDR.** May block AppleScript notifications,
  may TCC-restrict directories, may require specific commit signing.

A raw-shell hook config that works on workstation will silently no-op or
actively error on at least three of these. Synced config that breaks
hooks defeats the value of sync.

## Decision

Two layers:

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

For cases where raw shell logic is genuinely needed, POSIX-sh wrapper
scripts under `base-template/bin/` (rendered to `~/.claude/bin/`,
prepended to hook PATH) handle platform branching internally. Examples:
`claude-notify`, `claude-readlink-f`, `claude-clipboard`.

`render --check` validation refuses to install hooks whose required
capabilities are missing on the current host, with a clear report.

## Consequences

- Action vocabulary starts small and grows deliberately: `notify`,
  `log_jsonl`, `run_script`, `append_session_state`. New actions added
  only when a hook can't be expressed via existing primitives.
- Wrapper scripts are POSIX-sh (not bash) for FreeBSD compatibility
  with `/bin/sh`.
- Capability detection of MDM on macOS uses `profiles list`, presence
  of `/Library/Application Support/JamfPro/` etc.
- The tool itself never escalates privilege; on hosts marked
  `privileged_writes: manual` (firewall), it writes proposed scripts under
  `~/.claude/proposed/` for the user to run.
- Hosts with `security_posture: managed` and `blocked_capabilities`
  list get those capabilities treated as hard exclusions during render.

## Alternatives considered

- **Raw shell + per-host hand-edits.** Rejected: defeats sync.
- **Single shell wrapper that does everything.** Considered. The
  action-form abstraction is a thin layer on top; rejecting raw shell
  entirely is cleaner than maintaining a giant Python-shells-out shim.
- **Conditional templating in settings.json.** Rejected: settings.json
  has no template engine; we'd be inventing one.
