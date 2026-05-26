# ADR-0018: Minimum bootstrap UX — `maury init`

**Status:** Accepted
**Date:** 2026-05-06
**Amended:**
- 2026-05-07 — step 6 ("first render") expanded with a drift
  preflight per [ADR-0017](0017-drift-detection-and-reconciliation.md).
  Init now refuses to silently overwrite pre-existing target-dir
  content and persists `last-render.json` after a successful apply.
  See §"Init drift preflight (added 2026-05-07)".

## Related tenets

- [Tenet 1 — First, do no harm](../tenets.md#1-first-do-no-harm)
- [Tenet 11 — Explicit beats implicit, with conservative defaults](../tenets.md#11-explicit-beats-implicit-with-conservative-defaults)

## TL;DR

A new host adopting maury should need the absolute minimum: one URL
(or a tarball for air-gap) plus an SSH key already on the host, with
`pipx install maury` as the only prereq. `maury init` pulls the base
repo, reads its marker file, self-identifies (or proposes a new host
entry), and bootstraps additional deploy keys. `maury bootstrap` is a
separate verb for curator-side agency ops. Trade-off: the user must
remember the base URL + auth credential out-of-band (password
manager) — maury can't solve that without a chicken-and-egg.

> **Note (2026-05-08):** [ADR-0039](0039-bootstrap-and-host-lifecycle.md)
> rewrites this ADR's bootstrap flow against the distributed-marker
> model from [ADR-0037](0037-layer-taxonomy-and-repo-discovery.md):
> there is no single authoritative manifest — each repo carries its
> own `.meta/maury-marker.json` declaring only its direct sublayers.
> The step-by-step flow below is preserved as historical record; ADR-0039
> is the source of truth for the current bootstrap flow.

## Context and Problem Statement

Self-directed onboarding for a new host needs to work with the
absolute minimum the user has to bring. Every extra step is friction;
every undocumented assumption is a future support burden.

The realistic minimum is **one credential + one URL**, plus
maury itself installed. We design around that.

<details>
<summary><b>Decision drivers</b> (4 items — click to expand)</summary>

- **Tenet 11:** explicit beats implicit, with conservative
  defaults. The init flow asks for what it needs (URL + key),
  doesn't assume.
- **Friction kills adoption.** Every extra step on first-host
  setup is a place users give up. The "what's the minimum?"
  framing is the right starting question.
- **Air-gap is real.** Work environments with no path from
  the new host to GitHub need a first-class tarball/dir
  ingestion path.
- **Curator vs. new-host commands are different audiences.**
  Conflating `maury init` and `maury bootstrap` makes both
  worse.

</details>

<details>
<summary><b>Considered options</b> (6 options — click to expand)</summary>

- **Option A:** `maury setup` (verb name choice).
- **Option B:** Auto-detect and install pipx as part of init.
- **Option C:** Sync the bootstrap info via maury itself
  (chicken-and-egg).
- **Option D:** Web-based "bootstrap server" with one-time
  tokens.
- **Option E:** Single hardcoded auth flavor across all
  backends.
- **Option F (chosen):** `maury init <base-repo-url>` (or
  `--from-tarball` / `--from-dir`) — one credential + one URL
  is the user's only mandatory input; pipx is a documented
  prereq; auth flavor is per-backend.

</details>

## Decision Outcome

**Chosen option:** Option F — `maury init` is the user's first
command on a new host. The minimum input is one URL (or one
tarball path, for air-gap) plus an SSH key already on the host.
This is the only option that preserves "minimum input" while
keeping bootstrap-info storage out of maury (a
chicken-and-egg problem maury can't solve cleanly).

### Implementation details

#### `maury init` is the user's first command on a new host

```sh
maury init <base-repo-url>
```

Or, for offline / air-gapped bootstrap:

```sh
maury init --from-tarball ~/Downloads/maury-base.tar.gz
maury init --from-dir /mnt/usb/maury-base/
```

**One of these three forms is required.** The init flow then:

1. **Self-identify.** Generates a fresh `host_<hex>` ID. Writes it to
   `~/.config/maury/host-id` (per ADR-0015). This file is created once and
   never modified — it's how this host identifies itself in the
   manifest from now on.
2. **Pull (or ingest) the base repo.** Online: clones the URL with
   the SSH key the user has configured (per ADR-0016, default
   transport auth is SSH for git/github backends in v1). Offline:
   unpacks the tarball or copies the directory.
3. **Read the base marker and walk outward.** The base repo's
   `.meta/maury-marker.json` declares the base's direct sublayers;
   each sublayer's marker declares its own direct sublayers; the
   traversal continues until every reachable layer in the agency
   has been discovered. There is no single authoritative manifest —
   the agency view is assembled by walking the marker graph (per
   ADR-0037).
4. **Decide who I am.** Two paths:
   - **Pre-registered:** the manifest already has a host entry whose
     `host_id` matches `~/.config/maury/host-id`. Render and exit. Most
     hosts will be in this state because the curator (or an existing
     host) added them ahead of time.
   - **New host:** prompt — "register this host as new (name?
     profile? push policy?)". Maury writes the entry as a proposal
     to `proposals/promote-to-base/host-<id>.json` in the base repo
     (which we have read-only, so the write goes to the repo we
     *do* have write access to — typically a profile repo). A
     curator host approves; on next sync, the manifest is updated
     and this host is officially registered.
5. **Bootstrap deploy keys for additional repos.** For each repo
   the host's manifest entry references, generate a per-host SSH
   keypair, print the public key, walk the user through adding it
   as a deploy key on that repo (or use `gh` if available and the
   `github` backend is in use).
6. **Run probe + first render.** `maury probe` + render under
   `~/.claude/`. The render goes through a drift preflight per
   ADR-0017 and **may refuse and exit 1** if the target dir has
   pre-existing content — see §"Init drift preflight (added
   2026-05-07)" below for the bootstrap-case collision behavior,
   the `--force`/`--non-interactive` flags, and the post-apply
   `last-render.json` write.

#### `maury bootstrap-snippet` for from-existing-host onboarding

To shortcut step 1 above, run on any existing host (e.g., workstation):

```sh
maury bootstrap-snippet [--target macos|ubuntu|freebsd|linux]
```

Emits a one-liner the user can paste on the new laptop. The
generated line includes:
- OS-specific install of `pipx` (if not already installed) — see
  install table below.
- `pipx install maury`
- `maury init <base-repo-url>` filled in with this agency's actual
  base URL.

#### Pre-maury install paths

The canonical path is `pipx install maury`. The README's install
section leads with OS-specific instructions for getting pipx itself,
then converges on the same `pipx install` line:

| OS | Get pipx |
|---|---|
| macOS (Homebrew) | `brew install pipx` |
| macOS (MacPorts) | `port install pipx` |
| Ubuntu / Debian 24.04+ | `apt install pipx` |
| Ubuntu / Debian older | `python3 -m pip install --user pipx && python3 -m pipx ensurepath` |
| Fedora | `dnf install pipx` |
| Arch | `pacman -S python-pipx` |
| FreeBSD | `pkg install py311-pipx` |
| Generic / fallback | `python3 -m pip install --user pipx && python3 -m pipx ensurepath` |

`uv tool install maury` works as an equivalent on hosts that already
have `uv` installed (and on FreeBSD it's actually one binary fewer
to track since `uv` is what we already use for development).

#### CLI verb separation

- **`maury init`** — user's first command on a new machine.
- **Curator-side ops** — shipped: `maury agency init` (creates
  the agency marker + initial commit), `maury mode bootstrap`
  (registers a new host against an existing mode in the
  manifest; the pre-release `maury bootstrap host` CLI alias
  was retired 2026-05-19), `maury repo init` (per ADR-0038,
  rules-repo lifecycle). Future curator surfaces (snippet
  generators, etc.) belong to this same audience but are
  planned, not shipped.

Different verbs, different audiences, no overlap.

#### Cold-start recovery

The user is responsible for remembering or storing the base repo URL
and one auth credential. Recommended: password manager entry
("maury bootstrap: URL=git@github-base:exampleuser/maury-base.git;
SSH key in ~/.ssh/maury-bootstrap"). Maury does NOT build a
bootstrap-info-sync mechanism for this — that's a chicken-and-egg
problem (where would the info live before maury exists?) and outside
maury's scope.

For air-gapped recovery, the tarball form (`--from-tarball` /
`--from-dir`) is the canonical path. Generate one periodically with
`maury bundle base` (also v1 scope, small command) and store
wherever the user backs up sensitive data.

#### Auth flavor

V1 default for the `git` and `github` backends is **SSH key**. The
user generates a keypair (or uses an existing one), pastes the
public key as a deploy key on the base repo via the GitHub UI,
provides the URL to `maury init`. OAuth device flow and HTTPS+PAT
are candidates for v2 niceness but not v1 scope.

For non-git backends per ADR-0016 (Perforce, S3+age, etc.), each
adapter brings its own auth model.

#### What the init flow *doesn't* do (v1)

- **It does not auto-install `claude` itself.** maury depends on
  Claude Code already being installed and configured.
- **It does not auto-install Python.** The pipx install step
  assumes Python 3.11+ is on PATH.
- **It does not generate or push base content.** Init pulls an
  existing base; it doesn't bootstrap an agency from scratch. For
  that, see `maury agency init` (shipped 2026-05-18) for the
  agency-level marker + initial commit; a future `maury repo
  init` / curator-side new-base-repo command is planned per
  ADR-0038.

#### Init drift preflight (added 2026-05-07)

The original v1 spec had step 6 render directly into the target
dir. That violates Tenet 1 ("first, do no harm") on any host
that already has a `~/.claude/` — for example, the user has been
hand-managing `CLAUDE.md` for months and is now adopting maury
to share it across hosts. Silent overwrite would lose that work.

Init now applies the same three-flow drift policy described in
[ADR-0017 §"Sync flow with drift"][017-flows], with one extra
case for the no-baseline state:

| State | What init does |
|---|---|
| Target dir is empty / nonexistent | Render and apply normally. Persist `last-render.json` post-apply. |
| Target dir has a `last-render.json` baseline (re-init) | Standard `detect_drift` — same paths as `maury sync`. The three flows below apply. |
| Target dir has pre-existing files but no baseline (bootstrap-case collision) | Extra case specific to init: scan rendered file paths against on-disk content; any mismatch is a collision. The three flows below apply, with collision-specific phrasing. |

The three flows (table reproduced from ADR-0017's sync flows so
this ADR is self-contained; the underlying policy is shared):

| Flag | Behavior |
|---|---|
| `maury init` (default) | Refuse on any drift or bootstrap-case collision. Point user at `--force` / `--check`. Exit 1 with a list of colliding paths. |
| `maury init --non-interactive` | Refuse, exit 1. Cron/CI safe. Same set of conditions as default; differs only in the error phrasing (which mentions the flag explicitly so logs are diagnosable). |
| `maury init --force` | Clobber with a loud warning to stderr. Hand-edits or pre-existing content are overwritten. Rare manual override; appropriate when the user has already audited the target dir. |

`--force` and `--non-interactive` are mutually exclusive; the
check is a hand-rolled `ClickException` raised at the top of the
`init` command body, before the `init()` call. (Click 8 doesn't
have first-class option-group exclusivity, so the check is in
maury's code, not Click's.)

After a successful apply (whether default-clean or `--force`),
init persists `last-render.json` to
`<target>/maury-state/last-render.json` per
[ADR-0029](0029-maury-state-layout-contract.md), so subsequent
`maury sync` runs can detect drift against the init-rendered
state. Without this, a host's first sync after init had no
baseline and silently fell into ADR-0017's "first-time render"
no-drift branch — a hole in the safety story.

`--check` (dry-run) still exercises the drift preflight in full
and reports the would-be refusal; it just skips the actual
write and the baseline persist.

[017-flows]: 0017-drift-detection-and-reconciliation.md#sync-flow-with-drift

### Consequences

- ✅ **Good:** One credential + one URL is the minimum the
  user must bring, plus pipx-installable maury.
- ✅ **Good:** Clean separation of new-user vs curator
  commands — `maury init` always means "I'm a new host
  wanting to join an existing agency"; `maury bootstrap`
  always means "I'm setting up new agency infrastructure."
- ✅ **Good:** Air-gapped path is first-class, via
  `--from-tarball` and `--from-dir`. Critical for work
  environments where the new host can't directly reach
  GitHub.
- ✅ **Good:** `bootstrap-snippet` removes friction for the
  from-existing-host case — paste one line, get a working
  maury install + first sync.
- ✅ **Good:** Manifest-from-base is the source of truth for
  everything else about the new host. Once init's step 3
  completes, maury knows which other repos to clone, what
  deploy keys to generate, etc.
- ✅ **Good (added 2026-05-07):** Tenet 1 holds across init,
  not just sync. A host adopting maury onto a populated
  `~/.claude/` no longer risks silent loss of its pre-maury
  state.
- ❌ **Bad:** User must remember/store the base repo URL +
  one auth credential out-of-band (password manager
  recommended). Maury can't solve this without a chicken-
  and-egg.

### Confirmation

- `maury init <url>`, `maury init --from-tarball <path>`, and
  `maury init --from-dir <path>` ship in Phase 4.
- `bootstrap-snippet` emits a one-liner with the agency's
  base URL filled in.
- README install table covers the documented pipx-install
  paths per OS.
- (added 2026-05-07) `maury init` refuses on bootstrap-case
  collisions by default; `--force` and `--non-interactive`
  flags wired and mutually exclusive; `last-render.json`
  written post-apply. Implemented in
  `src/maury/bootstrap/init_cmd.py` with tests in
  `tests/unit/test_init.py` and `tests/unit/test_cli_init.py`.

<details>
<summary><b>Pros and cons of the options</b> (per-option ✅/❌ — click to expand)</summary>

#### Option A: `maury setup` (verb name)

- ✅ **Good:** Slightly more descriptive of "first-time
  configuration."
- ❌ **Bad:** `init` is shorter and well-known from
  `git init`/`npm init`/etc. Lower learning tax.

#### Option B: Auto-detect and install pipx

- ✅ **Good:** One fewer step on first install.
- ❌ **Bad:** Too much OS-detection logic for a one-time
  operation; documentation + bootstrap-snippet per OS is
  enough.

#### Option C: Sync the bootstrap info via maury itself

- ✅ **Good:** Self-contained recovery story.
- ❌ **Bad:** Chicken-and-egg — where would the info live
  *before* maury exists? Falls apart at the first cold
  bootstrap.

#### Option D: Web-based "bootstrap server" with one-time tokens

- ✅ **Good:** "Scan from your phone" UX is genuinely nice.
- ❌ **Bad:** Scope creep for v1; pipx + URL + SSH key
  already works.
- ⚖️ **Neutral:** Worth revisiting in v2 once core ships.

#### Option E: Single hardcoded auth flavor across all backends

- ✅ **Good:** Less per-backend variation.
- ❌ **Bad:** Conflicts with [ADR-0016](0016-pluggable-repo-backends.md)
  — auth is per-backend by design.

#### Option F (chosen): `maury init` + per-backend auth

- ✅ **Good:** Minimum input; clean verb separation;
  air-gap first-class.
- ❌ **Bad:** User stores bootstrap info themselves; no
  built-in recovery.

</details>

## Build-order placement

`maury init` lands in **Phase 4 (bootstrap commands)**
alongside `maury mode bootstrap` (the pre-release `bootstrap
host` alias was retired 2026-05-19) and the future `bootstrap
repo`. The probe mechanism (Phase 2 — already shipped) and
the render engine (Phase 3 — already shipped) are prerequisites.

## Followups

- **`maury bundle base`** (small command, v1) — produces a
  tarball of the base repo for offline distribution to new hosts.
- **One-time bootstrap-token UX** (v2) — `maury bootstrap-token`
  on workstation emits a signed token containing URL + temporary access
  key; `maury init <token>` on the new host needs nothing else.
  Requires a token-signing key kept by the curator.

## Amendment history

- 2026-05-11 — "fleet" renamed to "agency" per ADR-0037 doctoral examination. No semantic changes to bootstrap flow.
- 2026-05-13 — `.meta/manifest.json` → `.meta/maury-marker.json`; step 3 reworded to reflect the distributed-marker model from [ADR-0037](0037-layer-taxonomy-and-repo-discovery.md). Note added at top deferring to [ADR-0039](0039-bootstrap-and-host-lifecycle.md) as the source of truth for current bootstrap UX.
- 2026-05-19 — pre-release cleanup: the legacy `maury bootstrap
  host` CLI alias was retired (no users exist to deprecate from).
  Curator-side host registration ships only as `maury mode
  bootstrap`. CLI verb-separation section updated accordingly.
