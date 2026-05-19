# ADR-0033: `pr` repo mode

**Status:** Accepted
**Date:** 2026-05-07

## Related tenets

- [Tenet 1 — First, do no harm](../tenets.md#1-first-do-no-harm)
- [Tenet 5 — The user arbitrates ambiguity](../tenets.md#5-the-user-arbitrates-ambiguity)
- [Tenet 11 — Explicit beats implicit, with conservative defaults](../tenets.md#11-explicit-beats-implicit-with-conservative-defaults)

## TL;DR

The original `ro` / `rw` deploy-key binary missed a load-bearing
team workflow: engineers consume the team rules repo freely, but
writes must flow through PR review by the curator. This ADR adds a
third value, **`pr`**, to the `repo_mode` enum: read like `ro`,
write through pull request. When `maury review` sees a `pr`-mode
target it pushes the review branch and opens a PR via the platform
CLI (`gh` for GitHub; pluggable per
[ADR-0016](0016-pluggable-repo-backends.md)) instead of merging
locally. Trade-off: requires a platform CLI on hosts that
contribute to `pr`-mode repos, which makes contribution path
backend-aware (mitigated by the per-backend adapter pattern).

## Context

The maury manifest's repo entries today have a `mode` field
with two values: `ro` (read-only — host can pull but not push)
and `rw` (read-write — host pushes directly). Per
[ADR-0003](0003-per-host-deploy-keys.md), these map to deploy-
key permissions on GitHub.

That two-value model misses a real workflow:

> **The team curator publishes a maury profile (e.g.,
> `acme-team-engineering`). All engineers on the team
> *subscribe* to it — they have read access to fetch updates,
> but writes go through a PR-with-curator-review process. An
> engineer's run-branch from `maury mine` can't be merged
> directly; it has to be turned into a PR that the curator
> reviews and merges.**

This pattern is universally familiar (every open-source repo
on GitHub works this way), but maury's existing `ro`/`rw`
binary doesn't encode it. Today an engineer would either:

- Have `rw` access (curator trusts them blindly — wrong for
  production team-shared content).
- Have `ro` access (engineer can only read; contributing back
  requires manual workflow outside maury).

Per [concepts.md §9](../concepts.md#9-repo_mode-access-subtype),
this gap was named: a third `pr` mode that means "read like
ro, write through pull request" was introduced as a planned
construct. This ADR specifies it.

## Decision

### Add `pr` to the manifest's repo-mode enum

Manifest schema: `mode: ro | pr | rw` (was: `mode: ro | rw`).
Backward-compatible (existing manifests without explicit `pr`
entries are unaffected).

```json
{
  "hosts": {
    "host_e3844a43...": {
      "name": "<engineer-laptop>",
      "profile": "profile_acme...",
      "repos": {
        "base":     { "url": "git@github-base:org/maury-base.git",      "mode": "ro" },
        "team":     { "url": "git@github-team:acme/maury-team-eng.git", "mode": "pr" },
        "personal": { "url": "git@github-pers:user/maury-personal.git", "mode": "rw" }
      }
    }
  }
}
```

*(Some required host-entry fields elided for brevity — see
[ADR-0015](0015-surrogate-keys-for-hosts-and-profiles.md) and
[ADR-0003](0003-per-host-deploy-keys.md) for the full schema,
including `push_policy` and any other mandatory fields.)*

### Mechanically: `pr` is `ro` + a contribution side-channel

Per [concepts.md §9](../concepts.md#9-repo_mode-access-subtype):
"`pr` is a workflow layered on top of `ro` deploy-key access
plus a side channel (e.g., GitHub PR via `gh`); the mode value
just makes the contract explicit in the manifest."

Concretely:

- The deploy key for a `pr`-mode repo is **read-only at the
  git level**. No direct push is possible — GitHub will refuse.
- Contribution flows through GitHub's PR mechanism via the
  `gh` CLI (which authenticates via the user's GitHub account,
  separate from the deploy key per
  [ADR-0003](0003-per-host-deploy-keys.md)'s account/key
  separation).
- This means **`pr` mode requires `gh` to be installed and
  authenticated for the user.** `gh` is already a soft
  dependency for the `github` backend per
  [ADR-0016](0016-pluggable-repo-backends.md) (used for
  `create_remote` and `attach_credential`),
  [ADR-0018](0018-minimum-bootstrap-ux.md) (deploy-key install
  helper), and [ADR-0022](0022-branch-per-mining-run.md)
  (`gh pr create` for review-branch PRs). This ADR extends
  that existing dependency to the `pr`-mode contribution flow.
  Non-GitHub backends would need their own equivalent — out
  of scope for v1; only `pr` over GitHub is supported
  initially.

#### `gh` auth identity vs deploy-key account

[ADR-0003](0003-per-host-deploy-keys.md)'s deploy-key model
deliberately decouples per-host repo access from any GitHub
account identity (so an employer can't ask "which account is
authenticated on this device?"). `pr` mode reintroduces a
coupling: `gh auth login` requires the user to authenticate a
GitHub account, and the PR will be authored as that account.

**This matters for team repos.** An engineer with a personal
GitHub account on a work laptop will, by default, open
team-repo PRs as their personal account — likely not what
their employer wants. Engineers contributing to team `pr`
repos should ensure `gh auth login` is set to the
account-of-record for that team's content (typically their
work GitHub account on the work laptop).

Maury doesn't enforce or check this — it's a per-host user
configuration concern outside maury's scope. Documented as a
gotcha here.

### `maury sync` behavior on a `pr`-mode repo

Same as `ro`: pull only; no push. The "I want to share this
back" workflow is `maury review` (see below).

### `maury review` behavior on a `pr`-mode repo

When a mining run produces a branch
`maury/run/<run-id>` (per
[ADR-0022](0022-branch-per-mining-run.md)) on a `pr`-mode
repo, `maury review` walks commits as usual. The terminal
step diverges by mode:

| Mode | Terminal step |
|---|---|
| `rw` | `git merge` the review branch into main (default), or push and let the user open a PR (optional). |
| `pr` | **Push the review branch + open a PR via `gh pr create`.** Prints the PR URL for the user to follow. The curator reviews on GitHub and merges there. |
| `ro` | **Refuse to start.** A `ro`-mode repo accepts no contributions; the run-branch can be reviewed locally but has nowhere to land. Maury surfaces the available paths: cross-boundary promotion (per [ADR-0045](0045-cross-trust-boundary-promotion.md)) if applicable, or just discarding the run. |

The PR body (auto-generated) includes:

- The mining run's metadata (run-id, host_id, mining timestamp).
- A summary of accepted findings with their `Source-Profile`
  (per [ADR-0026](0026-profile-aware-mining.md)) and
  `Content-Hash` (per [ADR-0022](0022-branch-per-mining-run.md))
  trailers.
- A link to the local review session for context.

### Distinction from cross-boundary promotion

[ADR-0045](0045-cross-trust-boundary-promotion.md) covers
*cross-trust-boundary* promotion — content flowing between
repos the user has different access on. ADR-0045's mechanism
is a curator host with rw on both repos cherry-picking
across.

`pr` mode is **within one repo.** The contribution stays in
the same trust boundary; it just goes through PR-review
instead of direct push. The user could (separately) cross-
promote a PR-merged change to a different trust boundary via
the ADR-0045 flow.

The two compose naturally: a finding from a `pr`-mode team
repo could be promoted to `base` (a different trust
boundary) by the curator, after the team repo's PR landed.

### When `gh` isn't available

If `gh` is not installed or not authenticated, `maury review`
on a `pr`-mode repo refuses with:

```
ERROR: This repo is `pr` mode but `gh` is not available.
Install GitHub CLI (gh) and authenticate (`gh auth login`)
to contribute via PR. Until then, this repo is effectively
read-only on this host.
```

The user can fall back to manual PR creation: maury prints
the local review-branch name and the destination repo URL;
the user pushes manually and opens the PR via the GitHub web
UI.

### Schema

Adding `pr` to the enum is a `repo_mode` enum extension on the
v1 schema. Existing v1 manifests written before this ADR don't
reference `pr`; the schema accepts the additional value without
a version bump (extending an enum is forward-compatible at the
JSON layer — older maury versions reading a manifest that
mentions `pr` would just see an unrecognized enum value).

Per the 2026-05-19 cleanup, maury hasn't released yet and there
is only one shipping schema version (v1). When a v2 schema is
genuinely introduced, ADR-0030 (currently Deferred) is the
framework to reach for; for the `pr` extension specifically,
no migration is needed.

## Consequences

- **Three-mode model (`ro`/`pr`/`rw`) lands cleanly** behind
  one new enum value. concepts.md §6's planned forward-
  reference is now an actual ADR.
- **Engineers can subscribe to team-shared content** with
  read access plus a sanctioned contribution path. The
  team-upstream workflow (gap K part 2 → ADR-0034) is now
  expressible.
- **`gh` becomes a soft dependency for `pr` mode** — required
  if you want maury-driven PR creation; optional if you'll
  open PRs manually. Documented; not silently assumed.
- **No schema-version bump required** for the enum extension.
  Adding a new enum value is forward-compatible at the JSON
  layer; older maury versions seeing `pr` would surface it as
  an unrecognized enum value rather than fail. If a future
  change does require a version bump, ADR-0030 (currently
  Deferred) is the framework to revive.
- **`maury review` gains a per-mode terminal step.** Bounded
  conditional in the review flow; ~30 LOC.
- **Distinction from ADR-0045 stays clear.** `pr` mode is
  intra-trust-boundary; cross-boundary promotion is across
  trust boundaries. The two compose without overlap.

## Alternatives considered

- **Keep `ro`/`rw` and document "PR workflow" as user
  convention.** Rejected: hides the contract in users' heads.
  Different teams would interpret "subscribed" differently;
  maury can't help.
- **Add `pr` as a separate field (`mode: ro` + `contribute_via_pr: true`).**
  Rejected: the enum is the simpler representation. A
  separate field opens the door to weird combinations
  (`mode: rw` + `contribute_via_pr: true` would mean what?).
- **Use a different vocabulary** ("contributor", "reviewer",
  "subscriber"). Considered. Rejected: `pr` is the operational
  reality (writes happen through pull requests); naming the
  *role* over the *mechanism* is one indirection too many.
- **Make `pr` the universal write mode and deprecate `rw`.**
  (Force every contribution through PR review.) Rejected: too
  paternalistic. A user with `rw` on their own personal repo
  shouldn't have to PR-review themselves.

## Build-order placement

- **Manifest schema v3 + upgrade script** — small Phase 2
  follow-up.
- **`pr`-mode behavior in `maury review`** — Phase 7 (review
  UI).
- **`gh` integration for PR creation** — Phase 7.
- **`maury subscribe <url>`** convenience command for
  bootstrapping a `pr`-mode rules sublayer entry — Phase
  4-adjacent. Use-case framed in
  [ADR-0034](0034-published-subscribed-profiles.md);
  mechanics now in
  [ADR-0038](0038-precept-acquisition-model.md) (acquisition
  + advisory lifecycle) and
  [ADR-0037](0037-layer-taxonomy-and-repo-discovery.md)
  (rules-sublayer wiring in the marker file).

## Followups

- **Non-GitHub backends** (gitlab, gitea, codeberg) — same
  PR pattern via their respective CLIs (`glab`, `tea`, etc.).
  Per [ADR-0016](0016-pluggable-repo-backends.md), backend
  adapters expose this; maury's `pr`-mode logic stays
  generic. Not v1; lands when a non-GitHub backend lands.
- **Pre-PR validation hooks.** A team curator might want to
  enforce certain checks before accepting a PR (e.g.,
  finding has a `Source-Transcript` trailer, scope_hint is
  one of an allowed set). Today this is GitHub Actions on
  the team repo. v1.1 could add maury-side hooks that run
  before PR creation to catch failures locally.
- **PR squashing.** Currently `maury review` on `pr` mode
  pushes the review branch with multiple commits. Some
  curators prefer squashed PRs. v1.1: a `--squash` flag.

## Claude Code references

This ADR introduces no new Claude Code dependencies. The
`gh` CLI dependency is GitHub's, not Claude Code's.

## Amendment history

- 2026-05-11 — "profile" vocabulary renamed to "mode" per ADR-0037 doctoral examination. References to "profile" in this ADR now read "mode"; no semantic changes.
