# ADR-0052: Focus — the lightweight intra-trust-boundary mode switch

**Status:** Accepted
**Date:** 2026-05-20

## Related tenets

- [Tenet 1 — First, do no harm](../tenets.md#1-first-do-no-harm) — switching focus mid-session must not silently lose the operator's context.
- [Tenet 3 — Trust boundaries are physical, not policy](../tenets.md#3-trust-boundaries-are-physical-not-policy) — a focus switch that crosses a trust boundary must be refused at the mechanism level, not relied on as a user-discipline rule.
- [Tenet 11 — Explicit beats implicit, with conservative defaults](../tenets.md#11-explicit-beats-implicit-with-conservative-defaults) — refused focus switches surface a clear error pointing at `mode deregister`/`mode bootstrap` rather than silently coercing the switch.

## Context and Problem Statement

[ADR-0037](0037-layer-taxonomy-and-repo-discovery.md) established
that modes form a tree (`base → personal → personal:consulting →
personal:consulting:acme`) and [concepts.md §4](../concepts.md) made
it clear that **multiple modes in the tree can share one trust
boundary** — e.g., `work` and `work:client-acme` both live in the
single `mode-work` repo if the operator trusts those modes to see
each other's content.

[ADR-0039](0039-bootstrap-and-host-lifecycle.md) then said: changing
the host's *active mode* is a heavy operation. Two atomic commands
(`mode deregister`, `mode bootstrap`) with a fresh `host_<hex>`
each time. *"There is no single `maury mode switch` that wraps both
into a pseudo-transaction"* — because crossing a trust boundary
must be physical, deliberate, and never silent (per Tenet 3).

But that decision conflated two operationally-different cases:

- **Crossing a trust boundary** (e.g., `personal → work` on the
  same laptop): different repo, different deploy keys, different
  secrets reachable. Genuinely heavy. Must be deregister +
  re-bootstrap.
- **Moving within a trust boundary** (e.g., `personal:consulting:acme
  → personal:consulting:exampleco`): same repo, same deploy keys,
  same secrets reachable. The user is shifting their *focus*; nothing
  about the access surface changes. Forcing a deregister + bootstrap
  here is theatre — it adds friction without buying a real safety
  property.

The mechanism for intra-trust-boundary movement *already exists* —
the mode tree supports arbitrary depth and concepts.md describes
multiple modes coexisting in one trust boundary. What's missing is a
**UX-level word** for that operation and a CLI verb that performs it
safely (refusing if the user tries to cross a trust boundary).

A secondary problem: when the operator is in any non-default
location of the mode tree, *they need to know which leaf they're at*.
Without a visible cue, "am I in `personal:consulting:acme` or just
`personal`?" becomes a guessing game that defeats the point of having
distinct contexts.

## Decision Drivers

- The mode tree exists and works; don't invent a parallel primitive
  for what the tree already does.
- Trust-boundary crossings must remain heavy and deliberate (per
  ADR-0039 and Tenet 3). The lightweight verb must refuse to cross.
- Operators must be able to see which focus is active at all times
  (so they don't accidentally write `personal` content while believing
  they're in `personal:consulting:acme`).
- ADR-0025's active-session-detection infrastructure (the
  `active-sessions.jsonl` event log, the SessionStart/SessionEnd hook
  scheme) is load-bearing for *any* in-session context shift, not
  just mode switches. It should carry forward.
- "Focus" is a new vocabulary entry — pick a word that operators
  already understand from natural English; don't invent maury-specific
  jargon.

## Considered Options

- **Option 1:** Make focus a new conceptual primitive distinct from
  mode (own directory layout, own schema entries, own state machine).
- **Option 2:** Treat focus as purely a UX term over the existing
  mode tree (no new primitives; new CLI verb; new active-focus pointer).
- **Option 3:** Don't ship a verb at all — render takes a `--focus
  <path>` flag each call, no persistent state.

## Decision Outcome

**Chosen option:** "Option 2", because the mode tree already provides
every structural piece needed (inheritance, render order, trust
boundaries, single-parent invariant). Inventing a parallel primitive
would force operators to reason about two trees instead of one. The
work reduces to a vocabulary entry, a CLI verb that refuses to cross
trust boundaries, a small active-focus pointer in `host-identity.json`,
and a status-line integration so operators always see where they are.

Option 3 was tempting because it eliminates state-drift entirely
(no persistent active-focus → no way for it to disagree with
reality), but the Claude Code session-context-loading model means
**every fresh `claude` invocation re-reads `~/.claude/`** — so the
render must be persistent on disk anyway, and a persistent
active-focus pointer is no more state than the render it produced.
Plus, "what focus am I in?" needs to be answerable without a flag.

### Implementation details

#### "Focus" — the vocabulary entry

A **focus** is an adjustment of one's context — a set of conventions
and precepts — that's hierarchical by nature. Each focus is anchored
to a parent mode that determines a trust boundary. All foci under a
given mode share that mode's trust boundary.

Structurally a focus *is* a child mode in the existing mode tree.
What distinguishes a "focus" from a "mode" in the maury vocabulary is
**which operation moves between them**:

- Moving across trust boundaries — heavy: `mode deregister` +
  `mode bootstrap` per ADR-0039.
- Moving within a trust boundary — light: `focus use` per this ADR.

The word "focus" describes the light case. Operators say "I'm in the
`personal:consulting:acme` focus" the same way they'd say "I'm in the
`personal` mode" — same shape, different ceremony to leave.

Concepts.md §7 gains a "Focus — the lightweight intra-trust-boundary
leaf" subsection introducing the term and clarifying that no new
schema is needed.

#### Storage in the base repo

Unchanged from ADR-0037. Foci are nested modes in the existing
colon-separated tree:

```
modes/
  personal/
    CLAUDE.md             # personal-mode content
    .meta/maury-marker.json
  personal:consulting/
    CLAUDE.md             # adds on top of personal
    .meta/maury-marker.json
  personal:consulting:acme/
    CLAUDE.md
    .meta/maury-marker.json
```

No new layout. No new schema. The render engine already walks the
chain `base → personal → personal:consulting → personal:consulting:acme`
and composes per ADR-0019's refinement-by-default.

#### Active-focus pointer

A new field on `host-identity.json` (the ADR-0042 baseline):

```json
{
  "schema_version": 1,
  "host_id_hex": "24b2a0aa",
  "registered_at": "2026-05-20T15:42:11Z",
  "mode_id": "mode_<hex of registered mode>",
  "mode_name_at_bootstrap": "personal",
  "active_focus": "personal:consulting:acme"
}
```

The `active_focus` field is **optional**. When absent, the active
focus equals the registered mode (no descendant active). When
present, it MUST be a descendant of `mode_name_at_bootstrap` in the
mode tree — the focus-use verb enforces this.

No new file; the field rides on the existing baseline. This avoids
proliferating state files in `maury-state/` (per ADR-0029's "every
new state file is debt" rule) and keeps the focus pointer next to
the identity it scopes.

**Schema bump:** none. `active_focus` is an additive field; readers
on the previous version-1 schema ignore it gracefully. If a future
maury removes the field semantics, that's a v2 bump under
[ADR-0030](0030-manifest-schema-migrations.md).

#### CLI surface

```
maury focus use <dotted-path>     # set active focus
maury focus current               # print active focus (or "<registered-mode> (no focus set)")
maury focus list                  # list reachable foci from the registered mode
maury statusline                  # one-line summary for Claude Code's statusLine setting
```

`focus use <path>` is the primary verb. Preconditions, in order:

1. **Host is registered** — `host-identity.json` exists. If not,
   refuse with "run `maury init` first."
2. **Target focus is in the host's trust-boundary subtree** —
   walk the mode tree from `mode_name_at_bootstrap`; the target's
   `extends` chain must terminate at the registered mode. If the
   target's chain terminates at a *different* mode, refuse with the
   loud message:

   > Focus `personal:consulting:acme` is not in this host's trust
   > boundary (registered mode: `work`). Changing trust boundary
   > requires `mode deregister` followed by `mode bootstrap`. See
   > ADR-0039.

   This is the load-bearing safety property: a focus switch cannot
   silently cross a trust boundary even if the operator tries.
3. **No active Claude Code sessions on this host** (per the
   carried-forward ADR-0025 active-session-detection machinery). A
   running session has the *old* focus's CLAUDE.md loaded into
   working memory. Refuse unless `--force-active-session` is passed
   (a deliberately verbose flag — the user has to spell out what
   they're overriding). Reuses `active-sessions.jsonl` and the
   SessionStart/SessionEnd hooks ADR-0023 already installs.

On a clean switch:

1. Rewrite the `active_focus` field on `host-identity.json` via
   tmp+rename (per ADR-0029 invariant #4).
2. Re-render `~/.claude/` against the new chain (calls into the
   existing render pipeline; the new chain is just the new active
   leaf's `extends` chain).
3. Emit a `focus_switched` audit event per ADR-0035.
4. Print a one-line confirmation: `focus: <old-path> → <new-path>`.

On a refused switch, emit `focus_switch_refused` per ADR-0035 with
the precondition that failed.

#### Trust-boundary enforcement — how it works

A host's trust boundary is the subtree rooted at its registered
mode (the value in `mode_name_at_bootstrap`). A focus is reachable
iff its `extends` chain passes through the registered mode.

The check is:

```python
def is_reachable(target_mode_id: str, registered_mode_id: str, manifest: Manifest) -> bool:
    """Walk target's extends chain; the registered mode must appear."""
    current = target_mode_id
    while current is not None:
        if current == registered_mode_id:
            return True
        current = manifest.modes[current].extends
    return False
```

Single-parent + acyclic per [ADR-0049](0049-layer-taxonomy.md) (sub-ADR
of [ADR-0037](0037-layer-taxonomy-and-repo-discovery.md)) means this
terminates in O(tree depth). No need for cross-mode-tree
reachability analysis.

#### Sublayer attachments

Foci can declare `rules` sublayers in their own marker file like any
other mode. Concepts.md §3 already says sublayers float anywhere in
the tree; the focus case is just "anywhere" further down. No new
schema or render-order rules — the existing attachment-point order
from ADR-0037 covers it.

#### StatusLine integration

Claude Code's `settings.json` supports a `statusLine` setting that
runs a configured command and displays its stdout in the CLI prompt
area. Per [statusLine docs][cc-statusline], the command receives a
JSON-on-stdin context (cwd, session_id, model, etc.) and its first
line of stdout becomes the status text.

Maury ships `maury statusline` as a subcommand that:

1. Reads `~/.claude/maury-state/host-identity.json`.
2. Outputs a one-line summary to stdout:
   - With focus set: `mode:personal focus:personal:consulting:acme`
   - Without (or focus equals registered mode): `mode:personal`
   - Identity not found: `(no maury identity)` — never crash, never
     return non-zero. Claude Code displays whatever was returned;
     a maury bug must not break the statusLine.

The operator wires it into their settings:

```json
{
  "statusLine": {
    "type": "command",
    "command": "maury statusline"
  }
}
```

`maury init` adds this entry to the rendered `settings.json` by
default (operator can opt out with `--no-statusline`, mirroring the
hook-installation pattern from ADR-0023). Existing operator
`statusLine` settings are preserved if they don't conflict —
maury's installer refuses to overwrite a non-maury-managed
`statusLine` and surfaces a warning pointing at `--force` if the
operator wants the maury one anyway.

**Format is minimal by design.** ASCII only, no color, scriptable
(other tools can grep the statusLine via `claude --print` or the
session log). Color/decoration is a v1.1 polish.

#### Audit events

Adds two event kinds to ADR-0035:

| Kind | When | Payload |
|---|---|---|
| `focus_switched` | `maury focus use` succeeds | `{from_focus: "..."\|null, to_focus: "...", forced_active_session: bool}` |
| `focus_switch_refused` | `maury focus use` refuses (any precondition) | `{reason: "...", precondition: "...", target: "..."}` |

Removes from the "remaining event kinds" list: `mode_switched` and
`mode_switch_refused` (those were ADR-0025's design; ADR-0025 is
amended to point at this ADR and ADR-0039).

### Consequences

- ✅ **Good:** The operator now has a first-class lightweight verb
  for the common case (shifting focus within a trust boundary)
  without false ceremony. The heavy case (crossing a trust boundary)
  remains heavy and explicit.
- ✅ **Good:** No new schema, no new directory layout, no parallel
  primitive — the mode tree carries everything. Maintenance surface
  is exactly one new field on `host-identity.json` + one CLI verb +
  one subcommand.
- ✅ **Good:** Trust-boundary crossing is enforced at the mechanism
  level — `focus use` literally cannot perform a cross-boundary
  switch even if the operator tries. The error surfaces the right
  remediation (`mode deregister` + `mode bootstrap`).
- ✅ **Good:** Operators always see which focus is active via the
  statusLine. Removes the "am I in `personal` or
  `personal:consulting:acme`?" guessing game.
- ⚖️ **Neutral:** Adds two new audit event kinds and removes two
  obsolete ones (`mode_switched` / `mode_switch_refused` were never
  wired since ADR-0025 was never built).
- ❌ **Bad:** "Focus" overloads a common English word; readers might
  assume it means something more abstract. Mitigated by the
  vocabulary entry in concepts.md being explicit.
- ❌ **Bad:** The active-session precondition adds friction in the
  legitimate "I want to switch focus mid-session" case. The
  `--force-active-session` override is deliberately verbose so the
  operator must acknowledge the working-memory-mismatch risk.

### Confirmation

- Unit tests in `tests/unit/test_focus.py` cover the
  trust-boundary-reachability check, the precondition cascade
  (registered? in subtree? no active sessions?), and the audit
  event emission.
- An integration test in `tests/unit/test_cli_focus.py` exercises
  the CLI surface end-to-end with a temp manifest containing a
  multi-level focus tree.
- `maury doctor` gains a check that `host-identity.json`'s
  `active_focus` is reachable from `mode_name_at_bootstrap`
  (flags drift if the operator hand-edits a focus path that's no
  longer in the tree).
- A new smoke test in `.github/workflows/ci.yml`:
  `uv run maury statusline > /dev/null`.

## Pros and Cons of the Options

### Option 1: Focus as a new conceptual primitive

Define `focus` as a layer type distinct from `mode`. Own schema,
own directory layout, own state machine.

- ✅ **Good:** Vocabulary is unambiguous — "focus" and "mode" mean
  different things at the type level.
- ❌ **Bad:** Operators reason about two parallel trees.
- ❌ **Bad:** Doubles the maintenance surface — render engine, marker
  validator, audit-log enumeration, every place modes are mentioned.
- ❌ **Bad:** No real payoff; the mode tree already handles
  arbitrary-depth context nesting.

### Option 2 (chosen): Focus as a UX term over the existing mode tree

Foci are nested modes; "focus" is the word for the lightweight
switching verb's domain.

- ✅ **Good:** One tree, one set of primitives.
- ✅ **Good:** Implementation reduces to: vocabulary entry + CLI
  verb + one field on `host-identity.json` + statusLine integration.
- ⚖️ **Neutral:** "Focus" and "mode" both refer to nodes in the same
  tree; readers have to learn that the distinction is about *which
  verb moves you between them*, not about a type difference.

### Option 3: --focus flag per render, no persistent state

No active-focus pointer at all. Render takes `--focus
<path>` each invocation.

- ✅ **Good:** No state to drift; the current focus is always
  whatever the operator just typed.
- ❌ **Bad:** Doesn't answer "what focus am I in right now?" without
  shell history.
- ❌ **Bad:** Every Claude Code session start re-reads `~/.claude/`;
  the rendered tree IS the operator's current view, so we need a
  pointer to know what to render anyway.
- ❌ **Bad:** StatusLine has nothing to display.

## Build-order placement

- **Phase 5.x.d** (current — extends the Phase 5.x.b manifest-
  resolve work): ships `maury focus use`, `maury focus current`,
  `maury focus list`, `maury statusline`, the `active_focus` field
  on `host-identity.json`, the `focus_switched`/`focus_switch_refused`
  audit events, the trust-boundary-reachability check, and the
  `--no-statusline` opt-out flag on `maury init`.
- **Phase 5.x.e** (immediately after): the `maury doctor`
  `focus-unreachable` rule and the CI smoke test for `maury
  statusline`.

## Followups

- **Focus → mode bootstrap-from-seed** (deferred to **v1.1+**).
  Charles's "second job at acme" example: an operator working on
  `personal:consulting:acme` who later takes the gig as a paid job
  needs a fresh `work-acme` mode on a separate work laptop, with
  conventions and precepts seeded from the existing focus. This is
  a distinct operation from cross-trust-boundary promotion
  ([ADR-0045](0045-cross-trust-boundary-promotion.md), which moves
  incremental findings) — it bootstraps a fresh trust boundary with
  seed content from an existing focus. Open design questions
  parked in session-state include whether the seed is verbatim
  (conventions only) or includes precept subscriptions, and what
  the CLI shape is (`focus promote-to-mode` vs.
  `mode derive-from-focus`).
- **StatusLine color + decoration** (deferred to **v1.1+**). The
  V1 format is ASCII-only and scriptable. A polished version
  might color the focus path based on trust boundary (red for
  `work`, blue for `personal`, etc.) — could meaningfully reduce
  context-mistake risk for operators flipping often. Needs a
  color-configuration scheme that respects terminal capabilities.
- **`maury focus` shell completion**. Once the verb stabilizes,
  shell completion of focus paths (against the operator's mode
  tree) is a nice quality-of-life followup.

## Claude Code references

Verified-as-of 2026-05-20 against Anthropic's official Claude Code
documentation:

- [`cc-statusline`][cc-statusline] — `settings.json`'s `statusLine`
  setting; the command-stdout protocol this ADR's `maury statusline`
  subcommand implements.
- [`cc-settings`][cc-settings] — `~/.claude/settings.json` schema
  (the `statusLine` key is one entry among many; we install it via
  the same hook-installation pattern from ADR-0023).
- [`cc-hooks`][cc-hooks] — SessionStart/SessionEnd hooks that
  populate `active-sessions.jsonl`; this ADR reuses ADR-0023's
  installation pattern and ADR-0025's event-log mechanics.

[cc-statusline]: https://code.claude.com/docs/en/statusline
[cc-settings]: https://code.claude.com/docs/en/settings
[cc-hooks]: https://code.claude.com/docs/en/hooks

## Amendment history

None.
