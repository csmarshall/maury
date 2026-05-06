# ADR-0001: N profiles, not a hardcoded {home, work} pair

**Status:** Accepted
**Date:** 2026-05-06

## Context

Initial sketches assumed exactly two contexts: "home" and "work." Real
usage will likely add client engagements, research projects, and
side-contract contexts over time. Hardcoding the profile set into schema,
rules, and CLI commands creates a refactor every time the user adds one.

## Decision

Profiles are user-defined namespaces registered in the manifest. The
engine never hardcodes any profile name. A host has one active profile at
a time and may declare `lock: true` to prevent profile switching via
`maury profile use`. Profiles support optional single-parent
inheritance via `extends` so a `consulting` profile can carry shared
defaults that `acme-client` and `globex-client` inherit.

## Consequences

- Manifest is the source of truth for which profiles exist; rules engine
  validates rule targets against this set on every load.
- Profile lifecycle commands required: `profile new`, `rename`, `rm`, `use`.
- Renaming is non-trivial because rules reference profile names by string;
  `profile rename` must update the rule file in lockstep with the manifest.
- Inheritance walker is a render-time concern; flat profiles render with
  `extends: null` and the same code path.

## Alternatives considered

- **Hardcoded {home, work}.** Rejected: refactor cost grows with each new
  context.
- **Tag-based profiles** (a host has a set of tags rather than one
  profile). Rejected: makes "what's active right now" ambiguous and
  complicates the trust model.
