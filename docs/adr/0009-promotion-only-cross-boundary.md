# ADR-0009: Promotion-only flow for cross-boundary updates

**Status:** Superseded by [ADR-0045](0045-cross-trust-boundary-promotion.md) (2026-05-18)
**Date:** 2026-05-06

> **Superseded.** This ADR's mechanics (proposal queue, curator
> review, no source-host feedback) are now consolidated with
> [ADR-0027](0027-cross-context-promotion-via-shared-root.md)'s
> graph constraint into [ADR-0045: Cross-trust-boundary promotion](0045-cross-trust-boundary-promotion.md).
> The design described below is preserved as historical record
> of the mechanics layer. Read ADR-0045 for the canonical,
> currently-accepted decision.

## Related tenets

- [Tenet 1 — First, do no harm](../tenets.md#1-first-do-no-harm)
- [Tenet 3 — Trust boundaries are physical](../tenets.md#3-trust-boundaries-are-physical-not-policy)
- [Tenet 7 — Provenance is mandatory](../tenets.md#7-provenance-is-mandatory)

## TL;DR

A work host with read-only access to base will sometimes mine
fragments that genuinely belong in base — but giving it write access
to base would defeat the trust boundary. Maury uses a
**promotion-only flow**: the work host writes proposals into its own
profile repo (`proposals/promote-to-base/`), and any host with rw on
both source and destination runs `maury promote-review` to copy
approved proposals across. No source-host feedback channel — that's
intentional, as the signal could leak content classification.
Trade-off: latency between insight and base update, plus a curator
step.

## Context and Problem Statement

A host with rw access only to its own profile's repo (e.g., a work
laptop with rw on `maury-work`, ro on `maury-base`) cannot push directly
to base. But mining on that host will sometimes produce fragments that
genuinely belong in base (e.g., "I prefer terse summaries with no
trailing recap" — a universal preference that surfaced in a work
session).

We need a path for those insights to propagate without violating the
read/write asymmetry that gives us our trust boundary.

<details>
<summary><b>Decision drivers</b> (4 items — click to expand)</summary>

- **Tenet 1:** first, do no harm. Cross-boundary writes that
  bypass user review can leak context-specific content into a
  shared base — a tenet-1 violation.
- **Tenet 3:** trust boundaries are physical, not policy. Read/
  write asymmetry must hold even when insights legitimately need
  to flow.
- **Tenet 7:** provenance is mandatory. Promotion must carry a
  trace of who promoted what from where.
- **Threat model:** a compromised work host should not be able to
  silently inject content into shared base.

</details>

<details>
<summary><b>Considered options</b> (4 options — click to expand)</summary>

- **Option A:** Direct cross-repo writes from work hosts.
- **Option B:** Grant work hosts write access to base.
- **Option C:** Implicit promotion via maury's own logic — tool
  decides when to promote.
- **Option D (chosen):** Promotion-only flow — work host writes
  to a `proposals/promote-to-base/` queue in its own repo;
  curator reviews and copies across.

</details>

## Decision Outcome

**Chosen option:** Option D — a host can only push to repos it
has write access to (enforced server-side by GitHub deploy-key
permissions). Cross-boundary content moves through a
human-mediated proposal queue, never as a direct write. This is
the only option that preserves the ADR-0002/ADR-0003 trust
boundary while still letting useful insights flow upward.

### Implementation details

When a fragment classified for `base` is produced on a host
without base write access, maury writes it to that host's
profile repo as `proposals/promote-to-base/<id>.md`.

Any host with rw access to **both** the source repo (read) and
the destination repo (write) can run `maury promote-review` to
inspect the queue and copy approved proposals across.

The work laptop has no visibility into whether its proposal was
accepted, rejected, or modified. This is by design — feedback
signals could leak content classification.

A `push: disabled` mode is available for environments where even
producing proposals from the work host is policy-problematic. In
that mode, fragments classified for `base` are written only to
the local quarantine, never to git.

### Consequences

- ✅ **Good:** Trust boundary holds — the work laptop cannot
  inject content into base without curator review.
- ✅ **Good:** Promotion is a deliberate human action, not
  automation. The risk of accidentally promoting work-flavored
  content to base is mitigated by the curator step.
- ✅ **Good:** Curator role follows repo access, not host
  identity (per [ADR-0003](0003-per-host-deploy-keys.md)) — any
  host with the right two deploy keys can curate.
- ⚖️ **Neutral:** Insights about base preferences surfacing on
  a work host are deliberately one-way and require curator
  review.
- ⚖️ **Neutral:** A `push: disabled` mode exists for stricter
  environments.
- ❌ **Bad:** Adds latency between insight surfacing and
  insight landing in base — the curator must run
  `maury promote-review` for the fragment to propagate.
- ❌ **Bad:** Source host has no feedback channel on whether
  its proposal was accepted, rejected, or modified. **This is
  intentional** — feedback signals could leak content
  classification back to a host that should not have visibility
  into the destination repo. Tradeoff: weaker UX in exchange
  for not creating a side-channel that erodes the boundary.

### Confirmation

- Manifest schema validates that hosts with `mode: ro` on a
  destination repo route their finds through
  `proposals/promote-to-<dest>/` rather than direct write.
- `maury promote-review` (Phase 9, per `docs/status.md`)
  inspects the proposal queue and applies the curator's
  decisions with audit-log entries (per
  [ADR-0035](0035-audit-log.md)).
- [ADR-0027](0027-cross-context-promotion-via-shared-root.md)
  adds the inheritance-graph constraint on top of this ADR's
  mechanics.

<details>
<summary><b>Pros and cons of the options</b> (per-option ✅/❌ — click to expand)</summary>

#### Option A: Direct cross-repo writes from work hosts

- ✅ **Good:** Lowest latency from insight to base update.
- ❌ **Bad:** Violates the data-isolation premise.
- ❌ **Bad:** A misclassification immediately leaks context-
  specific content into shared base.

#### Option B: Grant work hosts write access to base

- ✅ **Good:** Simple — no proposal queue plumbing.
- ⚖️ **Neutral:** Available as an opt-in deployment posture if
  the user's threat model allows. Even then, the promote-review
  pattern remains useful for preventing accidental promotion.
- ❌ **Bad:** Rejected as default — same boundary-violation
  risk as Option A.

#### Option C: Implicit promotion via maury's own logic

- ✅ **Good:** Fully automated; no curator step.
- ❌ **Bad:** Requires the tool to make trust decisions about
  content classification, which contradicts the rule-engine
  architecture ([ADR-0004](0004-rule-engine-classification.md)).
- ❌ **Bad:** A misclassified fragment would silently cross
  the boundary.

#### Option D (chosen): Promotion-only flow with curator review

- ✅ **Good:** Trust boundary preserved; curator review
  catches misclassifications.
- ✅ **Good:** Works with existing deploy-key access model
  (per [ADR-0003](0003-per-host-deploy-keys.md)).
- ✅ **Good:** Composes with [ADR-0027](0027-cross-context-promotion-via-shared-root.md)'s
  inheritance-graph constraint for cross-context promotion.
- ❌ **Bad:** Adds latency; requires curator action.
- ❌ **Bad:** No feedback channel back to source host —
  intentional, to prevent classification side-channels (see
  Consequences).

</details>

## Build-order placement

Phase 9 (Promotion flow) — `maury promote-review` ships with
Phase 9. The proposal-queue layout (`proposals/promote-to-<dest>/`)
is established earlier (Phase 6/7 mining + review pipeline)
since fragments classified for unreachable destinations must
have somewhere to land before promotion exists.

## Followups

- **Audit-log integration** — every promotion writes an audit
  entry per [ADR-0035](0035-audit-log.md). Format and
  storage already specified there; wiring lands with Phase 9.
- **Inheritance-graph constraint** — [ADR-0027](0027-cross-context-promotion-via-shared-root.md)
  layers the *where in the graph* constraint on top of this
  ADR's *how cross-boundary promotion happens* mechanics.

## Amendment history

- 2026-05-11 — "profile" vocabulary renamed to "mode" per ADR-0037 doctoral examination. References to "profile" in this ADR now read "mode"; no semantic changes.
