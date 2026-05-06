# ADR-0005: Local-only mining — never cross-host transcript pull

**Status:** Accepted
**Date:** 2026-05-06

## Related tenets

- [Tenet 4 — Sensitive data stays local](../tenets.md#4-sensitive-data-stays-local)

## Context

A natural design would centralize mining: SSH to each host, pull all the
JSONL transcripts to a single workstation, run extraction once, push
proposals back. This is operationally simple and gives the miner a
unified view across hosts.

It also defeats the entire trust-boundary architecture. The work
laptop's transcripts may contain employer-confidential content; pulling
them to a personal host would create exactly the data flow we built the
repo-per-trust-boundary structure to prevent.

## Decision

Each host mines its own `~/.claude/projects/*.jsonl` transcripts locally.
Raw transcripts never leave the host. Cross-host coordination happens via
git only — the only thing pushed to the repo is sanitized, classified
fragments.

Cross-profile content emerging on the wrong host (e.g., a transcript on
the work laptop that mentions `linux-server` or `192.168.1.x`) is treated as an
anomaly: the fragment is **quarantined locally**, never written to git,
and surfaced in the next review with a clear "anomaly: home content from
work host" tag for the user to investigate.

## Consequences

- Each host runs the full miner + classifier + rule engine stack —
  ~simple deployment, but every host needs Anthropic SDK credentials.
- The miner cannot deduplicate across hosts at extraction time. Pattern
  detection (the 2+/3+ thresholds) operates on each host independently.
  Cross-host pattern aggregation could be added later by aggregating
  fragment proposals in the base repo, but is not v1 scope.
- Anomaly detection becomes a structural property of the system, not a
  policy enforced at higher layers. A misclassification is observable.
- A pre-classification redaction step is required for the work-host
  paranoid mode (configurable patterns scrubbed from candidates before
  any classification or git write).

## Alternatives considered

- **Centralized mining.** Rejected: defeats data-handling boundary.
- **Centralized mining with redaction at extraction.** Rejected: the
  redaction would have to be perfect to satisfy the boundary, and the
  raw transcripts would still cross hosts in the meantime.
- **Push raw transcripts to per-profile repos.** Rejected: same problem,
  and now the transcripts live in version-controlled storage forever.
