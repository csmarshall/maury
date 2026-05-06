# Maury — core tenets

These are the principles maury is built on. ADRs decide *what to do* in
specific situations; tenets decide *what kind of tool maury is*. Every
ADR should be checkable against these — if a decision violates a tenet,
either the decision is wrong or the tenet needs an explicit revision.

> **Why this document exists.** It's easy for a project to accrete
> ADRs that individually make sense but collectively pull in different
> directions. The tenets are a constitution: they make the underlying
> values explicit so future decisions stay coherent.

---

## 1. First, do no harm

The operating principle for everything below. Never silently overwrite,
lose, or expose user data. Every state-changing operation is auditable,
reversible where possible, and pauses for input when there's ambiguity.
A tool that helps you raise the boats can't be allowed to sink any of
them.

*Embodied by: drift-detect-before-clobber (Q10), three-way merge with
user arbitration (Q11), Claude-writes logged with revert path (Q8),
no auto-promotion across trust boundaries ([ADR-0009](adr/0009-promotion-only-cross-boundary.md)).*

## 2. Consistency within a profile; controlled difference across profiles

The reason maury exists. Within a profile, Claude's behavior,
configuration, capabilities, and skills should be **identical** across
every host you use — toad and rosa and the laptop should give you the
same Claude. Across profiles, differences must be **explicit,
intentional, and traceable** — not accidents of which machine you
happened to be sitting in front of.

Drift between hosts within a profile is a bug. Differences between
profiles are a feature.

*Embodied by: the entire project.*

## 3. Trust boundaries are physical, not policy

Server-side access controls — per-repo deploy keys, GitHub permissions,
per-backend auth — are the actual enforcement. Client-side rules,
allow-lists, and policies are defense-in-depth, never the boundary
itself. A boundary is real only when violating it requires bytes that
the offending host literally does not possess.

*Embodied by: [ADR-0002](adr/0002-repo-per-trust-boundary.md) (repo per trust boundary), [ADR-0003](adr/0003-per-host-deploy-keys.md) (per-host
deploy keys), [ADR-0009](adr/0009-promotion-only-cross-boundary.md) (promotion-only cross-boundary), [ADR-0014](adr/0014-host-local-secrets-with-metadata-sync.md)
(defense-in-depth secrets).*

## 4. Sensitive data stays local

Raw transcripts, secret values, and any data not explicitly classified
for sync stays on the host that produced it. Cross-host coordination
happens via sanitized, classified fragments only. The transport never
carries content the user hasn't seen and approved.

*Embodied by: [ADR-0005](adr/0005-local-only-mining.md) (local-only mining), [ADR-0014](adr/0014-host-local-secrets-with-metadata-sync.md) (secret values
host-local; metadata syncs), anomaly-quarantine for cross-profile
content emerging on the wrong host.*

## 5. The user arbitrates ambiguity

No silent precedence rules. No auto-resolved merge conflicts. No
opaque LLM judgments on routing-sensitive decisions. Default behavior
is "show me, ask me." Automation is opt-in via explicit flags
(`--non-interactive`, `--force`).

*Embodied by: [ADR-0004](adr/0004-rule-engine-classification.md) (classification is a deterministic rule engine,
not LLM judgment), Q11 (three-way merge with arbitration), Q10
(detect-first sync), the proposal review queue.*

## 6. Identity is not name

Entities have stable opaque IDs; names are mutable display labels.
Renames must not break references. Surrogate keys for entities that
maury creates (hosts, profiles, services, credentials, fragments,
captures, audit entries); natural keys only for things whose identity
is genuinely external (URLs, file paths, OS tool names).

*Embodied by: [ADR-0015](adr/0015-surrogate-keys-for-hosts-and-profiles.md) (surrogate keys for hosts and profiles); to be
applied to [ADR-0014](adr/0014-host-local-secrets-with-metadata-sync.md) services/credentials and to the not-yet-built
mining/audit modules.*

## 7. Provenance is mandatory

Every rule, fragment, capture, and audit entry records who, when, and
why. The ruleset and audit log together are the institutional memory.
Anything that lands in synced config can be traced back to its origin.

*Embodied by: [ADR-0004](adr/0004-rule-engine-classification.md) (rules carry `added` and `reason`), [ADR-0008](adr/0008-claude-diary-reference.md)
(synthesizer records originating fragment), [ADR-0009](adr/0009-promotion-only-cross-boundary.md) (audit on both
sides of promotion), Q8 (Claude-write provenance).*

## 8. Hand-edits are first-class input

No DRM. No read-only files. The user can always reach in and change
anything. Drift detection brings hand-edits through the same review
pipeline as mined fragments and active captures, with the same
provenance and audit treatment. Tooling reduces friction; it doesn't
gate.

*Embodied by: [ADR-0017](adr/0017-drift-detection-and-reconciliation.md) (drift detection and reconciliation), Q6
(prompt-to-claim for user-added files), Q7 (same path for rendered vs
source-tree edits).*

## 9. Defer to the platform

Use existing capabilities — Claude Code's authentication, OS keychains,
native package managers, the official best-practices doc — rather than
inventing parallels. Be a good citizen of the surrounding ecosystem.
Carry weight only where there's a real gap nothing else fills.

*Embodied by: [ADR-0007](adr/0007-python-with-uv.md) (Python via uv on each OS's native package
manager), [ADR-0011](adr/0011-anthropic-rubric-integration.md) (consume Anthropic's rubric, don't invent one),
[ADR-0012](adr/0012-llm-backend.md) (claude -p default — user's existing subscription quota),
[ADR-0014](adr/0014-host-local-secrets-with-metadata-sync.md) (OS keychain backends per platform).*

## 10. Modularity over hardcoding

N profiles, not two. Pluggable transports, not just GitHub. Pluggable
secret stores, not just age-files. Capability-aware hooks, not
OS-specific shell. Don't bake in assumptions you'll later regret —
especially not in schemas or directory structures, which are the
hardest to retrofit.

*Embodied by: [ADR-0001](adr/0001-n-profiles.md) (N profiles), [ADR-0006](adr/0006-capability-probe-hook-abstraction.md) (capability probe with
action-form hooks), [ADR-0014](adr/0014-host-local-secrets-with-metadata-sync.md) (pluggable secret backends), [ADR-0016](adr/0016-pluggable-repo-backends.md)
(pluggable repo backends).*

## 11. Explicit beats implicit, with conservative defaults

The manifest is explicit; nothing important is inferred. Default
behaviors are conservative — `push_policy: own_profile_only` not
`permissive`, `mode: ro` not `rw`, `cli` LLM backend not `sdk`. Escape
hatches exist (`--force`, `--non-interactive`) but the user must ask
for them.

*Embodied by: default push policy, default repo mode, default LLM
backend, default sync flow, the entire flag-escape pattern.*

---

## How to use these

- **When proposing a new ADR:** check it against each tenet. If you
  find yourself rationalizing a violation, either the ADR is wrong or
  the tenet needs an explicit revision (which itself becomes an ADR —
  "ADR-NNNN: revise tenet X because Y"). Never silently drift away
  from a tenet.
- **When reviewing a PR:** ask whether the change brings any tenet
  into tension, especially #1 (no harm), #2 (consistency), and #5
  (user arbitrates).
- **When debating a design choice:** if a debate stalls, name the
  tenets each side is appealing to. Often the disagreement is about
  which tenet should dominate, not about the surface question.

## Provenance

Drafted in the conversation that produced [ADR-0001](adr/0001-n-profiles.md) through [ADR-0017](adr/0017-drift-detection-and-reconciliation.md),
distilled from the implicit values that emerged across decisions.
Each tenet was independently visible in 2+ ADRs before being named.
This document doesn't add new principles — it makes existing ones
explicit so future decisions can reference them.
