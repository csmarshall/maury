"""Mining-run review per ADR-0022 — the curator side of the loop.

`maury mine` writes a `maury/run/<run-id>` branch with one commit per
finding (see `run_branch.py`). `maury review <run-id>` walks that branch
oldest-first, prompting accept / reject / edit / skip per finding:

- **accept** → `git cherry-pick` the commit onto `maury/review/<run-id>`.
- **edit**   → `git cherry-pick -n` then `$EDITOR`, then commit reusing
  the original message (`Content-Hash` preserved — the hash is the
  identity of the *idea Claude surfaced*, not the final wording).
- **reject** → accumulate `(content_hash, reason)`; after the walk they
  land as a single trailing no-op metadata commit whose body lists
  `Rejected-Content-Hash` / `Rejected-Reason` trailers.
- **skip**   → no review-branch effect; the finding re-surfaces next run.

Review operates only on a repo the operator has **rw** on — their own
base/mode repo within a single trust boundary (workflow.md Diagram 3).
Cross-trust-boundary proposal is a *different* command, `maury promote`
(ADR-0045, Diagram 4); do not rebuild promotion here.

This module ships the **pure-logic** layer (this file, slice 1): branch
naming, the rejection-commit body formatter, the stale-base predicate,
and a tolerant trailer parser. The git layer (`review_run`, `rebase_run`)
and the interactive CLI wrap these in later slices.
"""

from __future__ import annotations

from dataclasses import dataclass

from maury.mining.run_branch import (
    TRAILER_CONTENT_HASH,
    TRAILER_REJECTED_CONTENT_HASH,
    branch_name_for,
)

# Subject line of the no-op rejection commit, per ADR-0022 §"Rejection: a
# no-op metadata commit". Stable so a reader (or a future tool) can spot
# rejection commits in `git log --oneline`.
REJECTION_COMMIT_SUBJECT = "maury: rejected during curator review"

# Trailer keys unique to the rejection commit body. The repeated
# `Rejected-Content-Hash` key is defined in run_branch.py (it's the dedup
# primitive shared with the miner's `existing_content_hashes` scan).
TRAILER_ORIGINAL_RUN = "Original-Run"
TRAILER_CURATOR_HOST = "Curator-Host"
TRAILER_REJECTED_COUNT = "Rejected-Count"
TRAILER_REJECTED_REASON = "Rejected-Reason"

# Sentinel written when the operator rejects a finding without giving a
# reason. Keeps the `Rejected-Reason:` line present (so the parser sees a
# 1:1 pairing with each `Rejected-Content-Hash:`) without inventing prose.
NO_REASON_SENTINEL = "(none)"


def review_branch_name_for(run_id: str) -> str:
    """Return the canonical `maury/review/<run-id>` branch name.

    Parallel to `run_branch.branch_name_for`, which yields the
    `maury/run/<run-id>` branch the findings were mined onto.
    """
    return f"maury/review/{run_id}"


@dataclass(frozen=True)
class RejectedFinding:
    """One finding the curator rejected during review.

    `content_hash` is the finding's ADR-0022 Content-Hash (carried from
    the run-branch commit's trailer); `reason` is the operator's optional
    free-text justification. An empty/blank reason renders as
    `NO_REASON_SENTINEL` in the commit body.
    """

    content_hash: str
    reason: str = ""


def format_rejection_commit_body(
    *,
    run_id: str,
    curator_host: str,
    rejected: list[RejectedFinding],
) -> str:
    """Build the no-op rejection commit body per ADR-0022.

    Layout (a blank line separates the run/host header from the
    per-finding pairs):

        Original-Run: maury/run/<run-id>
        Curator-Host: <host-id>
        Rejected-Count: <N>

        Rejected-Content-Hash: <hash>
        Rejected-Reason: <reason or (none)>
        Rejected-Content-Hash: <hash>
        Rejected-Reason: <reason or (none)>

    Each `Rejected-Content-Hash:` line is immediately followed by its
    `Rejected-Reason:` line so the pairing is unambiguous. The trailers
    use the same `<Key>: <value>` (single colon, single space) form as
    `run_branch.format_commit_body`, so `git interpret-trailers` parses
    them and `git log --grep="^Rejected-Content-Hash:"` indexes them for
    the next mining run's dedup scan.

    Callers write this commit only when `rejected` is non-empty; passing
    an empty list yields a header-only body (`Rejected-Count: 0`).
    """
    header = [
        (TRAILER_ORIGINAL_RUN, branch_name_for(run_id)),
        (TRAILER_CURATOR_HOST, curator_host),
        (TRAILER_REJECTED_COUNT, str(len(rejected))),
    ]
    header_block = "\n".join(f"{k}: {v}" for k, v in header)

    pair_lines: list[str] = []
    for rf in rejected:
        reason = rf.reason.strip() or NO_REASON_SENTINEL
        pair_lines.append(f"{TRAILER_REJECTED_CONTENT_HASH}: {rf.content_hash}")
        pair_lines.append(f"{TRAILER_REJECTED_REASON}: {reason}")

    if not pair_lines:
        # Header-only body (no rejections). Still valid; callers normally
        # avoid writing a rejection commit in this case.
        return f"{header_block}\n"

    pairs_block = "\n".join(pair_lines)
    return f"{header_block}\n\n{pairs_block}\n"


def is_stale_base(*, main_sha: str, merge_base_sha: str) -> bool:
    """Return True if `main` has moved since the run branch was forked.

    The run branch was created off `main`'s HEAD at mining time. If
    `main` has since advanced, the merge-base of `main` and the run
    branch is `main`'s *old* HEAD, which differs from `main`'s *current*
    HEAD. In that case ADR-0022 §"Stale base handling" says `maury
    review` must refuse and point at `maury rebase-run` — we never
    silently rebase mid-review.

    Both arguments are full commit SHAs (whitespace tolerated).
    """
    return main_sha.strip() != merge_base_sha.strip()


def parse_trailer_values(message: str, trailer: str) -> list[str]:
    """Extract every value for `trailer` from a commit message.

    Tolerant of leading whitespace because `git log` indents commit
    bodies by four spaces; matches on the trailer key followed by a
    colon and returns the trimmed value. Blank values are skipped.

    Used by the resume path (slice 2) to find which findings are already
    cherry-picked onto the review branch — it parses the
    `Content-Hash:` trailers in `git log main..maury/review/<run-id>` —
    and is exercised directly in unit tests.
    """
    prefix = f"{trailer}:"
    values: list[str] = []
    for line in message.splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix):
            value = stripped.split(":", 1)[1].strip()
            if value:
                values.append(value)
    return values


def content_hashes_in_log(log_text: str) -> set[str]:
    """Return the set of `Content-Hash` values present in `git log` text.

    Convenience wrapper over `parse_trailer_values` for the resume
    predicate: given the output of `git log main..maury/review/<run-id>`,
    the returned set is the findings already accepted onto the review
    branch, which the walk skips on a resumed review.
    """
    return set(parse_trailer_values(log_text, TRAILER_CONTENT_HASH))


__all__ = [
    "NO_REASON_SENTINEL",
    "REJECTION_COMMIT_SUBJECT",
    "TRAILER_CURATOR_HOST",
    "TRAILER_ORIGINAL_RUN",
    "TRAILER_REJECTED_COUNT",
    "TRAILER_REJECTED_REASON",
    "RejectedFinding",
    "content_hashes_in_log",
    "format_rejection_commit_body",
    "is_stale_base",
    "parse_trailer_values",
    "review_branch_name_for",
]
