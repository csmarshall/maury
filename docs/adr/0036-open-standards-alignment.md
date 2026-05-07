# ADR-0036: Cross-vendor open-standards alignment

**Status:** Accepted
**Date:** 2026-05-07

## Related tenets

- [Tenet 9 — Defer to the platform](../tenets.md#9-defer-to-the-platform)
- [Tenet 10 — Modularity over hardcoding](../tenets.md#10-modularity-over-hardcoding)

## Context and Problem Statement

Maury was designed primarily against [Claude Code's][cc-overview]
documented behavior (per
[`claude-code-contract.md`](../claude-code-contract.md)). But the
agent-tooling ecosystem has grown wider: Anthropic, OpenAI, Block,
and 170+ Linux Foundation members co-steward a small but growing
set of cross-vendor open standards under the **Agentic AI
Foundation** (founded December 2025). Adoption is broad — the
same `SKILL.md` spec is read by Claude Code, Cursor, GitHub
Copilot, VS Code, Gemini CLI, OpenAI Codex, and others.

The project owner asked: should maury commit to comply with these
standards rather than treating Claude Code's conventions as the
only target? A 2026-05-07 standards survey (run by the
claude-code-guide subagent) returned concrete findings; this ADR
locks in the decisions.

## Decision Drivers

- **Portability across agent vendors.** Skills maury ships should
  work for users who later switch from Claude Code to Cursor,
  Codex, etc., without maury-side changes.
- **Avoid premature abstraction.** Don't take on standards work
  that has no current maury use case.
- **Avoid painting into a corner.** Don't make design choices
  that lock out future cross-vendor adoption.
- **Bounded implementation cost.** Standards compliance must be
  a small lift relative to the portability gain.
- **Consistency with maury's existing trust-boundary architecture**
  — already vendor-neutral; the agent surface should match.

## Considered Options

- **Option A:** Anthropic-only alignment (Claude Code as sole target).
- **Option B:** Commit to all four standards (Agent Skills, AGENTS.md, MCP, future AAF specs) immediately.
- **Option C:** Build a maury-specific skills format.
- **Option D:** Build a maury-specific cross-vendor hooks abstraction.
- **Option E (chosen):** Tiered alignment — commit to Agent Skills now (trivially aligned, real portability win), track AGENTS.md and MCP for future use cases, deliberately stay out of scope for non-existent standards.

## Decision Outcome

**Chosen option:** Option E — tiered alignment. Commit to Agent
Skills now (already aligned at the layout level; only YAML
frontmatter discipline is new). Track AGENTS.md and MCP for
future triggers (public profiles for AGENTS.md; multi-vendor
agent fleet for MCP). Out-of-scope on cross-vendor standards
that don't exist yet (manifest formats, hook standards,
state-dir standards). This satisfies the portability driver
without taking on unbounded standards work.

### Implementation details

Maury makes three commitments and explicitly defers two.

### 1. ✅ Commit: Agent Skills (`agentskills.io`)

[Agent Skills][agentskills-spec] is the cross-vendor spec for
`SKILL.md` files — a directory with a `SKILL.md` containing YAML
frontmatter (`name`, `description`, optional `license`,
`compatibility`, etc.) plus markdown instructions. **Progressive
disclosure**: agents load name/description at startup, full
content when the skill is activated, supporting files on demand.

**Maury's status: layout aligned, frontmatter formalization is
the new commitment.** The `maury-stage` skill shipped via
[ADR-0013](0013-active-in-session-capture.md) and the
`maury-status` skill defined in
[ADR-0017](0017-drift-detection-and-reconciliation.md) both
target the `~/.claude/skills/<name>/SKILL.md` *layout* that
Agent Skills specifies. Neither ADR currently spells out the
YAML frontmatter — they describe the prompt content "in spirit."
This ADR is what locks in that frontmatter discipline; the
actual frontmatter ships when each skill's implementation slice
lands.

**What we commit to:**

- Every skill maury ships (`maury-stage`, `maury-status`,
  planned `maury-pin`) carries Agent-Skills-conformant
  frontmatter:
  ```yaml
  ---
  name: maury-status
  description: Surface maury's view of host state (active profile, drift, pending captures) mid-session.
  license: MIT
  compatibility:
    - claude-code: ">=2.1.0"
    - any-agent-skills-1.0  # maury-internal cross-vendor marker (see note below)
  ---
  ```
  *Note: `any-agent-skills-1.0` is a **maury-internal**
  convention; the Agent Skills spec at
  [`agentskills.io/specification`][agentskills-spec] does not
  define a stock cross-vendor compatibility marker as of
  2026-05-07. We use this string in maury-shipped skills to
  signal "this skill targets the Agent Skills spec generically,
  not a single vendor"; it has no semantic effect on skill
  loaders that don't recognize it. If/when the spec adopts an
  official marker, we'll switch to that.*
- `name` follows the spec constraints (lowercase alphanumeric
  + hyphens, max 64 chars, no leading/trailing hyphens).
- `description` follows spec constraints (max 1024 chars,
  describes what + when).
- Doc-review agents check this on every skill commit.

**Why this matters:** the same skills become invokable by any
agent that reads Agent Skills, not just Claude Code. A user
who later tries Cursor or Codex with maury-managed config gets
the skills working without maury-side changes.

### 2. ⏳ Track, defer: AGENTS.md

[AGENTS.md][agents-md] is a Markdown file at a project root
giving AI coding agents context (build steps, test commands,
conventions, security flows). 60K+ projects use it; natively
read by Codex CLI, Copilot, Cursor, Windsurf, Aider, VS Code,
others.

**Maury's status: not currently applicable, may apply later.**
Maury isn't itself a project agents code in (or rather: maury's
own source tree could have an AGENTS.md, and arguably should,
but that's separate from maury *as a tool*). The interesting
question is: when users publish maury profiles to git repos,
should maury render an AGENTS.md alongside CLAUDE.md so the
profile is consumable by non-Claude-Code agents?

**What we defer (revisit when):**

- If profiles go public (per [ADR-0034](0034-published-subscribed-profiles.md)
  team-published model), shipping an `AGENTS.md` as a render
  output type alongside CLAUDE.md becomes high-value. Until
  there's user demand for non-Claude-Code rendering, this
  stays deferred.
- Maury's own source tree's `AGENTS.md` (for contributors)
  is a separate small task — would help future agent-mediated
  contribution to the maury repo. Trivial doc work; lands when
  a contributor needs it.

### 3. ⏳ Track, out-of-scope: Model Context Protocol (MCP)

[MCP][mcp-spec] is the cross-vendor protocol for AI agents to
communicate with external tools, services, and data sources.
10K+ servers; supported by Claude, Copilot, Gemini, ChatGPT,
Bedrock, Databricks. Stewarded by the Agentic AI Foundation
(handed off from Anthropic in late 2025).

**Maury's status: explicitly considered + rejected for v1, per
[ADR-0013](0013-active-in-session-capture.md):** *"A new MCP
server hosted by maury. Considered. Skill + hook is simpler and
arrives via the same render engine the rest of maury uses. MCP
would be over-engineered for this."*

That decision still holds for the **capture pipeline use case**.
But MCP could legitimately apply to a different, future maury
use case:

- **Maury-as-MCP-server.** If maury exposed its host-state
  (active profile, drift count, pending captures, sync status)
  as an MCP server, *any* MCP-aware agent (not just Claude
  Code) could query it. Useful if users adopt maury for
  multi-vendor agent fleets.
- **Maury-as-MCP-client.** If maury consumed external data via
  MCP (e.g., a team's Linear backlog as input to mining), it
  could integrate without bespoke per-service code.

**What we commit to:** keep maury MCP-compatible. Don't make
design choices that would lock out future MCP adoption (e.g.,
don't bake assumptions about Claude-Code-only invocation
patterns into core modules). When/if MCP adoption emerges as a
user need, it's an additive feature, not a rewrite.

### 4. ✅ Align (philosophical): Agentic AI Foundation

[The Agentic AI Foundation][aaf-press] (Linux Foundation,
founded December 2025) stewards Agent Skills, AGENTS.md, and
MCP under one umbrella. Founding members: Anthropic, OpenAI,
Block. Maury's design philosophy — decentralized, multi-vendor-
compatible configuration, host-local enforcement, no
proprietary lock-in beyond the documented Claude Code
dependencies — is naturally aligned with AAF principles.

**What we commit to:** monitor AAF for breaking changes to the
specs we depend on (Agent Skills SKILL.md format primarily).
When changes happen, treat them like Claude Code documentation
changes per
[`claude-code-contract.md`](../claude-code-contract.md)'s
drift-mitigation pattern — capture them in a snapshot, audit
maury for compliance, ship updates.

### 5. ❌ Out of scope

**Standards we deliberately don't engage with:**

- **Cross-vendor agent manifest formats.** No universal schema
  exists; OpenHands, Letta, Factory.ai etc. each have their
  own JSON/YAML. Maury's own manifest schema (per
  [ADR-0015](0015-surrogate-keys-for-hosts-and-profiles.md))
  is a different concern — it's about per-user host/profile
  registry, not about exposing agent capabilities.
- **Cross-vendor hook standards.** None exist. Each tool
  (Claude Code, GitHub Actions, Cursor, etc.) has its own
  hook system. MCP partially fills this gap for tools/resources
  but not for lifecycle events.
- **Cross-vendor agent state-directory standards.** None
  exist. Maury's `~/.claude/maury-state/` per
  [ADR-0029](0029-maury-state-layout-contract.md) is correctly
  scoped to Claude Code's `~/.claude/` hierarchy.

### Consequences

- ✅ **Good:** Skills published by maury are portable to any
  Agent-Skills-aware agent. Users who switch from Claude Code
  to Cursor (or use both) get the maury-managed skills working
  without maury-side changes.
- ✅ **Good:** One small documentation lift now
  (Agent-Skills-conformant frontmatter on shipped skills)
  prevents larger refactor later.
- ⚖️ **Neutral:** AGENTS.md and MCP commitments are "don't
  paint into a corner." No new code today; just keep design
  choices that preserve future compatibility.
- ❌ **Bad:** Doc-review agents add one more check (SKILL.md
  frontmatter conforms to the Agent Skills spec); CLAUDE.local.md
  checklist grows.
- ⚖️ **Neutral:** The Agentic AI Foundation becomes a third
  upstream-watch point alongside Claude Code documentation
  ([`claude-code-contract.md`](../claude-code-contract.md))
  and the Anthropic best-practices rubric (per
  [ADR-0011](0011-anthropic-rubric-integration.md)).

### Confirmation

- Doc-review agents check SKILL.md frontmatter on every commit
  that adds or modifies a skill (rule captured in CLAUDE.local.md).
- A future periodic refresh of `docs/agentskills-snapshots/`
  (per Followups below) will detect spec drift, mirroring the
  `docs/claude-code-snapshots/` pattern from
  [`claude-code-contract.md`](../claude-code-contract.md).
- `maury doctor` could optionally validate frontmatter on
  installed skills (followup; not v1).

## Pros and Cons of the Options

### Option A: Anthropic-only alignment

Treat Claude Code as the only target; ignore cross-vendor specs
even when they overlap with what maury already does.

- ❌ **Bad:** Misses portability win that's already-aligned-by-design.
- ❌ **Bad:** The Agent Skills spec is already Anthropic-co-authored AND adopted by 40+ vendors; ignoring it is leaving free portability on the table.
- ❌ **Bad:** Inconsistent with maury's vendor-neutral trust-boundary architecture.

### Option B: Commit to all four standards immediately

Implement Agent Skills + AGENTS.md output + MCP server-mode + monitor AAF, all now.

- ✅ **Good:** Maximally portable from day one.
- ❌ **Bad:** AGENTS.md and MCP have no current maury use case; preemptive implementation is over-engineering.
- ❌ **Bad:** Adds three new surface areas to maintain (AGENTS.md render output, MCP server, AAF spec snapshots) when only one (Agent Skills) is currently exercised.

### Option C: Maury-specific skills format

Define our own skills schema, ignoring Agent Skills.

- ❌ **Bad:** Zero benefit; reinvents what's already standardized.
- ❌ **Bad:** Breaks Agent Skills compliance and isolates maury from the broader ecosystem.
- ❌ **Bad:** Increases the maintenance surface (we'd have to keep our schema, document it, and explain why we deviate).

### Option D: Maury-specific cross-vendor hooks abstraction

Build an abstraction layer over Claude Code hooks + hypothetical future cross-vendor hooks.

- ⚖️ **Neutral:** Conceptually attractive (insulates maury from any one vendor's hook system).
- ❌ **Bad:** No cross-vendor hooks standard exists yet; building the abstraction is speculative.
- ❌ **Bad:** YAGNI risk — if a standard emerges with a different shape, our abstraction becomes wasted work.

### Option E (chosen): Tiered alignment

Commit Agent Skills now (already aligned), track AGENTS.md + MCP for future use cases, deliberately stay out of scope for non-existent standards.

- ✅ **Good:** Trivially aligned with the one standard maury actually uses (Agent Skills), with low documentation lift.
- ✅ **Good:** Preserves future compatibility with AGENTS.md + MCP without preemptive implementation.
- ✅ **Good:** Matches the project's general posture (defer to platforms; don't build abstractions before they're needed).
- ⚖️ **Neutral:** Requires one new doc-review rule (frontmatter check) and one followup (snapshots dir).
- ❌ **Bad:** When AGENTS.md or MCP eventually do apply, we'll have new design work to do — that work just isn't starting today.

## Build-order placement

- **Skills frontmatter retrofit** (Agent-Skills-conformant YAML
  on shipped skills) lands as a tiny followup wherever the first
  user-visible skill ships:
  - **Phase 5.x.a** (where `maury-status` ships per
    [ADR-0017](0017-drift-detection-and-reconciliation.md))
    is the earliest practical landing point.
  - **Phase 5.5 / Phase 7** (where the active-capture pipeline
    and `maury-pin` ship per
    [ADR-0013](0013-active-in-session-capture.md)) is the
    backstop.
  Either way, no skill ships without the frontmatter.
- **Doc-review agent prompt update** to include the SKILL.md
  frontmatter check is a CLAUDE.local.md edit; lands now.
- **AAF / Agent Skills spec snapshot** added to a new sibling
  `docs/agentskills-snapshots/` directory following the same
  drift-detection pattern as `docs/claude-code-snapshots/`.
  Lands when the first maury-shipped skill ships with conformant
  frontmatter (the snapshot pattern only earns its keep once a
  shipped skill depends on the spec).
- **AGENTS.md + MCP work** lands when its respective trigger
  fires (public profiles for AGENTS.md, multi-vendor agent
  fleet for MCP). Not on a roadmap.

## Followups

- **Source-tree AGENTS.md.** A small `AGENTS.md` at the maury
  project root would help future agent-mediated contributions
  to maury itself. Trivial; lands when the first contributor
  needs it.
- **Agent Skills snapshot directory.** Mirror the
  `docs/claude-code-snapshots/` pattern for the Agent Skills
  spec, so spec-drift is detected the same way. Worth doing
  when we register the first skill in the public registry.
- **Public profile registry** (gap-L per
  [ADR-0034](0034-published-subscribed-profiles.md)) — when
  this lands, AGENTS.md as a render output becomes part of
  the design. Track here so the connection isn't lost.

## Standards references

| Standard | URL |
|---|---|
| Agent Skills specification | https://agentskills.io/specification |
| Agent Skills client showcase | https://agentskills.io/clients |
| AGENTS.md specification | https://agents.md/ |
| Model Context Protocol specification | https://modelcontextprotocol.io/specification/2025-11-25 |
| Agentic AI Foundation announcement | https://www.linuxfoundation.org/press/agentic-ai-foundation |
| Anthropic MCP announcement | https://www.anthropic.com/news/model-context-protocol |

[cc-overview]: https://code.claude.com/docs/en/overview
[agentskills-spec]: https://agentskills.io/specification
[agents-md]: https://agents.md/
[mcp-spec]: https://modelcontextprotocol.io/specification/2025-11-25
[aaf-press]: https://www.linuxfoundation.org/press/agentic-ai-foundation
