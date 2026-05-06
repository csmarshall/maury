# ADR-0020: Two mining modes — bulk onboarding vs incremental maintenance

**Status:** Accepted
**Date:** 2026-05-06

## Related tenets

- [Tenet 7 — Provenance is mandatory](../tenets.md#7-provenance-is-mandatory)
- [Tenet 9 — Defer to the platform](../tenets.md#9-defer-to-the-platform)
- [Tenet 11 — Explicit beats implicit, with conservative defaults](../tenets.md#11-explicit-beats-implicit-with-conservative-defaults)

## Context

Earlier ADRs (0005, 0008, 0011, 0013) treated mining as a single
operation: scan transcripts since the last watermark, propose updates.
That's the *maintenance* case — and it's correct for it. But it
silently misses an equally important case: a user with months or years
of pre-maury Claude Code usage who has never had a curated CLAUDE.md
or skills library, and wants maury to *bootstrap* their config from
their existing transcript history.

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

## Decision

Maury supports **two distinct mining modes** with shared infrastructure
underneath:

### Bulk onboarding (`maury mine --bulk`)

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

### Incremental maintenance (`maury mine`)

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

### Shared infrastructure

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

### Cross-reference: the four-state model + temporal awareness

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

### Two distinct temporal mechanisms

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

## Consequences

- Phase 6 splits into **6a (incremental)** and **6b (bulk)**. 6a is
  the simpler chunk; build it first to validate the extraction
  pipeline. 6b layers clustering + batching + resumability + progress
  on top of 6a's foundations.
- Phase 6.5 (LLM backend abstraction) is a hard prereq for both
  sub-phases. Without it we'd hardcode `claude -p` calls everywhere.
- Phase 7 (proposal review UI) needs **two paths**: cluster-level
  review for bulk output, per-fragment review for incremental.
- The bulk case's clustering and frequency thresholds are the
  *architecturally novel* part. Without them, mining-at-scale produces
  noise; with them, it surfaces durable patterns.
- A user can re-run `maury mine --bulk` at any time. Re-runs detect
  and skip already-promoted patterns. Useful when significant new
  history accumulates (e.g., after working on a new domain for a
  month).

## Alternatives considered

- **Single mining mode that scales to bulk via parameters.**
  Rejected: the orchestration shapes are genuinely different
  (clustering is mandatory for bulk, optional for incremental; review
  UX is bulk-grouped vs streaming). Forcing one mode to handle both
  produces a worst-of-both-worlds CLI.
- **Bulk mining as a one-time wizard, not a re-runnable command.**
  Rejected: users will want to run bulk again after a major shift in
  how they're using Claude Code (new project domain, new tooling). It
  must be idempotent and re-runnable.
- **Skip clustering; do per-fragment LLM extraction at bulk scale.**
  Rejected: 14k user messages × LLM call is expensive in time and
  money, and produces 14k fragments to review. Clustering before
  extraction is the order-of-magnitude win.
- **LLM-based clustering in v1.** Considered. n-gram + Jaccard is
  cheap, deterministic, and good enough for the validation question
  (does the corpus contain real patterns at all). Embedding-based
  clustering is a v2 upgrade if v1 has visible misses.

## Prototype findings (validated 2026-05-06)

Three throwaway scripts under `scripts/` validated this ADR's design
end-to-end against Charles's real 761-message transcript corpus
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
- "Default reviewer/assignee on personal repos = Charles"
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
  "claude_md_quote": "When suggesting commands for Charles to run: always append `2>&1 | tee ...`",
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
