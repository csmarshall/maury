# Pattern: Team-published maury rules repo

A concrete recipe for the workflow described by
[ADR-0033](../adr/0033-pr-repo-mode.md) (`pr` repo mode),
[ADR-0034](../adr/0034-published-subscribed-profiles.md) (the
use-case framing), and
[ADR-0037](../adr/0037-layer-taxonomy-and-repo-discovery.md) +
[ADR-0038](../adr/0038-precept-acquisition-model.md) (the
current layer taxonomy and rules-acquisition model that
supersede ADR-0034's original mechanics).

> **What this pattern is for:** a team curator wants to publish
> shared maury content (CLAUDE.md fragments, hooks, skills,
> conventions) for engineers to consume. Engineers can
> contribute back, but writes go through PR review by the
> curator.

If you're new to maury, read [`docs/concepts.md`](../concepts.md)
first — especially §2 (layer types — `base`/`mode`/`rules`),
§4 (trust boundary), §5 (mode tree), and §9 (`repo_mode`).

---

## Roles

- **Curator** — owns the team `rules` repo with `rw` access.
  Reviews PRs from consumers. Typically a single person or a
  small team (technical lead, platform team).
- **Consumer** — an engineer whose mode declares the team
  rules repo as a sublayer with `repo_mode: pr`. Reads freely;
  contributes through PRs (per
  [ADR-0033](../adr/0033-pr-repo-mode.md)).

A given person can be a curator on one host (their main work
machine with `rw` deploy keys) and a consumer on another
(their laptop with `ro` or `pr` deploy keys + `gh` auth for
PRs). The distinction is per-deploy-key, not per-human.

---

## Curator setup (one-time)

### Step 1 — Create the team rules repo

A new git repo (typically on the curator's organization's
GitHub) following the maury repo-per-trust-boundary structure
([ADR-0002](../adr/0002-repo-per-trust-boundary.md)):

```
acme-corp/rules-team-engineering/
├── .meta/
│   └── maury-marker.json   ← declares this repo as a `rules` layer
├── CLAUDE.md               ← the team's shared CLAUDE.md content
├── settings.json           ← team-defaulted settings (optional)
├── skills/
│   └── jira-ticket-format/SKILL.md   ← team-specific skills
├── hooks.yaml              ← team-managed hook actions
└── ...
```

The marker declares this as a `rules` layer
([ADR-0037](../adr/0037-layer-taxonomy-and-repo-discovery.md)):

```json
{
  "schema_version": 1,
  "layer": "rules",
  "agency_id": "550e8400-e29b-41d4-a716-446655440000",
  "sublayers": []
}
```

The `agency_id` is the curator's agency — on a `rules` repo
it is a **provenance claim** only (informational; consumers
in other agencies can use this rules repo freely).

Curators who want to expose a `pr` contribution path should
also publish governance metadata as
`.meta/maury-governance.json` per
[ADR-0038](../adr/0038-precept-acquisition-model.md) —
declaring owners and the PR target so consumers' tooling
can route contributions correctly.

### Step 2 — Declare the rules repo as a sublayer of the curator's mode

On the curator's main work host, edit the mode marker
(usually `mode:work` or `mode:work:acme`) to add the new
rules repo as a sublayer with `repo_mode: rw`:

```json
{
  "schema_version": 1,
  "layer": "mode",
  "agency_id": "550e8400-e29b-41d4-a716-446655440000",
  "sublayers": [
    {
      "url": "git@github.com:acme-corp/rules-team-engineering.git",
      "repo_mode": "rw"
    }
  ],
  "hosts": { "host_curator123...": { "registered_at": "..." } }
}
```

`repo_mode: rw` denotes **convention semantics** — the
curator owns the rules repo. No override advisory fires when
the curator's mode content shadows rules content (overriding
your own conventions is meaningless per
[ADR-0038](../adr/0038-precept-acquisition-model.md)).

Run `maury sync`. Maury clones the new rules repo with
the curator host's `rw` deploy key.

### Step 3 — Push the initial content

```sh
git -C ~/.config/maury/repos/rules-team-engineering add .
git -C ~/.config/maury/repos/rules-team-engineering commit -m "Initial team rules content"
git -C ~/.config/maury/repos/rules-team-engineering push
```

(The local clone path follows maury's standard
`<repos_root>/<repo-nickname>` layout — see
[ADR-0029](../adr/0029-maury-state-layout-contract.md) for
the full directory contract.)

That's it — the rules repo is now published. There's no
maury-specific "publish" command; publishing is just having
a repo that other hosts can declare as a sublayer.

### Step 4 — Tell engineers about it

Out-of-band: post the URL in your team Slack, add it to
onboarding docs, etc. The maury v1 design is
discoverability-via-existing-channels; a maury-side rules
registry is a planned-but-not-built future enhancement
(see [ADR-0034 §Discoverability](../adr/0034-published-subscribed-profiles.md#discoverability)).

---

## Engineer (consumer) setup

### Step 1 — Subscribe

```sh
maury subscribe git@github.com:acme-corp/rules-team-engineering.git \
  [--repo-mode pr|ro]
```

`--repo-mode` defaults to `pr` (you can contribute back via
PR per [ADR-0033](../adr/0033-pr-repo-mode.md)). Pass `--repo-mode
ro` if you only need to consume.

This:

1. Adds the URL as a new `rules` sublayer entry in your active
   mode's `.meta/maury-marker.json`, with the chosen
   `repo_mode`.
2. Generates a per-host deploy keypair for *this host* on
   *this repo*. Prints the public key.
3. Prompts you to add the public key to the upstream repo's
   deploy keys (curator side: GitHub repo settings → Deploy
   keys → Add).
4. Pulls the rules repo and renders.

### Step 2 — Configure your `gh` auth (one-time)

Per [ADR-0033 §"`gh` auth identity vs deploy-key account"](../adr/0033-pr-repo-mode.md):

```sh
gh auth status                # check what's authenticated
gh auth login                 # authenticate as the right account
```

**Important:** the `gh` auth account is the one your future
PRs will be authored as. For team repos, ensure it matches
your work GitHub account, not your personal one.

### Step 3 — Compose into your render

`maury subscribe` already updated your mode's marker to
include the team rules repo. After the next `maury sync`,
the engineer's render composes:

```
base → mode:alice-engineering (with rules-team-engineering attached as a sublayer)
```

The rules sublayer slots in at the attachment point of the
mode that declared it, per
[ADR-0037](../adr/0037-layer-taxonomy-and-repo-discovery.md)'s
attachment-point render order. Your personal mode content
refines the team conventions per
[ADR-0019](../adr/0019-inheritance-semantics-refine-by-default.md);
because the team rules sublayer has `repo_mode: pr` (precept
semantics), an override advisory fires if your personal mode
content shadows team-rules content — informational,
acknowledgeable, non-blocking
([ADR-0038](../adr/0038-precept-acquisition-model.md)).

### Step 4 — Use Claude Code as normal

Nothing changes in your day-to-day session. Claude Code reads
your composed `~/.claude/` content; the team-engineering
fragments are now in your CLAUDE.md alongside whatever you
put in your personal mode.

---

## Engineer contributes back: the PR flow

When you (Alice) discover a useful pattern via mining
([ADR-0026](../adr/0026-profile-aware-mining.md)) that you
think belongs in the team rules repo (not just your personal
mode):

```sh
maury mine                            # produces a maury/run/<run-id> branch
maury review <run-id>                 # walk the commits
```

In the review UI, when accepting a finding that should land
in the team rules repo:

1. Target the `rules-team-engineering` sublayer.
2. ADR-0033's `pr`-mode terminal step kicks in: maury pushes
   the review branch to the team rules repo and opens a PR
   via `gh pr create` (routed per the team's
   `.meta/maury-governance.json` from
   [ADR-0038](../adr/0038-precept-acquisition-model.md)).
3. Maury prints the PR URL. You open it in your browser and
   add any extra context.

The curator reviews on GitHub. They merge → on your next
`maury sync`, the change pulls into your render via the
sublayer. The curator never had to coordinate with you
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
  `Source-Mode` ([ADR-0026](../adr/0026-profile-aware-mining.md))
  and `Content-Hash` ([ADR-0022](../adr/0022-branch-per-mining-run.md))
  trailers, and a link to the local review session.
- **Diff:** the proposed change to team rules CLAUDE.md /
  hooks / skills.

Standard review: comment, request changes, approve, merge.
No maury-specific tooling on the curator side beyond what
maury already produces in the PR body.

---

## Versioning the team rules repo

By default, consumers track HEAD — every `maury sync` pulls
the latest. The curator pushes; consumers see the change on
their next sync.

To pin a consumer to a specific version (e.g., "we audited
rules-team-engineering as of v1.2.0; don't auto-upgrade until
we review the next batch"):

```sh
# At subscribe time:
maury subscribe <url> --pin v1.2.0

# Later, explicitly update the pin:
maury subscription update rules-team-engineering
```

Curator-side: maintain semver tags on the rules repo
(`v1.0.0`, `v1.1.0`, `v2.0.0`) so consumers can pin
meaningfully. This is a team-policy choice, not maury's
responsibility.

---

## What this pattern doesn't do

- **No registry.** Engineers learn about team rules repos via
  out-of-band channels (Slack, onboarding docs).
- **No maury-side notifications.** When the curator pushes
  new content, consumers see it on their next `maury sync`.
  Use GitHub's "watch repo" feature if you want push
  notifications.
- **No fine-grained per-consumer permissions.** Every consumer
  declares the same `repo_mode` per their own mode marker
  (`pr` or `ro`). Finer-grained controls are GitHub's
  deploy-key + branch-protection territory, not maury's.
- **No consumer compliance auditing.** Maury doesn't track
  "which consumers are on which version." A curator can
  derive this externally if needed.

---

## Cross-references

- [ADR-0033](../adr/0033-pr-repo-mode.md) — the `pr` repo
  mode this pattern depends on.
- [ADR-0034](../adr/0034-published-subscribed-profiles.md) —
  the use-case framing (now expressed in terms of the
  layer/rules taxonomy from ADR-0037/0038).
- [ADR-0037](../adr/0037-layer-taxonomy-and-repo-discovery.md)
  — the `rules` layer type, marker schema, and attachment-
  point render order.
- [ADR-0038](../adr/0038-precept-acquisition-model.md) —
  the rules-acquisition model, governance metadata, and
  override-advisory lifecycle.
- [ADR-0009](../adr/0009-promotion-only-cross-boundary.md) —
  for the cross-trust-boundary promotion case (when you want
  to push content from one mode to a different trust
  boundary, e.g., to base).
- [ADR-0027](../adr/0027-cross-context-promotion-via-shared-root.md)
  — for the mode-tree constraint that makes ancestor-only
  promotion safe.
- [ADR-0019](../adr/0019-inheritance-semantics-refine-by-default.md)
  — for refinement-vs-replacement at render time.
- [`docs/concepts.md` §9](../concepts.md#9-repo_mode-access-subtype)
  — for the three-mode (`rw`/`pr`/`ro`) framing this
  pattern instantiates.
