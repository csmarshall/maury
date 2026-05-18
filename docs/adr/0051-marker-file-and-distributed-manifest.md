# ADR-0051: Marker file and distributed manifest

**Status:** Accepted
**Date:** 2026-05-18
**Sub-ADR of:** [ADR-0037](0037-layer-taxonomy-and-repo-discovery.md)

## Related tenets

- [Tenet 6 — Identity is not name](../tenets.md#6-identity-is-not-name)
- [Tenet 7 — Provenance is mandatory](../tenets.md#7-provenance-is-mandatory)
- [Tenet 10 — Modularity over hardcoding](../tenets.md#10-modularity-over-hardcoding)
- [Tenet 11 — Explicit beats implicit, with conservative defaults](../tenets.md#11-explicit-beats-implicit-with-conservative-defaults)

## Related ADRs

- [ADR-0015](0015-surrogate-keys-for-hosts-and-profiles.md) —
  URL is the surrogate key for repos; `host_<hex>` and
  `mode_<hex>` IDs (the schema's identifiers)
- [ADR-0016](0016-pluggable-repo-backends.md) — backend
  pluralism; the marker file works on every git backend
- [ADR-0030](0030-manifest-schema-migrations.md) — when marker
  schema bumps are required
- [ADR-0033](0033-pr-repo-mode.md) — `pr` repo mode; the
  `repo_mode` field on rules sublayers
- [ADR-0037](0037-layer-taxonomy-and-repo-discovery.md) —
  umbrella; this ADR carries the marker-file + distributed-
  manifest layer
- [ADR-0038](0038-precept-acquisition-model.md) — governance
  metadata, advisory dismissal storage (sibling `.meta/` files)
- [ADR-0039](0039-bootstrap-and-host-lifecycle.md) — bootstrap
  flow; `maury agency init` creates the first marker
- [ADR-0040](0040-render-pipeline.md) — `maury agency validate`
  command and its full flags list
- [ADR-0049](0049-layer-taxonomy.md) — sibling sub-ADR; the
  three layer types this marker records
- [ADR-0050](0050-agency-identity.md) — sibling sub-ADR; the
  `agency_id` field semantics

## TL;DR

Every maury-managed repo carries a committed file at
`.meta/maury-marker.json` recording: schema version, layer
type (`base` / `mode` / `rules`), `agency_id`, declared
sublayers, and (for `mode` repos) the registered hosts. The
marker file **is** the per-layer manifest — there is no
separate manifest file and no flat agency-wide registry. Each
layer declares its **direct dependencies only**; the agency's
full topology is discovered by walking the marker graph from
base outward. The marker is a **committed file**, not a git
tag — it survives shallow clones, every git backend, and
provides off-network provenance.

## Context and Problem Statement

[ADR-0049](0049-layer-taxonomy.md) defines what kinds of layers
exist; [ADR-0050](0050-agency-identity.md) defines the agency
boundary. This sub-ADR answers two operational questions:

- **Where is layer identity recorded?** A repo needs to assert
  "I am a maury-managed layer of type X belonging to agency Y
  with these sublayers." That assertion must survive shallow
  clones and work on every git backend.
- **How is the agency's topology recorded?** Is there one flat
  registry of every repo, edited by every curator? Or does
  each layer record only its own direct dependencies?

The naïve answer (a single agency-wide manifest file) fails on
locality of change — every team adding a rules repo to their
mode would have to edit the global file. The distributed
answer (each layer records its direct dependencies; the agency
is discovered by traversal) preserves locality but requires a
consistent on-repo marker format.

This ADR establishes the **`.meta/maury-marker.json`** file
format and the **distributed manifest** model.

## Decision Drivers

- **Locality of change.** Adding a rules repo to one work mode
  should not require editing a global registry. Curators of
  distinct modes should not gate-keep each other.
- **Off-network provenance.** A curator who clones a repo with
  no other context should be able to tell that it is
  maury-managed and what role it plays.
- **Backend pluralism (Tenet 10).** The marker mechanism must
  work on every git-compatible backend with no platform-
  specific surface.
- **Auditability (Tenet 7).** Identity claims must leave a
  commit-history trail; mutable surfaces (like git tags) lose
  the trail when re-pointed.
- **Tenet 11.** Layer type, agency membership, and sublayer
  dependencies are explicit fields, not inferred from file
  layout.

## Considered Options

### A — Git tag carries the marker

Identity claims live in a git tag (`maury-marker`) that points
at the latest commit and carries the agency_id/layer/sublayers
as tag-message JSON.

- ❌ Git tags are mutable; re-pointing a tag leaves no commit
  trail. Stable identity claims must be auditable.
- ❌ Tags can be filtered out of clones (`--no-tags`).
- ❌ Not every potential backend supports tags identically.

### B — Single agency-wide manifest in the base repo

The base repo's `manifest.json` lists every repo in the agency
with its layer type.

- ❌ Loses locality. Adding a rules repo to `mode:work` requires
  editing the base.
- ❌ Concentrates the curator role at the base — any team
  wanting to register a new layer has to coordinate with the
  base curator.
- ❌ A repo discovered out-of-band (someone passes you a URL)
  has no way to tell whether it's maury-managed without the
  base manifest in hand.

### C (chosen) — Committed `.meta/maury-marker.json` per repo, with `sublayers` recording direct dependencies

Every layer carries its own marker file recording its identity
plus its direct sublayer dependencies. The agency topology is
discovered by walking the marker graph from base outward.

- ✅ Locality preserved — adding a sublayer touches one marker
  file.
- ✅ Off-network provenance — a single repo can be classified
  by `cat .meta/maury-marker.json | jq -r '.layer'`.
- ✅ Committed file survives shallow clones, tag deletion,
  every git backend, and any future tracked-content backend.
- ⚖️ Agency-wide topology requires graph traversal rather
  than reading a single file. Acceptable for agency-sized
  installations (tens to hundreds of repos).

## Decision Outcome

Chosen: **Option C** — `.meta/maury-marker.json` as a
committed file per repo; distributed manifest discovered by
traversal.

### `.meta/maury-marker.json` — committed file, not a git tag

Every maury-managed repo carries a tracked file at
`.meta/maury-marker.json` recording schema version, layer
type, agency identity, declared sublayers, and (for mode repos)
the registered hosts. The marker file **is** the per-layer
manifest; there is no separate manifest file.

The marker is a **committed file**, not a git tag. Rationale:

- The `agency_id` is a stable identity claim; changes must be
  auditable through commit history. A git tag is mutable and
  leaves no trail when re-pointed.
- A committed file survives shallow clones, tag deletion, all
  git backends, and any future non-git-compatible backend that
  still supports tracked content.
- `.meta/` is already an established convention in maury.
  Three files live there across the layer types:
  - `.meta/maury-marker.json` — every layer (this ADR)
  - `.meta/maury-governance.json` — `rules` repos only
    ([ADR-0038](0038-precept-acquisition-model.md))
  - `.meta/maury-advisory-state.json` — `mode` repos only
    ([ADR-0038](0038-precept-acquisition-model.md))
- The earlier design's `manifest_url` field — an upward pointer
  from a child repo to the base — has been removed entirely.
  It contradicted the downward-dependency model. The repo's
  own URL is already known via `git remote get-url origin`;
  nothing in the marker needs to re-record it.

### Schema

`base` repo (lean — identity and sublayers only; no `hosts`
dict):

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

`mode` repo (sublayers may include nested modes and rules;
tracks registered hosts):

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

`rules` repo (no `hosts` dict; `agency_id` here is a provenance
claim rather than a membership claim, per
[ADR-0050](0050-agency-identity.md)):

```json
{
  "schema_version": 1,
  "layer": "rules",
  "agency_id": "550e8400-e29b-41d4-a716-446655440000",
  "sublayers": []
}
```

### Field semantics

- **`schema_version`** — integer, currently `1`. Bumps follow
  [ADR-0030](0030-manifest-schema-migrations.md).
- **`layer`** — one of `base`, `mode`, `rules` (per
  [ADR-0049](0049-layer-taxonomy.md)).
- **`agency_id`** — UUID. Membership claim on `base` and
  `mode`; provenance claim on `rules` (per
  [ADR-0050](0050-agency-identity.md)).
- **`sublayers`** — list of layers declared as direct
  dependencies of this layer (top-down). Absent or empty on
  leaf nodes. Each entry has:
  - **`url`** — the sublayer repo's URL (the surrogate key per
    [ADR-0015](0015-surrogate-keys-for-hosts-and-profiles.md)).
  - **`repo_mode`** — only meaningful when the sublayer is a
    `rules` repo. One of `rw`, `pr`, `ro` (per
    [ADR-0033](0033-pr-repo-mode.md)). Omitted on non-rules
    sublayer entries (the consuming layer always reads its
    declared sublayers with whatever access it has). This is
    a single value in the declaring layer's marker file,
    shared by every host registered to that mode; it is not
    per-host. This decouples advisory behavior from git
    access — `repo_mode` expresses intent, not capability.
    The field is named `repo_mode` rather than `mode` (as in
    ADR-0033's manifest schema) to avoid collision with the
    layer-type value `mode`. The valid values (`ro`, `pr`,
    `rw`) are unchanged from ADR-0033.
- **`hosts`** — dict keyed by `host_<hex>`, present only in
  `mode` repos. Each entry:
  - **`registered_at`** — RFC 3339 timestamp.
  - **`environment_tags`** — list of strings the host declared
    at bootstrap; the render engine matches them against
    env-tagged sections inside the layer graph.

Field names are fully spelled out. No abbreviations.

### Example pairing

A typical layout (work mode hosted on a corporate GitHub org;
home mode hosted on a personal Codeberg account; rules from a
third-party engineering-standards org):

- `git@github.com:acme-corp/maury-base.git` (`layer: base`)
  - sublayers: `mode-work` on acme-corp; `mode-home` on
    `git@codeberg.org:jdoe/`.
- `git@github.com:acme-corp/mode-work.git` (`layer: mode`)
  - sublayers: `git@github.com:eng-standards/rules-linting.git`
    (`repo_mode: ro`);
    `git@github.com:acme-corp/rules-team.git` (`repo_mode: pr`).
  - hosts: `host_abc123` (registered with environment tags
    `["ubuntu", "laptop", "work-desk"]`).
- `git@codeberg.org:jdoe/mode-home.git` (`layer: mode`)
  - sublayers: `git@github.com:eng-standards/rules-linting.git`
    (`repo_mode: ro`).
  - hosts: `host_def456`.

In real installations work and home repos almost never live in
the same org or even on the same provider; documentation
examples reflect this.

### Distributed manifest

Each layer declares its **direct dependencies only**. The
marker file of a layer is the manifest for that layer. There
is no flat agency-wide registry of every repo. The base
manifest stays small and stable; adding a `rules` repo to
`mode:work` edits only that mode's marker file.

Three load-bearing properties:

1. **Locality of change.** A team adding a new rules repo to
   their mode edits only the mode's marker file, not the base.
2. **Stable base.** Replacing the base affects only the base's
   direct sublayers, not the entire agency topology.
3. **Distributed authorship.** Each layer's curator owns their
   own sublayer declarations; no single party gate-keeps the
   registry.

#### Discovery

`maury repos list` traverses the full sublayer graph from base
outward, resolving every reachable layer. This is the only
operation that touches the entire agency in normal use; it
produces the agency's complete view from the distributed shape.

`maury sync` (per
[ADR-0039](0039-bootstrap-and-host-lifecycle.md)) traverses
**only the subgraph required for the host's currently active
mode** — base, the active mode chain, every mode-attached
`rules` repo, and the host's own host-tagged content sources.
Repos outside the active subgraph are not fetched, not warned
about, not touched.

A curator-side `maury sync --all` overrides the mode-scoped
behavior and traverses the full graph.

### Discovery contract

#### Universal marker

Every maury-managed repo has `.meta/maury-marker.json`. Two
questions the marker answers without any external lookup:

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

The marker survives every git backend (it is a tracked file in
the working tree) and any future non-git-compatible backend
that supports tracked content. No backend-specific affordance
is involved.

### CLI surface

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

The full `maury agency validate` flags list (the validator
command that operates on these markers) lives in
[ADR-0040](0040-render-pipeline.md) §"`maury agency validate`".

## Consequences

- ✅ **Good:** The marker file doubles as the per-layer
  manifest. One file per repo, one place to look for layer
  identity, sublayers, and (for modes) registered hosts.
- ✅ **Good:** Distributed manifest gives curators locality of
  change. Adding a `rules` dep to `mode:work` does not touch
  the base.
- ✅ **Good:** Committed marker file gives off-network
  provenance, survives shallow clones, and works on every git
  backend without a platform-specific surface. The earlier
  `manifest_url` upward pointer is gone.
- ✅ **Good:** Field names are fully spelled out — readable on
  inspection without a key.
- ⚖️ **Neutral:** `maury repos list` traverses the full
  sublayer graph rather than reading a flat registry.
  Acceptable for agency-sized graphs (tens to hundreds of
  repos).
- ⚖️ **Neutral:** Marker schema additions for `sublayers` and
  `hosts` follow the breaking-change rule per
  [ADR-0030](0030-manifest-schema-migrations.md).
- ❌ **Bad:** Bootstrap must commit `.meta/maury-marker.json`
  into every new repo. Curators retrofitting existing repos
  need a path (`maury repos add-markers` writes the file in
  one curator-side pass).

### Confirmation

- A bootstrap creates `.meta/maury-marker.json` with valid JSON
  matching the schema. Tested via fixture agencies that
  exercise each layer type.
- `maury repos list` traverses the marker graph and produces
  the same result regardless of which agency repo it is
  invoked from (graph-traversal output is start-point-
  invariant beyond the base). Tested.
- When implemented (per ADR-0037's open followup), the
  `profile_<hex>` → `mode_<hex>` migration will follow
  ADR-0030's per-version upgrade machinery; no code path
  produces the new format yet.

## Build-order placement

The marker file ships in Phase 4 (alongside `maury agency
init` per [ADR-0039](0039-bootstrap-and-host-lifecycle.md)).
The full CLI surface ships across Phase 4 (`init`, basic
`repos list`/`register`/`unregister`) and Phase 5 (`repos show`,
`repo init` per [ADR-0038](0038-precept-acquisition-model.md)).

## Migration for existing installations

Lazy migration:

1. `maury agency validate` warns about repos missing
   `.meta/maury-marker.json` and any legacy `profile_<hex>`
   scope field still in use.
2. `maury repos register --infer` populates marker files from
   existing layout with best-guess layer inference.
3. `maury repos add-markers` writes `.meta/maury-marker.json`
   into every agency repo in one curator-side pass.

No hard cutover; existing installations continue to function
without markers — they just lose the discoverability and
provenance benefits until migrated.

## Followups

- **Non-git backend marker expression.** Per
  [ADR-0016](0016-pluggable-repo-backends.md), maury restricts
  backends to git-compatible only — a tracked file works
  universally. If a future ADR opens the door to a
  non-git-compatible backend that lacks tracked-file semantics,
  a backend-specific marker expression would need design.
- **`mode_<hex>` schema migration.** The rename from
  `profile_<hex>` to `mode_<hex>` is a breaking schema change;
  a migration path per
  [ADR-0030](0030-manifest-schema-migrations.md) must be
  designed and implemented before existing installations adopt
  this terminology update.
- **Mode-tree visualization.** A `maury mode tree` command
  that prints the mode hierarchy with the active mode
  highlighted (under consideration in
  [ADR-0039](0039-bootstrap-and-host-lifecycle.md)).

## Claude Code references

This sub-ADR introduces no new Claude Code dependencies. The
marker file is a maury-internal artifact that does not interact
with Claude Code's surfaces.

## Amendment history

- 2026-05-18 — initial publication. Split out of
  [ADR-0037](0037-layer-taxonomy-and-repo-discovery.md) as the
  marker-file + distributed-manifest layer of the
  layer-and-discovery design.
