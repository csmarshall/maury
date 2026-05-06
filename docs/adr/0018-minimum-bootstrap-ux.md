# ADR-0018: Minimum bootstrap UX — `maury init`

**Status:** Accepted
**Date:** 2026-05-06

## Related tenets

- [Tenet 11 — Explicit beats implicit, with conservative defaults](../tenets.md#11-explicit-beats-implicit-with-conservative-defaults)

## Context

Self-directed onboarding for a new host needs to work with the
absolute minimum the user has to bring. Every extra step is friction;
every undocumented assumption is a future support burden.

The realistic minimum is **one credential + one URL**, plus
maury itself installed. We design around that.

## Decision

### `maury init` is the user's first command on a new host

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
   `~/.maury-host-id` (per ADR-0015). This file is created once and
   never modified — it's how this host identifies itself in the
   manifest from now on.
2. **Pull (or ingest) the base repo.** Online: clones the URL with
   the SSH key the user has configured (per ADR-0016, default
   transport auth is SSH for git/github backends in v1). Offline:
   unpacks the tarball or copies the directory.
3. **Read the manifest.** The base repo's `.meta/manifest.json` is
   now authoritative — maury knows about every other repo, every
   other host, every profile from this point forward.
4. **Decide who I am.** Two paths:
   - **Pre-registered:** the manifest already has a host entry whose
     `host_id` matches `~/.maury-host-id`. Render and exit. Most
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
   `~/.claude/`.

### `maury bootstrap-snippet` for from-existing-host onboarding

To shortcut step 1 above, run on any existing host (e.g., workstation):

```sh
maury bootstrap-snippet [--target macos|ubuntu|freebsd|linux]
```

Emits a one-liner the user can paste on the new laptop. The
generated line includes:
- OS-specific install of `pipx` (if not already installed) — see
  install table below.
- `pipx install maury`
- `maury init <base-repo-url>` filled in with this fleet's actual
  base URL.

### Pre-maury install paths (per question Q1)

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

### CLI verb separation (per Q4)

- **`maury init`** — user's first command on a new machine.
- **`maury bootstrap`** — curator-side ops: `bootstrap repo`
  (creates a new GitHub repo + deploy keys), `bootstrap profile`
  (creates a new profile dir + manifest entry), `bootstrap-snippet`
  (generates a paste-on-new-host one-liner).

Different verbs, different audiences, no overlap.

### Cold-start recovery (per Q3)

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

### Auth flavor (per Q2)

V1 default for the `git` and `github` backends is **SSH key**. The
user generates a keypair (or uses an existing one), pastes the
public key as a deploy key on the base repo via the GitHub UI,
provides the URL to `maury init`. OAuth device flow and HTTPS+PAT
are candidates for v2 niceness but not v1 scope.

For non-git backends per ADR-0016 (Perforce, S3+age, etc.), each
adapter brings its own auth model.

### What the init flow *doesn't* do (v1)

- **It does not auto-install `claude` itself.** maury depends on
  Claude Code already being installed and configured.
- **It does not auto-install Python.** The pipx install step
  assumes Python 3.11+ is on PATH.
- **It does not generate or push base content.** Init pulls
  existing base; it doesn't bootstrap a fleet from scratch. For
  that, see `maury bootstrap repo base` (curator command, Phase 4).

## Consequences

- **One credential + one URL** is the minimum the user must bring,
  plus pipx-installable maury.
- **Clean separation of new-user vs curator commands** — `maury
  init` always means "I'm a new host wanting to join an existing
  fleet"; `maury bootstrap` always means "I'm setting up new fleet
  infrastructure."
- **Air-gapped path is first-class**, via `--from-tarball` and
  `--from-dir`. Critical for work environments where the new host
  can't directly reach GitHub.
- **bootstrap-snippet** removes friction for the from-existing-host
  case — paste one line, get a working maury install + first sync.
- **Manifest-from-base is the source of truth** for everything else
  about the new host. Once init's step 3 completes, maury knows
  which other repos to clone, what deploy keys to generate, etc.

## Build-order placement

`maury init` lands in **Phase 4 (bootstrap commands)** alongside
`maury bootstrap repo` and `maury bootstrap host`. The probe
mechanism (Phase 2 — already shipped) and the render engine (Phase
3 — next up) are prerequisites.

## Alternatives considered

- **`maury setup` instead of `maury init`.** Considered. `init` is
  shorter, well-known from `git init`/`npm init`/etc. Won.
- **Auto-detect and install pipx.** Rejected: too much OS-detection
  logic for a one-time operation; documentation + bootstrap-snippet
  per OS is enough.
- **Sync the bootstrap info via maury itself.** Chicken-and-egg.
  Rejected.
- **Web-based "bootstrap server" with one-time tokens.** Considered
  for v2 (the "scan from your phone" UX). Real but rejected for v1
  as scope creep — pipx + URL + SSH key works fine.
- **Single hard-coded auth flavor across all backends.** Rejected
  per ADR-0016: auth is per-backend by design.

## Followups

- **`maury bundle base`** (small command, v1) — produces a
  tarball of the base repo for offline distribution to new hosts.
- **One-time bootstrap-token UX** (v2) — `maury bootstrap-token`
  on workstation emits a signed token containing URL + temporary access
  key; `maury init <token>` on the new host needs nothing else.
  Requires a token-signing key kept by the curator.
