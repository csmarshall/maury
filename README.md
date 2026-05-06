# maury

> *On a mission from Claude.*

```
  ┌──────────────────────────┐
  │  MAURY SLINE             │
  │  ─────────────           │
  │  Talent Agent            │
  │  Configs · Hosts · Skills│
  │  Chicago, IL             │
  └──────────────────────────┘
```

Multi-host Claude Code configuration sync, profile isolation, and
learned-rule mining. Maury keeps `~/.claude/` consistent across machines,
isolates "contexts" (home / work / per-client) at the GitHub-repo level
so personal content cannot leak onto a work device, and periodically
mines local conversation transcripts to propose updates to your CLAUDE.md,
settings, hooks, and skills.

## Why maury exists

If you use Claude Code on more than one machine, you've felt the gap:
your CLAUDE.md is gold on your workstation, missing on the linux box,
half-applied on the work laptop. You add a useful skill on one machine
and forget it
elsewhere. The same correction surfaces in three transcripts because
nothing carries the lesson across.

The community has partial solutions:

- **[jean-claude](https://github.com/MikeVeerman/jean-claude)** does
  multi-host config sync — but only two hardcoded profiles
  (work/personal) and no learning loop.
- **[claude-diary](https://github.com/rlancemartin/claude-diary)** mines
  transcripts to propose CLAUDE.md updates — but single-host, no
  profiles, auto-appends instead of queuing for review.
- **[chezmoi + age](https://www.arun.blog/sync-claude-code-with-chezmoi-and-age/)**
  is the well-known DIY path — but it's a generic dotfile manager that
  knows nothing about Claude Code, profile boundaries, or learning.
- **Anthropic ships nothing** for personal multi-host config sync.
  `/doctor` is a plumbing health-check; auto-memory (v2.1.59+) is
  per-project and machine-local.

**The combination** — N user-defined profiles + repo-level isolation +
local-only mining + rule-engine classification + cross-OS hook
portability + active in-session capture — is what no single tool does.
That's maury.

The project's purpose, captured as tenet #2 in
[`docs/tenets.md`](docs/tenets.md): *within a profile, Claude's
behavior should be identical across every host you use. Across
profiles, differences must be explicit.* Drift within a profile is
a bug; differences between profiles are a feature. (Tenet #1 is
"first, do no harm" — the operating principle for every state-
changing operation.)

## What it does

Three pillars:

1. **Sync** — `~/.claude/CLAUDE.md`, `settings.json`, agents, skills,
   keybindings, and hooks rendered from a versioned `base + profile + host`
   overlay. Cross-OS portable (macOS, Linux, FreeBSD).
2. **Profile isolation** — repo-per-trust-boundary architecture with
   per-host SSH deploy keys. A work host literally cannot read or push to
   personal-context bytes; isolation is enforced server-side by GitHub
   access control, not by client-side filtering.
3. **Learning** — local-only mining of `~/.claude/projects/*.jsonl`
   produces sanitized fragments classified by a deterministic, learnable
   rule engine. Proposals queue for review; reclassifications synthesize
   new rules.

## Why "Maury"?

Maury Sline is the talent agent in *The Blues Brothers* who books Jake
and Elwood's gigs — the closest thing to a manager the brothers have.
The metaphor maps cleanly: an agent who books your configs across town,
knows where everyone needs to be, and gets the band on stage.

The deeper thread is a four-generation family tree from the model down
to the tool that manages it:

```
                    ┌─────────────────────────┐
                    │  Claude  (Anthropic LLM)│
                    └────────────┬────────────┘
                                 │  named after
                                 ▼
              ┌────────────────────────────────────┐
              │  Claude Elwood Shannon             │
              │  father of information theory      │
              └────────────────┬───────────────────┘
                               │  shares "Elwood" with
                               ▼
                ┌──────────────────────────────┐
                │  Elwood Blues  (Dan Aykroyd) │
                │  The Blues Brothers          │
                └──────────────┬───────────────┘
                               │  managed by
                               ▼
                ┌──────────────────────────────┐
                │  Maury Sline (Steve Lawrence)│
                │  Elwood's booking agent      │
                │  the namesake of this tool   │
                └──────────────────────────────┘
```

So `claude → elwood → maury` traces the lineage from the model, through
information theory, through Chicago, to the tool that manages your
Claude config across hosts. The tagline writes itself.

## Status

Pre-v0.1, scaffolding stage. See [`docs/tenets.md`](docs/tenets.md) for
the principles maury is built on and [`docs/adr/`](docs/adr/) for the
architecture decisions.

## Install

> The instructions below are the **developer / clone-from-source** flow
> for hacking on maury itself. Once published, the canonical user-install
> path will be `pipx install maury` (per
> [ADR-0018](docs/adr/0018-minimum-bootstrap-ux.md)) — but the package
> isn't on PyPI yet.

Requires Python ≥3.11 and [uv](https://docs.astral.sh/uv/).

```sh
# macOS
brew install uv

# Ubuntu
curl -LsSf https://astral.sh/uv/install.sh | sh

# FreeBSD (note: don't use `uv python install` — no FreeBSD CPython artifacts)
pkg install python311 uv
```

Then:

```sh
uv sync
uv run maury --help
```

## Architecture (one paragraph)

A host has one active profile. Profiles compose `base` + (optional
inheritance chain) + `<profile>` fragment + `host overlay` and render to
`~/.claude/`. Each trust boundary is its own git repo; per-host deploy
keys grant scoped read/write. Hooks are written against named actions
(`notify`, `log_jsonl`, `run_script`) that the render engine resolves to
platform-specific commands using each host's capability probe. Mining
runs locally on every host and emits structured fragments; classification
is a pure rule engine over `.meta/rules.yaml`; the user reviews proposals
and reclassifications synthesize new rules.

## Related work

Maury stands on shoulders. Credit:

- **[rlancemartin/claude-diary](https://github.com/rlancemartin/claude-diary)**
  (MIT) — design reference for the mining loop. The 2+/3+ pattern
  threshold and observe-reflect-retrieve cadence are from claude-diary.
  Maury reimplements rather than depends.
- **[MikeVeerman/jean-claude](https://github.com/MikeVeerman/jean-claude)**
  — sync UX reference. Confirmed the gap a profile-aware + learning tool fills.
- **[chezmoi](https://github.com/twpayne/chezmoi)** — generic dotfile
  manager whose host-detection model inspired the capability probe. Not
  used as a dependency; the multi-repo trust-boundary architecture is
  outside chezmoi's design center.

## License

MIT. See `LICENSE`.
