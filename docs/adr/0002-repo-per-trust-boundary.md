# ADR-0002: Repo per trust boundary, not per profile

**Status:** Accepted
**Date:** 2026-05-06

## Related tenets

- [Tenet 3 — Trust boundaries are physical](../tenets.md#3-trust-boundaries-are-physical-not-policy)

## Context

The work laptop must not have access to personal-context bytes. We
considered single-repo with per-directory access, sparse checkout, and
per-profile branches. None of these are real isolation:

- GitHub does not support per-directory read access on a single repo.
- Sparse checkout and `.gitignore` filters are client-side conveniences;
  a `git fetch --all --unshallow` exposes everything in the repo.
- Branch protection limits **write** access, not read access; the work
  laptop could still `git fetch origin profile/home`.

The only honest answer is repo-level isolation: the work laptop never
holds the credentials to clone the personal repo.

## Decision

One git repo per **trust boundary**, where a trust boundary is a set of
profiles that can see each other. Profiles within a boundary live in the
same repo as separate `profiles/<name>/` directories. Base lives in its
own repo so work hosts can consume it without seeing personal content.

For the project owner's likely use:

| Repo | Contents | Read | Write |
|---|---|---|---|
| `maury-base` | Universal preferences | All hosts | Curator hosts |
| `maury-personal` | `home`, `homelab`, `research`, etc. | Personal hosts | Personal hosts |
| `maury-work` | `work` (and any sub-profiles in same boundary) | Work laptop + curators | Work laptop + curators |

## Consequences

- ~3 repos for typical use; +1 per genuinely isolated context.
- Bootstrap tooling must make repo creation painless.
- Cross-repo refactors (moving a fragment between profiles) become
  cross-repo commits — the `promote` command handles coordination.
- Render engine must walk multiple repo roots and merge.
- **Profiles are cheap; repos are expensive** — adding a profile inside
  an existing trust boundary is one command, adding a new trust boundary
  is a deliberate operation.

## Alternatives considered

- **Single repo, per-directory permissions.** Rejected: not supported by
  GitHub; cannot be made into a real boundary.
- **Branch-per-profile + branch protection.** Rejected: protects writes,
  not reads; isolation is illusory.
- **Sparse checkout.** Rejected: client-side only; not a security
  boundary.
- **Submodules.** Considered. Provides isolation if each submodule has
  its own access control. Rejected: adds operational complexity (submodule
  updates, detached HEAD pitfalls) without meaningful gain over plain
  repo-per-trust-boundary.
