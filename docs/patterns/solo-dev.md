# Pattern: Solo-dev maury install

A concrete recipe for the single-user case — one human, one or
more machines, no team coordination. The mirror of
[`team-upstream.md`](team-upstream.md), which covers the multi-
human / curator-and-consumer flow.

> **What this pattern is for:** you use Claude Code on more than
> one machine of your own (workstation + laptop, or workstation
> + homelab server, or any combination) and want one CLAUDE.md +
> settings + skills to stay current across all of them. Optionally
> with a hard partition between personal and work content so
> personal stuff cannot reach a work-issued device.

If you're new to maury, read [`elevator-pitch.md`](../elevator-pitch.md)
(2 minutes) or [`quickstart.md`](../quickstart.md) (5 minutes, hands-on
against the bundled seed). The recipe below assumes you've decided
maury is for you and want to set up a real install against your
own repos.

---

## Decision tree: how many modes do you need?

| Situation | Recommended setup |
|---|---|
| One human, all personal hardware, no work content involved | **One mode** (`personal`). One base repo + one mode repo. Simplest possible install. |
| One human, work-issued laptop in the mix, work content should NOT reach personal hardware (or vice versa) | **Two modes** (`personal`, `work`). One base repo + two mode repos. Per-host SSH deploy keys make the partition structural per [Tenet 3](../tenets.md#3-trust-boundaries-are-physical-not-policy). |
| One human, multiple client engagements that should stay isolated from each other | **Mode tree** — `personal`, `work`, `work:client-acme`, `work:client-globex`. See [`concepts.md` §5](../concepts.md#5-mode-tree-and-inheritance). |

Start with the simplest shape that works. You can add modes
later (see "Adding a second mode" below); the cost of starting
narrower is small.

The recipe below walks the **one-mode case first** (Steps 1-4),
then shows how to add a second host (Step 5) and a second mode
(Step 6). If you know upfront you need two modes, follow Steps
1-3 once for each and combine.

---

## Schema-gap note (read before Step 1)

Today's shipping CLI reads the **v2-flat-manifest** schema — one
`.meta/manifest.json` in the base repo that names every mode,
every host, and every per-host repo with its URL and access
mode. The target shape per
[ADR-0037](../adr/0037-layer-taxonomy-and-repo-discovery.md) is
**per-repo marker files** (`.meta/maury-marker.json` in each
repo with its own `layer`/`agency_id`/`sublayers`/`hosts`
fields), with discovery walking the marker graph from base
outward. The migration is tracked under
[ADR-0030](../adr/0030-manifest-schema-migrations.md) and not
yet shipped.

The recipe below uses **today's v2 shape**. When the marker
migration ships, the per-repo split below stays valid; the
metadata moves from one central manifest in the base repo to
one marker file per repo.

---

## Step 1 — Clone the bundled seed to copy from

You'll carve the seed into multiple real git repos in the next
two steps. First, clone maury locally to copy from:

```sh
git clone https://github.com/<org>/maury.git
ls maury/base-template
```

The seed has this shape:

```
base-template/
├── .meta/
│   ├── manifest.json     ← v2 manifest naming every mode + host + repo URL
│   └── rules.yaml        ← rule-engine seed
├── CLAUDE.md             ← universal CLAUDE.md content
├── settings.json         ← universal Claude Code settings
├── agents/  bin/  skills/   ← empty dirs (you fill these)
└── profiles/
    ├── home/             ← personal mode content (CLAUDE.md.fragment + hosts/)
    │   └── hosts/workstation/   ← per-host fragment for this host
    └── work/             ← work mode content
```

In v2, every mode lives as a subdirectory under `profiles/` in
the seed. In your production setup you'll **split each profile
subdirectory into its own real git repo** so the trust-boundary
isolation works (per
[ADR-0002](../adr/0002-repo-per-trust-boundary.md), one trust
boundary == one repo).

---

## Step 2 — Create your real git repos

For a single-mode personal-only setup, you need **two** real
repos:

```sh
# Base repo: the universal layer
gh repo create <your-username>/maury-base --private --clone

# Personal mode repo: your personal content
gh repo create <your-username>/maury-personal --private --clone
```

For a two-mode (personal + work) setup, you need **three** real
repos — and the work repo should live in a separate
provider/org so server-side ACLs enforce the partition:

```sh
# Base repo (anyone with read on base can read it):
gh repo create <your-username>/maury-base --private --clone

# Personal mode (your personal account):
gh repo create <your-username>/maury-personal --private --clone

# Work mode (employer org — different provider/account so the
# server-side access split is structural per ADR-0003):
gh repo create <your-employer-org>/maury-work --private --clone
```

Copy seed content into each:

```sh
# Base repo gets the universal layer + the manifest:
cp -R /path/to/maury/base-template/CLAUDE.md \
      /path/to/maury/base-template/settings.json \
      /path/to/maury/base-template/.meta \
      /path/to/maury/base-template/agents \
      /path/to/maury/base-template/bin \
      /path/to/maury/base-template/skills \
      maury-base/

# Personal-mode repo gets the personal profile content:
cp -R /path/to/maury/base-template/profiles/home/* maury-personal/

# (If using a work mode:) work repo gets the work profile content:
cp -R /path/to/maury/base-template/profiles/work/* maury-work/
```

Edit `maury-base/.meta/manifest.json` to replace the placeholder
`<your-username>` / `<your-employer-org>` / `<your-personal-email>`
strings with your actuals, and to set the per-host `repos` map
URLs to match the real repos you just created. Per
[ADR-0015](../adr/0015-surrogate-keys-for-hosts-and-profiles.md),
**regenerate the `profile_<hex>` and `host_<hex>` UUIDs** in the
manifest when forking the seed so your audit history is distinct
from upstream (e.g., `python3 -c "import uuid; print(uuid.uuid4().hex)"`
per key).

Commit + push each repo:

```sh
for r in maury-base maury-personal maury-work; do
  (cd $r && git add -A && git commit -m "Initial content from maury seed" && git push)
done
```

Edit `CLAUDE.md` and `.meta/rules.yaml` in the base repo to
match your own preferences (the seed has placeholder content
clearly marked; replace it). Same for the per-mode
`CLAUDE.md.fragment` files.

---

## Step 3 — Bootstrap your first host

Maury isn't on PyPI yet, so today's install is a developer-flow
clone-and-uv path (matches what `quickstart.md` documents). Per
[ADR-0018](../adr/0018-minimum-bootstrap-ux.md), the canonical
end-user path will become `pipx install maury` (or
`uv tool install maury`) once published.

On your workstation:

```sh
# If you don't already have the maury source from Step 1:
git clone https://github.com/<org>/maury.git
cd maury && uv sync

# Bootstrap from your base repo:
uv run maury init --from-dir /path/to/your/maury-base
```

The init flow per [ADR-0018](../adr/0018-minimum-bootstrap-ux.md):

1. Generates a fresh `host_<hex>` ID in `~/.maury-host-id`.
2. Reads your base repo's `.meta/manifest.json` to discover the
   layout.
3. Identifies this host: pre-registered if the manifest already
   lists this hostname/ID; otherwise prompts to register.
4. Walks the manifest's per-host `repos` map and prompts you to
   generate per-host SSH deploy keys for each repo
   ([ADR-0003](../adr/0003-per-host-deploy-keys.md)). You add
   each public key as a deploy key on the corresponding repo
   via the git provider's UI.
5. Runs the capability probe + first render under `~/.claude/`.

The drift-preflight per
[ADR-0018 §"Init drift preflight"](../adr/0018-minimum-bootstrap-ux.md#init-drift-preflight-added-2026-05-07)
refuses to silently overwrite any pre-existing `~/.claude/`
content. If your host has hand-managed `~/.claude/` content,
use `--check` first to see what init would do, then `--force`
only after you've audited the collision.

After init succeeds, your `~/.claude/` is maury-managed.

---

## Step 4 — Day-to-day loop

The daily-driver flow per [`workflow.md` §2](../workflow.md#2-daily-claude-code-session-loop).

**Calibration first** — not every step below is fully shipped:
- `maury sync` is 🟡 partial (drift detection not yet wired)
- `maury mine` is 🟡 partial (extractor + crossref shipped; run-branch generation pending)
- `maury review` is ⏳ planned
- Check [`status.md`](../status.md) for the authoritative shipped-vs-planned table before relying on any specific command.

Commands once they're all wired:

```sh
# At the start of a Claude Code work block, or any time you want
# to pull the latest from peer hosts:
uv run maury sync

# Use Claude Code normally. As you work, you may correct Claude,
# Claude may write via Edit/Write, you may hand-edit ~/.claude/
# directly — all three are first-class per Tenet 8.

# Periodically (weekly?), mine your transcripts for durable
# preference candidates:
uv run maury mine

# Review what mining proposed (planned per status.md):
uv run maury review <run-id>
```

---

## Step 5 — Add a second host

Months later, you get a new laptop. Setup is the same as Step 3
but you bootstrap from your existing base repo:

```sh
# On the new laptop:
git clone https://github.com/csmarshall/maury.git
cd maury && uv sync

uv run maury init --from-dir /path/to/your/maury-base
```

The init flow re-runs the per-host deploy-key step from Step 3:
the new laptop gets its OWN keypair for each repo it needs to
read or write, and you add each public key as a deploy key on
the corresponding repo via your git provider's UI. The
workstation's keys remain valid; deploy-key issuance is
per-host per [ADR-0003](../adr/0003-per-host-deploy-keys.md), so
each host is independently revocable.

If you've already used your workstation to register this new
host in the manifest (by hostname or proactive registration),
init recognizes it and reuses the entry. Otherwise init prompts
to register-new, writing a proposal that lands on next sync.

A planned bootstrap-snippet shortcut per
[ADR-0018](../adr/0018-minimum-bootstrap-ux.md)
(`maury bootstrap-snippet --target macos`, **not yet shipped** —
see [`status.md`](../status.md)) would emit a one-liner you
paste on the new laptop, eliminating the manual clone + init.
Until that ships, the manual path above works.

---

## Step 6 — Add a second mode later

Started solo with just `personal` and now joined a job that
needs a hard partition? You don't need to rebuild — add a new
mode repo + register your work laptop against it:

1. Create the new mode repo on your work git provider:
   `<work-org>/maury-work` (different provider from your personal
   repo so server-side ACLs enforce the partition).
2. Edit your base repo's manifest to add the new mode and the
   new host's entry. Per [ADR-0001](../adr/0001-n-profiles.md)
   N modes are first-class; you don't reconfigure to add one.
3. Bootstrap the work laptop against the new manifest. Init's
   register-new flow handles this.

The structural property — work content cannot reach a personal
host because the personal host doesn't hold the work repo's
deploy key — is enforced server-side from the moment you
generate per-host keys per
[ADR-0003](../adr/0003-per-host-deploy-keys.md). No client-side
discipline needed for the partition of **repo content** itself.

**Caveat that follows from this:** the partition for repo content
is structural; the partition for **Anthropic credentials when
mining** is operational, not structural. Per
[ADR-0041](../adr/0041-per-mode-anthropic-credentials.md), use a
work Anthropic account when working in `work` mode and a
personal Anthropic account in `personal` — Claude Code is
single-account-per-install today and maury can't enforce the
match. See [`security-model.md`](../security-model.md) for the
three-piece contract: the deploy-key isolation is piece 1
(structural); the per-mode-credential discipline is pieces 2 + 3
(operational, on you).

---

## What's NOT here vs. team setup

The solo-dev case is simpler than [`team-upstream.md`](team-upstream.md)
in three specific ways:

1. **No curator role.** You have `rw` on every repo you own; no
   one needs to review your changes before they land. Mining-run
   findings still go through `maury review` for your own
   sanity-check, but it's intra-personal (you reviewing your
   past self's proposals) rather than cross-human.

2. **No `pr`-mode workflow.** You don't need
   [ADR-0033](../adr/0033-pr-repo-mode.md)'s `pr` repo mode
   for your own repos — `rw` everywhere. The PR-flow is only
   relevant if you eventually consume someone else's `rules`
   repo (e.g., your employer's team rules), in which case
   [`team-upstream.md`](team-upstream.md) is the recipe to
   follow for that one sublayer.

3. **No governance metadata.** Solo-dev doesn't need
   `.meta/maury-governance.json` from
   [ADR-0038](../adr/0038-precept-acquisition-model.md) — that
   file declares PR review targets for `rules` repos with
   external consumers, which doesn't apply when you're the only
   consumer.

You DO still need:
- Per-host SSH deploy keys (one per host × repo combination).
- Genericized base-template seed as your starting content (or
  hand-write your own).
- Discipline about which Anthropic credential is active per mode
  ([`security-model.md`](../security-model.md)).

---

## Cross-references

- [`elevator-pitch.md`](../elevator-pitch.md) — 2-minute "is
  maury for me?" page.
- [`quickstart.md`](../quickstart.md) — try maury against the
  bundled seed before committing to a real install.
- [`concepts.md`](../concepts.md) — canonical vocabulary
  (mode, trust boundary, sublayer, marker file, etc.).
- [`security-model.md`](../security-model.md) — what maury's
  trust-boundary isolation actually buys you; the three-piece
  contract.
- [`workflow.md`](../workflow.md) — user-journey flowcharts
  including the daily-driver loop.
- [`status.md`](../status.md) — what's shipped today vs.
  planned. Authoritative.
- [`team-upstream.md`](team-upstream.md) — the multi-human
  counterpart (curator/consumer/PR flow).
- [ADR-0018](../adr/0018-minimum-bootstrap-ux.md) — `maury init`
  details + the bootstrap-snippet shortcut.
- [ADR-0039](../adr/0039-bootstrap-and-host-lifecycle.md) —
  current bootstrap flow, mode-scoped host identity, mode-
  change process.
