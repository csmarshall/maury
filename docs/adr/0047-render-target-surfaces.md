# ADR-0047: Render target surfaces

**Status:** Accepted
**Date:** 2026-05-18
**Sub-ADR of:** [ADR-0040](0040-render-pipeline.md)

## Related tenets

- [Tenet 2 — Consistency within a mode; controlled difference across modes](../tenets.md#2-consistency-within-a-mode-controlled-difference-across-modes)
- [Tenet 7 — Provenance is mandatory](../tenets.md#7-provenance-is-mandatory)
- [Tenet 8 — Hand-edits are first-class input](../tenets.md#8-hand-edits-are-first-class-input)
- [Tenet 11 — Explicit beats implicit, with conservative defaults](../tenets.md#11-explicit-beats-implicit-with-conservative-defaults)

## Related ADRs

- [ADR-0011](0011-anthropic-rubric-integration.md) — `maury doctor`
  rubric, invoked as part of the post-sync routing-guidance surface
- [ADR-0017](0017-drift-detection-and-reconciliation.md) — drift
  detection; the render pipeline does not overwrite hand-edits
  silently
- [ADR-0023](0023-hook-installation-and-tool-resolution.md) — hooks
  are installed through `settings.json`; structured-config routing
- [ADR-0040](0040-render-pipeline.md) — umbrella; this ADR
  carries the target-surface layer of the render pipeline design
- [ADR-0038](0038-precept-acquisition-model.md) — precept-versus-
  convention distinction; precept routing influences which surface
  is appropriate
- [ADR-0046](0046-source-file-naming.md) — sibling sub-ADR; what
  maury reads given the target surfaces established here
- [ADR-0048](0048-llm-condensation.md) — sibling sub-ADR; the
  optimization that operates on the always-on surfaces named here

## TL;DR

Claude Code has **multiple** configuration surfaces with different
loading semantics, and maury renders into all of them. CLAUDE.md
and unconditional rules are always-on; path-scoped rules and
skills are lazy or hot-reload; agents and settings.json require a
session restart. The **routing principle** is content-character-
based: always-on universal stable preferences go to CLAUDE.md;
mutable or path-scoped content goes to `~/.claude/rules/`;
invocable task-specific work goes to `~/.claude/agents/`;
structured config goes to `settings.json`. **Agent routing
guidance** fires at three attended moments (registration, sync,
rules-repo init) to steer authors away from putting task-specific
content into always-on memory.

## Context and Problem Statement

Claude Code is **not** a single-file configuration system. It
loads:

- `CLAUDE.md` — always-on prose memory at session start.
- `~/.claude/rules/*.md` — rules, with two flavors: unconditional
  (always-on like CLAUDE.md) and path-scoped (lazy, triggered on
  matching file read).
- `~/.claude/skills/<name>/SKILL.md` — invocable skill
  definitions.
- `~/.claude/agents/<name>.md` — subagent definitions with
  isolated context.
- `~/.claude/settings.json` — hooks, permissions, environment
  variables.
- `~/.claude/.mcp.json` — MCP server definitions.

Each surface has **different loading semantics**: when it loads,
whether it hot-reloads, what happens on `/compact`. Routing
source content to the wrong surface produces silent functional
bugs — a rule that should hot-reload mid-session but does not,
an agent that consumes always-on context budget on every session.

[ADR-0037](0037-layer-taxonomy-and-repo-discovery.md) defines
**layers**; [ADR-0046](0046-source-file-naming.md) defines
**source-file naming**; this ADR defines **what maury writes** and
**which source content goes where**.

A second problem: a common authoring mistake is putting
task-specific procedural content into always-on rules files,
where it permanently consumes context budget on every session.
The right surface is agents (invoked on demand, zero baseline
cost), but only if maury surfaces routing guidance at the moments
when authors are paying attention.

## Decision Drivers

- **Match Claude Code's actual surface semantics.** Routing
  decisions must respect when each surface loads, whether it hot-
  reloads, and what happens on `/compact`.
- **Content character is the routing key, not layer type.** The
  same routing logic applies to base, mode, and rules content
  uniformly; the layer just provides the source.
- **Author guidance is most useful at attended moments.**
  Registration, sync, and authoring-init are when the curator is
  attending. Buried docs are not.
- **Explicit beats implicit (Tenet 11).** Agent definitions are
  explicitly authored, never synthesized from rules content —
  maury is a routing-and-condensation engine, not a content
  generator.
- **Tenet 8.** Hand-edits to `~/.claude/` are detected as drift
  (per [ADR-0017](0017-drift-detection-and-reconciliation.md));
  this ADR's render writes do not silently overwrite them.

## Considered Options

### A — Render only to CLAUDE.md

Treat CLAUDE.md as the single canonical surface; concatenate
everything (rules, skills, agents) into it.

- ❌ Defeats Claude Code's loading-semantics design: path-scoped
  rules become always-on; agents become inert prose; skills
  cannot be invoked. The user loses the surface affordances
  Claude Code provides.

### B — Render to every Claude Code configuration surface, character-routed (chosen)

Maury writes to CLAUDE.md, rules, skills, agents, settings.json,
and .mcp.json. Source content routes by character, not by layer
type.

- ✅ Respects Claude Code's loading semantics.
- ✅ The user's full session shape is reproducible from layer
  state, not just the prose memory.
- ⚖️ Several loading-semantics claims are empirical; behavior
  change in Claude Code could invalidate the routing for
  specific surfaces.

### C — Render to surfaces, but synthesize agents from rules content

Detect task-specific clusters in rules and auto-generate
`agents/<name>.md` stubs.

- ❌ A useful agent definition needs behavioral framing, tool
  restrictions, and model-selection metadata — authorial intent
  maury cannot reliably infer. Synthesis would produce
  low-quality agents the user has to fix or delete.
- ❌ Violates Tenet 11 (explicit beats implicit).

### Agent routing-guidance surfaces

Once Option B is chosen, the question is **when** maury surfaces
routing guidance:

- **A1 — Only at sync time.** Run guidance once, post-render.
  Misses authoring-time when the author is most receptive.
- **A2 — Only at registration time.** Misses ongoing
  authoring and post-render budget pressure.
- **A3 (chosen) — Three attended points: registration, sync,
  rules-repo init.** Each fires at a moment the author is
  attending; covers the full authoring lifecycle.

## Decision Outcome

Chosen: **Option B + A3** — render to every Claude Code
surface, character-routed; surface agent-routing guidance at
three attended moments.

### Full render-target surface

Maury renders into **every** Claude Code configuration surface
that participates in session behavior. The surfaces and their
loading semantics — as documented or empirically observed — are:

| Target path | Source content | Loading semantics |
|---|---|---|
| `~/.claude/CLAUDE.md` | base + mode universal always-on content | always-on at session start; not hot-reloaded mid-session; new session required to pick up changes ([cc-memory]) |
| `~/.claude/rules/<name>.md` (no `paths:` frontmatter) | unconditional rules content | always-on at session start, similar to CLAUDE.md; **does** live-reload mid-session — empirical (verify against current release) |
| `~/.claude/rules/<name>.md` (with `paths:` frontmatter) | path-scoped rules content | lazy-loaded when a matching file is read; lost on `/compact` until re-triggered; live-reloads mid-session — empirical (verify against current release) |
| `~/.claude/skills/<name>/SKILL.md` | skill definitions | live-reload mid-session — empirical (verify against current release) |
| `~/.claude/agents/<name>.md` | subagent definitions (flat files, not subdirectories) | not hot-reloaded; session restart required to pick up changes ([cc-subagents]) |
| `~/.claude/settings.json` | hooks, permissions, environment variables; all layers merged | structured config; loaded at session start ([cc-settings]) |
| `~/.claude/.mcp.json` | MCP server definitions | structured config; loaded at session start — assumption, needs verification |

### Routing principle

Source content is routed to a target by its **character**, not by
which layer it lives in:

- **Always-on universal stable preferences** → `~/.claude/CLAUDE.md`.
  Kept lean (target ~200 lines total across CLAUDE.md plus
  unconditional rules files combined); reserved for content the
  user wants in every session without exception.
- **Mutable or domain-specific content that benefits from live
  reload** → `~/.claude/rules/*.md`. The author opts in by
  authoring the content in a way the routing logic recognizes (an
  explicit `paths:` frontmatter for path-scoped rules; an
  explicit "rule" header for unconditional rules — exact
  convention is a render-engine concern).
- **Structured config** (hooks, permissions, env vars) →
  `~/.claude/settings.json`. Per
  [ADR-0023](0023-hook-installation-and-tool-resolution.md),
  hooks are installed through this surface.
- **Invocable task-specific definitions with isolated context** →
  `~/.claude/agents/<name>.md`. Authored as `agents/<name>.md` in
  the layer repo (per [ADR-0046](0046-source-file-naming.md)).
  Each agent gets its own fresh context window on invocation. See
  the agents section below.
- **Reusable behavioral definitions with shared context** →
  `~/.claude/skills/<name>/SKILL.md`. Authored as
  `skills/<name>/SKILL.md` in the layer repo, mirroring the target
  path verbatim. Skills run in the main session context (per
  [cc-skills]); agents run isolated. When the distinction
  matters, prefer agents for
  task-specific workflows and skills for reusable sub-procedures
  that benefit from seeing session history.
- **MCP server definitions** → `~/.claude/.mcp.json`. Structured
  config from all layers merged. Loading semantics are assumed
  session-start (needs verification — see Followups).

**`@import` in CLAUDE.md.** Supported, with the documented 5-hop
maximum ([cc-memory]). `@import` in rules files is empirical —
currently assumed not supported (only in CLAUDE.md); verify
against current release before using. The render engine does not
emit `@import` directives in rules files until verification
confirms support.

[cc-memory]: https://code.claude.com/docs/en/memory
[cc-subagents]: https://code.claude.com/docs/en/sub-agents
[cc-settings]: https://code.claude.com/docs/en/settings
[cc-skills]: https://code.claude.com/docs/en/skills

### Agents surface design

A layer repo defines an agent by placing one flat markdown file
at `agents/<name>.md` (per
[ADR-0046](0046-source-file-naming.md)). The file's frontmatter
carries `name` and `description` (both required per the Claude
Code subagents specification, [cc-subagents]); the body is the
agent's system prompt.

Maury renders each `agents/<name>.md` to
`~/.claude/agents/<name>.md`. Agents are flat files under the
agents directory — **never** subdirectories.

Agent definitions are **explicitly authored**. Maury does not
synthesize agent definitions from rules content (see Option C
above for the rejected alternative).

#### Name collisions across layers

If two layers in the active render scope both define
`agents/<name>.md` for the same `<name>`, the collision is
resolved by **render order** (per ADR-0037): the deeper-
attachment-point layer wins, and a warning fires:

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
restart to take effect. The render pipeline does not emit
warnings about this on every render; instead, `maury sync`
reports counts of new and changed agents so the user can decide
whether to restart.

### Agent routing guidance

A common authoring mistake is putting task-specific procedural
content into rules files (always-on, burns context budget on
every session) when it belongs in agents (invoked on demand, zero
baseline cost). Maury surfaces routing guidance at **three**
points in the workflow — the moments when authors are paying
attention:

#### 1. `maury repos register` — source preflight (registration time)

When a curator runs `maury repos register <url>` against a layer
repo, maury scans the source repo before render and flags:

- **Task-specific rules files.** A rules file (or unconditional
  rules content inside a `base`/`mode` repo) is flagged when its
  content has heuristic markers of task-specificity — large size,
  imperative or procedural tone, action-oriented language
  ("first, do X; then, do Y; finally..."). The flag surfaces as
  a recommendation:
  ```
  hint: <file> looks task-specific (procedural, action-oriented).
        Consider defining an agent instead — task-specific rules
        consume always-on context budget on every session.
  ```
- **Missing `.meta/maury-marker.json`.** Informational only;
  pairs with the retrofit path in ADR-0037.

The output is **recommendations, not errors**. Registration
proceeds; the user decides whether to act. Surfacing at
registration time reinforces maury's value at the moment the
user is in setup mode and most receptive to guidance.

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
- **Best-practices rubric.** Per ADR-0011, the broader content-
  quality rubric runs against the rendered output.

The doctor output is informational; the sync succeeds regardless.

#### 3. `maury repo init` — authoring-time steering (rules repos)

Per [ADR-0038](0038-precept-acquisition-model.md), `maury repo
init` is **rules-repo-only** — it bootstraps the semver baseline,
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
inappropriately added to ambient files and discovered later by
the doctor pass.

For `base` and `mode` repos, the equivalent steering happens at
`maury repos register` source preflight (point 1 above), where
existing task-specific content is flagged with a routing hint.
There is no separate `maury repo init` flow for base/mode repos.

## Consequences

- ✅ **Good:** Every Claude Code configuration surface is in
  scope. Skills, agents, MCP, and settings.json all render
  through maury; the user's full session shape is reproducible
  from layer state.
- ✅ **Good:** Routing principle is content-character-based, not
  layer-type-based. The same routing logic handles base, mode,
  and rules content uniformly.
- ✅ **Good:** Explicitly-authored agents keep maury in its lane
  as a routing-and-condensation engine. Authors retain full
  intent over agent behavior.
- ✅ **Good:** Agent routing guidance fires at three attended
  moments. Authors get the recommendation while they are in the
  right mental mode to act on it.
- ⚖️ **Neutral:** Several Claude Code loading-semantics claims
  (rules live-reload, skills live-reload, .mcp.json loading) are
  marked empirical and need verification against the current
  release. Behavior change in Claude Code could invalidate the
  routing decisions for specific surfaces. The
  [Followups](#followups) section enumerates each.
- ⚖️ **Neutral:** Routing-guidance heuristics for task-
  specificity are starting points. Refinement based on real
  corpora is a learning-loop activity.
- ❌ **Bad:** Authors who insist on putting task-specific content
  in rules files (despite three guidance surfaces) can do so.
  Maury advises, does not block, consistent with the broader
  advise-do-not-block stance from ADR-0037.

### Confirmation

- Every Claude Code surface listed in the routing table is
  written during a full sync. Tested by asserting file existence
  and non-empty content for each path after sync against a
  fixture agency that includes content for every surface.
- Agent name collisions across layers produce the documented
  warning and last-wins resolution. Tested by registering two
  layers with identical agent names and asserting both the
  rendered file content (from the deeper attachment) and the
  warning emission.
- `maury repos register` flags task-specific rules files with the
  documented hint. Tested against fixtures with imperative,
  procedural content.
- `maury sync`'s post-render doctor pass surfaces budget over-
  utilization and rules-vs-agent routing issues. Tested by
  rendering fixtures that exceed the soft budget and asserting
  the warning surface.

## Build-order placement

When the render engine is implemented (per ADR-0040 umbrella):

- **Target surface writers.** Six writers (one per surface in
  the routing table), each respecting the surface's loading
  semantics. The settings.json writer must perform a cross-layer
  merge (hooks + permissions + env vars); the others are file-
  emission.
- **Agent routing guidance.** Three surfaces: registration-time
  scan, sync-time doctor invocation (paired with ADR-0011),
  authoring-time scaffold prompt in `maury repo init` (per
  ADR-0038).

## Followups

- **Path-scoped rules `@import` verification.** Empirical claim
  ("@import is documented for CLAUDE.md, unverified for rules
  files"). Verify against the current Claude Code release; if
  supported, extend the render engine to emit `@import`
  directives in rules files where useful.
- **Rules file live-reload verification.** Empirical claim
  ("unconditional rules files live-reload mid-session, similar
  to path-scoped rules"). Verify and document.
- **Skills live-reload verification.** Empirical claim. Verify
  and document.
- **`.mcp.json` loading semantics.** Assumption ("loaded at
  session start"). Verify and document; if it hot-reloads, sync
  emissions can take effect without a restart.
- **Task-specificity heuristics for the registration-time scan.**
  The initial heuristic (size, imperative tone, action-oriented
  language) is a starting point. Refinement based on real
  corpora of task-specific vs. ambient content is a
  learning-loop activity.
- **Settings.json merge conflict surface.** When two layers
  declare conflicting permission entries or hook commands, the
  merge rule needs a tiebreaker beyond "last wins by render
  order" — for example, additive merging of permission allow-
  lists vs. override semantics for hook commands. Design TBD.
- **MCP server cross-layer merge.** Same shape as settings.json
  merging; design follows once the basic case is implemented.

## Claude Code references

- [Claude Code memory — `CLAUDE.md` loading and `@import`][cc-memory]
  — establishes that CLAUDE.md is read at session start, supports
  `@import` with a 5-hop maximum, and is not hot-reloaded; basis
  for the always-on routing rule and the `@import` support claim.
- [Claude Code subagents — `.claude/agents/`][cc-subagents] —
  establishes the flat-file layout under `~/.claude/agents/`, the
  required frontmatter fields (`name`, `description`), and the
  not-hot-reloaded loading semantics for agents.
- [Claude Code settings — `settings.json`][cc-settings] —
  establishes the settings.json schema for hooks, permissions,
  and environment variables; basis for the structured-config
  routing rule.
- [Claude Code skills][cc-skills] — establishes the
  `skills/<name>/SKILL.md` layout and the main-session-context
  execution model that distinguishes skills from agents.

Empirical claims (verify against current release):

- Unconditional rules files (no `paths:` frontmatter) live-reload
  mid-session.
- Path-scoped rules files (with `paths:` frontmatter) are
  lazy-loaded on matching file read and lost on `/compact` until
  re-triggered.
- Skills files under `~/.claude/skills/<name>/SKILL.md` live-
  reload mid-session.
- `@import` is not supported in rules files (only in CLAUDE.md).

Assumptions (need verification):

- `~/.claude/.mcp.json` is loaded at session start; reload
  semantics unknown.

## Amendment history

- 2026-05-18 — initial publication. Split out of
  [ADR-0040](0040-render-pipeline.md) as the target-surface layer
  of the render pipeline design.
