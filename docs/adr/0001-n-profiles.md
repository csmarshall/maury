# ADR-0001: N profiles, not a hardcoded {home, work} pair

**Status:** Accepted
**Date:** 2026-05-06
**Amended:**
  - 2026-05-11 — "profile" renamed to "mode" throughout maury's vocabulary per ADR-0037 doctoral examination. The concept this ADR establishes (N user-defined modes, not hardcoded pairs; single-parent tree) is unchanged.

## Related tenets

- [Tenet 10 — Modularity over hardcoding](../tenets.md#10-modularity-over-hardcoding)

## TL;DR

Hardcoding `{home, work}` as the profile set forces a refactor every
time the user adds a client engagement, research project, or other
context. Maury instead supports **N user-defined profiles** registered
in the manifest, with optional single-parent `extends` inheritance. A
host is in exactly one profile at a time so trust-model questions
("what context is active?") stay unambiguous. Trade-off: profile names
are referenced as strings, so renames carry cross-file updates —
mitigated by ADR-0015's surrogate keys.

## Context and Problem Statement

Initial sketches assumed exactly two contexts: "home" and "work." Real
usage will likely add client engagements, research projects, and
side-contract contexts over time. Hardcoding the profile set into schema,
rules, and CLI commands creates a refactor every time the user adds one.

<details>
<summary><b>Decision drivers</b> (3 items — click to expand)</summary>

- **Tenet 10:** modularity over hardcoding — the schema shouldn't pin
  the user's vocabulary.
- **Real-world growth:** users add client/research/side-contract contexts
  over time; refactor cost grows with each new one if hardcoded.
- **Trust-model coherence:** "what context is active right now?" must be
  unambiguous for the cross-host promotion + access-control model.

</details>

<details>
<summary><b>Considered options</b> (3 options — click to expand)</summary>

- **Option A:** Hardcoded `{home, work}` pair.
- **Option B (chosen):** N user-defined profiles registered in the manifest.
- **Option C:** Tag-based profiles (a host carries a set of tags rather than one named profile).

</details>

## Decision Outcome

**Chosen option:** Option B — N user-defined profiles. The manifest is
the source of truth for which profiles exist; the engine never
hardcodes any profile name. This is the only option that keeps
modularity (Tenet 10) without making "what context am I in?" ambiguous.

### Implementation details

A host has one active profile at a time and may declare `lock: true`
to prevent profile switching via `maury profile use`. Profiles support
optional single-parent inheritance via `extends` so a `consulting`
profile can carry shared defaults that `acme-client` and
`globex-client` inherit.

### Consequences

- ✅ **Good:** Manifest is the single source of truth for which profiles
  exist; rules engine validates rule targets against this set on every load.
- ✅ **Good:** Adding a new context is a one-command operation
  (`maury profile new <name>`), not a refactor.
- ⚖️ **Neutral:** Profile lifecycle commands required:
  `profile new`, `rename`, `rm`, `use`.
- ❌ **Bad:** Renaming is non-trivial because rules reference profile
  names by string; `profile rename` must update the rule file in
  lockstep with the manifest. (Mitigated by [ADR-0015](0015-surrogate-keys-for-hosts-and-profiles.md)'s
  surrogate keys.)
- ⚖️ **Neutral:** Inheritance walker is a render-time concern; flat
  profiles render with `extends: null` and the same code path.

### Confirmation

- The manifest validator (`src/maury/manifest.py`) refuses
  unknown profile references; unit tests in
  `tests/unit/test_manifest.py` cover N-profile chains
  (validated against `docs/status.md`).
- `maury profile new / rename / rm / use` lifecycle commands
  ship with Phase 4 (Bootstrap commands); their status is
  tracked in `docs/status.md` rather than aspirated here.

<details>
<summary><b>Pros and cons of the options</b> (per-option ✅/❌ — click to expand)</summary>

#### Option A: Hardcoded {home, work}

- ✅ **Good:** Simplest possible schema; no profile validation needed.
- ❌ **Bad:** Refactor cost grows with each new context the user wants
  (client engagements, research, side contracts).
- ❌ **Bad:** Defeats Tenet 10 (modularity over hardcoding).

#### Option B (chosen): N user-defined profiles

- ✅ **Good:** Profile vocabulary is the user's, not maury's.
- ✅ **Good:** Single active profile per host means "where am I right
  now?" is unambiguous for trust-model purposes.
- ✅ **Good:** `extends` inheritance lets shared content propagate
  without copy-paste.
- ❌ **Bad:** Manifest schema gains a profiles registry; CLI gains
  lifecycle commands.
- ❌ **Bad:** Renames carry cross-file updates (mitigated by
  [ADR-0015](0015-surrogate-keys-for-hosts-and-profiles.md)).

#### Option C: Tag-based profiles

- ✅ **Good:** Maximum flexibility — a host can be "research + client-A"
  simultaneously.
- ❌ **Bad:** Makes "what's active right now?" ambiguous; trust-model
  decisions need a single context to evaluate.
- ❌ **Bad:** Cross-host promotion (per
  [ADR-0045](0045-cross-trust-boundary-promotion.md)) becomes much
  harder to specify when the active context is a set, not a singleton.

</details>

## Build-order placement

Phase 2 (Manifest + capability probe) — the manifest schema
that registers profiles is the load-bearing artifact for this
ADR. Phase 4 (Bootstrap commands) ships the
`maury profile new / rename / rm / use` lifecycle commands.

## Followups

- **Tag-based composition** (Option C) could land later as an
  *additional* dimension on top of named profiles (e.g., a host's
  active profile remains a singleton, but profiles can carry
  optional tags consumed by rules). Not v1 scope; revisit if a
  real use case appears.

## Amendment history

- 2026-05-11 — "profile" renamed to "mode" per ADR-0037. The N-modes concept (arbitrary count, single-parent tree, one active mode per host) is unchanged; only the vocabulary was updated. `profile_<hex>` surrogate IDs renamed to `mode_<hex>` per ADR-0015 amendment of the same date.
