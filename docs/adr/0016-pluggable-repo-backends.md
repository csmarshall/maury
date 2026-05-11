# ADR-0016: Pluggable repo backends — separate trust boundary from transport

**Status:** Accepted
**Date:** 2026-05-06
**Amended:**
- 2026-05-06 — addendum noting ADR-0022's git-substrate lock-in
  rules out non-git backends (p4, hg, svn, s3-age, bundle).
  Git-compatible alternatives (gitlab, gitea, codeberg,
  self-hosted git) remain in scope. See bottom-of-ADR
  Addendum (2026-05-06).

## Related tenets

- [Tenet 10 — Modularity over hardcoding](../tenets.md#10-modularity-over-hardcoding)

## TL;DR

The repo-per-trust-boundary model originally assumed git-on-GitHub
everywhere; real users have one boundary on GitHub and another on
GitLab/Gitea/Enterprise. Trust boundary and transport are orthogonal
axes that the schema should keep independent. Maury adds a per-repo
**`backend` field with an adapter interface**, shipping `git` and
`github` adapters in v1; `gitlab`/`gitea`/`codeberg` adapters land
post-v1. Per the 2026-05-06 addendum, ADR-0022's git-substrate lock-in
rules out non-git backends (`p4`, `hg`, `svn`, `s3-age`, `bundle`).
Trade-off: a small interface to design and one extra concept users
learn (backends).

## Context and Problem Statement

The repo-per-trust-boundary architecture
([ADR-0002](0002-repo-per-trust-boundary.md)) was originally
designed assuming git over GitHub everywhere. Real-world cases
break that assumption:

- Personal stuff on GitHub; work on GitHub Enterprise.
- Personal stuff on GitHub; work on self-hosted GitLab.
- Personal stuff on GitHub; work on Perforce (`p4`), Mercurial, or
  Subversion.
- Self-hosted Gitea on linux-server for personal, GitHub for work.
- Encrypted bundles dropped in S3 / B2 for ultra-paranoid environments.
- Air-gapped networks where the work boundary uses signed file
  bundles on a removable drive.

The **trust boundary** is "what set of profiles can see each other,
backed by what access-controlled store." The **transport** is "how do
bytes flow between hosts and the store." Conflating them means a user
can't pick a different transport for different boundaries — which is
exactly the realistic case.

Same instinct as [ADR-0015](0015-surrogate-keys-for-hosts-and-profiles.md):
don't bake an implementation detail into the schema.

<details>
<summary><b>Decision drivers</b> (5 items — click to expand)</summary>

- **Tenet 10:** modularity over hardcoding. Backend choice
  should be configuration, not code.
- **Real-world heterogeneity:** users genuinely have one
  boundary on GitHub and another on a different host (Gitea,
  Enterprise, etc.). Hardcoding GitHub locks them out.
- **Trust boundary ≠ transport:** these are two independent
  axes that the schema should keep independent.
- **Substrate constraint:** [ADR-0022](0022-branch-per-mining-run.md)
  commits maury to git as the substrate for the proposal model
  (commit-message-as-proposal, `git log --grep` dedup). Non-git
  backends would have no equivalent.
- **Forward-compatibility:** adding the schema field now is
  cheap; retrofitting it later means breaking every manifest.

</details>

<details>
<summary><b>Considered options</b> (5 options — click to expand)</summary>

- **Option A:** Hardcode git (and GitHub) everywhere.
- **Option B:** Hardcode git, treat non-git as out-of-scope
  permanently.
- **Option C:** Per-host backend instead of per-repo.
- **Option D:** Bake auth into manifest URLs.
- **Option E (chosen):** Per-repo `backend` field with an
  adapter interface; ship `git` and `github` adapters in v1;
  defer non-git backends; later constrained by ADR-0022 to
  git-compatible only.

</details>

## Decision Outcome

**Chosen option:** Option E — add a per-repo `backend` field.
Each backend is an adapter implementing a small interface;
the manifest selects the adapter at runtime. Initial v1
ships `git` (default) and `github` adapters. The
[2026-05-06 addendum](#addendum-2026-05-06) below restricts
future backends to git-compatible alternatives only,
following [ADR-0022](0022-branch-per-mining-run.md)'s
substrate lock-in.

### Implementation details

#### Schema addition

```json
"repos": {
  "base": {
    "url":     "git@github-base:exampleuser/maury-base.git",
    "mode":    "rw",
    "backend": "github"
  },
  "work": {
    "url":     "ssh://perforce.work.example.com:1666",
    "mode":    "rw",
    "backend": "p4",
    "backend_config": { "depot": "//maury/work" }
  }
}
```

`backend` is optional; default is `"git"` (plain git, no remote
bootstrapping support — you create the remote yourself, and maury just
pulls/pushes).

`backend_config` is a free-form object the backend adapter understands.
Schema validation is the adapter's responsibility, not the core
manifest's.

#### The adapter interface (v1 sketch)

```python
class RepoBackend(Protocol):
    name: str

    def clone(self, url: str, local_path: Path, *, mode: RepoMode,
              auth: BackendAuth) -> None: ...
    def pull(self, local_path: Path, *, auth: BackendAuth) -> PullResult: ...
    def push(self, local_path: Path, *, paths: list[Path],
             auth: BackendAuth, message: str) -> PushResult: ...
    def status(self, local_path: Path) -> RepoStatus: ...

    # Bootstrap is optional — git_generic just returns NotSupported.
    def supports_remote_bootstrap(self) -> bool: ...
    def create_remote(self, name: str, *, visibility: str,
                      auth: BackendAuth) -> str: ...  # returns the URL
    def attach_credential(self, remote_url: str, public_key: str,
                          *, mode: RepoMode, label: str,
                          auth: BackendAuth) -> None: ...
```

`BackendAuth` is per-backend (SSH key path for git, PAT for GitHub API
calls, P4PORT/P4USER for Perforce, AWS creds for S3, ...). The
manifest doesn't store secrets — it points at credential-store entries
managed under ADR-0014.

#### v1 backends

- **`git`** (default) — generic git over SSH/HTTPS. No remote
  bootstrap; user creates the remote and provides the URL. Works with
  any git server (Gitea, GitLab, self-hosted, Bitbucket).
- **`github`** — uses `gh` CLI for `create_remote` and
  `attach_credential` (deploy keys via `gh api repos/OWNER/REPO/keys`).
  Falls through to plain git for clone/pull/push/status.

#### v2+ backends

> **2026-05-06 addendum below restricted scope:** all
> non-git backends listed here are no longer planned, because
> [ADR-0022](0022-branch-per-mining-run.md) commits maury to
> git as the substrate. Per-entry status:

- **`gitlab`** — *still in scope*; analogous to `github`,
  using `glab` or GitLab API.
- **`gitea`** — *still in scope*; analogous, using Gitea API.
- **`codeberg`** — *still in scope* (added in addendum);
  straightforward extension of the Gitea-shape adapter.
- **`p4`** (Perforce) — **NO LONGER PLANNED.** Different
  model entirely (pessimistic locking, depots, no branches
  like git); has no equivalent of commit-message-as-proposal
  or `git log --grep` dedup.
- **`s3-age`** — **NO LONGER PLANNED.** Encrypted bundles in
  S3/B2 have no git semantics; diff/conflict resolution
  would have to live in maury.
- **`bundle`** — **NO LONGER PLANNED.** Signed/encrypted
  bundles dropped in a shared filesystem (NFS, removable
  drive); same substrate-incompatibility as `s3-age`.
- **`hg`**, **`svn`** — **NO LONGER PLANNED.** Same reason.

#### Discovery and registration

Backends register via a small registry in `src/maury/repos/backends/`.
v2+ third-party backends would use `entry_points` in
`pyproject.toml`. The manifest validator refuses unknown backend names
with a clear error pointing at the registry.

#### CLI surface

```sh
maury bootstrap repo <name> --backend github --visibility private --owner exampleuser
maury bootstrap repo <name> --backend git --url <existing-url>
maury bootstrap repo <name> --backend p4 --depot //maury/work
maury repo list                # shows all repos and their backends
maury repo backends            # lists registered backends + capabilities
```

#### Mixed-backend example

```json
"hosts": {
  "host_xxx": {
    "name": "work-laptop",
    "profile": "profile_yyy",
    "repos": {
      "base": { "url": "git@github-base:exampleuser/maury-base.git",
                "mode": "ro", "backend": "github" },
      "work": { "url": "ssh://perforce.work.example.com:1666",
                "mode": "rw", "backend": "p4",
                "backend_config": { "depot": "//maury/work" } }
    }
  }
}
```

The render engine, sync workflow, and audit log don't care about the
backend — they call into the adapter through the interface.

### Consequences

- ✅ **Good:** Trust boundary and transport become independent
  dimensions. Adding a fourth client engagement on a different
  git host doesn't pollute the personal GitHub repos with
  weird auth code.
- ✅ **Good:** New backends are additive — shipping `gitlab`
  later doesn't touch the existing `github` or `git`
  adapters.
- ✅ **Good:** Auth abstraction lives at the adapter
  boundary; per-backend credentials are referenced through
  [ADR-0014](0014-host-local-secrets-with-metadata-sync.md)'s
  secret store rather than copy-pasted in the manifest.
- ⚖️ **Neutral:** The manifest gains optional fields
  (`backend`, `backend_config`). Default `backend: "git"`
  keeps existing manifests valid.
- ❌ **Bad:** A small interface to design and one extra
  concept for users to learn. Worth it because the
  alternative is "if you're not on GitHub, you're SOL."

### Confirmation

- `RepoSpec` in the manifest schema includes optional
  `backend: str = "git"` and `backend_config: dict | None`.
- Backends register in `src/maury/repos/backends/`; the
  manifest validator refuses unknown backend names with a
  clear error pointing at the registry.
- Auth never appears in manifest URLs; the
  `attach_credential` adapter method routes through the
  [ADR-0014](0014-host-local-secrets-with-metadata-sync.md)
  store.

<details>
<summary><b>Pros and cons of the options</b> (per-option ✅/❌ — click to expand)</summary>

#### Option A: Hardcode git everywhere

- ✅ **Good:** Smallest schema; no adapter abstraction to
  design.
- ❌ **Bad:** A user with a non-git work shop is locked out
  of the work boundary entirely.

#### Option B: Hardcode git, treat non-git as out-of-scope

- ✅ **Good:** Same simplicity as Option A.
- ❌ **Bad:** The schema-only addition (Option E) is so cheap
  that there's no reason to refuse the future possibility.

#### Option C: Per-host backend instead of per-repo

- ✅ **Good:** Slightly simpler manifest.
- ❌ **Bad:** A host can legitimately have one git repo and
  one different-backend repo; per-repo is the right
  granularity.

#### Option D: Bake auth into manifest URLs

- ✅ **Good:** Self-contained URL strings.
- ❌ **Bad:** Secrets in manifest is a layering violation.
  Auth must flow through the adapter from the
  [ADR-0014](0014-host-local-secrets-with-metadata-sync.md)
  credential store.

#### Option E (chosen): Per-repo backend with adapter interface

- ✅ **Good:** Clean separation of trust boundary from
  transport.
- ✅ **Good:** Forward-compatible with new git-compatible
  hosts.
- ❌ **Bad:** Adapter interface to design and document.
- ❌ **Bad:** Users learn one extra concept (backends).

</details>

## Build-order placement

- **Schema field added now**, alongside the
  [ADR-0015](0015-surrogate-keys-for-hosts-and-profiles.md)
  surrogate-key refactor. `RepoSpec` gains optional
  `backend: str = "git"` and `backend_config: dict | None =
  None`.
- **`git` backend** lands in Phase 4 (bootstrap commands)
  and Phase 5 (sync workflow) — that's where the actual ops
  live.
- **`github` backend** lands in Phase 4 alongside `git`,
  since most users are there.
- **Other backends** are post-v1 work, added as users need
  them, scoped to git-compatible only per the addendum.

## Followups

- **`gitlab` adapter** — first non-GitHub git host worth
  shipping; uses `glab` or GitLab API.
- **`gitea` adapter** — for self-hosted use cases.
- **`codeberg` adapter** — straightforward extension of the
  Gitea-shape work.
- **Self-hosted git** is already covered by the default
  `git` backend; no new adapter needed.

## Addendum (2026-05-06)

[ADR-0022](0022-branch-per-mining-run.md) commits maury to git as
the substrate for the proposal model and the changelog. Non-git-
compatible backends (`p4`, `hg`, `svn`, `s3-age`, `bundle`) are no
longer planned: the commit-message-as-proposal and `git log --grep`
dedup mechanisms have no equivalent in those systems.

Git-compatible alternatives remain in scope: `gitlab`, `gitea`,
`codeberg`, self-hosted git, and the `github` adapter. The
backend-as-adapter abstraction in this ADR survives — it's still
useful for routing per-host auth, host capability detection, and
URL-shape variation across git-hosting providers.

If a user has a Perforce work shop and cannot use git for the work
boundary, the answer is no longer "we'll add a Perforce backend";
the answer is "you can't have a maury-managed work boundary on
that shop." Acceptable given target-user reality.

## Amendment history

- 2026-05-11 — "profile" vocabulary renamed to "mode" per ADR-0037 doctoral examination. References to "profile" in this ADR now read "mode"; no semantic changes.
