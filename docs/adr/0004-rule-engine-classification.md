# ADR-0004: Smart-but-strict classification — rules are the learned artifact

**Status:** Accepted
**Date:** 2026-05-06

## Related tenets

- [Tenet 5 — The user arbitrates ambiguity](../tenets.md#5-the-user-arbitrates-ambiguity)
- [Tenet 7 — Provenance is mandatory](../tenets.md#7-provenance-is-mandatory)

## TL;DR

A pure-LLM classifier is non-deterministic and opaque — wrong shape
for routing fragments across trust boundaries; a pure hand-written
ruleset is auditable but never learns. Maury splits the pipeline:
**LLM-driven mining + deterministic rule engine** over a hand-readable
YAML file. The ruleset *is* the learned artifact: corrections trigger
AI-assisted rule synthesis, but every synthesized rule needs explicit
user approval before landing. Trade-off: two-stage pipeline is more
code than a single LLM call, and reviewers have to learn rule grammar.

## Context and Problem Statement

Mining transcripts for preferences is genuinely fuzzy work — well-suited
to an LLM. But **classification** (which profile a fragment belongs to)
crosses the trust boundary, and "trust the LLM with security routing" is
a non-starter.

Whatever we choose must be:

- Deterministic and repeatable.
- Auditable — there must be a clear answer to *why* a fragment was
  placed where.
- Learnable from corrections, **without** giving the LLM authority
  over the boundary.

A pure-LLM classifier fails on all three counts. A pure hand-written
ruleset works on all three but doesn't learn.

<details>
<summary><b>Decision drivers</b> (4 items — click to expand)</summary>

- **Tenet 5:** the user arbitrates ambiguity. Classification
  decisions that aren't clear-cut must surface for review, not
  be guessed by an LLM.
- **Tenet 7:** provenance is mandatory. Every fragment landing
  somewhere must carry a trace of why.
- **Trust boundary integrity:** the work-laptop / personal split
  hinges on classification correctness; a non-deterministic
  classifier means the boundary is non-deterministic.
- **Long-term learning:** the system must accumulate institutional
  memory as a readable, version-controlled artifact — not as
  weights or embeddings.

</details>

<details>
<summary><b>Considered options</b> (4 options — click to expand)</summary>

- **Option A:** Pure LLM classifier — ask the model "which profile?"
- **Option B:** Pure hand-written rules — no learning component.
- **Option C:** Embedding similarity (compare fragment embedding to
  per-profile centroids).
- **Option D (chosen):** Two-stage pipeline — AI extraction +
  deterministic rule engine, with rule **synthesis** from user
  corrections.

</details>

## Decision Outcome

**Chosen option:** Option D — two stages with different trust
models. Mining is AI-driven (the fuzzy part); classification is a
deterministic rule engine over a hand-readable YAML file (the
boundary-crossing part). The ruleset is the *learned artifact* —
it grows over time from user corrections, but the LLM never has
authority over the trust boundary directly.

### Implementation details

Stage 1 — **Mining (AI-driven):** Extract candidate fragments from
transcripts. Output: structured fragments with provenance.

Stage 2 — **Classification (rule engine):** A deterministic engine
matches each fragment against a hand-readable YAML ruleset and
produces a `(profile, trace, confidence)` triple.

When the user reclassifies a fragment during review, the tool
synthesizes a candidate rule from that correction (an AI-assisted
step, but with deterministic output: a YAML rule diff) and asks
for explicit approval before appending to `rules.yaml`.

Three rule shapes:

- **classify** — assign a profile when conditions match.
- **forbid** — block a profile assignment regardless of classify rules
  (the redaction guards). Symbolic targets supported: `*` (all),
  `!<name>` (all except), literal names.
- **scope** — narrow further to host-tagged content for a specific host (per [ADR-0037](0037-layer-taxonomy-and-repo-discovery.md), machine-specific content lives in host-tagged sections inside the relevant mode repo).

Every classification carries a trace listing which rules fired and why.
`maury rules trace "<text>"` lets you dry-run any text against
the ruleset.

### Consequences

- ✅ **Good:** Classification is deterministic, repeatable,
  auditable — the entire learned state is plain YAML.
- ✅ **Good:** No silent miscategorization — fragments with no
  matching rule go to a `manual` review queue, never to git.
- ✅ **Good:** Rule synthesis is AI-assisted but cannot bypass
  user approval — boundary stays in human hands.
- ⚖️ **Neutral:** Rules file lives at `<base-repo>/.meta/rules.yaml`
  (in base because rules apply across profiles).
- ⚖️ **Neutral:** Initial seed rules are hand-written from
  existing CLAUDE.md content (hostnames, identity terms,
  code-style keywords, redaction patterns).
- ❌ **Bad:** Two-stage pipeline is more code than a single LLM
  call; reviewers must learn the rule grammar.

### Confirmation

- `src/maury/rules/` implements the engine, schema, trace
  command, and synthesis flow.
- `maury rules trace` exposes the engine to the user for
  dry-running rule changes.
- Manual review queue (Phase 7, per `docs/status.md`) is the
  catch-all for unmatched fragments; no fragment can be silently
  classified without a rule firing.

<details>
<summary><b>Pros and cons of the options</b> (per-option ✅/❌ — click to expand)</summary>

#### Option A: Pure LLM classifier

- ✅ **Good:** Trivially flexible — handles novel patterns
  without rule changes.
- ❌ **Bad:** Opaque — no auditable trace of why a fragment
  landed where.
- ❌ **Bad:** Non-deterministic — same input may classify
  differently across runs or model versions.
- ❌ **Bad:** Unsuitable for a security boundary; gives the
  model authority over data isolation.

#### Option B: Pure hand-written rules

- ✅ **Good:** Deterministic, auditable, all the boundary
  guarantees.
- ❌ **Bad:** Doesn't learn — user has to hand-author every
  rule and notice every drift.
- ❌ **Bad:** Becomes stale; institutional memory only grows
  through explicit user effort.

#### Option C: Embedding similarity

- ✅ **Good:** Adapts as the corpus grows without explicit
  rule writing.
- ❌ **Bad:** Opaque — "this fragment scored 0.73 against
  profile X" doesn't explain *why*.
- ❌ **Bad:** Hard to correct surgically — adjusting one
  classification without affecting others requires retraining
  centroids.
- ❌ **Bad:** Same audit/boundary problems as a pure LLM.

#### Option D (chosen): Two-stage with rule synthesis

- ✅ **Good:** All deterministic-rule benefits at the boundary.
- ✅ **Good:** Learns from user corrections via synthesis,
  without ceding boundary authority.
- ✅ **Good:** Output is plain YAML — readable, diffable,
  hand-editable.
- ❌ **Bad:** Two-stage pipeline complexity; rule grammar to
  learn.
- ❌ **Bad:** Synthesis quality depends on LLM cooperation;
  bad synthesis = more user friction reviewing proposed rules.

</details>

## Build-order placement

Phase 1 (Rule engine + trace tool) — the rule engine, schema,
and `maury rules trace` command are the first things built and
the foundation everything else depends on. Phase 8 (Rule
synthesis) adds the AI-assisted rule-from-correction path on top.

## Followups

- **Profile-scoped rules** that live inside `profiles/<name>/rules.yaml`
  (only applying when that profile is the target) would let
  client-engagement-specific redactions stay out of global
  rules. Punted to v2; tracked in plan §"Open questions".
- **Auto-apply threshold for high-confidence rules** — v1 has
  no auto-apply (everything reviewed). Tighten only if review
  fatigue surfaces in real use.

## Amendment history

- 2026-05-11 — "profile" vocabulary renamed to "mode" per ADR-0037 doctoral examination. References to "profile" in this ADR now read "mode"; no semantic changes.
- 2026-05-22 — **Phase 8 (rule synthesis) split into two paths; the content-edit path shipped, the classify-rule path deferred.** A 2026-05-22 design pass found that this ADR's Phase 8 — "synthesize a candidate `rules.yaml` rule when the user reclassifies a fragment's routing" — presupposes a per-mode *routing* step that the V1 staging-file simplification ([ADR-0022](0022-branch-per-mining-run.md)) removed from the mine→review flow: everything appends to one `mining-findings.md` and the operator refactors by hand, so there is no engine-assigned mode to *correct*. The classifier (`maury rules trace`, `rules/engine.py`) exists but isn't wired into placement. So **classify-rule synthesis (this ADR's literal Phase 8) is deferred** until per-mode auto-placement-by-classifier is built (the two are a pair). What shipped instead is **path B** — content-edit synthesis on the [ADR-0020](0020-two-mining-modes-bulk-and-incremental.md) four-state signals: `maury mine --synthesize` proposes clearer/stronger `CLAUDE.md` *wording* (advisory) for `rephrase-existing` / `investigate-why-not-followed` findings. That targets `CLAUDE.md` (the instructions), not `rules.yaml` (the classification ruleset this ADR governs) — a distinct artifact. See [ADR-0020 2026-05-22 amendment](0020-two-mining-modes-bulk-and-incremental.md#amendment-history) and the design pass in session-state.
- 2026-05-22 — **Phase 8 path A un-deferred (in design): [ADR-0053](0053-rule-driven-auto-placement.md) builds the routing model it was blocked on.** A later 2026-05-22 design pass produced ADR-0053 (rule-driven auto-placement), which wires this ADR's classifier into `maury review` so accepted findings auto-place into the classified source file — the per-mode routing step whose absence had deferred path A. Under ADR-0053, an operator **reclassification** re-files the finding *and* synthesizes a `classify` rule into `rules.yaml` (validated, approved) — the learning loop this ADR's Phase 8 described. Status: **built + accepted 2026-05-22** (CI-green on devel; ADR-0053 accepted). `maury review` now classifies each finding, auto-places accepted ones into the classified source file, and on reclassify synthesizes + appends a `classify` rule to `.meta/rules.yaml` (`src/maury/{placement,rules/synthesize}.py`, `rules/loader.append_rule_to_file`). The `profile=None` → manual-queue escape hatch this ADR defines persists under ADR-0053.
