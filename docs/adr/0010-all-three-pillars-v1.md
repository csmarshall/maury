# ADR-0010: All three pillars in v1

**Status:** Accepted
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

- See above. Charles confirmed all-in-one was preferred over staged.
