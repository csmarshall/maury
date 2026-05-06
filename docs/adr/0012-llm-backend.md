# ADR-0012: LLM backend — `claude -p` by default, SDK opt-in

**Status:** Accepted
**Date:** 2026-05-06

## Context

Several maury components require LLM calls:

- **Mining** (Phase 6) extracts candidate fragments from transcripts.
- **Rule synthesis** (Phase 8) proposes new YAML rules from user reclassifications.
- Other future features (e.g., promotion-review summarization, doctor's
  semantic critique) may also reach for an LLM.

Two ways to make those calls:

| Backend | Auth | Quota |
|---|---|---|
| Anthropic Python SDK | `ANTHROPIC_API_KEY` | Pay-per-token API budget |
| `claude -p "prompt"` (Claude Code headless mode) | Existing Claude Code auth | User's Claude Code subscription |

The SDK gives full control (prompt caching, streaming, batch, model
choice). Headless `claude -p` uses the user's existing subscription —
no separate key, no separate billing — and was designed exactly for the
"pipelines, scripts, hooks" use case.

For a personal-scale tool that the user is operating from machines that
already have Claude Code installed and authenticated, defaulting to a
separate API key feels backwards.

## Decision

**Default LLM backend is `claude -p`. SDK is opt-in per-host or per-call.**

- Configured via `host.llm_backend: "cli" | "sdk"` in the manifest, or
  `MAURY_LLM_BACKEND` environment variable.
- Default: `cli`.
- The miner emits a single internal `LLMClient` interface; both backends
  implement it. Mining/synthesis call the interface; the manifest picks
  the implementation at runtime.
- `claude -p --output-format stream-json` is the wire format for the CLI
  backend; `Messages` API with prompt caching is the wire format for the
  SDK backend.

## Consequences

- Maury runs out-of-the-box on any host that has `claude` installed and
  authenticated. No API key required.
- The user's mining and rule-synthesis traffic counts against their
  Claude Code subscription quota, not a separate API budget. Aligns
  with the personal-scale ethos.
- The CLI backend has higher per-call overhead (full Claude Code session
  invocation per call). For mining, where each call is itself a
  reasoning-heavy operation, the per-call overhead is small relative to
  the LLM time. For rule synthesis (one call per reclassification),
  same.
- We give up some control: prompt caching across batched mining calls is
  managed by Claude Code internally rather than by us. Acceptable.
- For environments where `claude -p` can't reach the network (corporate
  proxy, air-gapped) or the user explicitly wants pay-per-token billing
  for cost-tracking purposes, the SDK backend remains available.
- Adds one runtime dependency: `claude` must be on PATH. Capability
  probe records this; render engine refuses to enable mining hooks if
  `llm_backend: "cli"` and `claude` is missing.

## Alternatives considered

- **SDK only.** Rejected: forces every host to manage an API key for
  what's already a subscriber.
- **CLI only.** Rejected: removes the escape hatch for environments
  where `claude -p` won't work.
- **Detect at runtime which is available.** Considered, but explicit
  configuration is clearer for an audit and makes "why did this call
  use which backend" debuggable.
