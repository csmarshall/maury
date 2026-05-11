# ADR-0011: Integrate Anthropic best-practices rubric as a content-quality evaluator

**Status:** Accepted
**Date:** 2026-05-06

## Related tenets

- [Tenet 9 — Defer to the platform](../tenets.md#9-defer-to-the-platform)

## TL;DR

Anthropic ships no API for evaluating CLAUDE.md content quality, but
its best-practices doc encodes a clear ✅/❌ rubric and named
anti-patterns (over-specification, self-evident platitudes, file-by-
file descriptions, etc.). Maury adds a **`maury doctor` subcommand**
with deterministic pattern-based checks seeded from that rubric, with
each check citing its source URL. Mining proposals also run through
the doctor's anti-pattern detectors so violations surface before the
user sees them. Trade-off: maintenance coupling — when Anthropic
updates the rubric, we update the pattern list (mitigated by recording
source URLs).

## Context and Problem Statement

Anthropic ships no public API or CLI for evaluating CLAUDE.md / settings
content quality. The closest thing is `/doctor` (slash command) which is
a plumbing health-check (API connectivity, MCP, hook syntax, permission
shadowing) — it does not evaluate whether your CLAUDE.md content is
*good*.

The closest thing to an authoritative content rubric is the prose
document at `code.claude.com/docs/en/best-practices`, which contains:

- An explicit ✅/❌ table for what belongs in CLAUDE.md.
- Five named failure patterns including "the over-specified CLAUDE.md."
- Concrete anti-patterns ("self-evident practices like 'write clean
  code'", "file-by-file descriptions of the codebase", "detailed API
  documentation").
- Guidance on when to use skills vs CLAUDE.md vs hooks vs subagents.

Third-party tools partially fill the gap:

- **`cclint`** (Node, MIT, ~17★, active) — structural validator with
  JSON output; checks frontmatter / schema / naming.
- **`claude-config-doctor`** (skill v1.1.3, Apr 2026) — semantic
  cross-file conflict detection, no machine output.
- **`jeanclaudecode.ai`** — black box, no API.

Building maury without acknowledging this rubric would mean we'd
re-derive the same advice from scratch via the rule engine, with poor
provenance. The rubric exists; we should consume it.

<details>
<summary><b>Decision drivers</b> (4 items — click to expand)</summary>

- **Tenet 9:** defer to the platform. Anthropic's documented
  rubric is *the* authoritative source for "what belongs in
  CLAUDE.md"; reinventing it via the rule engine would have
  worse provenance.
- **Mining alignment:** mining produces CLAUDE.md proposals
  from day one. Without a quality check against the rubric,
  maury could happily propose content the docs say not to
  write.
- **Auditable provenance:** the doctor's checks should cite
  the source rule so users can click through to Anthropic's
  prose for context.
- **No new dependencies:** evaluator should ship as a small
  in-tree module, not a new external service.

</details>

<details>
<summary><b>Considered options</b> (5 options — click to expand)</summary>

- **Option A:** Defer the evaluator to v2.
- **Option B:** Shell out to `cclint` for structural validation.
- **Option C:** Reuse the existing rule engine for quality
  checks.
- **Option D:** AI-only critique — ask the LLM "is this rule
  good?"
- **Option E (chosen):** Add a `maury doctor` subcommand with
  pattern-based checks seeded from Anthropic's best-practices
  doc.

</details>

## Decision Outcome

**Chosen option:** Option E — add a `maury doctor` subcommand
and seed an evaluator with rules derived directly from
Anthropic's best-practices doc. Pattern-based checks are
deterministic, fast, and cite the source rule — the only
option that satisfies all four decision drivers.

### Implementation details

v1 scope:

1. **`maury doctor` command** that reads a CLAUDE.md file (default
   `~/.claude/CLAUDE.md` per [Claude Code memory documentation][cc-memory])
   and emits a report of best-practice violations based on the
   Anthropic rubric. Text and JSON output modes.
2. **Hard-coded checks for v1** corresponding directly to the named
   anti-patterns in the doc:
   - **Length / over-specification.** Flag when CLAUDE.md exceeds a
     conservative threshold (lines / chars) — references the
     "over-specified CLAUDE.md" failure pattern.
   - **Self-evident platitudes.** Match phrases like "write clean
     code," "follow best practices," "good code," "be careful," "use
     proper naming."
   - **Tutorial-style content.** Match opening phrases like "this
     project is," "the codebase consists of," long prose paragraphs.
   - **Standard-convention restatement.** Match rules that restate
     what Claude already knows (camelCase, PEP 8, "use semicolons",
     etc.).
   - **File-by-file descriptions.** Detect bullet lists describing
     individual files.
   - **Inline API docs.** Match patterns suggesting embedded API
     reference rather than a link.
3. **Mining proposal alignment.** When mining proposes a fragment for
   addition to CLAUDE.md, the doctor's anti-pattern checks run against
   the proposal text. Hits trigger a `flag-anti-pattern` warning in
   the proposal review UI, surfacing the rubric violation alongside
   the classification trace.
4. **Explicitly NOT in v1 scope:** AI-assisted semantic critique
   ("does this rule have non-obvious value?"), shell-out to `cclint`
   for structural validation, JSON-schema validation of settings.json
   beyond what the upstream `claude` binary already does. These are
   v2.

### Consequences

- ✅ **Good:** Maury becomes the only content-quality CLAUDE.md
  evaluator in the ecosystem, in addition to its sync +
  isolation + mining role.
- ✅ **Good:** Mining gains an extra signal — proposals that
  match anti-patterns are flagged before the user sees them,
  reducing the chance of approving content that violates
  Anthropic's own guidance.
- ✅ **Good:** Provenance is auditable — every flagged check
  cites the source rule in Anthropic's best-practices doc.
- ⚖️ **Neutral:** Anti-pattern checks are a small, well-bounded
  code module (`src/maury/doctor/`) that uses regex patterns +
  word counting; no AI dependency.
- ⚖️ **Neutral:** Adds a new top-level CLI command and a new
  module, but no new external dependencies.
- ❌ **Bad:** Maintenance coupling to upstream — when
  Anthropic updates the rubric, we update the doctor's pattern
  list. Mitigated by recording the source URL.

### Confirmation

- `src/maury/doctor/` exists and `maury doctor` is shipped per
  Phase 2.5 in `docs/status.md`.
- Each check carries a citation back to the Anthropic source
  rule it derives from (visible in both text and JSON
  output).

<details>
<summary><b>Pros and cons of the options</b> (per-option ✅/❌ — click to expand)</summary>

#### Option A: Defer the evaluator to v2

- ✅ **Good:** Smaller v1 scope.
- ❌ **Bad:** Mining produces CLAUDE.md proposals from day
  one; shipping a tool that proposes content without checking
  against the documented best practices feels backwards.
- ❌ **Bad:** Cost of folding in is small — deferring
  trades a small effort for a real gap in v1.

#### Option B: Shell out to `cclint`

- ✅ **Good:** Reuses an existing tool.
- ❌ **Bad:** `cclint` is structural, not semantic — doesn't
  evaluate the ✅/❌ rubric.
- ⚖️ **Neutral:** Reasonable to add as a v2 pre-pass (cheap
  subprocess, JSON output) for the structural-validation slice.

#### Option C: Reuse the existing rule engine

- ✅ **Good:** One engine, one mental model.
- ❌ **Bad:** The classification rule engine routes fragments
  to profiles; doctor evaluates content quality, which is a
  different operation.
- ❌ **Bad:** Reusing the engine would require new rule
  semantics (a `quality` shape that doesn't classify but
  flags). Cleaner for v1 to keep them separate; revisit
  unification in v2 if patterns converge.

#### Option D: AI-only critique

- ✅ **Good:** Could catch nuanced quality issues regex
  patterns miss.
- ❌ **Bad:** Pattern-based checks are deterministic, fast,
  and cite the source rule. AI-only would be opaque, which
  contradicts maury's "rules are the learned artifact" ethos
  ([ADR-0004](0004-rule-engine-classification.md)).
- ❌ **Bad:** Adds an LLM round-trip on every doctor invocation.

#### Option E (chosen): Pattern-based doctor seeded from Anthropic's docs

- ✅ **Good:** Deterministic, auditable, cites sources.
- ✅ **Good:** No new dependencies.
- ✅ **Good:** Aligns with maury's rule-engine ethos.
- ❌ **Bad:** Pattern-based checks may miss nuances; v2 can
  layer AI critique as an opt-in pre-pass.

</details>

## Build-order placement

Phase 2.5 — `maury doctor` lands after the rule engine
(Phase 1) and manifest/capability probe (Phase 2). Already
shipped per `docs/status.md`.

## Followups

- **AI-assisted semantic critique** (Option D, opt-in) for
  nuance the regex checks miss. v2.
- **Shell out to `cclint`** as an optional pre-pass for
  structural validation. v2.
- **Doctor-rubric drift detection** — when Anthropic updates
  best-practices, we should know. The
  `docs/claude-code-snapshots/` mechanism already exists for
  CC behavior; extend or mirror for the rubric URL.

## Claude Code references

Verified-as-of 2026-05-07 against Anthropic's official Claude
Code documentation:

- [`cc-best-practices`][cc-best-practices] — the ✅/❌ table
  and the five named failure patterns. The doctor's check
  list derives directly from this page.
- [`cc-memory`][cc-memory] — the documented location of
  CLAUDE.md (`~/.claude/CLAUDE.md` for user-global memory).
  `maury doctor` defaults to this path.

[cc-best-practices]: https://code.claude.com/docs/en/best-practices
[cc-memory]: https://code.claude.com/docs/en/memory

## Amendment history

None.
