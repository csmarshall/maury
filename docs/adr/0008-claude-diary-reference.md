# ADR-0008: Reimplement mining; claude-diary as design reference, not dependency

**Status:** Accepted
**Date:** 2026-05-06

## Context

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

## Decision

Build maury's miner in Python from scratch. Borrow proven design
ideas from claude-diary — credit Lance in README — but do not vendor
or depend on its code. Specifically borrowed:

- The 2+/3+ pattern threshold (2+ occurrences = pattern, 3+ = strong
  pattern, escalates classification confidence).
- The PreCompact-hook capture point as a recommended (optional)
  trigger, in addition to scheduled or manual mining.
- The observe → reflect → retrieve cadence as the conceptual cycle.

We control output format (structured fragments with provenance), so
integration with the rule engine and proposal queue is direct.

## Consequences

- Mining is a substantial code component, not a thin wrapper over a
  dependency.
- We can iterate on extraction quality independently — prompt engineering,
  caching strategy, deduplication heuristics — without coordinating with
  upstream.
- README "Related work" section credits claude-diary explicitly. A
  one-line "see also" issue filed upstream pointing at maury is the
  good-citizen gesture; we don't expect a reply.
- If claude-diary or a successor ever exposes a library API, revisit.

## Alternatives considered

- **Vendor claude-diary.** Rejected: no extractable library; the
  "code" is prompts. Adapting them is similar effort to writing our
  own.
- **Submit a structural-improvement PR.** Rejected pragmatically:
  precedent (PR #3 open 4 months) suggests it would sit unreviewed.
- **Recommend installing claude-diary alongside.** Rejected: hard
  handoff between two tools, no control over output format.
