# ADR-0043: Incremental mining via per-project watermark

**Status:** Accepted
**Date:** 2026-05-14

## Related tenets

- [Tenet 11 — Explicit beats implicit, with conservative defaults](../tenets.md#11-explicit-beats-implicit-with-conservative-defaults) — `maury mine` defaults to incremental (cheap, idempotent); a `--full` flag exists for explicit re-mine.
- [Tenet 9 — Defer to the platform](../tenets.md#9-defer-to-the-platform) — Claude Code's project-dir derivation is the platform's algorithm; maury adopts it (verified via `cc-contract:project-directory-derivation`) rather than maintaining a parallel implementation. This ADR promotes that algorithm from verifier-only to load-bearing production code.

## Related ADRs

- [ADR-0026](0026-profile-aware-mining.md) — mining the transcript history for durable preference candidates; this ADR adds state tracking so the operation is incremental rather than always-full.
- [ADR-0029](0029-maury-state-layout-contract.md) — `~/.local/state/maury/` inventory; this ADR adds `last-mine.json` to the inventory.
- [ADR-0035](0035-audit-log.md) — mining runs are audit-logged; the watermark itself is not audit data (it's per-host state).

## TL;DR

`maury mine` walks Claude Code's `~/.claude/projects/<dir>/*.jsonl`
transcript files and asks an LLM to extract durable preference
candidates. Today every run re-mines from scratch — slow,
expensive (LLM cost per window), and discouraging frequent use.
This ADR adds a per-project watermark file at
`~/.local/state/maury/last-mine.json` that records the highest
JSONL mtime processed per project. Default `maury mine` becomes
**incremental** (mine only data newer than the watermark);
`--full` re-mines everything; a `mining_algorithm_version` field
forces full re-mine when the extraction logic changes. Also wires
the verified `derive_project_dir()` algorithm into mining's
default behavior so `maury mine` (no args) means "mine THIS
project I'm sitting in," not "mine the busiest project in
`~/.claude/projects/`."

## Context and Problem Statement

`maury mine` was designed in Phase 6a/6c as a tool for surfacing
durable user preferences from past Claude Code sessions —
patterns the user has stated more than once across windows of
messages, candidates to add to `CLAUDE.md`. The extraction is
LLM-bound: for each window of N messages, an extraction prompt
asks the LLM to find preference candidates and a cross-reference
prompt asks "is this already in CLAUDE.md?"

Two problems with the current implementation:

1. **No state.** Each `maury mine` run starts from scratch — it
   walks every `.jsonl` in the chosen project dir and re-feeds
   every window to the LLM. If you mined yesterday and have one
   new session today, you'd still re-mine yesterday's content.
   The LLM cost scales with total transcript history, not with
   the delta. This makes frequent mining cost-prohibitive.

2. **Project selection is by busiest count.** Without `--project`,
   the command picks the project dir with the most user messages.
   That's a reasonable fallback but not what most users mean. If
   you're sitting in `~/work/project`, you probably want
   `maury mine` to mine the maury project, not whichever project
   has accumulated the most transcripts over your machine's
   lifetime.

Problem (1) is the bigger gating issue. Without incremental
support, `maury mine` is too expensive to run as a regular
hygiene operation; it can only be invoked as a deliberate
one-shot. With incremental support, it becomes a thing you can
run at the end of any work session for ~the cost of one extra
LLM call (one window of new content).

Problem (2) is a UX paper cut that becomes pressing once (1) is
fixed: a cheap incremental `maury mine` only saves users effort
if they don't have to type `--project -Users-jdoe-work-project`
every time.

The verified `derive_project_dir()` algorithm in
`src/maury/empirical_tests.py` (🧪 since 2026-05-13 per
`cc-contract:project-directory-derivation`) maps a cwd path to
the directory name Claude Code uses. Wiring it into mining's
defaults solves (2) by deriving from `Path.cwd()`.

## Decision Drivers

- **Mining must be cheap enough to run regularly.** A tool whose
  default cost is "re-mine everything" is a tool you reach for
  rarely. The fix is state.
- **State must be in `maury-state/`, not the project dir.** Per
  the project's existing pattern (`last-render.json`,
  `claude-writes.jsonl`, `active-context.json`, etc.), maury's
  state lives under `~/.local/state/maury/`. Polluting
  `~/.claude/projects/` with maury files breaks the boundary
  the rest of the codebase respects.
- **Watermark granularity should be minimum-viable.** Mtime-per-
  project is enough for the common case; finer granularity
  (session-id + byte offset) is over-engineering for current
  failure modes.
- **Algorithm changes invalidate watermarks.** If the extraction
  logic improves, old watermarks block the user from getting the
  better extraction over historical data. The state file needs
  a version field that, when bumped, forces a full re-mine.
- **The verified `derive_project_dir()` algorithm should be
  load-bearing.** Today it lives in `empirical_tests.py` only as
  the verifier's prediction; promoting it to production code
  strengthens the case for keeping the algorithm healthy across
  Claude Code updates.

## Considered Options

For the **incremental state location:**

- **Option α:** Drop a marker file inside each project dir (`~/.claude/projects/<dir>/.maury-mined`).
- **Option β (chosen):** Central `~/.local/state/maury/last-mine.json` keyed by project dir name.

For the **default project selection:**

- **Option A:** `--cwd` flag only; default behavior unchanged (busiest project).
- **Option B (chosen):** `--cwd` flag AND change no-args default to "derive from `Path.cwd()`, fall back to busiest if cwd has no project dir."
- **Option C:** Don't wire `derive_project_dir()` into mining.

## Decision Outcome

**Chosen options:** β (centralized watermark) + B (cwd-derived
default). The watermark file goes in maury-state alongside
existing state files; the project selection defaults to "this
project" with a fallback to the legacy busiest-project behavior
only when the current cwd isn't a recognized project dir.

### Implementation details

#### State file: `~/.local/state/maury/last-mine.json`

Single JSON document. Writes use the tmp+rename pattern from
[ADR-0029 §"Cross-cutting invariants"](0029-maury-state-layout-contract.md#cross-cutting-invariants)
(invariant #4) so a crashed mining run never corrupts the
watermark file. Keyed by project dir name (the Claude Code form,
e.g., `-Users-jdoe-work-project`). Each entry records the
high-water mark for that project:

```json
{
  "schema_version": 1,
  "mining_algorithm_version": 1,
  "projects": {
    "-Users-jdoe-work-project": {
      "last_mined_at": "2026-05-14T15:42:11Z",
      "last_jsonl_mtime": "2026-05-14T14:38:02Z",
      "windows_processed": 23,
      "findings_count": 8
    },
    "-Users-jdoe-work-other": {
      "last_mined_at": "2026-05-13T22:01:55Z",
      "last_jsonl_mtime": "2026-05-13T20:47:33Z",
      "windows_processed": 12,
      "findings_count": 4
    }
  }
}
```

- **`schema_version`** — bumps when this file's structure
  changes.
- **`mining_algorithm_version`** — bumps when the extraction or
  cross-reference algorithm changes in a way that would produce
  different findings on the same input. Bumping invalidates all
  watermarks; the next `maury mine` runs as if `--full` were
  passed.
- **`last_jsonl_mtime`** — the highest mtime seen across all
  `.jsonl` files in the project when mining last ran. Compared
  against current jsonl mtimes to decide whether to mine.
- **`windows_processed`** / **`findings_count`** — audit data
  for human-facing summary in `maury status`, not load-bearing.

#### Default behavior: incremental mining

`maury mine` (no args) executes:

```
1. Resolve project dir:
   - If --project <name> passed, use that.
   - Else if --cwd <path> passed, derive_project_dir(<path>).
   - Else if Path.cwd() resolves to an existing project dir
     under ~/.claude/projects/, use that.
   - Else fall back to the busiest-project heuristic.

2. Read ~/.local/state/maury/last-mine.json:
   - If file absent: this is the first mine. Process all
     transcripts; write a fresh state file at end.
   - If file present and mining_algorithm_version matches code:
     read the project's entry. If no entry exists for this
     project, mine all. If entry exists, mine only jsonls with
     mtime > last_jsonl_mtime.
   - If mining_algorithm_version differs: ignore all watermarks;
     mine all (equivalent to --full); update version on write.

3. Run extraction.

4. On success: update last-mine.json with current state. On
   failure: do NOT update; the next run retries the same range.
```

The `--full` flag explicitly forces step 2's "mine all" branch
regardless of watermark state.

#### CLI surface

```sh
maury mine                              # incremental, cwd-derived project
maury mine --cwd ~/other/repo           # incremental, derived from given path
maury mine --project <name>             # incremental, explicit project name
maury mine --full                       # re-mine everything (any project flag)
maury mine --since 2026-05-01           # mine messages newer than date (overrides watermark)
```

`--since` is an additional flag the watermark machinery makes
cheap to implement, useful for "mine the last two weeks even
though I've already mined that range." Treated as an override
that does not update the watermark on success (so the next
default run picks up where the user left off, not from
`--since`'s date).

#### `derive_project_dir()` wiring

The algorithm lives in `src/maury/empirical_tests.py` today
because that's where the verifier needed it. This ADR promotes
it to a public utility in `src/maury/projects.py` (new module):

```python
def derive_project_dir(cwd: Path) -> str:
    """Map a cwd to the Claude Code project-dir name.

    Algorithm verified empirically (cc-contract:project-directory-
    derivation). Re-verify on Claude Code minor-version bumps.
    """
    ...
```

`empirical_tests.py` imports from `projects.py` rather than
re-implementing — single source of truth. The verifier continues
to call this function as the prediction it cross-checks against
Claude Code's actual behavior, so a regression in the production
function is caught by the verifier's collision-pair test.

#### Failure mode: cwd not a recognized project

If `Path.cwd()` derives to a project dir name that doesn't exist
under `~/.claude/projects/`, the user is either (a) running
`maury mine` from a directory where they haven't actually used
Claude Code, or (b) using a cwd that hasn't accumulated enough
sessions yet to register.

The handling: silently fall back to the busiest-project heuristic
**and print a one-line notice**: `note: cwd 'X' has no project
dir under ~/.claude/projects/; falling back to busiest project Y`.
The notice makes the fallback visible without aborting.

The legitimate failure case — busiest fallback also has no
projects — surfaces as today's existing error:
`no projects with user messages found under ~/.claude/projects`.

#### Backwards compatibility

A user mining for the first time after upgrading to this version
has no `last-mine.json` and gets a first-run full mine that
establishes the baseline. Identical to a fresh install. No
migration logic needed.

Users who liked the old "busiest project" default behavior still
get it when running `maury mine` from a non-project dir (e.g.,
`$HOME`), or via `maury mine --project <name>` to be explicit.

### Consequences

- ✅ **Good:** Mining becomes regular-use cheap. Incremental cost
  ~= one window of new content. Frequent invocation becomes
  practical.
- ✅ **Good:** `maury mine` from a project dir does the right
  thing without flags. Reduces cognitive load for the common
  case.
- ✅ **Good:** The verified `derive_project_dir()` algorithm
  becomes load-bearing in production. The verifier's regression
  signal now protects production code, not just test code.
- ✅ **Good:** Algorithm changes are first-class. Bumping
  `mining_algorithm_version` is the explicit mechanism for "this
  extraction is materially different, re-run on history."
- ⚖️ **Neutral:** One new state file in `~/.local/state/maury/`.
  ADR-0029's inventory grows by one entry.
- ⚖️ **Neutral:** The fallback-to-busiest notice adds output
  noise on first mine from new dirs. Tolerable.
- ❌ **Bad:** Watermark assumes mtime monotonicity per project.
  If Claude Code ever back-dates JSONL files (it doesn't today,
  per inspection), the mtime check would miss those messages.
  Mitigated by `--full` as an escape hatch and `--since` for
  range overrides.
- ❌ **Bad:** Failed mining runs that successfully process some
  windows before erroring don't update the watermark — the next
  run re-mines those windows. Acceptable cost; the alternative
  (mid-run watermark updates) is complexity for a rare case.

### Confirmation

- Unit tests in `tests/unit/test_cli_mine_reconcile.py` extend to
  cover: first-run full mine, incremental skip of already-mined
  data, `--full` override, `mining_algorithm_version` bump,
  cwd-derived default, fallback-to-busiest notice.
- Integration test verifies the watermark file round-trips
  correctly across multiple mining runs on a fixture project dir.
- ADR-0029 File inventory entry for `last-mine.json` keeps this
  ADR discoverable from the state-layout reference.
- The verifier `maury verify-cc-projects-dir` continues to use
  the production `derive_project_dir()` as its prediction — a
  regression in production breaks the verifier's collision-pair
  test, which is a CI signal.

## Pros and Cons of the Options

### Option α: marker file inside each project dir

- ✅ **Good:** Co-located with the data; surviving even if maury-
  state is wiped.
- ❌ **Bad:** Pollutes `~/.claude/projects/` with maury files.
  Breaks the boundary the rest of the codebase respects ("maury
  doesn't touch Claude Code's state").
- ❌ **Bad:** Harder to reset — the user has to find each
  `.maury-mined` file rather than wiping one centralized location.

### Option β (chosen): centralized in maury-state

- ✅ **Good:** Matches the existing state-file pattern
  (`last-render.json`, `claude-writes.jsonl`, etc.).
- ✅ **Good:** Easy reset (`rm ~/.local/state/maury/last-mine.json`).
- ⚖️ **Neutral:** One JSON load + lookup indirection per
  `maury mine` run. Sub-millisecond cost.

### Option A: --cwd flag only

- ✅ **Good:** Minimal scope change. Default behavior preserved
  for users who rely on the busiest-project heuristic.
- ❌ **Bad:** The busiest-project default is rarely what users
  mean. `maury mine` from `~/work/project` mining the busiest
  project (which might be `~/work/other-project`) is a UX foot-gun.

### Option B (chosen): --cwd flag + cwd-derived default

- ✅ **Good:** "Mine the project I'm in" is the natural default
  for an interactive tool. The fallback to busiest preserves
  the old behavior for users running outside any project dir.
- ✅ **Good:** Wires the verified algorithm into production —
  defensive reuse, not just test code.

### Option C: don't wire derive_project_dir

- ✅ **Good:** Keeps mining independent of the verifier landscape.
- ❌ **Bad:** Verified algorithm stays test-only. Production
  users still have to know the cryptic project-dir form to
  invoke `--project`.

## Build-order placement

- **Phase 6** (mining — currently shipped at a `mine` command
  level): ships the watermark file, the incremental default, the
  `--full` / `--since` flags, the cwd-derived project selection.
- **`derive_project_dir()` extraction** to `src/maury/projects.py`:
  ships as part of the same Phase 6 work; verifier updated to
  import from production.

## Followups

- **Per-session-id watermark.** Today the watermark is per-
  project (mtime-based). If a project ever has multiple sessions
  with interleaved updates, mtime is a coarse approximation. A
  follow-up could track `(session_id, byte_offset)` per JSONL.
  Defer until a real failure mode shows up; the simple
  approximation is sufficient for current usage.
- **`maury status` integration.** Surface the watermark state
  ("last mined 2 days ago; ~3 new sessions since") so users
  notice when they're sitting on un-mined transcript volume.
- **Automatic mining on session-end hook.** A `Stop` hook could
  trigger incremental mining at session end. Deferred — the
  cost-of-LLM-on-every-session is a separate UX question.

## Claude Code references

Verified-as-of 2026-05-13 against Anthropic's official Claude
Code documentation:

- [`cc-contract:project-directory-derivation`](../claude-code-contract.md#cc-contractproject-directory-derivation)
  — the verified algorithm `derive_project_dir()` this ADR
  promotes from verifier-only to load-bearing production code.

The project-directory derivation algorithm is empirically
verified (🧪) per
[`maury verify-cc-projects-dir`](../../src/maury/empirical_tests.py)
and tracked upstream at
[anthropics/claude-code#54865](https://github.com/anthropics/claude-code/issues/54865).

## Amendment history

None.
