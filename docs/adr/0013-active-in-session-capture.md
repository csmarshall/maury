# ADR-0013: Active in-session capture — Claude Code helps build its own config

**Status:** Accepted
**Date:** 2026-05-06
**Amended:**
- 2026-05-07 — extended to cover user-initiated capture
  (UserPromptSubmit hook + `/maury-pin` slash command alongside
  the original Claude-initiated `maury-stage` skill); added
  active-context.json mechanism for telling Claude the host's
  active profile + inheritance chain; codified cross-boundary
  safety as a multi-layer guarantee (skill prompt + forbid rules
  + review step); mapped capture flow to inheritance access
  modes (concepts.md §6).

## Related tenets

- [Tenet 1 — First, do no harm](../tenets.md#1-first-do-no-harm)
- [Tenet 2 — Consistency within a profile](../tenets.md#2-consistency-within-a-profile-controlled-difference-across-profiles)
- [Tenet 5 — User arbitrates ambiguity](../tenets.md#5-user-arbitrates-ambiguity)
- [Tenet 8 — Hand-edits are first-class input](../tenets.md#8-hand-edits-are-first-class-input)

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

The **2026-05-07 amendment** extends this ADR to cover **both**
Claude-initiated capture (the original v1 design) and **user-
initiated capture** (where the user explicitly tells Claude or
maury to remember something). Both paths feed the same staging
pipeline; they're two entry points, not two systems.

## Decision

Add an **active capture pipeline** with two entry paths and one
staging backend. Maury ships four artifacts from `base/`,
distributed via the normal render engine:

### Entry path 1 — Claude-initiated (the original design)

Claude observes a durable pattern in the conversation, recognizes
it via the skill's instructions, and stages it.

1. **A skill `base/skills/maury-stage/SKILL.md`** that Claude
   invokes when it observes something the user may want to
   persist. Per [Claude Code skills documentation][cc-skills],
   skills are invokable units in `~/.claude/skills/<name>/SKILL.md`.
   The skill's prompt content tells Claude what to capture and
   what not to capture (mirrors the Anthropic CLAUDE.md ✅/❌
   rubric — see ADR-0011), and the format to use. The skill also
   tells Claude **the host's active profile and inheritance
   chain** (read at session start from `active-context.json`, see
   below) so suggestions never cross-boundary.
2. **A `base/CLAUDE.md` fragment** instructing Claude to invoke
   `maury-stage` when it observes durable preferences, voice
   cues, workflow patterns, anti-patterns, or new host-facts.

### Entry path 2 — user-initiated (added in 2026-05-07 amendment)

The user explicitly signals that something should be remembered.
Two sub-paths:

3. **A `UserPromptSubmit` hook** (action `run_script` resolving
   to a shipped `claude-maury-watch-prompt` script) that
   light-touch pattern-matches the user's prompt for explicit
   signals like "remember this," "for next time," "always do
   X." When matched, appends a *candidate* to the staging file
   tagged `source: user-prompt-cue` so the user reviews them
   alongside Claude-staged ones. Per
   [Claude Code hooks][cc-hooks], `UserPromptSubmit` fires once
   per turn on prompt submission — the right place to intercept
   the user's natural-language signal without changing the
   conversation flow.

4. **A custom slash command `~/.claude/commands/maury-pin.md`**
   per [Claude Code slash commands][cc-slash], rendered by maury,
   for explicit power-user pinning:
   ```
   /maury-pin <scope> <text>
   ```
   where `<scope>` is `base | <profile-name> | current` and
   `<text>` is the rule to pin. Writes to the staging file with
   `source: user-pin-explicit`. Higher confidence than the
   prompt-cue path because the user typed an explicit command.

### Backend — single staging file, single review

All four artifacts write to the same staging file (no separate
"Claude captures" vs "user pins" channels). Mining and review
treat them uniformly; the `source` field discriminates origin
for audit purposes only.

5. **A `Stop` hook** (action `run_script` resolving to a shipped
   wrapper `claude-maury-pending`) that prints `📌 maury: N
   capture(s) pending. Run \`maury review\` to inspect.` if the
   staging file is non-empty. Per [Claude Code hooks][cc-hooks],
   `Stop` fires once per turn (after each assistant response) —
   exactly the cadence wanted here, since the user wants the
   pending-capture nudge surfaced after they finish each
   assistant exchange, not just at session end.

The staging file lives at `~/.claude/maury-staging/captures.jsonl`.
It is **per-host**, **never synced to git**. Each line:

```json
{
  "ts": "2026-05-06T15:23:00Z",
  "source": "claude-skill|user-prompt-cue|user-pin-explicit",
  "kind": "voice|preference|workflow|anti-pattern|host-fact",
  "scope_hint": "base|<profile>|<host>|current",
  "text": "self-contained one-paragraph capture",
  "rationale": "why this is durable / how Claude derived it",
  "session_id": "abc..."
}
```

`scope_hint` is the source's best guess (Claude's, or the user's
when explicitly typed); the rule engine has final authority and
the user re-confirms during review. The value `current` is
shorthand for "use the active profile from `active-context.json`
on this host" — so a `/maury-pin current "..."` resolves to
whichever profile the host is currently in, without the user
having to type the profile name. The staging file is consumed
by mining (Phase 6); each line goes through the same
classification + proposal flow as a transcript-mined fragment,
but skips the extraction step (the line is already a clean
paragraph).

### Active-context file — what Claude needs to know

For Claude-initiated capture (entry path 1) to respect trust
boundaries, Claude needs to know **the host's active profile and
its inheritance chain.** Maury maintains
`~/.claude/maury-state/active-context.json`, rewritten on every
`maury init` and `maury profile use`:

```json
{
  "active_profile": "acme-client",
  "active_profile_id": "profile_a3f9...",
  "inheritance_chain": ["base", "work", "acme-client"],
  "host_id": "host_e3844a43...",
  "updated_at": "2026-05-07T..."
}
```

The `maury-stage` skill's prompt instructs Claude to read this
file at the start of every capture decision. The skill's prompt
text says (in spirit):

> *"The active profile on this host is `<active_profile>`. Its
> inheritance chain is `<chain>`. Suggest pinning content that
> fits this profile or its inheritance ancestors. Never suggest
> pinning content that belongs to a sibling profile — those have
> their own boundaries by design. If unsure, suggest
> `scope_hint: current` and let the user decide during review."*

The skill's prompt is loaded at session start per
[`cc-contract:startup-files-loaded`](../claude-code-contract.md#cc-contractstartup-files-loaded),
and that prompt is what instructs Claude to read
`active-context.json` before making capture decisions. Claude
Code does NOT auto-load `active-context.json`; the skill's
content is the only mechanism that brings the file into Claude's
context. Per
[`cc-contract:no-native-profile-tracking`](../claude-code-contract.md#cc-contractno-native-profile-tracking),
Claude Code itself records nothing about maury profiles — this
file is the only mechanism by which Claude can be told.

### Cross-boundary safety as a multi-layer guarantee

A single layer that can fail isn't enough. Cross-boundary
content leakage requires **all** of these to fail simultaneously:

1. **Skill prompt forbids cross-boundary suggestions.** Claude is
   explicitly told in the skill's prompt not to suggest pins
   outside the active inheritance chain.
2. **Forbid rules ([ADR-0004](0004-rule-engine-classification.md))
   run when the staged line is classified for a profile.** ADR-0004
   defines `forbid` as a classification-time guard that blocks a
   profile assignment regardless of `classify` rules. So a
   redaction-flagged phrase (host names from a different profile,
   identifiers, etc.) cannot be assigned to a profile that
   forbids it — meaning it cannot land as a proposal in that
   profile's repo, even if Claude staged it. The staging file
   may briefly hold flagged content, but the forbid guard
   prevents downstream profile assignment.
3. **Review step ([ADR-0022](0022-branch-per-mining-run.md)) is
   the final user-arbitrated gate.** Even if Claude AND the
   forbid rules failed, the user still sees the proposed change
   in the cherry-pick review and chooses to accept or reject.

A leak requires Claude AND the rule engine AND the user to fail
in succession — three-layer defense per Tenet 1.

### Inheritance access modes interaction

Per [concepts.md §6 (inheritance access modes)](../concepts.md#6-inheritance-access-mode):
the `scope_hint` chosen for a capture determines which repo's
review queue the proposal lands in. The user's effective access
mode (`ro` / `pr` / `rw`) on that target repo determines the
contribution flow during `maury review`:

- `rw` — proposal merges directly to that repo's main on accept.
- `pr` — proposal opens a GitHub PR (or equivalent) for curator
  review.
- `ro` — proposal cannot land directly; maury surfaces the
  cross-boundary promotion path ([ADR-0009](0009-promotion-only-cross-boundary.md))
  instead.

This means a user-pinned capture with `scope_hint: base` from a
work-laptop (which typically has `ro` on the base repo) will
correctly route through cross-boundary promotion rather than
quietly failing or producing a confusing error.

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
  per turn); `UserPromptSubmit` event used for the user-initiated
  passive watcher (fires once per turn before assistant
  responds, per the [`cc-contract:event-firing-cadence`](../claude-code-contract.md#cc-contractevent-firing-cadence)
  table).
- [`cc-skills`][cc-skills] — skills location (`~/.claude/skills/`)
  and SKILL.md invocation pattern.
- [`cc-slash`][cc-slash] — custom slash commands at
  `~/.claude/commands/<name>.md`; used for the `/maury-pin`
  power-user path.

[cc-hooks]: https://code.claude.com/docs/en/hooks
[cc-skills]: https://code.claude.com/docs/en/skills
[cc-slash]: https://code.claude.com/docs/en/slash-commands
