# ADR-0034: Published/subscribed profiles

**Status:** Accepted (mechanics superseded by [ADR-0037](0037-layer-taxonomy-and-repo-discovery.md) + [ADR-0038](0038-precept-acquisition-model.md); the use-case framing is preserved — see Amendment history at bottom)
**Date:** 2026-05-07

> **Reading this ADR:** the use-case framing (curator publishes team
> conventions; engineers subscribe + contribute back via PR) is still
> the right product story. The mechanics, however, are now expressed
> through [ADR-0037](0037-layer-taxonomy-and-repo-discovery.md)'s
> `rules` layer type and [ADR-0038](0038-precept-acquisition-model.md)'s
> acquisition / advisory model. Where this ADR says "publish a
> profile," read "publish a `rules` repo"; where it says "subscribe,"
> read "declare a `rules` sublayer in your mode's marker." The
> JSON examples below have been updated to the new schema.

## Related tenets

- [Tenet 1 — First, do no harm](../tenets.md#1-first-do-no-harm)
- [Tenet 3 — Trust boundaries are physical](../tenets.md#3-trust-boundaries-are-physical-not-policy)
- [Tenet 9 — Defer to the platform](../tenets.md#9-defer-to-the-platform)

## TL;DR

A real workflow earlier ADRs didn't address: a team curator publishes
shared maury content; engineers consume it, extend it, and contribute
back through curator-reviewed PRs. This ADR establishes the
**use-case layer** — curator-as-ongoing-role, multi-user properties,
discoverability, versioning, the contribution-back-via-PR flow.
Mechanics composed from ADR-0033 (`pr` repo mode) and a thin
`maury subscribe` convenience command. **Update 2026-05-13:** the
mechanical implementation has been superseded by
[ADR-0037](0037-layer-taxonomy-and-repo-discovery.md)'s `rules`
layer + [ADR-0038](0038-precept-acquisition-model.md)'s acquisition
model; this ADR's use-case framing is still the product story but
the JSON schema examples now reflect the per-repo marker model.

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
2. The repo's `.meta/maury-marker.json` declares it as a
   `rules` layer belonging to the curator's agency (per
   [ADR-0037](0037-layer-taxonomy-and-repo-discovery.md)).
   The curator's `agency_id` appears as a **provenance claim**
   on the rules repo — cross-agency consumption is expected
   and the field is informational on the consumer side.
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
maury subscribe <repo-url> [--repo-mode pr|ro]
```

This convenience command:

1. Adds the URL as a new `rules` sublayer entry in the
   engineer's active mode's `.meta/maury-marker.json`, with
   `repo_mode` defaulting to `pr` (per ADR-0033).
2. Generates a per-host deploy keypair for this rules repo,
   prints the public key, and prompts the user to add it on
   the upstream side (the standard per-host deploy-key flow
   per [ADR-0003](0003-per-host-deploy-keys.md)).
3. Pulls the repo and renders. The rules content is now part
   of the mode's render output per
   [ADR-0037](0037-layer-taxonomy-and-repo-discovery.md)'s
   attachment-point order.

Behind the scenes, this is identical to manually editing the
mode's marker file and running `maury sync` — but the
convenience helper captures the right defaults (`repo_mode:
pr`, deploy key generation) and reduces friction.

### Engineer's mode declares the team rules repo as a sublayer

After subscribe, the engineer's mode marker declares the team
rules repo as a sublayer with `repo_mode: pr` (per
[ADR-0037](0037-layer-taxonomy-and-repo-discovery.md)'s
marker schema):

```json
{
  "schema_version": 1,
  "layer": "mode",
  "agency_id": "550e8400-e29b-41d4-a716-446655440000",
  "sublayers": [
    {
      "url": "git@github.com:acme-corp/rules-team-engineering.git",
      "repo_mode": "pr"
    }
  ],
  "hosts": {
    "host_3f1a8b2c...": {
      "registered_at": "2026-05-08T10:00:00Z",
      "environment_tags": ["ubuntu", "laptop", "work-desk"]
    }
  }
}
```

So Alice's render composes: `base` + `mode:alice-engineering`
+ the `rules-team-engineering` sublayer (attached at her
mode's position in the chain, per ADR-0037's attachment-point
order). Her personal preferences refine the team conventions
([ADR-0019](0019-inheritance-semantics-refine-by-default.md)
refinement-by-default). Updates the curator pushes to
`rules-team-engineering` flow into Alice's render on her next
`maury sync`.

### Engineer contributes back via PR

When Alice's mining run ([ADR-0022](0022-branch-per-mining-run.md))
produces a finding she thinks belongs in the team profile
(not just hers), she:

1. During `maury review`, sets the `scope_hint` to
   `team-engineering` (her parent profile per
   [ADR-0045](0045-cross-trust-boundary-promotion.md)).
2. ADR-0045's graph check passes (team-engineering is an
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
- Two curators editing the *same* `.meta/maury-marker.json`
  file concurrently — handled by ADR-0024's structured-merge
  semantics, but the multi-curator case multiplies the
  marker-conflict frequency. v1 caveat: small curator teams
  are fine; larger teams may need additional coordination
  tooling.
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
  profile; ADR-0019 refinement applies; ADR-0045 promotion
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
- [ADR-0045](0045-cross-trust-boundary-promotion.md)'s
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

## Amendment history

- 2026-05-11 — "profile" renamed to "mode" per ADR-0037. The published/subscribed model is refined by ADR-0038 (rules layer + governance metadata); ADR-0038 is now the canonical reference for shared-rules acquisition.
- 2026-05-13 — schema examples updated to the per-repo marker file (`.meta/maury-marker.json` with `layer`/`agency_id`/`sublayers`/`hosts` fields per [ADR-0037](0037-layer-taxonomy-and-repo-discovery.md)). The old flat-manifest `profiles` map / `profile_<hex>` keys no longer exist as a schema; the modern model expresses "subscribe to a team profile" as **declaring a `rules` sublayer with `repo_mode: pr` or `ro`** in the consuming mode's marker. The use-case framing in the body is preserved; the mechanics are now expressed via [ADR-0037](0037-layer-taxonomy-and-repo-discovery.md) (`rules` layer + marker schema) and [ADR-0038](0038-precept-acquisition-model.md) (acquisition + governance metadata + override-advisory lifecycle). Reciprocal supersession-acknowledgement notes added to both ADRs the same day.
