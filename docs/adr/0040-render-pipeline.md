# ADR-0040: Render pipeline — source files, target surfaces, and LLM condensation

**Status:** Accepted
**Date:** 2026-05-11

## Related tenets

- [Tenet 1 — First, do no harm](../tenets.md#1-first-do-no-harm)
- [Tenet 2 — Consistency within a mode; controlled difference across modes](../tenets.md#2-consistency-within-a-mode-controlled-difference-across-modes)
- [Tenet 5 — The user arbitrates ambiguity](../tenets.md#5-the-user-arbitrates-ambiguity)
- [Tenet 7 — Provenance is mandatory](../tenets.md#7-provenance-is-mandatory)
- [Tenet 8 — Hand-edits are first-class input](../tenets.md#8-hand-edits-are-first-class-input)
- [Tenet 10 — Modularity over hardcoding](../tenets.md#10-modularity-over-hardcoding)
- [Tenet 11 — Explicit beats implicit, with conservative defaults](../tenets.md#11-explicit-beats-implicit-with-conservative-defaults)

## Related ADRs

- [ADR-0011](0011-anthropic-rubric-integration.md) — `maury doctor` content-quality rubric; invoked from the render pipeline
- [ADR-0012](0012-llm-backend.md) — LLM backend abstraction; powers the condensation pass
- [ADR-0016](0016-pluggable-repo-backends.md) — backend pluralism; no platform-specific affordances
- [ADR-0017](0017-drift-detection-and-reconciliation.md) — drift detection; render engine companion
- [ADR-0029](0029-maury-state-layout-contract.md) — `~/.claude/maury-state/` layout contract
- [ADR-0030](0030-manifest-schema-migrations.md) — when marker schema bumps are required
- [ADR-0037](0037-layer-taxonomy-and-repo-discovery.md) — layer taxonomy; marker file schema; render order; environment-as-content-attribute
- [ADR-0038](0038-precept-acquisition-model.md) — precept acquisition; advisory lifecycle; `repo_mode` governance
- [ADR-0023](0023-hook-installation-and-tool-resolution.md) — hook installation lifecycle; `settings.json` as the hook-ownership surface
- [ADR-0039](0039-bootstrap-and-host-lifecycle.md) — bootstrap flow; mode-scoped sync; mode change process

---

## Context and Problem Statement

[ADR-0037](0037-layer-taxonomy-and-repo-discovery.md) defines **what
layers are** and **what order they apply in**. It defines the
environment-as-content-attribute model (env-tagged sections inside
`base` and `mode` repos) and the host-as-content-attribute model
(host-tagged sections inside mode repos). It does not specify:

1. **How source content is laid out inside a layer repo.** A curator
   editing `mode-work.git` needs to know what filenames maury reads,
   what their naming convention is, and how env-/host-tagging is
   expressed on disk.
2. **What maury writes to `~/.claude/`.** Claude Code has more than
   one configuration surface — CLAUDE.md, `.claude/rules/`,
   `.claude/skills/`, `.claude/agents/`, `settings.json`,
   `.mcp.json`. Each surface has different loading semantics. Which
   source content goes where, and why?
3. **Whether maury post-processes content before writing.** Naive
   concatenation of all matching layer content blows past Claude
   Code's always-on context budget. An LLM condensation pass can
   recover budget without losing meaning, but only if precept
   content is preserved verbatim (per
   [ADR-0038](0038-precept-acquisition-model.md), precepts are the
   user's explicit commitments to upstream conventions and must not
   be paraphrased away).
4. **How the pipeline steers authors away from misusing surfaces.**
   The most common failure mode in hand-authored Claude Code
   configurations is putting task-specific procedural content into
   always-on memory (CLAUDE.md or unconditional rules), where it
   permanently consumes context budget on every session. Agents are
   the right surface for task-specific content, but only if maury
   surfaces routing guidance at the moments when authors are paying
   attention.

This ADR establishes:

- The **source-file naming convention** layer repos use at the repo
  root.
- The **full render-target surface** maury writes to inside
  `~/.claude/`, with the routing principle that determines which
  source content goes where.
- The **agents source layout** (one flat `.md` per agent) and the
  collision rule when multiple layers define an agent of the same
  name.
- The **LLM condensation pass** as the default sync behavior, with
  design-level (not confirmation-gate) precept preservation.
- The **agent routing guidance** that fires at three points in the
  workflow to steer task-specific content out of always-on memory.
- The **`maury agency validate`** command (renamed from the earlier
  `maury manifest validate`) that checks the full distributed layer
  graph for inconsistencies.

The non-negotiables carried over from earlier ADRs:

- **Tenet 1.** Render must not silently produce output that
  contradicts the user's explicit precepts. Precept-sourced content
  is preserved verbatim through the condensation pass by design, not
  by a confirmation step the user can fatigue out of.
- **Tenet 7.** Every line in the rendered output is provenance-
  bearing — the render engine knows which layer it came from,
  because both override-cascade and the condensation pass depend on
  that knowledge.
- **Tenet 8.** Hand-edits to `~/.claude/` are detected as drift (per
  [ADR-0017](0017-drift-detection-and-reconciliation.md)); the
  render pipeline does not silently overwrite them.
- **Backend pluralism (Tenet 10).** Source layout, target layout,
  and the condensation pass must work on every git-compatible
  backend with no platform-specific surface.

## Decision Drivers

- Source layout must be **obvious at a glance** — a curator opening a
  layer repo should be able to identify every content file and what
  it controls without a tutorial. The taxonomy collapses to a small
  set of filename patterns at the repo root, not a directory tree.
- Target layout must respect **Claude Code's actual surface
  semantics** — CLAUDE.md, rules, skills, agents, and settings.json
  load and reload differently; routing source content to the wrong
  surface produces silent functional bugs (e.g., a rule that should
  hot-reload mid-session but does not).
- Always-on context budget is **finite and precious**. The
  condensation pass exists because naive layer concatenation blows
  past the practical budget; the pass is the default, not a flag.
- Precept content must be **preserved verbatim through the
  condensation pass** as a structural invariant. Asking the user to
  confirm preservation on every sync produces alert fatigue; the
  invariant must hold without user attention.
- **Author guidance** is most useful at the moments the author is
  attending — registration, sync, and authoring init — not buried in
  docs.

---

## Considered Options

### Source file naming inside a layer repo

**Option A — Directory tree by dimension.** Layer repos use nested
subdirectories: `env/macos/laptop.md`, `host/host_abc123/secrets.md`,
`agents/research.md`. Cleanly organized; deep nesting.

- Rejected: a curator editing two related sections opens four
  directories to find them. Tag intersection (e.g., `macos` AND
  `laptop`) has no canonical path, requiring an arbitrary nesting
  order. Tooling that does flat scans (most editor "open file"
  dialogs) loses visibility into the layer's full content.

**Option B — Flat files at the repo root with dotted-name tagging
(chosen).** Every content file lives at the repo root. Filename
encodes the tag set:
`base.md`, `env.macos.md`, `env.macos.laptop.md`,
`host.host_abc123.md`, `agents/research.md`. Tag-intersection
filenames sort alphabetically (canonical order for consistency);
matching is order-independent.

- Adopted: one directory listing is the layer's full content
  inventory. No arbitrary nesting decisions. Tag-intersection is a
  single dotted filename, not a nested path. The `agents/`
  subdirectory is the single exception, justified by the agents-
  surface having a different rendering model (one-file-per-agent
  flat under `~/.claude/agents/`, not concatenated).

**Option C — Inline tag attributes inside a single big content
file.** A `content.md` with `<!-- tag: env=macos -->` blocks
throughout.

- Rejected: undermines locality (every change touches one giant
  file), conflicts more often in multi-author scenarios, and forces
  the render engine to parse content semantics rather than
  filenames.

### LLM condensation: default vs opt-in

**Option A — Condensation is opt-in (`maury sync --condense`).**
Default sync emits raw layer concatenation; the user opts in to LLM
post-processing.

- Rejected: naive concatenation blows past the always-on context
  budget for any non-trivial layer set. Most users would need
  `--condense` every time; making it the default reduces friction
  and matches the actual desired behavior. The escape hatch
  (`--raw`) remains for debug and audit.

**Option B — Condensation is the default; `--raw` opts out
(chosen).** `maury sync` runs the LLM pass. `maury sync --raw`
emits the unprocessed concatenation for inspection.

- Adopted: aligns the default with the typical user need.
  Precept preservation is enforced at the prompt level (see below),
  so the default is safe.

**Option C — No LLM pass; rely on authors to keep layers terse.**

- Rejected: layer content accumulates across modes and rules repos
  the user does not personally curate. The pass exists precisely to
  recover budget from content the user did not write.

### Agent definitions: synthesized vs explicit

**Option A — Maury synthesizes agent definitions from rules
content.** The render engine analyzes rules files, detects
task-specific clusters, and auto-generates `agents/<name>.md` stubs.

- Rejected: a useful agent definition needs behavioral framing,
  tool restrictions, and model-selection metadata — authorial
  intent maury cannot reliably infer from rules content. Synthesis
  would produce low-quality agents that the user would then have to
  fix or delete. Tenet 11 (explicit beats implicit) applies.

**Option B — Agent definitions are explicitly authored (chosen).**
Layer repos contain `agents/<name>.md` files with frontmatter
(`name`, `description`) and a body that is the agent's system
prompt. Maury renders them through to `~/.claude/agents/<name>.md`
without semantic interpretation.

- Adopted: maury is a routing-and-condensation engine for explicit
  content, not a content generator. The author-guidance surface
  (below) steers authors toward writing agents when their content
  is task-specific, but maury does not write the agents itself.

---

## Decision Outcome

### Source file naming convention

Layer repos (`base`, `mode`, `rules`) keep all content files at the
**repo root** — there is no `content/` or `src/` subdirectory. The
render engine identifies content files by filename pattern:

| Pattern | Applies when | Notes |
|---|---|---|
| `base.md` | always | unconditional content, applied to every host that reaches this layer |
| `env.<tag>.md` | host's `environment_tags` includes `<tag>` | single-tag environment match |
| `env.<tag1>.<tag2>.md` | host's tags include all listed tags | multi-tag AND; canonical alphabetical sort |
| `env.<tag1>.<tag2>.<tag3>.md` | host's tags include all listed tags | three-tag AND; same convention |
| `host.host_<hex>.md` | host's `host_<hex>` ID matches | machine-specific content; escape hatch = private narrow child mode repo per ADR-0037 |
| `agents/<name>.md` | always (subject to collision rule) | one flat file per agent definition; subdirectory exception like `skills/` |
| `skills/<name>/SKILL.md` | always | skill definition; mirrors the `~/.claude/skills/<name>/SKILL.md` target path verbatim |

**Naming rules:**

- Dots separate **tags** in env filenames. Hyphens are part of
  individual tag names (`env.low-battery.md` — one tag named
  `low-battery`; `env.low.battery.md` — two tags `low` and `battery`,
  which is almost certainly an author mistake).
- Tag names are matched against the host's `environment_tags` from
  the mode repo's `hosts` dict (per ADR-0037's marker schema).
- Multi-tag filenames use **canonical alphabetical order** for
  consistency (`env.laptop.macos.md`, not `env.macos.laptop.md`).
  Matching is order-independent; the canonical sort is a
  *convention* enforced by `maury agency validate` as a
  should-fix, not a hard requirement.
- The `env.` prefix marks environment-tagged files; the `host.`
  prefix marks host-tagged files; `base.md` is the unconditional
  file. Anything else at the repo root is ignored by the render
  engine (READMEs, license files, governance metadata under
  `.meta/`).
- Specificity follows ADR-0037's CSS-selector model: the count of
  required tag conditions on a file is its specificity. A
  three-tag file beats a two-tag file beats a one-tag file beats
  `base.md` for content that conflicts.

#### Example layer repo

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

A host registered to `mode-work` with `environment_tags: ["macos",
"laptop"]` renders `base.md`, `env.macos.md`, and
`env.laptop.macos.md` from this layer; `env.low-battery.md` and
`host.host_abc123.md` do not apply.

---

### Full render-target surface

Maury renders into **every** Claude Code configuration surface that
participates in session behavior. The surfaces and their loading
semantics — as documented by Claude Code itself — are:

| Target path | Source content | Loading semantics |
|---|---|---|
| `~/.claude/CLAUDE.md` | base + mode universal always-on content | always-on at session start; not hot-reloaded mid-session; new session required to pick up changes ([cc-memory]) |
| `~/.claude/rules/<name>.md` (no `paths:` frontmatter) | unconditional rules content | always-on at session start, similar to CLAUDE.md; **does** live-reload mid-session — empirical (verify against current release) |
| `~/.claude/rules/<name>.md` (with `paths:` frontmatter) | path-scoped rules content | lazy-loaded when a matching file is read; lost on `/compact` until re-triggered; live-reloads mid-session — empirical (verify against current release) |
| `~/.claude/skills/<name>/SKILL.md` | skill definitions | live-reload mid-session — empirical (verify against current release) |
| `~/.claude/agents/<name>.md` | subagent definitions (flat files, not subdirectories) | not hot-reloaded; session restart required to pick up changes ([cc-subagents]) |
| `~/.claude/settings.json` | hooks, permissions, environment variables; all layers merged | structured config; loaded at session start ([cc-settings]) |
| `~/.claude/.mcp.json` | MCP server definitions | structured config; loaded at session start — assumption, needs verification |

**Routing principle.** Source content is routed to a target by its
**character**, not by which layer it lives in:

- **Always-on universal stable preferences** → `~/.claude/CLAUDE.md`.
  Kept lean (target ~200 lines total across CLAUDE.md plus
  unconditional rules files combined); reserved for content the user
  wants in every session without exception.
- **Mutable or domain-specific content that benefits from live
  reload** → `~/.claude/rules/*.md`. The author opts in by
  authoring the content in a way the routing logic recognizes (an
  explicit `paths:` frontmatter for path-scoped rules; an explicit
  "rule" header for unconditional rules — exact convention is a
  render-engine concern).
- **Structured config** (hooks, permissions, env vars) →
  `~/.claude/settings.json`. Per
  [ADR-0023](0023-hook-installation-and-tool-resolution.md), hooks
  are installed through this surface.
- **Invocable task-specific definitions with isolated context** →
  `~/.claude/agents/<name>.md`. Authored as `agents/<name>.md` in the
  layer repo. Each agent gets its own fresh context window on
  invocation. See the agents section below.
- **Reusable behavioral definitions with shared context** →
  `~/.claude/skills/<name>/SKILL.md`. Authored as
  `skills/<name>/SKILL.md` in the layer repo, mirroring the target
  path verbatim. Skills run in the main session context; agents run
  isolated. When the distinction matters, prefer agents for
  task-specific workflows and skills for reusable sub-procedures that
  benefit from seeing session history.
- **MCP server definitions** → `~/.claude/.mcp.json`. Structured
  config from all layers merged. Loading semantics are assumed
  session-start (needs verification — see Followups).

**LLM optimization scope.** The condensation pass operates only on
CLAUDE.md and unconditional rules files (no `paths:` frontmatter).
Path-scoped rules files are purpose-specific by definition;
condensing them is counterproductive (the author wrote 80 lines
about React testing for a reason; the LLM has no signal to know
which lines are redundant). Skills, agents, settings, and `.mcp.json`
pass through unchanged.

**`@import` in CLAUDE.md.** Supported, with the documented 5-hop
maximum ([cc-memory]). `@import` in rules files is empirical —
currently assumed not supported (only in CLAUDE.md); verify against
current release before using. The render engine does not emit
`@import` directives in rules files until verification confirms
support.

[cc-memory]: https://code.claude.com/docs/en/memory
[cc-subagents]: https://code.claude.com/docs/en/sub-agents
[cc-settings]: https://code.claude.com/docs/en/settings

---

### Agents surface design

A layer repo defines an agent by placing one flat markdown file at
`agents/<name>.md`. The file's frontmatter carries `name` and
`description` (both required per the Claude Code subagents
specification, [cc-subagents]); the body is the agent's system
prompt.

Maury renders each `agents/<name>.md` to
`~/.claude/agents/<name>.md`. Agents are flat files under the
agents directory — **never** subdirectories.

Agent definitions are **explicitly authored**. Maury does not
synthesize agent definitions from rules content. The rationale (see
Considered Options above) is that a useful agent definition needs
behavioral framing, tool restrictions, and model-selection metadata
that maury cannot reliably infer.

#### Name collisions across layers

If two layers in the active render scope both define `agents/<name>.md`
for the same `<name>`, the collision is resolved by **render order**
(per ADR-0037): the deeper-attachment-point layer wins, and a
warning fires:

```
warning: superseding agent 'research' defined in
  git@github.com:eng-standards/rules-team.git
  with definition from
  git@codeberg.org:jdoe/mode-home.git
```

Last-wins is consistent with the render-order tiebreaker for all
other content; surfacing the warning ensures the curator notices
when an agent they expected to inherit got replaced.

#### Loading semantics

Subagent definitions are **not hot-reloaded** ([cc-subagents]).
Editing a synced agent definition mid-session requires a session
restart to take effect. The render pipeline does not emit warnings
about this on every render; instead, `maury sync` reports counts of
new and changed agents so the user can decide whether to restart.

---

### LLM condensation pass

The condensation pass is the **default** behavior of `maury sync`.
Raw concatenated output is the exception, available for debug and
audit.

```sh
maury sync                  # render + LLM condense (default)
maury sync --raw            # render only; no LLM pass (debug/audit)
maury sync --dry-run        # show what would change; do not write
maury sync --raw --dry-run  # show what raw render would produce
```

#### What the pass does

The condensation pass operates on the concatenated CLAUDE.md plus
unconditional rules files content. It:

- Identifies **redundant or overlapping rules** across layers (e.g.,
  both `base` and `mode` say "use 2-space indentation" — keep one).
- **Rephrases verbose rules** more concisely while preserving
  meaning.
- **Merges semantically equivalent rules** declared in different
  layers, attributing the merged result to the deepest contributing
  layer for provenance.
- Targets the ~200-line total budget for CLAUDE.md plus
  unconditional rules files combined. This is a soft target; the
  pass does not refuse to emit longer output when the source
  genuinely requires it.

#### Precept preservation — design, not confirmation

Precept-sourced content (content from sublayers declared with
`repo_mode: pr` or `ro`, per ADR-0038) **must not** be paraphrased,
merged, or summarized by the condensation pass. The user has
committed to the precept upstream; rephrasing it locally produces
silent drift from the upstream wording.

The enforcement mechanism is **structural**, not
confirmation-gated:

1. Before the LLM pass runs, the render engine emits **HTML
   provenance comments** around every precept-sourced span:
   ```html
   <!-- maury:precept-begin source=git@github.com:eng-standards/rules-linting.git -->
   ...precept content verbatim...
   <!-- maury:precept-end -->
   ```
2. The LLM prompt **explicitly instructs** the model to preserve
   any text between `maury:precept-begin` and `maury:precept-end`
   markers verbatim — no paraphrasing, no merging with adjacent
   content, no removal.
3. Convention-sourced content (from `repo_mode: rw` sublayers, plus
   base and mode universal content) is eligible for condensation.
4. After the LLM pass, the render engine verifies that every input
   precept span appears verbatim in the output. A precept-span
   mismatch is a render failure; the pipeline falls back to raw
   output and surfaces the LLM regression for review.

Confirmation gates were considered and rejected: requiring the user
to approve precept preservation on every sync produces alert
fatigue, and the user's confirmation does not actually verify
anything (they cannot diff the LLM output against the precepts in
their head). Structural enforcement at the prompt level, combined
with post-hoc verification, is safer and lower-friction.

#### LLM backend

The condensation pass uses the LLM backend abstraction defined in
[ADR-0012](0012-llm-backend.md). The backend is pluggable; users
who do not want any LLM pass run `maury sync --raw` exclusively.

---

### Agent routing guidance

A common authoring mistake is putting task-specific procedural
content into rules files (always-on, burns context budget on every
session) when it belongs in agents (invoked on demand, zero baseline
cost). Maury surfaces routing guidance at **three** points in the
workflow — the moments when authors are paying attention:

#### 1. `maury repos register` — source preflight (registration time)

When a curator runs `maury repos register <url>` against a layer
repo, maury scans the source repo before render and flags:

- **Task-specific rules files.** A rules file (or unconditional
  rules content inside a `base`/`mode` repo) is flagged when its
  content has heuristic markers of task-specificity — large size,
  imperative or procedural tone, action-oriented language ("first,
  do X; then, do Y; finally..."). The flag surfaces as a
  recommendation:
  ```
  hint: <file> looks task-specific (procedural, action-oriented).
        Consider defining an agent instead — task-specific rules
        consume always-on context budget on every session.
  ```
- **Missing `.meta/maury-marker.json`.** Informational only; pairs
  with the retrofit path in ADR-0037.

The output is **recommendations, not errors**. Registration
proceeds; the user decides whether to act. Surfacing at
registration time reinforces maury's value at the moment the user is
in setup mode and most receptive to guidance.

#### 2. `maury sync` — full doctor pass on rendered output

After render, `maury sync` invokes `maury doctor` (per
[ADR-0011](0011-anthropic-rubric-integration.md)) on the rendered
output. Doctor checks include:

- **Always-on context budget utilization.** Total line count and
  estimated token count across CLAUDE.md plus unconditional rules
  files. Flagged as a warning if approaching the soft target;
  flagged as an issue if exceeding it substantially.
- **Rules vs. agent routing across the full rendered set.**
  Task-specific content detected in rules surfaces with a
  recommendation to extract it into an agent.
- **Best-practices rubric.** Per ADR-0011, the broader
  content-quality rubric runs against the rendered output.

The doctor output is informational; the sync succeeds regardless.

#### 3. `maury repo init` — authoring-time steering (rules repos)

Per [ADR-0038](0038-precept-acquisition-model.md), `maury repo init`
is **rules-repo-only** — it bootstraps the semver baseline,
governance file, and post-commit hook for a new rules repo. The
ambient-vs-task-specific question is the natural opener for that
flow, since a rules repo's entire purpose is one or the other:

```
What kind of content will this rules repo hold?
  [a] Ambient rules — always active in every session
      (writes a base.md scaffold)
  [t] Task-specific — invoked on demand as an agent
      (writes an agents/<name>.md scaffold)
  [b] Both
```

The answer drives the initial scaffold. Task-specific content is
steered to the agents surface from the start, rather than being
inappropriately added to ambient files and discovered later by the
doctor pass.

For `base` and `mode` repos, the equivalent steering happens at
`maury repos register` source preflight (point 1 above), where
existing task-specific content is flagged with a routing hint.
There is no separate `maury repo init` flow for base/mode repos.

---

### `maury agency validate`

`maury agency validate` validates the full distributed layer graph.
The command replaces the earlier `maury manifest validate` name —
the manifest is now distributed across per-repo marker files (per
ADR-0037), not centralized in a single file, so "manifest validate"
no longer accurately describes the scope.

#### Hard failures (validation fails)

- **Two repos with `layer: base` and the same `agency_id`.** The
  base layer is unique per agency (ADR-0037); a duplicate is an
  incoherent state, not a warning case.

#### Warnings (validation surfaces issues; does not fail)

- **Sublayer URLs that resolve to repos missing
  `.meta/maury-marker.json`.** The sublayer is declared but not
  maury-aware; the consumer cannot determine the layer type.
- **`mode` sublayer entries carrying a `repo_mode` field.**
  `repo_mode` is meaningful only for `rules` sublayers (per
  ADR-0037); on a `mode` sublayer it is noise that suggests author
  confusion.
- **`rules` sublayer entries with no `repo_mode`.** The consumer
  has not declared their access relationship; the advisory model
  (ADR-0038) cannot fire correctly.
- **Marker files using legacy `profile_<hex>` format in scope
  fields.** Schema migration required per
  [ADR-0030](0030-manifest-schema-migrations.md).
- **Agent name collisions across layers in the active render
  scope.** Surfaced even when render order resolves the collision,
  so the curator can decide if the resolution is intended.
- **Filename violations of the canonical-tag-sort convention.**
  `env.macos.laptop.md` instead of `env.laptop.macos.md` —
  matching still works (order-independent), but the convention
  helps cross-repo consistency.

#### Informational (no action required)

- **Cycles in the sublayer graph.** Warned at registration (per
  ADR-0037); not a validation failure here either. The render
  engine's unified tiebreaker handles cyclic same-level conflicts
  via latest-commit timestamp.
- **`rules` repos with no semver tags.** Per ADR-0038; the
  advisory snooze menu gracefully degrades to patch-and-anchor.

---

## Consequences

- **Good:** Flat-file source layout means a curator opening a layer
  repo sees every content file in one directory listing. No
  arbitrary directory nesting decisions. Tag intersections are a
  single dotted filename rather than a nested path.
- **Good:** Every Claude Code configuration surface is in scope.
  Skills, agents, MCP, and settings.json all render through maury;
  the user's full session shape is reproducible from layer state.
- **Good:** Routing principle is content-character-based, not
  layer-type-based. The same routing logic handles base, mode, and
  rules content uniformly; the layer just provides the source.
- **Good:** Condensation is the default. Users get budget-aware
  output without remembering a flag; `--raw` is the audit escape
  hatch.
- **Good:** Precept preservation is structurally enforced — HTML
  comment markers plus prompt-level instructions plus post-hoc
  verification. No confirmation fatigue.
- **Good:** Agent routing guidance fires at three attended moments
  (registration, sync, init). Authors get the recommendation while
  they are in the right mental mode to act on it.
- **Good:** Explicitly-authored agents (not synthesized) keep maury
  in its lane as a routing and condensation engine. Authors retain
  full intent over agent behavior.
- **Good:** `maury agency validate` accurately names what it does
  — validates the agency, which is a distributed structure.
- **Neutral:** Canonical-tag-sort filename convention is enforced
  as a should-fix, not a hard rule. Matching is order-independent,
  so the convention is for human readability only.
- **Neutral:** The LLM condensation pass is a non-deterministic
  step. `--raw` provides the deterministic alternative for users
  who need reproducible output (e.g., for audit or compliance).
- **Neutral:** Several Claude Code loading-semantics claims (rules
  live-reload, skills live-reload, .mcp.json loading) are marked
  empirical and need verification against the current release.
  Behavior change in Claude Code could invalidate the rules/agent
  routing decisions.
- **Bad:** The condensation pass requires an LLM backend
  (per ADR-0012). Air-gapped or offline installations must use
  `--raw` exclusively, which means they bear the full always-on
  budget cost of unfiltered layer concatenation.
- **Bad:** Authors who insist on putting task-specific content in
  rules files (despite three guidance surfaces) can do so. Maury
  advises, does not block, consistent with the broader
  advise-do-not-block stance from ADR-0037.
- **Bad:** Precept-span verification adds a post-LLM check that can
  reject otherwise-valid output if the model paraphrases despite
  the instructions. The fallback to raw output is safe, but the
  user sees a render-failure surface they would not have seen with
  a less strict invariant.

### Confirmation

- `maury sync` runs the LLM condensation pass by default;
  `maury sync --raw` skips it; `maury sync --dry-run` reports
  changes without writing. Tested by running both modes against a
  fixture agency and asserting output divergence (raw is longer,
  condensed is shorter) and content equivalence (no precept span
  is altered).
- Every Claude Code surface listed in the routing table is written
  during a full sync. Tested by asserting file existence and
  non-empty content for each path after sync against a fixture
  agency that includes content for every surface.
- Agent name collisions across layers produce the documented
  warning and last-wins resolution. Tested by registering two
  layers with identical agent names and asserting both the
  rendered file content (from the deeper attachment) and the
  warning emission.
- Precept-span preservation is verified by the post-LLM check.
  Tested by injecting a deliberately-paraphrasable precept and
  asserting the pass either preserves it verbatim or falls back to
  raw output and surfaces a regression.
- `maury repos register` flags task-specific rules files with the
  documented hint. Tested against fixtures with imperative,
  procedural content.
- `maury agency validate` produces the documented hard-failure
  (two `layer: base` with same `agency_id`) and the documented
  warnings (missing markers, misplaced `repo_mode`, missing
  `repo_mode`, legacy `profile_<hex>`, agent name collisions,
  canonical-sort violations).

---

## Build-order placement

- **When implemented — source file scanning.** The render engine
  must scan layer repos for the documented filename patterns. Pairs
  with marker-file reading from ADR-0037.
- **When implemented — target surface writers.** Six writers (one
  per surface in the routing table), each respecting the surface's
  loading semantics. The settings.json writer must perform a
  cross-layer merge (hooks + permissions + env vars); the others
  are file-emission.
- **When implemented — LLM condensation pass.** Built on top of
  the LLM backend in ADR-0012. Requires the provenance-comment
  emission step from the render engine and the post-LLM
  verification step.
- **When implemented — agent routing guidance.** Three surfaces:
  registration-time scan, sync-time doctor invocation (paired with
  ADR-0011), authoring-time scaffold prompt in `maury repo init`.
- **When implemented — `maury agency validate`.** Reads every
  marker file reachable from base; applies the hard-failure /
  warning / informational rules above.

---

## Followups

- **Path-scoped rules `@import` verification.** Empirical claim
  ("@import is documented for CLAUDE.md, unverified for rules
  files"). Verify against the current Claude Code release; if
  supported, extend the render engine to emit `@import` directives
  in rules files where useful.
- **Rules file live-reload verification.** Empirical claim
  ("unconditional rules files live-reload mid-session, similar to
  path-scoped rules"). Verify and document.
- **Skills live-reload verification.** Empirical claim. Verify and
  document.
- **`.mcp.json` loading semantics.** Assumption ("loaded at session
  start"). Verify and document; if it hot-reloads, sync emissions
  can take effect without a restart.
- **Task-specificity heuristics for the registration-time scan.**
  The initial heuristic (size, imperative tone, action-oriented
  language) is a starting point. Refinement based on real corpora
  of task-specific vs. ambient content is a learning-loop activity.
- **Settings.json merge conflict surface.** When two layers
  declare conflicting permission entries or hook commands, the
  merge rule needs a tiebreaker beyond "last wins by render order"
  — for example, additive merging of permission allow-lists vs.
  override semantics for hook commands. Design TBD.
- **MCP server cross-layer merge.** Same shape as settings.json
  merging; design follows once the basic case is implemented.
- **Render-time provenance surface.** Per ADR-0037 §"Render-time
  provenance," provenance is tracked internally. A user-facing
  `maury show-provenance <field>` command is an open followup;
  precept-span markers are the first step toward exposing it.
- **Condensation prompt tuning.** The LLM prompt that instructs
  precept preservation and condensation goals is a living artifact;
  versioning and regression testing of the prompt belongs in the
  LLM-backend work area.

---

## Claude Code references

- [Claude Code memory — `CLAUDE.md` loading and `@import`][cc-memory]
  — establishes that CLAUDE.md is read at session start, supports
  `@import` with a 5-hop maximum, and is not hot-reloaded; basis for
  the always-on routing rule and the `@import` support claim.
- [Claude Code subagents — `.claude/agents/`][cc-subagents] —
  establishes the flat-file layout under `~/.claude/agents/`, the
  required frontmatter fields (`name`, `description`), and the
  not-hot-reloaded loading semantics for agents.
- [Claude Code settings — `settings.json`][cc-settings] — establishes
  the settings.json schema for hooks, permissions, and environment
  variables; basis for the structured-config routing rule.

Empirical claims (verify against current release):

- Unconditional rules files (no `paths:` frontmatter) live-reload
  mid-session.
- Path-scoped rules files (with `paths:` frontmatter) are
  lazy-loaded on matching file read and lost on `/compact` until
  re-triggered.
- Skills files under `~/.claude/skills/<name>/SKILL.md` live-reload
  mid-session.
- `@import` is not supported in rules files (only in CLAUDE.md).

Assumptions (need verification):

- `~/.claude/.mcp.json` is loaded at session start; reload semantics
  unknown.

---

## Amendment history
