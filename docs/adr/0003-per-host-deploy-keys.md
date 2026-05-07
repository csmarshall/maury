# ADR-0003: Per-host deploy keys, not account collaboration

**Status:** Accepted
**Date:** 2026-05-06

## Related tenets

- [Tenet 3 — Trust boundaries are physical](../tenets.md#3-trust-boundaries-are-physical-not-policy)

## TL;DR

The trust unit that matters is "can host X push to repo Y," not "can
account A push to repo Y" — and PATs/account-collab introduce expiry,
employer-policy ambiguity, and over-broad blast radius. Maury uses
**per-host SSH deploy keys**, one keypair per (host, repo) tuple,
routed via `~/.ssh/config` aliases. Revocation is surgical (one key =
one host disabled), GitHub's audit trail names the machine, and
nothing expires. Trade-off: more keys per host to manage, mitigated by
bootstrap automation.

## Context and Problem Statement

Once we settled on repo-per-trust-boundary (ADR-0002), we needed an
access mechanism for granting hosts read/write to specific repos.
The unit of trust we actually care about is **"can host X push to
repo Y,"** not "can account A push to repo Y." Account-based access
introduces a layer of indirection (and potential employer-policy
friction over which GitHub account is authenticated where).

<details>
<summary><b>Decision drivers</b> (4 items — click to expand)</summary>

- **Tenet 3:** trust boundaries are physical, not policy. Access
  enforcement must map onto the unit we actually care about (the
  host).
- **Audit clarity:** GitHub's audit trail must identify the
  *machine* that pushed, not just the GitHub user.
- **Blast radius:** loss of any single host should revoke at most
  that host's access, not invalidate a wider credential.
- **Employer-policy neutrality:** avoid questions about which
  GitHub account is authenticated on a managed device.

</details>

<details>
<summary><b>Considered options</b> (5 options — click to expand)</summary>

- **Option A:** Single account-wide PAT (Personal Access Token).
- **Option B:** Fine-grained PAT per host.
- **Option C (chosen):** Per-host SSH deploy keys, one keypair per
  (host, repo) combination.
- **Option D:** GitHub App.
- **Option E:** Machine users — a separate GitHub account per host.

</details>

## Decision Outcome

**Chosen option:** Option C — per-host SSH deploy keys, one
keypair per (host, repo) combination. This is the only option
where the access primitive is scoped to the trust unit we
actually care about (the host) and where revocation is surgical
(one key revoked = one host disabled).

### Implementation details

Public keys are added as deploy keys on the relevant repo with
appropriate read/write scope. Manifest entries store URLs like
`git@github-<alias>:<owner>/<repo>.git`; SSH config aliases route
auth to the right key.

Example for the work laptop:

```ssh-config
Host github-base
  HostName github.com
  IdentityFile ~/.ssh/maury-base_ed25519
  IdentitiesOnly yes

Host github-work
  HostName github.com
  IdentityFile ~/.ssh/maury-work_ed25519
  IdentitiesOnly yes
```

```json
"work-laptop": {
  "profile": "work",
  "lock": true,
  "push_policy": "own_profile_only",
  "repos": {
    "base": { "url": "git@github-base:exampleuser/maury-base.git", "mode": "rw" },
    "work": { "url": "git@github-work:example-work/maury-work.git", "mode": "rw" }
  }
}
```

Bootstrap (`maury bootstrap host`, per [ADR-0018](0018-minimum-bootstrap-ux.md))
generates the keypairs, prints the public keys for installation
on GitHub, and updates both the SSH config and the manifest.

### Consequences

- ✅ **Good:** Revocation is surgical — losing a host means
  revoking N keys, not invalidating an account.
- ✅ **Good:** GitHub audit trail identifies pushes by deploy key
  name; the naming convention `<host>-<repo>-<mode>` makes
  attribution obvious.
- ✅ **Good:** Bypasses employer-policy questions about which
  GitHub account is authenticated on which device.
- ❌ **Bad:** More keys per host (one per repo) to manage.
  Mitigated by bootstrap automation.
- ⚖️ **Neutral:** Bootstrap command must generate keypairs,
  print public keys for installation on GitHub, and update both
  SSH config and manifest.

### Confirmation

- Bootstrap command (per [ADR-0018](0018-minimum-bootstrap-ux.md))
  generates `<host>-<repo>_ed25519` keypairs and updates SSH
  config + manifest atomically.
- Manifest schema requires `repos.<name>.url` to be an
  SSH-aliased URL; pure HTTPS or `git@github.com` URLs are
  rejected because they couldn't differentiate between keys.

<details>
<summary><b>Pros and cons of the options</b> (per-option ✅/❌ — click to expand)</summary>

#### Option A: Single account-wide PAT

- ✅ **Good:** Trivial to set up — one token, paste it everywhere.
- ❌ **Bad:** Blast radius too wide; loss of any host = compromise
  of every reachable repo.
- ❌ **Bad:** Tokens expire and require rotation across all hosts
  simultaneously.

#### Option B: Fine-grained PAT per host

- ✅ **Good:** Better than wide PAT — per-host scope.
- ✅ **Good:** Could be used as an escape hatch for hosts that
  can't run an SSH agent.
- ❌ **Bad:** Tokens still expire and need rotation; deploy keys
  don't.
- ❌ **Bad:** Authenticates as a GitHub *user* (the token
  owner), so the employer-policy ambiguity remains.

#### Option C (chosen): Per-host SSH deploy keys

- ✅ **Good:** No expiry; no rotation cycle.
- ✅ **Good:** Per-(host, repo) scope — exactly the trust unit.
- ✅ **Good:** Authenticates as the *deploy key*, not as any
  GitHub user, sidestepping account-attribution questions.
- ❌ **Bad:** More keys to manage per host (one per repo) —
  mitigated by bootstrap automation.
- ❌ **Bad:** Requires SSH config aliasing trick to route
  multiple keys to `github.com`.

#### Option D: GitHub App

- ✅ **Good:** Modern integration model with fine-grained
  permissions.
- ❌ **Bad:** Designed for orgs and integrations at scale;
  overkill for personal-scale, single-curator use.
- ❌ **Bad:** Adds an installation/UI surface that must be
  managed in the GitHub web UI.

#### Option E: Machine users (separate GitHub account per host)

- ✅ **Good:** Per-host isolation; per-host audit trail.
- ❌ **Bad:** Account sprawl; each host needs its own GitHub
  account.
- ❌ **Bad:** Deploy keys give the same per-host scoping without
  the account-management overhead.

</details>

## Build-order placement

Phase 4 (Bootstrap commands) — `maury bootstrap host` per
[ADR-0018](0018-minimum-bootstrap-ux.md) is what generates the
keypairs and updates SSH config + manifest. The deploy-key
*concept* is foundational and predates implementation; the
mechanics ship with Phase 4.

## Followups

- **`pr` mode mechanics:** [ADR-0033](0033-pr-repo-mode.md) adds
  the `pr` (read + write via pull request) repo mode alongside
  the `ro` and `rw` defined here. See
  [concepts.md §6 — Inheritance access mode](../concepts.md#6-inheritance-access-mode)
  for the universal three-mode framing.
- **SSH-agent-less hosts:** if a host can't run an SSH agent,
  fine-grained PAT (Option B) is the documented escape hatch.
  Not yet implemented; lands when first such host is encountered.
