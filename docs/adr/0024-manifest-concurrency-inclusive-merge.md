# ADR-0024: Manifest concurrency and inclusive structured merge

**Status:** Accepted
**Date:** 2026-05-07

## Related tenets

- [Tenet 1 — First, do no harm](../tenets.md#1-first-do-no-harm)
- [Tenet 5 — The user arbitrates ambiguity](../tenets.md#5-the-user-arbitrates-ambiguity)
- [Tenet 8 — Hand-edits are first-class input](../tenets.md#8-hand-edits-are-first-class-input)

## TL;DR

Two hosts pushing concurrent marker-file edits produce git conflicts
that look textually identical to two different intents (both adding
different keys vs. both editing the same key) — but only one
resolution is safe in each case. Maury ships a structured merge tool
with **inclusive defaults**: auto-resolve only the additive case
(both sides added different keys → keep all of them), surface every
other ambiguity to an interactive resolver. Trade-off: more
prompting than a "latest wins" naive merge, but no silent host loss
from the marker.

## Context

Two hosts can mutate `.meta/maury-marker.json` concurrently. Bootstrap
two new machines in the same hour: both `maury init`, both add
themselves to `hosts: {…}`, both push. Second one rejects.

Git's stock 3-way merge sees both sides touched the same line region
(adjacent JSON entries near a closing brace) and produces a textual
conflict. Naive resolution patterns ("take theirs", "take ours",
"latest wins") work for some conflict shapes and silently destroy
data for others — specifically, two hosts adding *different* keys
look textually identical to two hosts modifying the *same* key, even
though the right resolution is opposite (keep both vs. pick one).

The cost of getting this wrong is not "the user has to re-run a
command." The cost is **a host silently disappears from the
manifest, fails to identify itself on its next sync, and the user
doesn't notice until something else breaks downstream.** That's
exactly the harm tenet 1 forbids.

## Decision

Maury ships a structured merge tool with **inclusive defaults**:
when in doubt, keep more, not less. The user is consulted only when
intent is genuinely ambiguous, and never to recover from data maury
already dropped.

### Resolution rules

The only diff pattern maury auto-resolves is the **inclusive
additive case**. Every other ambiguity surfaces to the interactive
resolver. This stays narrowly inside tenet 5 ("no silent precedence
rules; no auto-resolved merge conflicts"): the additive case isn't
a *conflict* in the semantic sense — both sides got what they
wanted — so resolving it silently doesn't violate the rule.

| Diff pattern | Resolution |
|---|---|
| **Both sides added different keys** at the same path | **Auto-resolve: keep all of them.** Different host_ids, different profile_ids, different repo nicknames — coexisting is the right answer. Not a conflict; an inclusive merge. |
| **Both sides modified the same key** from the ancestor's value | **Surface to interactive resolver.** E.g., A renames `home → personal`, B renames `home → work-personal`. We cannot pick one for the user; clock-skew can invert "later," and the user's intent in the two renames may have been different on each host. |
| **One side deletes a key, the other side modifies the same key** | **Surface to interactive resolver.** Different intents (`profile rm` vs `profile rename`); maury cannot pick. |
| **Both sides added the same key with different content** (e.g., user copied `~/.config/maury/host-id` between machines) | **Surface to interactive resolver.** Treated as a same-key overwrite. Note: per [ADR-0015](0015-surrogate-keys-for-hosts-and-profiles.md) the host-id file is created at bootstrap and never modified, so this only happens via user error — but we surface it rather than silently picking one identity. |

Two simpler cases for completeness:

| Diff pattern | Resolution |
|---|---|
| Both sides deleted the same key | Delete it. No conflict. |
| One side modified, other side untouched | Take the modification. No conflict (this is git's normal fast-forward case). |

**Key design property:** the only path where maury picks a winner
without asking is the additive case where there *is no winner to
pick* — both sides' contributions land. Every situation where a
choice between values must be made goes through the interactive
resolver. This is what reconciles the "inclusive default" framing
with tenet 5's prohibition on silent precedence rules.

### The flow

```
maury sync (or any manifest-mutating command)
   ↓
   git push rejected (non-fast-forward)
   ↓
   git fetch
   ↓
   maury runs structured merge:
       - Parse ancestor, ours, theirs
       - Walk every JSON path; classify by diff pattern; apply rule
       - Re-validate result against manifest schema
   ↓
   ┌──────────────────────────────────────────────────┐
   │ all paths resolved cleanly + schema valid?       │
   ├──────────────────────────────────────────────────┤
   │ yes → commit merge result; retry push            │
   │ no  → write result with conflict markers;        │
   │       prompt: "run maury manifest resolve <path>"│
   │       exit non-zero                              │
   └──────────────────────────────────────────────────┘
```

### `maury manifest resolve`

Interactive command. Extends the existing `maury manifest` Click
group (which today has `validate` and `show` subcommands; this adds
`resolve`). For each ambiguous path, shows:

- **Ancestor value:** what was there before either change.
- **Side A's change** with author + git committer timestamp + branch.
- **Side B's change** with author + git committer timestamp + branch.
- **Semantic context:** if the path is `profiles.home`, maury notes
  which hosts are bound to it ("3 hosts will lose their profile
  binding if this is deleted").
- **Prompt:** keep both / take A / take B / edit by hand.

The verbs are intentionally different from ADR-0017's reconcile
menu (`adopt | adapt | mark-managed | revert | skip-once`) because
the problem shape is different: ADR-0017 resolves *textual* drift
of a file's content over time; ADR-0024 resolves *structural*
ambiguity about which value lands at a JSON path. Same UX
spirit ("user arbitrates ambiguity"), distinct vocabulary that
matches the underlying decision being made.

**On clock skew:** "later" by committer timestamp assumes hosts
have reasonably synced clocks (NTP). Skew larger than a few
seconds can invert the apparent action order relative to wall-
clock action sequence. The resolver surfaces both timestamps
verbatim so the user can spot inversions and decide for
themselves which change reflects their *actual* most recent
intent.

After every choice, re-validates the manifest schema before
moving to the next conflict. Final commit message records the
resolutions: *"manifest merge: kept add of host_a3f9...; resolved
profile rename via interactive choice."*

### Semantic validation is mandatory before push

Whether the merge auto-resolved or went through interactive
resolve, the merged manifest is re-validated against the schema
before maury commits and pushes. The current validator (in
`src/maury/manifest.py`) already checks:

- Every `host.profile` reference points to an existing profile ID.
- Every `profile.extends` chain terminates without cycles.
- Manifest schema version is supported.

This ADR adds one new check (lands with the merge tool):

- Every `host.repos` URL is syntactically valid for its declared
  backend. Today the validator only checks URL presence and that
  `backend` is a string; per-backend URL syntax validation is new
  work and logically belongs to the [ADR-0016](0016-pluggable-repo-backends.md)
  adapter layer (each backend supplies a URL validator).

Validation failure aborts the merge with a specific error — never
push a structurally broken manifest.

### Other concurrency hygiene

- **Atomic write:** every marker write goes via
  `maury-marker.json.tmp` + POSIX `rename(2)`. Crash-safe.
- **Per-host process lock:** `~/.config/maury/.lock` (advisory
  fcntl). Prevents two `maury` processes on the same host from
  racing each other through the merge flow. Clearly errors if held.
- **No `revision: int` field on the manifest.** Git's HEAD SHA is
  already the revision identifier; adding a parallel counter
  invites them to drift.

### Non-interactive contexts

[ADR-0017](0017-drift-detection-and-reconciliation.md) defines
`--non-interactive` for the *drift* case ("refuse on any drift,
exit 1"). Following that precedent, `maury sync --non-interactive`
also refuses to invoke the interactive manifest resolver. If a
structured merge can't auto-resolve, the command exits 1 with a
specific error pointing at `maury manifest resolve`. Cron and CI
users opt into this strict behavior; default `maury sync` is the
friendly path.

## Consequences

- **Tenet 1 is enforced architecturally.** No code path silently
  drops a host registration, profile, or repo entry. The worst
  case is "user is asked to pick a side"; the worst case is never
  "data vanished."
- **The common case is invisible.** Two hosts bootstrapping on the
  same day → both registrations land, neither user even notices
  the merge happened.
- **The hard case has a real UI.** Delete-vs-modify gets a
  semantic prompt, not a raw vim conflict. The user sees the
  *consequences* of each option ("this profile has 3 hosts bound")
  before choosing.
- **Schema validation is on every push path.** Consequence beyond
  this ADR: any manifest-mutating command (init, profile rename,
  host re-bind, etc.) goes through the same validate-before-push
  gate.
- **Implementation cost is real but bounded.** ~250-350 LOC for
  the structured merge + interactive resolver + validator
  integration + tests. Lands alongside drift detection (Phase 5.x)
  because both are about "detecting concurrent state changes and
  giving the user a path to resolve them."
- **Lock-in to single-user assumption.** This design assumes one
  human owns all hosts that touch a given repo. Multi-user
  shared-repo scenarios (a team sharing a base repo) would need
  additional thought — out of scope per the project's target
  user.

## Alternatives considered

- **Lean entirely on git's stock merge with documentation.**
  Rejected: tenet 1. Git's text-level merge can silently lose
  semantic data when the user mis-resolves a conflict, and "you
  resolved your conflict wrong" is exactly the harm we're trying
  to avoid.
- **Apply "latest wins" as a uniform policy.** Rejected: works for
  overwrites, silently destroys data for additions (two hosts
  adding themselves with different keys). This was the design in
  an earlier draft; the project owner correctly identified that
  it violates tenet 1.
- **Last-wins for same-key overwrites only** (an earlier version of
  this ADR). Rejected on doc-review: violates tenet 5 ("no silent
  precedence rules"). Clock skew between hosts can invert "later"
  relative to true action order, and two same-key edits may have
  been the user changing their mind on one host without realizing
  the other host was about to make a different choice. Surfacing
  to interactive resolve is the only behavior consistent with
  *both* tenet 1 (don't drop state) and tenet 5 (don't auto-pick
  between user intents).
- **Add a `revision: int` field with optimistic concurrency.**
  Rejected: redundant with git's HEAD SHA. The two would inevitably
  drift apart, leaving us with two sources of truth for "is this
  manifest current."
- **Refuse to ever auto-merge; always prompt.** Rejected: every
  bootstrap of a new host would be a user-facing manifest conflict
  prompt. Bad UX for the common case to protect against an
  uncommon one. Inclusive auto-resolve for clear cases gets us
  both safety AND quiet operation.
- **Custom server-side hooks (GitHub webhook serializing pushes).**
  Rejected: requires server infrastructure, breaks in self-hosted
  / offline / air-gapped repos, and doesn't actually solve the
  semantic-merge problem — just queues conflicting pushes.

## Build-order placement

- **Atomic write + lockfile** are cheap; land in Phase 4
  (bootstrap commands) since `maury init` is the first
  manifest-writing path.
- **Structured merge + `maury manifest resolve`** land in Phase 5.x
  (drift detection + reconciliation) because they share the
  conceptual neighborhood: detecting concurrent state changes and
  walking the user through resolution. Implementation note: the
  ~250-350 LOC + tests this represents is a discrete slice on top
  of ADR-0017's existing Phase 5.x scope. Treat as 5.x.b (manifest
  merge), distinct from 5.x.a (drift detection on file content),
  even though both ship under the Phase 5.x umbrella.
- **Schema validation on every push path** is incremental — every
  manifest-writing command gains the validation gate as it's
  written.

## Followups

- **Conflict-resolution UX testing.** Once the interactive resolver
  exists, dogfood by deliberately creating concurrent edits across
  two hosts and walking through the prompts. Iterate on phrasing
  ("this profile has 3 hosts bound" — is that the framing that
  helps the user pick?).
- **Schema migrations as an adjacent concern.** Manifest schema
  version bumps are not concurrency conflicts but they're handled
  by the same validate-before-push gate. Cross-reference when the
  schema-migration ADR lands (gap H from the gaps walkthrough).
- **Multi-user mode** is explicitly out of scope here but would
  build on this design: rule 2 (last-wins on overwrites) becomes
  much more dangerous when two different humans are racing.

## Amendment history

- 2026-05-11 — "profile" vocabulary renamed to "mode" per ADR-0037 doctoral examination. References to "profile" in this ADR now read "mode"; no semantic changes.
- 2026-05-19 — auto-merge integration into `maury sync` shipped.
  Implementation note: sync.py's `_sync_one_repo` retains `git pull
  --ff-only` as the safe default; on a non-FF rejection AND when
  the repo carries `.meta/manifest.json` (i.e., is the base repo),
  sync calls a new `_pull_with_structured_merge` helper that does
  `git fetch origin` + `git merge --no-ff FETCH_HEAD`, then if the
  only conflicted path is `.meta/manifest.json` invokes
  `try_auto_merge_manifest()` (new non-interactive entry point in
  `manifest_resolve_cmd.py`). Outcomes: AUTO_MERGED → `git add` +
  `git commit -m "manifest merge: structured auto-resolve …"` to
  finish the in-progress merge, RepoSyncResult.action="pulled"
  with detail "auto-merged manifest"; NEEDS_USER_RESOLVE →
  RepoSyncResult.action="error" with detail pointing at
  `maury manifest resolve`. Non-manifest conflicts in the same
  merge surface as a `git merge --abort` + error pointing the
  user at manual mergetool. The render step uses sync's
  in-memory (pre-pull) manifest; a re-run picks up the merged
  view. Tests: `test_sync_auto_merges_additive_manifest_conflict`
  + `test_sync_surfaces_real_manifest_conflict_with_resolve_hint`
  + three `try_auto_merge_manifest()` unit tests.
