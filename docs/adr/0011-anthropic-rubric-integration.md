# ADR-0011: Integrate Anthropic best-practices rubric as a content-quality evaluator

**Status:** Accepted
**Date:** 2026-05-06

## Related tenets

- [Tenet 9 — Defer to the platform](../tenets.md#9-defer-to-the-platform)

## Context

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

## Decision

Add a `maury doctor` subcommand and seed an evaluator with rules
derived directly from Anthropic's best-practices doc. v1 scope:

1. **`maury doctor` command** that reads a CLAUDE.md file (default
   `~/.claude/CLAUDE.md`) and emits a report of best-practice violations
   based on the Anthropic rubric. Text and JSON output modes.
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

## Consequences

- maury becomes the only content-quality CLAUDE.md evaluator in the
  ecosystem, in addition to its sync + isolation + mining role.
- Anti-pattern checks are a small, well-bounded code module
  (`src/maury/doctor/`) that uses regex patterns + word counting; no
  AI dependency.
- The rubric is the authoritative source. When Anthropic updates the
  doc, we update the doctor's pattern list. README + ADR record the
  source URL so the provenance is auditable.
- Mining gains an extra signal: proposals that match anti-patterns
  are flagged before the user sees them, reducing the chance of
  approving content that violates Anthropic's own guidance.
- Adds a new top-level CLI command and a new module, but no new
  external dependencies.

## Alternatives considered

- **Defer to v2.** Rejected: mining produces CLAUDE.md proposals from
  day one, and shipping a tool that proposes content without checking
  against the documented best practices feels backwards. Cost of folding
  in is small.
- **Shell out to cclint.** Rejected for v1: cclint is structural, not
  semantic; doesn't evaluate the ✅/❌ rubric. Reasonable to add as a
  v2 pre-pass (cheap subprocess, JSON output).
- **Use the existing rule engine for quality checks.** Considered.
  The classification rule engine routes fragments to profiles; doctor
  evaluates content quality, which is a different operation. Reusing
  the engine would require new rule semantics (a `quality` shape that
  doesn't classify but flags). Cleaner for v1 to keep them separate;
  revisit unification in v2 if patterns converge.
- **AI-only critique.** Rejected: pattern-based checks are
  deterministic, fast, and cite the source rule. AI-only would be
  opaque, which contradicts maury's "rules are the learned artifact"
  ethos (ADR-0004).

## Source

- Anthropic best practices: <https://code.claude.com/docs/en/best-practices>
  (✅/❌ table, the five named failure patterns)
- Anthropic memory docs: <https://code.claude.com/docs/en/memory>
- "How Anthropic teams use Claude Code" PDF
