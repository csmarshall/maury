# ADR-0034: Published/subscribed profiles

**Status:** Accepted
**Date:** 2026-05-07

## Related tenets

- [Tenet 1 — First, do no harm](../tenets.md#1-first-do-no-harm)
- [Tenet 3 — Trust boundaries are physical](../tenets.md#3-trust-boundaries-are-physical-not-policy)
- [Tenet 9 — Defer to the platform](../tenets.md#9-defer-to-the-platform)

## Context

Earlier ADRs assumed maury's user is a single human with
multiple hosts (e.g., a developer with a workstation, a server,
and a work laptop, all under the same human's control).
[ADR-0024](0024-manifest-concurrency-inclusive-merge.md)
explicitly framed multi-user as out of scope.

But there's a real, common, valuable workflow that breaks
that assumption: **a team curator publishes a maury profile
that engineers subscribe to.** Examples:

- "Our engineering team uses Jira; PRs must include the
  ticket number" — published as `acme-team-engineering`
  profile.
- "Our codebase requires this set of `Bash` permissions and
  this `code-review` skill" — published as
  `corp-shared-defaults`.
- An open-source maintainer publishes "Anthropic best-
  practices" as a profile that anyone can subscribe to.

The gap-K design discussion identified this pattern. ADR-0033
specifies the mechanical primitive (`pr` repo mode);
implementation is Phase 7. This ADR captures the **use case
layer**: the curator-as-ongoing-role pattern, multi-user
properties, discoverability, versioning, and the contribution
flow back via PR.

## Decision

### Roles

A published/subscribed setup has two distinct roles:

- **Curator(s)** — humans with `rw` access to the published
  profile's repo. They author the canonical content, review
  PRs from subscribers, merge or reject. Long-lived role.
- **Subscribers** — humans (and their hosts) with `pr` mode
  access (per [ADR-0033](0033-pr-repo-mode.md)) to the same
  repo. Read freely; contribute through PRs.

A single repo may have multiple curators (a team) and many
subscribers (everyone using the published profile). The
distinction is **per-deploy-key**, not per-human — a curator
who also wants to subscribe to their own published profile
on a different host can configure that host with `pr` mode
(read-only key on that host) even though their main host has
`rw`.

### How a curator publishes a profile

1. Curator creates a maury repo (e.g.,
   `github.com/acme/maury-team-engineering`) following
   [ADR-0002](0002-repo-per-trust-boundary.md)'s repo-per-
   trust-boundary structure.
2. The repo's `.meta/manifest.json` declares the published
   profile(s) using maury's normal manifest schema. Per
   [ADR-0001](0001-n-profiles.md), multiple profiles can
   live in one repo if the curator wants to publish a tree
   (e.g., `team-engineering` + child `team-eng-backend` +
   child `team-eng-frontend`).
3. Curator's host has `rw` deploy key for this repo;
   subscribers will get read-only deploy keys.
4. Curator pushes initial content. Done — the profile is
   published.

The curator runs maury normally on this repo: edits content,
commits, pushes. No special "publishing" command exists; the
publishing is just having a repo that other hosts can `pr`-
subscribe to.

### How an engineer subscribes

```sh
maury subscribe <repo-url> [--as-profile <name>]
```

This convenience command:

1. Adds the URL to the engineer's manifest as a new repo
   entry with `mode: pr` (per ADR-0033).
2. Generates a read-only deploy keypair for this host on
   this repo, prints the public key, and prompts the user
   to add it on the upstream side (the standard per-host
   deploy-key flow per [ADR-0003](0003-per-host-deploy-keys.md)).
3. Writes the maury repo's profile-name as a subscribed
   profile in the engineer's local manifest, with optional
   rename via `--as-profile`.
4. Pulls the repo and renders.

Behind the scenes, this is identical to manually editing the
manifest and running `maury init` — but the convenience helper
captures the right defaults (`mode: pr`, deploy key
generation) and reduces friction.

### Engineer's profile extends the team profile

After subscribe, the engineer's own personal profile typically
**extends** the team profile via the inheritance mechanism
([ADR-0001](0001-n-profiles.md), [ADR-0019](0019-inheritance-semantics-refine-by-default.md)):

```json
{
  "profiles": {
    "profile_3f1a8b2c...": {
      "name": "alice-engineering",
      "extends": "profile_a3f9c421..."
    },
    "profile_a3f9c421...": {
      "name": "team-engineering"
    }
  }
}
```

*(Profile IDs elided for brevity per [ADR-0015](0015-surrogate-keys-for-hosts-and-profiles.md);
each is a `profile_<32 hex>` surrogate key. `extends:`
references the parent's ID, not its name, so a name-rename
doesn't break inheritance.)*

So Alice's render composes: team-base + team-engineering +
alice-engineering + alice-host-overlay. Her personal
preferences refine the team conventions ([ADR-0019](0019-inheritance-semantics-refine-by-default.md)
refinement-by-default). Updates the curator pushes to
team-engineering flow into Alice's render on her next
`maury sync`.

### Engineer contributes back via PR

When Alice's mining run ([ADR-0022](0022-branch-per-mining-run.md))
produces a finding she thinks belongs in the team profile
(not just hers), she:

1. During `maury review`, sets the `scope_hint` to
   `team-engineering` (her parent profile per
   [ADR-0027](0027-cross-context-promotion-via-shared-root.md)).
2. ADR-0027's graph check passes (team-engineering is an
   ancestor of alice-engineering).
3. ADR-0033's `pr`-mode terminal step kicks in: maury pushes
   the review branch to the team repo and opens a PR via
   `gh pr create`.
4. A curator reviews the PR on GitHub, merges or rejects.
5. On Alice's next `maury sync`, the merged change pulls
   into her render via inheritance.

No special "promote-to-team" command needed; the existing
review flow + `pr` mode + inheritance graph compose into the
right behavior.

### Multi-user, with explicit out-of-scope

[ADR-0024](0024-manifest-concurrency-inclusive-merge.md)
explicitly punted on multi-user (lines 196-198, 263-265:
"out of scope per the project's target user … would build on
this design"). This ADR carves out the **subscriber-curator
slice** while leaving ADR-0024's concurrent-curator-edit
case still out of scope. Specifically:

**In scope:**
- Multiple humans configuring their own hosts with `pr`-mode
  subscriptions to the same upstream.
- Upstream curators (one or several) reviewing PRs from any
  subscriber.
- Standard GitHub PR semantics (multiple reviewers, merge
  conflicts, etc.).

**Still out of scope (per ADR-0024):**
- Two curators editing the *same* `.meta/manifest.json` file
  concurrently — handled by ADR-0024's structured-merge
  semantics, but the multi-curator case multiplies the
  manifest-conflict frequency. v1 caveat: small curator
  teams are fine; larger teams may need additional
  coordination tooling.
- Per-subscriber visibility into "who else is subscribed."
  Maury doesn't track subscriber lists; that's GitHub's
  problem (the deploy-key list).
- Per-subscriber permissions beyond `ro`/`pr`/`rw`. Maury's
  three-mode model is the abstraction; finer-grained
  permissions are out.
- Auditing of subscriber compliance ("did everyone update
  to the latest team-engineering version?"). Out of scope;
  curators can build this externally.

### Discoverability

How does an engineer learn that a published profile exists?
Today: **out of band.** Someone tells them. They get the URL
from team docs or a Slack message.

Future enhancement (v1.1+): a `maury subscribe --search
<query>` that hits a curated index of published profiles.
That index is a real piece of infrastructure that doesn't
exist today; building it would be its own ADR (forward gap:
**"maury profile registry" — not in the original A-K gap
walkthrough; would be gap-L if/when scoped**). v1 is
out-of-band-only.

### Versioning the published profile

The team-engineering repo is a git repo. Subscribers track
HEAD by default — every `maury sync` pulls the latest
content.

A subscriber who wants to **pin** to a specific version
(e.g., "we audited team-engineering as of commit abc123;
don't auto-upgrade until I review the next batch") can:

```sh
maury subscribe <url> --pin <ref>      # pin to specific commit/tag/branch
maury subscription update <repo>        # later, explicitly update the pin
```

(`maury subscription update` follows the noun-verb subcommand
pattern used elsewhere — e.g., `maury manifest resolve`,
`maury host rename`, `maury profile rename`.)

`--pin` is implemented as setting `repos.<nick>.ref` in the
manifest to the specified ref. Maury's `git pull` becomes
`git fetch && git checkout <ref>` for pinned repos. v1 ships
this option; the default is HEAD-tracking (no pin).

The `repos.<nick>.ref` field lands in **manifest schema v3**
alongside [ADR-0033](0033-pr-repo-mode.md)'s `pr`-mode enum
extension — the v2→v3 upgrade script (per
[ADR-0030](0030-manifest-schema-migrations.md)) is a no-op for
repos that don't use `ref` (absent field = HEAD-tracking).

Curator-side semver discipline (e.g., tagging team-engineering
releases as v1.0.0, v1.1.0) is out of maury's scope — it's a
team-policy choice, not maury's responsibility.

### What this ADR is NOT specifying

- **A central maury "profile registry" / marketplace.** Out
  of scope; published profiles are just git repos.
- **Curator-curated update notifications.** When a curator
  pushes new content, subscribers learn about it on their
  next `maury sync`. No maury-side notification mechanism.
- **Subscriber-curator chat / collaboration tools.**
  GitHub's PR review UI is the collaboration surface.

## Consequences

- **Team-shared maury profiles become a first-class workflow.**
  An organization can publish a profile and have engineers
  subscribe with friction-low setup (`maury subscribe <url>`).
- **The inheritance graph extends naturally to subscribed
  profiles.** Engineer's personal profile extends the team
  profile; ADR-0019 refinement applies; ADR-0027 promotion
  graph works the same way.
- **Multi-user maury is partially in scope** (subscribers +
  curators) but small teams of curators only — large-curator-
  team coordination remains out of scope per ADR-0024.
- **Discoverability is the weakest piece of the v1 design.**
  Out-of-band only. v1.1 followup.
- **`maury subscribe` is one new convenience command.**
  ~80 LOC; thin wrapper over manual manifest editing +
  `maury init`.
- **Curator-side has no maury-specific tooling.** They use
  maury normally on their `rw` repo. PR review happens on
  GitHub.

## Alternatives considered

- **Build a maury profile marketplace / registry.** Rejected
  for v1: a real registry is a separate distributed system
  with its own ADR set. Out-of-band discovery is enough to
  prove the model.
- **Require subscribers to fork the team repo.** Rejected:
  fork sprawl, hard to keep in sync, friction for the
  engineer. `pr` mode + deploy keys is the maury-native
  pattern.
- **Push notifications when curator updates.** Rejected:
  GitHub already does this (watch the repo). Maury doesn't
  need to reinvent.
- **A separate "subscribed" extends-keyword that's distinct
  from inheritance** (e.g., `subscribes_to: <profile>` vs
  `extends: <profile>`). Rejected: the only thing that's
  different about a subscribed profile is its repo's `pr`
  mode; the inheritance behavior is identical. Reusing
  `extends` keeps the model coherent.

## Build-order placement

Phase 5+ — depends on:

- [ADR-0033](0033-pr-repo-mode.md)'s `pr` mode shipping
  (manifest schema v3, review-flow conditional).
- [ADR-0009](0009-promotion-only-cross-boundary.md)'s
  cross-boundary promotion (Phase 9) — though for the
  intra-team case (subscribed profile is in the inheritance
  chain), promotion goes via `pr` mode without needing
  cross-boundary mechanics.
- `maury subscribe` command (Phase 4-adjacent).

## Followups

- **`maury subscribe --search` and a published-profile
  index.** v1.1 if user demand emerges.
- **Curator-side analytics** ("how many subscribers? what's
  the PR backlog?"). Out of scope for v1; a curator can
  derive this from GitHub.
- **Profile versioning conventions.** A best-practices doc
  for curators who want to ship semver-tagged published
  profiles. v1.1.
- **Cross-org subscriptions.** An engineer subscribes to a
  profile from a different organization (e.g., "anthropic-
  best-practices"). v1 supports this since it's just a
  different URL; the social/legal questions (license, IP)
  aren't maury's to answer.

## Claude Code references

This ADR introduces no new Claude Code dependencies.
Subscribed profiles flow through the same render engine as
any other profile per
[`cc-contract:startup-files-loaded`](../claude-code-contract.md#cc-contractstartup-files-loaded);
Claude Code is unaware that some content originated from a
team-shared upstream.
