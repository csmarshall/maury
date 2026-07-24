"""Branch-fetch cross-trust-boundary promotion per ADR-0045 §4 + ADR-0022
§"Cross-repo promotion".

`maury promote --from <src-repo> --to <dest-repo> --to-mode <name>`:

A curator with read access to a source repo and write access to a
destination repo promotes findings *up the inheritance graph* (e.g. a
`work` finding that's really universal → `base`). The curator is the
destination-side reviewer — same accept/reject/edit/skip/quit loop as
`maury review`, but reading findings from another repo's `maury/run/*`
branches and landing the accepted ones on `maury/promoted/<id>` in the
destination, each carrying a `Promoted-From: <src-repo>@<sha>` trailer
for provenance.

Two guards compose, graph first (ADR-0045 §3):

1. **Graph constraint** (`promotion.graph`): a finding may only land in
   its source mode or an ancestor. Sibling / unrelated / descendant
   targets are refused *before* the curator sees them, with a "did you
   mean their shared ancestor?" hint from the LCA.
2. **Trust mechanics**: the curator can only write to repos they hold a
   deploy key for; promotion is a human-mediated copy, never a silent
   cross-boundary write.

Findings are applied by reconstruct-and-append (like `review`), not
`git cherry-pick` — the source SHA isn't reachable in the destination
repo, so the original commit message is *copied* (`%B`) and the
`Promoted-From` trailer appended. `Content-Hash` and `Source-Mode` ride
through in the copied message, so the destination's next mining run
dedups the promoted content (ADR-0026).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING

from maury.mining.review import (
    Decision,
    DecisionKind,
    Editor,
    RejectedFinding,
    _default_editor,
    commit_trailers,
    format_rejection_commit_body,
    parse_trailer_values,
)
from maury.mining.run_branch import (
    STAGING_FILE,
    TRAILER_CONTENT_HASH,
    TRAILER_SOURCE_MODE,
)
from maury.promotion.graph import (
    is_valid_promotion_target,
    lowest_common_ancestor,
)
from maury.promotion.proposal import (
    TRAILER_PROMOTE_TO,
    Proposal,
    read_proposals,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from maury.manifest import Manifest

# Provenance trailer added to every promoted commit (ADR-0045 §4).
PROMOTED_FROM_TRAILER = "Promoted-From"


class PromoteError(RuntimeError):
    """A promotion cannot proceed (missing repo/branch, unknown target
    mode, dirty dest worktree, git failure, etc.)."""


@dataclass(frozen=True)
class PromotionCandidate:
    """One finding on the source repo eligible for promotion review."""

    src_sha: str
    subject: str
    message: str
    """The source commit's full message (%B) — copied verbatim onto the
    promoted commit, with the Promoted-From trailer appended."""

    content_hash: str
    source_mode: str
    """The finding's Source-Mode trailer value (mode NAME); empty if the
    source commit predates ADR-0026."""

    block: str
    """The markdown the finding appended to mining-findings.md — shown to
    the curator and re-appended in the destination on accept."""

    src_branch: str


@dataclass(frozen=True)
class PromoteResult:
    """Outcome of `promote_run`."""

    promoted_id: str
    promoted_branch: str
    target_mode: str
    promoted: int = 0
    edited: int = 0
    rejected: int = 0
    skipped: int = 0
    graph_refused: int = 0
    """Findings refused by the graph constraint (never shown to curator)."""

    unresolved_source_mode: int = 0
    """Findings whose Source-Mode couldn't be resolved in the manifest."""

    resumed_skipped: int = 0
    quit_early: bool = False
    rejection_commit_written: bool = False
    graph_refused_notes: tuple[str, ...] = field(default_factory=tuple)
    """Human-readable 'did you mean <LCA>?' hints for refused findings."""


def promote_run(
    *,
    source_repo: Path,
    dest_repo: Path,
    target_mode_name: str,
    manifest: Manifest,
    promoted_id: str,
    curator_host: str,
    decide: Callable[[PromotionCandidate], Decision],
    editor: Editor = _default_editor,
    source_main: str = "main",
    dest_main: str = "main",
) -> PromoteResult:
    """Walk the source repo's `maury/run/*` findings and build a
    `maury/promoted/<promoted_id>` branch in the destination repo.

    Per finding:
      - resolve its Source-Mode to a manifest mode-id; unresolved → skip.
      - graph-check source-mode → target-mode; refused → skip (with an
        LCA hint), never shown to the curator (ADR-0045 §3).
      - otherwise call `decide(candidate)`:
          ACCEPT → re-append the block, commit with the copied message +
            Promoted-From trailer.
          EDIT   → same, block opened in $EDITOR first.
          REJECT → accumulate (content_hash, source_mode, reason).
          SKIP   → nothing.
          QUIT   → stop the walk (promoted/edited persist; resumable).

    On a full walk with ≥1 rejection, a no-op rejection commit is
    appended in the destination. Leaves the curator on the promoted
    branch.
    """
    _ensure_git_repo(source_repo, "source")
    _ensure_git_repo(dest_repo, "destination")
    _ensure_clean_worktree(dest_repo)

    target_mode_id = manifest.profile_id_by_name(target_mode_name)
    if target_mode_id is None:
        raise PromoteError(f"--to-mode {target_mode_name!r} is not a mode in the destination repo's manifest.")

    candidates = _enumerate_candidates(source_repo, source_main)

    promoted_branch = f"maury/promoted/{promoted_id}"
    _checkout_promoted_branch(dest_repo, promoted_branch, dest_main)
    already = _applied_content_hashes(dest_repo, dest_main, promoted_branch)

    promoted = edited = skipped = graph_refused = unresolved = resumed = 0
    rejected: list[RejectedFinding] = []
    refused_notes: list[str] = []
    quit_early = False

    for cand in candidates:
        if cand.content_hash and cand.content_hash in already:
            resumed += 1
            continue

        source_mode_id = manifest.profile_id_by_name(cand.source_mode) if cand.source_mode else None
        if source_mode_id is None:
            unresolved += 1
            continue

        if not is_valid_promotion_target(
            source_mode_id=source_mode_id, target_mode_id=target_mode_id, manifest=manifest
        ):
            graph_refused += 1
            refused_notes.append(_refusal_note(cand, target_mode_name, source_mode_id, target_mode_id, manifest))
            continue

        decision = decide(cand)
        if decision.kind is DecisionKind.QUIT:
            quit_early = True
            break
        if decision.kind is DecisionKind.SKIP:
            skipped += 1
            continue
        if decision.kind is DecisionKind.REJECT:
            rejected.append(
                RejectedFinding(content_hash=cand.content_hash, source_mode=cand.source_mode, reason=decision.reason)
            )
            continue
        if decision.kind is DecisionKind.EDIT:
            _apply_promotion(dest_repo, cand, source_repo=source_repo, editor=editor)
            edited += 1
            continue
        _apply_promotion(dest_repo, cand, source_repo=source_repo, editor=None)
        promoted += 1

    rejection_written = False
    if not quit_early and rejected:
        _write_rejection_commit(dest_repo, promoted_id=promoted_id, curator_host=curator_host, rejected=rejected)
        rejection_written = True

    return PromoteResult(
        promoted_id=promoted_id,
        promoted_branch=promoted_branch,
        target_mode=target_mode_name,
        promoted=promoted,
        edited=edited,
        rejected=len(rejected),
        skipped=skipped,
        graph_refused=graph_refused,
        unresolved_source_mode=unresolved,
        resumed_skipped=resumed,
        quit_early=quit_early,
        rejection_commit_written=rejection_written,
        graph_refused_notes=tuple(refused_notes),
    )


def _refusal_note(
    cand: PromotionCandidate,
    target_mode_name: str,
    source_mode_id: str,
    target_mode_id: str,
    manifest: Manifest,
) -> str:
    """Build a 'did you mean their shared ancestor?' hint for a
    graph-refused finding (ADR-0045 §8 + Followups)."""
    lca = lowest_common_ancestor(mode_a=source_mode_id, mode_b=target_mode_id, manifest=manifest)
    lca_name = manifest.profiles[lca].name if lca and lca in manifest.profiles else None
    base = f"{cand.subject!r}: {cand.source_mode!r} cannot promote to {target_mode_name!r} (not an ancestor)."
    if lca_name and lca_name != target_mode_name:
        return base + f" Their shared ancestor is {lca_name!r} — promote there instead."
    return base


def promote_review_run(
    *,
    source_repo: Path,
    dest_repo: Path,
    manifest: Manifest,
    promoted_id: str,
    curator_host: str,
    decide: Callable[[Proposal], Decision],
    editor: Editor = _default_editor,
    dest_main: str = "main",
) -> PromoteResult:
    """Walk the source repo's `proposals/promote-to-*/` queue (ADR-0045
    §4) and land approved proposals on `maury/promoted/<promoted_id>` in
    the destination repo.

    The proposal-queue analogue of `promote_run`: instead of reading the
    source's run branches, it reads pre-written proposal files (each one
    already names its `Promote-To` destination). The graph check still
    fires per proposal (defensive — a stale/illegal proposal in the queue
    is refused, never shown). Accepted proposals land with a
    `Promoted-From: <src>@proposal:<hash>` trailer.

    `target_mode` on the result is left empty — proposals each carry their
    own destination, so there is no single target mode for the run.
    """
    _ensure_git_repo(source_repo, "source")
    _ensure_git_repo(dest_repo, "destination")
    _ensure_clean_worktree(dest_repo)

    proposals = read_proposals(source_repo)

    promoted_branch = f"maury/promoted/{promoted_id}"
    _checkout_promoted_branch(dest_repo, promoted_branch, dest_main)
    already = _applied_content_hashes(dest_repo, dest_main, promoted_branch)
    src_label = source_repo.name or str(source_repo)

    promoted = edited = skipped = graph_refused = unresolved = resumed = 0
    rejected: list[RejectedFinding] = []
    refused_notes: list[str] = []
    quit_early = False

    for proposal in proposals:
        if proposal.content_hash and proposal.content_hash in already:
            resumed += 1
            continue

        source_mode_id = manifest.profile_id_by_name(proposal.source_mode) if proposal.source_mode else None
        target_mode_id = manifest.profile_id_by_name(proposal.dest_mode)
        if source_mode_id is None or target_mode_id is None:
            unresolved += 1
            continue

        if not is_valid_promotion_target(
            source_mode_id=source_mode_id, target_mode_id=target_mode_id, manifest=manifest
        ):
            graph_refused += 1
            lca = lowest_common_ancestor(mode_a=source_mode_id, mode_b=target_mode_id, manifest=manifest)
            lca_name = manifest.profiles[lca].name if lca and lca in manifest.profiles else "?"
            refused_notes.append(
                f"proposal {proposal.content_hash[:12]}: {proposal.source_mode!r} → "
                f"{proposal.dest_mode!r} refused; shared ancestor is {lca_name!r}."
            )
            continue

        decision = decide(proposal)
        if decision.kind is DecisionKind.QUIT:
            quit_early = True
            break
        if decision.kind is DecisionKind.SKIP:
            skipped += 1
            continue
        if decision.kind is DecisionKind.REJECT:
            rejected.append(
                RejectedFinding(
                    content_hash=proposal.content_hash, source_mode=proposal.source_mode, reason=decision.reason
                )
            )
            continue
        if decision.kind is DecisionKind.EDIT:
            _apply_proposal(dest_repo, proposal, src_label=src_label, editor=editor)
            edited += 1
            continue
        _apply_proposal(dest_repo, proposal, src_label=src_label, editor=None)
        promoted += 1

    rejection_written = False
    if not quit_early and rejected:
        _write_rejection_commit(dest_repo, promoted_id=promoted_id, curator_host=curator_host, rejected=rejected)
        rejection_written = True

    return PromoteResult(
        promoted_id=promoted_id,
        promoted_branch=promoted_branch,
        target_mode="",
        promoted=promoted,
        edited=edited,
        rejected=len(rejected),
        skipped=skipped,
        graph_refused=graph_refused,
        unresolved_source_mode=unresolved,
        resumed_skipped=resumed,
        quit_early=quit_early,
        rejection_commit_written=rejection_written,
        graph_refused_notes=tuple(refused_notes),
    )


def _apply_proposal(dest_repo: Path, proposal: Proposal, *, src_label: str, editor: Editor | None) -> None:
    """Append a proposal's finding block to the destination staging file
    and commit it with a constructed message + Promoted-From trailer."""
    block = _proposal_block(proposal.text)
    if editor is not None:
        block = _edit_block(block, editor=editor)

    staging_path = dest_repo / STAGING_FILE
    with staging_path.open("a", encoding="utf-8") as fh:
        fh.write(block)
    _git_or_raise(["git", "add", STAGING_FILE], cwd=dest_repo, action="stage promoted proposal")

    subject = _block_subject(block) or f"maury: promoted proposal {proposal.content_hash[:12]}"
    trailers = [
        (TRAILER_CONTENT_HASH, proposal.content_hash),
        (TRAILER_SOURCE_MODE, proposal.source_mode),
        (TRAILER_PROMOTE_TO, proposal.dest_mode),
        (PROMOTED_FROM_TRAILER, f"{src_label}@proposal:{proposal.content_hash}"),
    ]
    body = "\n".join(f"{k}: {v}" for k, v in trailers if v)
    with NamedTemporaryFile("w", suffix=".msg", delete=False, encoding="utf-8") as tmp:
        tmp.write(f"{subject}\n\n{body}\n")
        msg_path = Path(tmp.name)
    try:
        _git_or_raise(
            ["git", "commit", "-F", str(msg_path), "--cleanup=verbatim"],
            cwd=dest_repo,
            action="commit promoted proposal",
        )
    finally:
        msg_path.unlink(missing_ok=True)


def _proposal_block(text: str) -> str:
    """Extract the finding's markdown block (`## …` heading through the
    last bullet) from a proposal file, dropping the header and trailers."""
    block_start = text.find("## ")
    trailer_start = text.find(f"{TRAILER_PROMOTE_TO}:")
    if block_start == -1:
        return text
    end = trailer_start if trailer_start > block_start else len(text)
    return text[block_start:end].rstrip("\n") + "\n\n"


def _block_subject(block: str) -> str | None:
    """The `## maury: …` heading text (without the `## `), if present."""
    for line in block.splitlines():
        if line.startswith("## "):
            return line[3:].strip()
    return None


# ---- source enumeration -------------------------------------------------


def _enumerate_candidates(source_repo: Path, source_main: str) -> list[PromotionCandidate]:
    """Collect finding commits across all `maury/run/*` branches in the
    source repo, oldest-first, de-duplicated by Content-Hash."""
    rc, out = _run_git(["git", "for-each-ref", "--format=%(refname:short)", "refs/heads/maury/run/"], cwd=source_repo)
    if rc != 0:
        raise PromoteError(f"{source_repo}: cannot list maury/run/* branches: {out[:200]}")
    run_branches = [line.strip() for line in out.splitlines() if line.strip()]

    candidates: list[PromotionCandidate] = []
    seen: set[str] = set()
    for branch in run_branches:
        rc, out = _run_git(["git", "rev-list", "--reverse", f"{source_main}..{branch}"], cwd=source_repo)
        if rc != 0:
            continue
        for sha in (line.strip() for line in out.splitlines() if line.strip()):
            cand = _build_candidate(source_repo, sha, branch)
            if cand.content_hash and cand.content_hash in seen:
                continue
            if cand.content_hash:
                seen.add(cand.content_hash)
            candidates.append(cand)
    return candidates


def _build_candidate(source_repo: Path, sha: str, branch: str) -> PromotionCandidate:
    subject = _git_or_raise(["git", "show", "-s", "--format=%s", sha], cwd=source_repo, action="read subject").strip()
    message = _git_or_raise(["git", "show", "-s", "--format=%B", sha], cwd=source_repo, action="read message")
    # Parse identity from the trailer block only; `message` is retained full on
    # the candidate, but its evidence prose must not be scanned (poison guard).
    trailers = commit_trailers(message)
    hashes = parse_trailer_values(trailers, TRAILER_CONTENT_HASH)
    modes = parse_trailer_values(trailers, TRAILER_SOURCE_MODE)
    block = _appended_block(source_repo, sha)
    return PromotionCandidate(
        src_sha=sha,
        subject=subject,
        message=message,
        content_hash=hashes[0] if hashes else "",
        source_mode=modes[0] if modes else "",
        block=block,
        src_branch=branch,
    )


def _appended_block(repo: Path, sha: str) -> str:
    """The text this commit appended to the staging file: file@sha minus
    file@sha^ (each finding commit only appends)."""
    after = _file_at_ref(repo, f"{sha}:{STAGING_FILE}")
    before = _file_at_ref(repo, f"{sha}^:{STAGING_FILE}") or ""
    if after is None:
        raise PromoteError(f"commit {sha[:12]} does not touch {STAGING_FILE!r}; not a maury finding commit")
    return after[len(before) :]


def _file_at_ref(repo: Path, ref_path: str) -> str | None:
    rc, out = _run_git(["git", "show", ref_path], cwd=repo)
    return out if rc == 0 else None


# ---- destination write --------------------------------------------------


def _apply_promotion(dest_repo: Path, cand: PromotionCandidate, *, source_repo: Path, editor: Editor | None) -> None:
    """Append the finding's block to the destination staging file and
    commit it with the source message + a Promoted-From trailer."""
    block = cand.block
    if editor is not None:
        block = _edit_block(block, editor=editor)

    staging_path = dest_repo / STAGING_FILE
    with staging_path.open("a", encoding="utf-8") as fh:
        fh.write(block)
    _git_or_raise(["git", "add", STAGING_FILE], cwd=dest_repo, action="stage promoted finding")

    message = _message_with_promoted_from(cand, source_repo)
    with NamedTemporaryFile("w", suffix=".msg", delete=False, encoding="utf-8") as tmp:
        tmp.write(message)
        msg_path = Path(tmp.name)
    try:
        _git_or_raise(
            ["git", "commit", "-F", str(msg_path), "--cleanup=verbatim"],
            cwd=dest_repo,
            action="commit promoted finding",
        )
    finally:
        msg_path.unlink(missing_ok=True)


def _message_with_promoted_from(cand: PromotionCandidate, source_repo: Path) -> str:
    """Copy the source commit's message and append a Promoted-From trailer
    recording the source repo + original SHA (ADR-0045 §4)."""
    src_label = source_repo.name or str(source_repo)
    body = cand.message.rstrip("\n")
    return f"{body}\n{PROMOTED_FROM_TRAILER}: {src_label}@{cand.src_sha}\n"


def _edit_block(block: str, *, editor: Editor) -> str:
    with NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as tmp:
        tmp.write(block)
        tmp_path = Path(tmp.name)
    try:
        editor(tmp_path)
        return tmp_path.read_text(encoding="utf-8")
    finally:
        tmp_path.unlink(missing_ok=True)


def _write_rejection_commit(
    dest_repo: Path, *, promoted_id: str, curator_host: str, rejected: list[RejectedFinding]
) -> None:
    body = format_rejection_commit_body(run_id=promoted_id, curator_host=curator_host, rejected=rejected)
    from maury.mining.review import REJECTION_COMMIT_SUBJECT

    _git_or_raise(
        ["git", "commit", "--allow-empty", "-m", REJECTION_COMMIT_SUBJECT, "-m", body, "--cleanup=verbatim"],
        cwd=dest_repo,
        action="write promotion rejection commit",
    )


def _applied_content_hashes(dest_repo: Path, dest_main: str, promoted_branch: str) -> set[str]:
    """Content-Hash values already on the promoted branch (resume set)."""
    # Trailer blocks only, so evidence prose in a promoted finding commit can't
    # inflate the resume set (poison guard).
    rc, out = _run_git(
        ["git", "log", f"{dest_main}..{promoted_branch}", "--format=%(trailers:only=true,unfold=true)"],
        cwd=dest_repo,
    )
    if rc != 0:
        return set()
    applied = set(parse_trailer_values(out, TRAILER_CONTENT_HASH))
    applied |= set(parse_trailer_values(out, "Rejected-Content-Hash"))
    return applied


def _checkout_promoted_branch(dest_repo: Path, promoted_branch: str, dest_main: str) -> None:
    rc, _ = _run_git(["git", "show-ref", "--verify", "--quiet", f"refs/heads/{promoted_branch}"], cwd=dest_repo)
    if rc == 0:
        _git_or_raise(["git", "checkout", promoted_branch], cwd=dest_repo, action="resume promoted branch")
    else:
        main_sha = _git_or_raise(
            ["git", "rev-parse", "--verify", f"{dest_main}^{{commit}}"],
            cwd=dest_repo,
            action="resolve dest main",
        ).strip()
        _git_or_raise(
            ["git", "checkout", "-b", promoted_branch, main_sha], cwd=dest_repo, action="create promoted branch"
        )


# ---- git helpers --------------------------------------------------------


def _ensure_git_repo(repo: Path, label: str) -> None:
    rc, _ = _run_git(["git", "rev-parse", "--git-dir"], cwd=repo)
    if rc != 0:
        raise PromoteError(f"{label} repo {repo} is not a git working tree (or git not on PATH)")


def _ensure_clean_worktree(repo: Path) -> None:
    rc, out = _run_git(["git", "status", "--porcelain"], cwd=repo)
    if rc != 0:
        raise PromoteError(f"{repo}: git status failed: {out[:200]}")
    if out.strip():
        raise PromoteError(
            f"{repo}: destination working tree has uncommitted changes; commit or stash them before promoting."
        )


def _git_or_raise(cmd: list[str], *, cwd: Path, action: str) -> str:
    rc, out = _run_git(cmd, cwd=cwd)
    if rc != 0:
        raise PromoteError(f"git failed to {action} (rc={rc}): {out[:400]}")
    return out


def _run_git(cmd: list[str], *, cwd: Path) -> tuple[int, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30.0, check=False, cwd=str(cwd))
    except (OSError, subprocess.TimeoutExpired) as e:
        return -1, str(e)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


__all__ = [
    "PROMOTED_FROM_TRAILER",
    "PromoteError",
    "PromoteResult",
    "PromotionCandidate",
    "promote_review_run",
    "promote_run",
]
