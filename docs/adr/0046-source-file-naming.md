# ADR-0046: Source file naming inside a layer repo

**Status:** Accepted
**Date:** 2026-05-18
**Sub-ADR of:** [ADR-0040](0040-render-pipeline.md)

## Related tenets

- [Tenet 2 — Consistency within a mode; controlled difference across modes](../tenets.md#2-consistency-within-a-mode-controlled-difference-across-modes)
- [Tenet 10 — Modularity over hardcoding](../tenets.md#10-modularity-over-hardcoding)
- [Tenet 11 — Explicit beats implicit, with conservative defaults](../tenets.md#11-explicit-beats-implicit-with-conservative-defaults)

## Related ADRs

- [ADR-0037](0037-layer-taxonomy-and-repo-discovery.md) — layer
  taxonomy, marker file schema, environment-tag and host-tag models
- [ADR-0040](0040-render-pipeline.md) — umbrella; this ADR
  carries the source-file-naming layer of the render pipeline design
- [ADR-0047](0047-render-target-surfaces.md) — sibling sub-ADR;
  what maury writes given the source naming established here

## TL;DR

A layer repo (`base`, `mode`, `rules`) keeps every content file at
the repo root with **flat dotted-name tagging**: `base.md`,
`env.macos.md`, `env.laptop.macos.md`, `host.host_abc123.md`. Tags
combine with **AND semantics** in a single filename — dots separate
tags, hyphens are part of tag names. Multi-tag filenames sort
**canonically alphabetical** as a should-fix convention (matching
is order-independent). The `agents/` and `skills/` subdirectories
are documented exceptions because their target shape is one-file-
per-thing, not concatenated. Trade-off: tag-intersection composes
better as filenames than nested paths, at the cost of a slightly
busier `ls` output for repos with many tags.

## Context and Problem Statement

[ADR-0037](0037-layer-taxonomy-and-repo-discovery.md) defines
**what layers are** and **what order they apply in**. It defines
the environment-as-content-attribute and host-as-content-attribute
models. It does **not** specify how source content is laid out
inside a layer repo — what filenames maury reads, what their
naming convention is, and how env-/host-tagging is expressed on
disk.

A curator editing `mode-work.git` needs to be able to:

- See every content file in one directory listing.
- Identify which conditions trigger each file from its name alone.
- Write a multi-tag intersection (e.g., "macOS AND laptop") in a
  way that does not arbitrarily nest one tag under the other.
- Trust that maury matches filenames consistently across repos.

## Decision Drivers

- **Locality.** The full content of a layer should be visible in
  one directory listing.
- **No arbitrary nesting decisions.** Multi-dimensional tagging
  (env × host × precept) should not force the curator to pick an
  order to nest.
- **Filename-level matching.** The render engine should identify
  applicability from the filename alone, without parsing file
  bodies.
- **Tool affordance.** Editor "open file" dialogs and shell
  globbing both work better against a flat namespace than a deep
  tree.
- **One exception is acceptable when it matches the target
  shape.** Agents and skills render one-file-per-thing at the
  target; mirroring that at the source is a justified exception
  to the flat-file rule.

## Considered Options

### A — Directory tree by dimension

Layer repos use nested subdirectories: `env/macos/laptop.md`,
`host/host_abc123/secrets.md`, `agents/research.md`.

- ❌ A curator editing two related sections opens multiple
  directories to find them.
- ❌ Tag intersection (e.g., `macos` AND `laptop`) has no
  canonical path, requiring an arbitrary nesting order.
- ❌ Tooling that does flat scans (editor "open file" dialogs)
  loses visibility into the layer's full content.

### B — Flat files at the repo root with dotted-name tagging (chosen)

Every content file lives at the repo root. The filename encodes
the tag set: `base.md`, `env.macos.md`, `env.macos.laptop.md`,
`host.host_abc123.md`. Tag-intersection filenames sort
alphabetically (canonical order for consistency); matching is
order-independent.

- ✅ One directory listing is the layer's full content inventory.
- ✅ No arbitrary nesting decisions.
- ✅ Tag-intersection is a single dotted filename, not a nested
  path.
- ⚖️ The `agents/` and `skills/` subdirectories are documented
  exceptions, justified by their target shape differing from the
  concatenation surfaces.

### C — Inline tag attributes inside a single big content file

A `content.md` with `<!-- tag: env=macos -->` blocks throughout.

- ❌ Undermines locality (every change touches one giant file).
- ❌ Conflicts more often in multi-author scenarios.
- ❌ Forces the render engine to parse content semantics rather
  than filenames.

## Decision Outcome

Chosen: **Option B** — flat files at the repo root with
dotted-name tagging, with documented `agents/` and `skills/`
subdirectory exceptions.

### Filename patterns

Layer repos (`base`, `mode`, `rules`) keep all content files at
the **repo root** — there is no `content/` or `src/`
subdirectory. The render engine identifies content files by
filename pattern:

| Pattern | Applies when | Notes |
|---|---|---|
| `base.md` | always | unconditional content, applied to every host that reaches this layer |
| `env.<tag>.md` | host's `environment_tags` includes `<tag>` | single-tag environment match |
| `env.<tag1>.<tag2>.md` | host's tags include all listed tags | multi-tag AND; canonical alphabetical sort |
| `env.<tag1>.<tag2>.<tag3>.md` | host's tags include all listed tags | three-tag AND; same convention |
| `host.host_<hex>.md` | host's `host_<hex>` ID matches | machine-specific content; escape hatch = private narrow child mode repo per ADR-0037 |
| `agents/<name>.md` | always (subject to collision rule) | one flat file per agent definition; subdirectory exception |
| `skills/<name>/SKILL.md` | always | skill definition; mirrors the `~/.claude/skills/<name>/SKILL.md` target path verbatim |

### Naming rules

- **Dots separate tags** in env filenames. Hyphens are part of
  individual tag names (`env.low-battery.md` — one tag named
  `low-battery`; `env.low.battery.md` — two tags `low` and
  `battery`, which is almost certainly an author mistake).
- **Tag names** are matched against the host's `environment_tags`
  from the mode repo's `hosts` dict (per ADR-0037's marker schema).
- **Multi-tag filenames use canonical alphabetical order** for
  consistency (`env.laptop.macos.md`, not `env.macos.laptop.md`).
  Matching is order-independent; the canonical sort is a
  *convention* enforced by `maury agency validate` as a
  should-fix, not a hard requirement.
- **The `env.` prefix** marks environment-tagged files; the
  `host.` prefix marks host-tagged files; `base.md` is the
  unconditional file. Anything else at the repo root is ignored
  by the render engine (READMEs, license files, governance
  metadata under `.meta/`).
- **Specificity** follows ADR-0037's CSS-selector model: the
  count of required tag conditions on a file is its specificity.
  A three-tag file beats a two-tag file beats a one-tag file
  beats `base.md` for content that conflicts. The same-
  specificity tiebreaker is render-order (per ADR-0037).

### Subdirectory exceptions

Two subdirectories are documented exceptions to the
flat-at-root rule because their target shape differs:

- **`agents/<name>.md`** — one file per agent. Subagents render
  one-file-per-name to `~/.claude/agents/<name>.md` (per
  [ADR-0047](0047-render-target-surfaces.md)); the source
  shape mirrors the target.
- **`skills/<name>/SKILL.md`** — one directory per skill (the
  skill's `SKILL.md` plus any supporting files the skill
  references). The source path mirrors the target path
  verbatim: `skills/<name>/SKILL.md` →
  `~/.claude/skills/<name>/SKILL.md`.

### Example layer repo

```
mode-work/
├── .meta/
│   └── maury-marker.json
├── base.md                          ← applies to every host in this mode
├── env.macos.md                     ← macOS-specific section
├── env.laptop.macos.md              ← macOS laptops only
├── env.low-battery.md               ← low-battery hosts only
├── host.host_abc123.md              ← machine-specific for one host
├── agents/
│   ├── research.md                  ← subagent: invoked on demand
│   └── code-review.md
└── skills/
    └── maury-stage/
        └── SKILL.md                 ← skill definition; path mirrors ~/.claude/skills/
```

A host registered to `mode-work` with
`environment_tags: ["macos", "laptop"]` renders `base.md`,
`env.macos.md`, and `env.laptop.macos.md` from this layer;
`env.low-battery.md` and `host.host_abc123.md` do not apply.

## Consequences

- ✅ **Good:** A curator opening a layer repo sees every content
  file in one directory listing. No arbitrary directory nesting
  decisions. Tag intersections are a single dotted filename
  rather than a nested path.
- ✅ **Good:** Filename-level matching keeps the render engine
  simple — applicability is a filename-glob question, not a body-
  parsing one.
- ⚖️ **Neutral:** Canonical-tag-sort is enforced as a should-fix
  by `maury agency validate`, not as a hard rule. Matching is
  order-independent; the convention is for human readability.
- ⚖️ **Neutral:** The `agents/` and `skills/` subdirectory
  exceptions are minor surface area; each maps a single
  subdirectory to a single target shape.
- ❌ **Bad:** A layer repo with many tag dimensions can end up
  with many root-level files (`env.macos.md`, `env.linux.md`,
  `env.macos.laptop.md`, ... etc). `ls` becomes noisier than a
  nested tree. The trade-off is that intersection-tagged files
  remain self-describing in their names.

### Confirmation

- `maury agency validate` (per ADR-0040) flags filename violations
  of the canonical-tag-sort convention as should-fix warnings.
- Render-engine unit tests cover each filename pattern across the
  full matrix of host environment-tag sets and host-id matches.

## Build-order placement

When the render engine is implemented (per ADR-0040 umbrella):
the source-file scanner reads the filename patterns documented
here from each layer repo's root, plus the `agents/` and
`skills/` subdirectory exceptions.

## Followups

- **Glob-tag patterns.** A future amendment may extend the
  filename convention with wildcard tags (e.g., `env.*.laptop.md`
  to mean "any OS on a laptop"). Out of scope for v1 — additive
  if useful.
- **Filename-collision detection across layers.** Currently the
  collision case is "two layers both have `agents/research.md`";
  the same pattern may surface for `env.macos.md` and other
  content files when one mode inherits from another. Detection
  and surfacing is a sibling concern in
  [ADR-0047](0047-render-target-surfaces.md) (which owns the
  render-target collision resolution).

## Claude Code references

This sub-ADR introduces no new Claude Code dependencies beyond
what [ADR-0047](0047-render-target-surfaces.md) cites for the
target-side surfaces it pairs with.

## Amendment history

- 2026-05-18 — initial publication. Split out of
  [ADR-0040](0040-render-pipeline.md) as the source-file-naming
  layer of the render pipeline design.
