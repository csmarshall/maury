# ADR-0010: All three pillars in v1

**Status:** Accepted; partially amended — subsequent ADRs added phases
and deferred some pieces to v1.1. See "Amendment (2026-05-06)" below.
**Date:** 2026-05-06

## Context

The maury design spans three pillars: sync (across hosts), profile
isolation (across trust boundaries), and learning (mining transcripts
with rule-engine classification and proposal queue). Each is
independently large.

We considered three v1 scopes:

1. **Sync first, mining in v2.** Faster to a useful daily-driver tool
   but defers validating the riskiest piece (mining quality).
2. **Mining-only spike first.** Validate the learning loop on real
   workstation transcripts before investing in multi-host scaffolding. Lowest
   risk for "is this whole thing worth building."
3. **All three together.** Largest upfront build, but the rule engine
   matures with real data immediately and there's no v1-to-v2
   retrofit.

## Decision

Build all three pillars in v1, designed as one coherent system.

## Consequences

- Larger v1 scope; longer to first usable release.
- The rule engine, render engine, and miner each get exercised against
  the others' real outputs from day one. No interface assumptions
  ossify before they're tested.
- Internal phasing remains in clean order (rule engine → manifest →
  capability probe → render engine → bootstrap → sync → mining →
  proposal review → rule synthesis → promotion → audit), but we ship
  them together.
- Release readiness depends on mining producing useful output. If
  extraction quality is poor in early dogfooding, ship sync+isolation
  as v0.5 and ship the full thing as v1 once mining is proven.

## Alternatives considered

- See above. The owner confirmed all-in-one was preferred over staged.

## Amendment (2026-05-06): phase additions + v1.1 deferrals

After the initial decision, several follow-up ADRs added new phases
within v1's three pillars or pushed pieces to v1.1. The current v1
phase list is the union of the original phasing in this ADR and the
additions/deferrals below; treat this amendment as the source of truth
for what ships in v1 vs v1.1.

**Added to v1 by subsequent ADRs:**

- **Phase 2.5 — `maury doctor`** (per [ADR-0011](0011-anthropic-rubric-integration.md)): Anthropic-rubric
  CLAUDE.md evaluator. Already shipped.
- **Phase 6.5 — LLM backend abstraction** (per [ADR-0012](0012-llm-backend.md)): `LLMClient`
  protocol with `claude -p` default + SDK opt-in.

**Deferred to v1.1:**

- **Phase 5.5 active-capture distribution** (per [ADR-0013](0013-active-in-session-capture.md) +
  [ADR-0017](0017-drift-detection-and-reconciliation.md)): the full `maury-stage` skill + CLAUDE.md fragment +
  staging-file consumer pipeline. v1 ships only the lighter
  `maury-status` skill (so Claude can warn the user about pending
  drift / proposals in-session).
- **Phase 5.6 host-local secrets** (per [ADR-0014](0014-host-local-secrets-with-metadata-sync.md)): the entire
  secrets/services + 3-backend module.
- **Phase 5.5b background drift watcher** (per [ADR-0017](0017-drift-detection-and-reconciliation.md)).
- **`maury refactor promote-common`** (per [ADR-0019](0019-inheritance-semantics-refine-by-default.md)): the
  replacement→refinement migration tool.
- **Pluggable repo backends beyond `git`/`github`** (per
  [ADR-0016](0016-pluggable-repo-backends.md)): `gitlab`, `gitea`, `p4`, `s3-age`, etc. The
  schema field is in v1; the additional adapters are post-v1.

**Cut from earlier scope:** none. The v1.1 deferrals reduce scope but
nothing originally promised has been removed entirely.
