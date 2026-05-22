# ADR-0053: Rule-driven auto-placement + classify-rule synthesis

> Maury's ADR format is a **MADR/Nygard hybrid** with maury-specific
> sections preserved. See [`docs/process/adr-format.md`](../process/adr-format.md)
> for the full specification.

**Status:** Proposed
**Date:** 2026-05-22

## Related tenets

- [Tenet 3 — Trust boundaries are physical, not policy](../tenets.md#3-trust-boundaries-are-physical-not-policy) — placement targets a layer inside the operator's own trust boundary; the classifier is deterministic, so the boundary it routes across stays deterministic.
- [Tenet 5 — The user arbitrates ambiguity](../tenets.md#5-the-user-arbitrates-ambiguity) — the engine proposes a target and (on reclassification) a routing rule; the operator confirms before either lands. No LLM judgment over routing.
- [Tenet 7 — Provenance is mandatory](../tenets.md#7-provenance-is-mandatory) — every placed fragment carries its mined-from origin and `Content-Hash`; every synthesized rule carries its `added`/`reason` provenance.

## Context and Problem Statement

Today the mining pipeline runs `maury mine --write-run-branch` →
`maury review <run-id>`, and **every accepted finding appends to one
flat `mining-findings.md`** at the repo root. The operator then
manually refactors those bullets into the correct source file —
the repo-root `CLAUDE.md` for base content, a mode fragment for
mode-scoped content, a host-tagged fragment for one-box content.
That hand-refactor step is the deliberate V1 simplification recorded
in [ADR-0022](0022-branch-per-mining-run.md)'s implementation
amendment, which says plainly: *"A future ADR can introduce smarter
routing once we have operator experience with the staging-file
model."* **This is that future ADR.**

The pieces to do the routing already exist; they were just never
wired together:

- The **rule engine** ([ADR-0004](0004-rule-engine-classification.md))
  classifies a fragment. `classify_fragment(text, rules,
  known_profiles)` returns a `Classification(profile, host_overlay,
  confidence, trace, forbidden_ids)`. `profile=None` means "no rule
  matched" — ADR-0004 already routes that to a manual review queue.
- The **render engine** already reads the exact placement targets a
  classification implies: base content lives in the repo-root
  `CLAUDE.md`; a mode's content lives in
  `profiles/<mode>/CLAUDE.md.fragment`; host-tagged content lives in
  `profiles/<mode>/hosts/<host>/CLAUDE.md.fragment`. (The code keys
  these by `profile_id`/`profiles/`; the canonical vocabulary term
  for the content namespace is **mode** per
  [`docs/concepts.md`](../concepts.md). The on-disk paths keep the
  `profiles/` spelling.)

So routing isn't new infrastructure — it's a **wiring + placement-
mechanics decision**: map the classifier's output onto the
already-understood target files, during `maury review`, on the
review branch, with provenance.

Wiring this in also unblocks a capability that has been deferred
twice. [ADR-0004](0004-rule-engine-classification.md)'s **Phase 8**
("synthesize a candidate `rules.yaml` rule when the operator
reclassifies a fragment") presupposes that the operator *sees the
engine route a fragment to mode X*, disagrees, and corrects it to
mode Y. The V1 staging-file model removed that routing step
entirely — there was no engine-assigned mode to correct — so Phase 8
split into a content-edit path (path B, shipped) and a classify-rule
path (path A, deferred). **This ADR builds the routing step path A
was waiting on, and path A becomes the learning loop that grows the
ruleset from the operator's reclassifications.**

Two artifacts are in play and must not be conflated:

- **`rules.yaml`** is the **classification ruleset** — it decides
  *where a fragment should go*. It is the routing table.
- **`CLAUDE.md` and its fragments** are the **actual instructions** —
  the content Claude reads. They are the destination.

Auto-placement writes a finding's *content* to an instruction file.
Classify-rule synthesis writes a *routing rule* to `rules.yaml`. They
are different writes to different files for different purposes.

## Decision Drivers

- **ADR-0022 sanctioned this evolution.** The single-staging-file
  model was an explicit V1 stopgap with a documented upgrade path;
  this is not a reversal, it is the planned next step.
- **Tenet 5 — the user arbitrates.** Routing crosses
  content-namespace lines that matter; the operator must be able to
  see, confirm, and override every classification, and must approve
  any rule the engine proposes to learn.
- **Tenet 3 — the boundary stays deterministic.** Placement only
  ever writes inside the operator's own RW repo (their trust
  boundary); the classifier that decides the target is a
  deterministic rule engine, never an LLM verdict.
- **Tenet 7 — provenance.** A fragment that lands in a fragment file
  must be traceable to the mining run that surfaced it; a rule that
  lands in `rules.yaml` must record why it was added.
- **The pieces already exist.** `classify_fragment` and the render
  engine's target resolution are both shipped and tested; the cost
  here is integration, not invention.
- **A permanent escape hatch is required.** Not every finding will
  match a rule, and the operator must never be forced to invent a
  routing rule just to file a one-off. `profile=None` → manual queue
  must remain a first-class, permanent destination.

## Considered Options

- **Option 1:** Keep the V1 manual-refactor model unchanged.
- **Option 2:** Classifier as a non-binding *suggester* — it prints a
  hint, the operator still hand-files everything.
- **Option 3 (chosen):** Classifier as the *router* — accept
  auto-places into the classified target file on the review branch;
  unmatched/low-confidence falls back to the manual queue;
  reclassification both re-files and synthesizes a routing rule.

## Decision Outcome

**Chosen option:** Option 3 — the classifier becomes the router. This
is the only option that delivers ADR-0022's promised "smarter
routing," and the only one that closes ADR-0004's Phase 8 learning
loop: a router the operator can correct is exactly what makes a
correction signal worth synthesizing into a rule. Option 1 freezes a
known stopgap; Option 2 builds the classifier-in-the-loop plumbing
but stops short of the payoff, leaving the operator doing the same
hand-refactor while also reading a hint. Because there are no users
yet, there is no thin-V1 to migrate off — the full classifier-driven
model is built directly.

The model is **hybrid**: matched findings auto-place into their
source fragment; unmatched findings (`profile=None`) fall to the
manual queue `mining-findings.md`, which is
[ADR-0004](0004-rule-engine-classification.md)'s permanent "no rule →
manual review" escape hatch and is explicitly **not** throwaway.
For matched findings this **supersedes**
[ADR-0022](0022-branch-per-mining-run.md)'s V1 single-staging-file
model — it is precisely the "smarter routing" ADR-0022 anticipated.
For unmatched findings, the staging file persists unchanged.

(ADR-0004 phrases the manual queue as "never to git." That means a
fragment is never *silently auto-classified* into a committed source
location without review — not that the queue is uncommitted. The queue
here is the reviewed `mining-findings.md` staging file on the review
branch: it is committed, but only as reviewable staged content the
operator hand-files, never as a silent classification. The two
statements are consistent.)

### Implementation details

#### Where it runs: classify-on-accept inside `maury review`

`maury review <run-id>` loads `.meta/rules.yaml` and the manifest at
startup, then for each finding commit on `maury/run/<run-id>` runs
`classify_fragment(finding_text, rules, known_profiles)`. Before
prompting, it shows the engine's classification and trace, e.g.:

```
→ mode `work` via rule `hostname-acme`, confidence: high
  (trace: classify hostname-acme matched on keyword "acme-corp")
```

The per-finding prompt keeps the
[ADR-0022](0022-branch-per-mining-run.md) review verbs
(`accept` / `reject` / `edit` / `skip` / `quit`) and adds one:

- **accept** — place the finding's content into the **classified
  target file** on the review branch (mechanics below), carrying its
  provenance. This replaces "append to `mining-findings.md`" for
  matched findings.
- **reclassify** — the operator picks a *different* target (a
  different mode, base, host-tagged, or the manual queue). Maury
  (1) places the content there and (2) synthesizes a `classify` rule
  so future similar fragments auto-route (see "The learning loop"
  below). **Always available**, on any finding, regardless of the
  engine's confidence.
- **reject** / **edit** / **skip** / **quit** — unchanged from
  ADR-0022's review side, including `Content-Hash`-preserving edit
  and the trailing `Rejected-Content-Hash` no-op commit.

#### Placement targets

The target a `Classification` resolves to mirrors the render engine's
existing source-file map:

| Classification | Source file (on the review branch) |
|---|---|
| base (`profile` resolves to base) | repo-root `CLAUDE.md` |
| a mode | `profiles/<mode>/CLAUDE.md.fragment` |
| a mode with `host_overlay` | `profiles/<mode>/hosts/<host>/CLAUDE.md.fragment` |
| `profile=None` (no rule matched) | `mining-findings.md` (manual queue) |

`forbid` and `scope` rules need no special placement handling: the
engine has already applied them by the time `classify_fragment`
returns (a forbidden mode is removed from contention; a matched
`scope` rule supplies the `host_overlay`). Placement simply honors
the `Classification` it is handed.

Placement into a mode fragment then flows downward by inheritance per
[ADR-0019](0019-inheritance-semantics-refine-by-default.md)'s
refine-by-default semantics: a fragment placed in `work`'s file
composes into every render of `work` and its descendant modes, with
no further routing decision required. Placement decides *which layer*
content enters; ADR-0019 decides *how that layer composes*.

#### Placement mechanics: append-with-provenance on the review branch

Placement appends the finding's content to the target file as a
provenance-marked block and commits it on `maury/review/<run-id>`,
reusing the apply-by-reconstruct discipline ADR-0022's review side
already established (so a skipped or rejected earlier finding never
leaves a later one conflict-prone). The block carries:

- the finding's preference text (verbatim, or as edited);
- a **mined-from** marker naming the originating mining run; and
- the finding's **`Content-Hash`**, preserved verbatim from the run
  commit so a future re-surface of the same idea still dedups against
  the placed content (per ADR-0022's dedup contract and its
  edit-preserves-hash refinement).

The placement is on the review branch, so it remains fully reviewable
before the operator merges to main. Nothing auto-merges (Tenet 1, per
ADR-0022's review side).

#### Confidence gating

The classifier's `confidence` gates how much ceremony an accept
requires:

- **high** confidence — `accept` auto-places into the classified
  target without an extra confirmation step.
- **low / medium** confidence — `accept` shows the proposed target
  and prompts the operator to confirm it before placing, so a
  weakly-matched finding doesn't silently land in the wrong fragment.

In every case the operator may instead choose **reclassify** to pick a
different target. Batch flags from ADR-0022 (`--accept-all` /
`--reject-all`) bypass the per-finding prompt; `--accept-all` places
each finding into its classified target (treating the classification
as confirmed), which is the documented scripted-trust path.

#### The learning loop: classify-rule synthesis (ADR-0004 Phase 8 path A)

When the operator **reclassifies** a finding — overriding the
engine's target — that correction is the signal ADR-0004's Phase 8
was designed around. Maury:

1. **Places** the finding into the operator-chosen target (above).
2. **Synthesizes a `classify` rule** that would have routed this
   fragment to the chosen target:
   `synthesize_classify_rule(finding, target, manifest)` makes one
   AI-assisted call that proposes a single `rules.yaml` entry (a
   `when` condition → `then.profile`/`then.host_overlay`). **The LLM
   proposes the rule's shape; the YAML serialization is
   deterministic.** This mirrors ADR-0004's design exactly: the LLM
   assists, but never gains authority over the boundary.
3. **Validates** the proposed entry via `maury rules validate` before
   it can land. An invalid synthesized rule is shown and discarded,
   never appended.
4. **Asks for explicit approval**, then appends the validated entry
   to `.meta/rules.yaml` **on the review branch**, with its `added`
   date and a `reason` recording the reclassification that produced
   it (Tenet 7). Future mining runs' findings that match the new
   `when` will auto-route to the corrected target.

This is the pairing the session-state design pass locked:
**auto-placement *applies* `rules.yaml`; reclassification *grows*
it.** The operator approves before either the placement or the rule
lands; the boundary stays in human hands (Tenet 5).

Note the artifact split once more: step 1 writes *content* to an
instruction file; steps 2–4 write a *routing rule* to `rules.yaml`.
Reclassifying re-files the one finding immediately **and** teaches
the router for next time.

#### Relationship to the already-shipped path B

[ADR-0020](0020-two-mining-modes-bulk-and-incremental.md)'s
`maury mine --synthesize` (Phase 8 path B, shipped 2026-05-22)
rewrites **`CLAUDE.md` wording** for `rephrase-existing` /
`investigate-why-not-followed` findings — it edits the *instructions*.
This ADR is the **`rules.yaml` side** (path A) — it grows the
*routing table*. The two are complementary and target different
artifacts; neither subsumes the other.

### Consequences

- ✅ **Good:** Accepted findings land in the right source file
  automatically; the manual-refactor step that ADR-0022 deferred is
  eliminated for matched findings.
- ✅ **Good:** Closes ADR-0004's Phase 8 learning loop —
  reclassifications grow `rules.yaml`, so routing improves with use
  instead of staying static.
- ✅ **Good:** Provenance is preserved end-to-end (mined-from +
  `Content-Hash` on placed content; `added`/`reason` on synthesized
  rules), satisfying Tenet 7 and keeping dedup correct.
- ✅ **Good:** The `profile=None` → manual queue escape hatch means
  the operator is never forced to invent a routing rule to file a
  one-off finding.
- ⚖️ **Neutral:** `maury review` gains real state — it now loads
  rules + manifest, classifies, gates on confidence, and can mutate
  two files (a fragment and `rules.yaml`) per finding. Larger surface
  than the ADR-0022 review walk, but the building blocks are shipped.
- ⚖️ **Neutral:** Two source files can change in one review session
  (the placed fragment and `rules.yaml`); both ride the same review
  branch and merge together, so there is still one mergeable unit.
- ❌ **Bad:** Synthesized-rule quality depends on LLM cooperation; a
  weak proposal costs the operator a review-and-discard. Mitigated by
  the validate-before-land gate and explicit approval (same tradeoff
  ADR-0004 already accepted for synthesis).
- ❌ **Bad:** Misrouting risk for high-confidence-but-wrong matches.
  Mitigated by reclassify always being available and by the trace
  being shown before every accept.

### Confirmation

- Unit tests cover the placement engine
  (`place_finding(repo, classification, text, ...)` →
  append-to-fragment with provenance + dedup marker; pure logic +
  git layer) and classify-rule synthesis
  (`synthesize_classify_rule(...)` with a stubbed LLM backend, then a
  `maury rules validate` pass on the output).
- Integration tests cover the reshaped `review_run`: classification
  shown per finding, accept places into the classified target,
  reclassify places + synthesizes + validates + (on approval)
  appends to `rules.yaml`, `profile=None` falls to the manual queue,
  and confidence gating prompts on low/medium.
- Doc-review agents check that this ADR's two-artifact distinction
  (`rules.yaml` = routing vs `CLAUDE.md`/fragments = instructions)
  is not blurred in any doc that describes the review flow, and that
  the `profile=None` → manual-queue escape hatch is described as
  permanent.

## Pros and Cons of the Options

### Option 1: Keep the V1 manual-refactor model

The status quo — every accepted finding appends to `mining-findings.md`
and the operator hand-files it.

- ✅ **Good:** Zero new code; the review flow stays minimal.
- ❌ **Bad:** Leaves the manual-refactor toil ADR-0022 explicitly
  flagged as temporary.
- ❌ **Bad:** Keeps ADR-0004's Phase 8 path A permanently blocked —
  there is no engine routing to correct, so no correction signal to
  learn from.

### Option 2: Classifier as a non-binding suggester

The engine classifies and prints a hint, but the operator still
hand-files every finding.

- ✅ **Good:** Surfaces the classification without committing to
  auto-placement mechanics.
- ❌ **Bad:** Builds the classify-in-the-loop plumbing yet keeps the
  hand-refactor — most of the cost, little of the payoff.
- ❌ **Bad:** A hint with no action and no learning loop doesn't grow
  `rules.yaml`; Phase 8 path A stays blocked.

### Option 3 (chosen): Classifier as the router

Accept auto-places; unmatched/low-confidence falls back to the manual
queue; reclassify re-files and synthesizes a routing rule.

- ✅ **Good:** Delivers ADR-0022's promised smarter routing and
  closes ADR-0004's Phase 8 learning loop in one cohesive design.
- ✅ **Good:** Keeps the operator in control — trace shown, confidence
  gated, reclassify always available, rules approved before landing.
- ❌ **Bad:** Largest surface of the three; `maury review` becomes
  stateful and can mutate two files per finding.

## Build-order placement

- **Phase 7 (Proposal queue + review UI):** this ADR reshapes the
  already-shipped `maury review` to classify-on-accept and auto-place.
- **Phase 8 (Rule synthesis):** this ADR builds **path A**
  (classify-rule synthesis into `rules.yaml`), the half ADR-0004's
  Phase 8 left deferred. Path B (content-edit synthesis,
  [ADR-0020](0020-two-mining-modes-bulk-and-incremental.md)) already
  shipped 2026-05-22.

Sliced build (after this ADR lands):

1. **This ADR** + amendment entries on
   [ADR-0004](0004-rule-engine-classification.md) (Phase 8 path A
   un-deferred) and
   [ADR-0022](0022-branch-per-mining-run.md) (staging-file model
   superseded for matched findings), plus a
   [`docs/status.md`](../status.md) update.
2. **Placement engine** — `place_finding(...)`: append-to-fragment
   with provenance + dedup marker (pure logic + git layer). Tests.
3. **Classify-rule synthesis** — `synthesize_classify_rule(...)` →
   one `rules.yaml` entry; validate via `maury rules validate`. Tests
   with a stubbed LLM backend.
4. **Review integration** — load rules + manifest; classify each
   finding; accept places; reclassify places + synthesizes;
   `profile=None` → manual queue; confidence gating. Reshapes
   `review_run`. Tests.
5. **CLI/UX + audit + docs** — reclassify prompt + flags, audit
   wiring (see Followups re: event kinds), doc amendments, and a
   fresh-context doc-review.

## Followups

- **Audit event kinds for placement and synthesis.**
  [ADR-0035](0035-audit-log.md)'s event-kind enumeration is a closed
  list and currently has no `finding_placed` or `rule_synthesized`
  kind. Following the precedent set by ADR-0022's review side (which
  reused `review_completed` rather than inventing new kinds), V1 of
  this work should prefer to fold placement/synthesis observability
  into the existing `review_completed` payload where possible. If a
  dedicated kind is genuinely warranted, it requires an explicit
  ADR-0035 amendment **in the same commit** that introduces it —
  decide during the CLI/audit slice.
- **Synthesized-rule precision tuning.** Early synthesized `when`
  conditions may be too broad (over-routing) or too narrow
  (under-routing). No auto-tuning in V1 — the operator reviews each
  proposed rule. Revisit only if review fatigue surfaces in real use
  (parallels ADR-0004's "auto-apply threshold" followup).
- **Mode-scoped rule placement.** [ADR-0004](0004-rule-engine-classification.md)
  already tracks `profiles/<mode>/rules.yaml` (mode-scoped rules) as a
  v2 followup; a synthesized rule that only applies under one mode
  could land there rather than in base `.meta/rules.yaml`. Deferred to
  that same v2 work.

## Amendment history

- 2026-05-22 — initial publication.

## Related ADRs

- [ADR-0004 — Smart-but-strict classification](0004-rule-engine-classification.md)
  — defines the rule engine, the three rule shapes
  (`classify`/`forbid`/`scope`), the `profile=None` → manual-queue
  rule, and Phase 8 rule synthesis. This ADR implements/refines
  ADR-0004's **Phase 8 path A** (classify-rule synthesis), which
  ADR-0004's 2026-05-22 amendment recorded as deferred pending the
  routing model built here. **ADR-0004 should be amended** to note
  that path A is un-deferred by this ADR (this ADR does not edit it).
- [ADR-0022 — Branch-per-mining-run](0022-branch-per-mining-run.md)
  — defines the run/review branch lifecycle and the V1
  single-staging-file simplification this ADR's auto-placement
  **supersedes for matched findings** (the "smarter routing" ADR-0022
  anticipated). The manual queue persists for `profile=None`.
  **ADR-0022 should be amended** accordingly.
- [ADR-0019 — Inheritance semantics: refine by default](0019-inheritance-semantics-refine-by-default.md)
  — content placed into a mode fragment flows to descendant modes by
  inheritance; placement chooses the layer, ADR-0019 governs how the
  layer composes.
- [ADR-0020 — Two mining modes](0020-two-mining-modes-bulk-and-incremental.md)
  — defines the four-state crossref and Phase 8 **path B**
  (`--synthesize` content rewrite, shipped). This ADR is the
  complementary `rules.yaml` side (path A); the two target different
  artifacts.
- [ADR-0035 — Audit log](0035-audit-log.md) — the closed event-kind
  enumeration that any new placement/synthesis audit event must amend
  (see Followups).
