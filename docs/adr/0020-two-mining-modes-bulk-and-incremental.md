# ADR-0020: Two mining modes — bulk onboarding vs incremental maintenance

**Status:** Accepted
**Date:** 2026-05-06

## Related tenets

- [Tenet 7 — Provenance is mandatory](../tenets.md#7-provenance-is-mandatory)
- [Tenet 9 — Defer to the platform](../tenets.md#9-defer-to-the-platform)
- [Tenet 11 — Explicit beats implicit, with conservative defaults](../tenets.md#11-explicit-beats-implicit-with-conservative-defaults)

## TL;DR

Mining a maintenance trickle (tens of new transcripts) and bootstrap
mining of pre-maury history (hundreds of MB) are genuinely different
workloads — clustering and resumability are load-bearing for bulk,
not for incremental. Maury ships two distinct verbs (`maury mine` for
maintenance, `maury mine --bulk` for onboarding) over shared
extraction + four-state cross-reference infrastructure (NEW /
PRESENT_AND_CLEAR / PRESENT_BUT_UNCLEAR / PRESENT_AND_REINFORCED),
with clustering happening *after* LLM extraction per prototype
findings. Trade-off: Phase 6 splits into 6a + 6b — more surface to
build and test than a single mode would have been.

## Context and Problem Statement

Earlier ADRs ([0005](0005-local-only-mining.md),
[0008](0008-claude-diary-reference.md),
[0011](0011-anthropic-rubric-integration.md),
[0013](0013-active-in-session-capture.md)) treated mining as a
single operation: scan transcripts since the last watermark,
propose updates. That's the *maintenance* case — and it's correct
for it. But it silently misses an equally important case: a user
with months or years of pre-maury Claude Code usage who has never
had a curated CLAUDE.md or skills library, and wants maury to
*bootstrap* their config from their existing transcript history.

These are genuinely different problems:

| Aspect | Maintenance | Onboarding |
|---|---|---|
| When | Periodic (cron, hook, manual) | Once, when a user adopts maury |
| Input size | Tens of new transcripts | Hundreds of MB of historical transcripts |
| Frequency thresholds | Useful but optional | **Load-bearing** — without dedup + frequency filter, output is noise |
| LLM cost | Cheap per run | Significant — must be batched and resumable |
| Review UX | Per-fragment streaming | Cluster-aware bulk review |
| Cross-reference | Compare against current CLAUDE.md to suppress duplicates | No prior config to compare against |

A maintenance run on 100 MB of transcripts works in principle but
takes hours, drowns in noise, and gives the user 800 fragments to
review one-by-one. That's not a tool, it's a punishment. The bulk
case needs different orchestration.

<details>
<summary><b>Decision drivers</b> (5 items — click to expand)</summary>

- **Tenet 7:** provenance is mandatory. Cluster-level
  proposals must carry "12 occurrences across 8 sessions"
  evidence so the curator can judge.
- **Tenet 9:** defer to the platform. Use git history (free)
  for the temporal axis of cross-reference, not a separate
  storage system.
- **Tenet 11:** explicit beats implicit. Two clearly named
  modes (`maury mine` vs `maury mine --bulk`) beat a single
  command that silently switches behavior based on input
  size.
- **Bulk-case orchestration is genuinely different** from
  maintenance — clustering and resumability aren't optional;
  they're load-bearing.
- **Prototype validation** (see "Prototype findings" below)
  showed lexical clustering pre-LLM-extraction was the wrong
  order; clustering must happen *after* LLM extraction.

</details>

<details>
<summary><b>Considered options</b> (5 options — click to expand)</summary>

- **Option A:** Single mining mode that scales to bulk via
  parameters.
- **Option B:** Bulk mining as a one-time wizard, not a
  re-runnable command.
- **Option C:** Skip clustering; do per-fragment LLM
  extraction at bulk scale.
- **Option D:** LLM-based clustering in v1 (not lexical).
- **Option E (chosen):** Two distinct modes (`maury mine`
  for maintenance, `maury mine --bulk` for onboarding) with
  shared extraction/cross-reference infrastructure
  underneath; clustering is post-LLM-extraction, not pre.

</details>

## Decision Outcome

**Chosen option:** Option E — maury supports **two distinct
mining modes** with shared infrastructure underneath. The
orchestration shapes are genuinely different (clustering is
mandatory for bulk, optional for incremental; review UX is
bulk-grouped vs streaming); forcing one mode to handle both
produces a worst-of-both-worlds CLI.

### Implementation details

#### Bulk onboarding (`maury mine --bulk`)

Walks all transcripts under `~/.claude/projects/*.jsonl`, extracts
preference candidates, **clusters similar candidates**, applies a
**frequency threshold** (≥2 occurrences across distinct sessions or
projects), and emits cluster-level proposals.

Required components:

- **Transcript walker** with progress reporting and resumable
  checkpoints (so a multi-hour run survives a Ctrl-C or network blip).
- **Pre-processor** that strips code blocks, normalizes whitespace,
  filters noise (single-word messages, pasted file content).
- **Clusterer** that groups similar candidates *before* LLM extraction.
  v1 algorithm: n-gram similarity + Jaccard distance (stdlib, fast).
  v2 may upgrade to embedding-based clustering if v1 misses things.
- **Frequency filter** with the 2+/3+ thresholds borrowed from
  claude-diary's design (per ADR-0008).
- **Batched LLM extractor** that runs `claude -p` on each cluster
  representative (not on every original message), with prompt caching
  across batches. Per ADR-0012, default backend is the user's
  subscription quota.
- **Cluster-level review UI** that shows a representative fragment +
  its provenance ("12 occurrences across 8 sessions, 6 projects, with
  example excerpts") and lets the user accept/reject the cluster as
  one operation.

#### Incremental maintenance (`maury mine`)

Reads the per-project watermark from
`<repo>/profiles/<profile>/hosts/<host>/watermarks.json`, processes
only transcripts (or transcript chunks) modified since, runs the same
pipeline as bulk but at smaller scale.

Differences from bulk:

- **No mandatory clustering** — the input is small enough that
  per-fragment review is fine.
- **Cross-references against the current rendered config** so we
  don't re-propose content already present.
- **Cheap enough to invoke from a hook** — designed for periodic
  invocation without user attention.

#### Shared infrastructure

Both modes use:

- The JSONL parser (extracts `type:"user"` events with
  `message.content`; handles both string and content-block forms).
- The pre-processor (noise filtering, code-block stripping,
  system-injected pseudo-user content removal — see "Prototype
  findings" below for the noise patterns the prototype validated).
- The LLM extractor (Phase 6.5 backend abstraction; defaults to
  `claude -p`).
- The pre-classification redaction step (configurable patterns
  scrubbed from candidates before any classification or git write —
  important for the work-host paranoid mode per ADR-0014).
- The **post-extraction grouping** step (NOT pre-extraction
  clustering — see prototype findings).
- The **cross-reference step** with full temporal awareness (see next
  section).
- The rule engine (Phase 1, already done) for classification.
- The proposal queue (Phase 7).

#### Cross-reference: the four-state model + temporal awareness

For each candidate finding emerging from extraction, the cross-
reference step decides which of **four states** it's in relative to
the user's current config:

| State | Meaning | Action |
|---|---|---|
| `NEW` | Pattern not represented in current CLAUDE.md / rules | Propose for review |
| `PRESENT_AND_CLEAR` | Rule exists with clear phrasing; user mention is consistent | Suppress (with summary count) |
| `PRESENT_BUT_UNCLEAR` | Rule exists but wording may be why Claude isn't following | Propose rephrasing the existing rule |
| `PRESENT_AND_REINFORCED` | Rule exists AND transcript shows user correcting Claude | Flag with `investigate-why-not-followed` ⚠️ |

The fourth state is the **most actionable signal of all** — "your
config says X but Claude isn't doing X here, and here's where." It's
an audit capability nothing else in the ecosystem provides.

The cross-reference is an LLM call (string-matching can't tell whether
"the substance" of a rule is present; only an LLM can). The prompt
includes:

1. The mined finding (text + verbatim evidence quote + a few messages
   of surrounding transcript context).
2. The **current** rendered CLAUDE.md.
3. **Historical** CLAUDE.md as it was at the transcript timestamp —
   reconstructed via `git rev-list -1 --before=<transcript-ts> HEAD`
   then `git show <sha>:base/CLAUDE.md`. Free because CLAUDE.md lives
   in a synced git repo per ADR-0002.
4. The full `added:` registry of structured rules from `rules.yaml`
   (each rule already carries an `added:` date per the schema in
   Phase 1).

With all four inputs the LLM reasons correctly across the temporal
axis:

- Rule wasn't in historical, IS in current → user was *establishing*
  the rule; suppress as already-promoted (avoids false REINFORCED
  flags on rule-establishing moments).
- Rule was in historical AND user corrected Claude → genuine
  REINFORCED ⚠️.
- Rule was in historical AND user mentioned consistently → CLEAR.
- Rule isn't in current at all → NEW.

#### Two distinct temporal mechanisms

Mining needs two unrelated kinds of timestamps. They get conflated
easily; calling them out:

| Mechanism | Purpose | Storage |
|---|---|---|
| **Mining watermark** | Don't re-mine what we've already processed. | `<repo>/profiles/<profile>/hosts/<host>/watermarks.json`, advanced after each `maury mine` run |
| **Config history** | Tell the cross-reference what CLAUDE.md / rules looked like at the transcript timestamp. | Comes free from git history of the synced repo + per-rule `added:` dates |

For users running bulk-onboarding before they have any maury history:
no historical CLAUDE.md to consult. Graceful degradation: use file
mtime of `~/.claude/CLAUDE.md` as a coarse pre/post indicator; for
transcripts older than that, assume the rule could not have existed;
otherwise mark findings without temporal classification and let the
user judge during review. Future incremental runs (after the first
sync) get full temporal awareness.

### Consequences

- ✅ **Good:** Each mode's orchestration matches its
  workload. Bulk gets clustering + batching + resumability;
  incremental stays cheap and hook-invocable.
- ✅ **Good:** Shared extraction + cross-reference
  infrastructure means one set of tests covers the
  classification logic for both modes.
- ✅ **Good:** Re-running `maury mine --bulk` is safe —
  re-runs detect and skip already-promoted patterns. Useful
  when significant new history accumulates.
- ✅ **Good:** The bulk case's clustering and frequency
  thresholds are the *architecturally novel* part. Without
  them, mining-at-scale produces noise; with them, it
  surfaces durable patterns.
- ⚖️ **Neutral:** Phase 7 (proposal review UI) needs two
  paths — cluster-level review for bulk output, per-
  fragment review for incremental.
- ❌ **Bad:** Phase 6 is bigger than originally scoped (6a
  incremental + 6b bulk). Mitigated by 6a being the simpler
  chunk built first.

### Confirmation

- `maury mine` and `maury mine --bulk` are documented as
  separate commands in `docs/operations.md`.
- The four-state cross-reference (NEW / PRESENT_AND_CLEAR /
  PRESENT_BUT_UNCLEAR / PRESENT_AND_REINFORCED) is the
  contract between the extractor and the review UI;
  validated by the third prototype script.
- Watermarks live at
  `<repo>/profiles/<profile>/hosts/<host>/watermarks.json`.

<details>
<summary><b>Pros and cons of the options</b> (per-option ✅/❌ — click to expand)</summary>

#### Option A: Single mining mode

- ✅ **Good:** One command to learn.
- ❌ **Bad:** Orchestration shapes genuinely differ;
  forcing one mode produces worst-of-both-worlds CLI.

#### Option B: Bulk mining as one-time wizard

- ✅ **Good:** Cleaner first-time UX framing.
- ❌ **Bad:** Users will want to run bulk again after
  significant shifts (new domain, new tooling); idempotent
  re-runnable is required.

#### Option C: Skip clustering; per-fragment LLM at bulk scale

- ✅ **Good:** Simpler pipeline.
- ❌ **Bad:** 14k user messages × LLM call is expensive in
  time and money, and produces 14k fragments to review.
  Clustering is the order-of-magnitude win.

#### Option D: LLM-based clustering in v1

- ✅ **Good:** Catches semantic similarity lexical methods
  miss.
- ❌ **Bad:** n-gram + Jaccard is cheap, deterministic, and
  good enough for the v1 validation question (does the
  corpus contain real patterns at all).
- ⚖️ **Neutral:** Embedding-based clustering is a v2
  upgrade if v1 has visible misses.

#### Option E (chosen): Two modes + post-LLM clustering

- ✅ **Good:** Each mode's shape matches its workload.
- ✅ **Good:** Order (LLM extract → cluster) is the
  prototype-validated order, not the original (cluster →
  LLM) order.
- ❌ **Bad:** Phase 6 surface is larger (6a + 6b).

</details>

## Build-order placement

Phase 6 splits into:
- **6a (incremental)** — the simpler chunk; build first to
  validate the extraction pipeline against real
  transcripts.
- **6b (bulk)** — layers clustering + batching +
  resumability + progress on top of 6a's foundations.
- **6.5 (LLM backend abstraction,
  [ADR-0012](0012-llm-backend.md))** — hard prereq for
  both sub-phases.

Phase 7 (proposal review UI) consumes both modes' output
and needs two review paths.

## Prototype findings (validated 2026-05-06)

Three throwaway scripts under `scripts/` validated this ADR's design
end-to-end against the project owner's real 761-message transcript corpus
across 8 projects. Each finding shaped the architecture above.

### Finding 1: lexical clustering does NOT pre-extract signal

`scripts/mining_prototype.py` ran n-gram + Jaccard clustering across
761 user messages. Result after aggressive system-noise filtering:
**zero clusters across 2+ distinct sessions.** The user phrases the
same preference many different ways ("use ruff for linting" vs "lint
with ruff" vs "ruff handles formatting too"). Lexical similarity is
not semantic similarity, and at the volume of one user's transcripts,
patterns don't repeat in their lexical surface form often enough to
cluster.

**Implication on the architecture:** **clustering happens AFTER LLM
extraction, not before.** The original "cluster pre-LLM to reduce
LLM cost" optimization was wrong — there's nothing to cluster
pre-LLM. Order has to flip:

1. Walk transcripts in **windowed batches** (50-100 user messages
   per window).
2. **LLM extracts** per-window candidate preferences.
3. **THEN** group/cluster the LLM-extracted candidates (now
   semantically dense; lexical or LLM-based grouping works).
4. Apply frequency filter on the grouped candidates.

### Finding 2: LLM extraction surfaces real, useful preferences

`scripts/mining_prototype_llm.py` ran 3 windows of 50 messages each
through `claude -p` with a structured extraction prompt. Output:
**18 real findings** including:

- "Use 2>&1 | tee <NAME>_$(date +...).log on commands the user runs"
  (already promoted — surfaces correctly as `PRESENT_AND_REINFORCED`
  in the cross-reference)
- "Write multi-line commands as scripts, not one-liners"
- "Save state before risky/exploratory actions"
- "Prefer approaches that avoid disabling SIP / code signing on macOS"
- "Default reviewer/assignee on personal repos = <the project owner>"
- Host-specific facts (pipx venv path, Python version, iTerm2
  keybinding limitations)

Cost: ~12K input tokens for 150 messages. A full bulk pass over the
761-message corpus would be ~50K tokens — moments of subscription
quota on `claude -p` per ADR-0012.

### Finding 3: cross-reference correctly classifies the 4 states

`scripts/mining_crossref_prototype.py` evaluated one finding (the
2>&1 pattern) against current `~/.claude/CLAUDE.md`. Output:

```json
{
  "state": "PRESENT_AND_REINFORCED",
  "claude_md_quote": "When suggesting commands for the user to run: always append `2>&1 | tee ...`",
  "rationale": "The rule is already in CLAUDE.md verbatim, but the transcript shows the user correcting Claude after it suggested a command without the tee redirection — clear sign the existing rule isn't being followed.",
  "suggested_action": "investigate-why-not-followed"
}
```

The classification logic is correct; the only nuance the prototype
revealed is the need for **temporal awareness** (the transcript
predated the rule's introduction in this case, so the correct
classification is "already-promoted, not REINFORCED"). That's
addressed by the temporal section above.

## Followups

- **Productize the three prototypes** as Phase 6a/6b/6c modules with
  proper test coverage and clean integration into the rule engine
  + proposal queue.
- **Cluster representative selection** (post-extraction): when a
  cluster has 12 similar findings, which one do we show in the
  review UI? The *medoid* (highest mean similarity to others) is the
  obvious choice for the lexical-cluster case; for LLM-grouped
  candidates, the LLM can return a canonical representative as part
  of the grouping call.
- **Reuse doctor's pattern-matching from ADR-0011**: the doctor
  already evaluates CLAUDE.md against the Anthropic rubric. The
  cross-reference step could reuse some of that infrastructure
  (notably the platitude / standard-convention checks) to flag
  proposals that would *worsen* CLAUDE.md if accepted.
- **Watermark schema**: define the `watermarks.json` shape. Per-
  transcript-file timestamps with sha of last-processed event seem
  right; could use just file mtime if simpler.

## Amendment history

- 2026-05-11 — "profile" vocabulary renamed to "mode" per ADR-0037 doctoral examination. References to "profile" in this ADR now read "mode"; no semantic changes.
- 2026-05-22 — **the four-state actions gained a generation step (Phase 8 path B).** `rephrase-existing` (PRESENT_BUT_UNCLEAR) and `investigate-why-not-followed` (PRESENT_AND_REINFORCED) previously only *classified*; `maury mine --synthesize` now makes one extra LLM call per such finding (`src/maury/mining/synthesize.py::synthesize_rewrite`) to generate a **proposed CLAUDE.md rewrite** — reword for clarity (UNCLEAR) or strengthen (REINFORCED). The proposal is **advisory**: it rides in the finding's `mining-findings.md` block as a "proposed rewrite" section; the operator applies it to the right source file by hand (V1 staging-file model per [ADR-0022](0022-branch-per-mining-run.md)). No source-rule auto-location / applied diff. This is Phase 8 path B; classify-rule synthesis into `rules.yaml` (the literal [ADR-0004](0004-rule-engine-classification.md) Phase 8) stays deferred until per-mode auto-placement exists. **Also fixed** in the same change: the run-branch emit read a non-existent `.entries` attr on the cross-reference summary, so `Crossref-State:` trailers always recorded `NEW` after `--crossref`; states now thread correctly.
