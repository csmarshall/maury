# ADR-0040: Render pipeline (umbrella)

**Status:** Accepted (umbrella; details split into sub-ADRs 2026-05-18)
**Date:** 2026-05-11

> **Umbrella ADR.** The render pipeline is large enough that the
> design lives in three sub-ADRs, each owning one layer of the
> pipeline:
>
> - [ADR-0046 — Source file naming](0046-source-file-naming.md):
>   what maury reads from each layer repo (flat files at the
>   repo root, dotted-name tagging, `agents/` and `skills/`
>   subdirectory exceptions).
> - [ADR-0047 — Render target surfaces](0047-render-target-surfaces.md):
>   what maury writes to `~/.claude/` (CLAUDE.md, rules, skills,
>   agents, settings.json, .mcp.json), the routing principle, and
>   agent routing guidance at three attended moments.
> - [ADR-0048 — LLM condensation](0048-llm-condensation.md): the
>   default budget-recovery pass, precept preservation as a
>   structural invariant, and the `--raw` escape hatch.
>
> This ADR carries the cross-cutting concerns: the pipeline's
> overall shape, `maury agency validate` (which spans all three
> layers), shared consequences, and build-order coordination.
> When a reader needs to know one specific layer in depth, jump to
> the sub-ADR; when a reader needs to know how the pieces fit,
> stay here.

## Related tenets

- [Tenet 1 — First, do no harm](../tenets.md#1-first-do-no-harm)
- [Tenet 2 — Consistency within a mode; controlled difference across modes](../tenets.md#2-consistency-within-a-mode-controlled-difference-across-modes)
- [Tenet 5 — The user arbitrates ambiguity](../tenets.md#5-the-user-arbitrates-ambiguity)
- [Tenet 7 — Provenance is mandatory](../tenets.md#7-provenance-is-mandatory)
- [Tenet 8 — Hand-edits are first-class input](../tenets.md#8-hand-edits-are-first-class-input)
- [Tenet 10 — Modularity over hardcoding](../tenets.md#10-modularity-over-hardcoding)
- [Tenet 11 — Explicit beats implicit, with conservative defaults](../tenets.md#11-explicit-beats-implicit-with-conservative-defaults)

## Related ADRs

- [ADR-0011](0011-anthropic-rubric-integration.md) — `maury
  doctor` content-quality rubric; invoked from the render
  pipeline (sub-detail in ADR-0047)
- [ADR-0012](0012-llm-backend.md) — LLM backend abstraction;
  powers the condensation pass (sub-detail in ADR-0048)
- [ADR-0016](0016-pluggable-repo-backends.md) — backend
  pluralism; no platform-specific affordances anywhere in the
  pipeline
- [ADR-0017](0017-drift-detection-and-reconciliation.md) — drift
  detection; the render pipeline's companion (writes are
  reconciled, not silently overwritten)
- [ADR-0029](0029-maury-state-layout-contract.md) —
  `~/.claude/maury-state/` layout contract; the
  `last-render.json` provenance the pipeline writes
- [ADR-0030](0030-manifest-schema-migrations.md) — when marker
  schema bumps are required
- [ADR-0037](0037-layer-taxonomy-and-repo-discovery.md) — layer
  taxonomy, marker file schema, environment-as-content-attribute,
  render order
- [ADR-0038](0038-precept-acquisition-model.md) — precept
  acquisition; advisory lifecycle; `repo_mode` governance
- [ADR-0023](0023-hook-installation-and-tool-resolution.md) —
  hook installation lifecycle; `settings.json` as the hook-
  ownership surface
- [ADR-0039](0039-bootstrap-and-host-lifecycle.md) — bootstrap
  flow; mode-scoped sync; mode change process

## TL;DR

The render pipeline walks the marker graph in attachment-point
order (per ADR-0037), reads source content from a documented
layer-repo file layout (per ADR-0046), and writes to Claude
Code's multiple configuration surfaces (CLAUDE.md, rules, skills,
agents, settings.json, .mcp.json — per ADR-0047). An **LLM
condensation pass** (per ADR-0048) recovers always-on context
budget for non-precept content; precepts are preserved verbatim.
The pipeline actively steers authors away from misusing surfaces
via routing guidance and `maury agency validate`. Trade-off:
condensation adds an LLM dependency to render, but is the only
way to keep large agencies inside Claude Code's context budget
without discarding content.

## Context and Problem Statement

[ADR-0037](0037-layer-taxonomy-and-repo-discovery.md) defines
**what layers are** and **what order they apply in**. It defines
the environment-as-content-attribute and host-as-content-
attribute models. It does not specify the three layers of the
render pipeline:

1. **Source-file naming** — how content is laid out inside a
   layer repo. **Owned by [ADR-0046](0046-source-file-naming.md).**
2. **Render-target surfaces** — what maury writes to `~/.claude/`
   and the routing principle that decides where each piece of
   source content goes. **Owned by [ADR-0047](0047-render-target-surfaces.md).**
3. **LLM condensation** — the optimization pass that recovers
   always-on context budget without losing precept content.
   **Owned by [ADR-0048](0048-llm-condensation.md).**

This umbrella ADR establishes the cross-cutting shape (how the
three layers compose into a pipeline), `maury agency validate`
(which spans all three layers), and the shared non-negotiables
that any one sub-ADR could otherwise quietly violate:

- **Tenet 1.** Render must not silently produce output that
  contradicts the user's explicit precepts. Precept-sourced
  content is preserved verbatim through the condensation pass by
  design, not by a confirmation step the user can fatigue out of.
- **Tenet 7.** Every line in the rendered output is provenance-
  bearing — the render engine knows which layer it came from,
  because both override-cascade and the condensation pass depend
  on that knowledge.
- **Tenet 8.** Hand-edits to `~/.claude/` are detected as drift
  (per [ADR-0017](0017-drift-detection-and-reconciliation.md));
  the render pipeline does not silently overwrite them.
- **Backend pluralism (Tenet 10).** Source layout, target
  layout, and the condensation pass must work on every git-
  compatible backend with no platform-specific surface.

## Pipeline shape

A single `maury sync` invocation:

1. **Walks the marker graph** (per ADR-0037) to enumerate every
   layer in the active render scope, in attachment-point order.
2. **Reads source content** from each layer repo using the
   filename patterns in [ADR-0046](0046-source-file-naming.md).
   Applicability filters by `environment_tags` and `host_<hex>` per
   the host's manifest entry.
3. **Routes content to target surfaces** per
   [ADR-0047](0047-render-target-surfaces.md). The routing
   principle is content-character-based, not layer-type-based;
   the same routing handles base, mode, and rules content
   uniformly.
4. **Emits provenance markers** around precept-sourced spans
   destined for always-on surfaces. The markers feed the
   condensation pass's preservation invariant.
5. **Runs the LLM condensation pass** (per
   [ADR-0048](0048-llm-condensation.md)) on CLAUDE.md plus
   unconditional rules files. Skipped under `--raw`.
6. **Verifies precept-span preservation** post-LLM. A mismatch
   falls back to raw output and surfaces the regression.
7. **Writes target surfaces** atomically, comparing against
   `last-render.json` (per ADR-0029) so a hand-edit shows up as
   drift rather than a silent overwrite (per ADR-0017).
8. **Invokes `maury doctor`** on the rendered output for budget
   and routing-guidance surfacing (per ADR-0047 §"Agent routing
   guidance").

## `maury agency validate`

The umbrella ADR owns `maury agency validate` because the
command validates the full distributed layer graph, spanning
all three sub-ADRs' concerns. The command replaces the earlier
`maury manifest validate` name — the manifest is now distributed
across per-repo marker files (per ADR-0037), not centralized in
a single file, so "manifest validate" no longer accurately
describes the scope. `maury manifest validate` remains as a
deprecation alias.

### Hard failures (validation fails)

- **Two repos with `layer: base` and the same `agency_id`.** The
  base layer is unique per agency (ADR-0037); a duplicate is an
  incoherent state, not a warning case.

### Warnings (validation surfaces issues; does not fail)

- **Sublayer URLs that resolve to repos missing
  `.meta/maury-marker.json`.** The sublayer is declared but not
  maury-aware; the consumer cannot determine the layer type.
- **`mode` sublayer entries carrying a `repo_mode` field.**
  `repo_mode` is meaningful only for `rules` sublayers (per
  ADR-0037); on a `mode` sublayer it is noise that suggests
  author confusion.
- **`rules` sublayer entries with no `repo_mode`.** The consumer
  has not declared their access relationship; the advisory model
  (ADR-0038) cannot fire correctly.
- **Marker files using legacy `profile_<hex>` format in scope
  fields.** Schema migration required per
  [ADR-0030](0030-manifest-schema-migrations.md).
- **Agent name collisions across layers in the active render
  scope.** Surfaced even when render order resolves the
  collision, so the curator can decide if the resolution is
  intended. (Detail in ADR-0047.)
- **Filename violations of the canonical-tag-sort convention.**
  `env.macos.laptop.md` instead of `env.laptop.macos.md` —
  matching still works (order-independent), but the convention
  helps cross-repo consistency. (Detail in ADR-0046.)

### Informational (no action required)

- **Cycles in the sublayer graph.** Warned at registration (per
  ADR-0037); not a validation failure here either. The render
  engine's unified tiebreaker handles cyclic same-level conflicts
  via latest-commit timestamp.
- **`rules` repos with no semver tags.** Per ADR-0038; the
  advisory snooze menu gracefully degrades to patch-and-anchor.

## Cross-cutting consequences

The three sub-ADRs each carry their own consequences; the ones
that span all three layers live here:

- ✅ **Good:** The pipeline is decomposable. A reader can
  understand one layer (source, target, or condensation) in
  isolation and ignore the others until they need them.
- ✅ **Good:** The provenance chain runs end-to-end. Source-file
  naming (ADR-0046) encodes layer attribution in the filename
  itself; render-target surfaces (ADR-0047) preserve attribution
  per-line; LLM condensation (ADR-0048) preserves attribution
  through precept-span markers and last-deepest-wins merge
  attribution. Tenet 7 holds across the pipeline.
- ✅ **Good:** No-op render is fast. A pipeline that finds no
  source changes since `last-render.json` and no drift can
  short-circuit before the LLM pass, keeping the steady-state
  cost negligible.
- ⚖️ **Neutral:** The pipeline depends on an LLM backend by
  default. `--raw` is the air-gapped/offline escape hatch.
  (Detail in ADR-0048.)
- ❌ **Bad:** A render failure mid-pipeline can leave
  `~/.claude/` partially written if writes aren't atomic
  across surfaces. The atomic-write discipline must hold for
  every target writer; tested per surface.

## Build-order placement

The three sub-ADRs each ship their slice:

- **ADR-0046 (source-file naming).** The source-file scanner
  ships when the render engine first reads layer repos. Pairs
  with marker-file reading from ADR-0037.
- **ADR-0047 (target surfaces).** Six target writers, one per
  surface in the routing table. The settings.json writer
  requires the cross-layer merge from ADR-0023; the others are
  file-emission. Agent routing guidance ships in three pieces
  (registration-time scan, sync-time doctor invocation, init-
  time scaffold).
- **ADR-0048 (LLM condensation).** Built on top of the LLM
  backend in ADR-0012. Requires the provenance-comment emission
  step from the render engine (ADR-0047) and the post-LLM
  verification step. Can stage behind `--condense` for early
  implementation and flip to default once the verifier is
  reliable.

`maury agency validate` ships when at least two layers' worth of
state is real (post-ADR-0037 marker files; post-ADR-0046 source
naming). The hard-failure rule (two `layer: base` with same
`agency_id`) ships first; the warning rules accumulate as their
inputs become populated.

## Followups

The sub-ADRs each enumerate followups in their domain. The
cross-cutting ones:

- **Render-time provenance surface.** A user-facing `maury
  show-provenance <field>` command is an open followup spanning
  all three sub-ADRs.
- **Atomic-write discipline across surfaces.** All six target
  writers must compose into an atomic "either all succeed or
  none persist" transaction. The shape of that transaction (tmp
  directory + atomic rename of the whole `~/.claude/` tree?
  per-file tmp+rename with a rollback log?) is TBD.
- **No-op render fast path.** The short-circuit-when-no-changes
  pattern is named here; the implementation lives where the
  pipeline is wired together.

## Claude Code references

Detailed Claude Code citations live in the relevant sub-ADRs:

- Loading-semantics claims (CLAUDE.md, rules, skills, agents,
  settings.json, .mcp.json) live in
  [ADR-0047](0047-render-target-surfaces.md).
- No new Claude Code citations belong in
  [ADR-0046](0046-source-file-naming.md) or
  [ADR-0048](0048-llm-condensation.md) at this time.

This umbrella ADR makes no Claude Code behavior claims beyond
referencing those sub-ADR citations.

## Amendment history

- 2026-05-18 — split into three sub-ADRs: source file naming
  ([ADR-0046](0046-source-file-naming.md)), render target
  surfaces ([ADR-0047](0047-render-target-surfaces.md)), LLM
  condensation ([ADR-0048](0048-llm-condensation.md)). This
  ADR becomes the umbrella, keeping cross-cutting concerns
  (`maury agency validate`, pipeline shape, build-order
  coordination). One additive clarification accompanied the
  split: `maury manifest validate` is explicitly named as a
  deprecation alias for `maury agency validate` (the pre-split
  ADR named the rename but did not commit to keeping the old
  name as an alias). All other content moved without semantic
  change; the design was already decomposable, the split makes
  the decomposition explicit.
- 2026-05-11 — initial publication, single ADR covering all
  three layers + `maury agency validate`. Replaced by the
  umbrella+sub-ADR structure on 2026-05-18.
