# ADR-0022: Branch-per-mining-run with commit-message-as-proposal

**Status:** Accepted
**Date:** 2026-05-06
**Supersedes:** [ADR-0021](0021-promotion-changelog.md)
**Amended:**
- 2026-05-07 — dedup scan widens from `Content-Hash` (scalar) to
  `(Content-Hash, Source-Profile)` tuple per
  [ADR-0026 §"Cross-host case"](0026-profile-aware-mining.md#cross-host-case-multi-host-mining).
  Same finding text from `personal` vs `work` are conceptually
  different proposals and should not dedupe each other.
  Implementation: `git log --grep="^Content-Hash:"` still
  catches both, but the matcher reduces by `(hash, profile)`
  pairs rather than hashes alone. Backward-compatible for
  commits without `Source-Profile:` (treat as wildcard).

## Related tenets

- [Tenet 1 — First, do no harm](../tenets.md#1-first-do-no-harm)
- [Tenet 7 — Provenance is mandatory](../tenets.md#7-provenance-is-mandatory)
- [Tenet 8 — Hand-edits are first-class input](../tenets.md#8-hand-edits-are-first-class-input)

## TL;DR

Mining produces a **branch with one commit per finding**: the
diff IS the proposed change, the message body IS the rationale, the
SHA IS the finding identity. `maury review` walks the branch with
cherry-pick prompts; rejections land as a single trailing no-op
commit on the review branch whose body lists `Rejected-Content-Hash`
trailers. Dedup is `git log --grep` over both accepted and rejected
hashes — sub-second on years of history.
[Supersedes ADR-0021](0021-promotion-changelog.md)'s sidecar
changelog. Trade-off: locks maury to git as the substrate forever
(non-git backends ruled out per ADR-0016 addendum).

## Context and Problem Statement

Earlier ADRs hand-waved over the proposal queue.
[ADR-0021](0021-promotion-changelog.md) proposed a sidecar
`.meta/changelog.jsonl`. Both were over-engineering: git already
provides everything we need — branches for in-flight work, commits
for individual changes with rationale in the message body,
`git log` for history queries. Inventing a parallel data structure
duplicates what git is designed to do, and creates two sources of
truth for "what changed and why."

Per the project owner's explicit feedback: *"use git as a tool for
what it's designed for — tracking change and using the commit log to
explain the change. No sidecars. No extra tracking files."*

<details>
<summary><b>Decision drivers</b> (5 items — click to expand)</summary>

- **Tenet 1:** first, do no harm. Rejection memory must be
  permanent and dedup must work; otherwise users see the same
  rejected finding repeatedly (a kind of harm-by-papercut).
- **Tenet 7:** provenance is mandatory. Every finding must
  carry rationale (commit body), evidence (transcript window
  reference), and lineage (branch → main → promoted-to).
- **Tenet 8:** hand-edits are first-class. Git is the user's
  native tool for editing diffs; making proposals real
  commits means cherry-pick / rebase / `git commit --amend`
  all just work.
- **Single source of truth:** the commit log already
  captures everything ADR-0021 proposed; sidecar files
  create divergence.
- **No extra dependencies:** branches, trailers, log-grep
  are stdlib git operations. No custom data store to
  maintain.

</details>

<details>
<summary><b>Considered options</b> (7 options — click to expand)</summary>

- **Option A:** Sidecar `.meta/changelog.jsonl` per
  [ADR-0021](0021-promotion-changelog.md) (which this ADR
  supersedes).
- **Option B:** One branch per finding instead of one per
  run.
- **Option C:** Track rejections as commit SHAs in the next
  accepted commit's body; look up the rejected commit's diff
  later.
- **Option D:** Don't track rejections at all; re-mine and
  re-show.
- **Option E:** Refs namespace for rejected commits
  (`refs/maury/rejected/...`).
- **Option F:** PR workflow as primary instead of local CLI.
- **Option G (chosen):** Branch-per-run with one commit per
  finding; rejections land as a single trailing no-op
  metadata commit on the review branch; dedup via
  `git log --grep` on `Content-Hash` and
  `Rejected-Content-Hash` trailers.

</details>

## Decision Outcome

**Chosen option:** Option G — a mining run produces a
**branch with one commit per finding**. The commit's diff IS
the proposed change. The commit's message body IS the
rationale. The commit's SHA IS the finding's identity. This
is the only option that uses git as designed, makes
`maury why` a `git log --grep` one-liner, and keeps
rejection memory permanent and dedup-queryable across all
hosts via the merged-to-main no-op rejection commit.

### Implementation details

#### Branch lifecycle

```
maury mine
  └─ creates branch `maury/run/<run-id>` off main
     └─ for each finding: one commit
        commit subject:  "maury: <kind> — <one-line summary>"
        commit body:     structured rationale (see below)
        commit diff:     the proposed change to CLAUDE.md / rules.yaml / etc.

maury review <run-id>
  └─ creates branch `maury/review/<run-id>` off current main
     └─ walks `git log main..maury/run/<run-id>` oldest-first
     └─ for each commit: show diff + parsed body, prompt {accept | reject | edit | skip}
     └─ accepted: cherry-pick onto review branch
     └─ rejected: appended to a single trailing "Rejected" commit (see below)
     └─ on cherry-pick conflict: re-prompt, user picks one or merges by hand

merge `maury/review/<run-id>` to main
  └─ either via PR (gh pr create) or local merge
  └─ deletes the as-mined `maury/run/<run-id>` branch
```

#### Commit message format (the source of truth)

Every maury-generated commit has a structured trailer block — RFC 822
key/value pairs that `git interpret-trailers` understands and that
`git log --grep` can scan in sub-second time:

```
maury: feedback — prefer terse responses over long summaries

The user has corrected this twice in the last week. Promoting from
session observation to a durable rule.

Kind:               feedback
Scope-Hint:         base
Confidence:         high
Crossref-State:     NEW
Content-Hash:       4f8c2a91e3b6d7c8...
Source-Transcript:  ~/.claude/projects/abc123/2026-05-06-1142.jsonl
Source-Window:      messages 47-93
Mining-Run:         maury/run/2026-05-06-host_e3844a43...-r1
```

`Content-Hash` is the semantic fingerprint of *what the finding is
about*, not the file diff: `sha256(kind + "\n" + scope + "\n" +
normalized_text)` where `normalized_text` lowercases, collapses
whitespace, and strips punctuation. Two findings with slightly
different wording but the same idea hash to the same value.

#### Rejection: a no-op metadata commit

When `maury review` rejects findings during a session, the rejections
land as a **single trailing no-op commit** on the review branch — it
modifies no files, but its body lists the content-hashes of every
rejected finding:

```
maury: rejected during curator review

Original-Run:       maury/run/2026-05-06-host_e3844a43...-r1
Curator-Host:       host_e3844a43...
Rejected-Count:     3

Rejected-Content-Hash:  9a21d4f5...
Rejected-Reason:        too narrow; this is project-specific not base
Rejected-Content-Hash:  e3c4b8a7...
Rejected-Reason:        already implied by existing rule "code-style-python"
Rejected-Content-Hash:  7b1f2e09...
Rejected-Reason:        false positive — Claude misread the user's intent
```

When the review branch merges to main, this no-op commit is part of
main's history. The rejected hashes are therefore queryable by any
future mining run via `git log --all --grep="Rejected-Content-Hash:"`.

The rejected finding's original commit (on the deleted run branch)
is allowed to be garbage-collected. We don't need to keep it alive
because everything semantic about it lives in the rejection record's
`Rejected-Content-Hash` line.

#### Dedup at next mining run

Before creating a commit for a new candidate, the miner runs:

```
git log --all --grep="^Content-Hash:"           # accepted history
git log --all --grep="^Rejected-Content-Hash:"  # rejection history
```

Builds a set of "already seen" hashes (both accepted and rejected),
hashes the new candidate, and skips it on a hit. Both scans are
sub-second on multi-year history because git's log-grep is bounded
by commit count, not tree size.

Cache point: if total findings ever exceeds 10k (we don't expect
this), maury caches the set keyed by main's HEAD SHA in
`refs/maury/dedup-cache`. Until then, recompute every run.

#### Cross-repo promotion

When the curator host has rw on both source and destination repos
(per [ADR-0009](0009-promotion-only-cross-boundary.md)):

```
maury promote --from <source-repo> --to <dest-repo>
  └─ fetches the source repo's `maury/run/*` branches read-only
  └─ same review UI as `maury review`
  └─ accepted commits are cherry-picked onto a fresh
     `maury/promoted/<id>` branch in the destination repo
  └─ each cherry-picked commit gets an extra trailer line
     `Promoted-From: <source-repo>@<original-sha>` so promotion
     lineage is visible in `git log`
  └─ the original `Content-Hash:` trailer is preserved on the
     promoted commit (NOT stripped). This is intentional: the dedup
     scan in the destination repo's next mining run will then
     suppress the same finding if it surfaces locally, avoiding a
     duplicate proposal for content that already lives in the
     destination via promotion.
  └─ rejected commits get the same no-op rejection commit treatment
```

#### Stale base handling

If main has moved between mining and review, `maury review` refuses
to start until you `maury rebase-run <run-id>` (which is just
`git rebase main` on the run branch with conflict re-prompting). We
will not silently rebase mid-review.

### Consequences

- ✅ **Good:** Single source of truth. No sidecar files
  compete with the commit log. `git log` is the proposal
  queue, the changelog, the audit trail, and the dedup
  index.
- ✅ **Good:** `maury why <rule>` is just `git log`.
  `git log --follow --grep="<rule-text>" -- CLAUDE.md` finds
  the maury commit that added the rule; reading its body
  gives you everything [ADR-0021](0021-promotion-changelog.md)
  promised plus the actual diff for free.
- ✅ **Good:** Cross-repo lineage is visible. A
  `Promoted-From:` trailer on the destination commit links
  back to the source repo's commit SHA.
  `git log --grep="Promoted-From:"` on base shows everything
  ever promoted in.
- ✅ **Good:** Rejection memory is permanent and cheap. The
  no-op commit on main is forever; the dedup hash is the
  same across all hosts (because main is the same across
  all hosts).
- ✅ **Good:** Removes [ADR-0021](0021-promotion-changelog.md)'s
  `rule_<hex>` migration. No surrogate ID for rules; commit
  SHA is the identity.
  [ADR-0015](0015-surrogate-keys-for-hosts-and-profiles.md)'s
  "rules keep their slug `id:`" stands as written.
- ✅ **Good:** Performance is bounded by commit count, not
  file size. Sub-second log-grep on years of history. If we
  ever exceed it, cache in a refs namespace.
- ✅ **Good:** Implementation surface is small. Mining
  writes commits; `maury review` is a wrapper around
  `git log` + `git cherry-pick`; `maury why` is
  `git log --grep`. No proposal data model, no queue
  schema, no JSON files to validate.
- ❌ **Bad:** Lock-in to git. This commits maury to git as
  the backend in perpetuity.
  [ADR-0016](0016-pluggable-repo-backends.md) is now
  constrained to git-compatible systems (gitea, gitlab,
  codeberg) — no Perforce, no Mercurial. Acceptable given
  target-user reality (codified in ADR-0016's 2026-05-06
  addendum).

### Confirmation

- Mining (Phase 6) writes branches under `maury/run/`.
- `maury review` walks `git log main..maury/run/<run-id>`
  oldest-first per the documented branch lifecycle.
- Dedup uses `git log --grep="^Content-Hash:"` and
  `git log --grep="^Rejected-Content-Hash:"`; reduce by
  `(hash, source-profile)` per the 2026-05-07 amendment.

<details>
<summary><b>Pros and cons of the options</b> (per-option ✅/❌ — click to expand)</summary>

#### Option A: Sidecar `.meta/changelog.jsonl` (ADR-0021)

- ✅ **Good:** Structured queryable JSON.
- ❌ **Bad:** Duplicates git; two sources of truth; file
  format to maintain; no win over commit log.

#### Option B: One branch per finding

- ✅ **Good:** Most granular branch lifecycle.
- ❌ **Bad:** N branches per mining session is queue
  clutter; mining-run cohesion (seeing all of one
  session's insights together) is genuinely useful for
  curator review.

#### Option C: Track rejections as SHAs in next commit body

- ✅ **Good:** Rejection-record lives next to acceptance.
- ❌ **Bad:** Too processing-intensive over time — would
  scale O(history) on every mining run with fuzzy diff
  comparison. Content-hash dedup avoids the fuzzy match
  entirely.

#### Option D: Don't track rejections; re-mine and re-show

- ✅ **Good:** Simplest possible implementation.
- ❌ **Bad:** Re-seeing the same rejected finding on every
  run is the kind of papercut that makes people stop using
  the tool.

#### Option E: Refs namespace for rejected commits

- ✅ **Good:** Git-native; doesn't pollute main.
- ❌ **Bad:** Refs are git-managed but invisible to most
  tools and workflows. The no-op metadata commit on main is
  more discoverable and rides for free with the merge.

#### Option F: PR workflow as primary

- ✅ **Good:** Familiar GitHub UX.
- ❌ **Bad:** Requires GitHub access for every host that
  mines, which defeats the local-only mining property of
  [ADR-0005](0005-local-only-mining.md).
- ⚖️ **Neutral:** PR is available as the *output* of
  `maury review` (the cleaned-up `maury/review/<run-id>`
  branch can be pushed and a PR opened), but it's not the
  review interface itself.

#### Option G (chosen): Branch-per-run with rejection no-op commit

- ✅ **Good:** Uses git as designed; no new data layer.
- ✅ **Good:** Permanent rejection memory; sub-second
  dedup via `git log --grep`.
- ✅ **Good:** Implementation surface is tiny — wrapper
  around `git log` and `git cherry-pick`.
- ❌ **Bad:** Locks maury to git substrate forever.

</details>

## Build-order placement

- **Phase 6 (Mining)** writes the run branches and commit
  trailers.
- **Phase 7 (Proposal queue + review UI)** is the
  branch-walk wrapper around `git log` + `git cherry-pick`.
- **Phase 9 (Promotion)** adds the cross-repo cherry-pick
  flow with `Promoted-From:` trailers.

## Followups

- **`Content-Hash` normalization spec.** The hash function needs to
  be stable across maury versions. Pin it in the rule engine module
  with a versioned constant; bump only with a documented migration.
- **PR-mode template.** When the user wants `gh pr create` after
  `maury review`, generate a PR body from the merged commit messages
  so reviewers on GitHub see the full rationale.
- **Phase 7 task description update.** The task currently says
  "proposal queue + review UI." Reword to "branch-walk review UI" to
  match this ADR.

## Amendment history

- 2026-05-11 — "profile" vocabulary renamed to "mode" per ADR-0037 doctoral examination. References to "profile" in this ADR now read "mode"; no semantic changes.
