# maury — parallel efforts in this space

A handful of other projects address pieces of the
"Claude Code on more than one machine" problem. Each one solves
something real; together they map out the design space. Maury is
one composition of these primitives, not an evaluation of which
project is "best" — different shapes work for different setups.

> **Calibration:** snapshot as of 2026-05-13. Each project is
> active; details rot fast. Click through to each repo for the
> current state.

## At a glance

| Project | Multi-host sync | Work/personal isolation | Mines transcripts | Status |
|---|---|---|---|---|
| **maury** | ✓ one git repo per trust boundary | structural — per-host deploy keys; one mode = one server-side access scope | ✓ local-only; rule-engine classified; opt-in | pre-v0.1, active |
| [jean-claude](https://github.com/MikeVeerman/jean-claude) | ✓ single git repo with N profiles | ✗ profile-aware but not enforced at the access layer | ✗ | active |
| [claude-diary](https://github.com/rlancemartin/claude-diary) | ✗ single-host | n/a | ✓ session reflection + transcript parse | active |
| [ccms](https://github.com/miwidot/ccms) | ✓ rsync over SSH; the full `~/.claude/` | ✗ syncs everything | ✗ | active |
| [chezmoi](https://github.com/twpayne/chezmoi) + [age](https://github.com/FiloSottile/age) | ✓ generic dotfile manager + per-file encryption | ⚠ encryption-based, not trust-boundary | ✗ | very active |
| [Anthropic auto-memory](https://code.claude.com/docs/en/memory) (CC v2.1.59+) | ✗ machine-local; per-project memory dir, not cross-host | ✗ | ✓ Claude writes its own per-project memory dir (`MEMORY.md` index + topic files) | shipped, default on |

## What each one does, in one paragraph

### jean-claude

A companion CLI for managing N Claude Code profiles and optionally syncing them across machines via git. Profile-aware: you can create per-context configurations (work / personal / per-client) and switch between them. Bidirectional sync via a shared git repo. Recent work adds plugin and Claude Desktop MCP support. **What maury borrowed:** the sync UX — confirmation that profile-aware + git-backed is a real workflow worth pursuing. Maury's structural difference is one git repo *per* trust boundary rather than one repo with N profiles inside; the per-trust-boundary architecture is what makes work-content-can't-reach-personal-laptop a server-side enforced property rather than a client-side discipline.

### claude-diary

A slash-command plugin (`/diary`, `/reflect`) that gives Claude Code memory across sessions. `/diary` reflects on the current session's context (user messages, tool invocations, files modified, decisions made) and writes a markdown entry to `~/.claude/memory/diary/`. `/reflect` analyzes accumulated entries and proposes updates to `CLAUDE.md`. The maintainer's [Dec 2025 writeup](https://rlancemartin.github.io/2025/12/01/claude_diary/) is the canonical introduction. **What maury borrowed:** the mining loop's design philosophy — the 2+/3+ pattern threshold (a candidate becomes a proposal after recurring 2-3 times) and the observe-reflect-retrieve cadence. Maury reimplements rather than depends because the multi-host and trust-boundary stories are out of scope for claude-diary.

### ccms

A bash script that uses rsync over SSH to push/pull the full `~/.claude/` between machines. Bidirectional, with checksum-verified syncs and rolling backups. No profile concept; what's on one machine ends up on the other. **Design space note:** ccms is the answer when "I just want my `~/.claude/` to be the same on every box I use" is the whole problem. Maury would be over-engineering for that user; ccms is right-sized.

### chezmoi (+ age)

A generic dotfile manager that templates `~/.foorc` / `~/.bashrc` / etc. across machines from a single git repo, with per-host overrides and (via age) per-file encryption. Heavily used; well-maintained; the de-facto Unix dotfile-management answer. **Design space note:** chezmoi knows nothing about Claude Code specifically — no awareness of `~/.claude/projects/*.jsonl`, no mining loop, no rule engine, no concept of a profile or trust boundary. A user could implement maury-like behavior in chezmoi by hand, but the discipline lives in their template files rather than in the tool. Encryption-based isolation is conceptually different from maury's deploy-key-per-trust-boundary model — both work, with different trade-offs.

### Anthropic auto-memory (Claude Code v2.1.59+)

Default-on feature where Claude Code itself decides what to remember from each session and writes notes to `~/.claude/projects/<project>/memory/` — a `MEMORY.md` index plus topic files (`debugging.md`, `api-conventions.md`, etc.) Claude creates as the project's knowledge grows. Toggleable via the toggle inside `/memory`, the `autoMemoryEnabled` setting, or `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`. **Design space note:** auto-memory is per-project and machine-local — Anthropic is not (yet) in the business of syncing your Claude Code state across boxes. The maury<->auto-memory relationship is complementary: auto-memory captures per-project learnings; maury would sync the learnings that promote across projects (universal preferences, mode-wide conventions) without overriding Anthropic's per-project store.

## Where maury fits

Maury's specific composition is **N user-defined modes** (not two hardcoded, not unlimited-but-flat) **+ repo-per-trust-boundary isolation** (server-side enforced) **+ local-only mining with a deterministic rule engine** (not LLM-classified routing for safety decisions) **+ cross-OS hooks via a capability probe** (one hook contract on macOS / Linux / FreeBSD) **+ active in-session capture** (planned per [ADR-0013](adr/0013-active-in-session-capture.md)). Each individual piece exists somewhere else. The combination is the gap.

If your problem is just "sync my `~/.claude/` across machines and don't worry about isolation" — ccms or chezmoi.
If your problem is "give Claude per-project memory" — Anthropic auto-memory does this natively.
If your problem is "let Claude write a diary about each session and propose `CLAUDE.md` updates" — claude-diary.
If your problem is "I want N profiles git-synced" — jean-claude.

If your problem is **"I have work and personal modes, I cannot let work content reach my personal laptop or vice versa, and I want the lessons I'm teaching Claude in one context to flow appropriately to others** — that's the slice maury composes.

## What's missing from this snapshot

If a project addresses an adjacent problem and isn't listed here, file an issue or open a PR. The set above is current as of the date at the top; the design space is moving fast. Other projects worth knowing about that didn't make this comparison table for space:

- [`porkchop/claude-code-sync`](https://github.com/porkchop/claude-code-sync) and [`perfectra1n/claude-code-sync`](https://github.com/perfectra1n/claude-code-sync) — both sync conversation-history JSONL across machines (bash + Rust respectively). [`toroleapinc/claude-brain`](https://github.com/toroleapinc/claude-brain) takes a different angle: LLM-based semantic merge of memory / skills / agents.
- [`centminmod/my-claude-code-setup`](https://github.com/centminmod/my-claude-code-setup) — opinionated starter template for a personal Claude Code setup; not a sync tool but useful as seed content.
- Anthropic's [Workspaces](https://platform.claude.com/docs/en/manage-claude/workspaces) — server-side organizational isolation; the natural complement to maury's client-side mode boundaries (see [ADR-0041](adr/0041-per-mode-anthropic-credentials.md)).

## See also

- [`README.md` §"Related work"](../README.md#related-work) — the short-form credit list (this doc is the long form).
- [`elevator-pitch.md`](elevator-pitch.md) — the 2-minute "is maury for me?" page.
- [`security-model.md`](security-model.md) — what maury's trust-boundary isolation actually buys you, with concrete threat walkthroughs.
- [`adr/0010-all-three-pillars-v1.md`](adr/0010-all-three-pillars-v1.md) — the scope decision: maury's three pillars (sync + isolation + learning) ship together because the value comes from the composition, not from any one pillar alone.
