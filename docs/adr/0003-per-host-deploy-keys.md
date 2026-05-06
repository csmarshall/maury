# ADR-0003: Per-host deploy keys, not account collaboration

**Status:** Accepted
**Date:** 2026-05-06

## Related tenets

- [Tenet 3 — Trust boundaries are physical](../tenets.md#3-trust-boundaries-are-physical-not-policy)

## Context

Once we settled on repo-per-trust-boundary (ADR-0002), we needed an
access mechanism for granting hosts read/write to specific repos. The
natural choices are:

1. Account-based collaboration (add `example-work` as collaborator on
   `maury-base`).
2. Fine-grained PATs (a token from one account scoped to specific repos).
3. Per-host SSH deploy keys.

The unit of trust we actually care about is "can host X push to repo Y,"
not "can account A push to repo Y." Account-based access introduces a
layer of indirection (and potential employer-policy friction over which
GitHub account is authenticated where).

## Decision

Per-host SSH deploy keys, one keypair per (host, repo) combination. Public
keys are added as deploy keys on the relevant repo with appropriate
read/write scope. Manifest entries store URLs like
`git@github-<alias>:<owner>/<repo>.git`; SSH config aliases route auth
to the right key.

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

## Consequences

- Bootstrap command must generate keypairs, print public keys for
  installation on GitHub, and update both SSH config and manifest.
- More keys per host (one per repo), but each is narrowly scoped — losing
  a host means revoking N keys, not invalidating an account.
- Audit trail in GitHub identifies pushes by deploy key name; naming
  convention `<host>-<repo>-<mode>` makes attribution obvious.
- Bypasses employer-policy questions about which GitHub account is
  authenticated on which device.

## Alternatives considered

- **Single account-wide PAT.** Rejected: blast radius too wide; loss of
  laptop = compromise of every reachable repo.
- **Fine-grained PAT per host.** Better than wide PAT, but tokens expire
  and need rotation; deploy keys don't. Could be used as escape hatch for
  hosts that can't run an SSH agent.
- **GitHub App.** Overkill for personal-scale; designed for orgs.
- **Machine users (separate GitHub accounts per host).** Adds account
  sprawl; deploy keys give the same per-host scoping without that.
