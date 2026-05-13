# maury — glossary

Quick-lookup definitions for every term maury uses. For the longer
explanations (with analogies, examples, and design rationale) see
[`concepts.md`](concepts.md); for the decisions those terms operate
on, see the cited ADRs.

If a term shows up in an ADR or doc and isn't here, that's a doc bug —
file it as a finding for the next ADR landscape audit.

| Term | Means | See |
|---|---|---|
| **Agency** | The bounded set of repos and hosts maury manages together; identified by `agency_id` UUID | [concepts §1](concepts.md#1-agency), [ADR-0037](adr/0037-layer-taxonomy-and-repo-discovery.md), [ADR-0039](adr/0039-bootstrap-and-host-lifecycle.md) |
| **`agency_id`** | UUID generated once at `maury agency init`; membership claim on `base`/`mode`, provenance claim on `rules` | [concepts §1](concepts.md#1-agency), [ADR-0037](adr/0037-layer-taxonomy-and-repo-discovery.md) |
| **Layer type** | One of `base`, `mode`, `rules`. The marker file's `layer` field | [concepts §2](concepts.md#2-layer-types), [ADR-0037](adr/0037-layer-taxonomy-and-repo-discovery.md) |
| **Base** | The agency-wide foundation; exactly one per agency | [concepts §2](concepts.md#2-layer-types), [ADR-0037](adr/0037-layer-taxonomy-and-repo-discovery.md) |
| **Mode** | A named configuration mode in the WHAT dimension; modes form an arbitrary-depth tree below base | [concepts §2](concepts.md#2-layer-types), [concepts §5](concepts.md#5-mode-tree-and-inheritance), [ADR-0037](adr/0037-layer-taxonomy-and-repo-discovery.md) |
| **`mode_<hex>`** | Surrogate key for a mode (renamed from `profile_<hex>`; breaking schema change) | [ADR-0015](adr/0015-surrogate-keys-for-hosts-and-profiles.md), [ADR-0037](adr/0037-layer-taxonomy-and-repo-discovery.md) |
| **Rules** | A shareable layer of conventions/precepts; floats anywhere in the tree | [concepts §2](concepts.md#2-layer-types), [ADR-0037](adr/0037-layer-taxonomy-and-repo-discovery.md), [ADR-0038](adr/0038-precept-acquisition-model.md) |
| **Sublayer** | A direct dependency of a layer, declared in that layer's marker file | [concepts §3](concepts.md#3-sublayers), [ADR-0037](adr/0037-layer-taxonomy-and-repo-discovery.md) |
| **Marker file** | `.meta/maury-marker.json` — committed file declaring layer type, agency, sublayers, and (for modes) hosts | [concepts §3](concepts.md#3-sublayers), [ADR-0037](adr/0037-layer-taxonomy-and-repo-discovery.md) |
| **Trust boundary** | One git repo = one access scope | [concepts §4](concepts.md#4-trust-boundary), [ADR-0002](adr/0002-repo-per-trust-boundary.md) |
| **Mode tree** | The parent-child relationship between modes, rooted at `base` | [concepts §5](concepts.md#5-mode-tree-and-inheritance), [ADR-0037](adr/0037-layer-taxonomy-and-repo-discovery.md) |
| **Mode chain** | The root-to-leaf path through the mode tree (e.g., `base → work → work:client-acme`) | [concepts §5](concepts.md#5-mode-tree-and-inheritance) |
| **Layer** | One source of content composed at render | [concepts §6](concepts.md#6-layer-at-render-time), [ADR-0037](adr/0037-layer-taxonomy-and-repo-discovery.md) |
| **Attachment point** | Where in the render stack a `rules` repo slots in (immediately after the layer that declared it as a sublayer) | [concepts §6](concepts.md#6-layer-at-render-time), [ADR-0037](adr/0037-layer-taxonomy-and-repo-discovery.md) |
| **Environment tags** | Free-form tags a host declares at bootstrap; the render engine matches them against env-tagged sections | [concepts §2](concepts.md#2-layer-types), [ADR-0037](adr/0037-layer-taxonomy-and-repo-discovery.md), [ADR-0039](adr/0039-bootstrap-and-host-lifecycle.md) |
| **Host-tagged section** | A section inside a `mode` repo gated on a specific `host_<hex>`; carries machine-specific config | [ADR-0037](adr/0037-layer-taxonomy-and-repo-discovery.md) |
| **Active mode** | The one mode (the leaf) a host is currently in | [concepts §7](concepts.md#7-active-mode), [ADR-0039](adr/0039-bootstrap-and-host-lifecycle.md) |
| **Mode-scoped host identity** | `host_<hex>` IDs are not device-stable; a new ID is generated on each mode bootstrap | [concepts §8](concepts.md#8-mode-scoped-host-identity), [ADR-0039](adr/0039-bootstrap-and-host-lifecycle.md) |
| **`repo_mode`** | Access subtype declared on a sublayer entry: `rw` / `pr` / `ro`. For `rules` sublayers, drives convention vs precept semantics | [concepts §9](concepts.md#9-repo_mode-access-subtype), [ADR-0033](adr/0033-pr-repo-mode.md) |
| **Convention** | A `rules` sublayer the declaring layer owns (`repo_mode: rw`); no advisory on override | [concepts §9](concepts.md#9-repo_mode-access-subtype), [ADR-0037](adr/0037-layer-taxonomy-and-repo-discovery.md), [ADR-0038](adr/0038-precept-acquisition-model.md) |
| **Precept** | A `rules` sublayer the declaring layer follows (`repo_mode: pr`/`ro`); advisory fires on override | [concepts §9](concepts.md#9-repo_mode-access-subtype), [ADR-0037](adr/0037-layer-taxonomy-and-repo-discovery.md), [ADR-0038](adr/0038-precept-acquisition-model.md) |
| **Override advisory** | Informational notification fired when a child layer overrides content from a precept; acknowledgeable, non-blocking | [ADR-0038](adr/0038-precept-acquisition-model.md) |
| **`maury-status` skill** | Claude-invokable mid-session affordance that surfaces maury's view of host state (active mode, drift, pending captures/proposals, active sessions) | [ADR-0017 §"The `maury-status` skill"](adr/0017-drift-detection-and-reconciliation.md#the-maury-status-skill) |
| **Render** | Compose all layers → write to `~/.claude/` | [ADR-0019](adr/0019-inheritance-semantics-refine-by-default.md), [ADR-0037](adr/0037-layer-taxonomy-and-repo-discovery.md) |
| **Refinement** | Default merge semantics: child adds to parent | [ADR-0019](adr/0019-inheritance-semantics-refine-by-default.md) |
| **Replacement** | Explicit override semantics: child replaces parent | [ADR-0019](adr/0019-inheritance-semantics-refine-by-default.md) |
| **Promotion** | Move content up the mode tree (toward base) | [ADR-0009](adr/0009-promotion-only-cross-boundary.md) (mechanics), [ADR-0027](adr/0027-cross-context-promotion-via-shared-root.md) (graph constraint) |
| **Cross-trust-boundary promotion** | Promotion that crosses repos (e.g., work → base when base lives in a separate repo) — requires a curator host with write access to both repos | [ADR-0009](adr/0009-promotion-only-cross-boundary.md) |
| **Curator** | A user (and the host they operate on) with write access to a higher-trust repo. Acts as the gate for cross-trust-boundary promotion review | [ADR-0009](adr/0009-promotion-only-cross-boundary.md) |
| **Provenance** | The record of where rendered content came from (which layer contributed which lines) — surfaced as a comment block at the top of every rendered file | [ADR-0019](adr/0019-inheritance-semantics-refine-by-default.md), Tenet 7 |
| **Marker** | `.meta/maury-marker.json` — the per-layer manifest. There is no central manifest; each layer's marker declares its own direct sublayers. Schema in [ADR-0037](adr/0037-layer-taxonomy-and-repo-discovery.md); fields extended by [ADR-0033](adr/0033-pr-repo-mode.md) (`repo_mode` values), [ADR-0038](adr/0038-precept-acquisition-model.md) (governance side-file), [ADR-0024](adr/0024-manifest-concurrency-inclusive-merge.md) (concurrency mechanics) | [ADR-0037](adr/0037-layer-taxonomy-and-repo-discovery.md) |
