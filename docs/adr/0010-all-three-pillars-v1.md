# ADR-0010: All three pillars in v1

**Status:** Accepted
**Date:** 2026-05-06
**Amended:**
- 2026-05-06 — phase additions + v1.1 deferrals captured in
  "Amendment (2026-05-06)" section below: Phase 2.5 (`maury
  doctor` per ADR-0011), Phase 6.5 (LLM backend abstraction
  per ADR-0012); v1.1 deferrals for ADR-0013 active capture,
  ADR-0014 secrets, ADR-0017 watch-mode drift, ADR-0019
  refactor-promote-common, and ADR-0016 non-git backends.

## Related tenets

- [Tenet 1 — First, do no harm](../tenets.md#1-first-do-no-harm)
- [Tenet 11 — Explicit beats implicit, with conservative defaults](../tenets.md#11-explicit-beats-implicit-with-conservative-defaults)

## Context and Problem Statement

The maury design spans three pillars: sync (across hosts), profile
isolation (across trust boundaries), and learning (mining transcripts
with rule-engine classification and proposal queue). Each is
independently large. The question for v1 is: do we ship one pillar
at a time and grow, or do we ship all three together?

## Decision Drivers

- **Mining-quality risk:** the riskiest piece of the design is
  whether mining produces useful output at all. Deferring it to
  v2 defers the answer to the question that determines whether
  the whole project is worth building.
- **Interface ossification risk:** if sync ships before mining,
  the rule engine, render engine, and miner won't be exercised
  against each other's real outputs from day one — assumptions
  bake in.
- **Coherence over time-to-first-release:** the user
  prioritized "build the thing right" over "ship a smaller
  thing sooner."
- **Retrofit cost:** v1-to-v2 schema and CLI changes are more
  expensive than v0-to-v1 changes inside an unreleased tool.

## Considered Options

- **Option A:** Sync first, mining in v2.
- **Option B:** Mining-only spike first.
- **Option C (chosen):** All three pillars together as one
  coherent v1.

## Decision Outcome

**Chosen option:** Option C — build all three pillars in v1,
designed as one coherent system. The user explicitly preferred
all-in-one over staged when the trade-off was named.

### Implementation details

Internal phasing remains in clean order (rule engine → manifest
→ capability probe → render engine → bootstrap → sync → mining
→ proposal review → rule synthesis → promotion → audit), but we
ship them together rather than as separate releases.

Release readiness depends on mining producing useful output. If
extraction quality is poor in early dogfooding, the **escape
hatch** is to ship sync+isolation as v0.5 and ship the full
thing as v1 once mining is proven. This preserves the "all
three" intent without forcing a bad mining release.

### Consequences

- ✅ **Good:** The rule engine, render engine, and miner each
  get exercised against the others' real outputs from day one.
  No interface assumptions ossify before they're tested.
- ✅ **Good:** No v1-to-v2 retrofit cost; the schema is shaped
  by all three pillars from the start.
- ❌ **Bad:** Larger v1 scope; longer to first usable release.
- ⚖️ **Neutral:** Phase ordering is internal scaffolding; users
  see only the v1 release.

### Confirmation

- `docs/status.md` tracks per-phase shipped/partial/planned
  status; release-readiness is gated by all three pillars
  having usable surfaces.
- The Amendment section below tracks phase additions and v1.1
  deferrals as the design has evolved.

## Pros and Cons of the Options

### Option A: Sync first, mining in v2

- ✅ **Good:** Fastest path to a useful daily-driver tool
  (multi-host config sync alone is valuable).
- ❌ **Bad:** Defers validating the riskiest piece (mining
  quality). The whole project's value depends on mining
  working; deferring is risk-averse only at first glance.
- ❌ **Bad:** Sync schema would have to be retrofitted when
  mining lands.

### Option B: Mining-only spike first

- ✅ **Good:** Lowest risk for "is this whole thing worth
  building" — answers the value question first.
- ❌ **Bad:** Single-host mining is much less interesting than
  multi-host mining; the spike doesn't reflect the eventual
  shape of the tool.
- ❌ **Bad:** Multi-host scaffolding is what makes maury
  unique; deferring it means the spike is only a partial
  signal.

### Option C (chosen): All three together

- ✅ **Good:** All interfaces exercised against real
  cross-component outputs from day one.
- ✅ **Good:** No retrofit cost.
- ❌ **Bad:** Largest upfront build; longest time to first
  release.
- ❌ **Bad:** Risk concentrated at one release point — if
  mining doesn't work, the whole release slips.

## Build-order placement

This ADR *defines* the build order rather than living inside
one phase. The phase list (rule engine → manifest → capability
probe → render engine → bootstrap → sync → mining → proposal
review → rule synthesis → promotion → audit) is normative; the
Amendment section below tracks subsequent additions and
deferrals.

## Followups

- See Amendment (2026-05-06) below for ongoing phase additions
  and v1.1 deferrals as new ADRs land.

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
