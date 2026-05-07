# ADR-0015: Surrogate keys for hosts and profiles

**Status:** Accepted
**Date:** 2026-05-06
**Amended:**
- 2026-05-06 — added rule-IDs addendum proposing `rule_<32 hex>`
  prefix for rules; subsequently RETRACTED on the same day after
  ADR-0022 made it unnecessary (commit log preserves the trail).

## Related tenets

- [Tenet 6 — Identity is not name](../tenets.md#6-identity-is-not-name)

## TL;DR

The original v1 manifest used human-readable names as primary keys for
hosts and profiles — a textbook violation of the surrogate-vs-natural-
key principle (Codd / DDD Entity Identity). Renaming a host or profile
would have broken every reference: directory paths, audit log entries,
rule targets, fragment provenance. Maury adds **`host_<32 hex>` and
`profile_<32 hex>` surrogate IDs** for both entity types, with names
demoted to mutable display labels. Directory paths still use names for
navigability; renames are atomic operations that update manifest +
`git mv` together. Trade-off: pre-commit hook needed to keep
directory name in sync with the embedded `name` field.

## Context and Problem Statement

The v1 manifest schema used the human-readable name as the dict key for
both hosts and profiles:

```json
"hosts":    { "workstation": {"profile": "home", ...}, ... },
"profiles": { "work": {"extends": null, ...}, ... }
```

This is a textbook violation of the **surrogate-key vs natural-key**
distinction (Codd) and the DDD **Entity Identity** principle: an
entity's identity should persist across attribute changes, including
its name. Real consequences of getting this wrong:

- Renaming `"workstation"` → `"froggie"` breaks: every manifest reference,
  the host overlay path `profiles/home/hosts/workstation/`, every audit log
  entry, any future cross-host reference in fragments
  ("running on workstation").
- Renaming `"work"` → `"old-amazon"` breaks: every rule whose target
  profile is `"work"`, every host's `profile` field, the directory
  `profiles/work/`, inheritance chain references in
  `extends: "work"`, fragment provenance recording the originating
  profile.

Same pattern shows up everywhere in well-designed systems:

| System | Stable identifier | Mutable label |
|---|---|---|
| AWS | ARN | `Name` tag |
| Kubernetes | `metadata.uid` | `metadata.name` |
| Active Directory | `objectGUID` | `sAMAccountName` |
| Cognito | `sub` | `username` |
| GitHub API | numeric `id` | `name` (renaming preserves `id`) |
| Web architecture | URI / URN | display title ("Cool URIs don't change") |

The principle: **never use a human-readable label as a primary identifier
in your data layer.**

<details>
<summary><b>Decision drivers</b> (4 items — click to expand)</summary>

- **Tenet 6:** identity is not name. Renaming a host or
  profile must not break references — names are mutable
  labels, identities are not.
- **Industry convention:** every well-designed identity
  system in production today separates surrogate from
  natural keys. Diverging is unsupportable.
- **Catch the cost early:** a surrogate-key retrofit before
  the render engine bakes in the name-as-key path is cheap;
  after is expensive.
- **Preserve directory navigability:** users want to
  `cd profiles/home/hosts/workstation`, not
  `cd profiles/profile_3f1a.../hosts/host_8a7f.../`. The
  schema choice should not force opaque directory layouts.

</details>

<details>
<summary><b>Considered options</b> (5 options — click to expand)</summary>

- **Option A:** Keep names as keys; accept the rename pain.
- **Option B (chosen):** Surrogate keys for hosts and
  profiles; names stay in directory paths for navigability.
- **Option C:** Surrogate keys AND ID-only directories.
- **Option D:** ULID instead of UUID4 for surrogate IDs.
- **Option E:** GUIDs without entity-type prefix.

</details>

## Decision Outcome

**Chosen option:** Option B — add surrogate keys to **hosts**
and **profiles**. The `name` field becomes a mutable display
label; internal references use the ID. Directory paths
continue to use names (paired with manifest update on
rename) so navigability is preserved.

### Implementation details

#### ID format

Stable, opaque, time-sortable string with an entity-type prefix:

```
host_<32 hex chars>      e.g., host_8a7f3c1d4e9b4a2c8f1e7d5b6c2a9e4f
profile_<32 hex chars>   e.g., profile_3f1a8b2c4d5e6f7081a2b3c4d5e6f708
```

Implementation: `uuid4().hex` with the prefix prepended. Stdlib only,
no dependency. The prefix (`host_`, `profile_`) makes IDs
self-describing in audit logs and error messages.

#### Manifest schema (revised)

```json
{
  "version": 2,
  "profiles": {
    "profile_3f1a...": { "name": "home", "extends": null, "description": "..." },
    "profile_4a2b...": { "name": "work", "extends": null, "description": "..." }
  },
  "hosts": {
    "host_8a7f...": { "name": "workstation",        "profile": "profile_3f1a...", ... },
    "host_9b8c...": { "name": "work-laptop", "profile": "profile_4a2b...", ... }
  }
}
```

Note that `host.profile` now references the **profile ID**, not the
profile name. Same for `extends:` in inheritance chains.

#### Repo directory layout

**Names stay in directory paths** for human-friendliness and
git-archaeology friendliness, but the manifest is the source of truth
for the (id, name) mapping. Renaming is an atomic `maury` operation
that updates the manifest AND `git mv`'s the directory.

```
profiles/
├── home/                  <-- directory uses name
│   ├── profile.yaml       <-- contains: id, name
│   └── hosts/
│       └── workstation/          <-- directory uses name
│           ├── host.yaml  <-- contains: id, name
│           └── ...
```

This trades a tiny amount of identity-purity (the directory name
mirrors the mutable `name` field) for a substantial usability win
(humans can `cd profiles/home/hosts/workstation` without having to look up
IDs). The contract is: directory rename is always paired with manifest
update via `maury <entity> rename`. The pre-commit hook checks that
every `<entity>.yaml`'s embedded `name` matches the directory name it
lives in.

#### Host self-identification

Each host writes its ID once at first bootstrap to
`~/.maury-host-id`. The file is created by `maury bootstrap host` and
**never modified**. `socket.gethostname()` becomes a hint for setting
the *initial* `name` field only — never the source of truth for which
manifest entry applies to this machine.

If `~/.maury-host-id` is missing (e.g., fresh install before
bootstrap), maury commands that need to know "which host am I"
short-circuit to a warning and refuse to push.

#### Internal references use IDs

- Rules can reference profiles in `then.profile` and `then.forbid_profile`
  by **either** name or ID. Names are resolved through the manifest
  at engine load. (Backward-compat for hand-written rules; the
  synthesizer always emits IDs.)
- Audit log records both ID and name on every entry: `{id, name_at_event_time}`.
- Fragment provenance records originating host_id + profile_id.
- Cross-host fragments referencing other hosts use IDs (e.g.,
  "running on host_8a7f..." rather than "running on workstation").

#### What does NOT change

- **Repos** are identified by URL in the manifest. The `repos` dict on
  each host uses local nicknames as keys (`"base"`, `"personal"`,
  `"work"`); these are display labels for URLs, never referenced
  cross-host. URL IS the surrogate key.
- **Rules** already have stable IDs (the `id:` field). No change in
  v1; see [addendum](#addendum-rule-ids-2026-05-06) below.
- **Capability tool names** (`sed`, `gsed`) are real-world identifiers
  from PATH; not maury entities.
- **Action vocabulary** (`notify`, `log_jsonl`) — vocabulary terms,
  not instances.
- **File paths within repos** (`CLAUDE.md`, `settings.json`) —
  filesystem conventions.

#### CLI surface

Default display uses names; canonical reference uses IDs.

```sh
maury host list             # shows: name (id_short) profile=home ...
maury host list --ids       # shows full IDs
maury host rename <name|id> <new-name>   # safe rename
maury host show <name|id>   # accepts either
maury profile rename ...    # same
```

Error messages that name an entity always include both:
"profile 'work' (profile_4a2b...) not found in registry."

#### Migration

Bump manifest `version: 1` → `version: 2`. Provide a one-shot upgrade:

```sh
maury manifest upgrade-v1-to-v2 --in <path>
```

Generates fresh IDs for every host and profile, rewrites the file,
and prints a mapping report. Existing seed manifest in
`base-template/.meta/manifest.json` gets regenerated.

### Consequences

- ✅ **Good:** Renaming is safe. Renaming a host or profile
  updates only the `name` field + the corresponding directory.
  Audit history, rules, fragments, cross-references all stay
  valid because they reference the ID.
- ✅ **Good:** Identity stability matches Tenet 6 and the
  industry norm; no rename-as-refactor pain.
- ⚖️ **Neutral:** Manifest is slightly less human-readable.
  Mitigated by CLI display defaulting to names and by
  including the embedded `name` in every error message.
- ⚖️ **Neutral:** Schema version bumps to 2. Loader rejects
  v1 with a "run upgrade" message (per
  [ADR-0030](0030-manifest-schema-migrations.md)).
- ❌ **Bad:** Bootstrap on a new host requires the
  `~/.maury-host-id` file. `maury bootstrap host` creates it;
  without it, the host can't identify itself in the manifest.
- ❌ **Bad:** Code refactor is moderate — manifest module
  changes (lookup helpers, validation), rule engine gains
  profile-name resolution pass, all tests updated, seed
  manifest regenerated. Estimated ~1 hour because we're
  catching it before render engine hardcodes anything.

### Confirmation

- Manifest validator enforces `host.profile` and
  `profile.extends` reference IDs (with name-resolution as a
  backward-compat fallback for hand-written rules).
- Pre-commit hook checks every `<entity>.yaml`'s embedded
  `name` matches the directory name it lives in.
- `~/.maury-host-id` is created exactly once at bootstrap
  and never modified by maury commands afterward.

<details>
<summary><b>Pros and cons of the options</b> (per-option ✅/❌ — click to expand)</summary>

#### Option A: Keep names as keys; accept the rename pain

- ✅ **Good:** Simplest schema; directly readable.
- ❌ **Bad:** Every rename is a multi-file refactor.
- ❌ **Bad:** Diverges from every well-designed identity
  system in production today.

#### Option B (chosen): Surrogate keys; names in directories

- ✅ **Good:** Renaming is a one-command atomic operation.
- ✅ **Good:** Directory navigability preserved.
- ❌ **Bad:** Pre-commit hook needed to keep directory name
  in sync with embedded `name` field.

#### Option C: Surrogate keys AND ID-only directories

- ✅ **Good:** Maximally pure; no name-in-path coupling.
- ❌ **Bad:** Trades navigability for purity that the
  manifest already enforces. Users can't `cd profiles/home/`
  without an ID lookup.

#### Option D: ULID instead of UUID4

- ✅ **Good:** Sortable by creation time; shorter
  (26 vs 32 chars).
- ❌ **Bad:** Requires a dependency or 30 lines of custom
  code; UUID4 is stdlib.
- ⚖️ **Neutral:** Time-ordering is rarely needed for this
  use case. Worth revisiting if directory listings of
  historical IDs become a thing.

#### Option E: GUIDs without entity-type prefix

- ✅ **Good:** Slightly shorter strings.
- ❌ **Bad:** Prefixes (`host_`, `profile_`) make IDs
  self-describing in error messages and audit logs at
  trivial cost — opaque GUIDs lose that.

</details>

## Build-order placement

Inserted **immediately**, before Phase 3 (render engine).
Cheap retrofit at the time; expensive after the render
engine would have been built around v1 path conventions.
Already shipped per `docs/status.md`.

## Followups

- **ULID re-evaluation** if directory listings of historical
  IDs become common — ULID's time-ordering would help
  there.
- **Rule-ID surrogates** were proposed in the addendum
  below but RETRACTED on 2026-05-06 after
  [ADR-0022](0022-branch-per-mining-run.md) made the
  changelog use the commit log directly. Rules keep their
  existing `id:` slug field.

## Addendum: rule IDs — RETRACTED (2026-05-06)

This addendum proposed migrating rule IDs to the `rule_<32 hex>`
surrogate prefix to support [ADR-0021](0021-promotion-changelog.md)'s
promotion changelog. ADR-0021 has since been superseded by
[ADR-0022](0022-branch-per-mining-run.md), which uses the commit log
as the changelog and identifies findings by commit SHA. No surrogate
ID for rules is needed.

The original "What does NOT change" bullet stands as written: rules
keep their existing `id:` slug field. No migration.
