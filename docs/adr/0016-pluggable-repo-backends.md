# ADR-0016: Pluggable repo backends — separate trust boundary from transport

**Status:** Accepted
**Date:** 2026-05-06

## Related tenets

- [Tenet 10 — Modularity over hardcoding](../tenets.md#10-modularity-over-hardcoding)

## Context

The repo-per-trust-boundary architecture (ADR-0002) was originally
designed assuming git over GitHub everywhere. Real-world cases break
that assumption:

- Personal stuff on GitHub; work on GitHub Enterprise.
- Personal stuff on GitHub; work on self-hosted GitLab.
- Personal stuff on GitHub; work on Perforce (`p4`), Mercurial, or
  Subversion.
- Self-hosted Gitea on rosa for personal, GitHub for work.
- Encrypted bundles dropped in S3 / B2 for ultra-paranoid environments.
- Air-gapped networks where the work boundary uses signed file
  bundles on a removable drive.

The **trust boundary** is "what set of profiles can see each other,
backed by what access-controlled store." The **transport** is "how do
bytes flow between hosts and the store." Conflating them means a user
can't pick a different transport for different boundaries — which is
exactly the realistic case.

Same instinct as ADR-0015: don't bake an implementation detail into
the schema.

## Decision

Add a per-repo `backend` field. Each backend is an adapter implementing
a small interface; the manifest selects the adapter at runtime.

### Schema addition

```json
"repos": {
  "base": {
    "url":     "git@github-base:csmarshall/maury-base.git",
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

### The adapter interface (v1 sketch)

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

### v1 backends

- **`git`** (default) — generic git over SSH/HTTPS. No remote
  bootstrap; user creates the remote and provides the URL. Works with
  any git server (Gitea, GitLab, self-hosted, Bitbucket).
- **`github`** — uses `gh` CLI for `create_remote` and
  `attach_credential` (deploy keys via `gh api repos/OWNER/REPO/keys`).
  Falls through to plain git for clone/pull/push/status.

### v2+ backends (deferred)

- **`gitlab`** — analogous, using `glab` or GitLab API.
- **`gitea`** — analogous, using Gitea API.
- **`p4`** (Perforce) — different model entirely (pessimistic locking,
  depots, no branches like git). Adapter is a substantial chunk of
  work.
- **`s3-age`** — encrypted bundles in S3/B2; sync = upload/download +
  age encrypt/decrypt. No git semantics; would mean diff/conflict
  resolution lives in maury.
- **`bundle`** — signed/encrypted bundles dropped in a shared
  filesystem (NFS, removable drive). For air-gapped work environments.
- **`hg`**, **`svn`** — niche but plausible.

### Discovery and registration

Backends register via a small registry in `src/maury/repos/backends/`.
v2+ third-party backends would use `entry_points` in
`pyproject.toml`. The manifest validator refuses unknown backend names
with a clear error pointing at the registry.

### CLI surface

```sh
maury bootstrap repo <name> --backend github --visibility private --owner csmarshall
maury bootstrap repo <name> --backend git --url <existing-url>
maury bootstrap repo <name> --backend p4 --depot //maury/work
maury repo list                # shows all repos and their backends
maury repo backends            # lists registered backends + capabilities
```

### Mixed-backend example

```json
"hosts": {
  "host_xxx": {
    "name": "work-laptop",
    "profile": "profile_yyy",
    "repos": {
      "base": { "url": "git@github-base:csmarshall/maury-base.git",
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

## Consequences

- Trust boundary and transport become independent dimensions. Adding a
  fourth Perforce-only client engagement doesn't pollute the personal
  GitHub repos with weird auth code.
- New backends are additive: shipping `gitlab` later doesn't touch
  the existing `github` or `git` adapters.
- The manifest gains optional fields (`backend`, `backend_config`).
  Default `backend: "git"` keeps existing manifests valid.
- Auth abstraction lives at the adapter boundary; per-backend
  credentials are referenced through ADR-0014's secret store rather
  than copy-pasted in the manifest.
- Cost: a small interface to design and one extra concept for users
  to learn. Worth it because the alternative is "if you're not on
  GitHub, you're SOL."

## Build-order placement

- **Schema field added now**, alongside the ADR-0015 surrogate-key
  refactor. `RepoSpec` gains optional `backend: str = "git"` and
  `backend_config: dict | None = None`.
- **`git` backend** lands in Phase 4 (bootstrap commands) and
  Phase 5 (sync workflow) — that's where the actual ops live.
- **`github` backend** lands in Phase 4 alongside `git`, since most
  users are there.
- **Other backends** are post-v1 work, added as users need them.

## Alternatives considered

- **Hardcode git everywhere.** Rejected: a user with a Perforce work
  shop is locked out of the work boundary entirely.
- **Hardcode git, treat non-git as out-of-scope.** Considered. The
  schema-only addition is so cheap that there's no reason to refuse
  the future possibility.
- **Per-host backend instead of per-repo.** Rejected: a host can
  legitimately have one git repo and one Perforce depot; per-repo is
  the right granularity.
- **Bake auth into manifest URLs.** Rejected: secrets in manifest is
  a layering violation. Auth flows through the adapter from the
  ADR-0014 credential store.
