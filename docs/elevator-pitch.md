# maury — the elevator pitch

**Multi-host Claude Code config sync, with mode isolation and a
learning loop.** Keep `~/.claude/` consistent across every machine
you use, partition home from work at the repo level so personal
content cannot reach a work laptop, and turn lessons from your
own transcripts into proposed rule updates.

## The problem

[Claude Code](https://code.claude.com/docs/en/overview)
keeps its per-user state in `~/.claude/` — `CLAUDE.md`,
`settings.json`, agents, skills, hooks, keybindings. The longer
you use it, the more carefully tuned that directory gets.

Now you have a second machine. And a third. And a work laptop
where the personal `CLAUDE.md` absolutely should not appear.

Today there is no first-party answer. You hand-copy files, run
a generic dotfile manager that knows nothing about Claude Code,
or accept that one host has the gold and the rest are stale. The
same correction surfaces in three transcripts because nothing
carries the lesson across.

## The shape of the solution

- **Composed renders, not file copies.** Your `~/.claude/` is
  rendered from a `base` layer (the universal stuff) plus a chain
  of **mode** layers (`personal`, `work`, `work:client-acme`, …)
  plus optional `rules` sublayers (shared conventions / team
  precepts). Same inputs → same output on every host.
- **One git repo per trust boundary.** Each mode lives in its own
  repo with per-host SSH deploy keys. The work laptop cannot
  fetch personal-mode bytes because the git provider will not
  serve them to that key. Isolation is server-side, not
  client-side filtering. (See tenet
  [#3 Trust boundaries are physical, not policy](tenets.md#3-trust-boundaries-are-physical-not-policy).)
- **Local-only mining of your transcripts.** Maury reads
  `~/.claude/projects/*.jsonl` on each host, extracts sanitized
  fragments, classifies them through a **deterministic rule
  engine** (no LLM judgment for routing-sensitive decisions),
  and queues proposals for you to review.
- **Cross-OS portable.** Hooks are written against named actions
  (`notify`, `log_jsonl`, `run_script`) that resolve to
  platform-specific commands using a per-host capability probe.
  macOS, Linux, FreeBSD share one source of truth.
- **Promotion, not auto-sync, across boundaries.** A useful
  pattern found while in `work:client-acme` can be promoted up
  the mode tree (to `work`, or further to `base`) through curator
  review. Nothing flows sideways or down without a human.

## How maury sits alongside parallel efforts

The README's [Related work](../README.md#related-work) section
credits each project in detail. Short version: each tool in this
space covers one piece — `jean-claude` does two-profile sync,
`claude-diary` does single-host mining, `chezmoi` is a generic
dotfile manager that does not know what a mode is. **The
combination** — N user-defined modes + repo-level isolation +
local-only mining + deterministic rule engine + cross-OS hooks +
active in-session capture — is the slice maury composes on top.
Anthropic-native tooling solves a different problem: `/doctor` is
a plumbing check and v2.1.59+ auto-memory is per-project and
machine-local.

## Who maury is for

- **You use Claude Code on more than one machine** and have felt
  the drift.
- **You want home and work configs strictly partitioned**, with
  the partition enforced by server-side ACLs rather than your own
  discipline.
- **You're a small team curating shared Claude Code conventions**
  (a `rules` repo) and want PR-style contribution flow without
  hand-passing dotfiles. See
  [`patterns/team-upstream.md`](patterns/team-upstream.md).

## Who maury is **not** for

- One-machine users — for now, `claude-diary` (mining alone) or
  Anthropic's own auto-memory will serve you better.
- People who want zero-config magic. Maury is explicit on
  purpose: `repo_mode: ro` by default, no auto-promotion, the
  user arbitrates every ambiguity (tenets
  [#5](tenets.md#5-the-user-arbitrates-ambiguity) and
  [#11](tenets.md#11-explicit-beats-implicit-with-conservative-defaults)).
- Anyone wanting to swap git for S3, Dropbox, or another VCS.
  Maury's design is built on git's commit log, branches, content
  addressing, and three-way merge —
  [it does not abstract over the substrate](concepts.md#what-maury-assumes-about-its-substrate).

## Honest status

Pre-v0.1, scaffolding stage. Some commands work today
(`init`, `render`, `doctor`, the rule engine, mining
extraction); the drift-detection and promotion paths are
designed but not yet wired. See
[`docs/status.md`](status.md) for the authoritative
shipped-vs-planned table.

## Where to go next

- **[`docs/concepts.md`](concepts.md)** — the canonical mental
  model (uniform / lockers / vending machines analogy, then
  formal definitions). Start here.
- **[`docs/tenets.md`](tenets.md)** — the eleven principles every
  ADR is checked against.
- **[`docs/status.md`](status.md)** — what works today.
- **[`docs/adr/`](adr/)** — every architecture decision, indexed
  by topic in the [ADR README](adr/README.md).

The name comes from Maury Sline, the talent agent in *The Blues
Brothers* who books the band's gigs — fitting for a tool that
books your configs across town. There's a longer lineage joke in
the [README](../README.md#why-maury); enjoy it on the way out.
