# ADR-0009: Promotion-only flow for cross-boundary updates

**Status:** Accepted
**Date:** 2026-05-06

## Related tenets

- [Tenet 1 — First, do no harm](../tenets.md#1-first-do-no-harm)
- [Tenet 3 — Trust boundaries are physical](../tenets.md#3-trust-boundaries-are-physical-not-policy)
- [Tenet 7 — Provenance is mandatory](../tenets.md#7-provenance-is-mandatory)

## Context

A host with rw access only to its own profile's repo (e.g., a work
laptop with rw on `maury-work`, ro on `maury-base`) cannot push directly
to base. But mining on that host will sometimes produce fragments that
genuinely belong in base (e.g., "I prefer terse summaries with no
trailing recap" — a universal preference that surfaced in a work
session).

We need a path for those insights to propagate without violating the
read/write asymmetry that gives us our trust boundary.

## Decision

A host can only push to repos it has write access to (enforced
server-side by GitHub deploy-key permissions). When a fragment
classified for `base` is produced on a host without base write access,
maury writes it to that host's profile repo as
`proposals/promote-to-base/<id>.md`.

Any host with rw access to **both** the source repo (read) and the
destination repo (write) can run `maury promote-review` to inspect the
queue and copy approved proposals across.

The work laptop has no visibility into whether its proposal was
accepted, rejected, or modified. This is by design — feedback signals
could leak content classification.

## Consequences

- Insights about base preferences surfacing on a work host are
  deliberately one-way and require curator review.
- Promotion is a deliberate human action, not automation. The risk of
  accidentally promoting work-flavored content to base is mitigated by
  the curator step.
- A `push: disabled` mode is available for environments where even
  producing proposals from the work host is policy-problematic. In
  that mode, fragments classified for `base` are written only to the
  local quarantine, never to git.
- Curator role follows repo access, not host identity (per ADR-0003).

## Alternatives considered

- **Direct cross-repo writes from work hosts.** Rejected: violates the
  data-isolation premise.
- **Granting work hosts write access to base.** Rejected for default;
  available as an opt-in deployment posture if the user's threat model
  allows. Even then, the promote-review pattern remains useful for
  preventing accidental promotion of context-specific content.
- **Implicit promotion via maury's own logic.** Rejected: requires the
  tool to make trust decisions about content classification, which
  contradicts the rule-engine architecture (ADR-0004).
