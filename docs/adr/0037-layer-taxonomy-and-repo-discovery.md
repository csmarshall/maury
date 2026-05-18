# ADR-0037: Layer taxonomy and repo discovery (umbrella)

**Status:** Accepted (umbrella; details split into sub-ADRs 2026-05-18)
**Date:** 2026-05-08

> **Umbrella ADR.** The layer-taxonomy + repo-discovery design
> is large enough that the body lives in three sub-ADRs, each
> owning one layer of the design:
>
> - [ADR-0049 — Three-layer taxonomy and configuration dimensions](0049-layer-taxonomy.md):
>   the two-dimensional WHAT-vs-WHERE/HOW framing, the three
>   layer types (`base`/`mode`/`rules`), why no environment or
>   machine layer types, convention/precept as an access
>   relationship, render order + unified tiebreaker, override
>   advisory, render-time provenance, layer graduation.
> - [ADR-0050 — Agency identity and boundary](0050-agency-identity.md):
>   the agency concept, `agency_id` UUID semantics (membership
>   claim on `base`/`mode`, provenance claim on `rules`),
>   hard-error vs informational mismatch behavior, base layer
>   uniqueness, `--replace --i-am-sure`.
> - [ADR-0051 — Marker file and distributed manifest](0051-marker-file-and-distributed-manifest.md):
>   `.meta/maury-marker.json` as a committed file (not a git
>   tag), the per-layer schema, distributed manifest properties,
>   discovery contract, CLI surface, migration.
>
> This ADR carries the cross-cutting framing: the problem
> statement that motivates all three sub-ADRs, the
> non-negotiables that any one of them could otherwise quietly
> violate, the consequences that span all three, and the
> navigation. When a reader needs to know one layer in depth,
> jump to the sub-ADR; when a reader needs the integrated story,
> stay here.

## Related tenets

- [Tenet 1 — First, do no harm](../tenets.md#1-first-do-no-harm)
- [Tenet 2 — Consistency within a mode; controlled difference across modes](../tenets.md#2-consistency-within-a-mode-controlled-difference-across-modes)
- [Tenet 5 — The user arbitrates ambiguity](../tenets.md#5-the-user-arbitrates-ambiguity)
- [Tenet 6 — Identity is not name](../tenets.md#6-identity-is-not-name)
- [Tenet 8 — Hand-edits are first-class input](../tenets.md#8-hand-edits-are-first-class-input)
- [Tenet 10 — Modularity over hardcoding](../tenets.md#10-modularity-over-hardcoding)
- [Tenet 11 — Explicit beats implicit, with conservative defaults](../tenets.md#11-explicit-beats-implicit-with-conservative-defaults)

## Related ADRs

- [ADR-0015](0015-surrogate-keys-for-hosts-and-profiles.md) —
  URL is the surrogate key for repos; `host_<hex>` IDs; the
  agency's surrogate key for the WHAT dimension is `mode_<hex>`
- [ADR-0016](0016-pluggable-repo-backends.md) — backend
  pluralism; no platform-specific affordances
- [ADR-0017](0017-drift-detection-and-reconciliation.md) —
  drift detection; render engine companion
- [ADR-0030](0030-manifest-schema-migrations.md) — when
  manifest schema bumps are required
- [ADR-0033](0033-pr-repo-mode.md) — `pr` repo mode; PR
  contribution side-channel
- [ADR-0038](0038-precept-acquisition-model.md) — rules
  acquisition model; governance metadata; advisory dismissal
  storage
- [ADR-0039](0039-bootstrap-and-host-lifecycle.md) — bootstrap
  flow; mode-scoped host identity; mode change process; agency
  init
- [ADR-0040](0040-render-pipeline.md) — render pipeline
  umbrella; `maury agency validate` flags list
- [ADR-0049](0049-layer-taxonomy.md) — sub-ADR carrying the
  taxonomy + render-order layer
- [ADR-0050](0050-agency-identity.md) — sub-ADR carrying the
  agency-identity layer
- [ADR-0051](0051-marker-file-and-distributed-manifest.md) —
  sub-ADR carrying the marker-file + distributed-manifest layer

## TL;DR

Maury's layer taxonomy is exactly three types: **`base`** (the
agency-wide root, ×1), **`mode`** (what the user is doing — a
nested tree below base), and **`rules`** (shareable
conventions/precepts — floats anywhere). Each repo declares its
type, its agency, and its **direct sublayers** in
`.meta/maury-marker.json` — there is no flat agency-wide
registry. The agency is bounded by **`agency_id`** (UUID) and
discovered by walking the marker graph from base outward.
Environment and machine-specific content is content inside a
layer (env-tagged and host-tagged sections), not a separate
layer type. Trade-off: a stranger looking at a single repo
cannot infer the full agency topology without walking the
graph; this is the price of locality-of-change and distributed
authorship.

Details for each layer of the design live in the three
sub-ADRs — [ADR-0049](0049-layer-taxonomy.md) for the
taxonomy, [ADR-0050](0050-agency-identity.md) for agency
identity, and
[ADR-0051](0051-marker-file-and-distributed-manifest.md) for
the marker file. This ADR is the umbrella entry point.

## Context

Maury manages a collection of git repositories that together
render a single CLAUDE.md for any host the user works on. Two
questions sit at the foundation of the design:

1. **What kinds of repos does a maury installation contain,
   and how do they relate to each other?** Without a clear
   taxonomy, every repo is a special case and the render
   engine has no general semantics to apply. **Answered in
   [ADR-0049](0049-layer-taxonomy.md).**
2. **How does maury discover which repos belong to an
   installation, and how does a curator looking at a repo know
   what role it plays?** Without a discovery story, agency
   membership is implicit and a stranger inspecting a repo
   cannot tell whether it is maury-managed. **Answered jointly
   by [ADR-0050](0050-agency-identity.md) (what is the bounded
   identity?) and
   [ADR-0051](0051-marker-file-and-distributed-manifest.md)
   (how is it recorded?).**

Both questions must be answered in a way that satisfies several
non-negotiables that every sub-ADR must honor:

- **Backend pluralism (Tenet 10 /
  [ADR-0016](0016-pluggable-repo-backends.md)).** Every
  mechanism must work on every git-compatible backend. No
  GitHub-only, GitLab-only, or other platform-specific
  surfaces.
- **Locality of change.** Adding a rules repo to one work mode
  should not require editing a global registry. Curators of
  distinct modes should not gate-keep each other.
- **Stable identity (Tenet 6).** The set of repos and hosts
  maury manages — call it the **agency** — must have an
  identity that survives renames, URL changes, and re-homing
  of individual repos.
- **Off-network provenance.** A curator who clones a repo with
  no other context should be able to tell that it is
  maury-managed and what role it plays.
- **Maintenance surface minimization.** Anything documented in
  the spec accrues support burden. Universal mechanisms
  (committed files, manifest fields) only.

Each sub-ADR carries its own decision-drivers list, considered
options, decision outcome, and consequences. This umbrella ADR
carries the cross-cutting consequences and the navigation.

## Cross-cutting consequences

The three sub-ADRs each enumerate consequences in their own
domain (`Consequences` section of each); the ones that span
all three live here:

- ✅ **Good:** Layer taxonomy (ADR-0049), agency identity
  (ADR-0050), and marker file (ADR-0051) compose into a
  complete answer to "what kind of thing is this repo, what
  installation does it belong to, and what does it claim?" —
  three orthogonal questions, three sub-ADRs.
- ✅ **Good:** Off-network provenance holds end-to-end. A
  curator clones a repo, sees `.meta/maury-marker.json`, reads
  the `layer` field (ADR-0051), reads the `agency_id` field
  (ADR-0050), looks at the `sublayers` list, and understands
  the repo's role without external context.
- ✅ **Good:** No platform-specific affordances. Backend
  pluralism (ADR-0016) is preserved across all three sub-ADRs.
- ✅ **Good:** The render engine has a complete contract: layer
  type + cascade rules (ADR-0049), per-layer manifest
  (ADR-0051), and bounded agency (ADR-0050). The render
  pipeline ([ADR-0040](0040-render-pipeline.md)) builds on
  this contract.
- ⚖️ **Neutral:** Three separate `.meta/*.json` files across
  the layer types (`maury-marker.json` here, plus
  `maury-governance.json` and `maury-advisory-state.json` per
  [ADR-0038](0038-precept-acquisition-model.md)). One per
  concern, one place to look.
- ❌ **Bad:** Agency-wide topology requires graph traversal
  rather than reading a single file. Acceptable for
  agency-sized installations; documented in
  [ADR-0051](0051-marker-file-and-distributed-manifest.md).

## `maury agency validate`

The validator command operates on the marker graph that this
umbrella's three sub-ADRs define. The full flags list and
hard-failure/warning/informational categorization lives in
[ADR-0040 §"`maury agency validate`"](0040-render-pipeline.md#maury-agency-validate)
— ADR-0040 owns the command because it spans the layer/agency
contract here plus the render-pipeline concerns (filename
conventions, agent-name collisions) that the sub-ADRs there
add.

Validations originating from this umbrella's contract (see
[ADR-0040](0040-render-pipeline.md) for the full flag list
including the agent-name-collision and canonical-tag-sort
warnings that come from the render pipeline):

- Two repos with `layer: base` and the same `agency_id` →
  **hard failure** (per [ADR-0050](0050-agency-identity.md)).
- Sublayer URLs that resolve to repos missing
  `.meta/maury-marker.json` → warning (per
  [ADR-0051](0051-marker-file-and-distributed-manifest.md)).
- `mode` sublayer entries carrying a `repo_mode` field →
  warning (per
  [ADR-0051](0051-marker-file-and-distributed-manifest.md)).
- `rules` sublayer entries with no `repo_mode` → warning (per
  [ADR-0049](0049-layer-taxonomy.md)'s advisory model).
- Marker files using legacy `profile_<hex>` format in scope
  fields → warning (per
  [ADR-0030](0030-manifest-schema-migrations.md)).
- Cycles in the sublayer graph → informational (per
  [ADR-0049](0049-layer-taxonomy.md)'s tiebreaker rule;
  cycles resolve deterministically via latest-commit).
- `rules` repos with no semver tags → informational (per
  [ADR-0038](0038-precept-acquisition-model.md)).

## Build-order placement

The three sub-ADRs each ship their slice:

- **ADR-0049 (taxonomy).** Render-engine semantics ship when
  the first render pipeline lands (Phase 4-5 per ADR-0040).
- **ADR-0050 (agency identity).** `agency_id` generation ships
  in Phase 4 (`maury agency init` per ADR-0039); membership-
  mismatch checks ship with `maury repos register`.
- **ADR-0051 (marker + manifest).** Marker file ships in
  Phase 4 (alongside `maury agency init`); full CLI surface
  spans Phase 4-5.

`maury agency validate` ships with the validator command per
[ADR-0040](0040-render-pipeline.md).

## Open followups

The sub-ADRs each enumerate followups in their domain. The
cross-cutting ones:

- **Mode-tree visualization.** A `maury mode tree` command
  that prints the mode hierarchy with the active mode
  highlighted (under consideration in
  [ADR-0039](0039-bootstrap-and-host-lifecycle.md)).
- **`mode_<hex>` schema migration.** The rename from
  `profile_<hex>` to `mode_<hex>` is a breaking schema change
  spanning all three sub-ADRs (taxonomy uses the new name,
  marker schema needs the version bump, agency-init flow
  produces the new format); a migration path per
  [ADR-0030](0030-manifest-schema-migrations.md) must be
  designed and implemented before existing installations
  adopt this terminology update.

## Claude Code references

Detailed Claude Code citations live in the relevant sub-ADR:

- [Claude Code memory][cc-memory] — the "advise, do not block"
  principle is cited in [ADR-0049](0049-layer-taxonomy.md)
  under "Precepts are prescriptive, not enforceable."

This umbrella ADR makes no Claude Code behavior claims beyond
that reference.

[cc-memory]: https://code.claude.com/docs/en/memory

## Amendment history

- 2026-05-18 — split into three sub-ADRs: layer taxonomy
  ([ADR-0049](0049-layer-taxonomy.md)), agency identity
  ([ADR-0050](0050-agency-identity.md)), marker file and
  distributed manifest
  ([ADR-0051](0051-marker-file-and-distributed-manifest.md)).
  This ADR becomes the umbrella, keeping cross-cutting
  framing (non-negotiables, consequences, navigation). No
  semantic changes — the design was already decomposable; the
  split makes the decomposition explicit.
- 2026-05-13 — this ADR's `rules` layer + `repo_mode` model
  formally supersedes the publish/subscribe **mechanics** of
  [ADR-0034](0034-published-subscribed-profiles.md) (the
  `extends`-based inheritance + `subscribe` mental model).
  ADR-0034's use-case framing (curator publishes shared
  content; engineers consume + contribute back via PR) is
  preserved as the product story; the mechanics live here and
  in [ADR-0038](0038-precept-acquisition-model.md).
