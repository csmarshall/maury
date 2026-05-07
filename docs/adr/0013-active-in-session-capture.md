# ADR-0013: Active in-session capture — Claude Code helps build its own config

**Status:** Accepted
**Date:** 2026-05-06

## Related tenets

- [Tenet 2 — Consistency within a profile](../tenets.md#2-consistency-within-a-profile-controlled-difference-across-profiles)

## Context

The original mining design (ADR-0005, ADR-0008) is **passive**: each
host periodically scans its `~/.claude/projects/*.jsonl` transcripts,
extracts candidate fragments, classifies them, and queues proposals.

This works but is lossy. Claude Code already has a model of when
something is durable vs ephemeral — it just doesn't have a place to
say so. Asking the user to wait until next sync, then re-mining
transcripts to recover insights Claude already had in-session, is
strictly worse than letting Claude flag them as they happen.

Concrete example: in a session where Claude reads the user's email
to learn their writing voice, the model derives a clean, summarizable
characterization of that voice. That characterization is the durable
thing the user wants. Mining the JSONL transcript later may or may not
recover it cleanly — there's no signal in the transcript that *that
particular paragraph* was the durable part.

## Decision

Add an **active capture pipeline**: maury ships three artifacts that
let Claude stage durable insights into a per-host file that maury then
consumes alongside passive mining.

Distributed via the normal render engine, all from `base/`:

1. **A skill `base/skills/maury-stage/SKILL.md`** that Claude invokes
   when it observes something the user may want to persist. Per
   [Claude Code skills documentation][cc-skills], skills are
   invokable units in `~/.claude/skills/<name>/SKILL.md`. The
   skill's prompt content tells Claude what to capture and what not
   to capture (mirrors the Anthropic CLAUDE.md ✅/❌ rubric — see
   ADR-0011), and the format to use.
2. **A `base/CLAUDE.md` fragment** instructing Claude to invoke
   `maury-stage` when it observes durable preferences, voice cues,
   workflow patterns, anti-patterns, or new host-facts.
3. **A `Stop` hook** (action `run_script` resolving to a shipped
   wrapper `claude-maury-pending`) that prints `📌 maury: N
   capture(s) pending. Run \`maury review\` to inspect.` if the
   staging file is non-empty. Per [Claude Code hooks][cc-hooks],
   `Stop` fires once per turn (after each assistant response) —
   exactly the cadence wanted here, since the user wants the
   pending-capture nudge surfaced after they finish each assistant
   exchange, not just at session end.

The staging file lives at `~/.claude/maury-staging/captures.jsonl`. It
is **per-host**, **never synced to git**. Each line:

```json
{
  "ts": "2026-05-06T15:23:00Z",
  "kind": "voice|preference|workflow|anti-pattern|host-fact",
  "scope_hint": "base|<profile>|<host>",
  "text": "self-contained one-paragraph capture",
  "rationale": "why this is durable / how Claude derived it"
}
```

`scope_hint` is Claude's best guess; the rule engine has final
authority. The staging file is consumed by mining (Phase 6); each
line goes through the same classification + proposal flow as a
transcript-mined fragment, but skips the extraction step (Claude
already wrote a clean paragraph).

## Consequences

- Mining gets a high-signal input source. Active captures are labeled
  by Claude with kind + scope_hint at write time, so they enter
  classification with much more context than raw transcript snippets.
- The pipeline is recursive in a satisfying way: maury distributes the
  skill + fragment + hook to every host through normal sync, so the
  capture mechanism arrives on every machine the moment it joins the
  fleet.
- The user gets a clear feedback loop: at session end, "here's what
  Claude thought you might want to persist." No mystery batch processing.
- We rely on Claude following the CLAUDE.md instruction. False negatives
  (Claude doesn't stage something it could have) are caught by the
  passive miner. False positives (Claude stages noise) are caught by
  the user during `maury review`.
- The staging file must never sync to git. Defense-in-depth:
  `~/.claude/maury-staging/` is local-only, plus the file lives outside
  any synced repo path.
- Cross-profile content emerging on the wrong host (e.g., a "linux-server"
  capture from a work-laptop session) gets quarantined by the
  anomaly-detection logic from ADR-0005 before reaching git.

## Build-order placement

New phase **Phase 5.5 — Active capture distribution.** Sits between
Phase 5 (sync) and Phase 6 (mining):

- Authors the skill, fragment, hook in `base-template/`.
- Implements the staging-file consumer in the mining module.
- Verifies the render engine installs all three artifacts on a fresh host.

Cannot ship before Phase 3 (render engine) and Phase 5 (sync workflow).

## Alternatives considered

- **Anthropic auto-memory (v2.1.59+).** Per-project, machine-local,
  doesn't propagate to user-global CLAUDE.md or to other hosts. Useful
  but not a substitute. Maury can additionally consume from the
  auto-memory tree as a secondary signal source — punt to v2.
- **Pure passive mining.** What we'd planned. Rejected as strictly
  inferior once we realized Claude can label durable content as it
  happens.
- **A new MCP server hosted by maury.** Considered. Skill + hook is
  simpler and arrives via the same render engine the rest of maury
  uses. MCP would be over-engineered for this.

## Claude Code references

Verified-as-of 2026-05-07 against Anthropic's official Claude
Code documentation:

- [`cc-hooks`][cc-hooks] — `Stop` event semantics (fires once
  per turn).
- [`cc-skills`][cc-skills] — skills location (`~/.claude/skills/`)
  and SKILL.md invocation pattern.

[cc-hooks]: https://code.claude.com/docs/en/hooks
[cc-skills]: https://code.claude.com/docs/en/skills
