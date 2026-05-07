# ADR-0005: Local-only mining — never cross-host transcript pull

**Status:** Accepted
**Date:** 2026-05-06

## Related tenets

- [Tenet 4 — Sensitive data stays local](../tenets.md#4-sensitive-data-stays-local)

## TL;DR

Centralizing mining (SSH to each host, pull all transcripts, mine once)
is operationally simple but defeats every trust-boundary guarantee —
work-host transcripts would land on personal hosts. Maury runs the full
miner + classifier locally on each host; raw transcripts never leave
the host that produced them. Cross-host coordination happens via git
only, carrying classified, sanitized fragments. Trade-off: no
cross-host pattern dedup at extraction time; per-host LLM credentials
to manage; misclassified cross-profile content quarantines locally
instead of silently propagating.

## Context and Problem Statement

A natural design would centralize mining: SSH to each host, pull all the
JSONL transcripts to a single workstation, run extraction once, push
proposals back. This is operationally simple and gives the miner a
unified view across hosts.

It also defeats the entire trust-boundary architecture. The work
laptop's transcripts may contain employer-confidential content; pulling
them to a personal host would create exactly the data flow we built the
repo-per-trust-boundary structure (ADR-0002) to prevent.

<details>
<summary><b>Decision drivers</b> (4 items — click to expand)</summary>

- **Tenet 4:** sensitive data stays local. Raw transcripts are
  the most sensitive data maury touches; centralizing them
  inverts the boundary.
- **Trust-boundary integrity (ADR-0002):** any cross-host raw
  data flow defeats the per-repo isolation guarantee.
- **Anomaly observability:** misclassifications should be
  structurally visible (quarantined fragments), not silently
  fail open.
- **Simplicity of deployment:** each host should be self-
  contained for the mining pipeline.

</details>

<details>
<summary><b>Considered options</b> (4 options — click to expand)</summary>

- **Option A:** Centralized mining — pull raw transcripts to one
  workstation, mine there, push back.
- **Option B:** Centralized mining with redaction at extraction —
  redact in transit before raw transcripts cross hosts.
- **Option C:** Push raw transcripts to per-profile repos as a
  shared corpus.
- **Option D (chosen):** Local-only mining — each host mines its
  own transcripts; only sanitized, classified fragments cross via
  git.

</details>

## Decision Outcome

**Chosen option:** Option D — each host mines its own
`~/.claude/projects/*.jsonl` transcripts locally. Raw transcripts
never leave the host. Cross-host coordination happens via git
only. The only thing pushed to a repo is sanitized, classified
fragments. This is the only option that preserves the
ADR-0002 trust boundary.

### Implementation details

Cross-profile content emerging on the wrong host (e.g., a
transcript on the work laptop that mentions `linux-server` or
`192.168.1.x`) is treated as an anomaly: the fragment is
**quarantined locally**, never written to git, and surfaced in
the next review with a clear "anomaly: home content from work
host" tag for the user to investigate.

Per-host responsibilities for the mining pipeline (extraction +
classification + rule engine) live in `src/maury/mining/`,
`src/maury/rules/`, and the per-host `~/.claude/maury-state/`
working directory ([ADR-0029](0029-maury-state-layout-contract.md)).

### Consequences

- ✅ **Good:** Trust boundary holds end-to-end — no raw bytes
  cross trust boundaries.
- ✅ **Good:** Anomaly detection becomes a structural property of
  the system, not a policy enforced at higher layers. A
  misclassification is observable.
- ⚖️ **Neutral:** Each host runs the full miner + classifier +
  rule engine stack — simple deployment, but every host needs
  LLM access (`claude -p` per [ADR-0012](0012-llm-backend.md)).
- ❌ **Bad:** The miner cannot deduplicate across hosts at
  extraction time. Pattern detection (the 2+/3+ thresholds)
  operates on each host independently. Cross-host pattern
  aggregation could be added later by aggregating fragment
  proposals in the base repo, but is not v1 scope.
- ❌ **Bad:** A pre-classification redaction step is required
  for the work-host paranoid mode (configurable patterns
  scrubbed from candidates before any classification or git
  write).

### Confirmation

- Mining code (`src/maury/mining/`, planned per Phase 6) reads
  only from `~/.claude/projects/` on the local host; there is
  no remote-fetch entry point.
- Quarantine path (per [ADR-0029](0029-maury-state-layout-contract.md))
  lives under `~/.claude/maury-state/quarantine/` and is
  gitignored at the maury-state layer.
- Anomaly detection rules live in `rules.yaml` as `forbid`
  rules (per [ADR-0004](0004-rule-engine-classification.md)).

<details>
<summary><b>Pros and cons of the options</b> (per-option ✅/❌ — click to expand)</summary>

#### Option A: Centralized mining

- ✅ **Good:** Cross-host pattern detection is trivial — the
  miner sees everything.
- ✅ **Good:** Single LLM credential to manage.
- ❌ **Bad:** Defeats the data-handling boundary; raw
  transcripts cross trust boundaries.
- ❌ **Bad:** Single point of compromise — if the central host
  is breached, every host's raw transcripts are exposed.

#### Option B: Centralized with redaction at extraction

- ✅ **Good:** In principle satisfies the boundary if the
  redaction is correct.
- ❌ **Bad:** Redaction would have to be **perfect** to satisfy
  the boundary, and we have no way to prove that.
- ❌ **Bad:** Raw transcripts still cross hosts in transit; a
  network observer or a flaw in the transport breaks the
  boundary.

#### Option C: Push raw transcripts to per-profile repos

- ✅ **Good:** Storage in git gives versioning and audit.
- ❌ **Bad:** Now the transcripts live in version-controlled
  storage forever — the worst possible blast radius.
- ❌ **Bad:** Same boundary violation as Option A, plus
  permanence.

#### Option D (chosen): Local-only mining

- ✅ **Good:** Trust boundary is structurally enforced — there
  is no code path that moves raw transcripts off-host.
- ✅ **Good:** Quarantine is observable; misclassification is
  visible in the review queue.
- ❌ **Bad:** Per-host LLM credential management; per-host
  pattern detection only.
- ❌ **Bad:** Redaction step adds complexity to the work-host
  configuration.

</details>

## Build-order placement

Phase 6 (Mining + extraction) — the mining pipeline ships with
Phase 6, runs purely against local
`~/.claude/projects/*.jsonl`. The quarantine path lives under
`~/.claude/maury-state/quarantine/` per
[ADR-0029](0029-maury-state-layout-contract.md).

## Followups

- **Cross-host pattern aggregation** — pattern detection
  (2+/3+ thresholds) currently runs per-host. A
  base-repo-side aggregator could roll up fragment proposals
  across hosts. Not v1 scope; revisit when multi-host pattern
  noise is observable.
- **Pre-classification redaction** for the work-host paranoid
  mode — configurable patterns scrubbed from candidates
  before any classification or git write. Concrete scrubber
  schema TBD.

## Claude Code references

Verified-as-of 2026-05-07 against Anthropic's official Claude
Code documentation:

- [`cc-jsonl-store`][cc-jsonl-store] — transcripts live at
  `~/.claude/projects/<project-hash>/<session-id>.jsonl`,
  one line per turn, append-only. Maury reads only this
  path; it is the documented mining source.

[cc-jsonl-store]: https://code.claude.com/docs/en/sessions
