# ADR-0030: Manifest schema migrations

**Status:** Deferred (2026-05-19) — design preserved; implementation deferred until a v2 schema is genuinely needed
**Date:** 2026-05-07

> **Deferred (2026-05-19).** Maury hasn't released yet, so there
> are no users with pre-existing manifests to migrate from.
> The migration framework this ADR describes (per-version
> upgrade scripts, refuse-newer-than-supported, etc.) was
> originally written when v1→v2 was an active concern. With
> the v1→v2 cleanup of 2026-05-19 (everything currently shipping
> is `version: 1`), there is no second schema version to migrate
> to and no users to migrate. This ADR is preserved as the
> design we'll reach for when a real v2 introduction is on the
> table; the implementation lands then, not pre-emptively.
> Until that point, references to "schema migration" in other
> ADRs should be read as "future work" rather than as shipping
> machinery.

## Related tenets

- [Tenet 1 — First, do no harm](../tenets.md#1-first-do-no-harm)
- [Tenet 11 — Explicit beats implicit, with conservative defaults](../tenets.md#11-explicit-beats-implicit-with-conservative-defaults)

## TL;DR

Manifest schema versions are git-tracked and pulled by every host.
A silent auto-migration on one host would force the upgrade on all
peers as a fait accompli, breaking peers that haven't yet upgraded
their maury — exactly the cross-host coordination harm Tenet 1
forbids. Three rules: (1) maury **never silently auto-migrates**;
migration is always a user-invoked `maury manifest upgrade-vN-to-vN+1`
command. (2) Maury **refuses to operate on a manifest version newer
than it understands**, with a clear "upgrade maury or check out an
older commit" message. (3) Maury operates fine on older versions ≤
its max-supported, optionally suggesting the upgrade. Trade-off:
the user has to coordinate the upgrade across hosts deliberately;
nothing surprises a peer.

## Context

The manifest format has a `version: <int>` field. The current
shipping schema is **v2**, set by
[ADR-0015](0015-surrogate-keys-for-hosts-and-profiles.md) when
it bumped from the original v1 (name-keyed entries) to surrogate
keys (`host_<hex>`, `profile_<hex>`). The v1 → v2 upgrade
introduced a one-shot command:

```sh
maury manifest upgrade-v1-to-v2 --in <path>
```

ADR-0015 defined this command but did not generalize the
migration model: when v3 arrives, what happens? When a host
running maury-shipped-with-v2-support encounters a v3 manifest,
what does it do? When a host running maury-shipped-with-v3
encounters a v2 manifest, does it auto-upgrade or refuse?

The manifest is git-controlled. Multiple hosts pull the same
manifest. A silent migration on one host that pushes the
upgraded version creates a fait accompli for every other host
that hasn't upgraded their maury yet — which means
silently-upgraded hosts forcing an upgrade on all peers without
user consent.

That's exactly the kind of cross-host coordination problem
tenet 1 forbids. We need an explicit, single-direction model
that's safe across mixed-version host fleets.

## Decision

### Three rules

**1. Maury never silently auto-migrates a manifest.** Migration
is always an explicit user-invoked command. The manifest in git
stays at its current version until the user runs the upgrade.

**2. Maury refuses to operate on a manifest with a version
*newer* than it understands.** A maury binary that ships with
v2-and-earlier support, encountering a v3 manifest, exits with
a specific error: *"manifest is v3; this maury supports up to
v2. Upgrade maury (`pipx upgrade maury`) or check out an older
manifest commit."* No silent "best effort" parsing.

**3. Maury operates fine on manifest versions ≤ its own
maximum supported version.** A v3-supporting maury reads a v2
manifest as v2 — no auto-migration, no warnings beyond an
optional one-line "manifest is at v2; v3 is available, run
`maury manifest upgrade-v2-to-v3` to migrate" suggestion.

### The upgrade command pattern

Per-version upgrade scripts, one per N→N+1 step:

```sh
maury manifest upgrade-v1-to-v2 --in <path> [--out <path>]
maury manifest upgrade-v2-to-v3 --in <path> [--out <path>]
maury manifest upgrade-vN-to-vN+1 --in <path> [--out <path>]
```

Each script:

1. **Validates** the input is at the source version (refuses
   if the input is at a different version — including a newer
   one).
2. **Writes a backup** of the input file at `<input>.v<N>.bak`
   before modifying anything.
3. **Transforms** the document according to the v(N+1) schema.
4. **Validates** the output against the v(N+1) schema.
5. **Writes** the upgraded manifest to `<out>` (defaulting to
   in-place at `<input>`).
6. **Prints** a summary of changes (e.g., "renamed 2 host
   keys, added `repos` field to 3 entries, removed deprecated
   `legacy_id` fields from 7 rules").

For migrations across multiple versions (v1 → v3), the user
runs the scripts in sequence. Maury can ship a convenience
wrapper:

```sh
maury manifest upgrade --in <path> --to-version 3
```

…which internally chains the per-version scripts and prints
the cumulative summary. The chained form does NOT roll back on
partial failure: if v2→v3 fails after v1→v2 succeeded, the v1
backup is still present and the manifest is left at v2 (the
most recent successful upgrade). The user fixes the v3 issue
and re-runs the chained command; it picks up at the v2→v3
step and continues forward.

`--supported-versions` returns:

```json
{"min": 1, "max": 3, "current_default": 3}
```

Scriptable for cross-host capability checks ("are all my hosts
running maury that supports v3?").

### Multi-host coordination

When the user runs `maury manifest upgrade-v2-to-v3` on host
A and pushes the upgraded manifest, hosts B and C (still
running v2-only maury) will refuse to operate on the v3
manifest on next sync. They surface the error from rule 2.

This is **deliberate**. The user upgrades all their hosts'
maury installations *before* upgrading the manifest schema.
Recommended workflow:

1. `pipx upgrade maury` on every host.
2. Verify each host's maury supports the new version:
   `maury --version` and `maury manifest --supported-versions`.
3. On any one host: `maury manifest upgrade-v2-to-v3 --in
   .meta/maury-marker.json` then commit + push.
4. Other hosts pull and continue normally on next sync.

If the user pushes the upgrade before upgrading peers, peers
fail loud (per rule 2) until they're upgraded. No silent
divergence.

**Where the failure surfaces:** at the marker/manifest load
step in maury, NOT at `git pull`. Peers' `git pull` succeeds
(it's just fetching JSON); maury's reader refuses on next read.
Recovery is `pipx upgrade maury` followed by re-running the
maury command that failed. The peer's local clone has the v3
file but maury refuses to operate on it until the binary is
upgraded.

**Air-gapped hosts:** per
[ADR-0028](0028-offline-behavior.md) §"Air-gap pattern", the
host has no internet and so no `pipx upgrade maury`. Schema
upgrades for air-gapped hosts ride the same sneakernet channel
as everything else: a fresh maury wheel gets installed via the
sneakernet pipx channel before the new manifest schema is
pulled. Coordination is by the user's hand; nothing about the
upgrade flow needs network.

### What v3 (and beyond) cannot do

The migration mechanism doesn't constrain *what* schema
changes are possible, but it does impose a practical
constraint: **any change must be expressible as a v(N) → v(N+1)
transformation** that an upgrade script can perform.

In practice this means:

- **Renaming a field** is fine (rewrite the key).
- **Restructuring nested objects** is fine.
- **Adding new required fields with derived defaults** is fine
  (compute defaults during the upgrade).
- **Removing fields** is fine (drop them; backup preserves the
  removed data).
- **Changing semantics of a field without renaming** defeats
  the per-version-reader contract. A v2 reader looking at a v2
  manifest would see correct-by-name but incorrect-by-meaning
  data after a future v3 author "redefined" the field's
  semantics in `upgrade-v2-to-v3`. **Always rename when
  semantics change** — the version bump becomes visible at
  field-name granularity too, and per-version readers stay
  isolated.

### Backward-compat: maury reading old manifests

A maury that ships v3 support also ships its v2 reader.
"Supports up to v2" is a misnomer; it means "supports v1, v2,
and v3 — reads each at its own schema; only refuses v4 and
later."

Maury *practically* never removes support for old versions.
The v1 reader ships indefinitely. Old-manifest support is cheap
(the readers are small, well-tested, and only matter for legacy
paths). The alternative — refusing to read old manifests —
would force users to upgrade in lockstep with maury releases,
which we explicitly don't want.

If the maintenance burden of stale-schema readers ever exceeds
the value of supporting hosts that haven't synced in years, a
deprecation cycle could be introduced — but it would get its
own ADR with a multi-release deprecation window. No deprecation
is planned today.

### What the upgrade script doesn't do

It does NOT push the upgraded manifest to git. The user
explicitly commits and pushes — same workflow as any other
manifest edit. The upgrade is a local file transformation;
distribution is a separate, deliberate step.

It does NOT touch other state files (`~/.local/state/maury/`
contents per ADR-0029, the synced repo's other content). Only
`.meta/maury-marker.json` is in scope.

It does NOT auto-bump dependent files (e.g., a v3 schema that
also requires changes to `rules.yaml` would need a separate
`maury rules upgrade-vN-to-vN+1` command). Each schemaed file
gets its own upgrade pipeline.

> Note: `rules.yaml` is **not currently versioned** — there's
> no `version:` field in
> [ADR-0004](0004-rule-engine-classification.md)'s schema. If
> and when it gains a schema version, the same per-version-
> upgrade pattern from this ADR applies. Until then, the
> single `maury rules trace` reader handles all `rules.yaml`
> content as one schema.

## Consequences

- **Cross-host coordination is explicit.** A new manifest
  version doesn't force-upgrade peers; peers refuse to operate
  until their maury is upgraded. The user controls the
  rollout.
- **Migration is auditable.** Each upgrade writes a backup;
  the upgrade script's summary is a record of what changed.
  When something breaks, the backup is the recovery path.
- **Old-manifest support is forever.** Maury accumulates
  readers; never removes them. Cost: small (each reader is a
  bounded amount of code); benefit: large (no forced
  upgrades).
- **Mixed-version host fleets are safe.** A host left behind
  for months on an old maury will read the old manifest just
  fine until someone upgrades the manifest schema; at that
  point it fails loud (rule 2) until upgraded.
- **The user owns the rollout decision.** When to upgrade
  hosts, when to upgrade the manifest, what order to do them
  in — all explicit.
- **Implementation cost is per-version-bump.** Each schema
  change ships with: an upgrade script, a v(N+1) reader
  (replacing the v(N) reader as default for new manifests),
  and tests for the upgrade and the new reader. Bounded.

## Alternatives considered

- **Auto-migrate on first encounter.** Rejected: silently
  upgrades the manifest in git, forcing peers to upgrade
  without consent. Tenet 1 violation.
- **Maury refuses any manifest != its current schema version.**
  Rejected: forces lockstep upgrades across hosts, which
  defeats independent-pace updates.
- **Maintain a single "smart reader" that handles all
  versions.** Considered. Rejected because the smart reader
  becomes increasingly complex with every version, and bugs
  in the version-detection logic become silent corruption
  bugs. Per-version readers are simpler and isolatable.
- **Embed migration logic in the manifest itself** (e.g., a
  `_migrations:` field with transformation hints).
  Rejected: makes the manifest a meta-document about itself;
  hard to reason about; the upgrade scripts (named
  `upgrade-vN-to-vN+1`) are a clearer contract.
- **Use a schema-registry external service** (e.g., a JSON
  Schema repository hosted by maury). Rejected: introduces a
  network dependency at manifest-load time; conflicts with
  ADR-0028's offline-friendly default-local model.

## Build-order placement

- **The `maury manifest upgrade-v1-to-v2` command** was
  promised in [ADR-0015](0015-surrogate-keys-for-hosts-and-profiles.md)
  but is **not yet implemented** (per `docs/status.md` —
  marked ⏳ planned, not stubbed). It's a follow-up
  implementation task; not on Phase 2's critical path
  (Phase 2 is shipped). Lands as a small slice whenever the
  v1→v2 migration becomes user-relevant (no v1 hosts exist
  yet in dogfooding).
- **The version-refuse mechanism** (rule 2) lands wherever the
  manifest reader currently lives (`src/maury/manifest.py`).
  Small addition.
- **The `--supported-versions` informational flag** is a
  small addition to the manifest CLI group.
- **Future migration scripts** ride on each version-bump ADR
  and ship as part of that ADR's implementation slice.

## Followups

- **Integration with ADR-0024 manifest concurrency.** When
  two hosts try to upgrade the manifest concurrently:
  - **Sequential case (one finishes pushing before the other
    pulls):** the second host runs `git pull` and gets the
    already-upgraded manifest; their local upgrade attempt
    becomes a no-op (input is already at v3 → upgrade refuses
    to run, prints "already at v3").
  - **Concurrent upgrade-then-push case (both upgrade locally
    before either pulls):** both end up at v3 locally; the
    push race resolves via standard git rejection. The second
    host pulls and triggers a v3-vs-v3 manifest merge per
    [ADR-0024](0024-manifest-concurrency-inclusive-merge.md) —
    not a schema-version conflict. The structured merge
    handles per-field divergence in any derived upgrade
    values (e.g., a v3-introduced `repos[].last_synced_at`
    derived from local state would have different values on
    each host; the merge resolver surfaces this as an
    overwrite-conflict for the user to choose). Worth a
    cross-link from ADR-0024.
- **Schema documentation per version.** Each version's schema
  should be documented somewhere navigable
  (e.g., `docs/manifest-schema/v1.md`, `v2.md`, ...). Helps
  contributors writing migration scripts and helps users
  understand what changed. v1.1 doc work.
- **`maury manifest diff-versions <from> <to>`** — a helper
  that shows what changes between two schema versions
  conceptually (not a file diff; a schema diff). Useful when
  preparing to write a migration script. v1.1.

## Claude Code references

This ADR introduces no new Claude Code dependencies. Manifest
schema is maury-internal; Claude Code is unaware of it.

## Amendment history

- 2026-05-19 — status moved to **Deferred**. Pre-release v1→v2 migration tooling was deleted (no users exist to migrate from). Design preserved; implementation lands when a real v2 schema is introduced.
