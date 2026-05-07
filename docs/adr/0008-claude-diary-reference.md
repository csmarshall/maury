# ADR-0008: Reimplement mining; claude-diary as design reference, not dependency

**Status:** Accepted
**Date:** 2026-05-06

## Related tenets

- [Tenet 7 — Provenance is mandatory](../tenets.md#7-provenance-is-mandatory)
- [Tenet 9 — Defer to the platform](../tenets.md#9-defer-to-the-platform)

## Context and Problem Statement

[rlancemartin/claude-diary](https://github.com/rlancemartin/claude-diary)
is a community-built tool that mines `~/.claude/projects/*.jsonl`
transcripts and uses pattern thresholds to update CLAUDE.md. It's MIT
licensed, well-regarded (363 stars), and at first glance looks like a
candidate to vendor, fork, or contribute to.

Investigation revealed:

- Not Anthropic-official. Built by Lance Martin (LangChain).
- Maintainer is disengaged: last commit Dec 2025, three open issues
  with no maintainer response (one of them a real correctness bug
  re: PreCompact hook stdout), one structural-improvement PR open ~4
  months unreviewed.
- **There is no library code.** The repo is `commands/diary.md` +
  `commands/reflect.md` (slash-command prompts) + a 6-line
  PreCompact hook script. The mining logic lives entirely inside LLM
  prompts; the 2+/3+ thresholds are natural-language directives in
  the prompts.
- Output is markdown auto-appended to CLAUDE.md, not structured data
  consumable by a downstream pipeline.
- Two downstream projects (`claude-layered-learning`, `session-letter`)
  hit the same "nothing to extend" wall and built around it.

## Decision Drivers

- **Tenet 7:** provenance is mandatory. Maury needs structured
  fragment output with provenance; claude-diary's natural-
  language prompt outputs don't carry the metadata we need.
- **Tenet 9:** defer to the platform. We do reuse what's there
  (ideas, design patterns); we don't fork code that has no
  library shape.
- **Independent iteration cadence:** maury's mining quality is on
  the critical path; coupling it to an unmaintained upstream is
  a roadmap risk.
- **Credit the prior art:** the design patterns (2+/3+ thresholds,
  PreCompact hook capture, observe-reflect-retrieve) are
  meaningful and worth crediting publicly.

## Considered Options

- **Option A:** Vendor claude-diary into maury.
- **Option B:** Submit a structural-improvement PR upstream and
  build on it.
- **Option C:** Recommend installing claude-diary alongside maury.
- **Option D (chosen):** Reimplement in Python; credit
  claude-diary in README as a design reference; do not vendor or
  depend.

## Decision Outcome

**Chosen option:** Option D — build maury's miner in Python from
scratch. Borrow proven design ideas from claude-diary — credit
Lance in README — but do not vendor or depend on its code. This
is the only option that gives us structured-fragment output and
independent iteration without forking an unmaintained upstream.

### Implementation details

Specifically borrowed from claude-diary:

- The 2+/3+ pattern threshold (2+ occurrences = pattern, 3+ = strong
  pattern, escalates classification confidence).
- The PreCompact-hook capture point as a recommended (optional)
  trigger, in addition to scheduled or manual mining.
- The observe → reflect → retrieve cadence as the conceptual cycle.

We control output format (structured fragments with provenance), so
integration with the rule engine and proposal queue is direct.

README "Related work" section credits claude-diary explicitly. A
one-line "see also" issue filed upstream pointing at maury is the
good-citizen gesture; we don't expect a reply.

### Consequences

- ✅ **Good:** Output is structured (fragments with provenance),
  consumable by the rule engine and proposal queue.
- ✅ **Good:** Independent iteration on extraction quality —
  prompt engineering, caching strategy, deduplication heuristics
  — without coordinating with upstream.
- ✅ **Good:** Credits the prior art publicly; ethically clean.
- ❌ **Bad:** Mining is a substantial code component, not a thin
  wrapper over a dependency.
- ⚖️ **Neutral:** If claude-diary or a successor ever exposes a
  library API, revisit.

### Confirmation

- README "Related work" credits claude-diary explicitly with
  the borrowed concepts named.
- Mining code (planned at `src/maury/mining/`, Phase 6) is
  pure-maury; no claude-diary imports.

## Pros and Cons of the Options

### Option A: Vendor claude-diary

- ✅ **Good:** Reuses an established tool's design verbatim.
- ❌ **Bad:** No extractable library — the "code" is prompts.
  Adapting them is similar effort to writing our own.
- ❌ **Bad:** Output format is markdown-append to CLAUDE.md;
  doesn't fit our structured-fragment pipeline.

### Option B: Submit a structural-improvement PR upstream

- ✅ **Good:** Improves the broader ecosystem.
- ❌ **Bad:** Precedent (existing PR open 4 months unreviewed)
  suggests it would sit indefinitely.
- ❌ **Bad:** Even if merged, we'd still be coupled to
  upstream's release cadence.

### Option C: Recommend installing claude-diary alongside

- ✅ **Good:** Zero code investment; user gets both tools.
- ❌ **Bad:** Hard handoff between two tools with no shared
  format.
- ❌ **Bad:** No control over output structure or schema; we
  can't classify or audit what claude-diary produces.

### Option D (chosen): Reimplement; credit upstream

- ✅ **Good:** Full control over output format and schema.
- ✅ **Good:** Independent iteration cadence.
- ✅ **Good:** Public credit preserves the ethical relationship.
- ❌ **Bad:** Mining is a substantial code component to write
  and maintain.

## Build-order placement

Phase 6 (Mining + extraction) — the borrowed concepts (2+/3+
thresholds, observe-reflect-retrieve cadence, optional
PreCompact-hook trigger) inform the design of the Phase 6
miner.

## Followups

- **Upstream "see also" issue** filed against
  [rlancemartin/claude-diary](https://github.com/rlancemartin/claude-diary)
  pointing at maury — the good-citizen gesture. Not yet filed;
  do it when maury is at first user-visible release.
- **Re-evaluate if upstream exposes a library API** — current
  state (no library, prompts only) made forking pointless. If
  that changes, revisit the dependency stance.

## Claude Code references

Verified-as-of 2026-05-07 against Anthropic's official Claude
Code documentation:

- [`cc-hooks-precompact`][cc-hooks-precompact] — the
  `PreCompact` hook event fires before Claude Code compacts
  the conversation. claude-diary uses this as its capture
  trigger; maury offers it as one of several optional
  triggers (alongside scheduled and manual mining).
- [`cc-jsonl-store`][cc-jsonl-store] — transcript storage path
  (the corpus claude-diary mines and that maury's miner reads
  per [ADR-0005](0005-local-only-mining.md)).

[cc-hooks-precompact]: https://code.claude.com/docs/en/hooks#precompact
[cc-jsonl-store]: https://code.claude.com/docs/en/sessions
