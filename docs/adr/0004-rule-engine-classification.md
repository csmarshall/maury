# ADR-0004: Smart-but-strict classification — rules are the learned artifact

**Status:** Accepted
**Date:** 2026-05-06

## Related tenets

- [Tenet 5 — The user arbitrates ambiguity](../tenets.md#5-the-user-arbitrates-ambiguity)
- [Tenet 7 — Provenance is mandatory](../tenets.md#7-provenance-is-mandatory)

## Context

Mining transcripts for preferences is genuinely fuzzy work — well-suited
to an LLM. But **classification** (which profile a fragment belongs to)
crosses the trust boundary, and "trust the LLM with security routing" is
a non-starter. We need:

- Deterministic, repeatable decisions
- An audit trail that says *why* a fragment was placed where
- The ability to learn from corrections without giving the LLM
  authority over the boundary

A pure-LLM classifier fails on all three counts. A pure hand-written
ruleset works on all three but doesn't learn.

## Decision

Two stages with different trust models:

1. **Mining (AI-driven).** Extract candidate fragments from transcripts.
   Output: structured fragments with provenance.
2. **Classification (rule engine).** A deterministic engine matches each
   fragment against a hand-readable YAML ruleset and produces a
   `(profile, trace, confidence)` triple.

The ruleset is the **learned artifact**. When the user reclassifies a
fragment during review, the tool synthesizes a candidate rule from that
correction (an AI-assisted step, but with deterministic output: a YAML
rule) and asks for explicit approval before appending to `rules.yaml`.

Three rule shapes:

- **classify** — assign a profile when conditions match.
- **forbid** — block a profile assignment regardless of classify rules
  (the redaction guards). Symbolic targets supported: `*` (all),
  `!<name>` (all except), literal names.
- **scope** — narrow further to a host overlay (`rosa` → `home/hosts/rosa/`).

Every classification carries a trace listing which rules fired and why.
`maury rules trace "<text>"` lets you dry-run any text against
the ruleset.

## Consequences

- Rules file lives at `<base-repo>/.meta/rules.yaml` (in base because
  rules apply across profiles).
- No silent miscategorization — fragments with no matching rule go to a
  `manual` review queue, never to git.
- The user can read, hand-edit, version-control the entire learned
  state. No black box.
- Initial seed rules are hand-written from existing CLAUDE.md content
  (hostnames, identity terms, code-style keywords, redaction patterns).
- Rule synthesis is an AI step, but it cannot bypass user approval.

## Alternatives considered

- **Pure LLM classifier.** Rejected: opaque, non-deterministic,
  unsuitable for a security boundary.
- **Pure hand-written rules.** Rejected: doesn't learn; user has to
  hand-author every rule.
- **Embedding similarity.** Rejected for the same reason as LLM: opaque,
  hard to audit, hard to correct surgically.
