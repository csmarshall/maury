# ADR-0049: Three-layer taxonomy and configuration dimensions

**Status:** Accepted
**Date:** 2026-05-18
**Sub-ADR of:** [ADR-0037](0037-layer-taxonomy-and-repo-discovery.md)

## Related tenets

- [Tenet 2 — Consistency within a mode; controlled difference across modes](../tenets.md#2-consistency-within-a-mode-controlled-difference-across-modes)
- [Tenet 5 — The user arbitrates ambiguity](../tenets.md#5-the-user-arbitrates-ambiguity)
- [Tenet 8 — Hand-edits are first-class input](../tenets.md#8-hand-edits-are-first-class-input)
- [Tenet 10 — Modularity over hardcoding](../tenets.md#10-modularity-over-hardcoding)

## Related ADRs

- [ADR-0019](0019-inheritance-semantics-refine-by-default.md) —
  inheritance semantics; how layers compose at render time
- [ADR-0037](0037-layer-taxonomy-and-repo-discovery.md) —
  umbrella; this ADR carries the taxonomy + render-order layer
- [ADR-0038](0038-precept-acquisition-model.md) — rules
  acquisition; the full override-advisory lifecycle this ADR
  hands off to
- [ADR-0050](0050-agency-identity.md) — sibling sub-ADR; the
  agency boundary that contains layers of these types
- [ADR-0051](0051-marker-file-and-distributed-manifest.md) —
  sibling sub-ADR; the marker file that records each layer's
  type and dependencies

## TL;DR

Maury's configuration sits on **two orthogonal dimensions**:
**WHAT** (the user's mode tree — `base → mode:work →
mode:work:client-acme`) and **WHERE/HOW** (environment-tagged
and host-tagged sections inside `base` and `mode` repos). The
WHAT dimension maps to exactly **three layer types**:
`base` (the agency-wide root, ×1), `mode` (what the user is
doing — a nested tree below base), and `rules` (shareable
conventions/precepts — floats anywhere). Environment is content,
not a separate layer type; machine is content, not a separate
layer type. The render engine applies layers in attachment-point
order with a unified tiebreaker (narrower scope wins; equal
scope → latest commit). Trade-off: three layer types means
mode-tree depth carries most of the structural variation, but
the taxonomy stays small enough to reason about.

## Context and Problem Statement

Maury manages a collection of git repositories that together
render a single CLAUDE.md for any host the user works on. The
foundational structural question is: **what kinds of repos does
a maury installation contain, and how do they relate to each
other?**

Without a clear taxonomy, every repo is a special case and the
render engine has no general semantics to apply. With too many
types, the user has to learn an arbitrary vocabulary. The
taxonomy needs to be:

- **Small enough to learn quickly** — a curator should be able
  to name every layer type within a paragraph of explanation.
- **Load-bearing enough to encode meaning** — each type's
  semantics must do real work for the render engine (cascade
  direction, inheritance, advisory behavior).
- **Two-dimensional** — the user's mental model is "what am I
  doing" (work vs. home vs. a specific client) AND "where am I
  doing it" (this laptop vs. that workstation; macOS vs.
  Linux). Both dimensions need representation.

[ADR-0037](0037-layer-taxonomy-and-repo-discovery.md) is the
umbrella; this sub-ADR carries the **taxonomy + render-order**
layer. The agency-identity layer lives in
[ADR-0050](0050-agency-identity.md); the marker-file +
distributed-manifest layer lives in
[ADR-0051](0051-marker-file-and-distributed-manifest.md).

## Decision Drivers

- **Two-dimensional model maps to mental model.** Engineers
  configure environments along both "what am I doing" and
  "where am I doing it" axes; the taxonomy needs to honor
  both without conflating them.
- **Maintenance surface minimization.** Anything documented in
  the spec accrues support burden. Universal mechanisms only.
- **Tenet 2.** Hosts in the same mode get the same baseline,
  with controlled difference expressed through env- and
  host-tagged sections rather than ad-hoc per-host divergence.
- **Tenet 10 (modularity).** Layer types must compose; the
  taxonomy should not embed accidental coupling between
  unrelated repos.
- **Tenet 5 (user arbitrates).** When two pieces of content
  compete, there must be a single deterministic rule the user
  can predict; the advisory system surfaces the resolution but
  does not block.

## Considered Options

### A — Many layer types (env, machine, project, team, ...)

A type-per-purpose taxonomy: `base`, `environment`, `machine`,
`mode`, `project`, `team`, `rules`, ...

- ❌ Vocabulary explosion. Each type needs its own semantics for
  cascade and inheritance.
- ❌ Type-per-purpose conflates orthogonal concerns. A `team`
  repo is a `mode` repo with team-scoped access; the type
  doesn't encode anything the access relationship doesn't
  already encode.
- ❌ Environment and machine are content attributes, not
  structural concepts — making them types creates a parallel
  hierarchy that has to stay synced with the mode tree.

### B — Two layer types (`base` + everything-else)

A `base` layer plus a single generic "content" type covering
modes, rules, environment, and machine.

- ❌ Loses the structural distinction between WHAT the user is
  doing (modes form a tree) and WHAT they are following
  (rules float through attachment points).
- ❌ Render order has no principled basis — the engine cannot
  tell "inherit from parent mode" from "apply this rules repo
  wherever it was attached."

### C (chosen) — Three layer types: `base` + `mode` + `rules`

- ✅ Three types maps cleanly to the user's mental model:
  agency-wide foundation, WHAT-dimension mode tree, floating
  shareable rules.
- ✅ Environment and machine are content, not layers. The
  WHERE/HOW dimension is expressed through tag-based sections
  inside `base` and `mode` repos.
- ✅ Convention vs. precept is access (`repo_mode` on the rules
  sublayer entry), not a separate type.
- ⚖️ The mode tree carries most of the structural variation.
  Deep mode chains substitute for a separate type-per-purpose
  hierarchy.

## Decision Outcome

Chosen: **Option C** — three layer types, environment-as-
content-attribute, machine-as-content-concept.

### Two configuration dimensions

A maury installation's configuration is shaped by two
**orthogonal** dimensions:

| Dimension | Question it answers | How it is expressed |
|---|---|---|
| **WHAT** | What is the user doing? | `mode` chain (a nested tree below `base`) |
| **WHERE/HOW** | On which physical box, in what kind of environment? | Environment-tagged sections inside `base` and `mode` repos; host-tagged sections inside `mode` repos for machine-specific content |

The same mode (e.g., `work:client-acme`) can render on a Linux
workstation in one office and a macOS laptop in another,
picking up different env-tagged sections from base and from the
mode chain. Two engineers in the same `work:client-acme` mode,
on similar laptops, see the same env-tagged sections trigger
but each have their own host-tagged sections (or, if access
isolation requires it, their own private narrow-scoped child
mode repo).

```
Engineer A (macOS laptop, home office)        Engineer B (Linux workstation, on-site)
  base                                          base
    [macos-tagged sections apply]                 [linux-tagged sections apply]
    [home-office-tagged sections apply]           [client-site-tagged sections apply]
  mode: work                                    mode: work
  mode: work:client-acme                        mode: work:client-acme
    [host_A-tagged sections apply]                [host_B-tagged sections apply]
```

Same WHAT, different WHERE/HOW. The render engine reads each
host's declared `environment_tags` and applies the matching
sections. Host-specific machine config rides inside the same
mode repo as host-tagged content; an escape hatch (a private
narrow-scoped child mode repo) exists for cases where access
isolation matters.

`rules` repos float through both dimensions: a rules repo
declared at any structural level applies to every host whose
active mode chain passes through that level, with `repo_mode`
driving advisory behavior on override.

### The three layer types

Every repo in a maury installation has a **layer type**. The
layer type encodes structural position, content character, and
default inheritance.

| Type | Dimension | Reusable? | Content character |
|---|---|---|---|
| `base` | structural root (×1) | agency-wide | global defaults; contains env-tagged sections |
| `mode` | WHAT (nested tree) | per-chain | mode-specific config; contains env-tagged and host-tagged sections; private narrow-scoped child mode repos serve as the access-isolation escape hatch for machine-specific config |
| `rules` | floating | yes — many consumers | shareable conventions / precepts; access subtype = `repo_mode` |

There are exactly three layer types. The marker file's `layer`
field (per [ADR-0051](0051-marker-file-and-distributed-manifest.md))
takes one of three values: `base`, `mode`, or `rules`.

#### Why no `environment` layer type

Environment-shaped content (OS family, package manager, machine
class, location, etc.) lives as **tagged sections inside `base`
and `mode` repos**, not in a dedicated layer type. Each host
declares its `environment_tags` at bootstrap; the render engine
applies the matching sections wherever they appear in the
layer graph.

Specificity follows the **CSS selector model**: the count of
required tag conditions on a section is its specificity.

- A section gated on `.macos.laptop` (two conditions) beats a
  section gated on `.macos` (one condition).
- A section gated on `.macos.laptop.low-battery` (three) beats
  both.
- Tag names may contain hyphens (`low-battery`) — that is a
  naming convention only; the count is what determines
  specificity.
- Equal specificity ties are resolved by the unified tiebreaker
  rule below.

The exact section-tagging syntax inside `base` and `mode` repos
is a render-engine concern (per
[ADR-0046](0046-source-file-naming.md)), not part of the
layer-taxonomy contract. The taxonomy guarantees only that the
host's declared `environment_tags` are matched at render time.

#### Why no `machine` layer type

Machine-specific content (hardware facts, credential paths,
one-off quirks) lives as **host-tagged sections inside the
relevant mode repo** by default. Most host-specific data is
small and does not warrant a dedicated repo; tagging by
`host_<hex>` inside a shared mode repo keeps the repo count low.

Where access isolation is genuinely required (truly sensitive
credentials, separate ACLs), the **escape hatch** is a private
narrow-scoped child mode repo declared as a sublayer of the
parent mode. Its `layer` value is still `mode`; it is simply a
mode repo with a single host registered against it.

```
base
  └─ mode:work
       (contains host-tagged sections for machine config — default)
       └─ private host-specific mode repo, layer: mode
            (escape hatch — used only when access isolation is required)
```

There is no agency-wide cross-mode "machine per host" concept.
A given piece of hardware participating in two modes has no
shared structural layer; it appears as host-tagged content (or
as a private child mode repo) inside each mode it participates
in.

#### `base`

The agency-wide foundation. Exactly **one** per agency (per
[ADR-0050](0050-agency-identity.md)).

- Structural position: root of the hierarchy; all other layers
  descend from it.
- Content character: agency-wide defaults — CLAUDE.md preamble,
  shared tool configs, conventions the agency curator
  maintains. May contain environment-tagged sections for OS
  family, package manager, or other broadly-applicable variants.
- Inheritance: cascades to every mode and to `rules` repos
  declared beneath it. Children can override freely.
- Hosts: hosts do **not** register to `base`. They register to
  a mode.
- Managed by: agency curator (`rw`); all hosts read the
  rendered output.

Registering a second base into the same agency is a hard error;
override requires `--replace --i-am-sure` (per
[ADR-0050](0050-agency-identity.md)). A successful replace does
not delete the old base; it only re-points the agency's
identity claim at a new repo.

#### `mode`

A named configuration mode — what the user is doing on this
hardware. Modes form an **arbitrary-depth tree**, not flat
siblings: `base → mode:work → mode:work:client-acme`.

- Structural position: below `base`. A mode cannot be a child
  of two parent modes simultaneously (tree, not graph — no
  diamond inheritance).
- A host has **one active mode chain** at a time.
- Content character: mode-specific config — the `work` mode has
  different strictness, metadata, and tooling expectations than
  the `home` mode. May contain environment-tagged sections (for
  OS- or location-shaped variants) and host-tagged sections
  (for machine-specific content). A nested mode like
  `work:client-acme` layers project-specific rules on top of
  the parent `work` mode.
- Inheritance: cascades down the chain to descendant modes and
  to `rules` repos declared beneath any mode in the chain.
- Hosts: each mode repo's marker file carries a `hosts` dict.
  Hosts register against modes, not against base. (See
  [ADR-0039](0039-bootstrap-and-host-lifecycle.md) for the
  mode-scoped host identity semantics.)
- Managed by: whoever has `rw` on the mode repo. Per
  [ADR-0033](0033-pr-repo-mode.md), `repo_mode: pr` consumers
  contribute via PR; `repo_mode: ro` consumers cannot
  contribute.

Nested mode IDs are `mode_<hex>` surrogate keys per
[ADR-0015](0015-surrogate-keys-for-hosts-and-profiles.md)
(renamed from `profile_<hex>`).

#### `rules`

A shareable layer of rules — conventions for the owner,
precepts for consumers.

- Structural position: floating — declared as a sublayer of
  any other layer (`base` or any mode in the tree) and
  cascading from that attachment point downward.
- Content character: rules content. Whether the rules are a
  convention (the owner's content) or a precept (an external
  source the consumer follows) is a property of the
  **consumer's relationship**, not the repo's content. The
  same rules repo can have both relationships across its
  consumers.
- Inheritance: cascades to layers beneath the attachment
  point. Child layers can override; whether an advisory fires
  on override depends on the declaring layer's `repo_mode` for
  the rules sublayer (see
  [ADR-0038](0038-precept-acquisition-model.md)).
- Managed by: governed by the declaring layer's `repo_mode`
  value. The owner has `rw`; subscribers have `pr` (PR-able)
  or `ro` (read-only). Governance metadata in
  `.meta/maury-governance.json` declares owners and PR target
  — see ADR-0038.

The terms "convention" and "precept" describe the
**relationship between a host and a `rules` repo**, not the
repo's type:

- `repo_mode: rw` → **convention** semantics. The host owns
  this layer; no advisory fires on override (overriding
  yourself is meaningless).
- `repo_mode: pr` → **precept** semantics. The host follows
  this layer; can propose changes via PR. Advisory fires when
  a child layer overrides content from this layer.
- `repo_mode: ro` → **precept** semantics. The host follows
  this layer with no contribution path. Advisory fires when a
  child layer overrides content from this layer.

The same `rules` repo can be a convention for its owner's
install and a precept for every other consumer. The marker
file's `layer: rules` is a structural label; the declaring
layer's `repo_mode` (recorded on the sublayer entry that
declares the dep, shared by all hosts registered to that mode)
drives advisory behavior. `repo_mode` is the declaring layer's
stated relationship with the sublayer — a single value in the
marker file — and is decoupled from any individual host's
actual git access level. A mode repo can declare `repo_mode: ro`
against a rules repo for advisory purposes even if some hosts
happen to have git push access to that rules repo through their
backend ACLs.

### Render order

The render engine assembles the final CLAUDE.md by applying
layers in a fixed order, with the **attachment-point model**
for `rules` layers: each `rules` repo slots in immediately
after the layer that declared it as a sublayer.

```
base content
  → rules declared by base                           (attachment: base)
  → mode chain content (widest → narrowest)
      → rules declared by each mode in the chain    (attachment: that mode level)
        → env-tagged sections applied throughout
          (matched against the host's environment_tags)
```

Highest priority is **last applied**. The order is
intent-driven: deeper attachment in the hierarchy = higher
specificity. A project-level `rules` override beats a
base-level one because it was declared closer to the work
being done. Host-tagged content inside a mode repo is a
property of that mode's content layer, applied at that mode's
position in the chain; it does not float separately.

#### Unified tiebreaker rule

When two pieces of content compete at the same level:

> **Narrower scope wins. At equal scope, the latest git commit
> timestamp wins.**

The attachment-point model resolves cross-level conflicts
before the timestamp tiebreaker comes into play. Timestamp
resolution is only invoked for **same-level** conflicts (e.g.,
two `rules` repos declared at the same mode level disagreeing
on the same field).

Cycles in the sublayer graph are warned at registration but
not blocked: in a cycle, each participating layer has the same
effective scope from the others' perspective, so the
latest-commit tiebreaker applies. Warning the curator suffices.
Blocking cycles would be incorrect for two reasons: (1) the
render engine resolves them deterministically — the
latest-commit tiebreaker produces a stable output, not an
infinite loop; (2) diamond dependencies (two modes both
declaring the same rules repo as a sublayer) look structurally
similar to cycles in a naive traversal and should not be
blocked either. The warn-not-block policy handles both shapes
uniformly.

### Override advisory

When the render engine detects that a layer declared with
`repo_mode: rw` overrides content inherited from a layer
declared with `repo_mode: pr` or `ro`, it issues an advisory.
The advisory is informational and acknowledgeable; it does not
block.

The full advisory lifecycle (firing rules, dismissal paths,
storage, identity scheme) lives in
[ADR-0038](0038-precept-acquisition-model.md).

No advisory is issued for `rw`-over-`rw` overrides — the
declaring party owns the content and child customization is
expected.

### Precepts are prescriptive, not enforceable

Maury **advises** — it does not warn or block. Per
[the official Claude Code documentation][cc-memory], CLAUDE.md
content is read by Claude as guidance; there is no
configuration-layer mechanism in Claude Code to make any rule
mandatory. The advisory system in ADR-0038 is the entire
mechanism: surface the divergence, make it acknowledgeable, do
not gate. This is consistent with Tenet 5 (the user arbitrates
ambiguity) and Tenet 8 (hand-edits are first-class input).

[cc-memory]: https://code.claude.com/docs/en/memory

### Render-time provenance

The render engine tracks, for every piece of content in the
rendered CLAUDE.md, which layer it originated from. Provenance
enables:

- Correct override cascade (child overrides parent, with the
  unified tiebreaker).
- Advisory generation (when implemented): provenance is the
  prerequisite for detecting `rules`-content overrides.
- Layer-graduation suggestions (when implemented): detecting
  when child layers converge on similar modifications to the
  same rule.
- Rule decomposition (when implemented): same mechanism
  applied to concrete content rather than layer-level patterns.

Provenance is not surfaced to the user by default; it is an
internal render-engine concern that the advisory system
surfaces selectively.

### Layer graduation

Layer content can graduate **outward** as patterns emerge
(more specific → more reusable):

- A host-tagged section in a mode repo that turns out to apply
  to every similar machine of the same shape graduates into an
  environment-tagged section in the same (or parent) mode.
- An environment-tagged section in a child mode that applies
  across the parent mode's full subtree graduates upward to the
  parent mode.
- A pattern that applies across every mode graduates into
  `base`, or into a `rules` repo that the entire agency
  subscribes to.

Example: an engineer has `OPENAI_API_KEY=op://Personal/...` (a
1Password CLI reference) in their host-tagged section of
`mode:home`. After getting a second laptop and adding the same
line to its (also host-tagged) section, the rule is clearly
environmental — it applies to every macOS host this user
manages — and graduates to a `.macos`-tagged section in the
same mode repo. Later, when the team adopts 1Password CLI
org-wide, it graduates again to a section in `base` or to a
shared `rules` repo.

Rule decomposition (under consideration as a future
render-engine capability) would suggest these graduations
automatically by analyzing convergent modifications across
descendant layers.

## Consequences

- ✅ **Good:** Three layer types, not five or six. Environment
  is content; machine is content; convention/precept is access.
  The taxonomy is small but each type's semantics is
  load-bearing.
- ✅ **Good:** The two-dimensional model (WHAT vs WHERE/HOW)
  maps cleanly to how engineers actually configure their
  environments, with environment expressed as a content
  attribute rather than a separate repo type.
- ✅ **Good:** Convention/precept as access relationship (not
  layer type) means the same `rules` repo can serve different
  roles for different consumers without duplicating content.
- ✅ **Good:** Hosts register to modes, not to base. Mode-scoped
  host identity (per ADR-0039) follows naturally; the `hosts`
  dict lives where it is structurally meaningful.
- ✅ **Good:** Attachment-point render order is intent-driven;
  deeper attachment wins. The unified tiebreaker (narrower
  scope; equal scope → latest commit) is one rule with no
  special cases, including cycles.
- ⚖️ **Neutral:** Mode-tree depth carries most of the
  structural variation. Deep mode chains substitute for what a
  larger taxonomy would have expressed as separate types.
- ❌ **Bad:** Layer-graduation suggestions and rule
  decomposition are downstream features; until they exist,
  users have to notice graduation candidates manually.

### Confirmation

- The render engine produces deterministic output given a fixed
  layer set, fixed host environment-tags, and a fixed mode
  chain. Tested against fixture agencies with each layer type
  and tag combination.
- The unified tiebreaker is implemented as a single function
  with no per-layer-type special cases. Tested against
  same-level conflicts at every layer level.
- Override advisory fires for `repo_mode: pr`-/`ro`-attached
  rules content overridden by an `rw` consumer; does not fire
  for `rw`-over-`rw`. Tested per case.

## Build-order placement

When implemented (per ADR-0037 umbrella):

- **Render engine.** Reads the layer graph, applies the
  attachment-point render order, surfaces override advisories
  via the ADR-0038 lifecycle.
- **Render-time provenance.** Tracks per-line layer origin;
  consumed by the override-advisory generator and (later) by
  the graduation-suggestion engine.

## Followups

- **Layer graduation tooling.** Render-engine analysis of
  content redundancy across layers; advisory system for
  suggesting host-tagged → env-tagged and section → mode
  graduations.
- **Rule decomposition.** Concrete-content variant of
  graduation analysis — detect convergent modifications to the
  same rule across descendant layers and suggest pushing the
  rule upward.

## Claude Code references

- [Claude Code memory: how CLAUDE.md is read][cc-memory] —
  establishes that CLAUDE.md content is guidance, not enforced
  configuration; the basis for the "advise, do not block"
  principle in the override advisory model.

## Amendment history

- 2026-05-18 — initial publication. Split out of
  [ADR-0037](0037-layer-taxonomy-and-repo-discovery.md) as the
  taxonomy + render-order layer of the layer-and-discovery
  design.
