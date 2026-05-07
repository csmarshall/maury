# ADR-0012: LLM backend — `claude -p` by default, SDK opt-in

**Status:** Accepted
**Date:** 2026-05-06

## Related tenets

- [Tenet 9 — Defer to the platform](../tenets.md#9-defer-to-the-platform)

## TL;DR

If the user already has Claude Code installed and authenticated,
forcing a separate `ANTHROPIC_API_KEY` for mining and rule synthesis
is rebuilding what's already there — and bills against a separate
budget. Maury defaults to **`claude -p` (headless mode) for LLM
calls**, with the Anthropic SDK as opt-in via
`host.llm_backend: "cli" | "sdk"` in the manifest. Both backends
implement a single `LLMClient` interface so callers don't care which
runs. Trade-off: less control over prompt caching with the CLI
default; two backends to maintain.

## Context and Problem Statement

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

<details>
<summary><b>Decision drivers</b> (4 items — click to expand)</summary>

- **Tenet 9:** defer to the platform. If the user already has
  Claude Code authenticated, requiring a separate API key is
  rebuilding what's already there.
- **Personal-scale economics:** mining/synthesis traffic should
  count against the user's existing Claude Code subscription,
  not require a separate billing relationship.
- **Escape hatch:** environments where `claude -p` can't work
  (air-gap, corporate proxies, explicit cost-tracking
  requirements) need a path.
- **Auditability:** "which backend made this call?" should be
  answerable from configuration, not from runtime probing.

</details>

<details>
<summary><b>Considered options</b> (4 options — click to expand)</summary>

- **Option A:** SDK only — every host manages an `ANTHROPIC_API_KEY`.
- **Option B:** CLI only — `claude -p` everywhere, no escape
  hatch.
- **Option C:** Detect at runtime which backend is available
  and use whichever wins.
- **Option D (chosen):** `claude -p` default with SDK opt-in
  per-host or per-call, behind a single `LLMClient` interface.

</details>

## Decision Outcome

**Chosen option:** Option D — default LLM backend is
`claude -p`. SDK is opt-in per-host or per-call. The miner
emits a single internal `LLMClient` interface; both backends
implement it. This is the only option that gets the
out-of-the-box ergonomics of using existing Claude Code auth
*and* preserves an escape hatch for environments where it
won't work, while keeping which-backend-was-used auditable.

### Implementation details

- Configured via `host.llm_backend: "cli" | "sdk"` in the
  manifest, or `MAURY_LLM_BACKEND` environment variable.
- Default: `cli`.
- The miner emits a single internal `LLMClient` interface;
  both backends implement it. Mining/synthesis call the
  interface; the manifest picks the implementation at
  runtime.
- `claude -p --output-format stream-json` is the wire format
  for the CLI backend; `Messages` API with prompt caching is
  the wire format for the SDK backend.

### Consequences

- ✅ **Good:** Maury runs out-of-the-box on any host that has
  `claude` installed and authenticated. No API key required.
- ✅ **Good:** The user's mining and rule-synthesis traffic
  counts against their Claude Code subscription quota, not a
  separate API budget. Aligns with the personal-scale ethos.
- ✅ **Good:** Escape hatch preserved — SDK backend available
  for air-gap / proxy / cost-tracking environments.
- ⚖️ **Neutral:** The CLI backend has higher per-call overhead
  (full Claude Code session invocation per call). For
  mining, where each call is itself a reasoning-heavy
  operation, the per-call overhead is small relative to the
  LLM time. For rule synthesis (one call per
  reclassification), same.
- ❌ **Bad:** We give up some control — prompt caching across
  batched mining calls is managed by Claude Code internally
  rather than by us.
- ❌ **Bad:** Adds a runtime dependency (`claude` must be on
  PATH for the default backend). Capability probe records
  this; render engine refuses to enable mining hooks if
  `llm_backend: "cli"` and `claude` is missing.

### Confirmation

- `src/maury/llm/` (planned) defines the `LLMClient`
  interface and the two backend implementations.
- Capability probe surfaces `claude_cli_present: bool`; the
  render engine consults this when `llm_backend: "cli"`.
- Manifest schema validates `llm_backend ∈ {"cli", "sdk"}`.

<details>
<summary><b>Pros and cons of the options</b> (per-option ✅/❌ — click to expand)</summary>

#### Option A: SDK only

- ✅ **Good:** Full control over batching, caching, model
  choice.
- ❌ **Bad:** Forces every host to manage an API key for what
  is already a subscriber.
- ❌ **Bad:** Separate billing relationship for what is
  already a Claude Code workload.

#### Option B: CLI only

- ✅ **Good:** Single backend; less code.
- ❌ **Bad:** Removes the escape hatch for environments where
  `claude -p` won't work (corporate proxy, air-gap).
- ❌ **Bad:** Hosts with explicit cost-tracking requirements
  can't switch to per-token billing.

#### Option C: Detect at runtime

- ✅ **Good:** No configuration needed.
- ❌ **Bad:** Explicit configuration is clearer for an audit
  and makes "why did this call use which backend"
  debuggable.
- ❌ **Bad:** Detection-based fallback is the kind of silent
  precedence Tenet 5 warns against.

#### Option D (chosen): CLI default + SDK opt-in behind one interface

- ✅ **Good:** Best out-of-the-box ergonomics for the common
  case.
- ✅ **Good:** Explicit per-host configuration; auditable.
- ✅ **Good:** Escape hatch preserved.
- ❌ **Bad:** Two backends to maintain.
- ❌ **Bad:** Less control over caching/batching when running
  on the CLI default.

</details>

## Build-order placement

Phase 6.5 — LLM backend abstraction lands between Phase 6
(Mining) and Phase 8 (Rule synthesis), since both depend on
the `LLMClient` interface. The default-cli default makes
Phase 6 immediately runnable on hosts with `claude`
installed.

## Followups

- **Per-call backend override** beyond per-host — surface a
  CLI flag (`--llm-backend=sdk`) for one-off calls so the
  user can mix backends within a single mining run if
  needed.
- **Cost reporting** — a `maury costs` command summarizing
  per-host LLM spend, distinguishing cli (subscription quota)
  from sdk (per-token API charges). Requires the SDK backend
  to surface cost data.

## Claude Code references

Verified-as-of 2026-05-07 against Anthropic's official Claude
Code documentation:

- [`cc-headless`][cc-headless] — `claude -p "prompt"` is
  Claude Code's documented headless / non-interactive mode,
  designed for "pipelines, scripts, hooks." Auth uses the
  user's existing Claude Code login; the user's
  subscription quota is the budget.
- [`cc-headless-output`][cc-headless-output] —
  `--output-format stream-json` is the documented streaming
  JSON output format the CLI backend uses to integrate with
  the `LLMClient` interface.
- [Anthropic Python SDK][anthropic-sdk] — the alternative
  backend; uses `ANTHROPIC_API_KEY` and per-token billing.

[cc-headless]: https://code.claude.com/docs/en/headless
[cc-headless-output]: https://code.claude.com/docs/en/headless#output-formats
[anthropic-sdk]: https://docs.anthropic.com/en/api/client-sdks#python
