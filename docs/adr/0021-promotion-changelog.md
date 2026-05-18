# ADR-0021: Promotion changelog for rule lineage

**Status:** Superseded by [ADR-0022](0022-branch-per-mining-run.md)
**Date:** 2026-05-06
**Superseded:** 2026-05-06

## Why superseded

This ADR proposed a parallel `.meta/changelog.jsonl` data structure
to record rule provenance. ADR-0022 instead uses git's own commit log
as the changelog — the proposal IS a commit, the rationale IS the
commit message body, and `maury why <rule>` becomes
`git log --follow --grep=...`. No sidecar file is needed.

The forward-link to ADR-0015's "rule IDs adopt `rule_<hex>`" addendum
also goes away, because findings are identified by their commit SHA
on the run branch — no surrogate ID is minted.

The original Decision/Consequences below is preserved as a breadcrumb
of the design pivot; do not implement.

---

## Related tenets

- [Tenet 1 — First, do no harm](../tenets.md#1-first-do-no-harm)
- [Tenet 7 — Provenance is mandatory](../tenets.md#7-provenance-is-mandatory)
- [Tenet 8 — Hand-edits are first-class input](../tenets.md#8-hand-edits-are-first-class-input)

> **Status note:** the sections below are preserved from the
> original (pre-supersession) ADR as a breadcrumb of the design
> pivot. They use the older Nygard-style section names
> (Context / Decision / Consequences / Alternatives considered)
> rather than the MADR/Nygard hybrid format the rest of the ADR
> set uses; this is intentional. Do not implement; see
> [ADR-0022](0022-branch-per-mining-run.md).

## Context

Today, when a rule is promoted from a profile-scoped repo into base
(per [ADR-0045](0045-cross-trust-boundary-promotion.md)), the only
record of *why* is whatever the curator typed at `git commit` time.
Once the proposal file is deleted post-promotion, the rule sits in
`CLAUDE.md` or `rules.yaml` with no link back to the conversation
excerpt that triggered it, the cross-reference state, or the curator's
reasoning. Re-deriving that intent from `git log -p` is archaeology —
and breaks entirely when the rule moves files or gets reworded.

The mining loop already produces all the metadata we need: the
`Finding.evidence` quote, `confidence`, `kind`, `scope_hint`, source
transcript timestamps, the
[ADR-0020](0020-two-mining-modes-bulk-and-incremental.md) four-state
classification (`NEW`, `PRESENT_AND_CLEAR`, `PRESENT_BUT_UNCLEAR`,
`PRESENT_AND_REINFORCED`), and the curator's accept/reject decision.
Currently we throw all of it away at promotion time.

## Decision

Add `.meta/changelog.jsonl` (one per repo, append-only) where every
promotion event writes a structured entry capturing:

- `ts` — ISO-8601 timestamp.
- `rule_id` — `rule_<32 hex>` surrogate per
  [ADR-0015 addendum (retracted)](0015-surrogate-keys-for-hosts-and-profiles.md#addendum-rule-ids--retracted-2026-05-06).
  Rules adopt the prefixed format; legacy slug IDs in v1 seed rules
  are migrated automatically.
- `action` — `added` | `modified` | `removed`. Intentionally narrower
  than the audit log's reconcile-action vocabulary
  ([ADR-0017](0017-drift-detection-and-reconciliation.md): `adopt` |
  `adapt` | `revert` | etc.) — this changelog tracks rule lifecycle
  only, not user-driven reconcile decisions.
- `promoted_from` — `{source_repo, proposal_id, finding_kind,
  scope_hint, crossref_state, confidence}`. `crossref_state` is the
  `state` field from `CrossRefResult` (one of the four ADR-0020
  states); `proposal_id` is a surrogate minted when the proposal is
  queued (concrete format TBD in Phase 7).
- `evidence_excerpt` — the **redacted** finding text. This already
  passed the rule engine's `forbid` filter per
  [ADR-0004](0004-rule-engine-classification.md), so it is safe to
  live in the destination repo even when the destination is a
  higher-trust boundary than the source.
- `curator_host_id` — `host_<hex>` of the host that approved.
- `curator_note` — optional free-form text the curator typed at
  `maury promote-review` time.

Add a `maury why <rule-id-or-grep>` command that reads
`changelog.jsonl` and prints the rationale, evidence, cross-reference
state, and curator note for matching rules.

## Consequences

- **Auditable across repos.** Cross-boundary promotion (work → base
  per [ADR-0045](0045-cross-trust-boundary-promotion.md)) leaves a
  record on both sides — the work repo's audit log records "exported
  as proposal X", the base repo's changelog records "accepted from
  work repo as rule Y". A base rule can be traced back to the
  work-laptop transcript window without the base repo ever having
  seen the raw transcript.
- **Survives rule rewording.** A `PRESENT_BUT_UNCLEAR` rephrase emits
  a `modified` entry pointing back to the original `added` entry by
  `rule_id`. Full lineage even when the surface text changes.
- **Trust-boundary safe.** `evidence_excerpt` is the already-redacted
  finding text, not the raw transcript. Same redaction guarantee that
  governs proposals.
- **Travels with the synced rule.** Because the changelog lives in the
  same repo as the rule, every host that pulls the repo gets the
  provenance for free. No separate sync channel.
- **Overlaps with the audit log but doesn't replace it.** The audit
  log per [ADR-0017](0017-drift-detection-and-reconciliation.md) is
  per-host and records every state-changing op; the changelog is
  per-repo and records only rule lineage. Audit log is operational
  ("what did this host do, when"); changelog is content provenance
  ("why does this rule exist"). They overlap but neither is
  redundant.
- **`maury why` becomes the answer to "why is this in my CLAUDE.md".**
  The single most-asked question of any synced-config tool.

## Alternatives considered

- **Free-form git commit messages (status quo).** Rejected: not
  structured, hard to query, breaks if the rule moves files, and the
  curator's care about message quality varies day-to-day. `git log -p`
  is not a provenance system.
- **Embed rationale as comments in `rules.yaml` / `CLAUDE.md`.**
  Rejected: doesn't survive rewording or splitting a rule across
  files; pollutes the user-facing rendered file with maury metadata
  the user didn't write; doesn't capture evidence quotes or curator
  host without bloating the file further.
- **Store the original proposal file forever instead of deleting it
  post-promotion.** Rejected: bloats the repo with files that have no
  query structure, and "the proposal file" is what the curator sees
  *pre*-decision. We want the *post*-decision record — the curator's
  note and final accepted form, not the candidate text.

## Amendment history

- 2026-05-11 — "profile" vocabulary renamed to "mode" per ADR-0037 doctoral examination. References to "profile" in this ADR now read "mode"; no semantic changes.
