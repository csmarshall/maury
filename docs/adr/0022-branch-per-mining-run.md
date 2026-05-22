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
(per [ADR-0045](0045-cross-trust-boundary-promotion.md)):

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
- 2026-05-22 — **the `(Content-Hash, Source-Mode)` dedup tuple promised by the 2026-05-07 amendment shipped**, and **§"Cross-repo promotion" was implemented** as `maury promote` (per [ADR-0045](0045-cross-trust-boundary-promotion.md)). Dedup now keys on `(Content-Hash, Source-Mode)` via `run_branch.FindingKeys` (wildcard back-compat for trailer-less commits); see [ADR-0026 2026-05-22 amendment](0026-profile-aware-mining.md#amendment-history). The promotion flow's cross-repo apply reconstructs-and-appends (the source SHA isn't reachable in the destination), copying the source commit message and appending a `Promoted-From: <src>@<sha>` trailer — `Content-Hash` rides through as this ADR intends.
- 2026-05-22 — **the V1 single-staging-file model is superseded for *matched* findings by [ADR-0053](0053-rule-driven-auto-placement.md).** The implementation amendment above noted "a future ADR can introduce smarter routing once we have operator experience with the staging-file model"; ADR-0053 (rule-driven auto-placement) is that ADR. Under it, `maury review` classifies each finding and `accept` places it into the classified source file (repo-root `CLAUDE.md` / `profiles/<mode>/CLAUDE.md.fragment` / a host-tagged fragment) rather than appending to `mining-findings.md`. The single staging file **persists as the manual queue for unmatched findings** (`profile=None`), which is [ADR-0004](0004-rule-engine-classification.md)'s permanent escape hatch — so this supersession is partial (matched → fragment; unmatched → staging). Status: designed (ADR-0053 Proposed); build tracked by ADR-0053's slices.
- 2026-05-21 — **implementation shipped** (Phase 6 first slice).
  Two commits on devel:
  * `feat(mining): Content-Hash + commit-trailer formatters` (slice 1)
    — pure-logic module `src/maury/mining/run_branch.py` with
    `content_hash`, `generate_run_id`, `format_commit_subject`,
    `format_commit_body`, `format_finding_block`, and
    `branch_name_for`. 24 unit tests cover the Content-Hash
    normalization invariants, RFC 822 trailer format, em-dash
    subject + truncation, and the markdown block for the staging
    file. `CONTENT_HASH_ALGORITHM_VERSION` pinned at 1 per
    §Followups.
  * `feat(mining): write_run_branch + maury mine --write-run-branch`
    (slices 2-4) — adds `existing_content_hashes(repo_dir)` that
    runs `git log --all --grep="^Content-Hash:"` and
    `--grep="^Rejected-Content-Hash:"`, returning the union as
    the dedup set. Adds `write_run_branch(...)` that validates
    preconditions (git repo, clean worktree, branch absent),
    creates the branch off HEAD, appends each non-deduped finding
    as a section in `mining-findings.md`, and commits each one
    with the full trailer block. Skips the branch creation
    entirely when dedup eats every finding (no orphan empty
    branches). CLI flag `--write-run-branch` on `maury mine`
    wires it up; requires `--repo` and a host-identity baseline.
    Emits `mining_run_created` audit event per ADR-0035.

  **V1 simplification: the per-finding diff is an append to
  `mining-findings.md` at the repo root.** ADR-0022's body says
  "the diff IS the proposed change to CLAUDE.md / rules.yaml /
  etc." — that "etc." was doing work. For V1, every finding
  appends a structured section (`## maury: <kind> — <summary>`
  + rationale + block-quoted evidence + metadata bullets) to
  a single file. The reviewer reads the file, cherry-picks
  accepted findings to main, and refactors bullets into
  CLAUDE.md/rules.yaml as a separate manual step. A future
  ADR can introduce smarter routing once we have operator
  experience with the staging-file model.

  Remaining Phase 6/7 work (shipped 2026-05-21, see next entry):
  * `maury review <run-id>` — walk the branch with the review UI
    per §"Branch lifecycle".
  * `Rejected-Content-Hash` writing (the no-op metadata commit
    that lands rejections) — owned by `maury review`.
  * `maury rebase-run <run-id>` for the "main moved between
    mine and review" case. Owned by `maury review` slice.

- 2026-05-21 — **review side shipped** (Phase 7). Module
  `src/maury/mining/review.py` + CLI verbs `maury review <run-id>`
  and `maury rebase-run <run-id>`. Three notable refinements to the
  §"Branch lifecycle" / §"Rejection" design as written:

  1. **Apply-by-reconstruct, not literal `git cherry-pick`.** The
     ADR body says `maury review` "cherry-picks accepted commits."
     The V1 single-staging-file model (every finding appends to
     `mining-findings.md`) makes literal cherry-pick conflict-prone:
     skipping or rejecting an *earlier* finding then accepting a
     *later* one leaves the later commit's diff context (the earlier
     block) absent on the review branch → conflict. The engine
     instead reconstructs each accepted finding's appended block as
     `file@sha` minus `file@sha^` and re-appends it, committing with
     `git commit -C <sha> --cleanup=verbatim` so the original message
     + `Content-Hash` trailer ride through verbatim. The outcome is
     identical to what the ADR intends (per-finding commits carrying
     their trailers land on `maury/review/<run-id>`), but gap-safe
     and conflict-free. The mermaid diagram in
     [`workflow.md`](../workflow.md) §3 says "apply accepted commits"
     for the same reason.
  2. **Rejection commit only on full-walk completion.** Quitting
     mid-walk (`q`) keeps accepted/edited commits (real commits →
     resumable) but discards pending rejections, so there is at most
     one `Rejected` no-op commit per completed review. Resume scans
     both `Content-Hash` and `Rejected-Content-Hash` in
     `git log main..maury/review/<run-id>` to skip already-handled
     findings without re-prompting.
  3. **Edit preserves `Content-Hash`.** `[e]dit` opens the block in
     `$EDITOR` before committing but reuses the original message
     (`-C`), so the hash — the identity of the *idea Claude
     surfaced*, not the final wording — is unchanged. A future
     re-surface of the original phrasing therefore still dedups.

  Audit: `review_completed` (`{run_id, accepted, rejected,
  merged_to}`; edited counts as accepted, `merged_to` is null since
  review never merges — the operator merges). No new ADR-0035 event
  kinds were added. `--accept-all` / `--reject-all [--reason]` batch
  flags ship for scripted use. 44 tests (20 pure-logic, 13 git-layer,
  11 CLI).
