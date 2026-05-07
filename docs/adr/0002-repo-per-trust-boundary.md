# ADR-0002: Repo per trust boundary, not per profile

**Status:** Accepted
**Date:** 2026-05-06

## Related tenets

- [Tenet 3 — Trust boundaries are physical](../tenets.md#3-trust-boundaries-are-physical-not-policy)

## Context and Problem Statement

The work laptop must not have access to personal-context bytes. The
question is **at what layer do we enforce this?** Several
client-side and branch-level options look attractive on paper but
don't actually provide isolation:

- GitHub does not support per-directory read access on a single repo.
- Sparse checkout and `.gitignore` filters are client-side conveniences;
  a `git fetch --all --unshallow` exposes everything in the repo.
- Branch protection limits **write** access, not read access; the work
  laptop could still `git fetch origin profile/home`.

The only honest answer is repo-level isolation: the work laptop never
holds the credentials to clone the personal repo.

## Decision Drivers

- **Tenet 3:** trust boundaries are physical, not policy. The
  enforcement must be at a layer the user can't bypass.
- **Threat model:** "the work laptop has no path to personal bytes"
  must be enforceable at fetch-time, not just at render-time.
- **Operational simplicity:** adding a profile within a trust boundary
  should be one command; adding a trust boundary is a deliberate
  operation.
- **Substrate constraint:** maury assumes git as the substrate (per
  [`concepts.md` §"What maury assumes about its substrate"](../concepts.md#what-maury-assumes-about-its-substrate));
  GitHub-style hosting determines what access primitives are
  available.

## Considered Options

- **Option A:** Single repo, per-directory permissions.
- **Option B:** Branch-per-profile + branch protection.
- **Option C:** Sparse checkout to limit what each host pulls.
- **Option D:** Submodules — one parent repo with per-profile submodules.
- **Option E (chosen):** One git repo per trust boundary; profiles
  within a boundary share the repo.

## Decision Outcome

**Chosen option:** Option E — one git repo per **trust boundary**,
where a trust boundary is a set of profiles that can see each other.
Profiles within a boundary live in the same repo as separate
`profiles/<name>/` directories. Base lives in its own repo so work
hosts can consume it without seeing personal content. This is the
only option whose enforcement is server-side and unbypassable.

> **A note on the "git" in "git repo":** maury assumes git as the
> substrate, not "any version-controlled storage." S3, NFS/CIFS/AFS,
> Dropbox, raw filesystem-via-rsync, Perforce, Mercurial, SVN are NOT
> drop-in alternatives. Git provides specific properties maury depends
> on at the design level (commit log as proposal queue, branches as
> in-flight work, content-addressing for SHAs, three-way merge, deploy-
> key access enforcement). See [`concepts.md` §"What maury assumes
> about its substrate"](../concepts.md#what-maury-assumes-about-its-substrate)
> for the full inventory. Git-compatible alternatives (GitLab, Gitea,
> Codeberg, self-hosted git) work; other storage backends do not.

### Implementation details

For the project owner's likely use:

| Repo | Contents | Read | Write |
|---|---|---|---|
| `maury-base` | Universal preferences | All hosts | Curator hosts |
| `maury-personal` | `home`, `homelab`, `research`, etc. | Personal hosts | Personal hosts |
| `maury-work` | `work` (and any sub-profiles in same boundary) | Work laptop + curators | Work laptop + curators |

Per-host deploy keys (per [ADR-0003](0003-per-host-deploy-keys.md))
provide the per-repo access control; cross-repo content movement
goes through the promotion flow ([ADR-0009](0009-promotion-only-cross-boundary.md))
constrained by the inheritance graph ([ADR-0027](0027-cross-context-promotion-via-shared-root.md)).

### Consequences

- ✅ **Good:** Isolation is server-side and unbypassable. The work
  laptop literally cannot fetch personal bytes — it has no key.
- ✅ **Good:** Profiles are cheap to add inside an existing trust
  boundary; the repo schema accommodates many.
- ❌ **Bad:** ~3 repos for typical use; +1 per genuinely isolated
  context. Bootstrap tooling must make repo creation painless.
- ❌ **Bad:** Cross-repo refactors (moving a fragment between
  profiles in different boundaries) become cross-repo commits — the
  `promote` command handles coordination.
- ⚖️ **Neutral:** Render engine must walk multiple repo roots and
  merge.
- ✅ **Good:** **Profiles are cheap; repos are expensive** — the
  load-bearing mental model. Adding a profile inside an
  existing trust boundary is one command; adding a new trust
  boundary is a deliberate operation. This asymmetry is
  exactly what we want — it pushes design pressure toward
  "one repo, multiple profiles" until a real isolation need
  forces a new boundary.

### Confirmation

- The manifest schema requires a `repos: {...}` map per host with
  per-repo URLs and modes; cross-validated against profile
  membership.
- Bootstrap (`maury init`) and per-host deploy-key generation
  ([ADR-0003](0003-per-host-deploy-keys.md), [ADR-0018](0018-minimum-bootstrap-ux.md))
  enforce the repo-per-boundary structure operationally.

## Pros and Cons of the Options

### Option A: Single repo, per-directory permissions

- ✅ **Good:** Conceptually simple — one place for all maury content.
- ❌ **Bad:** Not supported by GitHub or any major git host; can't
  be made into a real boundary.

### Option B: Branch-per-profile + branch protection

- ✅ **Good:** Reuses existing GitHub primitive (branch protection).
- ❌ **Bad:** Branch protection limits **writes**, not reads; the
  work laptop could still `git fetch origin profile/home` and see
  every byte.
- ❌ **Bad:** Isolation is illusory; tenet-3 violation.

### Option C: Sparse checkout

- ✅ **Good:** Lighter clones for hosts that don't need everything.
- ❌ **Bad:** Client-side only; not a security boundary. A user
  who runs `git fetch --all --unshallow` defeats it.

### Option D: Submodules

- ✅ **Good:** Each submodule is a separate repo, so per-submodule
  access control is real.
- ❌ **Bad:** Adds operational complexity (submodule updates,
  detached HEAD pitfalls) without meaningful gain over plain
  repo-per-trust-boundary — same isolation, more rope.

### Option E (chosen): Repo per trust boundary

- ✅ **Good:** Server-side enforcement of "this host can/can't
  see these bytes."
- ✅ **Good:** Per-host deploy keys map cleanly to per-repo access.
- ❌ **Bad:** Multiple repos to bootstrap and maintain; cross-repo
  operations (promotion) need explicit support.
- ❌ **Bad:** No client-side ergonomics like sparse-checkout for
  reducing what each host pulls within a boundary (mitigated:
  trust boundaries are usually small).

## Build-order placement

Phase 4 (Bootstrap commands) — `maury bootstrap repo` per
[ADR-0018](0018-minimum-bootstrap-ux.md) creates the per-boundary
repos and sets up deploy-key access via [ADR-0003](0003-per-host-deploy-keys.md).
The "one repo per trust boundary" decision is foundational and
shapes everything from manifest schema to render-engine
multi-root walking.

## Followups

- **Self-hosted git option** (e.g., Gitea on a homelab host) is
  in scope architecturally but not yet documented as a path. Per
  [`concepts.md` §"What maury assumes about its substrate"](../concepts.md#what-maury-assumes-about-its-substrate),
  any git-compatible substrate works; the README and bootstrap
  flows currently assume GitHub.
- **Granular within-boundary access** (e.g., per-profile
  sub-permissions inside a single repo) is intentionally not
  pursued — see ADR text. Revisit only if a host needs to be
  inside a boundary but excluded from one profile's content.
