# maury — FAQ

Short answers to the questions evaluators most often ask before
deciding whether to invest. Each answer links out to the ADR or
doc that owns the depth.

For the longer reads:
- **[elevator pitch](elevator-pitch.md)** — two-minute flyby.
- **[quickstart](quickstart.md)** — five-minute try-without-committing.
- **[concepts](concepts.md)** — canonical vocabulary.
- **[parallel efforts](parallel-efforts.md)** — how maury sits
  alongside other tools in the same space.
- **[ADR index](adr/README.md)** — every design decision, with
  rationale and cited tenets.

---

## How is this different from a dotfile manager (chezmoi, yadm, stow)?

Dotfile managers know nothing about Claude Code. They treat
`~/.claude/` as opaque bytes — copy this file from there to here.
That's enough for the static config (CLAUDE.md, settings.json),
but it misses the *behavioral* surface that matters:

- **Trust boundary** — a dotfile manager can put one repo on
  every host. maury treats home and work as separate repos
  per [ADR-0002](adr/0002-repo-per-trust-boundary.md), so a
  work laptop is physically incapable of cloning the home
  content (no SSH key access; per
  [ADR-0003](adr/0003-per-host-deploy-keys.md) the boundary is
  enforced by what each host's deploy key can pull, not by
  filename hygiene).
- **Mode composition** — `home → personal-website` inherits
  from `home`'s rules and adds project-specific ones. Dotfile
  managers force you to flatten or duplicate; maury's
  inheritance is structural (see
  [concepts §5](concepts.md#5-mode-tree-and-inheritance)).
- **Drift detection** — maury tracks what it rendered last so
  hand-edits surface as drift rather than silently overwriting
  your work, per
  [ADR-0017](adr/0017-drift-detection-and-reconciliation.md).
  Dotfile managers don't.
- **Mining loop** — maury reads your past transcripts and
  proposes rule additions for things you've corrected more
  than once, per
  [ADR-0005](adr/0005-local-only-mining.md) and
  [ADR-0008](adr/0008-claude-diary-reference.md). No dotfile
  manager does this.

The [parallel-efforts](parallel-efforts.md) doc walks through
chezmoi, jean-claude, claude-diary, ccms, and Anthropic's
native sync feature side-by-side.

## Why git as the substrate? Why not a database or an API?

The full list of properties maury depends on git for lives
in [concepts.md §"What maury assumes about its substrate"](concepts.md#what-maury-assumes-about-its-substrate).
The short version:

1. **Every developer has it.** A maury host needs `git` and
   nothing more (no daemon, no auth server, no API endpoint).
2. **Server-side access enforcement.** SSH-key access on
   the remote is the trust-boundary primitive — already
   the access model for git repos, no new mechanism to
   invent. Per
   [ADR-0002](adr/0002-repo-per-trust-boundary.md), this is
   why each trust boundary gets its own repo: a host with
   the work deploy key cannot pull home repos.
3. **History, audit trail, and review** come free — every
   change is a commit, every review is a PR.

The cost is that maury inherits git's quirks (no native
file-level access control inside a repo, no atomic
cross-repo transactions). The trade is intentional;
the alternatives all introduced *more* failure modes
than git's quirks add.

## Why N modes, not just hardcoded home/work?

Per [ADR-0001](adr/0001-n-profiles.md), modes are a tree,
not a binary. A real configuration looks like:

```
home
├── personal-website
└── side-project-x
work
└── client-engagement-a
```

…where `personal-website` inherits home's rules and adds its
own. Hardcoding home/work would force every cross-context
distinction (work ↔ specific-client, home ↔ specific-side-project)
to live in one flat namespace.

The cost is that the tree is a *user-defined* artifact — you
declare what modes exist by editing the manifest. Most users
end up with 2-3 modes; the structural flexibility shows up
when you genuinely need it (multiple clients, a side
project that needs its own LLM personality, etc.).

## Can my work laptop accidentally pull my personal CLAUDE.md?

**Partly structural, partly operational.** Per
[ADR-0041](adr/0041-per-mode-anthropic-credentials.md)
(which defines the work/home boundary as a three-piece
contract; pieces 1 and 4 below are structural, pieces 2
and 3 are operational) and summarized in
[security-model.md](security-model.md):

1. **Per-host SSH deploy keys (structural).** Per
   [ADR-0003](adr/0003-per-host-deploy-keys.md), a work
   laptop has a deploy key that grants access ONLY to the
   work repos. The home repos refuse the SSH connection.
2. **Per-mode Anthropic credentials (operational).** Work
   should use work Anthropic credentials; home should use
   home's. Claude Code is single-account-per-install today
   so maury cannot structurally enforce this — the user
   maintains the discipline of running `claude` under the
   right account per mode.
3. **Active-account visibility (operational).** maury
   reports the active Claude Code account in `maury status`
   so a mismatch is visible. Detection, not prevention.
4. **Host-identity guard (structural)**, added in
   [ADR-0042](adr/0042-host-identity-guard.md) (2026-05-14):
   maury records an identity baseline at first sync and
   refuses to sync, render, or mine if the locally-stored
   host_id hex changes. Catches three failure modes:
   accidental hex edit, cross-host paste (copying another
   laptop's host_id), and "I tried to move this host
   between modes by editing the file."

The honest summary: a determined user can defeat (2) and (3)
by mis-running `claude`; the structural pieces (1) and (4)
make accidents very hard. See
[security-model.md §"What maury structurally protects against"](security-model.md)
for the full failure-mode table.

## Is mining sending my transcripts to Anthropic or a third party?

Per [ADR-0005](adr/0005-local-only-mining.md), raw
transcripts **never leave the host**. Mining is local-only.

What goes to the network:
- **The LLM call** that classifies a candidate finding. Per
  [ADR-0041](adr/0041-per-mode-anthropic-credentials.md),
  this uses the mode's Anthropic credentials. The call
  includes the candidate finding text and a CLAUDE.md
  cross-reference excerpt — NOT the full transcript.
- **Nothing else.** No telemetry, no usage reporting, no
  background sync to a maury-operated service. maury has no
  service backend.

The [mining cost model](#whats-the-cost-of-running-maury-mine)
below covers what the LLM call itself costs.

## What if I hand-edit `~/.claude/CLAUDE.md` after `maury sync`?

Per [ADR-0017](adr/0017-drift-detection-and-reconciliation.md),
hand-edits are **first-class input**. maury records a
`last-render.json` baseline at sync time and detects drift
on the next sync.

You get five choices when reconciling (interactively at sync
time, or explicitly via `maury reconcile`):
- **adopt** — capture the edit as a proposal queued for
  review; promote later via `maury review`.
- **adapt** — same as adopt, but normalize through the
  best-practices rubric first.
- **mark-managed** — tell maury "this file is now mine on
  this host; stop rendering it." Recorded so this host stops
  rendering the path. (Whether the marker syncs to peers
  has open ADR-0017 nuance; conservative read: per-host
  today.)
- **revert** — restore to last-rendered. The hand-edit is
  discarded (with an audit log entry).
- **skip-once** — leave the edit as-is for this sync; it'll
  resurface as drift next time.

`maury status` summarizes current drift counts so you can
spot a forgotten hand-edit before sync time.

## What happens if Anthropic changes Claude Code's transcript format?

Per
[`cc-contract:transcript-jsonl-stability`](claude-code-contract.md#cc-contracttranscript-jsonl-stability),
maury empirically verifies the transcript JSONL schema
against the installed Claude Code version via
`maury verify-cc-transcript-schema`.

Two failure modes the verifier catches:
- **Core fields drift** — `type`, `sessionId`, or `timestamp`
  rename → `all_lines_have_core_fields` flips to false.
- **Message fields drift** — `message.role` or
  `message.content` restructure → `message_bearing_lines_have_message_fields`
  flips to false; mining will break on next run.

The response if drift is detected is designed in
[ADR-0044](adr/0044-strict-transcript-parser-and-schema-lock.md):
a maury-side JSON Schema describing the verified transcript
shape, a strict parser that validates each line against it,
and a compatibility list pinning known-good Claude Code
versions. Phased rollout — strict mode is opt-in via
`--strict` in v1, then default in v1.1+ once shadow-mode
data justifies the flip.

Re-run the verifier after every Claude Code minor-version
bump that might touch transcript output.

## What if I rename my host or move it between modes?

The 8-hex prefix of your host ID is **immutable identity**;
the trailing `_<tag>` suffix is a freely-editable cosmetic
label. Per
[ADR-0015](adr/0015-surrogate-keys-for-hosts-and-profiles.md):
every host ID is `host_<8 hex>_<tag>`. (The pre-release
untagged `host_<32 hex>` form was retired 2026-05-19; the
guard only ever needs to compare 8-hex prefixes.)

- Edit the tag (`host_24b2a0aa_laptop` → `host_24b2a0aa_main-laptop`)
  — fine, no consequences.
- Edit the hex (`host_24b2a0aa_laptop` → `host_88ff77ee_laptop`)
  — caught by the identity-baseline guard
  ([ADR-0042](adr/0042-host-identity-guard.md)); maury refuses
  sync/reconcile/mine and tells you how to proceed
  deliberately.

For an actual mode transfer (work laptop → home laptop),
the path per [ADR-0039](adr/0039-bootstrap-and-host-lifecycle.md):
deregister from the old mode (clears the local host_id), then
`maury init --reset` into the new mode (generates a fresh
host_id). Host identity is mode-scoped intentionally — the
audit trail stays unambiguous.

## What's the cost of running `maury mine`?

It depends on the backend:
- **`--backend cli`** (default) — uses your Claude Code
  subscription quota. No additional charges; consumes the
  same monthly quota Claude Code uses for everything else.
- **`--backend sdk`** — uses your Anthropic API key
  directly, billed per-token at standard API rates.

`maury mine` is incremental by default per
[ADR-0043](adr/0043-incremental-mining.md): the first run
on a project processes its entire transcript history (the
cost depends on how much you've used Claude Code there);
subsequent runs process only the delta since the last
watermark, so the cost converges to roughly one extraction
window per new session.

Cap the per-run cost with `--max-windows N` if you want a
hard ceiling regardless of how much history is pending.

## More questions?

[Open an issue on GitHub](https://github.com/csmarshall/maury/issues)
or read the [ADR index](adr/README.md) — every architectural
decision has its own ADR with rationale.
