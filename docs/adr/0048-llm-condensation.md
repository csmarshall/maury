# ADR-0048: LLM condensation pass

**Status:** Accepted
**Date:** 2026-05-18
**Sub-ADR of:** [ADR-0040](0040-render-pipeline.md)

## Related tenets

- [Tenet 1 — First, do no harm](../tenets.md#1-first-do-no-harm)
- [Tenet 5 — The user arbitrates ambiguity](../tenets.md#5-the-user-arbitrates-ambiguity)
- [Tenet 7 — Provenance is mandatory](../tenets.md#7-provenance-is-mandatory)
- [Tenet 11 — Explicit beats implicit, with conservative defaults](../tenets.md#11-explicit-beats-implicit-with-conservative-defaults)

## Related ADRs

- [ADR-0012](0012-llm-backend.md) — LLM backend abstraction;
  powers the condensation pass
- [ADR-0038](0038-precept-acquisition-model.md) — precept-versus-
  convention distinction; precepts must be preserved verbatim
  through condensation
- [ADR-0040](0040-render-pipeline.md) — umbrella; this ADR
  carries the LLM-condensation layer of the render pipeline design
- [ADR-0041](0041-per-mode-anthropic-credentials.md) — per-mode
  Anthropic credentials; the condensation pass crosses the LLM
  trust boundary and must use the mode-scoped credential
- [ADR-0046](0046-source-file-naming.md) — sibling sub-ADR;
  source-file naming feeding the condensation input
- [ADR-0047](0047-render-target-surfaces.md) — sibling sub-ADR;
  CLAUDE.md and unconditional rules are the surfaces this pass
  operates on

## TL;DR

Naive concatenation of all matching layer content blows past
Claude Code's always-on context budget. An **LLM condensation
pass** is the default behavior of `maury sync`: it rephrases
verbose rules concisely and merges semantically-equivalent rules
across layers, targeting ~200 lines total across CLAUDE.md plus
unconditional rules files. **Precept-sourced content** (from
sublayers declared `repo_mode: pr` or `ro`, per ADR-0038) is
preserved verbatim through the pass — enforced **structurally**
via HTML provenance comments + prompt instructions + post-hoc
verification, not via user confirmation. `maury sync --raw`
skips the pass entirely for debug, audit, or air-gapped use.

## Context and Problem Statement

[ADR-0047](0047-render-target-surfaces.md) establishes that
maury writes to CLAUDE.md and unconditional rules files as the
always-on surfaces. The combined always-on content is read at
**every** session start and permanently consumes context budget.
Real-world layer accumulation (base + mode + several rules
repos) easily exceeds the practical budget.

Two adjacent problems:

1. **Layer content accumulates across modes and rules repos the
   user does not personally curate.** Even a disciplined user
   inherits content from subscribed rules repos. A condensation
   pass is the only way to recover budget from content the user
   did not write.
2. **Precept-sourced content must not be paraphrased.** Per
   [ADR-0038](0038-precept-acquisition-model.md), precepts are
   the user's explicit commitments to upstream conventions
   (`repo_mode: pr` or `ro` sublayers). Rephrasing a precept
   locally produces silent drift from the upstream wording. The
   condensation pass must distinguish precept content from
   convention content and treat them differently.

## Decision Drivers

- **Always-on context budget is finite and precious.** The pass
  exists because naive layer concatenation blows past the
  practical budget.
- **Precept preservation as a structural invariant, not a
  confirmation step.** Asking the user to confirm preservation
  on every sync produces alert fatigue; the invariant must hold
  without user attention.
- **Default behavior matches typical user need.** Most layer
  sets exceed budget; condensation should be the default, with
  `--raw` as the escape hatch.
- **Tenet 7 (provenance).** Every line in the rendered output is
  provenance-bearing; condensation must preserve enough metadata
  to attribute the merged result.
- **Tenet 1 (first, do no harm).** A render failure (e.g., LLM
  paraphrases a precept) must fail safely — fall back to raw
  output rather than silently emit corrupted content.

## Considered Options

### A — Condensation is opt-in (`maury sync --condense`)

Default sync emits raw layer concatenation; the user opts in to
LLM post-processing.

- ❌ Naive concatenation blows past the always-on context budget
  for any non-trivial layer set. Most users would need
  `--condense` every time; making it the default reduces friction
  and matches the actual desired behavior.

### B — Condensation is the default; `--raw` opts out (chosen)

`maury sync` runs the LLM pass. `maury sync --raw` emits the
unprocessed concatenation for inspection.

- ✅ Aligns the default with the typical user need.
- ✅ The escape hatch (`--raw`) remains for debug, audit, and
  air-gapped use.
- ⚖️ Precept preservation is enforced at the prompt level (see
  below), so the default is safe.

### C — No LLM pass; rely on authors to keep layers terse

- ❌ Layer content accumulates across modes and rules repos the
  user does not personally curate. The pass exists precisely to
  recover budget from content the user did not write.

### Precept preservation: confirmation-gated vs. structural

- **D1 — Confirmation gate.** Show the user the precept diffs;
  ask "preserve verbatim? (y/n)" on every sync.
- **D2 (chosen) — Structural enforcement.** HTML provenance
  markers around precept spans + prompt-level instructions to
  preserve + post-hoc verification. No user prompt.

D1 produces alert fatigue, and the user's confirmation does not
actually verify anything (they cannot diff the LLM output
against the precepts in their head). D2 is safer and lower-
friction.

## Decision Outcome

Chosen: **Option B + D2** — condensation is the default;
`--raw` opts out; precept preservation is structural.

### What the pass does

The condensation pass operates on the concatenated CLAUDE.md
plus unconditional rules files content. It:

- Identifies **redundant or overlapping rules** across layers
  (e.g., both `base` and `mode` say "use 2-space indentation" —
  keep one).
- **Rephrases verbose rules** more concisely while preserving
  meaning.
- **Merges semantically equivalent rules** declared in different
  layers, attributing the merged result to the deepest
  contributing layer for provenance.
- Targets the ~200-line total budget for CLAUDE.md plus
  unconditional rules files combined. This is a soft target;
  the pass does not refuse to emit longer output when the
  source genuinely requires it.

```sh
maury sync                  # render + LLM condense (default)
maury sync --raw            # render only; no LLM pass (debug/audit)
maury sync --dry-run        # show what would change; do not write
maury sync --raw --dry-run  # show what raw render would produce
```

### LLM optimization scope

The condensation pass operates **only** on CLAUDE.md and
unconditional rules files (no `paths:` frontmatter). Path-scoped
rules files are purpose-specific by definition; condensing them
is counterproductive (the author wrote 80 lines about React
testing for a reason; the LLM has no signal to know which lines
are redundant). Skills, agents, settings, and `.mcp.json` pass
through unchanged.

### Precept preservation — design, not confirmation

Precept-sourced content (content from sublayers declared with
`repo_mode: pr` or `ro`, per ADR-0038) **must not** be
paraphrased, merged, or summarized by the condensation pass. The
user has committed to the precept upstream; rephrasing it locally
produces silent drift from the upstream wording.

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
3. Convention-sourced content (from `repo_mode: rw` sublayers,
   plus base and mode universal content) is eligible for
   condensation.
4. After the LLM pass, the render engine **verifies** that every
   input precept span appears verbatim in the output. A
   precept-span mismatch is a render failure; the pipeline falls
   back to raw output and surfaces the LLM regression for review.

Confirmation gates were considered and rejected (see Option D1
above): requiring the user to approve precept preservation on
every sync produces alert fatigue, and the user's confirmation
does not actually verify anything (they cannot diff the LLM
output against the precepts in their head). Structural
enforcement at the prompt level, combined with post-hoc
verification, is safer and lower-friction.

### LLM backend

The condensation pass uses the LLM backend abstraction defined
in [ADR-0012](0012-llm-backend.md). The backend is pluggable;
users who do not want any LLM pass run `maury sync --raw`
exclusively.

Per [ADR-0041](0041-per-mode-anthropic-credentials.md), the
backend uses the **mode-scoped** Anthropic credential — the
condensation pass for `mode-work` content uses the work-mode
credential, not the personal-mode credential. The render
pipeline reads the active mode at sync time and supplies the
matching credential to the backend.

This is the same mode-isolation discipline that governs
**mining** (the other LLM-using subsystem, per
[ADR-0026](0026-profile-aware-mining.md)). Render and mining
are independent pipelines, but both cross the LLM trust boundary
identically: each operation is tagged with its active mode, and
the active-mode credential is the only credential that
participates in the LLM call.

## Consequences

- ✅ **Good:** Condensation is the default. Users get budget-
  aware output without remembering a flag; `--raw` is the audit
  escape hatch.
- ✅ **Good:** Precept preservation is structurally enforced —
  HTML comment markers plus prompt-level instructions plus
  post-hoc verification. No confirmation fatigue.
- ✅ **Good:** Path-scoped rules and other non-always-on
  surfaces are not condensed, matching their purpose-specific
  character.
- ⚖️ **Neutral:** The LLM condensation pass is a non-
  deterministic step. `--raw` provides the deterministic
  alternative for users who need reproducible output (e.g., for
  audit or compliance).
- ⚖️ **Neutral:** Per-mode credential routing crosses the LLM
  trust boundary in the way ADR-0041 established. The
  condensation pass is governed by the same mode-isolation
  discipline that governs mining (per
  [ADR-0026](0026-profile-aware-mining.md)) — both are LLM-
  using subsystems and both are mode-scoped at the credential
  layer.
- ❌ **Bad:** The condensation pass requires an LLM backend
  (per ADR-0012). Air-gapped or offline installations must use
  `--raw` exclusively, which means they bear the full always-on
  budget cost of unfiltered layer concatenation.
- ❌ **Bad:** Precept-span verification adds a post-LLM check
  that can reject otherwise-valid output if the model
  paraphrases despite the instructions. The fallback to raw
  output is safe, but the user sees a render-failure surface
  they would not have seen with a less strict invariant.

### Confirmation

- `maury sync` runs the LLM condensation pass by default;
  `maury sync --raw` skips it; `maury sync --dry-run` reports
  changes without writing. Tested by running both modes against
  a fixture agency and asserting output divergence (raw is
  longer, condensed is shorter) and content equivalence (no
  precept span is altered).
- Precept-span preservation is verified by the post-LLM check.
  Tested by injecting a deliberately-paraphrasable precept and
  asserting the pass either preserves it verbatim or falls back
  to raw output and surfaces a regression.
- Per-mode credential routing is verified by asserting that
  sync of `mode-work` content invokes the LLM backend with the
  work-mode credential, not the personal-mode credential.

## Build-order placement

When the render engine is implemented (per ADR-0040 umbrella):

- **LLM condensation pass.** Built on top of the LLM backend in
  ADR-0012. Requires the provenance-comment emission step from
  the render engine and the post-LLM verification step.

The condensation pass depends on the source-file scanner from
[ADR-0046](0046-source-file-naming.md) (to identify precept-
sourced vs. convention-sourced spans) and the target-surface
writers from [ADR-0047](0047-render-target-surfaces.md) (to know
which surfaces are eligible for condensation). It can be staged
behind `--condense` for early implementation and flipped to
default once the precept-preservation verifier is reliable.

## Followups

- **Condensation prompt tuning.** The LLM prompt that instructs
  precept preservation and condensation goals is a living
  artifact; versioning and regression testing of the prompt
  belongs in the LLM-backend work area.
- **Render-time provenance surface.** Per ADR-0037 §"Render-time
  provenance," provenance is tracked internally. A user-facing
  `maury show-provenance <field>` command is an open followup;
  precept-span markers are the first step toward exposing it.
- **Condensation-output cache.** A future amendment may add a
  content-addressed cache keyed by `(input concatenation hash,
  prompt version)` so repeated syncs do not invoke the LLM when
  nothing has changed. Out of scope for v1.
- **Streaming-condensation for very large agencies.** If a
  user's combined always-on content exceeds the LLM's input
  budget, the pass needs a chunking strategy that preserves
  cross-chunk redundancy detection. Out of scope for v1; surfaces
  via dry-run warnings until then.

## Claude Code references

This sub-ADR introduces no new Claude Code dependencies. The
condensation pass operates on content destined for the surfaces
described in [ADR-0047](0047-render-target-surfaces.md); see
that ADR's Claude Code references section.

## Amendment history

- 2026-05-18 — initial publication. Split out of
  [ADR-0040](0040-render-pipeline.md) as the LLM-condensation
  layer of the render pipeline design.
