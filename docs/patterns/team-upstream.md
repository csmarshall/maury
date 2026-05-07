# Pattern: Team-published maury profile

A concrete recipe for the workflow described by
[ADR-0033](../adr/0033-pr-repo-mode.md) (`pr` repo mode) and
[ADR-0034](../adr/0034-published-subscribed-profiles.md)
(published/subscribed profiles).

> **What this pattern is for:** a team curator wants to publish
> shared maury content (CLAUDE.md fragments, hooks, skills,
> conventions) for engineers to subscribe to. Engineers can
> contribute back, but writes go through PR review by the
> curator.

If you're new to maury, read [`docs/concepts.md`](../concepts.md)
first — especially §2 (trust boundary), §3 (inheritance), §6
(inheritance access mode).

---

## Roles

- **Curator** — owns the team profile's repo with `rw`
  access. Reviews PRs from subscribers. Typically a single
  person or a small team (technical lead, platform team).
- **Subscriber** — an engineer with `pr` mode access to the
  team repo. Reads freely; contributes through PRs.

A given person can be a curator on one host (their main work
machine with `rw` deploy keys) and a subscriber on another
(their laptop with `ro` deploy keys + `gh` auth for PRs). Per
[ADR-0034](../adr/0034-published-subscribed-profiles.md), the
distinction is per-deploy-key, not per-human.

---

## Curator setup (one-time)

### Step 1 — Create the team repo

A new git repo (typically on GitHub) following the maury
repo-per-trust-boundary structure
([ADR-0002](../adr/0002-repo-per-trust-boundary.md)):

```
acme/maury-team-engineering/
├── .meta/
│   └── manifest.json       ← declares the team-engineering profile(s)
├── CLAUDE.md               ← the team's shared CLAUDE.md content
├── settings.json           ← team-defaulted settings (optional)
├── skills/
│   └── jira-ticket-format/SKILL.md   ← team-specific skills
├── hooks.yaml              ← team-managed hook actions
└── ...
```

The manifest declares the published profile(s). Keep it minimal
to start:

```json
{
  "version": 3,
  "profiles": {
    "profile_<id>": {
      "name": "team-engineering",
      "extends": null
    }
  },
  "hosts": {}
}
```

Note: `version: 3` — the team repo's manifest schema must be
v3 or later because subscribers will use `pr` mode (per
[ADR-0033](../adr/0033-pr-repo-mode.md)).

### Step 2 — Bootstrap the curator's host

The curator runs `maury init` against the new repo, gets the
host registered with `rw` deploy keys, and starts populating
content. Standard maury setup; no team-specific tooling.

### Step 3 — Push the initial content

```sh
git -C ~/.config/maury/repos/maury-team-engineering add .
git -C ~/.config/maury/repos/maury-team-engineering commit -m "Initial team-engineering content"
git -C ~/.config/maury/repos/maury-team-engineering push
```

(The local clone path follows maury's standard
`<repos_root>/<repo-nickname>` layout — see
[ADR-0029](../adr/0029-maury-state-layout-contract.md) for
the full directory contract. The repo nickname is whatever
you registered in the manifest; here it's the repo's
GitHub-side basename.)

That's it — the profile is now published. There's no maury-
specific "publish" command; publishing is just having a repo
that other hosts can subscribe to.

### Step 4 — Tell engineers about it

Out-of-band: post the URL in your team Slack, add it to
onboarding docs, etc. The maury v1 design is
discoverability-via-existing-channels; a maury-side profile
registry is a planned-but-not-built future enhancement
(see [ADR-0034 §Discoverability](../adr/0034-published-subscribed-profiles.md#discoverability)).

---

## Engineer (subscriber) setup

### Step 1 — Subscribe

```sh
maury subscribe https://github.com/acme/maury-team-engineering.git \
  [--as-profile <local-name>]
```

The `--as-profile` flag is **optional**. Without it, the
subscribed profile uses whatever name the upstream curator
declared in their manifest. Pass it only if you need to
disambiguate (e.g., subscribing to two different teams that
both call their profile `team-engineering`).

This:

1. Adds the URL to your local manifest as a `pr`-mode repo
   entry.
2. Generates a read-only deploy keypair for *this host* on
   *this repo*. Prints the public key.
3. Prompts you to add the public key to the upstream repo's
   deploy keys (curator side: GitHub repo settings → Deploy
   keys → Add).
4. Pulls the team repo and renders.

### Step 2 — Configure your `gh` auth (one-time)

Per [ADR-0033 §"`gh` auth identity vs deploy-key account"](../adr/0033-pr-repo-mode.md):

```sh
gh auth status                # check what's authenticated
gh auth login                 # authenticate as the right account
```

**Important:** the `gh` auth account is the one your future
PRs will be authored as. For team repos, ensure it matches
your work GitHub account, not your personal one.

### Step 3 — Extend the team profile with your personal one

Edit your manifest to make your personal profile extend the
subscribed team profile:

```json
{
  "profiles": {
    "profile_<your_id>": {
      "name": "alice-engineering",
      "extends": "profile_<team_id>"
    },
    "profile_<team_id>": {
      "name": "team-engineering"
    }
  }
}
```

After this, `maury sync` walks: **base → team-engineering →
alice-engineering → alice-host-overlay**. The "base" here is
the team-engineering repo's own `base` content (what the
curator put at the root of the team profile's inheritance
tree); subscribers don't have their own separate `base` —
they inherit through the team's tree. Your personal
preferences refine the team conventions (per
[ADR-0019](../adr/0019-inheritance-semantics-refine-by-default.md)).

### Step 4 — Use Claude Code as normal

Nothing changes in your day-to-day session. Claude Code reads
your composed `~/.claude/` content; the team-engineering
fragments are now in your CLAUDE.md alongside whatever you
put in your personal layer.

---

## Engineer contributes back: the PR flow

When you (Alice) discover a useful pattern via mining
([ADR-0026](../adr/0026-profile-aware-mining.md)) that you
think belongs in the team profile (not just your personal one):

```sh
maury mine                            # produces a maury/run/<run-id> branch
maury review <run-id>                 # walk the commits
```

In the review UI, when accepting a finding that should land
in `team-engineering`:

1. Set `scope_hint: team-engineering`.
2. ADR-0027's graph check passes (team-engineering is an
   ancestor of alice-engineering — the inheritance chain is
   `base → team-engineering → alice-engineering`).
3. ADR-0033's `pr`-mode terminal step kicks in: maury pushes
   the review branch to the team repo and opens a PR via
   `gh pr create`.
4. Maury prints the PR URL. You open it in your browser and
   add any extra context.

The curator reviews on GitHub. They merge → on your next
`maury sync`, the change pulls into your render via
inheritance. The curator never had to coordinate with you
beyond the standard PR review.

---

## Curator's PR review workflow

The curator sees the PR like any GitHub PR:

- **Title:** auto-generated by maury, includes the source
  finding's `Kind` and a one-line summary.
- **Body:** auto-generated by maury per
  [ADR-0033 §"`maury review` behavior on a `pr`-mode repo"](../adr/0033-pr-repo-mode.md).
  Includes mining-run metadata (run-id, host_id, mining
  timestamp), summary of accepted findings with their
  `Source-Profile` ([ADR-0026](../adr/0026-profile-aware-mining.md))
  and `Content-Hash` ([ADR-0022](../adr/0022-branch-per-mining-run.md))
  trailers, and a link to the local review session.
- **Diff:** the proposed change to team CLAUDE.md / hooks /
  skills.

Standard review: comment, request changes, approve, merge.
No maury-specific tooling on the curator side beyond what
maury already produces in the PR body.

---

## Versioning the team profile

By default, subscribers track HEAD — every `maury sync` pulls
the latest. The curator pushes; subscribers see the change
on their next sync.

To pin a subscriber to a specific version (e.g., "we audited
team-engineering as of v1.2.0; don't auto-upgrade until we
review the next batch"):

```sh
# At subscribe time:
maury subscribe <url> --pin v1.2.0

# Later, explicitly update the pin:
maury subscription update team-engineering
```

Curator-side: maintain semver tags on the team repo (`v1.0.0`,
`v1.1.0`, `v2.0.0`) so subscribers can pin meaningfully. This
is a team-policy choice, not maury's responsibility.

---

## What this pattern doesn't do

- **No registry.** Engineers learn about team profiles via
  out-of-band channels (Slack, onboarding docs).
- **No maury-side notifications.** When the curator pushes
  new content, subscribers see it on their next `maury sync`.
  Use GitHub's "watch repo" feature if you want push
  notifications.
- **No fine-grained per-subscriber permissions.** Every
  subscriber has the same `pr` mode access (read + PR-
  contribute). Finer-grained controls are GitHub's deploy-key
  + branch-protection territory, not maury's.
- **No subscriber compliance auditing.** Maury doesn't track
  "which subscribers are on which version." A curator can
  derive this externally if needed.

---

## Cross-references

- [ADR-0033](../adr/0033-pr-repo-mode.md) — the `pr` repo
  mode this pattern depends on.
- [ADR-0034](../adr/0034-published-subscribed-profiles.md) —
  the use case this pattern implements.
- [ADR-0009](../adr/0009-promotion-only-cross-boundary.md) —
  for the cross-trust-boundary promotion case (when you want
  to push content from a team profile to a different trust
  boundary, e.g., to base).
- [ADR-0027](../adr/0027-cross-context-promotion-via-shared-root.md)
  — for the inheritance-graph constraint that makes
  ancestor-only promotion safe.
- [ADR-0019](../adr/0019-inheritance-semantics-refine-by-default.md)
  — for refinement-vs-replacement at render time.
- [`docs/concepts.md` §6](../concepts.md#6-inheritance-access-mode)
  — for the three-mode framing this pattern instantiates.
