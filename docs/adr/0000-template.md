# ADR-NNNN: Title

> Maury's ADR format is a **MADR/Nygard hybrid** with maury-specific
> sections preserved. See [`docs/process/adr-format.md`](../process/adr-format.md)
> for the full specification + rationale + how to migrate older
> ADRs to this layout. Quick sketch follows.

**Status:** Proposed | Accepted | Superseded by [ADR-NNNN](NNNN-slug.md) | Deprecated
**Date:** YYYY-MM-DD
<!-- Optional fields, in order, when applicable: -->
<!-- **Supersedes:** [ADR-NNNN](NNNN-slug.md) -->
<!-- **Superseded:** YYYY-MM-DD -->
<!-- **Amended:** -->
<!--   - YYYY-MM-DD — short note on what this amendment changed -->
<!--   - YYYY-MM-DD — short note on the next amendment -->

## Related tenets

- [Tenet N — Title](../tenets.md#n-slug) — one-line connection to this ADR.
<!-- Maury-specific. Cite every tenet this ADR embodies; tenets and ADRs
     have bidirectional cross-references. -->

## Context and Problem Statement

The situation, the constraints, the question we're answering. Free-form
prose; can be a question, a paragraph, or an illustrative story.
Two-to-five paragraphs typical.

## Decision Drivers

- The forces that shape the decision. One bullet each.
- E.g.: "Tenet 1 forbids silently losing user state."
- E.g.: "Implementation cost must be bounded."
- E.g.: "Cross-host concurrency must be safe by default."

## Considered Options

- **Option 1:** short title
- **Option 2:** short title
- **Option 3:** short title
<!-- Title each option distinctly. Detail goes in "Pros and Cons" below. -->

## Decision Outcome

**Chosen option:** "Option N", because {one-paragraph justification —
which decision driver(s) it satisfies, why it beats alternatives}.

### Implementation details

Where the bulk of the design specifics live. Subsections welcome
(`### Section name`). This is what was previously "Decision" — the
"how" of the chosen option.

### Consequences

- ✅ **Good:** {positive consequence — quality improved, capability gained}
- ❌ **Bad:** {negative consequence — cost paid, capability deferred, risk introduced}
- ⚖️ **Neutral:** {tradeoff that's neither clearly good nor bad}

### Confirmation

How we verify the ADR is followed in practice. Examples:
- "Doc-review agents check that X."
- "Tests in `tests/Y/test_Z.py` cover the contract."
- "`maury doctor` flags violations."
- "Manual verification via the smoke-test recipe in
  `docs/patterns/<name>.md`."

## Pros and Cons of the Options

### Option 1: {title}

{one-line description / context}

- ✅ **Good:** {argument for}
- ❌ **Bad:** {argument against}
- ⚖️ **Neutral:** {wash}

### Option 2: {title}

{one-line description / context}

- ✅ **Good:** {argument for}
- ❌ **Bad:** {argument against}

<!-- Add as many options as were genuinely considered. Don't pad. -->

## Build-order placement

<!-- Maury-specific. Where this lands in the implementation roadmap.
     Reference Phase numbers from docs/status.md. -->

- **Phase X:** what ships here.
- **Phase Y:** what's deferred.

## Followups

<!-- Maury-specific. Specific follow-up work this ADR identifies but
     doesn't itself spec. v1.1 / v2 deferrals go here. -->

- **Followup name:** description, target phase or version.

## Claude Code references

<!-- Maury-specific. If this ADR makes claims about Claude Code's
     documented behavior, cite the URLs here per the project rule in
     CLAUDE.local.md. Use reference-style link defs. -->

Verified-as-of YYYY-MM-DD against Anthropic's official Claude Code
documentation:

- [`cc-XXX`][cc-XXX] — what this ADR cites it for.

[cc-XXX]: https://code.claude.com/docs/en/...

## Amendment history

None.
