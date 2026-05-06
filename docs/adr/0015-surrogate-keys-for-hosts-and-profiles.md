# ADR-0015: Surrogate keys for hosts and profiles

**Status:** Accepted
**Date:** 2026-05-06

## Related tenets

- [Tenet 6 — Identity is not name](../tenets.md#6-identity-is-not-name)

## Context

The v1 manifest schema used the human-readable name as the dict key for
both hosts and profiles:

```json
"hosts":    { "toad": {"profile": "home", ...}, ... },
"profiles": { "work": {"extends": null, ...}, ... }
```

This is a textbook violation of the **surrogate-key vs natural-key**
distinction (Codd) and the DDD **Entity Identity** principle: an
entity's identity should persist across attribute changes, including
its name. Real consequences of getting this wrong:

- Renaming `"toad"` → `"froggie"` breaks: every manifest reference,
  the host overlay path `profiles/home/hosts/toad/`, every audit log
  entry, any future cross-host reference in fragments
  ("running on toad").
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

## Decision

Add surrogate keys to **hosts** and **profiles**. The `name` field
becomes a mutable display label; internal references use the ID.

### ID format

Stable, opaque, time-sortable string with an entity-type prefix:

```
host_<32 hex chars>      e.g., host_8a7f3c1d4e9b4a2c8f1e7d5b6c2a9e4f
profile_<32 hex chars>   e.g., profile_3f1a8b2c4d5e6f7081a2b3c4d5e6f708
```

Implementation: `uuid4().hex` with the prefix prepended. Stdlib only,
no dependency. The prefix (`host_`, `profile_`) makes IDs
self-describing in audit logs and error messages.

### Manifest schema (revised)

```json
{
  "version": 2,
  "profiles": {
    "profile_3f1a...": { "name": "home", "extends": null, "description": "..." },
    "profile_4a2b...": { "name": "work", "extends": null, "description": "..." }
  },
  "hosts": {
    "host_8a7f...": { "name": "toad",        "profile": "profile_3f1a...", ... },
    "host_9b8c...": { "name": "work-laptop", "profile": "profile_4a2b...", ... }
  }
}
```

Note that `host.profile` now references the **profile ID**, not the
profile name. Same for `extends:` in inheritance chains.

### Repo directory layout

**Names stay in directory paths** for human-friendliness and
git-archaeology friendliness, but the manifest is the source of truth
for the (id, name) mapping. Renaming is an atomic `maury` operation
that updates the manifest AND `git mv`'s the directory.

```
profiles/
├── home/                  <-- directory uses name
│   ├── profile.yaml       <-- contains: id, name
│   └── hosts/
│       └── toad/          <-- directory uses name
│           ├── host.yaml  <-- contains: id, name
│           └── ...
```

This trades a tiny amount of identity-purity (the directory name
mirrors the mutable `name` field) for a substantial usability win
(humans can `cd profiles/home/hosts/toad` without having to look up
IDs). The contract is: directory rename is always paired with manifest
update via `maury <entity> rename`. The pre-commit hook checks that
every `<entity>.yaml`'s embedded `name` matches the directory name it
lives in.

### Host self-identification

Each host writes its ID once at first bootstrap to
`~/.maury-host-id`. The file is created by `maury bootstrap host` and
**never modified**. `socket.gethostname()` becomes a hint for setting
the *initial* `name` field only — never the source of truth for which
manifest entry applies to this machine.

If `~/.maury-host-id` is missing (e.g., fresh install before
bootstrap), maury commands that need to know "which host am I"
short-circuit to a warning and refuse to push.

### Internal references use IDs

- Rules can reference profiles in `then.profile` and `then.forbid_profile`
  by **either** name or ID. Names are resolved through the manifest
  at engine load. (Backward-compat for hand-written rules; the
  synthesizer always emits IDs.)
- Audit log records both ID and name on every entry: `{id, name_at_event_time}`.
- Fragment provenance records originating host_id + profile_id.
- Cross-host fragments referencing other hosts use IDs (e.g.,
  "running on host_8a7f..." rather than "running on toad").

### What does NOT change

- **Repos** are identified by URL in the manifest. The `repos` dict on
  each host uses local nicknames as keys (`"base"`, `"personal"`,
  `"work"`); these are display labels for URLs, never referenced
  cross-host. URL IS the surrogate key.
- **Rules** already have stable IDs (the `id:` field). No change.
- **Capability tool names** (`sed`, `gsed`) are real-world identifiers
  from PATH; not maury entities.
- **Action vocabulary** (`notify`, `log_jsonl`) — vocabulary terms,
  not instances.
- **File paths within repos** (`CLAUDE.md`, `settings.json`) —
  filesystem conventions.

### CLI surface

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

### Migration

Bump manifest `version: 1` → `version: 2`. Provide a one-shot upgrade:

```sh
maury manifest upgrade-v1-to-v2 --in <path>
```

Generates fresh IDs for every host and profile, rewrites the file,
and prints a mapping report. Existing seed manifest in
`base-template/.meta/manifest.json` gets regenerated.

## Consequences

- **Renaming is safe.** Renaming a host or profile updates only the
  `name` field + the corresponding directory. Audit history, rules,
  fragments, cross-references all stay valid because they reference
  the ID.
- **Manifest is slightly less human-readable.** Mitigated by CLI
  display defaulting to names and by including the embedded `name`
  in every error message.
- **Bootstrap on a new host requires the `~/.maury-host-id` file.**
  `maury bootstrap host` creates it; without it, the host can't
  identify itself in the manifest.
- **Code refactor is moderate.** Manifest module changes (lookup
  helpers, validation), rule engine gains profile-name resolution
  pass, all tests updated, seed manifest regenerated. Estimated
  ~1 hour because we're catching it before render engine
  hardcodes anything.
- **Schema version bumps to 2.** Loader rejects v1 with a "run
  upgrade" message.

## Build-order placement

Insert **immediately**, before Phase 3 (render engine). Cheap retrofit
now; expensive after the render engine is built around the v1 path
conventions. No new task; folds into the next thing we build.

## Alternatives considered

- **Keep names as keys, accept the rename pain.** Rejected: the
  user explicitly flagged this as a real pain point and the systems-
  design literature is unanimous.
- **Surrogate keys but use names in directories.** This is what we
  picked.
- **Surrogate keys AND ID-only directories** (`profiles/profile_3f1a.../`).
  Rejected: trades navigability for purity that the manifest already
  enforces.
- **ULID instead of UUID4.** Considered. ULID is sortable by creation
  time and shorter (26 vs 32 chars). Rejected for v1 because it'd add
  a dependency or 30 lines of custom code; UUID4 is stdlib and the
  time-ordering is rarely needed for this use case. Worth revisiting
  if directory listings of historical IDs become a thing.
- **GUIDs without prefix.** Rejected: prefixes (`host_`, `profile_`)
  make IDs self-describing in error messages and audit logs at
  trivial cost.
