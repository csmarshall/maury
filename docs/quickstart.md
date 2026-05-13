# maury — quickstart (≈5 minutes)

This is the **try-without-committing** path. By the end you'll have
seen maury render a Claude Code config tree from the bundled
`base-template/` seed, validated the manifest, traced a rule against
sample text, and run `maury doctor` against the rendered output —
all in dry-run mode, nothing written outside `/tmp`.

If after this you decide maury is for you, the longer-form
"set up your own base repo" walkthrough lives in
[`workflow.md`](workflow.md) and the per-command operational
reference is in [`operations.md`](operations.md).

> **Calibration:** maury is pre-v0.1, scaffolding stage. The
> commands below all work today; the broader "sync across multiple
> hosts" story is partially implemented per
> [`status.md`](status.md). This quickstart deliberately uses only
> the shipped, pure-local commands.

## Prereqs

- Python ≥ 3.11
- [`uv`](https://docs.astral.sh/uv/) (or `pipx` once maury is on
  PyPI — not yet)
- About 5 minutes

## 1. Clone and install (≈1 min)

```sh
git clone https://github.com/<org>/maury.git
cd maury
uv sync
```

`uv sync` creates a project-local virtualenv and installs maury's
dependencies. From here on, `uv run maury <args>` runs the CLI.

```sh
uv run maury --version
uv run maury --help
```

## 2. Inspect the seed (≈1 min)

The repo ships a `base-template/` directory — a minimal example of
the layout a real `maury-base.git` repo would have. It contains a
universal `CLAUDE.md`, a `settings.json`, two example profiles
(`home`, `work`), one example host (`workstation`), a starter
`rules.yaml`, and the `.meta/manifest.json` that ties them together.

```sh
find base-template -type f
```

Open `base-template/CLAUDE.md` and
`base-template/profiles/home/CLAUDE.md.fragment` — these are the
two files that will compose into the rendered output below. Every
file in `base-template/` carries placeholder content (`<your-…>`)
that you would replace when forking this as your own base repo.

## 3. Validate the manifest (≈30 sec)

```sh
uv run maury manifest validate --manifest-file base-template/.meta/manifest.json
```

Expected output:

```
OK: 3 profile(s), 2 host(s).
```

If you get a schema error, the manifest has drifted; file an issue.

## 4. Trace a rule against sample text (≈30 sec)

```sh
uv run maury rules trace --rules-file base-template/.meta/rules.yaml \
  "I'm using ruff and mypy"
```

You'll see which rules fired (`[hit]`), which didn't (`[miss]`),
and the final classification:

```
classification: base (confidence=high)
forbidden_by: -

rules considered:
  [hit ] code-style-python       classify  any_keyword matched: ['ruff', 'mypy']
  …
```

`maury rules trace` is the dry-run lens on the classifier — useful
for "would this be classified into the right profile?" before
mining ever runs.

## 5. Dry-run a render (≈1 min)

```sh
mkdir /tmp/quickstart-claude
uv run maury render --check \
  --manifest-file base-template/.meta/manifest.json \
  --profile home --host workstation \
  --target /tmp/quickstart-claude
```

Expected output (byte counts may differ by a handful if the seed
has been edited):

```
rendering host=workstation profile=home (layers=3, files=2)
  would write  CLAUDE.md  (new, 2376 bytes)
  would write  settings.json  (new, 55 bytes)
(--check; no files written)
```

The `--check` flag is the safe way to see what *would* happen
without writing anything (the same flag is on every state-changing
maury command — `init`, `sync`, etc.).

> **Vocabulary note.** The CLI still uses `--profile` as the flag
> name and prints `profile=…` in its output, but the canonical
> term in maury's design docs is **mode** (per
> [`concepts.md`](concepts.md) and ADR-0037). They mean the same
> thing — a rename to `--mode` is queued for the next manifest
> schema bump per
> [ADR-0030](adr/0030-manifest-schema-migrations.md). Until then,
> `--profile` (the flag) and "mode" (the concept) coexist.

Drop the `--check` flag to actually write, then look at the
rendered files:

```sh
uv run maury render \
  --manifest-file base-template/.meta/manifest.json \
  --profile home --host workstation \
  --target /tmp/quickstart-claude

cat /tmp/quickstart-claude/CLAUDE.md
```

You should see the universal base `CLAUDE.md` content followed by
the `home` profile fragment followed by the `workstation` host
fragment — three layers composed in order, with provenance
comments showing where each piece came from.

## 6. Run `maury doctor` against the output (≈1 min)

[`maury doctor`](adr/0011-anthropic-rubric-integration.md) evaluates
a `CLAUDE.md` against Anthropic's published rubric for high-quality
agent memory files. Point it at the rendered file:

```sh
uv run maury doctor --file /tmp/quickstart-claude/CLAUDE.md
```

You'll get a rubric-scored summary. The bundled seed is intentionally
minimal — `doctor` may flag "could be improved" findings depending
on rubric version. The point isn't to ace the rubric on the seed,
it's to see how the tool reports against any `CLAUDE.md` you point
at.

## 7. Clean up

```sh
rm -rf /tmp/quickstart-claude
```

## What you've seen

- **Layered render.** base + profile fragment + host fragment
  composed into one `~/.claude/` tree, with provenance.
- **Validated manifest.** Schema integrity checked before any
  action.
- **Classifier trace.** Deterministic rule engine, dry-run-able.
- **Quality rubric.** `maury doctor` against any `~/.claude/`.

None of that touched your real `~/.claude/`. Everything ran against
the seed and `/tmp`.

## What you have NOT seen (and where to read next)

- **Multi-host sync.** [`workflow.md`](workflow.md) diagram 2 and
  [`operations.md` §`maury sync`](operations.md#maury-sync) cover
  the daily-driver loop. Drift detection is partially shipped per
  [`status.md`](status.md).
- **Trust-boundary isolation.** The seed is one base repo on disk;
  the real architecture is one git repo per trust boundary with
  per-host SSH deploy keys.
  [`concepts.md` §4](concepts.md#4-trust-boundary) and
  [ADR-0002](adr/0002-repo-per-trust-boundary.md) explain why and
  how. [ADR-0003](adr/0003-per-host-deploy-keys.md) covers the
  deploy-key model.
- **Mining + review.** Locally extracting durable preferences from
  `~/.claude/projects/*.jsonl` transcripts —
  [`workflow.md`](workflow.md) diagram 3 walks the loop; mining
  itself is partially shipped per [`status.md`](status.md).
- **Team-shared rules.** A curator publishes a `rules` repo;
  engineers consume + contribute back via PR. See
  [`patterns/team-upstream.md`](patterns/team-upstream.md).

## Next steps

- If you want the full mental model:
  [`concepts.md`](concepts.md) (wardrobe / lockers / vending
  machines, then formal definitions).
- If you want a term you saw above defined precisely:
  [`glossary.md`](glossary.md).
- If you want the *target* (post-v0.1) shape vs. *today's* shape:
  [`status.md`](status.md).
- If you want to set up a real maury install (your own base repo,
  multiple hosts): not yet documented as a step-by-step guide —
  this is the next gap on the roadmap. Watch
  [`status.md`](status.md) for when it ships.
