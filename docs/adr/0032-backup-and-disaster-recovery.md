# ADR-0032: Backup and disaster recovery

**Status:** Accepted
**Date:** 2026-05-07

## Related tenets

- [Tenet 1 — First, do no harm](../tenets.md#1-first-do-no-harm)
- [Tenet 9 — Defer to the platform](../tenets.md#9-defer-to-the-platform)

## TL;DR

Synced repos and the rendered `~/.claude/` are recoverable from git
remotes; what backup actually needs to address is the **host-local
content** — `~/.maury-host-id`, the append-only logs
(`claude-writes.jsonl`, `session-history.jsonl`, etc.), the staging
files in `~/.claude/maury-staging/`, and (opt-in) the raw Claude
Code transcripts under `~/.claude/projects/`. Two commands:
**`maury backup`** writes a `.tar.zst` bundle; **`maury restore`**
unpacks it back into place. Default scope is metadata-only;
`--include-transcripts` opts into the (much larger) raw transcript
set. Storage is the user's choice — restic to B2, rsync to a NAS,
USB stick. Trade-off: explicit user discipline rather than
maury-managed backup automation, but no opinionated dependency on
a specific remote.

## Context

[ADR-0029](0029-maury-state-layout-contract.md) §"Recoverable
from loss" enumerates which files in `~/.claude/maury-state/`
survive a directory wipe (last-render and active-context get
rebuilt on next sync) and which don't (the append-only logs —
claude-writes, session-history, profile-switches —
permanently lose their history). [ADR-0029](0029-maury-state-layout-contract.md)
also lists what doesn't live in maury-state (synced repos,
identity file, maury-staging).

That's a recovery analysis, but it's not a backup story.
What does a maury user need to back up to actually survive
disk failure or accidental deletion? What's the recovery
procedure?

The answers depend on what's recoverable from external
sources vs. what only exists on this host:

| Asset | External recovery | Local-only |
|---|---|---|
| Synced repos | git remote (`git clone`) | — |
| Manifest | inside synced repo | — |
| Rendered `~/.claude/` content | `maury render` from manifest | — |
| `last-render.json` | rebuilt by next render | — |
| `active-context.json` | rebuilt by `maury init` / `maury profile use` | — |
| `~/.maury-host-id` | — | yes (identity, irreplaceable) |
| `claude-writes.jsonl` | — | yes (drift attribution history) |
| `session-history.jsonl` | — | yes (profile attribution for past transcripts) |
| `profile-switches.jsonl` (until Phase 10) | — | yes (audit history) |
| `~/.claude/maury-staging/captures.jsonl` | — | yes (un-reviewed captures) |
| `~/.claude/maury-staging/pending-mining/` | — | yes (offline-staged mining work) |
| `~/.claude/projects/<hash>/*.jsonl` (Claude Code transcripts) | — | yes (raw conversation history) |

The local-only items are what backup needs to address.

## Decision

### Two commands

```sh
maury backup [--out <path>] [--include-transcripts]
maury restore <path> [--into <home>]
```

`maury backup` writes a tarball containing the local-only
assets (defaulting to `maury-backup-<host_id_short>-<YYYY-MM-DD>.tar.gz`
in the cwd). `maury restore` consumes a tarball and writes the
contents back into the appropriate locations under `$HOME`.

### What `maury backup` includes by default

```
~/.maury-host-id                              (identity)
~/.claude/maury-state/claude-writes.jsonl     (drift history)
~/.claude/maury-state/session-history.jsonl   (profile history)
~/.claude/maury-state/profile-switches.jsonl  (audit history)
~/.claude/maury-state/active-sessions.jsonl   (in-flight session state)
~/.claude/maury-state/active-context.json     (current binding — re-derivable but cheap to back up)
~/.claude/maury-staging/                      (un-reviewed captures + offline-mining material)
```

### What `maury backup` includes with `--include-transcripts`

Adds:

```
~/.claude/projects/                           (Claude Code raw transcripts)
```

This is **opt-in** because:

1. It can be very large (months/years of transcripts).
2. The user may already back this up via a separate
   transcript-archival system.
3. Some transcripts may contain sensitive context the user
   wants archived separately (e.g., encrypted).

A user who wants comprehensive recovery uses
`--include-transcripts`; a user who only needs maury-state
recovery uses the default.

### What `maury backup` does NOT include

Everything in [ADR-0029](0029-maury-state-layout-contract.md)'s
"recoverable from external sources" category:

- Cloned synced repos at `repos_root` (typically
  `~/.config/maury/repos/`). Recoverable via `git clone`.
- Rendered `~/.claude/` content (CLAUDE.md, settings.json,
  skills/, agents/, etc.). Recoverable via `maury render`.
- `last-render.json` (rebuilt on next render).
- The maury Python package itself (re-installed via pipx).
- Per-host config at `~/.config/maury/config.toml` (per
  [ADR-0031](0031-self-update-path.md)) — currently single-
  preference; re-set if needed. *Future: when this file
  accumulates more keys, revisit including it.*

### `maury restore` flow

```
maury restore <tarball>
```

1. **Validates** the tarball: must contain a top-level `manifest.txt`
   listing what's inside, plus the expected file paths. Refuse if
   malformed.
2. **Checks identity collision.** Three cases:
   - **No existing `~/.maury-host-id`** (fresh install or
     deleted identity): restore proceeds and writes the
     tarball's identity. This is the canonical disk-loss
     recovery flow.
   - **Existing `~/.maury-host-id` matches tarball's
     `host_id`**: restore proceeds; the tarball is a backup
     of this same host.
   - **Existing `~/.maury-host-id` differs from tarball's
     `host_id`**: refuse with *"this host already has
     identity X; the backup is for identity Y. Use
     `--into /tmp/restore-tmp` to extract without touching
     this host's state, then move files selectively."*
3. **Restores files** into their canonical locations under
   `$HOME` (or `--into <dir>` if specified).
4. **Tells the user what to do next.** `maury restore` itself
   does NOT re-run init or sync — it just lays down files. The
   user runs `maury sync` (if already-initialized state is
   restored) or `maury init --from-dir <repo>` (if needing to
   re-clone the repos) to repopulate `last-render.json` and
   active-context. The restore command prints both options
   with the exact next-command to type.
5. **Prints a summary** of what was restored, what was
   skipped (if any), and what next-steps the user should take.

### Cadence and tooling

`maury backup` is **invoked manually** by the user — maury
does not schedule backups. Per tenet 9 (defer to the
platform), users have existing backup workflows (Time
Machine, restic to B2, `borg`, etc.) and should compose
`maury backup` into those rather than have maury reinvent
scheduling.

Recommended pattern: a cron job or systemd timer that runs
`maury backup --out /backups/maury/` on the user's preferred
cadence, with their existing backup tool picking up the
`/backups/` directory.

### Backup tarball format

Plain `tar.gz`. Inside:

```
maury-backup-<host_id_short>-<YYYY-MM-DD>/
  ├── manifest.txt          ← what's in this backup, schema version,
  │                            host_id, created_at
  ├── maury-host-id         ← copy of ~/.maury-host-id
  ├── maury-state/
  │   ├── claude-writes.jsonl
  │   ├── session-history.jsonl
  │   └── ...
  ├── maury-staging/
  │   ├── captures.jsonl
  │   └── pending-mining/...
  └── projects/             ← only if --include-transcripts
      └── <hash>/...
```

`manifest.txt` example:

```
maury backup
schema_version: 1
host_id: host_e3844a43...
host_id_short: e3844a43
created_at: 2026-05-07T14:32:00Z
maury_version: 0.5.1
includes_transcripts: false
files:
  - maury-host-id (40 bytes)
  - maury-state/claude-writes.jsonl (1.2 MB, 4823 lines)
  - maury-state/session-history.jsonl (88 KB, 312 lines)
  - ...
```

The `schema_version: 1` lets future maury versions evolve the
backup format with a per-version restorer (same pattern as
[ADR-0030](0030-manifest-schema-migrations.md)).

### Air-gap and sneakernet

[ADR-0028](0028-offline-behavior.md) §"Air-gap pattern"
covers the *bootstrap-and-update* sneakernet path
(`--from-tarball`, `--store-only` / `--resume-pending`).
ADR-0032 adds the **disaster-recovery** sneakernet path: an
air-gapped host's irreplaceable history (host_id, append-only
logs, un-reviewed captures, offline-staged mining material)
can be backed up via `maury backup` to a USB stick and
sneakernetted to long-term storage. Without this, an
air-gapped host's local-only assets have no recovery path
at all.

### Backup integrity verification (ships with v1)

```
maury backup verify <tarball>
```

Reads `manifest.txt`, walks each listed file, computes
sha256, and refuses if any file is missing or mismatched.
Backup tools without integrity verification are a known
footgun: silent corruption discovered at restore time is
catastrophic. Per tenet 1, this ships in v1 alongside
`maury backup` and `maury restore`.

The `maury backup` command writes a sha256 for each file into
`manifest.txt` so verification has a target.

### Disaster scenarios and recovery procedures

| Scenario | Recovery |
|---|---|
| Disk wipe; have backup | `pipx install maury` → `maury restore <tarball>` → `maury sync` to re-clone repos |
| Disk wipe; no backup | `pipx install maury` → `maury init --from-dir <repo>` (new identity; loses all maury-state history; un-reviewed captures gone; transcript history gone if `~/.claude/projects/` not separately archived) |
| `~/.maury-host-id` accidentally deleted | If you have a backup containing it, `maury restore` puts it back. Otherwise, treat as new host and re-bootstrap (you'll show up in the manifest as a new host_id; old host_id remains in manifest history for audit). |
| Just maury-state corrupted | `rm -rf ~/.claude/maury-state/`, then `maury init` (or `maury sync` if already initialized) — `last-render.json` rebuilds on next render; `active-context.json` rebuilds on `maury init` / `maury profile use`; append-only history (claude-writes, session-history, profile-switches) is lost; warn user about the lost history per [ADR-0029](0029-maury-state-layout-contract.md) |
| Synced repo lost on every host (catastrophic) | Restore the most recent backup that includes a clone snapshot (NOT included by default — see followup); failing that, the team's most-recent push to remote is the recovery point |

## Consequences

- **Identity is the only truly irrecoverable asset.** If
  `~/.maury-host-id` is gone and not backed up, the host
  becomes a new host (different identity in the manifest). The
  old host's audit trail still exists in the manifest's history,
  but the new host can't claim it.
- **Append-only history is recoverable iff backed up.** Drift
  attribution, profile-aware mining attribution, audit history
  — all live only in append-only logs. A user who cares about
  these takes regular backups.
- **Un-reviewed captures and offline-staged mining are at risk
  without backup.** The user has work-in-progress that's
  host-local-only.
- **Synced repos are belt-and-suspenders.** They live on git
  remotes AND in local clones; double-failure to lose them.
  Not in default backup; recoverable by clone.
- **Backup composes with existing backup tools.** `maury
  backup` produces a tarball; the user's restic / B2 / Time
  Machine / etc. handles cadence and storage. No
  reinvention.
- **Two new CLI commands.** `maury backup` and `maury
  restore`. ~150 LOC + tests. Self-contained; no dependencies
  on Phase 7+ work.
- **Backup tarball schema versioning** (1, 2, …) means future
  maury can evolve the format without breaking old backups —
  same per-version-reader pattern as
  [ADR-0030](0030-manifest-schema-migrations.md).

## Alternatives considered

- **Maury schedules its own backups** (cron-style, daemon-
  style). Rejected: tenet 9 — defer to the platform. Users
  already have backup tools; reinventing scheduling here would
  duplicate them and create config-fragmentation.
- **Backup includes the synced repos by default.** Rejected:
  bloats backups for assets that are already
  triply-redundant (git remote + local clone + every other
  host's clone). Add `--include-repos` flag if a user wants it.
- **Encrypted backups by default.** Considered. Rejected for
  v1: composable with the user's backup tool's encryption
  (restic, age, etc.). v1.1: a `--encrypt-with <recipient>`
  flag if there's user demand.
- **Continuous incremental backup** (a long-running daemon).
  Rejected: too much surface area; users have existing tools
  for this. v1 is one-shot tarballs.
- **Embed backup of `~/.claude/projects/` (transcripts) in
  default.** Rejected: the size argument. Opt-in via
  `--include-transcripts` is the right balance.

## Build-order placement

Phase 4 or 5 — small slice. `maury backup` and
`maury restore` are bounded operations on local state. Useful
to ship early because it gives the user a recovery story
immediately.

## Followups

- **`--include-repos` flag.** For users who want
  belt-and-suspenders coverage (e.g., before doing something
  potentially destructive to their git remotes).
- **`maury backup --diff-since <prev-tarball>`.** Incremental
  backup that captures only changes since a previous tarball.
  v1.1; not blocking.
<!-- backup-integrity verification promoted from followup to v1 above -->

- **Multi-tarball / rolling-backup support.** Today
  `maury backup` produces a one-shot tarball. v1.1: a
  `--rotate <N>` flag that keeps the N most recent backups in
  a directory, pruning older ones. Until then, the user's
  external backup tool handles rotation.
- **Restore-to-different-host workflow.** Sometimes a user
  wants to migrate maury state from old laptop to new laptop
  (same human, new hardware). The current `restore` refuses
  on identity collision. Followup: a
  `--migrate-identity` flag that updates the manifest to
  rename the old `host_id` to a new name + new `host_id`,
  preserving history. v1.1.
- **Expand backup to include `~/.config/maury/config.toml`
  once the per-host preference surface grows beyond one key.**
  Tracked here so we don't forget.

## Claude Code references

This ADR introduces no new Claude Code dependencies. The
`~/.claude/projects/` opt-in references the directory Claude
Code owns per
[`cc-contract:past-transcripts-not-auto-loaded`](../claude-code-contract.md#cc-contractpast-transcripts-not-auto-loaded);
maury reads but doesn't write into that tree.

## Amendment history

- 2026-05-11 — "profile" vocabulary renamed to "mode" per ADR-0037 doctoral examination. References to "profile" in this ADR now read "mode"; no semantic changes.
