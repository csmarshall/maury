# ADR-0031: Self-update path

**Status:** Accepted
**Date:** 2026-05-07

## Related tenets

- [Tenet 9 — Defer to the platform](../tenets.md#9-defer-to-the-platform)
- [Tenet 11 — Explicit beats implicit, with conservative defaults](../tenets.md#11-explicit-beats-implicit-with-conservative-defaults)

## TL;DR

No `maury self-update` command — maury defers self-upgrade to the
package manager (`pipx upgrade maury`, `uv tool upgrade maury`,
`git pull && uv sync` for source installs). Maury surfaces age
two ways: an explicit `maury --version` and a low-noise PyPI
version-check at most once per day, cached in
`last-version-check.json` (per [ADR-0029](0029-maury-state-layout-contract.md)),
showing a one-line stale-version warning when a newer release is
available. Air-gapped hosts disable the version-check entirely.
[ADR-0030](0030-manifest-schema-migrations.md) handles the
specific case where a peer pushes a marker schema newer than this
host's maury can read. Trade-off: maury doesn't self-update, so
users running stale binaries miss bug/safety fixes unless they
notice the warning or pull manually — but no surprise upgrades
either.

## Context

Maury will be installed via `pipx install maury` (per
[ADR-0018](0018-minimum-bootstrap-ux.md)) once published.
During pre-v0.1, source installs use `uv sync` against a clone
of the repo. Either way, the question is: how does an installed
maury get newer?

Two related concerns:

1. **The user wants to upgrade.** What command do they run?
   What does maury do to make this easy or hard?
2. **The user is running an old maury and doesn't realize it.**
   How does maury surface that fact? Does it block? Warn?
   Silently keep working?

The risk is real: a user runs an old maury for six months,
misses an important safety fix or bug fix, or generates output
incompatible with their team's newer-maury hosts. But the
opposite risk is also real: maury that aggressively forces
upgrades or pings PyPI on every command becomes annoying,
network-dependent, or worse — a vector for surprise behavior
changes mid-workflow.

[ADR-0030](0030-manifest-schema-migrations.md) handles one
specific case (manifest-schema-newer-than-maury-supports →
hard refuse). This ADR handles the more general case (maury
binary is old vs. what's available; maury binary is old vs.
what other peers are running).

## Decision

### Maury delegates self-upgrade to the package manager

There is **no `maury self-update` command.** Upgrade paths:

| Install method | Upgrade command |
|---|---|
| `pipx install maury` | `pipx upgrade maury` |
| `uv tool install maury` | `uv tool upgrade maury` |
| Source-clone + `uv sync` | `git pull && uv sync` |
| Air-gapped pipx | sneakernet new wheel + `pipx install --force maury-*.whl` |

This follows tenet 9 (defer to the platform) — the package
manager already does this job; building a maury-specific wrapper
adds ceremony without value, and risks getting upgrade
semantics subtly wrong.

### Stale-version warning, not stale-version blocking

`maury sync` and `maury status` opportunistically check for
"maury is significantly behind" and surface a soft warning if
so. They never block; the warning is informational.

```
$ maury sync
... (sync output) ...

! maury 0.3.2 is installed; 0.5.1 is available on PyPI.
  You're 2 minor versions behind. To upgrade: pipx upgrade maury
  (Suppress with: maury config set update-warnings off)
```

The warning prints AFTER the command's main output so it
doesn't crowd the operational signal.

### Caching: once-per-day check, never required

The version check fetches `https://pypi.org/pypi/maury/json`
(or equivalent) at most **once per day per host**, cached at
`~/.claude/maury-state/last-version-check.json`. If the check
fails (no network, PyPI down), maury proceeds without the
warning — never blocks on the version probe.

Cache schema:

```json
{
  "checked_at": "2026-05-07T14:00:00Z",
  "installed_version": "0.3.2",
  "latest_version": "0.5.1",
  "channel": "pypi"
}
```

The user can disable update warnings entirely:

```sh
maury config set update-warnings off
```

The disable lives in `~/.config/maury/config.toml` (per-host),
not in the manifest (per-user). Disabling on the work laptop
doesn't suppress warnings on the workstation.

### Threshold for "significantly behind"

The warning fires when the installed version is **two or more
minor versions** behind latest. One-minor-version differences
are common during normal release cadence and not worth nagging
about. Patch-version differences never trigger the warning;
patches are upgrade-when-convenient.

Major-version differences ALSO trigger the warning, with
stronger language ("major version available; review changelog
before upgrading").

The 2-minor threshold is a **starting heuristic**; revisit
once we have real release-cadence data. Tunable via
`maury config set update-warning-threshold N` (a v1.1
addition; v1 ships the constant 2).

### Hard-block scenarios (already covered elsewhere)

This ADR's stale-version check is purely advisory. The
following hard-block scenarios are handled by other ADRs:

- **Manifest schema newer than supported:** ADR-0030 rule 2
  refuses outright. The user must upgrade maury before they
  can sync.
- **Claude Code interaction-contract drift detected:** when
  `maury verify-cc-contract` (planned per `docs/status.md`)
  flags that a documented behavior changed, that's a
  user-facing maintenance event — not an automatic upgrade.

The stale-version check doesn't gate any operation. The user
can run an N-version-behind maury indefinitely.

### Source-install caveat

Users running maury from a `uv sync`'d clone (pre-v0.1
developers, or anyone hacking on maury) get the warning too,
unless suppressed. The exact mechanism for distinguishing
clone-installs from package-installs is implementation-detail
TBD — `+local` PEP 440 local-version syntax is the cleanest
candidate but requires explicit `setuptools-scm` config or
similar. The clone owner self-manages their version via
`git pull`; the warning logic just needs a reliable signal to
skip them. Worst case for v1: clone installs see the warning
and disable it manually via `maury config set update-warnings
off`.

### Air-gapped hosts

Per [ADR-0028](0028-offline-behavior.md) §"Air-gap pattern",
air-gapped hosts have no internet. The PyPI version-check fetch
will always fail; per the no-pre-probe principle, the failure
degrades silently to no-warning. Air-gap operators track maury
versions out-of-band via the sneakernet wheel cadence. This
matches ADR-0028's "read-only commands always work" pattern —
the version check is a soft enhancement, never a blocker.

## Consequences

- **One upgrade path per install method, all delegated to the
  platform.** No maury-specific upgrade machinery to maintain.
- **Soft-warn, never block.** A user running old maury keeps
  working; the warning surfaces the gap without forcing
  action.
- **Once-daily PyPI check is bounded network use.** A user
  running `maury sync` every five minutes pays the version-
  check cost once per day, not once per command.
- **Manifest-schema mismatches stay hard-blocking** per
  ADR-0030. The two concerns don't conflate.
- **No silent auto-upgrades.** Per tenet 11 (explicit beats
  implicit), the user runs the upgrade command consciously.
- **Graceful offline behavior.** No version check = no
  warning, but the command itself proceeds (consistent with
  ADR-0028's per-command offline policy).
- **One new state file** added to `~/.claude/maury-state/`:
  `last-version-check.json`. Per
  [ADR-0029](0029-maury-state-layout-contract.md), this ADR
  amends 0029's File inventory.
- **One new config file** at `~/.config/maury/config.toml`
  for the per-host disable knob. This is a third lifecycle
  category, distinct from maury-state and identity:
  preferences are user-set and persistent across crashes;
  bookkeeping state is maury-set and recoverable
  ([ADR-0029](0029-maury-state-layout-contract.md));
  identity (`~/.maury-host-id`) is one-time-set and
  irrecoverable ([ADR-0015](0015-surrogate-keys-for-hosts-and-profiles.md)).
  Different lifecycles, different homes. Trivial schema for
  v1; document in 0029-style fashion if the file accumulates
  more keys.
- **A new `maury config` CLI command group** with one initial
  key (`update-warnings`). Lean: one-off addition, no
  separate ADR needed for v1. If the config-key surface grows
  beyond ~5 keys, that growth gets its own ADR.

## Alternatives considered

- **Ship `maury self-update` that wraps `pipx upgrade maury`.**
  Rejected: tenet 9 (defer to the platform). pipx already does
  this; the wrapper would add nothing except a maury-specific
  way to get the same result, and would have to handle every
  install-method variant maury supports.
- **Aggressively block on stale maury (refuse to sync if N
  versions behind).** Rejected: too coercive. A user running an
  old maury for legitimate reasons (LTS deployment, frozen
  environment) shouldn't be blocked; they should be informed.
- **No version check at all.** Considered. The risk of running
  an old maury invisibly is real (silent divergence from peers,
  missed safety fixes); the cost of a once-daily PyPI HEAD
  request is minimal. The check pays for itself.
- **Probe the version on every command.** Rejected: probes
  add latency (per
  [ADR-0028](0028-offline-behavior.md)'s "no pre-probe"
  argument). Once-daily caching is the right balance.
- **Embed version-recommendation in the manifest.** (E.g.,
  manifest specifies "minimum maury version 0.5.0".) Rejected:
  that's the manifest schema-version mechanism in disguise
  (ADR-0030). Reusing it for binary version creates
  confusion; the two are different concerns (binary
  capabilities vs. manifest format).

## Build-order placement

Phase 4 or 5 — small slice. The version check + cache + warning
output is ~50 LOC. The disable-config knob is similarly small.
Both can land any time after maury is on PyPI; until then, the
warning code is dead (no upstream to compare against).

`docs/status.md` should add a row for `maury config set
update-warnings off` once the feature ships.

## Followups

- **Multi-channel support.** A user might install from a
  private PyPI index (corporate environment) instead of
  public PyPI. v1.1: the version-check URL becomes
  configurable per-host (defaults to public PyPI). Until
  then, private-index users disable the check.
- **Release notes link in the warning.** Currently the
  warning just names the version. A v1.1 enhancement could
  fetch the release notes URL and include it ("see release
  notes: <url>") so the user can decide if the upgrade is
  worth doing now.
- **Telemetry of version-check failures.** Useful for
  understanding how often network checks succeed across the
  user base. Out of scope for v1; would require a telemetry
  channel maury doesn't have.

## Claude Code references

This ADR introduces no new Claude Code dependencies. The
PyPI fetch is HTTP-only, via the standard library or a small
bounded dependency. Claude Code is unaware of maury's binary
version.

## Amendment history

None.
