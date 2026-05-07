# maury — ADR format

Maury's Architecture Decision Records use a **MADR/Nygard hybrid**
with maury-specific extensions preserved.

## Why a hybrid

- **MADR ([Markdown Architectural Decision Records 4.x][madr])** is
  the de-facto community standard for ADRs. Its structure (Decision
  Drivers → Considered Options → Decision Outcome → Pros and Cons of
  the Options) makes design tradeoffs explicit and scannable for
  reviewers who don't know the project's history.
- **Nygard's original ADR format (2011)** is more conversational,
  prose-heavy, and lower-overhead. Easy to read; easy to write
  during active design discussion.
- **Maury's custom sections** (Related tenets, Build-order placement,
  Followups, Claude Code references, the `**Amended:**` field) carry
  meaningful project-specific information that neither MADR nor
  Nygard has slots for.

The hybrid keeps MADR's structural rigor where it adds clarity
(Considered Options surfaced upfront, Pros/Cons per option,
Decision Outcome with explicit chosen-option callout) and Nygard's
prose tone where verbosity helps (Context as a story, Implementation
details as flowing prose with subsections).

## Required sections (in this order)

1. **Title** — `# ADR-NNNN: Title`
2. **Status / Date / (optional Amended / Supersedes / Superseded)**
3. **Related tenets** — at least one. Bidirectional with `tenets.md`.
4. **Context and Problem Statement** — free-form prose.
5. **Decision Drivers** — bullet list of the forces shaping the
   decision.
6. **Considered Options** — bullet list of options with short titles.
7. **Decision Outcome** — explicit "Chosen option: X, because Y."
   - **Implementation details** subsection.
   - **Consequences** subsection (Good / Bad / Neutral bullets).
   - **Confirmation** subsection (how we verify).
8. **Pros and Cons of the Options** — per-option breakdown.
9. **Build-order placement** — maury-specific.
10. **Followups** — maury-specific.
11. **Claude Code references** — maury-specific, when the ADR
    makes claims about Claude Code's documented behavior.

## Differences from upstream MADR 4.x

| MADR 4.x | Maury |
|---|---|
| `## Context and Problem Statement` | Same |
| `## Decision Drivers` | Same |
| `## Considered Options` | Same |
| `## Decision Outcome` with `### Consequences` and `### Confirmation` | Same, plus **`### Implementation details`** as a third subsection (maury's design specifics often need a dedicated section, and pure-MADR doesn't have a slot for it) |
| `## Pros and Cons of the Options` | Same |
| `## More Information` (optional) | Replaced by maury's three custom sections (Build-order, Followups, Claude Code references) which serve the same role with project-specific structure |
| (no equivalent) | **`## Related tenets`** — maury cross-references its 11 tenets explicitly |
| (no equivalent) | **`**Amended:**`** field — mini-changelog convention for in-place amendments |

## Differences from Nygard's original

| Nygard | Maury |
|---|---|
| `## Context` | Renamed to `## Context and Problem Statement` per MADR 4.x |
| `## Decision` | Replaced by `## Decision Outcome` with subsections per MADR 4.x |
| `## Consequences` | Now a subsection of Decision Outcome with Good/Bad/Neutral structure |
| `## Alternatives considered` (optional, often one-liners) | Replaced by `## Considered Options` (upfront titles) + `## Pros and Cons of the Options` (per-option detail) |

## Migrating older ADRs

ADRs 0000-0036 were written before this format was formalized
and use a Nygard-derivative structure. Migration is being done in
batches; if you're touching an old ADR for any reason, take the
opportunity to migrate it to this format.

Per-ADR migration recipe:

1. Add `## Decision Drivers` between Context and the existing
   "Decision" section. Extract the drivers from the prose (they're
   usually already there, just unstructured).
2. Add `## Considered Options` listing the alternatives by short
   title. The detail moves to "Pros and Cons" below.
3. Rename the existing `## Decision` section to `## Decision Outcome`,
   add the explicit "Chosen option: X, because Y" framing at the
   top, demote the existing detail to `### Implementation details`,
   and split `## Consequences` into a `### Consequences` subsection.
4. Add `### Confirmation` subsection — how we verify the ADR is
   followed (often: "doc-review agent checks for X" or "tests
   cover the contract").
5. Replace the old `## Alternatives considered` with `## Pros and
   Cons of the Options` — per-option pros/cons rather than just
   rejected one-liners. Keep the same options.
6. Maury-specific sections (Related tenets, Build-order placement,
   Followups, Claude Code references, Amended) are unchanged.

A reasonable migration commit batches 5-10 ADRs at a time so the
diff stays scannable.

## What this format is NOT

- It's not a strict superset of upstream MADR — the
  `### Implementation details` subsection is a maury extension
  inside Decision Outcome that MADR 4.x doesn't define. Tools
  that strictly validate against MADR 4.x will see this as an
  extra section; that's intentional.
- It's not exhaustively form-driven. The prose-heavy tone is
  intentional; over-bulleting design discussions makes them harder
  to read, not easier.
- It's not the only valid record. Patterns docs
  (`docs/patterns/`), the Claude Code contract
  (`docs/claude-code-contract.md`), and concepts
  (`docs/concepts.md`) are different document types with their
  own conventions.

## Tools and references

- [MADR 4.x specification][madr] — the upstream standard.
- [Michael Nygard's original ADR post (2011)][nygard] — the
  conversational ancestor.
- [`docs/adr/0000-template.md`](../adr/0000-template.md) — the
  template every new ADR copies from.
- [`CLAUDE.local.md`](../../CLAUDE.local.md) (gitignored) — project
  rules including the doc-review checklist that enforces this
  format.

[madr]: https://adr.github.io/madr/
[nygard]: https://www.cognitect.com/blog/2011/11/15/documenting-architecture-decisions
