# ADR-0050: Agency identity and boundary

**Status:** Accepted
**Date:** 2026-05-18
**Sub-ADR of:** [ADR-0037](0037-layer-taxonomy-and-repo-discovery.md)

## Related tenets

- [Tenet 3 — Trust boundaries are physical, not policy](../tenets.md#3-trust-boundaries-are-physical-not-policy)
- [Tenet 6 — Identity is not name](../tenets.md#6-identity-is-not-name)
- [Tenet 11 — Explicit beats implicit, with conservative defaults](../tenets.md#11-explicit-beats-implicit-with-conservative-defaults)

## Related ADRs

- [ADR-0015](0015-surrogate-keys-for-hosts-and-profiles.md) —
  URL is the surrogate key for repos; `host_<hex>` IDs; this
  ADR's `agency_id` is the surrogate key for the bounded
  installation
- [ADR-0037](0037-layer-taxonomy-and-repo-discovery.md) —
  umbrella; this ADR carries the agency-identity layer
- [ADR-0039](0039-bootstrap-and-host-lifecycle.md) — bootstrap
  flow; `maury agency init` generates the `agency_id`
- [ADR-0049](0049-layer-taxonomy.md) — sibling sub-ADR; the
  three layer types contained within an agency
- [ADR-0051](0051-marker-file-and-distributed-manifest.md) —
  sibling sub-ADR; the marker file that carries `agency_id` on
  every layer

## TL;DR

The **agency** is the bounded management unit — the totality of
repos and hosts maury manages together as one installation. It
has a stable identity (`agency_id`, a UUID generated once at
`maury agency init` and never changed) that survives repo
renames, URL changes, and re-homing. Every layer's marker file
carries `agency_id`. On `base` and `mode` repos, `agency_id` is
a **membership claim** ("this repo belongs to this agency"); a
mismatch is a hard error at registration. On `rules` repos,
`agency_id` is a **provenance claim** ("this repo was created
by this agency"); cross-agency consumption is supported and
expected.

## Context and Problem Statement

A maury installation may span many repos and hosts. Two
inseparable questions follow:

- **What identity does the installation itself have?** Without
  a stable identity, agency-wide invariants (like "exactly one
  base layer") cannot be enforced, and a stranger looking at a
  repo cannot tell which installation it belongs to.
- **How do `rules` repos work across installations?**
  Engineering-standards teams routinely publish rules repos
  intended for consumption by many organizations. The
  installation identity must distinguish "this is OUR repo" from
  "this is a SHARED repo we subscribe to."

The naïve answer (use repo URLs as the identity) fails on Tenet
6 — URLs change, hosts get re-homed, and the agency must
survive those changes. The needed identity is **opaque, stable,
and self-asserting**: every layer in the agency carries the
same `agency_id`, and the validity of the assertion is checked
at registration.

[ADR-0037](0037-layer-taxonomy-and-repo-discovery.md) is the
umbrella; this sub-ADR carries the **agency-identity** layer.
The taxonomy lives in [ADR-0049](0049-layer-taxonomy.md); the
marker-file schema lives in
[ADR-0051](0051-marker-file-and-distributed-manifest.md).

## Decision Drivers

- **Stable identity (Tenet 6).** The agency must have an
  identity that survives renames, URL changes, and re-homing
  of individual repos.
- **Cross-agency rules sharing is a feature.** Teams publish
  shared rules; blocking cross-agency consumption defeats the
  reuse story.
- **Membership incoherence is a hard error.** A `base` or
  `mode` repo with a foreign `agency_id` is almost always a
  mis-pasted URL or accidental cross-agency contamination —
  the right response is to force resolution, not silent
  acceptance.
- **Tenet 11 (explicit beats implicit).** Agency membership is
  an explicit field in the marker file, not an implicit
  property of which repos sit next to each other on disk.

## Considered Options

### A — Identify agencies by base repo URL

The agency's identity is the URL of its `base` repo.

- ❌ URLs change. Renaming or re-homing the base would
  invalidate every other repo's membership claim.
- ❌ Backend pluralism is broken — the URL format is
  backend-specific.

### B — Identify agencies by a curator-chosen string ("acme-corp")

Each agency picks a short human-readable name.

- ❌ Naming collisions are inevitable across teams that pick
  the same string.
- ❌ Renaming the agency would still invalidate every claim.

### C (chosen) — Identify agencies by a generated UUID (`agency_id`)

- ✅ Opaque and self-stable. The `agency_id` is generated once
  at `maury agency init` and never changes, regardless of repo
  URLs or hosting.
- ✅ Two agencies can never collide accidentally (UUID
  collision probability is effectively zero).
- ✅ Every layer's marker file carries it; the agency's identity
  is asserted on every repo independently.
- ⚖️ Opaque — a curator cannot tell from the `agency_id` alone
  which agency it is. The human-readable name lives in the
  base repo's name and `agency_id` is only the surrogate.

### Membership-vs-provenance semantics

A second decision sits inside option C: what does it mean for a
`rules` repo to carry an `agency_id` that doesn't match the
consuming installation's?

- **D1 (chosen) — `agency_id` is membership on `base` + `mode`,
  provenance on `rules`.** Mismatch on `base`/`mode` is a hard
  error; mismatch on `rules` is informational.
- **D2 — Universal membership.** Every layer's `agency_id` must
  match. Cross-agency rules consumption is forbidden.
  Rejected: defeats the shared-rules story.
- **D3 — Universal provenance.** Mismatch is informational
  everywhere; the installation accepts any `base`/`mode` claim.
  Rejected: incoherent state. A `base` repo making a foreign
  membership claim is genuinely wrong, not just surprising.

## Decision Outcome

Chosen: **Option C + D1** — `agency_id` is a UUID; membership
claim on `base` and `mode`, provenance claim on `rules`.

### The agency

The bounded management unit — the totality of repos and hosts
maury manages together as a single installation — is named an
**agency**. The name reflects that maury *is* an agent operating
on the user's behalf across multiple hosts and modalities; the
bounded unit it operates within is therefore an agency.

Every layer carries an `agency_id` field in its marker file
(per [ADR-0051](0051-marker-file-and-distributed-manifest.md)):

- On `base` and `mode` repos: a **membership claim** ("this
  repo belongs to this agency").
- On `rules` repos: a **provenance claim** ("this repo was
  originally created by this agency"). Cross-agency
  consumption of `rules` repos is expected and supported; they
  are designed to be shareable.

The `agency_id` is a UUID generated once at `maury agency init`
(see [ADR-0039](0039-bootstrap-and-host-lifecycle.md)) and
never changes.

### Mismatch behavior

A `base` or `mode` repo whose `agency_id` does not match the
installation's `agency_id` is a **hard error at registration**.

**Rationale.** A `base` or `mode` repo is making a membership
claim — it asserts it belongs to a specific agency. When that
claim contradicts the installing agency's identity, the state
is incoherent rather than merely surprising. It almost always
indicates a mis-pasted URL or accidental cross-agency
contamination. A warning that the user can dismiss is the
wrong response to an incoherent state; a hard error forces
resolution.

A `rules` repo whose `agency_id` differs is **informational
only** — cross-agency rules sharing is a feature, not an
anomaly. The `agency_id` on a `rules` repo is **provenance**
(where the repo was created), not **membership** (who may
consume it). A `rules` repo from a different agency is
expected: it means "this was created elsewhere and we are
subscribing to it." Engineering-standards teams publish
`rules` repos intended for consumption by every team in the
company; blocking cross-agency consumption on a rules repo
would make sharing impossible without a hard `agency_id`
mismatch override. The hard-error behavior is reserved for
`base` and `mode` repos, where a mismatched `agency_id` is a
genuine membership incoherence rather than a cross-team sharing
pattern.

### Base layer uniqueness

Exactly **one** `base` layer exists per agency.

- `maury repos register` against an existing base in the same
  `agency_id` is a hard error. Override requires
  `--replace --i-am-sure`.
- `--replace` re-points the agency's base; it does not delete
  the old base or its content. A cascade warning lists every
  repo whose sublayer declarations reference the outgoing base
  before the replace proceeds.
- `maury agency validate` (per
  [ADR-0040](0040-render-pipeline.md)) emits a hard failure
  (not a warning) if it finds two `layer: base` repos with the
  same `agency_id`. There is no "but it might be intentional"
  path.

A successful `--replace --i-am-sure` does not delete the old
base; it only re-points the agency's identity claim at a new
repo. The old base can still be inspected (its marker file
still claims membership in the agency); the agency simply
sources its base content from the replacement going forward.

## Consequences

- ✅ **Good:** `agency_id` provides stable agency identity
  across repo renames and re-homings. Cross-agency `rules`
  consumption is supported (provenance, not membership).
- ✅ **Good:** Two distinct semantic loads on one field
  (membership for `base`/`mode`, provenance for `rules`) cover
  both invariant enforcement and cross-team sharing without
  introducing a second identity field.
- ✅ **Good:** Hard-error-at-registration for `base`/`mode`
  mismatches forces resolution at the moment the user can
  diagnose the problem (they have the URL they pasted in front
  of them).
- ✅ **Good:** UUID generation at `maury agency init` is a
  one-time act; the curator never has to type or remember the
  agency_id.
- ⚖️ **Neutral:** `--replace --i-am-sure` exists for the rare
  case where re-pointing the base is correct. The double-flag
  pattern signals "this is destructive enough to require two
  separate confirmations."
- ❌ **Bad:** Opaque UUIDs mean a curator looking at
  `agency_id: 550e8400-e29b-41d4-a716-446655440000` cannot tell
  which agency it is. The human-readable name lives in the
  base repo's URL/name, not in `agency_id`. Acceptable
  trade-off for stability.

### Confirmation

- `maury agency init` generates the `agency_id` once and writes
  it to the base repo's marker. Tested by asserting the
  generated UUID matches RFC 4122 and is persisted across
  `maury sync` invocations.
- `maury repos register` against a `base`/`mode` repo with a
  mismatched `agency_id` refuses with a specific error. Tested
  per repo type.
- `maury repos register` against a `rules` repo with a
  mismatched `agency_id` succeeds with an informational notice.
  Tested.
- `maury agency validate` emits a hard failure when two
  `layer: base` repos share an `agency_id`. Tested.

## Build-order placement

`agency_id` generation ships in Phase 4 (`maury agency init`
per [ADR-0039](0039-bootstrap-and-host-lifecycle.md)).
Registration-time mismatch checks ship alongside `maury repos
register` in Phase 4-5. `maury agency validate`'s hard-failure
rule ships with the validator command per
[ADR-0040](0040-render-pipeline.md).

## Followups

- **`agency_id` discovery from the URL alone.** A user
  inspecting `git@github.com:acme-corp/maury-base.git` from
  outside the agency can clone and `jq .agency_id < .meta/maury-marker.json`
  to read it. A `maury repo describe <url>` convenience command
  is an open followup.
- **Inter-agency rules subscription analytics.** Once
  cross-agency rules consumption is common in practice, knowing
  which agencies subscribe to a given rules repo is useful
  data for the rules owner. Out of scope for v1; surfaced
  through git access logs or platform analytics in the
  meantime.

## Claude Code references

This sub-ADR introduces no new Claude Code dependencies. Agency
identity is a maury-internal concept that does not interact
with Claude Code's surfaces.

## Amendment history

- 2026-05-18 — initial publication. Split out of
  [ADR-0037](0037-layer-taxonomy-and-repo-discovery.md) as the
  agency-identity layer of the layer-and-discovery design.
