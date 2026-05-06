# scripts/ — throwaway prototypes and one-shot helpers

These are NOT part of the maury package. They're standalone Python
scripts that validate ideas before we commit to building them
properly. Each one was used to answer a specific architectural
question; the answers shaped the ADRs.

Run them with `uv run python scripts/<script>.py`.

## Mining prototypes (validate ADR-0020)

Three scripts together validated the bulk-mining design end-to-end
against Charles's real `~/.claude/projects/` corpus.

### `mining_prototype.py` — lexical n-gram clustering

**Question asked:** can we cluster transcript messages by lexical
similarity (n-gram + Jaccard) to find recurring patterns cheaply,
before invoking the LLM?

**Answer:** **No.** With aggressive system-noise filtering, zero
clusters across 2+ distinct sessions. Users phrase the same
preference many different ways; lexical similarity ≠ semantic
similarity at this corpus scale.

**Outcome:** ADR-0020 was amended — clustering moves from
"pre-LLM optimization" to "post-LLM grouping step." The script's
noise filters (system-injected pseudo-user content patterns) are
directly reused in the production miner.

### `mining_prototype_llm.py` — LLM-driven extraction

**Question asked:** does `claude -p` (per ADR-0012) actually surface
real durable preferences when given windowed batches of transcript
messages?

**Answer:** **Yes, unambiguously.** 18 real findings from 3 windows
of 50 messages each in one project. Workflow patterns, host facts,
style preferences, identity facts — all correctly extracted with
verbatim evidence quotes. ~12K input tokens per 150 messages.

**Outcome:** Phase 6 build is justified. The script's prompt
template is the starting point for the production extractor.

### `mining_crossref_prototype.py` — four-state cross-reference

**Question asked:** for a mined finding, can the LLM correctly
distinguish "new preference" from "already in CLAUDE.md but Claude
isn't following it"?

**Answer:** **Yes.** Demonstrated for the 2>&1 tee-pattern finding:
the LLM correctly identified that the rule was in CLAUDE.md verbatim,
saw the assistant in the transcript ignoring it, and bucketed as
`PRESENT_AND_REINFORCED` with action `investigate-why-not-followed`.

The prototype also revealed the need for **temporal awareness**
(this specific case predated the rule's introduction; only with
historical CLAUDE.md context can the LLM tell "user established
rule" from "user corrected Claude on existing rule"). That was
folded into ADR-0020.

## What happens to these after Phase 6 ships

They become regression tests. Once the production miner exists,
re-running the prototypes against the same corpus should produce
roughly comparable findings. If the production miner produces
materially worse output, we have a clear baseline to debug against.
