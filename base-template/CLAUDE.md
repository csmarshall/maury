# Universal CLAUDE.md (managed by maury)

> Rendered from `<base>/CLAUDE.md` + mode fragments + host-tagged sections.
> Do not hand-edit through `~/.claude/CLAUDE.md`; use `maury reconcile`
> to capture changes via the proposal pipeline.

> This file is a **seed** — copy it into your `maury-base.git` repo and
> edit to match your conventions. The content below is illustrative;
> nothing here is mandatory.

## Code style

- Self-documenting names; comments only for non-obvious logic.
- Type hints + docstrings for all functions in Python.
- Preserve existing comments when refactoring.

## Communication

- Concise, technically precise. Direct answers first; context after.
- No confirmation requests for obvious steps.
- Flag edge cases and gotchas proactively.

## Shell scripts

- Always include `unset TMOUT` near the top.
- Use `set -euo pipefail` where appropriate.
- Meaningful exit codes and error messages.
- Include usage/help for scripts with arguments.

## Logging

- Timestamp + level + context on every significant operation.
- Levels: DEBUG (steps), INFO (major ops), ERROR (failures + state).
- No sensitive data in logs.
- Configurable via env var.
