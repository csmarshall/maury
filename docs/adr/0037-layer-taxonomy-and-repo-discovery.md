# ADR-0037: Layer taxonomy and repo discovery

**Status:** Accepted
**Date:** 2026-05-08

## Related tenets

- [Tenet 1 — First, do no harm](../tenets.md#1-first-do-no-harm)
- [Tenet 2 — Consistency within a mode; controlled difference across modes](../tenets.md#2-consistency-within-a-mode-controlled-difference-across-modes)
- [Tenet 5 — The user arbitrates ambiguity](../tenets.md#5-the-user-arbitrates-ambiguity)
- [Tenet 6 — Identity is not name](../tenets.md#6-identity-is-not-name)
- [Tenet 8 — Hand-edits are first-class input](../tenets.md#8-hand-edits-are-first-class-input)
- [Tenet 10 — Modularity over hardcoding](../tenets.md#10-modularity-over-hardcoding)
- [Tenet 11 — Explicit beats implicit, with conservative defaults](../tenets.md#11-explicit-beats-implicit-with-conservative-defaults)

## Related ADRs

- [ADR-0015](0015-surrogate-keys-for-hosts-and-profiles.md) — URL is the surrogate key for repos; `host_<hex>` IDs (this ADR's surrogate key for the WHAT dimension is `mode_<hex>`, formerly `profile_<hex>`); see ADR-0039 for mode-scoped host identity semantics
- [ADR-0016](0016-pluggable-repo-backends.md) — backend pluralism; no platform-specific affordances
- [ADR-0017](0017-drift-detection-and-reconciliation.md) — drift detection; render engine
- [ADR-0030](0030-manifest-schema-migrations.md) — when manifest schema bumps are required
- [ADR-0033](0033-pr-repo-mode.md) — `pr` repo mode; PR contribution side-channel
- [ADR-0038](0038-precept-acquisition-model.md) — rules acquisition model; governance metadata; advisory dismissal storage
- [ADR-0039](0039-bootstrap-and-host-lifecycle.md) — bootstrap flow; mode-scoped host identity; mode change process; agency init

---

## TL;DR

Maury's layer taxonomy is exactly three types: **`base`** (the
agency-wide root, ×1), **`mode`** (what the user is doing — a nested
tree below base), and **`rules`** (shareable conventions/precepts —
floats anywhere). Each repo declares its type, its agency, and its
**direct sublayers** in `.meta/maury-marker.json` — there is no flat
agency-wide registry. The agency is bounded by **`agency_id`**
(UUID) and discovered by walking the marker graph from base outward.
Environment and machine-specific content is content inside a layer
(env-tagged and host-tagged sections), not a separate layer type.
Trade-off: a stranger looking at a single repo cannot infer the full
agency topology without walking the graph; this is the price of
locality-of-change and distributed authorship.

## Context

Maury manages a collection of git repositories that together render a
single CLAUDE.md for any host the user works on. Two questions sit at
the foundation of the design:

1. **What kinds of repos does a maury installation contain, and how do
   they relate to each other?** Without a clear taxonomy, every repo is
   a special case and the render engine has no general semantics to
   apply.
2. **How does maury discover which repos belong to an installation, and
   how does a curator looking at a repo know what role it plays?**
   Without a discovery story, agency membership is implicit and a
   stranger inspecting a repo cannot tell whether it is maury-managed.

Both questions must be answered in a way that satisfies several
non-negotiables:

- **Backend pluralism (Tenet 10 / [ADR-0016](0016-pluggable-repo-backends.md)).**
  Every mechanism must work on every git-compatible backend. No
  GitHub-only, GitLab-only, or other platform-specific surfaces.
- **Locality of change.** Adding a rules repo to one work mode should
  not require editing a global registry. Curators of distinct modes
  should not gate-keep each other.
- **Stable identity (Tenet 6).** The set of repos and hosts maury
  manages — call it the **agency** — must have an identity that
  survives renames, URL changes, and re-homing of individual repos.
- **Off-network provenance.** A curator who clones a repo with no other
  context should be able to tell that it is maury-managed and what role
  it plays.
- **Maintenance surface minimization.** Anything documented in the
  spec accrues support burden. Universal mechanisms (committed files,
  manifest fields) only.

This ADR establishes the **layer taxonomy** (three types: `base`,
`mode`, `rules`), the **distributed manifest** that records each
layer's direct dependencies, and the committed **marker file**
(`.meta/maury-marker.json`) that doubles as the per-layer manifest. It
also defines the **render order** and **tiebreaker rule** the render
engine applies, the **environment-as-content-attribute** model that
removes the need for a separate environment layer type, and the
**machine-as-content-concept** model that removes the need for a
separate machine layer type.

## Decision

### Two configuration dimensions

A maury installation's configuration is shaped by two **orthogonal**
dimensions:

| Dimension | Question it answers | How it is expressed |
|---|---|---|
| **WHAT** | What is the user doing? | `mode` chain (a nested tree below `base`) |
| **WHERE/HOW** | On which physical box, in what kind of environment? | Environment-tagged sections inside `base` and `mode` repos; host-tagged sections inside `mode` repos for machine-specific content |

The same mode (e.g. `work:client-acme`) can render on a Linux
workstation in one office and a macOS laptop in another, picking up
different env-tagged sections from base and from the mode chain. Two
engineers in the same `work:client-acme` mode, on similar laptops, see
the same env-tagged sections trigger but each have their own
host-tagged sections (or, if access isolation requires it, their own
private narrow-scoped child mode repo).

```
Engineer A (macOS laptop, home office)        Engineer B (Linux workstation, on-site)
  base                                          base
    [macos-tagged sections apply]                 [linux-tagged sections apply]
    [home-office-tagged sections apply]           [client-site-tagged sections apply]
  mode: work                                    mode: work
  mode: work:client-acme                        mode: work:client-acme
    [host_A-tagged sections apply]                [host_B-tagged sections apply]
```

Same WHAT, different WHERE/HOW. The render engine reads each host's
declared `environment_tags` and applies the matching sections. Host-
specific machine config rides inside the same mode repo as host-tagged
content; an escape hatch (a private narrow-scoped child mode repo)
exists for cases where access isolation matters. This satisfies
Tenet 2 — hosts in the same mode get the same baseline, with
controlled difference expressed through env- and host-tagged
sections rather than ad-hoc per-host divergence.

`rules` repos float through both dimensions: a rules repo declared at
any structural level applies to every host whose active mode chain
passes through that level, with `repo_mode` driving advisory behavior
on override.

---

### The three layer types

Every repo in a maury installation has a **layer type**. The layer type
encodes structural position, content character, and default
inheritance.

| Type | Dimension | Reusable? | Content character |
|---|---|---|---|
| `base` | structural root (×1) | agency-wide | global defaults; contains env-tagged sections |
| `mode` | WHAT (nested tree) | per-chain | mode-specific config; contains env-tagged and host-tagged sections; private narrow-scoped child mode repos serve as the access-isolation escape hatch for machine-specific config |
| `rules` | floating | yes — many consumers | shareable conventions / precepts; access subtype = `repo_mode` |

There are exactly three layer types. The marker file's `layer` field
takes one of three values: `base`, `mode`, or `rules`.

#### Why no `environment` layer type

Environment-shaped content (OS family, package manager, machine class,
location, etc.) lives as **tagged sections inside `base` and `mode`
repos**, not in a dedicated layer type. Each host declares its
`environment_tags` at bootstrap; the render engine applies the matching
sections wherever they appear in the layer graph.

Specificity follows the **CSS selector model**: the count of required
tag conditions on a section is its specificity.

- A section gated on `.macos.laptop` (two conditions) beats a section
  gated on `.macos` (one condition).
- A section gated on `.macos.laptop.low-battery` (three) beats both.
- Tag names may contain hyphens (`low-battery`) — that is a naming
  convention only; the count is what determines specificity.
- Equal specificity ties are resolved by the unified tiebreaker rule
  below.

The exact section-tagging syntax inside `base` and `mode` repos is a
render-engine concern, not part of the layer-taxonomy contract. The
taxonomy guarantees only that the host's declared `environment_tags`
are matched at render time.

#### Why no `machine` layer type

Machine-specific content (hardware facts, credential paths, one-off
quirks) lives as **host-tagged sections inside the relevant mode
repo** by default. Most host-specific data is small and does not
warrant a dedicated repo; tagging by `host_<hex>` inside a shared
mode repo keeps the repo count low.

Where access isolation is genuinely required (truly sensitive
credentials, separate ACLs), the **escape hatch** is a private
narrow-scoped child mode repo declared as a sublayer of the parent
mode. Its `layer` value is still `mode`; it is simply a mode repo with
a single host registered against it.

```
base
  └─ mode:work
       (contains host-tagged sections for machine config — default)
       └─ private host-specific mode repo, layer: mode
            (escape hatch — used only when access isolation is required)
```

There is no agency-wide cross-mode "machine per host" concept. A given
piece of hardware participating in two modes has no shared structural
layer; it appears as host-tagged content (or as a private child mode
repo) inside each mode it participates in.

#### `base`

The agency-wide foundation. Exactly **one** per agency.

- Structural position: root of the hierarchy; all other layers descend
  from it.
- Content character: agency-wide defaults — CLAUDE.md preamble, shared
  tool configs, conventions the agency curator maintains. May contain
  environment-tagged sections for OS family, package manager, or other
  broadly-applicable variants.
- Inheritance: cascades to every mode and to `rules` repos declared
  beneath it. Children can override freely.
- Hosts: hosts do **not** register to `base`. They register to a mode.
- Managed by: agency curator (`rw`); all hosts read the rendered
  output.

Registering a second base into the same agency is a hard error;
override requires `--replace --i-am-sure`. A successful replace does
not delete the old base; it only re-points the agency's identity claim
at a new repo.

#### `mode`

A named configuration mode — what the user is doing on this hardware.
Modes form an **arbitrary-depth tree**, not flat siblings:
`base → mode:work → mode:work:client-acme`.

- Structural position: below `base`. A mode cannot be a child of two
  parent modes simultaneously (tree, not graph — no diamond
  inheritance).
- A host has **one active mode chain** at a time.
- Content character: mode-specific config — the `work` mode has
  different strictness, metadata, and tooling expectations than the
  `home` mode. May contain environment-tagged sections (for OS- or
  location-shaped variants) and host-tagged sections (for
  machine-specific content). A nested mode like `work:client-acme`
  layers project-specific rules on top of the parent `work` mode.
- Inheritance: cascades down the chain to descendant modes and to
  `rules` repos declared beneath any mode in the chain.
- Hosts: each mode repo's marker file carries a `hosts` dict. Hosts
  register against modes, not against base. (See ADR-0039 for the
  mode-scoped host identity semantics.)
- Managed by: whoever has `rw` on the mode repo. Per
  [ADR-0033](0033-pr-repo-mode.md), `repo_mode: pr` consumers
  contribute via PR; `repo_mode: ro` consumers cannot contribute.

Nested mode IDs are `mode_<hex>` surrogate keys per
[ADR-0015](0015-surrogate-keys-for-hosts-and-profiles.md) (renamed
from `profile_<hex>`; this is a breaking schema change requiring a
version bump per [ADR-0030](0030-manifest-schema-migrations.md)).

#### `rules`

A shareable layer of rules — conventions for the owner, precepts for
consumers.

- Structural position: floating — declared as a sublayer of any other
  layer (`base` or any mode in the tree) and cascading from that
  attachment point downward.
- Content character: rules content. Whether the rules are a convention
  (the owner's content) or a precept (an external source the consumer
  follows) is a property of the **consumer's relationship**, not the
  repo's content. The same rules repo can have both relationships
  across its consumers.
- Inheritance: cascades to layers beneath the attachment point. Child
  layers can override; whether an advisory fires on override depends on
  the declaring layer's `repo_mode` for the rules sublayer (see
  [ADR-0038](0038-precept-acquisition-model.md)).
- Managed by: governed by the declaring layer's `repo_mode` value. The
  owner has `rw`; subscribers have `pr` (PR-able) or `ro` (read-only).
  Governance metadata in `.meta/maury-governance.json` declares owners
  and PR target — see ADR-0038.

The terms "convention" and "precept" describe the **relationship
between a host and a `rules` repo**, not the repo's type:

- `repo_mode: rw` → **convention** semantics. The host owns this
  layer; no advisory fires on override (overriding yourself is
  meaningless).
- `repo_mode: pr` → **precept** semantics. The host follows this
  layer; can propose changes via PR. Advisory fires when a child layer
  overrides content from this layer.
- `repo_mode: ro` → **precept** semantics. The host follows this
  layer with no contribution path. Advisory fires when a child layer
  overrides content from this layer.

The same `rules` repo can be a convention for its owner's install and
a precept for every other consumer. The marker file's `layer: rules`
is a structural label; the declaring layer's `repo_mode` (recorded on
the sublayer entry that declares the dep, shared by all hosts
registered to that mode) drives advisory behavior. `repo_mode` is the
declaring layer's stated relationship with the sublayer — a single
value in the marker file — and is decoupled from any individual host's
actual git access level. A mode repo can declare `repo_mode: ro`
against a rules repo for advisory purposes even if some hosts happen
to have git push access to that rules repo through their backend
ACLs.

---

### The agency

The bounded management unit — the totality of repos and hosts maury
manages together as a single installation — is named an **agency**.
The name reflects that maury *is* an agent operating on the user's
behalf across multiple hosts and modalities; the bounded unit it
operates within is therefore an agency.

Every layer carries an `agency_id` field in its marker file:

- On `base` and `mode` repos: a **membership claim** ("this repo
  belongs to this agency").
- On `rules` repos: a **provenance claim** ("this repo was originally
  created by this agency"). Cross-agency consumption of `rules` repos
  is expected and supported; they are designed to be shareable.

The `agency_id` is a UUID generated once at `maury agency init` (see
[ADR-0039](0039-bootstrap-and-host-lifecycle.md)) and never changes.
A `base` or `mode` repo whose `agency_id` does not match the
installation's `agency_id` is a **hard error at registration**. The
rationale: a `base` or `mode` repo is making a membership claim — it
asserts it belongs to a specific agency. When that claim contradicts
the installing agency's identity, the state is incoherent rather than
merely surprising. It almost always indicates a mis-pasted URL or
accidental cross-agency contamination. A warning that the user can
dismiss is the wrong response to an incoherent state; a hard error
forces resolution. A `rules` repo whose `agency_id` differs is
informational only — cross-agency rules sharing is a feature, not an
anomaly. The `agency_id` on a `rules` repo is **provenance** (where the
repo was created), not **membership** (who may consume it). A `rules`
repo from a different agency is expected: it means "this was created
elsewhere and we are subscribing to it." Engineering-standards teams
publish `rules` repos intended for consumption by every team in the
company; blocking cross-agency consumption on a rules repo would make
sharing impossible without a hard `agency_id` mismatch override. The
hard-error behavior is reserved for `base` and `mode` repos, where a
mismatched `agency_id` is a genuine membership incoherence rather than
a cross-team sharing pattern.

---

### `.meta/maury-marker.json` — committed file, not a git tag

Every maury-managed repo carries a tracked file at
`.meta/maury-marker.json` recording schema version, layer type, agency
identity, declared sublayers, and (for mode repos) the registered
hosts. The marker file **is** the per-layer manifest; there is no
separate manifest file.

The marker is a **committed file**, not a git tag. Rationale:

- The `agency_id` is a stable identity claim; changes must be
  auditable through commit history. A git tag is mutable and leaves no
  trail when re-pointed.
- A committed file survives shallow clones, tag deletion, all git
  backends, and any future non-git-compatible backend that still
  supports tracked content.
- `.meta/` is already an established convention in maury. Three files
  live there across the layer types:
  - `.meta/maury-marker.json` — every layer (this ADR)
  - `.meta/maury-governance.json` — `rules` repos only (ADR-0038)
  - `.meta/maury-advisory-state.json` — `mode` repos only (ADR-0038)
- The earlier design's `manifest_url` field — an upward pointer from a
  child repo to the base — has been removed entirely. It contradicted
  the downward-dependency model. The repo's own URL is already known
  via `git remote get-url origin`; nothing in the marker needs to
  re-record it.

#### Schema

`base` repo (lean — identity and sublayers only; no `hosts` dict):

```json
{
  "schema_version": 1,
  "layer": "base",
  "agency_id": "550e8400-e29b-41d4-a716-446655440000",
  "sublayers": [
    { "url": "git@github.com:acme-corp/mode-work.git" },
    { "url": "git@codeberg.org:jdoe/mode-home.git" }
  ]
}
```

`mode` repo (sublayers may include nested modes and rules; tracks
registered hosts):

```json
{
  "schema_version": 1,
  "layer": "mode",
  "agency_id": "550e8400-e29b-41d4-a716-446655440000",
  "sublayers": [
    { "url": "git@github.com:eng-standards/rules-linting.git", "repo_mode": "ro" },
    { "url": "git@github.com:acme-corp/rules-team.git",        "repo_mode": "pr" }
  ],
  "hosts": {
    "host_abc123": {
      "registered_at": "2026-01-15T10:00:00Z",
      "environment_tags": ["ubuntu", "laptop", "work-desk"]
    }
  }
}
```

`rules` repo (no `hosts` dict; `agency_id` here is a provenance claim
rather than a membership claim):

```json
{
  "schema_version": 1,
  "layer": "rules",
  "agency_id": "550e8400-e29b-41d4-a716-446655440000",
  "sublayers": []
}
```

#### Field semantics

- **`schema_version`** — integer, currently `1`. Bumps follow
  [ADR-0030](0030-manifest-schema-migrations.md).
- **`layer`** — one of `base`, `mode`, `rules`.
- **`agency_id`** — UUID. Membership claim on `base` and `mode`;
  provenance claim on `rules`.
- **`sublayers`** — list of layers declared as direct dependencies of
  this layer (top-down). Absent or empty on leaf nodes. Each entry has:
  - **`url`** — the sublayer repo's URL (the surrogate key per
    ADR-0015).
  - **`repo_mode`** — only meaningful when the sublayer is a `rules`
    repo. One of `rw`, `pr`, `ro` (per
    [ADR-0033](0033-pr-repo-mode.md)). Omitted on non-rules sublayer
    entries (the consuming layer always reads its declared sublayers
    with whatever access it has). This is a single value in the
    declaring layer's marker file, shared by every host registered to
    that mode; it is not per-host. This decouples advisory behavior
    from git access — `repo_mode` expresses intent, not capability.
    The field is named `repo_mode` rather than `mode` (as in
    ADR-0033's manifest schema) to avoid collision with the
    layer-type value `mode`. The valid values (`ro`, `pr`, `rw`) are
    unchanged from ADR-0033.
- **`hosts`** — dict keyed by `host_<hex>`, present only in `mode`
  repos. Each entry:
  - **`registered_at`** — RFC 3339 timestamp.
  - **`environment_tags`** — list of strings the host declared at
    bootstrap; the render engine matches them against env-tagged
    sections inside the layer graph.

Field names are fully spelled out. No abbreviations.

#### Example pairing

A typical layout (work mode hosted on a corporate GitHub org; home
mode hosted on a personal Codeberg account; rules from a third-party
engineering-standards org):

- `git@github.com:acme-corp/maury-base.git` (`layer: base`)
  - sublayers: `mode-work` on acme-corp; `mode-home` on
    `git@codeberg.org:jdoe/`.
- `git@github.com:acme-corp/mode-work.git` (`layer: mode`)
  - sublayers: `git@github.com:eng-standards/rules-linting.git`
    (`repo_mode: ro`); `git@github.com:acme-corp/rules-team.git`
    (`repo_mode: pr`).
  - hosts: `host_abc123` (registered with environment tags
    `["ubuntu", "laptop", "work-desk"]`).
- `git@codeberg.org:jdoe/mode-home.git` (`layer: mode`)
  - sublayers: `git@github.com:eng-standards/rules-linting.git`
    (`repo_mode: ro`).
  - hosts: `host_def456`.

In real installations work and home repos almost never live in the
same org or even on the same provider; documentation examples reflect
this.

---

### Distributed manifest

Each layer declares its **direct dependencies only**. The marker file
of a layer is the manifest for that layer. There is no flat
agency-wide registry of every repo. The base manifest stays small and
stable; adding a `rules` repo to `mode:work` edits only that mode's
marker file.

Three load-bearing properties:

1. **Locality of change.** A team adding a new rules repo to their
   mode edits only the mode's marker file, not the base.
2. **Stable base.** Replacing the base affects only the base's direct
   sublayers, not the entire agency topology.
3. **Distributed authorship.** Each layer's curator owns their own
   sublayer declarations; no single party gate-keeps the registry.

#### Discovery

`maury repos list` traverses the full sublayer graph from base
outward, resolving every reachable layer. This is the only operation
that touches the entire agency in normal use; it produces the agency's
complete view from the distributed shape.

`maury sync` (per [ADR-0039](0039-bootstrap-and-host-lifecycle.md))
traverses **only the subgraph required for the host's currently active
mode** — base, the active mode chain, every mode-attached `rules`
repo, and the host's own host-tagged content sources. Repos outside
the active subgraph are not fetched, not warned about, not touched.

A curator-side `maury sync --all` overrides the mode-scoped behavior
and traverses the full graph.

---

### Render order

The render engine assembles the final CLAUDE.md by applying layers in
a fixed order, with the **attachment-point model** for `rules` layers:
each `rules` repo slots in immediately after the layer that declared
it as a sublayer.

```
base content
  → rules declared by base                           (attachment: base)
  → mode chain content (widest → narrowest)
      → rules declared by each mode in the chain    (attachment: that mode level)
        → env-tagged sections applied throughout
          (matched against the host's environment_tags)
```

Highest priority is **last applied**. The order is intent-driven:
deeper attachment in the hierarchy = higher specificity. A
project-level `rules` override beats a base-level one because it was
declared closer to the work being done. Host-tagged content inside a
mode repo is a property of that mode's content layer, applied at that
mode's position in the chain; it does not float separately.

#### Unified tiebreaker rule

When two pieces of content compete at the same level:

> **Narrower scope wins. At equal scope, the latest git commit
> timestamp wins.**

The attachment-point model resolves cross-level conflicts before the
timestamp tiebreaker comes into play. Timestamp resolution is only
invoked for **same-level** conflicts (e.g., two `rules` repos declared
at the same mode level disagreeing on the same field).

Cycles in the sublayer graph are warned at registration but not
blocked: in a cycle, each participating layer has the same effective
scope from the others' perspective, so the latest-commit tiebreaker
applies. Warning the curator suffices. Blocking cycles would be
incorrect for two reasons: (1) the render engine resolves them
deterministically — the latest-commit tiebreaker produces a stable
output, not an infinite loop; (2) diamond dependencies (two modes
both declaring the same rules repo as a sublayer) look structurally
similar to cycles in a naive traversal and should not be blocked
either. The warn-not-block policy handles both shapes uniformly.

#### Override advisory

When the render engine detects that a layer declared with
`repo_mode: rw` overrides content inherited from a layer declared
with `repo_mode: pr` or `ro`, it issues an advisory. The
advisory is informational and acknowledgeable; it does not block.

The full advisory lifecycle (firing rules, dismissal paths, storage,
identity scheme) lives in [ADR-0038](0038-precept-acquisition-model.md).

No advisory is issued for `rw`-over-`rw` overrides — the declaring
party owns the content and child customization is expected.

#### Precepts are prescriptive, not enforceable

Maury **advises** — it does not warn or block. Per
[the official Claude Code documentation][cc-memory], CLAUDE.md content
is read by Claude as guidance; there is no configuration-layer
mechanism in Claude Code to make any rule mandatory. The advisory
system in ADR-0038 is the entire mechanism: surface the divergence,
make it acknowledgeable, do not gate. This is consistent with Tenet 5
(the user arbitrates ambiguity) and Tenet 8 (hand-edits are
first-class input).

[cc-memory]: https://code.claude.com/docs/en/memory

---

### Layer graduation

Layer content can graduate **outward** as patterns emerge (more
specific → more reusable):

- A host-tagged section in a mode repo that turns out to apply to
  every similar machine of the same shape graduates into an
  environment-tagged section in the same (or parent) mode.
- An environment-tagged section in a child mode that applies across
  the parent mode's full subtree graduates upward to the parent mode.
- A pattern that applies across every mode graduates into `base`, or
  into a `rules` repo that the entire agency subscribes to.

Example: an engineer has `OPENAI_API_KEY=op://Personal/...` (a
1Password CLI reference) in their host-tagged section of `mode:home`.
After getting a second laptop and adding the same line to its (also
host-tagged) section, the rule is clearly environmental — it applies
to every macOS host this user manages — and graduates to a
`.macos`-tagged section in the same mode repo. Later, when the team
adopts 1Password CLI org-wide, it graduates again to a section in
`base` or to a shared `rules` repo.

Rule decomposition (under consideration as a future render-engine
capability) would suggest these graduations automatically by analyzing
convergent modifications across descendant layers.

---

### Discovery contract

#### Universal marker

Every maury-managed repo has `.meta/maury-marker.json`. Two questions
the marker answers without any external lookup:

1. *Is this repo maury-managed?* — file presence = yes.
2. *What layer type is it?* — the `layer` field.

To list maury-managed repos under a directory:

```sh
find . -name 'maury-marker.json' -path '*/.meta/*'
```

To classify a single repo without cloning the agency:

```sh
cat .meta/maury-marker.json | jq -r '.layer, .agency_id'
```

The marker survives every git backend (it is a tracked file in the
working tree) and any future non-git-compatible backend that supports
tracked content. No backend-specific affordance is involved.

#### Base layer uniqueness

Exactly **one** base layer exists per agency.

- `maury repos register` against an existing base in the same
  `agency_id` is a hard error. Override requires
  `--replace --i-am-sure`.
- `--replace` re-points the agency's base; it does not delete the old
  base or its content. A cascade warning lists every repo whose
  sublayer declarations reference the outgoing base before the replace
  proceeds.
- `maury agency validate` emits a hard failure (not a warning) if
  it finds two `layer: base` repos with the same `agency_id`. There is
  no "but it might be intentional" path.

#### CLI surface

```sh
maury agency init                # generate agency_id, write base marker

maury repos list                 # all agency repos, layer + agency_id
maury repos list --layer rules   # filter by layer type
maury repos show <url|name>      # full record + which hosts/layers reference it
maury repos register <url>       # add to a layer's sublayers (interactive)
maury repos register <url> --replace --i-am-sure  # override base uniqueness
maury repos unregister <url>     # remove from a layer's sublayers

maury repo init <url>            # bootstrap a rules repo's semver baseline
                                 # and install a post-commit version hook
                                 # owner-only: requires repo_mode: rw
                                 # (see ADR-0038)
maury repo init <url> --force    # re-initialize an already-initialized rules repo
```

`maury agency validate` flags:

- Two repos with `layer: base` and the same `agency_id` → **hard
  failure**.
- Sublayer URLs that resolve to repos missing the `.meta/maury-marker.json`
  file → warning.
- `mode` sublayer entries that carry a `repo_mode` field (only
  meaningful for `rules`) → warning.
- `rules` sublayer entries with no `repo_mode` → warning (consumer
  must declare access).
- Cycles in the sublayer graph → warning at registration; not a
  validation failure.
- `rules` repos with no semver tags → informational note (per
  ADR-0038).
- Marker files using the legacy `profile_<hex>` format anywhere in
  scope fields → warning (schema migration required per ADR-0030).

---

### Render-time provenance

The render engine tracks, for every piece of content in the rendered
CLAUDE.md, which layer it originated from. Provenance enables:

- Correct override cascade (child overrides parent, with the unified
  tiebreaker).
- Advisory generation (when implemented): provenance is the
  prerequisite for detecting `rules`-content overrides.
- Layer-graduation suggestions (when implemented): detecting when
  child layers converge on similar modifications to the same rule.
- Rule decomposition (when implemented): same mechanism applied to
  concrete content rather than layer-level patterns.

Provenance is not surfaced to the user by default; it is an
internal render-engine concern that the advisory system surfaces
selectively.

---

## Consequences

- **Good:** Layer taxonomy gives the render engine clear semantics for
  cascade, override, and advisory generation. The two-dimensional
  model (WHAT vs WHERE/HOW) maps cleanly to how engineers actually
  configure their environments, with environment expressed as a
  content attribute rather than a separate repo type.
- **Good:** Three layer types, not five or six. Environment is
  content; machine is content; convention/precept is access. The
  taxonomy is small but each type's semantics is load-bearing.
- **Good:** The marker file doubles as the per-layer manifest. One
  file per repo, one place to look for layer identity, sublayers, and
  (for modes) registered hosts.
- **Good:** Distributed manifest gives curators locality of change.
  Adding a `rules` dep to `mode:work` does not touch the base.
- **Good:** Committed marker file gives off-network provenance,
  survives shallow clones, and works on every git backend without a
  platform-specific surface. The earlier `manifest_url` upward
  pointer is gone.
- **Good:** `agency_id` provides stable agency identity across repo
  renames and re-homings. Cross-agency `rules` consumption is
  supported (provenance, not membership).
- **Good:** Convention/precept as access relationship (not layer type)
  means the same `rules` repo can serve different roles for different
  consumers without duplicating content.
- **Good:** Hosts register to modes, not to base. Mode-scoped host
  identity (per ADR-0039) follows naturally; the `hosts` dict lives
  where it is structurally meaningful.
- **Good:** Attachment-point render order is intent-driven; deeper
  attachment wins. The unified tiebreaker (narrower scope; equal scope
  → latest commit) is one rule with no special cases, including
  cycles.
- **Good:** No platform-specific affordances. Backend pluralism
  (ADR-0016) is preserved.
- **Neutral:** `maury repos list` traverses the full sublayer graph
  rather than reading a flat registry. Acceptable for agency-sized
  graphs (tens to hundreds of repos).
- **Neutral:** Marker schema additions for `sublayers` and `hosts`
  follow the breaking-change rule per ADR-0030.
- **Bad:** Bootstrap must commit `.meta/maury-marker.json` into every
  new repo. Curators retrofitting existing repos need a path
  (`maury repos add-markers` writes the file in one curator-side
  pass).
- **Bad:** Layer-graduation suggestions and rule decomposition are
  downstream features; until they exist, users have to notice
  graduation candidates manually.

---

## Migration for existing installations

Lazy migration:

1. `maury agency validate` warns about repos missing
   `.meta/maury-marker.json` and any legacy `profile_<hex>` scope
   field still in use.
2. `maury repos register --infer` populates marker files from existing
   layout with best-guess layer inference.
3. `maury repos add-markers` writes `.meta/maury-marker.json` into
   every agency repo in one curator-side pass.

No hard cutover; existing installations continue to function without
markers — they just lose the discoverability and provenance benefits
until migrated.

---

## Open followups

- **Mode-tree visualization.** A `maury mode tree` command that prints
  the mode hierarchy with the active mode highlighted (under
  consideration in [ADR-0039](0039-bootstrap-and-host-lifecycle.md)).
- **Layer graduation tooling.** Render-engine analysis of content
  redundancy across layers; advisory system for suggesting
  host-tagged → env-tagged and section → mode graduations.
- **Non-git backend marker expression.** Per ADR-0016, maury restricts
  backends to git-compatible only — a tracked file works universally.
  If a future ADR opens the door to a non-git-compatible backend that
  lacks tracked-file semantics, a backend-specific marker expression
  would need design.
- **`mode_<hex>` schema migration.** The rename from `profile_<hex>`
  to `mode_<hex>` is a breaking schema change; a migration path per
  ADR-0030 must be designed and implemented before existing
  installations adopt this terminology update.

---

## Claude Code references

- [Claude Code memory: how CLAUDE.md is read][cc-memory] — establishes
  that CLAUDE.md content is guidance, not enforced configuration; the
  basis for the "advise, do not block" principle in the override
  advisory model.

## Amendment history

- 2026-05-13 — this ADR's `rules` layer + `repo_mode` model formally supersedes the publish/subscribe **mechanics** of [ADR-0034](0034-published-subscribed-profiles.md) (the `extends`-based inheritance + `subscribe` mental model). ADR-0034's use-case framing (curator publishes shared content; engineers consume + contribute back via PR) is preserved as the product story; the mechanics live here and in [ADR-0038](0038-precept-acquisition-model.md).
