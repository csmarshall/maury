# ADR-0007: Python with uv, including FreeBSD

**Status:** Accepted
**Date:** 2026-05-06

## Related tenets

- [Tenet 9 — Defer to the platform](../tenets.md#9-defer-to-the-platform)

## Context

Implementation language candidates: Python, Go, TypeScript (if forking
jean-claude), Rust. Constraints:

- User preference: Python (per `~/.claude/CLAUDE.md`).
- Multi-host distribution: must work cleanly on macOS, Ubuntu, FreeBSD.
- Anthropic SDK + prompt caching across transcript batches: heaviest
  dependency, most mature in Python.
- Static-binary distribution (Go/Rust) is attractive because it sidesteps
  per-host runtime version skew.

The deciding question was whether FreeBSD (kamek) breaks the Python
distribution story. Investigation showed it doesn't: `pkg install uv`
works on FreeBSD via the `devel/uv` port, with one caveat (no FreeBSD
artifacts for `uv python install`; use system Python instead).

## Decision

Python ≥3.11. Use `uv` for environment and dependency management on every
host, including kamek. On FreeBSD specifically:

1. `pkg install python311 uv` (don't use `uv python install`).
2. `uv python pin /usr/local/bin/python3.11` to anchor.
3. If running inside an iocage jail, set
   `SSL_CERT_FILE=/etc/ssl/cert.pem` or `--native-tls` for TLS.

## Consequences

- Standard `pyproject.toml` + `uv sync` workflow on every host.
- Anthropic SDK and prompt caching are first-class.
- No static-binary distribution; deploying to a new host requires
  Python ≥3.11 installed first. Acceptable given the host count.
- We pay startup cost on every CLI invocation; tolerable for a tool
  invoked tens of times per day, not thousands.

## Alternatives considered

- **Go.** Strong distribution story (single static binary, trivial
  cross-compile to FreeBSD via `GOOS=freebsd`). Rejected: weaker
  Anthropic SDK ecosystem, against stated language preference, and
  uv-on-FreeBSD removed Go's main advantage.
- **Rust.** Same as Go but with worse ecosystem fit and steeper learning
  curve for the user.
- **TypeScript (fork jean-claude).** Rejected: TypeScript against
  preference; jean-claude's two-profile assumption is baked in deeply,
  retrofit cost similar to clean rebuild.
- **Python via PEP 723 single-file scripts.** Considered. uv's standard
  project layout is clearer for a multi-module tool of this scope.
- **Python on chezmoi** (use chezmoi for the rendering layer). Rejected:
  chezmoi's design center is single-repo dotfile management; the
  multi-repo trust-boundary architecture fights it. Adds Go binary dep
  on every host without proportional code savings.
