# ADR-0015: Surrogate keys for hosts and profiles

**Status:** Accepted
**Date:** 2026-05-06
**Amended:**
- 2026-05-06 — added rule-IDs addendum proposing `rule_<32 hex>`
  prefix for rules; subsequently RETRACTED on the same day after
  ADR-0022 made it unnecessary (commit log preserves the trail).
- 2026-05-07 — corrected the §"Host self-identification" claim
  that `~/.maury-host-id` is "created by `maury bootstrap host`."
  As of the Phase 4 implementation, `maury init` creates the
  file on the new host's first run (see
  `src/maury/bootstrap/init_cmd.py:120-126`); `maury bootstrap
  host` is a curator-side command that runs on a *different*
  host (with rw access to the base repo) and never writes to
  `~/.maury-host-id` on the host being registered. The "never
  modified" invariant still holds — once init creates the file,
  it's immutable.
- 2026-05-11 — **breaking schema change:** `profile_<32 hex>` surrogate
  ID prefix renamed to `mode_<32 hex>` per ADR-0037 doctoral examination
  (profile → mode vocabulary rename). The `host_<32 hex>` prefix is
  unchanged. All existing `profile_<hex>` values in manifests require
  migration per ADR-0030. See ADR-0037 §mode for the full context.
- 2026-05-14 — **non-breaking format extension:** `host_<hex>` IDs may
  now carry an optional **cosmetic tag suffix** for human readability:
  `host_<8-hex>_<tag>` (e.g., `host_24b2a0aa_laptop`). The 8-hex prefix
  remains the lookup primitive; the tag is purely cosmetic and never
  parsed for resolution. Tag grammar: `[a-z0-9-]{1,32}` (DNS hostname
  rules per RFC 1123). Existing 32-hex IDs (`host_<32 hex>`) remain
  valid forever — no migration. The "never modified after first init"
  invariant splits: the hex is immutable identity; the tag is freely
  editable. See §"Tagged ID format" below.

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

- Renaming `"workstation"` → `"froggie"` breaks: every marker reference,
  every host-tagged section keyed by the old name, every audit log
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
no dependency. The prefix (`host_`, `mode_`) makes IDs
self-describing in audit logs and error messages.

#### Tagged ID format (amended 2026-05-14)

The legacy `host_<32 hex>` form has a real readability problem: a
user looking at `~/.maury-host-id` or grepping the manifest sees
`host_24b2a0aadfd3459fa2a21ed7d0d79333` and has no idea which
host that refers to without cross-referencing the manifest's
`name` field. For an entity that's surfaced in every audit log
entry, in error messages, and in the local-state file, that's
cognitive friction with no payoff.

The amended format adds an **optional cosmetic tag suffix**:

```
host_<8 hex chars>_<tag>     e.g., host_24b2a0aa_laptop
```

Where:

- **`<8 hex chars>`** — the lookup primitive. 8 hex = 4 billion
  possibilities; for personal-scale fleets (≤ 100 hosts) the
  birthday-paradox collision probability is < 1 in 800 million.
  If a curator-side `bootstrap host` ever detects a hex
  collision against the manifest, init regenerates.
- **`<tag>`** — purely cosmetic. Grammar: `[a-z0-9-]{1,32}`
  (lowercase alphanumeric + hyphens, max 32 chars). Matches DNS
  hostname rules per [RFC 1123 §2.1](https://www.rfc-editor.org/rfc/rfc1123#section-2.1)
  — stand-on-shoulders so anyone familiar with DNS labels
  understands the grammar without re-reading maury's spec. The tag is **never parsed for
  resolution**, **never used for lookup**, and **never validated
  against anything load-bearing**. Maury's code touches the tag
  only at two points: writing it during `init` (after
  normalizing user input) and displaying it in human-facing
  output.

Both `host_<32 hex>` (legacy 32-hex) and `host_<8 hex>_<tag>`
(new tagged) regex-validate. No migration needed; hosts initialized
before 2026-05-14 keep their original IDs forever. The validation
regex shipped in `src/maury/ids.py`:

```
^host_([0-9a-f]{32}|[0-9a-f]{8}_[a-z0-9-]{1,32})$
```

The `mode_<32 hex>` format is **not** extended with tags. Modes
are not surfaced in `~/.maury-host-id` or in audit log entries
where ID readability matters most; the manifest's `name` field
already carries the human label. Adding tags to mode IDs would
double the schema surface for no real payoff.

#### Tag normalization

The user's `--tag` input at `init` time gets normalized lossily
to fit the grammar:

1. Downcase: `XADAM` → `xadam`.
2. Replace any non-`[a-z0-9-]` character with `-`: `xadam___` →
   `xadam---`, `host@home` → `host-home`.
3. Truncate to 32 characters.
4. If the result is empty (e.g., user input was all special
   characters): error and re-prompt.

After normalization, `init` shows the user the proposed tag and
asks: "Tag this host as `xadam---`? `[Y/n/edit]`" with the
DNS-rule summary embedded in the help text. The user accepts,
rejects (re-prompt), or edits.

Defaults: at init's prompt, the suggested tag is
`socket.gethostname()` normalized through the same pipeline.
Most users hit enter.

#### Mutability split (amended 2026-05-14)

ADR-0015's original "never modified after first init" invariant
splits into two:

- **Hex (immutable identity).** The 8-hex prefix is written once
  at `init` and is the lookup primitive. Editing it is
  functionally equivalent to swapping the host's identity —
  see ADR-0042 for the sync-time guard that detects and refuses
  this case.
- **Tag (mutable cosmetic).** The tag suffix in
  `~/.maury-host-id` may be freely edited by the user. Maury's
  code never reads the tag for any logic; it's purely a label.
  Changing `host_24b2a0aa_laptop` to `host_24b2a0aa_main-laptop`
  is harmless.

#### Manifest schema (revised)

```json
{
  "version": 1,
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
`~/.maury-host-id`. The file is created by `maury init` on the
host's first run. The 8-hex prefix is **never modified** after
init; the cosmetic tag suffix is freely editable (see §"Mutability
split" above for the post-2026-05-14 amendment). `socket.gethostname()`
is **not** consulted for resolution after init — per ADR-0039 step 8,
init generates a fresh `host_<hex>_<tag>` UUID and writes it locally,
then the curator records that locally-generated UUID in the mode's
marker file. Hostname is no longer load-bearing for any
identity-resolution code path.

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
  v1; see [addendum (retracted)](#addendum-rule-ids--retracted-2026-05-06) below.
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

#### Schema version

The manifest carries `"version": 1` (the schema introduced by this
ADR is the first one users ever see; there is no v0 in the wild).
Migration tooling will land if and when a v2 schema is introduced —
not pre-emptively.

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
  `~/.maury-host-id` file. `maury init` creates it on first
  run on the new host; without it (e.g., before init has been
  run), the host can't identify itself in the manifest. The
  curator-side `maury bootstrap host` registers the *manifest
  entry* for the new host but does not touch
  `~/.maury-host-id` on the new machine — that's init's job.
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
- `~/.maury-host-id` is created exactly once by `maury init`
  on the new host's first run, and never modified by maury
  commands afterward.

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

## Amendment history

- 2026-05-06 — rule-IDs addendum proposed then retracted same day (see top-of-file note).
- 2026-05-07 — corrected §"Host self-identification" claim (see top-of-file note).
- 2026-05-11 — `profile_<32 hex>` renamed to `mode_<32 hex>` throughout. Breaking schema change; migration required per ADR-0030. `host_<32 hex>` unchanged.
- 2026-05-14 — non-breaking format extension: `host_<hex>` IDs may carry an optional cosmetic tag suffix (`host_<8 hex>_<tag>`). Hex remains immutable identity; tag is freely editable. Existing 32-hex IDs accepted forever; no migration. See top-of-file note + §"Tagged ID format" for grammar (RFC 1123 DNS labels) and §"Mutability split" for the hex/tag invariant separation.
- 2026-05-19 — `version: 1` is the first schema users will ever see. Pre-release "v1→v2 migration" tooling deleted (no users exist to migrate from). Migration framework lands if/when a v2 is genuinely needed.
