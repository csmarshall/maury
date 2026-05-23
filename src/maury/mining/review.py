"""Mining-run review per ADR-0022 — the curator side of the loop.

`maury mine` writes a `maury/run/<run-id>` branch with one commit per
finding (see `run_branch.py`). `maury review <run-id>` walks that branch
oldest-first, prompting accept / reject / edit / skip per finding:

- **accept** → re-append the finding's block and commit it with the
  original message (`Content-Hash` preserved — the hash is the identity
  of the *idea Claude surfaced*, not the final wording).
- **edit**   → same, but the block is opened in `$EDITOR` first.
- **reject** → accumulate `(content_hash, reason)`; after the walk they
  land as a single trailing no-op metadata commit whose body lists
  `Rejected-Content-Hash` / `Rejected-Reason` trailers.
- **skip**   → no review-branch effect; the finding re-surfaces next run.

Findings are applied by **reconstructing the appended block**
(`file@sha` minus `file@sha^`) and re-appending it — *not* by literal
`git cherry-pick`. ADR-0022 §"Branch lifecycle" says "cherry-pick," but
the V1 single-staging-file model makes literal cherry-pick conflict-prone:
each finding appends to `mining-findings.md`, so skipping/rejecting an
earlier finding then accepting a later one leaves the later commit's diff
context absent on the review branch. Reconstruct-and-append is gap-safe
and conflict-free; the outcome is identical (per-finding commits carrying
their trailers). Recorded in ADR-0022's amendment history.

Review operates only on a repo the operator has **rw** on — their own
base/mode repo within a single trust boundary (workflow.md Diagram 3).
Cross-trust-boundary proposal is a *different* command, `maury promote`
(ADR-0045, Diagram 4); do not rebuild promotion here.

Two layers:

- **Pure logic** — branch naming, the rejection-commit body formatter,
  the stale-base predicate, and a tolerant trailer parser. No I/O.
- **Git layer** — `review_run` (walk + apply, decision-provider injected
  so it is TTY-free and unit-testable) and `rebase_run` (the stale-base
  fixup). The interactive CLI supplies the prompt-based provider.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING

from maury.mining.run_branch import (
    STAGING_FILE,
    TRAILER_CONTENT_HASH,
    TRAILER_KIND,
    TRAILER_REJECTED_CONTENT_HASH,
    TRAILER_REJECTED_SOURCE_MODE,
    TRAILER_SOURCE_MODE,
    branch_name_for,
)
from maury.placement import place_finding, placement_relpath
from maury.rules.engine import classify_fragment
from maury.rules.loader import append_rule_to_file
from maury.rules.schema import Classification, Confidence
from maury.rules.synthesize import synthesize_classify_rule

if TYPE_CHECKING:
    from maury.llm import LLMClient
    from maury.manifest import Manifest
    from maury.rules.schema import Rule

# Rules file path relative to the base repo root.
RULES_RELPATH = ".meta/rules.yaml"

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
    the run-branch commit's trailer); `source_mode` is the mode it was
    mined under (ADR-0026), so rejection memory stays mode-scoped; `reason`
    is the operator's optional free-text justification. An empty/blank
    reason renders as `NO_REASON_SENTINEL` in the commit body; an empty
    `source_mode` simply omits the `Rejected-Source-Mode` line (the
    rejected hash then dedups as a wildcard across modes).
    """

    content_hash: str
    source_mode: str = ""
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
        Rejected-Source-Mode: <mode>          (omitted if not known)
        Rejected-Reason: <reason or (none)>
        Rejected-Content-Hash: <hash>
        Rejected-Reason: <reason or (none)>

    Each `Rejected-Content-Hash:` line is followed by its optional
    `Rejected-Source-Mode:` (ADR-0026, so rejection memory is mode-scoped)
    and its `Rejected-Reason:` line. The trailers use the same
    `<Key>: <value>` (single colon, single space) form as
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
        if rf.source_mode.strip():
            pair_lines.append(f"{TRAILER_REJECTED_SOURCE_MODE}: {rf.source_mode.strip()}")
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


# ---- git layer (slice 2) -------------------------------------------------


class ReviewError(RuntimeError):
    """Raised when a review operation cannot proceed (run branch missing,
    stale base, dirty work tree, git invocation failure, etc.)."""


class DecisionKind(StrEnum):
    """The four per-finding verbs plus the loop-terminating QUIT.

    QUIT stops the walk early; accepted/edited commits already on the
    review branch persist (the next `maury review` resumes), but pending
    rejections are discarded — the rejection commit is written only when
    a full walk completes.
    """

    ACCEPT = "accept"
    REJECT = "reject"
    EDIT = "edit"
    SKIP = "skip"
    QUIT = "quit"
    RECLASSIFY = "reclassify"


@dataclass(frozen=True)
class CommitView:
    """What the decision provider sees for one run-branch finding commit."""

    sha: str
    subject: str
    body: str
    content_hash: str
    """The finding's Content-Hash trailer (empty string if absent)."""

    source_mode: str
    """The finding's Source-Mode trailer (ADR-0026; empty if absent).
    Carried into the rejection record so rejection memory is mode-scoped."""

    block: str
    """The markdown the commit appended to the staging file — also the
    diff shown to the operator (it's an append-only model)."""

    classification: Classification | None = None
    """The rule engine's classification of this finding (ADR-0053), when
    `review_run` was given rules + a manifest. `None` when classification
    wasn't run; `classification.profile is None` means no rule matched
    (→ manual queue). The provider shows this so the operator can confirm
    or reclassify."""


@dataclass(frozen=True)
class ReclassifyTarget:
    """Where the operator wants a reclassified finding to go (ADR-0053)."""

    profile: str
    """Target mode name (or the base mode's name)."""

    host_overlay: str | None = None


@dataclass(frozen=True)
class Decision:
    """A decision provider's verdict on one finding.

    `reason` carries the optional rejection justification (only read when
    `kind is DecisionKind.REJECT`); `target` carries the operator's chosen
    destination (only read when `kind is DecisionKind.RECLASSIFY`).
    """

    kind: DecisionKind
    reason: str = ""
    target: ReclassifyTarget | None = None


# A decision provider maps a finding to a Decision. The interactive CLI
# (slice 3) supplies a TTY-prompt provider; tests supply a scripted one.
DecisionProvider = Callable[[CommitView], Decision]

# An editor opens the given path for the operator to edit in place. The
# default shells out to $EDITOR; tests inject a programmatic mutator.
Editor = Callable[[Path], None]


@dataclass(frozen=True)
class ReviewResult:
    """Outcome of `review_run`."""

    run_id: str
    review_branch: str
    parent_sha: str
    accepted: int = 0
    edited: int = 0
    rejected: int = 0
    skipped: int = 0
    """Explicit SKIP decisions this session."""

    resumed_skipped: int = 0
    """Findings already present on the review branch (resume), skipped
    without prompting."""

    quit_early: bool = False
    rejection_commit_written: bool = False

    placed: int = 0
    """Accepted/edited findings auto-placed into a classified source
    fragment (ADR-0053). The remainder of accepted/edited went to the
    manual queue (`mining-findings.md`)."""

    reclassified: int = 0
    """Findings the operator reclassified to a different target (ADR-0053)."""

    synthesized: int = 0
    """Reclassifications that produced a `classify` rule appended to
    `.meta/rules.yaml`."""

    @property
    def merged_to(self) -> None:
        """Review never merges; the operator does. Present so audit
        payload construction can stay uniform."""
        return None


@dataclass(frozen=True)
class RebaseRunResult:
    """Outcome of `rebase_run`."""

    run_id: str
    run_branch: str
    rebased: bool
    """False when main had not moved (nothing to do)."""

    new_base_sha: str


def _default_editor(path: Path) -> None:
    """Open `path` in `$EDITOR` (fallback `vi`) for in-place editing."""
    editor = os.environ.get("EDITOR", "vi")
    subprocess.run([editor, str(path)], check=True)


def review_run(
    *,
    repo_dir: Path,
    run_id: str,
    curator_host: str,
    decide: DecisionProvider,
    editor: Editor = _default_editor,
    main_ref: str = "main",
    rules: list[Rule] | None = None,
    manifest: Manifest | None = None,
    llm: LLMClient | None = None,
) -> ReviewResult:
    """Walk `maury/run/<run-id>` and build `maury/review/<run-id>`.

    Per ADR-0022 §"Branch lifecycle". Steps:

      1. Refuse if the run branch is missing, the work tree is dirty, or
         `main` has moved since mining (stale base → `maury rebase-run`).
      2. Create `maury/review/<run-id>` off `main` (or RESUME an existing
         one — already-applied findings are skipped by Content-Hash).
      3. Walk `git rev-list --reverse main..<run-branch>` oldest-first;
         for each finding call `decide(view)`:
           - ACCEPT → re-append the finding's block, commit `-C <sha>`
             (message + Content-Hash preserved verbatim).
           - EDIT   → same, but the block is opened in `$EDITOR` first.
           - REJECT → accumulate `(content_hash, reason)`.
           - SKIP   → no review-branch effect.
           - QUIT   → stop the walk (accepted/edited persist; pending
             rejections discarded).
      4. On a full walk with ≥1 rejection, append one no-op rejection
         metadata commit (`--allow-empty`).

    Findings are applied by reconstructing the appended block rather than
    `git cherry-pick`, because the single-staging-file append model makes
    literal cherry-pick conflict-prone when earlier findings are skipped.
    The outcome is identical: per-finding commits carrying their trailers.

    Leaves the operator checked out on the review branch (the deliverable).
    """
    _ensure_git_repo(repo_dir)
    _ensure_clean_worktree(repo_dir)

    run_branch = branch_name_for(run_id)
    _ensure_branch_exists(repo_dir, run_branch)

    main_sha = _rev_parse(repo_dir, main_ref)
    merge_base = _merge_base(repo_dir, main_ref, run_branch)
    if is_stale_base(main_sha=main_sha, merge_base_sha=merge_base):
        raise ReviewError(
            f"{main_ref} has moved since {run_branch} was mined "
            f"(merge-base {merge_base[:12]} != {main_ref} {main_sha[:12]}). "
            f"Run `maury rebase-run {run_id}` first; review will not silently rebase."
        )

    review_branch = review_branch_name_for(run_id)
    _checkout_review_branch(repo_dir, review_branch, main_sha)

    already_done = _applied_hashes(repo_dir, main_ref, review_branch)

    shas = _rev_list_oldest_first(repo_dir, main_ref, run_branch)

    known_profiles = {spec.name for spec in manifest.profiles.values()} if manifest is not None else set()

    accepted = edited = skipped = resumed = placed = reclassified = synthesized = 0
    rejected: list[RejectedFinding] = []
    quit_early = False

    for sha in shas:
        view = _build_commit_view(repo_dir, sha, rules=rules, known_profiles=known_profiles)
        if view.content_hash and view.content_hash in already_done:
            resumed += 1
            continue

        # ADR-0053: where would this finding auto-place? `None` → the
        # manual queue (mining-findings.md), which is also the case when
        # no rules/manifest were supplied or no rule matched.
        target = (
            placement_relpath(view.classification, manifest=manifest)
            if view.classification is not None and manifest is not None
            else None
        )

        decision = decide(view)
        if decision.kind is DecisionKind.QUIT:
            quit_early = True
            break
        if decision.kind is DecisionKind.SKIP:
            skipped += 1
            continue
        if decision.kind is DecisionKind.REJECT:
            rejected.append(
                RejectedFinding(
                    content_hash=view.content_hash,
                    source_mode=view.source_mode,
                    reason=decision.reason,
                )
            )
            continue
        if decision.kind is DecisionKind.RECLASSIFY and decision.target is not None and manifest is not None:
            rc_target, did_synth = _apply_reclassify(
                repo_dir,
                view,
                target=decision.target,
                run_id=run_id,
                manifest=manifest,
                rules=rules,
                llm=llm,
            )
            reclassified += 1
            placed += 1 if rc_target is not None else 0
            synthesized += 1 if did_synth else 0
            continue
        if decision.kind is DecisionKind.EDIT:
            _apply_finding(repo_dir, view, editor=editor, target_relpath=target, run_id=run_id)
            edited += 1
            placed += 1 if target is not None else 0
            continue
        # ACCEPT
        _apply_finding(repo_dir, view, editor=None, target_relpath=target, run_id=run_id)
        accepted += 1
        placed += 1 if target is not None else 0

    rejection_written = False
    if not quit_early and rejected:
        _write_rejection_commit(
            repo_dir,
            run_id=run_id,
            curator_host=curator_host,
            rejected=rejected,
        )
        rejection_written = True

    return ReviewResult(
        run_id=run_id,
        review_branch=review_branch,
        parent_sha=main_sha,
        accepted=accepted,
        edited=edited,
        rejected=len(rejected),
        skipped=skipped,
        resumed_skipped=resumed,
        quit_early=quit_early,
        rejection_commit_written=rejection_written,
        placed=placed,
        reclassified=reclassified,
        synthesized=synthesized,
    )


def rebase_run(
    *,
    repo_dir: Path,
    run_id: str,
    main_ref: str = "main",
) -> RebaseRunResult:
    """Rebase `maury/run/<run-id>` onto current `main` per ADR-0022
    §"Stale base handling".

    A no-op when `main` has not moved. On a rebase conflict the rebase is
    aborted and a `ReviewError` is raised pointing the operator at manual
    resolution — we do not leave the repo mid-rebase.
    """
    _ensure_git_repo(repo_dir)
    _ensure_clean_worktree(repo_dir)

    run_branch = branch_name_for(run_id)
    _ensure_branch_exists(repo_dir, run_branch)

    main_sha = _rev_parse(repo_dir, main_ref)
    merge_base = _merge_base(repo_dir, main_ref, run_branch)
    if not is_stale_base(main_sha=main_sha, merge_base_sha=merge_base):
        return RebaseRunResult(run_id=run_id, run_branch=run_branch, rebased=False, new_base_sha=main_sha)

    _git_or_raise(["git", "checkout", run_branch], cwd=repo_dir, action="checkout run branch")
    rc, out = _run_git(["git", "rebase", main_ref], cwd=repo_dir)
    if rc != 0:
        # Abort so the operator isn't stranded mid-rebase.
        _run_git(["git", "rebase", "--abort"], cwd=repo_dir)
        raise ReviewError(
            f"rebase of {run_branch} onto {main_ref} conflicted; aborted. "
            f"Resolve by hand (`git checkout {run_branch} && git rebase {main_ref}`).\n{out[:400]}"
        )
    return RebaseRunResult(run_id=run_id, run_branch=run_branch, rebased=True, new_base_sha=main_sha)


# ---- git helpers ---------------------------------------------------------


def _apply_reclassify(
    repo_dir: Path,
    view: CommitView,
    *,
    target: ReclassifyTarget,
    run_id: str,
    manifest: Manifest,
    rules: list[Rule] | None,
    llm: LLMClient | None,
) -> tuple[str | None, bool]:
    """Place a reclassified finding into the operator's chosen target and,
    when an LLM is available, synthesize + append a `classify` rule so
    future similar fragments auto-route there (ADR-0053 / Phase 8 path A).

    The placement and the synthesized rule (if any) ride one commit.
    Returns (target_relpath, synthesized?).
    """
    relpath = placement_relpath(
        Classification(
            profile=target.profile,
            host_overlay=target.host_overlay,
            confidence=Confidence.MEDIUM,
            trace=(),
            forbidden_by=(),
        ),
        manifest=manifest,
    )

    also_stage: tuple[str, ...] = ()
    synthesized = False
    if llm is not None:
        kinds = parse_trailer_values(view.body, TRAILER_KIND)
        proposal = synthesize_classify_rule(
            finding_text=_finding_text_from_block(view.block),
            finding_kind=kinds[0] if kinds else "preference",
            target_profile=target.profile,
            host_overlay=target.host_overlay,
            source_run=branch_name_for(run_id),
            llm=llm,
        )
        if proposal is not None:
            append_rule_to_file(repo_dir / RULES_RELPATH, proposal.rule)
            also_stage = (RULES_RELPATH,)
            synthesized = True

    _apply_finding(repo_dir, view, editor=None, target_relpath=relpath, run_id=run_id, also_stage=also_stage)
    return relpath, synthesized


def _apply_finding(
    repo_dir: Path,
    view: CommitView,
    *,
    editor: Editor | None,
    target_relpath: str | None = None,
    run_id: str = "",
    also_stage: tuple[str, ...] = (),
) -> None:
    """Apply an accepted/edited finding on the review branch and commit it
    with the original commit's message (`-C`).

    - `target_relpath is None` (no rule matched, or no rules supplied) →
      append the run-branch block to the manual queue `mining-findings.md`
      (ADR-0022 behavior / ADR-0004 manual queue).
    - otherwise (ADR-0053) → place the finding's preference text, with
      provenance, into the classified source fragment `target_relpath`.

    For EDIT the block is opened in `$EDITOR` first.
    """
    block = view.block
    if editor is not None:
        block = _edit_block(block, editor=editor)

    if target_relpath is None:
        staging_path = repo_dir / STAGING_FILE
        with staging_path.open("a", encoding="utf-8") as fh:
            fh.write(block)
        rel = STAGING_FILE
    else:
        place_finding(
            repo_dir,
            relpath=target_relpath,
            finding_text=_finding_text_from_block(block),
            content_hash=view.content_hash,
            source_run=branch_name_for(run_id),
        )
        rel = target_relpath

    _git_or_raise(["git", "add", rel, *also_stage], cwd=repo_dir, action="stage finding")
    _git_or_raise(
        ["git", "commit", "-C", view.sha, "--cleanup=verbatim"],
        cwd=repo_dir,
        action="commit accepted finding",
    )


def _finding_text_from_block(block: str) -> str:
    """Extract the finding's preference text from its mining-findings.md
    block: the paragraph after the `## ` heading, before the evidence
    block-quote / metadata bullets. Falls back to the whole block if the
    expected shape isn't found (e.g. a hand-edited block)."""
    lines = block.splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.startswith("## ")), None)
    if start is None:
        return block.strip()
    collected: list[str] = []
    for ln in lines[start + 1 :]:
        stripped = ln.strip()
        if stripped.startswith(">") or stripped.startswith("- "):
            break
        collected.append(ln)
    text = "\n".join(collected).strip()
    return text or block.strip()


def _edit_block(block: str, *, editor: Editor) -> str:
    """Write `block` to a temp file, let the operator edit it, return the
    edited content."""
    with NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as tmp:
        tmp.write(block)
        tmp_path = Path(tmp.name)
    try:
        editor(tmp_path)
        return tmp_path.read_text(encoding="utf-8")
    finally:
        tmp_path.unlink(missing_ok=True)


def _write_rejection_commit(
    repo_dir: Path,
    *,
    run_id: str,
    curator_host: str,
    rejected: list[RejectedFinding],
) -> None:
    body = format_rejection_commit_body(run_id=run_id, curator_host=curator_host, rejected=rejected)
    _git_or_raise(
        [
            "git",
            "commit",
            "--allow-empty",
            "-m",
            REJECTION_COMMIT_SUBJECT,
            "-m",
            body,
            "--cleanup=verbatim",
        ],
        cwd=repo_dir,
        action="write rejection commit",
    )


def _build_commit_view(
    repo_dir: Path,
    sha: str,
    *,
    rules: list[Rule] | None = None,
    known_profiles: set[str] | None = None,
) -> CommitView:
    subject = _git_or_raise(["git", "show", "-s", "--format=%s", sha], cwd=repo_dir, action="read subject").strip()
    body = _git_or_raise(["git", "show", "-s", "--format=%b", sha], cwd=repo_dir, action="read body")
    hashes = parse_trailer_values(body, TRAILER_CONTENT_HASH)
    content_hash = hashes[0] if hashes else ""
    modes = parse_trailer_values(body, TRAILER_SOURCE_MODE)
    source_mode = modes[0] if modes else ""
    block = _appended_block(repo_dir, sha)
    # ADR-0053: classify the finding for auto-placement, when rules were
    # supplied. Classify on the finding's text (not the metadata bullets).
    classification = None
    if rules is not None:
        classification = classify_fragment(_finding_text_from_block(block), rules, known_profiles or set())
    return CommitView(
        sha=sha,
        subject=subject,
        body=body,
        content_hash=content_hash,
        source_mode=source_mode,
        block=block,
        classification=classification,
    )


def _appended_block(repo_dir: Path, sha: str) -> str:
    """Return the text this commit appended to the staging file.

    Each finding commit only appends, so `file@sha = file@sha^ + block`.
    We compute `block = after[len(before):]` — robust and conflict-free,
    no diff parsing. `before` is empty when the commit created the file.
    """
    after = _file_at_ref(repo_dir, f"{sha}:{STAGING_FILE}")
    before = _file_at_ref(repo_dir, f"{sha}^:{STAGING_FILE}") or ""
    if after is None:
        raise ReviewError(f"commit {sha[:12]} does not touch {STAGING_FILE!r}; not a maury finding commit")
    return after[len(before) :]


def _file_at_ref(repo_dir: Path, ref_path: str) -> str | None:
    """`git show <ref>:<path>`; None if the path doesn't exist at the ref."""
    rc, out = _run_git(["git", "show", ref_path], cwd=repo_dir)
    return out if rc == 0 else None


def _applied_hashes(repo_dir: Path, main_ref: str, review_branch: str) -> set[str]:
    """Content-Hash + Rejected-Content-Hash already recorded on the review
    branch (`main..review_branch`) — the resume-skip set."""
    rc, out = _run_git(["git", "log", f"{main_ref}..{review_branch}"], cwd=repo_dir)
    if rc != 0:
        return set()
    applied = set(parse_trailer_values(out, TRAILER_CONTENT_HASH))
    applied |= set(parse_trailer_values(out, TRAILER_REJECTED_CONTENT_HASH))
    return applied


def _rev_list_oldest_first(repo_dir: Path, main_ref: str, run_branch: str) -> list[str]:
    out = _git_or_raise(
        ["git", "rev-list", "--reverse", f"{main_ref}..{run_branch}"],
        cwd=repo_dir,
        action="list run-branch commits",
    )
    return [line.strip() for line in out.splitlines() if line.strip()]


def _checkout_review_branch(repo_dir: Path, review_branch: str, main_sha: str) -> None:
    """Resume an existing review branch, else create it off `main`."""
    rc, _ = _run_git(["git", "show-ref", "--verify", "--quiet", f"refs/heads/{review_branch}"], cwd=repo_dir)
    if rc == 0:
        _git_or_raise(["git", "checkout", review_branch], cwd=repo_dir, action="resume review branch")
    else:
        _git_or_raise(
            ["git", "checkout", "-b", review_branch, main_sha],
            cwd=repo_dir,
            action="create review branch",
        )


def _merge_base(repo_dir: Path, a: str, b: str) -> str:
    out = _git_or_raise(["git", "merge-base", a, b], cwd=repo_dir, action="compute merge-base")
    return out.strip()


def _rev_parse(repo_dir: Path, ref: str) -> str:
    rc, out = _run_git(["git", "rev-parse", "--verify", f"{ref}^{{commit}}"], cwd=repo_dir)
    if rc != 0:
        raise ReviewError(f"{repo_dir}: cannot resolve ref {ref!r}: {out[:200]}")
    return out.strip()


def _ensure_git_repo(repo_dir: Path) -> None:
    rc, _ = _run_git(["git", "rev-parse", "--git-dir"], cwd=repo_dir)
    if rc != 0:
        raise ReviewError(f"{repo_dir}: not a git working tree (or git not on PATH)")


def _ensure_clean_worktree(repo_dir: Path) -> None:
    rc, out = _run_git(["git", "status", "--porcelain"], cwd=repo_dir)
    if rc != 0:
        raise ReviewError(f"{repo_dir}: git status failed: {out[:200]}")
    if out.strip():
        raise ReviewError(
            f"{repo_dir}: working tree has uncommitted changes; commit or stash them before reviewing.\n{out[:400]}"
        )


def _ensure_branch_exists(repo_dir: Path, branch: str) -> None:
    rc, _ = _run_git(["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], cwd=repo_dir)
    if rc != 0:
        raise ReviewError(f"{repo_dir}: branch {branch!r} does not exist (was it mined? already merged/deleted?)")


def _git_or_raise(cmd: list[str], *, cwd: Path, action: str) -> str:
    rc, out = _run_git(cmd, cwd=cwd)
    if rc != 0:
        raise ReviewError(f"git failed to {action} (rc={rc}): {out[:400]}")
    return out


def _run_git(cmd: list[str], *, cwd: Path) -> tuple[int, str]:
    """Run a git command in cwd; return (rc, combined stdout+stderr)."""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30.0,
            check=False,
            cwd=str(cwd),
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return -1, str(e)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


__all__ = [
    "NO_REASON_SENTINEL",
    "REJECTION_COMMIT_SUBJECT",
    "TRAILER_CURATOR_HOST",
    "TRAILER_ORIGINAL_RUN",
    "TRAILER_REJECTED_COUNT",
    "TRAILER_REJECTED_REASON",
    "CommitView",
    "Decision",
    "DecisionKind",
    "DecisionProvider",
    "Editor",
    "RebaseRunResult",
    "RejectedFinding",
    "ReviewError",
    "ReviewResult",
    "content_hashes_in_log",
    "format_rejection_commit_body",
    "is_stale_base",
    "parse_trailer_values",
    "rebase_run",
    "review_branch_name_for",
    "review_run",
]
