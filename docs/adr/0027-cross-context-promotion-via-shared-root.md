# ADR-0027: Cross-context promotion via shared inheritance root

**Status:** Accepted
**Date:** 2026-05-07

## Related tenets

- [Tenet 1 — First, do no harm](../tenets.md#1-first-do-no-harm)
- [Tenet 3 — Trust boundaries are physical](../tenets.md#3-trust-boundaries-are-physical-not-policy)
- [Tenet 5 — The user arbitrates ambiguity](../tenets.md#5-the-user-arbitrates-ambiguity)

## Context

[ADR-0009](0009-promotion-only-cross-boundary.md) established
the *mechanics* of cross-trust-boundary promotion: how a finding
from one repo gets reviewed and applied to another (proposal
queue, curator review, audit trail).

[ADR-0026](0026-profile-aware-mining.md) established that
findings carry a `Source-Profile` trailer recording which profile
they came from.

What's still undefined: **which target profiles are valid
destinations for a finding from profile A?** ADR-0009's
mechanics don't constrain this — they apply equally to any
cross-boundary write the curator approves. A naive reading would
allow a finding from `personal` to be promoted to `acme-client`
directly, even though `personal` and `acme-client` are
unrelated trust contexts.

The constraint maury actually wants is: **promotion can only
flow along edges of the inheritance graph.** A finding from A
can land in A itself, A's parent, A's grandparent, ..., up to
the root (`base`). It cannot land in A's siblings or unrelated
profiles. That follows from the user's mental model
(inheritance is the trust graph) and from concepts.md §6 (the
inheritance access mode is the universal vocabulary for
child-forebearer interaction).

## Decision

### The promotion graph IS the inheritance graph

A finding produced under `Source-Profile: A` can be promoted to:

- **A itself** — no promotion; the proposal lands in A's repo
  on A's branch.
- **Any ancestor of A** in A's inheritance chain — A's parent,
  A's grandparent, ..., up to the root profile.

It **cannot** be promoted to:

- **Siblings of A** (profiles that share a parent with A but
  aren't ancestors of A).
- **Unrelated profiles** (profiles in different trees or
  different roots).

To get content from `acme-client` to `globex-client` (siblings
under `work` per concepts.md §3 example), the only legal path is:

1. Promote from `acme-client` to `work` (their shared parent).
2. The render engine flows `work`'s content to `globex-client`
   via standard inheritance ([ADR-0019](0019-inheritance-semantics-refine-by-default.md)).

To get content from `personal` to `work` (siblings under `base`):

1. Promote from `personal` to `base` (their shared root).
2. `base` flows to both via inheritance.

### Why this constraint

Two reasons:

**1. Tenet 3 (trust boundaries are physical).** If profile A and
profile B are siblings rather than parent/child, they're
deliberately separated — the user organized the inheritance
graph that way to express "these contexts shouldn't see each
other's content directly." Lateral promotion would silently
violate that organization. Going through the shared parent
forces the user to think about *what content is shared
fundamentally* vs *what's specific to one context.*

**2. Render-time consistency.** The render engine
([ADR-0019](0019-inheritance-semantics-refine-by-default.md))
walks the inheritance chain from root to leaf. Content can only
*reach* a profile via that walk. If promotion landed content in
a sibling, the render engine would still apply it correctly —
but the user's mental model of "what content reaches this
profile?" would diverge from the inheritance graph. Now you'd
have to know about both the extends edges AND the
once-promoted-laterally edges. That's exactly the kind of
hidden state the design avoids.

### Validation points

The constraint is enforced at two points in the proposal
lifecycle:

**At mining time** — when a finding is written to the staging
file (per [ADR-0013 amendment](0013-active-in-session-capture.md))
or committed to a run branch (per [ADR-0022](0022-branch-per-mining-run.md)),
the `scope_hint` value is validated:

```
scope_hint must be either:
  - "current"                                 (the source profile)
  - the source profile's name/id              (same as current)
  - any ancestor of the source profile        (per the manifest)
  - "base"                                    (always valid)
```

If a `scope_hint` violates this, maury refuses to stage/commit
with a specific error: *"scope_hint='globex-client' is not a
valid destination for a finding from 'acme-client' — they are
siblings, not in an ancestor relationship. Promote to 'work'
(shared parent) or 'base' (shared root) instead."*

> **Host-scoped captures are out of scope for this graph check.**
> Per [ADR-0013](0013-active-in-session-capture.md) §"Active-
> context file", `scope_hint` may also take a host name (e.g.,
> `<host>` for content that should land only in this specific
> host's overlay). Hosts aren't in the inheritance graph — they
> attach to a single profile per ADR-0001. So host-scope hints
> bypass this ADR's check entirely; the host overlay is always
> a valid destination for the host that owns it.

**At review time** — when the curator runs `maury review` per
ADR-0022, accepted findings cherry-pick onto a review branch in
the target repo. The cherry-pick destination is constrained to
the same set: source-profile and ancestors. The curator's UI
shows valid targets only.

### Interaction with `pr` mode and trust boundaries

[ADR-0009](0009-promotion-only-cross-boundary.md) handles the
*mechanics* of cross-trust-boundary writes (curator host, deploy
keys, audit). [concepts.md §6](../concepts.md#6-inheritance-access-mode)
defines the access mode (`ro` / `pr` / `rw`) on each repo.

Both compose with this ADR's graph constraint:

| Promotion path | Graph constraint | Trust-boundary mechanics |
|---|---|---|
| Same repo, ancestor target | Allowed | Local cherry-pick or PR per repo's mode |
| Cross-repo, ancestor target | Allowed | Cross-boundary promotion per ADR-0009 |
| Same repo, sibling target | **Refused** | N/A (graph rejects before mechanics) |
| Cross-repo, sibling target | **Refused** | N/A |
| Cross-repo, unrelated target | **Refused** | N/A |

The graph check happens first. If the target is graph-illegal,
the trust-boundary mechanics aren't even consulted.

### Implementation

The check is a function of the manifest:

```python
def is_valid_promotion_target(
    *,
    source_profile_id: str,
    target_profile_id: str,
    manifest: Manifest,
) -> bool:
    if source_profile_id not in manifest.profiles:
        raise UnknownProfileError(source_profile_id)
    if target_profile_id not in manifest.profiles:
        raise UnknownProfileError(target_profile_id)
    if source_profile_id == target_profile_id:
        return True
    if target_profile_id == BASE_PROFILE_ID:
        return True
    for ancestor in inheritance_chain(source_profile_id, manifest):
        if ancestor == target_profile_id:
            return True
    return False
```

Contract:

- **Both `source_profile_id` and `target_profile_id` MUST exist in
  the manifest.** Unknown profile IDs raise `UnknownProfileError`
  rather than returning `False` — a typo'd profile name is a
  programming error, not a "no, that's not allowed" answer.
- **`BASE_PROFILE_ID` MUST be present in the manifest.** The
  manifest validator (per [ADR-0015](0015-surrogate-keys-for-hosts-and-profiles.md))
  guarantees this. If `BASE_PROFILE_ID` is missing, treat as a
  manifest-integrity error and refuse all promotion until fixed.
- **`inheritance_chain` returns the source's ancestors root-ward**
  (not including the source itself). Per
  [ADR-0001](0001-n-profiles.md), single-parent inheritance keeps
  this linear: walk `extends:` recursively until null.

**Lowest common ancestor (LCA):** when an error message wants to
suggest "did you mean their shared parent X?", maury computes
LCA of source and intended-target by walking both chains
root-ward and finding the deepest common node. Single-parent
inheritance makes both chains linear, so LCA is `O(depth_a +
depth_b)`. In practice depth ≤ 3, so this is effectively
constant.

Lives in `src/maury/promotion/graph.py`. Called from:

- `maury mine` (when staging-file write happens with an explicit
  scope_hint)
- `maury review` (when accepting a finding into a review branch)
- `maury promote --from --to` (when curator cross-promotes)

## Consequences

- **The user's organizational mental model is enforced.** If you
  put `personal` and `work` as siblings under `base`, lateral
  leakage between them is structurally impossible without first
  surfacing the shared content in `base`.
- **Promoting to base becomes the explicit "this applies
  everywhere" gesture.** That's the right framing — base content
  flows to all descendants, so it should require deliberate
  promotion.
- **A common workflow becomes explicit:** "I learned X in profile
  A and want it in B" requires the user to think *"what is the
  shared parent that should also have X?"*. That's not
  friction-for-its-own-sake; it's exactly the
  refinement-vs-replacement question
  [ADR-0019](0019-inheritance-semantics-refine-by-default.md)
  already asks.
- **The promotion graph is auditable from the manifest alone.**
  No special "promotion permissions" table — the inheritance
  graph is the only source of truth.
- **Lowest-common-ancestor (LCA) lookup is needed.** A small
  utility function on the manifest. Trivial implementation;
  bounded by the depth of the inheritance chain (typically ≤ 3
  in practice).
- **Single-parent inheritance from
  [ADR-0001](0001-n-profiles.md) keeps this clean.** A multi-
  parent (diamond) inheritance graph would make LCA lookup
  ambiguous — you'd have to choose which parent's lineage to
  promote through. ADR-0001's single-parent rule sidesteps the
  question entirely.

## Alternatives considered

- **Allow lateral promotion with explicit user confirmation.**
  Rejected: violates tenet 3 (the user organized the graph to
  separate these contexts) and creates hidden state (which
  pairs of profiles have once-promoted-laterally edges?).
- **Allow promotion to any profile; let `forbid` rules from
  ADR-0004 catch unsafe content.** Rejected: forbid rules are a
  redaction layer for *content*, not a structural constraint
  for *destinations*. Defense-in-depth means both layers; this
  ADR adds the structural layer.
- **Define "promotion permissions" as a separate table in the
  manifest.** Rejected: introduces a parallel structure that
  must stay synced with the inheritance graph. The graph is
  already the source of truth; reuse it.
- **Allow lateral promotion if both profiles share a common
  ancestor at any depth.** Considered. This is what the LCA
  resolution fundamentally does, but the *target* is the
  ancestor, not the sibling. Lateral-with-LCA would still let
  content land in the sibling rather than at the ancestor —
  which is the leakage we're preventing.
- **Use a directed acyclic graph (DAG) of explicit promotion
  edges, separate from inheritance.** Rejected: see "single-
  parent inheritance keeps this clean" — multi-parent would
  reintroduce the problem, and the user explicitly chose
  single-parent in ADR-0001.

## Build-order placement

Phase 9 — Promotion flow. Lands as part of the cross-boundary
promotion command (`maury promote --from --to`). The graph-check
function (`is_valid_promotion_target`) is small enough to land
earlier — in Phase 6 (mining) or Phase 7 (review) — alongside the
scope_hint validation at staging-write time. Implementation order:

1. **`is_valid_promotion_target`** + LCA lookup in
   `src/maury/promotion/graph.py` — Phase 6/7 (whenever the
   first scope_hint check needs it).
2. **scope_hint validation at staging write** — Phase 6 (mining
   pipeline).
3. **Review-time validation** — Phase 7 (review UI).
4. **Cross-promotion command** — Phase 9.

## Followups

- **Helpful "did you mean?" suggestions.** When validation
  refuses a promotion, surface the lowest common ancestor of
  source and intended-target as a suggestion: *"You wanted
  acme-client → globex-client; their shared ancestor is `work`.
  Did you mean to promote to `work`?"*
- **Manifest validation.** The `maury manifest validate`
  command should also walk all rules with explicit profile
  scopes and verify each scope is valid for the rule's source
  profile. Catches malformed YAML rules statically rather than
  at proposal time.
- **What if the user reorganizes the inheritance graph after
  rules already exist?** A rule pinned to `work` flows to
  `acme-client` via inheritance. If `acme-client` is later
  re-parented to a different profile, that rule no longer
  applies. v1.1 design: a `maury profile reparent <name> --new-
  parent <name>` command (the only sanctioned way to change a
  profile's `extends:`) invokes the manifest validator's "drift
  check" pass — walks all profile-scoped rules in the affected
  subtree, identifies rules whose source-profile no longer has
  the target as an ancestor, and surfaces a list to the user
  with options: leave (rule becomes inert), promote-up
  (re-pin to the new shared ancestor with the rule's actual
  destinations), or delete. Until `maury profile reparent`
  exists, manual `extends:` edits in the manifest are technically
  possible but strongly discouraged because no drift-check fires.

## Claude Code references

This ADR introduces no new Claude Code dependencies beyond what
ADR-0009 and ADR-0026 already cite. No new contract entries
needed.
