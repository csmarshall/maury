# ADR-0045: Cross-trust-boundary promotion

**Status:** Accepted
**Date:** 2026-05-18
**Supersedes:** [ADR-0009](0009-promotion-only-cross-boundary.md), [ADR-0027](0027-cross-context-promotion-via-shared-root.md)

## Related tenets

- [Tenet 1 — First, do no harm](../tenets.md#1-first-do-no-harm) — silent cross-boundary content moves are the canonical Tenet 1 failure mode.
- [Tenet 3 — Trust boundaries are physical, not policy](../tenets.md#3-trust-boundaries-are-physical-not-policy) — both pieces of this ADR (mechanics + graph constraint) operationalize Tenet 3 at different layers.
- [Tenet 5 — The user arbitrates ambiguity](../tenets.md#5-the-user-arbitrates-ambiguity) — the curator review step is exactly this principle applied to cross-boundary writes.
- [Tenet 7 — Provenance is mandatory](../tenets.md#7-provenance-is-mandatory) — every promotion carries an audit trail; the source-mode metadata travels with the content.

## TL;DR

A finding mined under one mode-registration sometimes belongs in
a different one — most commonly, a work host surfaces a
universal preference that should live in `base`. This ADR
specifies how that movement happens *safely*, combining two
constraints that previously lived in separate ADRs (now
superseded):

1. **The mechanics: promotion-only, curator-mediated, no
   source-host feedback** (the previous ADR-0009). A host with
   read-only access to a destination repo writes a proposal to
   its own repo's `proposals/promote-to-<dest>/` queue. Any host
   with rw access to both source and destination can run
   `maury promote-review` to inspect and copy approved proposals
   across. Cross-boundary writes never bypass the curator.
2. **The graph constraint: promotion follows inheritance edges
   only** (the previous ADR-0027). A finding from mode `A` can
   only land in `A` itself or any of its ancestors up to `base`.
   Lateral promotion to a sibling is structurally refused — to
   move content between siblings, promote to their shared
   ancestor and let inheritance flow it down.

Together: trust boundaries hold (mechanics), the promotion graph
is the inheritance graph (constraint), and the user's
organizational choices on the mode tree are enforced by the
graph itself rather than curator discretion.

## Context and Problem Statement

[ADR-0002](0002-repo-per-trust-boundary.md) and
[ADR-0003](0003-per-host-deploy-keys.md) establish that trust
boundaries are server-side (per-repo deploy keys; a work host
with `ro` access to `base` cannot push there). Mining
([ADR-0005](0005-local-only-mining.md),
[ADR-0008](0008-claude-diary-reference.md)) can produce findings
that genuinely belong on the other side of that boundary —
"prefer terse summaries with no trailing recap" is the canonical
example: it surfaced in a work session but is a universal
preference that should land in `base`.

Two distinct questions to answer:

- **HOW does the content move across the boundary?** A work
  host can't push to `base`. Naïvely, that means useful insights
  are stuck. We need a path that preserves the boundary while
  still letting the content flow.
- **WHERE in the graph can the content land?** Once we have a
  movement path, what's a valid destination? Can a finding from
  `personal` land in `acme-client`? In `globex-client`? In
  `base`? The mechanics-layer ADR doesn't constrain this; the
  graph-layer constraint does.

This ADR consolidates both into one design. Prior versions
([ADR-0009](0009-promotion-only-cross-boundary.md) for
mechanics, [ADR-0027](0027-cross-context-promotion-via-shared-root.md)
for the graph constraint) addressed them separately, with ADR-0027
explicitly citing ADR-0009's mechanics as a foundation. The two
ADRs were always read as one design; they're now consolidated as
ADR-0045 to reflect that and to prevent future readers from
applying one layer without the other.

## Decision Drivers

- **Tenet 1.** Cross-boundary writes that bypass user review can
  silently leak context-specific content into a shared layer.
- **Tenet 3.** Read/write asymmetry is the trust-boundary
  primitive — both the mechanics (curator-mediated writes) and
  the graph (inheritance-edge-only promotion) must hold even
  when insights legitimately need to flow.
- **Tenet 5.** The user arbitrates ambiguity — the curator step
  is where the user decides what crosses.
- **Tenet 7.** Provenance is mandatory — promotion carries a
  trace of who promoted what from where, source mode included.
- **The user's organizational mental model is load-bearing.**
  If you put `personal` and `work` as siblings under `base`,
  that organization is itself a statement about what shouldn't
  see what. The graph constraint enforces the organization
  structurally rather than asking the curator to remember it.
- **A compromised work host should not be able to silently
  inject content** into a shared layer (mechanics requirement).
- **The render engine walks inheritance edges** ([ADR-0019](0019-inheritance-semantics-refine-by-default.md));
  promotion crossing edges that aren't in the inheritance graph
  would create hidden state ("what content reaches this mode?"
  would no longer be answerable from the inheritance graph alone).

## Considered Options

For the mechanics layer (how the content moves):

- **M-A:** Direct cross-repo writes from work hosts.
- **M-B:** Grant work hosts write access to base.
- **M-C:** Implicit promotion via maury's own logic — tool
  decides when to promote.
- **M-D (chosen):** Promotion-only flow — host writes to a
  proposal queue in its own repo; curator with rw on both runs
  `maury promote-review`.

For the graph layer (where it can land):

- **G-A:** No graph constraint — any cross-mode promotion that
  the curator approves is valid. (The behavior ADR-0009 alone
  would have permitted.)
- **G-B:** Lateral promotion allowed with explicit user
  confirmation per move.
- **G-C:** A separate "promotion permissions" table in the
  manifest.
- **G-D (chosen):** Promotion follows inheritance edges only;
  destination must be the source mode itself or an ancestor.

## Decision Outcome

**Chosen:** M-D + G-D, composed.

The mechanics layer ensures every cross-boundary write goes
through curator review — a host can only push to repos it has
write access to (enforced server-side by deploy-key
permissions), so cross-boundary content moves through a
human-mediated proposal queue, never as a direct write.

The graph layer constrains where the curator can land the
content — promotion can only flow along edges of the
inheritance graph. The graph constraint is checked *before* the
mechanics fire: a graph-illegal promotion is refused at proposal-
write time without ever reaching the curator.

The two together preserve Tenet 3 at both layers. The graph
makes "where can this leak?" provably bounded by the inheritance
graph alone; the mechanics make "who decides if it does?" a
human curator with the right two deploy keys.

### Implementation details

#### 1. The proposal queue

When a fragment classified for destination D is produced on a
host without rw access to D's repo, maury writes it to that
host's own repo as `proposals/promote-to-<D>/<id>.md`. The
proposal carries:

- The fragment text + classification.
- The source mode (`Source-Mode` trailer, per
  [ADR-0026](0026-profile-aware-mining.md)).
- The source host id (per [ADR-0015](0015-surrogate-keys-for-hosts-and-profiles.md)).
- A timestamp.

#### 2. The graph constraint (`is_valid_promotion_target`)

A finding under `Source-Mode: A` can be promoted to:

- **A itself** — no promotion; lands in A's repo on A's branch.
- **Any ancestor of A** in A's inheritance chain — A's parent,
  A's grandparent, ..., up to the root mode.

It **cannot** be promoted to:

- **Siblings of A** (modes that share a parent with A but aren't
  ancestors of A).
- **Unrelated modes** (modes in different trees or different roots).

To move content from `acme-client` to `globex-client` (siblings
under `work`), the only legal path is to promote first to `work`
(their shared parent); the render engine then flows that
content to `globex-client` via standard inheritance per
[ADR-0019](0019-inheritance-semantics-refine-by-default.md).

The check is a small function on the manifest:

```python
def is_valid_promotion_target(
    *,
    source_mode_id: str,
    target_mode_id: str,
    manifest: Manifest,
) -> bool:
    if source_mode_id not in manifest.modes:
        raise UnknownModeError(source_mode_id)
    if target_mode_id not in manifest.modes:
        raise UnknownModeError(target_mode_id)
    if source_mode_id == target_mode_id:
        return True
    if target_mode_id == BASE_MODE_ID:
        return True
    for ancestor in inheritance_chain(source_mode_id, manifest):
        if ancestor == target_mode_id:
            return True
    return False
```

Contract:

- **Both ids MUST exist in the manifest.** Unknown ids raise
  `UnknownModeError` rather than returning `False` — a typo is a
  programming error, not a "no, that's not allowed" answer.
- **`BASE_MODE_ID` MUST be present.** Manifest validator (per
  [ADR-0015](0015-surrogate-keys-for-hosts-and-profiles.md))
  guarantees this; if missing, treat as manifest-integrity
  error and refuse all promotion until fixed.
- **`inheritance_chain` returns ancestors root-ward**, not
  including the source itself. Per [ADR-0001](0001-n-profiles.md),
  single-parent inheritance keeps this linear.

Lives in `src/maury/promotion/graph.py`.

#### 3. Validation points

The graph constraint fires at three points:

**At staging-write time** — when a finding is committed to a
run-branch ([ADR-0022](0022-branch-per-mining-run.md)) or
written to the active-capture staging file
([ADR-0013](0013-active-in-session-capture.md)), the
`scope_hint` is validated:

```
scope_hint must be either:
  - "current"                                 (the source mode)
  - the source mode's name/id                 (same as current)
  - any ancestor of the source mode           (per the manifest)
  - "base"                                    (always valid)
  - a host-id                                 (host-tagged section
                                               of the host's own
                                               registered mode;
                                               bypasses the graph
                                               check, per the host-
                                               scope carve-out below)
```

If a `scope_hint` violates this, maury refuses with a specific
error: *"scope_hint='globex-client' is not a valid destination
for a finding from 'acme-client' — they are siblings, not in an
ancestor relationship. Promote to 'work' (shared parent) or
'base' (shared root) instead."*

> **Host-scoped captures bypass the graph check.** Per
> [ADR-0013 §"Active-context file"](0013-active-in-session-capture.md#active-context-file--what-claude-needs-to-know),
> `scope_hint` may take a host name (e.g., `<host>` for
> content that lands only in this host's host-tagged section).
> Hosts aren't in the mode tree — they register against a single
> mode per [ADR-0039](0039-bootstrap-and-host-lifecycle.md). A
> host's host-tagged section inside its registered mode is
> always a valid destination for that host's content.

**At review time** — when the curator runs `maury review`
([ADR-0022](0022-branch-per-mining-run.md)), accepted findings
cherry-pick onto a review branch in the target repo. The
cherry-pick destination is constrained to the same set: source-
mode and ancestors. The curator's UI surfaces valid targets only.

**At cross-promotion time** — `maury promote-review` (the
mechanics-layer command from §4) checks the graph constraint
on each proposal it considers; a graph-illegal proposal in the
queue is surfaced as a "drop or fix" item rather than a "land
or land-with-modifications" choice.

#### 4. Curator workflow

Any host with rw access to **both** the source repo (read) and
the destination repo (write) runs `maury promote-review` to:

- Iterate the source repo's `proposals/promote-to-<dest>/`
  queue.
- For each proposal: surface the fragment + `Source-Mode` +
  source host id + the graph-validity check.
- Curator accepts, rejects, or modifies.
- On accept, maury copies the proposal across (commit with
  audit trail per [ADR-0035](0035-audit-log.md)).
- The accepted-proposal commit gets a `Promoted-From` trailer
  recording the source repo + proposal id for provenance.

#### 5. No source-host feedback channel

The proposing host has **no visibility** into whether its
proposal was accepted, rejected, or modified. This is by design:
feedback signals could leak content classification back to a
host that should not have visibility into the destination repo.
The cost is a UX trade-off (the proposing user doesn't know if
their finding landed); the gain is preserving the
unidirectionality of the trust boundary.

If the user wants confirmation, they look at the destination
repo directly (which they have read access to in the
common-case where they're the curator on a different machine).

#### 6. `push_policy: disabled` mode

For environments where producing proposals from a particular
host is policy-problematic (regulated workplaces, etc.), maury
supports a `push_policy: disabled` setting on that host. In this mode,
fragments classified for a destination the host can't reach are
written only to the local quarantine — never to git. The user
opts in deliberately; default is to write proposals (the
M-D flow).

#### 7. Interaction with `pr` repo mode

[ADR-0033](0033-pr-repo-mode.md) adds a third repo mode (`pr` —
contribute-via-pull-request) alongside `ro` and `rw`. The
mechanics-layer flow composes cleanly:

| Promotion path | Graph check | Mechanics |
|---|---|---|
| Same repo, ancestor target | Allowed | Local cherry-pick |
| Cross-repo, ancestor target, dest `rw` | Allowed | Direct cross-boundary promotion (§4) |
| Cross-repo, ancestor target, dest `pr` | Allowed | Promotion writes a PR; curator merges upstream |
| Cross-repo, ancestor target, dest `ro` | Allowed | Promotion writes to source's proposal queue; out-of-band curator |
| Same/cross-repo, sibling target | **Refused** | N/A (graph rejects before mechanics) |
| Same/cross-repo, unrelated target | **Refused** | N/A |

The graph check happens first. If the target is graph-illegal,
the trust-boundary mechanics aren't consulted.

#### 8. Lowest common ancestor (LCA) helper

When the graph check refuses a promotion and the curator (or
the staging-write error message) wants to suggest "did you mean
their shared parent?", maury computes LCA of source and
intended-target by walking both inheritance chains root-ward
and finding the deepest common node. Single-parent inheritance
([ADR-0001](0001-n-profiles.md)) makes both chains linear, so
LCA is `O(depth_a + depth_b)` — effectively constant since
depth ≤ 3 in practice.

### Consequences

- ✅ **Good:** Trust boundary holds at both layers. The work
  laptop cannot inject content into base without curator review
  (mechanics); the curator cannot leak between siblings even
  with good intent (graph).
- ✅ **Good:** The user's organizational mental model is
  enforced structurally. If you put `personal` and `work` as
  siblings under `base`, lateral leakage between them is
  refused without first surfacing the shared content in `base`.
- ✅ **Good:** The promotion graph is auditable from the
  manifest alone — no separate "promotion permissions" table to
  stay synced with the inheritance graph.
- ✅ **Good:** Promoting to base becomes the explicit "this
  applies everywhere" gesture, mirroring the
  refinement-vs-replacement framing [ADR-0019](0019-inheritance-semantics-refine-by-default.md)
  already asks.
- ✅ **Good:** Curator role follows repo access, not host
  identity ([ADR-0003](0003-per-host-deploy-keys.md)) — any
  host with the right two deploy keys can curate.
- ⚖️ **Neutral:** A common workflow becomes explicit: "I
  learned X in mode A and want it in B" requires the user to
  think "what shared parent should also have X?" — that's the
  Tenet 5 arbitration moment, not friction-for-its-own-sake.
- ⚖️ **Neutral:** `push_policy: disabled` is available for stricter
  environments where producing proposals at all is unwelcome.
- ❌ **Bad:** Adds latency between insight surfacing and
  insight landing in the target — the curator must run
  `maury promote-review` for the proposal to propagate.
- ❌ **Bad:** No feedback channel back to the source host.
  Intentional; documented in §5.

### Confirmation

- Manifest schema validates that hosts with `repo_mode: ro` on
  a destination repo route their finds through
  `proposals/promote-to-<dest>/` rather than direct write.
- `maury promote-review` (Phase 9) inspects the proposal queue
  and applies the curator's decisions with audit-log entries
  per [ADR-0035](0035-audit-log.md).
- `is_valid_promotion_target` lives in
  `src/maury/promotion/graph.py` and is unit-tested for the
  full matrix of source/target combinations (same-mode,
  ancestor, sibling, unrelated, base).
- `maury agency validate` (planned per
  [ADR-0040](0040-render-pipeline.md)) walks all rules with
  explicit mode scopes and verifies each scope is valid for
  the rule's source mode.
- LCA helper is unit-tested for single-parent chains and the
  manifest-integrity error case (`BASE_MODE_ID` missing).

## Pros and Cons of the Options

### Mechanics layer

#### M-A: Direct cross-repo writes from work hosts

- ✅ Lowest latency from insight to base update.
- ❌ Violates the data-isolation premise.
- ❌ A misclassification immediately leaks context-specific
  content into shared base.

#### M-B: Grant work hosts write access to base

- ✅ Simple — no proposal queue plumbing.
- ⚖️ Available as an opt-in deployment posture if the threat
  model allows. Even then, the promote-review pattern remains
  useful for catching accidental promotion.
- ❌ Rejected as default — same boundary-violation risk as M-A.

#### M-C: Implicit promotion via maury's own logic

- ✅ Fully automated; no curator step.
- ❌ Requires the tool to make trust decisions about content
  classification, which contradicts the rule-engine
  architecture ([ADR-0004](0004-rule-engine-classification.md)).
- ❌ A misclassified fragment would silently cross the boundary.

#### M-D (chosen): Promotion-only with curator review

- ✅ Trust boundary preserved; curator review catches
  misclassifications.
- ✅ Works with existing deploy-key access model
  ([ADR-0003](0003-per-host-deploy-keys.md)).
- ✅ Composes with G-D's graph constraint.
- ❌ Adds latency; requires curator action.
- ❌ No feedback channel back to source host — intentional, to
  prevent classification side-channels.

### Graph layer

#### G-A: No graph constraint

- ✅ Maximum flexibility for the curator.
- ❌ The curator alone bears the responsibility for not leaking
  laterally. Easy to slip.
- ❌ Decouples the promotion graph from the inheritance graph,
  creating hidden state ("what content reaches this mode?" is
  no longer answerable from inheritance alone).

#### G-B: Lateral with explicit user confirmation

- ✅ Preserves the curator's option to override.
- ❌ Still violates Tenet 3 — the user organized siblings
  separately *because* they shouldn't share directly. Override
  per-move erodes the organization over time.
- ❌ Hidden state: which pairs of modes have
  once-promoted-laterally edges?

#### G-C: Separate "promotion permissions" table

- ✅ Explicit; auditable.
- ❌ Introduces a parallel structure that must stay synced
  with the inheritance graph. The graph is already the source
  of truth; reuse it.

#### G-D (chosen): Inheritance-edge-only promotion

- ✅ The inheritance graph IS the promotion graph — one source
  of truth.
- ✅ "What content reaches this mode?" is answerable from
  inheritance alone, no hidden edges.
- ✅ Forces the user to think about *what's shared
  fundamentally* vs *what's specific to one context.*
- ⚖️ Sibling-to-sibling moves require an extra hop through the
  shared ancestor — but that's the explicit shared-content
  question, not friction.

## Build-order placement

Phase 9 (Promotion flow). Implementation order:

1. **`is_valid_promotion_target` + LCA helper** in
   `src/maury/promotion/graph.py` — Phase 6/7 (whenever the
   first scope_hint check needs it).
2. **scope_hint validation at staging write** — Phase 6
   (mining pipeline).
3. **Review-time validation** — Phase 7 (review UI).
4. **`maury promote-review` and `maury promote --from --to`** —
   Phase 9.
5. **Audit-log integration** — Phase 9, per
   [ADR-0035](0035-audit-log.md).

The proposal-queue layout (`proposals/promote-to-<dest>/`) is
established earlier (Phase 6/7 mining + review pipeline) since
fragments classified for unreachable destinations must have
somewhere to land before `maury promote-review` exists.

## Followups

- **Helpful "did you mean?" error messages.** When validation
  refuses a promotion, surface the LCA of source and intended-
  target as a suggestion: *"You wanted acme-client →
  globex-client; their shared ancestor is `work`. Did you mean
  to promote to `work`?"*
- **Mode-tree reparenting drift check.** If the user
  reorganizes the inheritance graph (`maury mode reparent`,
  planned per v1.1), the manifest validator should walk all
  mode-scoped rules in the affected subtree and identify rules
  whose source-mode no longer has the target as an ancestor.
  Surface to user with options: leave (rule becomes inert),
  promote-up (re-pin to the new shared ancestor), or delete.
  Until `mode reparent` exists, manual `extends:` edits in the
  manifest are technically possible but strongly discouraged
  because no drift-check fires.
- **Multi-parent inheritance.** Single-parent ([ADR-0001](0001-n-profiles.md))
  keeps the graph clean — a multi-parent (diamond) inheritance
  graph would make LCA ambiguous (which parent's lineage to
  promote through?). If multi-parent ever becomes a real ask,
  this ADR's LCA logic needs revisiting.

## Claude Code references

This ADR introduces no new Claude Code dependencies beyond
what [ADR-0026](0026-profile-aware-mining.md) and the inheritance
flow already cite. No new `cc-contract:*` entries needed.

## Amendment history

- 2026-05-18 — initial publication. Consolidates the mechanics
  layer (formerly [ADR-0009](0009-promotion-only-cross-boundary.md))
  and the graph constraint layer (formerly
  [ADR-0027](0027-cross-context-promotion-via-shared-root.md))
  into one design. Both source ADRs marked Superseded by this
  ADR. No semantic changes from the consolidation — the two
  layers were always read as one design; this consolidation
  reflects that operationally.

- 2026-05-22 — **implementation shipped (Phase 9).** Both mechanisms,
  graph-first:
  * **Graph constraint** — `src/maury/promotion/graph.py`:
    `is_valid_promotion_target` + `lowest_common_ancestor` +
    `base_mode_id`, exactly per §2/§8. Adapts to the live manifest API
    (ids are profile IDs; `inheritance_chain` is root-first inclusive so
    ancestors are `chain[:-1]`).
  * **Branch-fetch promotion** — `maury promote --from <src> --to <dest>
    --to-mode <name>` (`promotion/promote.py::promote_run`). Reads the
    source's `maury/run/*` findings read-only, graph-checks each, runs
    the ADR-0022 review loop, lands accepts on `maury/promoted/<id>` with
    a `Promoted-From: <src>@<sha>` trailer (the source message is
    *copied* — the SHA isn't reachable in the dest — so `Content-Hash` /
    `Source-Mode` ride through; reconstruct-and-append, not cherry-pick,
    matching the ADR-0022 review amendment).
  * **Proposal queue** — `proposals/promote-to-<dest>/<id>.md` written at
    mine time (`promotion/proposal.py`, wired into `maury mine
    --write-run-branch`) when a finding's destination is outside the
    host's writable subtree; `maury promote-review --from --to`
    (`promote_review_run`) walks the queue with the same loop. The
    "writable subtree" test is **graph-only** (`needs_promotion_proposal`:
    the host's mode must be an ancestor-or-self of the destination) — no
    separate repo-access-map is modeled; rw-on-own-subtree / ro-on-
    ancestors is the deploy-key reality (ADR-0003).
  * **Source mode** travels per-finding via the `Source-Mode` trailer
    (ADR-0026), resolved name→id against the manifest for the graph check.

  **V1 scope notes:**
  - **Proposals are committed on the run branch** (one trailing commit),
    not a dedicated branch/ref; the curator reads them from the source
    repo working tree. A future ADR may revisit the queue's commit/visibility
    semantics.
  - **`pr` repo-mode promotion (§7) is not implemented** — the code's
    `RepoMode` enum is `ro`/`rw` only; ADR-0033's `pr` mode isn't in the
    data model yet. The §7 matrix's pr-mode row is deferred.
  - **`push_policy: disabled` (§6) is not yet enforced** at proposal-write
    time. Deferred followup.
