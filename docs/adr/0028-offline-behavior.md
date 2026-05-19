# ADR-0028: Offline behavior

**Status:** Accepted
**Date:** 2026-05-07

## Related tenets

- [Tenet 1 — First, do no harm](../tenets.md#1-first-do-no-harm)
- [Tenet 11 — Explicit beats implicit, with conservative defaults](../tenets.md#11-explicit-beats-implicit-with-conservative-defaults)

## TL;DR

Maury runs in environments where the network may be missing
(planes, tunnels, air-gapped hosts, API incidents). Rather than a
global "offline mode," each command names its own offline behavior:
pure-local commands (`render`, `doctor`, `agency validate`,
`status`, `rules trace`, `reconcile`, `review`, etc.) work as-is;
network-dependent commands (`sync`, `mine`, `promote`,
`verify-cc-contract`) **hard-refuse on first remote-unreachable
error** by default, with explicit `--offline` (sync/promote) or
`--store-only` (mine) opt-in degrade paths. `init --from-tarball`
is the sanctioned air-gap onboarding path. Trade-off: more friction
on flaky networks than a silent-degrade default, but no surprise
about whether your last sync actually pulled fresh state.

## Context

Maury runs in environments where network access can be missing,
limited, or undesired:

- A user is on a plane / in a tunnel / on a flaky cafe Wi-Fi.
- A user is on call and SSH'd into a host with no general
  internet egress.
- A host is in an air-gapped environment (security-restricted
  network) where the only sanctioned transport is sneakernet
  (USB stick, manual file transfer).
- The Anthropic API is having an incident; `claude -p` is
  unreachable.

Several maury commands depend on external resources:

- `maury sync` — `git pull` from one or more remotes.
- `maury mine` — LLM extraction (default `claude -p`, opt-in
  Anthropic SDK per [ADR-0012](0012-llm-backend.md)).
- `maury init --from-dir` — needs the source repo locally clonable.

Other commands are pure-local (`render`, `doctor`,
`agency validate`, the `status`-display path).

What's never been pinned down: **how does maury behave when a
network operation isn't available?** Three reasonable defaults:
silent degrade, hard refuse, opt-in degrade. They have very
different safety properties and very different UX consequences.

## Decision

Per-command policy. There is no global "offline mode"; each
command names what it does when its network dependency is
unavailable.

| Command | Network dep | Offline behavior |
|---|---|---|
| `maury render` | none | Works as-is. Rendering is pure-local once repos are cloned. |
| `maury doctor` | none | Works as-is. Pure-local rule evaluation. |
| `maury agency validate` / `manifest show` | none | Works as-is. |
| `maury status` | none | Works as-is. Reads local state files. |
| `maury rules trace` | none | Works as-is. |
| `maury sync` | git pull on remotes | **Default: hard refuse on first remote-unreachable error.** Opt-in via `--offline` to skip pull and render from local clones (with loud warning). |
| `maury mine` | LLM (`claude -p` or SDK) | **Default: hard refuse if the configured LLM backend is unreachable.** Fall-forward path: `--store-only` saves a normalized transcript subset to `~/.claude/maury-staging/pending-mining/` for later online mining. |
| `maury init --from-dir` | none if --from-dir is local | Works as-is when given a local path. |
| `maury init --from-tarball` | none | Works as-is. The sanctioned air-gap onboarding path (per [ADR-0018](0018-minimum-bootstrap-ux.md)). |
| `maury review` | none | Works as-is. Walks local run branches. |
| `maury promote --from --to` | git fetch on source repo | **Default: hard refuse on remote-unreachable.** Opt-in via `--offline` to use last-fetched source state (with warning). |
| `maury verify-cc-contract` *(planned per [status.md](../status.md))* | network fetch of Anthropic docs | **Default: hard refuse.** No fall-forward — verification is moot without network. |
| `maury reconcile`, `maury hooks`, `maury manifest resolve`, `maury sessions prune`, `maury refactor *`, `maury uninstall` | none | All pure-local. Work as-is offline. |
| `maury subscribe <url>` *(planned, gap K)* | git fetch on remote | When implemented, follows the same hard-refuse default + `--offline` opt-in pattern as `sync`. |

### Detection: opportunistic, not pre-probed

Maury does **not** probe for network connectivity before running.
Two reasons:

1. **Probes lie.** A successful ping doesn't mean the git remote
   is reachable; a successful `claude -p ping` doesn't mean the
   next mining call won't time out. The only honest signal is
   the actual operation failing.
2. **Probes add latency to every command.** A 100ms TCP probe
   on every `maury status` call is a tax for no benefit.

Network failures are caught at their actual operation site (e.g.,
`git pull` returning non-zero) and surfaced with a maury-specific
error message that names the offending remote and the available
fall-forward (if any).

**Counter-example we accept the loss on:** a long-running
`maury mine` over many transcripts. If the LLM goes down 30
minutes in, the user has burned 30 minutes of work. A 1s
pre-probe at the start of `maury mine` is qualitatively
different from probing on every `maury status`. We accept this
loss because partial mining work is recoverable via
`--store-only` staging — the prep work persists; only the LLM
extraction step is lost. A user worried about long-mine LLM
flakiness can preempt by always running with `--store-only`
first, then `--resume-pending` once they've confirmed the LLM
is up.

### Hard-refuse default rationale

Tenet 1 (first, do no harm) and tenet 11 (explicit beats
implicit, with conservative defaults) both push toward
refuse-by-default for ambiguous cases. If `maury sync` silently rendered from stale local clones,
the user might believe their `~/.claude/` reflects the latest
team-shared content when it doesn't — exactly the kind of
silent state-divergence the safety story rejects.

The user has to type `--offline` once they understand they're
running against stale state. The friction is the point.

### Mining's fall-forward: `--store-only`

Mining is special because:

- The transcripts on disk RIGHT NOW are time-sensitive (the
  conversation is fresh in the user's mind; insights are
  recoverable now and harder to recover later).
- The LLM extraction is independently restartable.

So `maury mine --store-only` walks transcripts now, normalizes
them into a windowed/redacted form, and writes the prepared
material to `~/.claude/maury-staging/pending-mining/`. When the
LLM is reachable again, `maury mine --resume-pending` picks up
where the offline run stopped — runs the LLM extraction over
the staged windows, classifies, commits to run branches.

The `maury/run/<run-id>` branch from
[ADR-0022](0022-branch-per-mining-run.md) is **created at
`--resume-pending` time, not at `--store-only` time.** The
store-only step is pure transcript prep (read + normalize +
stage); no commits, no branches. This keeps the offline phase
side-effect-free relative to git state.

This costs ~no information (the transcripts on disk are still
there) but lets a user "do the prep work" while offline and
finish when back online.

### Air-gap pattern

For environments where the host **never** has internet (security-
restricted, USB-only):

1. **Bootstrap:** `maury init --from-tarball <path>` — sanctioned
   per [ADR-0018](0018-minimum-bootstrap-ux.md).
2. **Sync:** Periodically transfer a fresh tarball of the
   maury-base repo via the sneakernet channel, then ingest it.
   **The current implementation gap:** ADR-0018 specifies
   `--from-tarball` as a one-time bootstrap path; it does not
   define a re-run-as-update behavior, and `~/.maury-host-id`
   is "created once and never modified." Two options for closing
   this gap (followup):
   - Amend ADR-0018 to specify "re-run with `--from-tarball`
     when host-id already exists treats the tarball as the new
     source repo and updates the local clone in place,"
   - Or add a `maury sync --from-tarball <path>` mode that
     does what `maury sync` does (drift check + render + apply)
     but with the tarball-extracted directory as the local
     "remote" instead of `git pull`. (Cleaner; localizes the
     change to the sync command.)

   Until that's resolved, the air-gap update path requires
   manual intervention: extract the tarball over the existing
   clone directory, then run `maury sync --offline`.

3. **Mining:** `maury mine --store-only` saves prepared windows;
   transfer the staging dir out via sneakernet to an online host
   for processing. The staging dir is self-contained — copy it
   to an online host running maury, run
   `maury mine --resume-pending --staging <path>` there.

The pattern is **mostly** composition of existing primitives
(`--from-tarball`, `--offline`, `--store-only` /
`--resume-pending`) plus the manual update workflow noted above
until the followup lands.

### Configuration: no `offline: true` in manifest

We considered (and rejected) a manifest-level `offline: true`
toggle. Rejected because:

- It's another concept the user has to know about and reason
  about.
- It encourages "set and forget" — leading the user to never
  see network errors when their network IS working.
- Per-command `--offline` flag is more honest: every invocation
  acknowledges the choice.

## Consequences

- **Read-only commands always work.** `render`, `doctor`,
  `status`, `agency validate`, `rules trace` — never refuse on
  network.
- **Network-dependent commands fail loud by default.** No
  silent degradation. The user opts in to degraded mode with
  `--offline` (sync, promote) or `--store-only` (mine).
- **Mining has a real prep-now-finish-later workflow.** Useful
  on planes / in tunnels.
- **Air-gap is supported via composition**, not a special mode.
  The same `--from-tarball` and `--offline` flags compose into
  the air-gap workflow.
- **No probe latency.** All commands run at full speed; network
  failure is detected at the actual operation, with a clear
  error message naming the dependency that failed.
- **Cron / CI safety.** Per
  [ADR-0017](0017-drift-detection-and-reconciliation.md)'s
  `--non-interactive` semantics, a cron'd `maury sync` on a
  flaky-network host will exit 1 cleanly rather than silently
  diverging from the team. ADR-0028's default-refuse policy
  composes with that: the cron operator sees the failure in
  their log and can decide whether to add `--offline` to the
  cron command or fix the network. (See ADR-0017 for the full
  cron-safe semantics; this ADR doesn't re-specify them.)
- **Implementation cost is small.** Each command's network
  call needs error handling (already needed) plus a
  maury-specific error message. The `--offline` flag is one
  conditional per relevant command. The `pending-mining/`
  staging dir is a directory-walk + rename operation.

## Alternatives considered

- **Silent degrade by default.** Rejected: tenet 1. A user
  unaware they're running against stale state can ship work
  built on outdated rules.
- **Pre-probe network on every command.** Rejected: probes lie
  and add latency. The actual operation is the only honest
  signal.
- **A global `MAURY_OFFLINE=1` env var.** Considered. Rejected:
  same problem as manifest-level — set-and-forget hides the
  choice from later invocations. Per-command `--offline` keeps
  the choice visible.
- **Cache the last successful sync result with a TTL.**
  Considered. Rejected for v1: adds cache-staleness questions
  (when does the cache expire? what does maury do at the TTL
  boundary?). Local clones are already the cache; we don't
  need a parallel one.
- **Hybrid: auto-detect offline via a single 2s probe at
  startup.** Rejected: probes still lie. A 2s probe also dwarfs
  the latency of most maury commands.

## Build-order placement

The `--offline` flag on `sync` and `promote` lands as a small
addition during their respective phase work (Phase 5 sync,
Phase 9 promotion). The mining `--store-only` /
`--resume-pending` flow lands in Phase 6 (mining) as part of
the LLM-backend work tracked by [ADR-0012](0012-llm-backend.md).
None of this is blocking — read-only commands already work
offline by virtue of having no network calls.

## Followups

- **Stale-clone warning when `--offline` is used on `sync`.**
  Render output should include a comment block at the top of
  every rendered file noting "rendered offline from clone last
  pulled <timestamp>" so a user reading `~/.claude/CLAUDE.md`
  later sees how stale the source was.
- **`docs/patterns/air-gap.md` — promoted from "v1.1 nice-to-
  have" to "v1 doc work."** This ADR sells "air-gap is just
  composition," but a user can't follow that composition without
  a concrete walkthrough doc. Lands as part of the gap-F
  implementation slice (alongside the `--offline` and
  `--store-only` flags).
- **Network-aware cron.** If a user's `crontab` runs
  `maury sync` hourly and the network is down for two days, they
  get 48 cron-failure emails. v1.1: a `--quiet-on-network-fail`
  flag that distinguishes "network problem" (silent skip,
  exit 0) from "real error" (exit 1). v1: cron operator owns
  this via shell-level wrapping.

## Claude Code references

This ADR introduces no new Claude Code dependencies. The LLM
backend abstraction (per [ADR-0012](0012-llm-backend.md)) is
what surfaces the LLM-unreachable error to mining; everything
else is git-level or pure-local.

## Amendment history

- 2026-05-11 — `maury manifest validate` → `maury agency validate` per ADR-0037. No behavioral changes.
