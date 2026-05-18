# ADR-0044: Strict transcript parser and JSON Schema lock

**Status:** Accepted
**Date:** 2026-05-18

## Related tenets

- [Tenet 1 — First, do no harm](../tenets.md#1-first-do-no-harm) — silent mining mis-parse is a Tenet 1 violation; failure must surface loudly, not silently flood the proposal queue with wrong findings.
- [Tenet 9 — Defer to the platform](../tenets.md#9-defer-to-the-platform) — the day Anthropic publishes an official transcript schema, maury defers to it and retires the maury-side lock. Until then, we're defensive.
- [Tenet 11 — Explicit beats implicit, with conservative defaults](../tenets.md#11-explicit-beats-implicit-with-conservative-defaults) — schema drift gets explicit acknowledgement (a `--strict` opt-in in v1; default in v1.1+) rather than silent miscount.

## Related ADRs

- [ADR-0008](0008-claude-diary-reference.md) — original mining design; `walk_user_messages_in_file` in `src/maury/mining/transcripts.py` is the lenient parser this ADR proposes to strict-mode.
- [ADR-0043](0043-incremental-mining.md) — incremental mining + `mining_algorithm_version`; this ADR extends the version mechanism to invalidate the schema lock too.
- [ADR-0029](0029-maury-state-layout-contract.md) — `~/.claude/maury-state/` inventory; this ADR adds a compatibility list (location TBD per Followups) but not a new state file.

## TL;DR

[`cc-contract:transcript-jsonl-stability`](../claude-code-contract.md#cc-contracttranscript-jsonl-stability)
is 🧪 verified at claude 2.1.142 today (2026-05-18). Anthropic
publishes no schema guarantee; the two upstream tracking issues
([#53516](https://github.com/anthropics/claude-code/issues/53516),
[#49400](https://github.com/anthropics/claude-code/issues/49400))
are open. **If Claude Code's transcript format drifts and
mining doesn't notice, the proposal queue silently fills with
mis-parsed findings — a Tenet 1 violation.** This ADR
designs the defensive response *before* drift is detected, so the
implementation isn't scrambled together after the fact.

**Chosen path:** ship a maury-side JSON Schema describing the
verified transcript shape + a strict parser that validates each
line against it. In v1, the strict parser is a `--strict`
opt-in flag on `maury mine`; in v1.1+ (or after one production
drift incident), strict becomes the default with `--lenient` as
the opt-out. Pin known-good Claude Code versions in a
compatibility list; `maury status` surfaces version vs. list.
Bumping `mining_algorithm_version` (ADR-0043) invalidates the
schema lock alongside the watermarks.

## Context and Problem Statement

Today's mining parser (`walk_user_messages_in_file` in
`src/maury/mining/transcripts.py`) is **lenient by design**:
unknown fields are ignored, malformed lines are skipped silently,
events of unexpected `type` are filtered out. That's the right
default for a tool that reads someone else's evolving output —
it survives schema additions without breaking.

The verifier (`maury verify-cc-transcript-schema`, ADR-0043
adjacent) confirms post-hoc that the current Claude Code version
emits a shape the lenient parser will read correctly. As of
2026-05-18 against claude 2.1.142, the verifier reports
1/1 mining-compatible user messages and both core-fields +
message-fields predicates passing.

**The failure mode this ADR addresses:** Anthropic ships a
Claude Code update that renames `message.role` (say, to
`message.from`), or changes `message.content` from a list to
a dict, or splits the `user` event type into two (`user-prompt`
and `user-followup`). The lenient parser silently filters out
the affected events. `maury mine` runs without errors,
processes fewer messages than expected, and writes findings
based on a non-representative sample.

Without explicit defense, this would be discovered weeks later
when the maintainer runs the verifier and notices
`user_messages_mining_compatible` dropped from "matches expected"
to "matches a fraction." Meanwhile the proposal queue holds
findings derived from a biased sample.

## Decision Drivers

- **Silent mis-parse is the worst failure mode.** Tenet 1 forbids
  it. Detection mechanisms must run at mining time, not after
  the fact.
- **The verifier is the source of truth, but it's not online.**
  It runs when invoked, not on every `maury mine`. We need an
  always-on detection signal at mining time.
- **Lenient default is correct for survival; strict default is
  correct for correctness.** The migration from one to the other
  needs explicit user acknowledgement so we don't break existing
  installs the day this lands.
- **Schemas describe, they don't prescribe.** Our schema is
  derived from empirical observation, not Anthropic spec. If
  Claude Code adds a new optional field tomorrow, the strict
  parser will reject it — that's wrong if the new field doesn't
  affect mining. The schema needs to be permissive about
  *additions* while strict about *the fields mining consumes*.

## Considered Options

- **Option A:** Lenient parsing forever (current state). Verifier
  as periodic check.
- **Option B:** Strict parser at mining time with full JSON
  Schema lock from day one. No opt-out.
- **Option C (chosen):** Phased strict parser. v1 ships
  `--strict` as opt-in (default lenient); v1.1+ or post-incident,
  strict becomes default with `--lenient` opt-out. Schema lock
  describes the load-bearing fields; permissive on additions.
  Compatibility list pins known-good Claude Code versions.
- **Option D:** Pin maury to a tested Claude Code version range
  only; no strict parser.

## Decision Outcome

**Chosen: Option C.** Phased rollout so the in-flight v0.1
mining users don't get a hard-default change unannounced.
The strict parser ships first as opt-in evidence; if it
catches drift in the wild before v1.1+ ships, we have the
data to promote it to default. If no drift is observed for
many CC releases, the case for v1.1+ default-strict gets
stronger anyway.

### Implementation details

#### 1. The JSON Schema

Lives at `src/maury/mining/transcript_schema.json`. Generated
from the empirical corpus the verifier produces. Describes
two kinds of constraints:

**Per-line invariants (apply to every event):**

```json
{
  "type": "object",
  "required": ["type", "sessionId", "timestamp"],
  "properties": {
    "type": {"type": "string"},
    "sessionId": {"type": "string"},
    "timestamp": {"type": "string"}
  },
  "additionalProperties": true
}
```

These mirror the `all_lines_have_core_fields` predicate.
`additionalProperties: true` is **intentional** — Claude Code
adds housekeeping fields (`promptId`, `parentUuid`,
`isSidechain`, `operation`, `attachment`, …) and we don't
want to fail on those.

**Per-type invariants (apply when `type` is in the message-
bearing set):** a separate sub-schema fires for
`type: "user"` and `type: "assistant"`:

```json
{
  "if": {"properties": {"type": {"enum": ["user", "assistant"]}}},
  "then": {
    "required": ["message"],
    "properties": {
      "message": {
        "type": "object",
        "required": ["role", "content"],
        "properties": {
          "role": {"type": "string"},
          "content": {
            "oneOf": [
              {"type": "string"},
              {"type": "array"}
            ]
          }
        }
      }
    }
  }
}
```

These mirror the `message_bearing_lines_have_message_fields`
predicate.

#### 2. The strict parser

Lives at `src/maury/mining/strict_parser.py` (new module).
Wraps `walk_user_messages_in_file` with validation:

```python
def walk_user_messages_in_file_strict(
    jsonl_path: Path,
    *,
    project: str,
    schema: JsonSchema,
) -> Iterable[TranscriptMessage]:
    """Strict variant: validates each line against the schema.

    Raises TranscriptSchemaDrift on the first non-conforming line.
    Caller catches and either aborts (--strict) or falls back to
    lenient mode with a warning (--lenient default in v1)."""
```

The strict parser uses `jsonschema` (pure-Python, well-known,
already widely vendored). The Followups section weighs
`fastjsonschema` (compiles schemas at load-time; measurably
faster on hot paths per its README) for v1.1+ if
parse time becomes a measurable cost.

#### 3. The `--strict` / `--lenient` flag

Added to `maury mine`:

```
maury mine --strict
  → use the strict parser. Abort with TranscriptSchemaDrift
    if any line fails validation. Caller sees a verbose error
    message pointing to `maury verify-cc-transcript-schema`.

maury mine --lenient  (v1 default; v1.1+ explicit opt-out)
  → use the lenient parser. Schema validation runs in
    "shadow mode" — when a line would fail strict validation,
    emit a one-line warn to stderr but continue processing.
    Subsequent runs of the same line on the same project
    suppress the warning (one per project per session).

maury mine  (v1)  → lenient (default)
maury mine  (v1.1+) → strict (default)
```

The shadow-mode warning is the load-bearing v1 mechanism:
it gives the user (and the maintainer) the data to decide
whether to flip the default without breaking anyone first.

#### 4. The compatibility list

Storage location is a Followup decision; the example below is
**illustrative**. Using `pyproject.toml` under a new
`[tool.maury.cc-compatibility]` section is one candidate;
alternatives (`compat.toml`, JSON alongside the schema) are
weighed in the Followups section.

```toml
# Illustrative — final location per Followups.
[tool.maury.cc-compatibility]
known-good = [
  "2.1.140",  # verified 2026-05-13 (project-dir derivation)
  "2.1.141",  # verified 2026-05-13 (hook-execution-timing)
  "2.1.142",  # verified 2026-05-18 (transcript schema)
]
schema-version = 1
```

`maury status` adds a one-line check: "your Claude Code
version 2.1.142 is in the known-good list." If outside,
the line reads `⚠️ untested Claude Code version 2.1.143 —
run `maury verify-cc-transcript-schema` to confirm
compatibility.`

`maury verify-cc-transcript-schema` on a passing run prints
the snippet to add to `pyproject.toml`, so promoting a new
version to known-good is one copy-paste.

#### 5. Schema versioning and the `mining_algorithm_version` link

`MINING_ALGORITHM_VERSION` (ADR-0043) currently invalidates
watermarks when the extraction algorithm changes. Extend its
contract: it also invalidates the schema lock. The schema
file carries a `schema_version` field; the strict parser
asserts `MINING_ALGORITHM_VERSION` and `schema_version`
match. If they don't, the parser refuses to run — the user
sees a clear "schema/algorithm version mismatch — re-run
verifier" message.

#### 6. Migration from v1 to v1.1+

The v1.1+ default flip happens in one of two paths:

- **Time-based:** if v1 ships with `--strict` opt-in and
  the shadow-mode warning fires zero times across 90 days of
  community use, the case for default-strict is strong;
  v1.1+ ships with it.
- **Incident-based:** if the shadow-mode warning fires in
  production before 90 days, v1.1+ ships immediately with
  default-strict and a release note pointing at the incident.

Either way, `--lenient` remains available as opt-out.

### Consequences

- ✅ **Good:** Silent drift becomes loud (in `--strict`) or
  audible (in shadow mode). Tenet 1 satisfied.
- ✅ **Good:** Schema is the explicit contract between maury
  and Claude Code, derived from empirical data — not aspiration.
- ✅ **Good:** Compatibility list is the dial. Tightening
  (remove versions) and loosening (add verified versions) are
  one-line operations.
- ✅ **Good:** Composable with ADR-0043's algorithm-version
  invalidation. One bump invalidates schema + watermarks
  together; no surprise.
- ⚖️ **Neutral:** Adds `jsonschema` dependency. Pure-Python,
  no new compiler toolchain, ~80 KB on disk.
- ⚖️ **Neutral:** Compatibility-list location (pyproject.toml
  section vs. a dedicated TOML/JSON file in `docs/` or
  `src/maury/`) is a Followup decision.
- ❌ **Bad:** Maury-side schema can be wrong (derived from a
  finite empirical corpus). A schema-strict run could fail on
  a legitimate-but-uncovered schema variant. Mitigation: the
  `additionalProperties: true` posture + shadow mode in v1
  surface this before it becomes a default-strict false positive.
- ❌ **Bad:** New surface to maintain. The schema needs to
  evolve when the verifier corpus does. The
  `MINING_ALGORITHM_VERSION` link gives this a regression-safe
  invariant, but it's still one more file in the contract set.

### Confirmation

- Schema file exists at the cited path and validates against
  itself (it's a JSON Schema, which is itself JSON Schema-
  validated).
- `maury mine --strict` raises `TranscriptSchemaDrift` on a
  test fixture with deliberately-broken JSONL; the existing
  `tests/unit/test_cli_mine_reconcile.py` adds a case for
  this when v1 ships.
- `maury verify-cc-transcript-schema` prints the
  pyproject.toml snippet to add when a new claude version
  verifies clean.
- `maury status` reports CC version vs. known-good list.
- The shadow-mode warning's once-per-session-per-project
  suppression is unit-tested.

## Pros and Cons of the Options

### Option A: Lenient forever, verifier as periodic check

- ✅ Simplest. Current state, no new code.
- ❌ Schema drift = silent miscount until verifier runs.
- ❌ No structural defense at mining time, just an audit
  trail after the fact.
- ❌ Violates Tenet 1 — silent failure mode for a load-bearing
  pipeline.

### Option B: Strict default from day one

- ✅ Fails loud immediately.
- ✅ Schema is the contract; mining is the consumer.
- ❌ Breaks every existing maury install the day this lands —
  any version drift from 2026-05-18's verified shape would
  break mining for users who haven't yet re-verified.
- ❌ False positives on optional-field additions. Without
  shadow-mode data, we can't tell hostile drift from benign
  additions.

### Option C (chosen): Phased strict parser

- ✅ Migration is gentle: v1 ships strict as opt-in;
  v1.1+ promotes to default once shadow-mode data justifies it.
- ✅ Shadow mode gives operational data without breaking
  anyone.
- ✅ Compatibility list = explicit version-pin dial.
- ⚖️ More moving parts than B but appropriate caution for
  a load-bearing default change.

### Option D: Pin to CC version range only

- ✅ Conservative.
- ❌ Doesn't catch behavior change *within* a "supported"
  range — Anthropic could change minor-version behavior
  silently.
- ❌ User friction: every CC patch release blocks maury
  until we re-test. Doesn't compose with progress.

## Build-order placement

- **v1** — ship the schema + strict parser + `--strict`
  opt-in + shadow-mode warning + compatibility list. Status
  flag for CC-version vs. list. The bulk of this ADR.
- **v1.1+** — promote `--strict` to default. Trigger is
  either time-based (a clean shadow-mode run across some
  sustained window — see §6 for the proposed 90-day figure)
  or incident-based (immediately after the first observed
  drift). Whichever happens first.
- **v2.x** — if Anthropic ships an official schema via
  upstream resolution of #53516 or #49400, replace the
  maury-side schema with theirs and retire the compatibility
  list (Tenet 9: defer to the platform).

## Followups

- **Schema location.** Currently proposed at
  `src/maury/mining/transcript_schema.json`. Alternative:
  `docs/schemas/transcript.json` for visibility. Decide
  during v1 build.
- **Compatibility list location.** Currently proposed in
  `pyproject.toml` `[tool.maury.cc-compatibility]`.
  Alternatives: a separate `compat.toml` or a JSON file
  alongside the schema. Decide during v1 build.
- **`jsonschema` vs. `fastjsonschema`.** Stdlib has no JSON
  Schema validator. `jsonschema` is the well-known choice;
  `fastjsonschema` compiles schemas at load-time and claims
  meaningful speedups on hot paths (see its README for
  measured numbers) at the cost of a C-extension dep. Defer
  the decision until v1 measures actual parse time.
- **Schema-version field semantics.** The schema has a
  `schema_version` field that must match
  `MINING_ALGORITHM_VERSION`. Should they always be
  identical, or can the schema-version evolve independently
  (e.g., adding a non-mining-affecting field)? Sub-decision
  for v1.
- **Shadow-mode warning surface.** Currently spec'd as
  "one per project per session." Alternative: log every
  failing line to `~/.claude/maury-state/transcript-drift.jsonl`
  so a maintainer reading the file later sees the full
  history. Trade: more state file surface vs. better
  forensics. Decide during v1 build.

## Claude Code references

This ADR references but does not extend Claude Code's
documented behavior. The cited contract entry carries the
verified-version record:

- [`cc-contract:transcript-jsonl-stability`](../claude-code-contract.md#cc-contracttranscript-jsonl-stability)
  — verification record, observed `type` values, mining-
  compatibility predicates.

Upstream tracking issues this ADR's hypothetical retirement
depends on:

- [anthropics/claude-code #53516](https://github.com/anthropics/claude-code/issues/53516) — *open feature request*. Stable, documented transcript schema.
- [anthropics/claude-code #49400](https://github.com/anthropics/claude-code/issues/49400) — *open docs request*. Publish the JSONL schema.

## Amendment history

None.
