# ADR-0007: Python with uv, including FreeBSD

**Status:** Accepted
**Date:** 2026-05-06

## Related tenets

- [Tenet 9 — Defer to the platform](../tenets.md#9-defer-to-the-platform)

## TL;DR

The deciding question for Python vs Go/Rust was whether FreeBSD breaks
the Python distribution story — investigation showed it doesn't
(`pkg install uv` works via the `devel/uv` port). Maury uses
**Python ≥3.11 with `uv`** for env/dep management on every host
including FreeBSD, with the standard `pyproject.toml` + `uv sync`
workflow throughout. Picks up first-class Anthropic SDK support and
matches user fluency. Trade-off: no static-binary distribution, so
each host needs Python 3.11+ installed (acceptable for typical fleets
under ~10 hosts).

## Context and Problem Statement

Implementation language and toolchain choice for maury. Candidates:
Python, Go, TypeScript (if forking jean-claude), Rust. Constraints:

- Operator preference: Python (the user environment in which
  maury is being built has Python as the established default).
- Multi-host distribution: must work cleanly on macOS, Ubuntu, FreeBSD.
- Anthropic SDK + prompt caching across transcript batches: heaviest
  dependency, most mature in Python.
- Static-binary distribution (Go/Rust) is attractive because it sidesteps
  per-host runtime version skew.

The deciding question was whether FreeBSD (firewall) breaks the Python
distribution story. Investigation showed it doesn't: `pkg install uv`
works on FreeBSD via the `devel/uv` port, with one caveat (no FreeBSD
artifacts for `uv python install`; use system Python instead).

<details>
<summary><b>Decision drivers</b> (4 items — click to expand)</summary>

- **Tenet 9:** defer to the platform. Use the language and
  package manager the user already prefers and that has the
  best ecosystem fit for the heaviest dependency (Anthropic
  SDK).
- **Cross-OS reach:** must run on macOS, Ubuntu, FreeBSD without
  per-OS code paths in the build system.
- **Anthropic SDK first-class:** the LLM integration is on the
  hot path; weaker SDK = more friction.
- **User familiarity:** the user is fluent in Python; another
  language adds a learning tax for no proportional gain.

</details>

<details>
<summary><b>Considered options</b> (6 options — click to expand)</summary>

- **Option A:** Go — single static binary, trivial cross-compile.
- **Option B:** Rust — same distribution story as Go.
- **Option C:** TypeScript — fork jean-claude.
- **Option D:** Python via PEP 723 single-file scripts.
- **Option E:** Python on top of chezmoi (use chezmoi for
  rendering).
- **Option F (chosen):** Python ≥3.11 with `uv` for environment
  and dependency management on every host, including FreeBSD.

</details>

## Decision Outcome

**Chosen option:** Option F — Python ≥3.11. Use `uv` for
environment and dependency management on every host, including
firewall. The FreeBSD investigation removed the main
counter-argument (Go's distribution edge), and Python wins on
ecosystem fit + user preference.

### Implementation details

On FreeBSD specifically:

1. `pkg install python311 uv` (don't use `uv python install` —
   no FreeBSD artifacts).
2. `uv python pin /usr/local/bin/python3.11` to anchor.
3. If running inside an iocage jail, set
   `SSL_CERT_FILE=/etc/ssl/cert.pem` or `--native-tls` for TLS.

Standard `pyproject.toml` + `uv sync` workflow on every host
(macOS, Ubuntu, FreeBSD).

### Consequences

- ✅ **Good:** Anthropic SDK and prompt caching are first-class
  — best-in-class library support for the hot path.
- ✅ **Good:** Standard `pyproject.toml` + `uv sync` workflow
  on every host; no per-OS build divergence.
- ✅ **Good:** Matches user preference and existing skill set.
- ❌ **Bad:** No static-binary distribution; deploying to a new
  host requires Python ≥3.11 installed first. Acceptable given
  the host count (typically <10).
- ❌ **Bad:** Pay startup cost on every CLI invocation;
  tolerable for a tool invoked tens of times per day, not
  thousands.

### Confirmation

- `pyproject.toml` declares `requires-python = ">=3.11"`.
- `uv.lock` pinned and tested across macOS / Ubuntu / FreeBSD.
- README documents the FreeBSD-specific install path
  (`pkg install python311 uv`).

<details>
<summary><b>Pros and cons of the options</b> (per-option ✅/❌ — click to expand)</summary>

#### Option A: Go

- ✅ **Good:** Single static binary, trivial cross-compile to
  FreeBSD via `GOOS=freebsd`.
- ✅ **Good:** No per-host runtime version skew.
- ❌ **Bad:** Weaker Anthropic SDK ecosystem.
- ❌ **Bad:** Against stated language preference.
- ❌ **Bad:** uv-on-FreeBSD removed Go's main practical
  advantage (FreeBSD distribution).

#### Option B: Rust

- ✅ **Good:** Same distribution story as Go; great safety
  guarantees.
- ❌ **Bad:** Weaker ecosystem fit than Python for this
  workload.
- ❌ **Bad:** Steeper learning curve for the user.

#### Option C: TypeScript (fork jean-claude)

- ✅ **Good:** Reuses an existing tool's UX and code.
- ❌ **Bad:** Against language preference.
- ❌ **Bad:** jean-claude's two-profile assumption is baked in
  deeply; retrofit cost similar to clean rebuild.

#### Option D: Python via PEP 723 single-file scripts

- ✅ **Good:** Lightweight; no project scaffolding.
- ❌ **Bad:** uv's standard project layout is clearer for a
  multi-module tool of this scope.

#### Option E: Python on top of chezmoi

- ✅ **Good:** Reuses a mature dotfile manager.
- ❌ **Bad:** chezmoi's design center is single-repo dotfile
  management; the multi-repo trust-boundary architecture
  fights it.
- ❌ **Bad:** Adds Go binary dep on every host without
  proportional code savings.

#### Option F (chosen): Python ≥3.11 + uv

- ✅ **Good:** Best Anthropic SDK support; matches user
  preference; uniform workflow across hosts.
- ❌ **Bad:** No static binary; per-host Python runtime
  required.

</details>

## Build-order placement

Phase 0 / project scaffolding — the language and toolchain
choice predates every implementation phase. `pyproject.toml`
and `uv.lock` are committed at scaffolding time; downstream
phases assume them.

## Followups

- **PyPI release** — `pipx install maury` is the canonical
  user-install path per
  [ADR-0018](0018-minimum-bootstrap-ux.md). Package not yet
  on PyPI; ships with the first release.
- **PEP 723 single-file scripts** for tiny ancillary tools
  (e.g., one-shot helpers) remain available where the
  multi-module project layout would be overkill.

## Amendment history

- 2026-05-11 — "profile" vocabulary renamed to "mode" per ADR-0037 doctoral examination. References to "profile" in this ADR now read "mode"; no semantic changes.
