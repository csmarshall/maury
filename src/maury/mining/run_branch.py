"""Mining run-branch generation per ADR-0022.

A mining run produces a git branch (`maury/run/<run-id>`) off main with
one commit per finding. The commit's diff IS the proposed change to a
single staging file (`mining-findings.md` at the repo root); the
commit's body IS the rationale (structured RFC 822 trailer block); the
commit's SHA IS the finding's identity.

This module ships two layers:

- **Pure logic** (this file, slice 1) — `content_hash`,
  `format_commit_subject`, `format_commit_body`, `format_finding_block`,
  `generate_run_id`. No I/O. Testable in isolation.
- **Git layer** (slice 3) — `write_run_branch`. Wraps the pure-logic
  output into actual `git commit` calls in the target repo.

The CLI integration (slice 4) lives in `cli.py`'s `mine_cmd`.

Per ADR-0022 §"Commit message format": every maury-generated commit
carries an RFC 822 trailer block (`Kind:`, `Content-Hash:`, etc.) that
`git interpret-trailers` understands and `git log --grep` queries in
sub-second time. The `Content-Hash` trailer is the dedup primitive: a
sha256 over the normalized (kind + scope + text) tuple. Two findings
with slightly different wording but the same idea hash identically.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from maury.mining.extractor import Finding

# Per ADR-0022 §Followups: pin the normalization version so future
# changes are detectable. Bump only with a documented migration.
CONTENT_HASH_ALGORITHM_VERSION = 1

# Trailer keys per ADR-0022 §"Commit message format". RFC 822 lines:
# `<Key>: <value>` with a space after the colon.
TRAILER_CONTENT_HASH = "Content-Hash"
TRAILER_REJECTED_CONTENT_HASH = "Rejected-Content-Hash"
TRAILER_KIND = "Kind"
TRAILER_SCOPE_HINT = "Scope-Hint"
TRAILER_CONFIDENCE = "Confidence"
TRAILER_CROSSREF_STATE = "Crossref-State"
TRAILER_SOURCE_TRANSCRIPT = "Source-Transcript"
TRAILER_SOURCE_WINDOW = "Source-Window"
TRAILER_MINING_RUN = "Mining-Run"
# Per ADR-0026: the mode this finding was mined under. Carries the mode
# NAME (a colon-path like `personal:consulting:acme` is fine). Used by
# `maury promote` to graph-check each finding's source mode against the
# target (ADR-0045), and by the (Content-Hash, Source-Mode) dedup tuple.
# Optional for backward-compat: commits mined before this trailer existed
# simply omit it.
TRAILER_SOURCE_MODE = "Source-Mode"

# Target staging file. The "diff IS the proposed change" per ADR-0022;
# in V1 every finding's diff is an append to this file at the repo root.
# Operators refactor accepted bullets into CLAUDE.md / rules.yaml after
# review (Phase 7 ships the review verb).
STAGING_FILE = "mining-findings.md"


def content_hash(*, kind: str, scope_hint: str, text: str) -> str:
    """Compute the ADR-0022 Content-Hash for a finding.

    Spec (frozen at CONTENT_HASH_ALGORITHM_VERSION=1):
      `sha256(kind + "\\n" + scope + "\\n" + normalized_text)`
      where `normalized_text` is:
        1. lowercased
        2. punctuation stripped
        3. whitespace collapsed to single spaces, leading/trailing
           whitespace stripped

    Two findings with the same idea but different wording produce the
    same hash; that's the whole point — dedup works across paraphrases.
    """
    normalized = _normalize_text(text)
    payload = f"{kind}\n{scope_hint}\n{normalized}".encode()
    return hashlib.sha256(payload).hexdigest()


def _normalize_text(raw: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    lowered = raw.lower()
    # Strip everything that isn't a letter, digit, or whitespace.
    no_punct = re.sub(r"[^a-z0-9\s]", "", lowered)
    # Collapse whitespace runs to a single space and strip ends.
    return " ".join(no_punct.split())


def generate_run_id(*, host_hex: str, when: datetime | None = None) -> str:
    """Build a stable run-id from the host's hex prefix + a UTC timestamp.

    Format: `<YYYY-MM-DDTHHMMSS>-<host-hex>`. Example:
      `2026-05-21T161234-24b2a0aa`.

    The timestamp uses second precision (no colons, no fractional
    seconds) so the run-id is safe in branch names and filesystem paths
    without escaping. Combined with the host hex it's effectively
    unique across an operator's hosts and across days; sub-second
    collisions on the same host are theoretical (mining is human-
    triggered, not a tight loop).
    """
    from datetime import UTC

    moment = when if when is not None else datetime.now(UTC)
    timestamp = moment.strftime("%Y-%m-%dT%H%M%S")
    return f"{timestamp}-{host_hex}"


def branch_name_for(run_id: str) -> str:
    """Return the canonical `maury/run/<run-id>` branch name."""
    return f"maury/run/{run_id}"


def format_commit_subject(finding: Finding) -> str:
    """Per ADR-0022: `maury: <kind> — <one-line summary>`.

    The summary is `finding.text` truncated to a sensible length so the
    commit subject stays readable in `git log --oneline`. ADR-0022's
    example uses an em-dash separator; we match it.
    """
    summary = _one_line(finding.text, max_len=72)
    return f"maury: {finding.kind} — {summary}"


def _one_line(text: str, *, max_len: int) -> str:
    """Collapse newlines + truncate with an ellipsis if needed."""
    flat = " ".join(text.split())
    if len(flat) <= max_len:
        return flat
    return flat[: max_len - 1].rstrip() + "…"


def format_commit_body(
    finding: Finding,
    *,
    run_id: str,
    crossref_state: str | None = None,
    source_mode: str | None = None,
) -> str:
    """Build the structured commit body for one finding per ADR-0022.

    Layout (blank line separates prose rationale from the RFC 822
    trailer block):

        <evidence prose, multiple paragraphs allowed>

        Kind:               <kind>
        Scope-Hint:         <scope>
        Confidence:         <confidence>
        Crossref-State:     <state or NEW>
        Content-Hash:       <sha256>
        Source-Mode:        <mode name>          (omitted if not supplied)
        Source-Transcript:  <jsonl path>
        Source-Window:      messages <start>-<end>
        Mining-Run:         maury/run/<run-id>

    `source_mode` is the mode the finding was mined under (ADR-0026),
    used by `maury promote` for the per-finding graph check. When None
    the `Source-Mode` trailer is omitted entirely (backward-compatible).

    The trailers MUST be in this exact form (key, colon, single space,
    value, newline) so `git interpret-trailers` parses them cleanly and
    `git log --grep="^Content-Hash:"` matches without surprises.
    """
    hash_value = content_hash(kind=finding.kind, scope_hint=finding.scope_hint, text=finding.text)
    window_range = _format_window_range(finding)

    rationale = finding.evidence.strip() if finding.evidence else "(no evidence captured)"

    trailers = [
        (TRAILER_KIND, finding.kind),
        (TRAILER_SCOPE_HINT, finding.scope_hint),
        (TRAILER_CONFIDENCE, finding.confidence),
        (TRAILER_CROSSREF_STATE, crossref_state or "NEW"),
        (TRAILER_CONTENT_HASH, hash_value),
    ]
    if source_mode:
        trailers.append((TRAILER_SOURCE_MODE, source_mode))
    trailers += [
        (TRAILER_SOURCE_TRANSCRIPT, _source_transcript_label(finding)),
        (TRAILER_SOURCE_WINDOW, window_range),
        (TRAILER_MINING_RUN, branch_name_for(run_id)),
    ]
    trailer_block = "\n".join(f"{k}: {v}" for k, v in trailers)
    return f"{rationale}\n\n{trailer_block}\n"


def _format_window_range(finding: Finding) -> str:
    """Render the source-window range as `messages <start>-<end>`."""
    w = finding.source_window
    # ExtractionWindow exposes its messages; first/last give the range.
    if not w.messages:
        return "messages <empty window>"
    start_idx = w.index * len(w.messages)
    end_idx = start_idx + len(w.messages) - 1
    return f"messages {start_idx}-{end_idx}"


def _source_transcript_label(finding: Finding) -> str:
    """Best-effort label for the source transcript file.

    The finding's source_window carries TranscriptMessages, each of
    which references a transcript path. We surface the first message's
    path as the canonical source; if multiple session_ids span the
    window, the path on the first message is still a useful pointer.
    """
    w = finding.source_window
    if not w.messages:
        return "(unknown)"
    first = w.messages[0]
    return str(getattr(first, "transcript_path", "(unknown)"))


def format_finding_block(
    finding: Finding, *, run_id: str, crossref_state: str | None = None, source_mode: str | None = None
) -> str:
    """Build the markdown block appended to `mining-findings.md` for this finding.

    Each finding lands as a self-contained `## <heading>` section so an
    operator skimming the file in `git diff` or in an editor can see
    the boundaries without parsing trailers. The trailer content lives
    on the *commit*, not in the markdown; the markdown is the
    human-facing artifact.

    Example output:

        ## maury: feedback — prefer terse responses
        <blank>
        The user has corrected this twice in the last week.
        <blank>
        > evidence: "<quote from transcript>"
        <blank>
        - kind: feedback
        - scope hint: base
        - confidence: high
        - source: ~/.claude/projects/abc/2026-05-06-1142.jsonl
    """
    subject = format_commit_subject(finding)
    state = crossref_state or "NEW"
    evidence_blob = finding.evidence.strip() if finding.evidence else "(no evidence captured)"
    # Block-quote the evidence so markdown viewers render it distinctively.
    evidence_md = "\n".join(f"> {line}" if line else ">" for line in evidence_blob.splitlines())
    return (
        f"## {subject}\n"
        f"\n"
        f"{finding.text.strip()}\n"
        f"\n"
        f"{evidence_md}\n"
        f"\n"
        f"- kind: {finding.kind}\n"
        f"- scope hint: {finding.scope_hint}\n"
        f"- confidence: {finding.confidence}\n"
        f"- crossref state: {state}\n"
        + (f"- source mode: {source_mode}\n" if source_mode else "")
        + f"- source: {_source_transcript_label(finding)}\n"
        f"- run: {branch_name_for(run_id)}\n"
        f"\n"
    )


# ---- git layer (slice 2 + 3) ---------------------------------------------


class RunBranchError(RuntimeError):
    """Raised when a run-branch operation cannot proceed (dirty work tree,
    branch already exists, git invocation failure, etc.)."""


@dataclass(frozen=True)
class RunBranchResult:
    """Outcome of `write_run_branch`."""

    run_id: str
    branch: str
    commits_written: int
    """Number of new commits added (one per non-deduped finding)."""

    skipped_dedup: int
    """Findings suppressed because their Content-Hash already appears in
    the repo's accepted or rejected history."""

    parent_sha: str
    """Commit SHA the run branch was created from (typically `main`'s
    HEAD at run time)."""

    skipped_hashes: tuple[str, ...] = field(default_factory=tuple)
    """Content-Hash values that matched the dedup set."""


# ---- dedup query ---------------------------------------------------------


def existing_content_hashes(repo_dir: Path) -> set[str]:
    """Return every Content-Hash and Rejected-Content-Hash currently in
    the repo's reachable history.

    Per ADR-0022 §"Dedup at next mining run", the dedup primitive is
    `git log --all --grep="^Content-Hash:"` (accepted history) plus
    `git log --all --grep="^Rejected-Content-Hash:"` (rejections that
    landed via the no-op metadata commit). Sub-second on multi-year
    history because git's grep is commit-bounded, not tree-bounded.

    Returns an empty set if the repo has no history yet or if the grep
    invocation fails for any reason (don't break mining on a
    transient git error).
    """
    hashes: set[str] = set()
    for trailer in (TRAILER_CONTENT_HASH, TRAILER_REJECTED_CONTENT_HASH):
        rc, out = _run_git(
            ["git", "log", "--all", f"--grep=^{trailer}:"],
            cwd=repo_dir,
        )
        if rc != 0:
            continue
        # Parse each "^{Trailer}: <hash>" line; tolerant of leading
        # whitespace (git -log indents bodies by 4 spaces).
        for line in out.splitlines():
            stripped = line.strip()
            if stripped.startswith(f"{trailer}:"):
                value = stripped.split(":", 1)[1].strip()
                if value:
                    hashes.add(value)
    return hashes


# ---- branch write --------------------------------------------------------


def write_run_branch(
    *,
    repo_dir: Path,
    findings: list[Finding],
    host_hex: str,
    crossref_states: dict[int, str] | None = None,
    when: datetime | None = None,
    source_mode: str | None = None,
) -> RunBranchResult:
    """Materialize the mining findings as a `maury/run/<run-id>` branch.

    Per ADR-0022:
      - Create the branch off the current HEAD.
      - For each finding (after dedup), append its markdown block to
        `mining-findings.md` and commit with the structured trailer
        block. One commit per finding.
      - Skip findings whose Content-Hash already appears in any
        reachable history (accepted or rejected per the trailers).

    Preconditions:
      - `repo_dir` is a git working tree (raises RunBranchError otherwise).
      - The working tree is clean (no uncommitted changes; raises if dirty).
      - The branch `maury/run/<run-id>` does not yet exist (raises if it
        does).

    `crossref_states` is an optional dict mapping finding index → state
    string (NEW / PRESENT_AND_CLEAR / PRESENT_BUT_UNCLEAR /
    PRESENT_AND_REINFORCED). Missing indices default to NEW.

    Returns a `RunBranchResult` summarizing what was committed. If
    every finding was dedup-suppressed, the branch is NOT created
    (no orphan empty branches); `commits_written=0` in that case.
    """
    _ensure_git_repo(repo_dir)
    _ensure_clean_worktree(repo_dir)

    parent_sha = _head_sha(repo_dir)
    run_id = generate_run_id(host_hex=host_hex, when=when)
    branch = branch_name_for(run_id)
    _ensure_branch_absent(repo_dir, branch)

    dedup_set = existing_content_hashes(repo_dir)
    crossref_states = crossref_states or {}

    # Filter findings to those that are not dedup hits. We compute
    # hashes once so commits use the same value.
    queued: list[tuple[Finding, str, str]] = []
    skipped: list[str] = []
    for idx, finding in enumerate(findings):
        h = content_hash(kind=finding.kind, scope_hint=finding.scope_hint, text=finding.text)
        if h in dedup_set:
            skipped.append(h)
            continue
        state = crossref_states.get(idx, "NEW")
        queued.append((finding, h, state))

    if not queued:
        return RunBranchResult(
            run_id=run_id,
            branch=branch,
            commits_written=0,
            skipped_dedup=len(skipped),
            parent_sha=parent_sha,
            skipped_hashes=tuple(skipped),
        )

    # Create + checkout the run branch.
    _git_or_raise(["git", "checkout", "-b", branch, parent_sha], cwd=repo_dir, action="create run branch")

    try:
        staging_path = repo_dir / STAGING_FILE
        for finding, _hash, state in queued:
            block = format_finding_block(finding, run_id=run_id, crossref_state=state, source_mode=source_mode)
            # Append; create if absent. The file lives at repo root.
            _append_staging_block(staging_path, block)
            _git_or_raise(
                ["git", "add", STAGING_FILE],
                cwd=repo_dir,
                action="stage findings file",
            )
            subject = format_commit_subject(finding)
            body = format_commit_body(finding, run_id=run_id, crossref_state=state, source_mode=source_mode)
            # `--allow-empty-message` is *not* used; we always have a
            # subject. `--cleanup=verbatim` keeps our trailer formatting.
            _git_or_raise(
                [
                    "git",
                    "commit",
                    "-m",
                    subject,
                    "-m",
                    body,
                    "--cleanup=verbatim",
                ],
                cwd=repo_dir,
                action="commit finding",
            )
    except RunBranchError:
        # Best-effort: try to leave the repo on the parent_sha so the
        # operator isn't stranded on a partial run branch. If even this
        # cleanup fails, let the original error propagate.
        _run_git(["git", "checkout", parent_sha], cwd=repo_dir)
        raise

    return RunBranchResult(
        run_id=run_id,
        branch=branch,
        commits_written=len(queued),
        skipped_dedup=len(skipped),
        parent_sha=parent_sha,
        skipped_hashes=tuple(skipped),
    )


# ---- git helpers ---------------------------------------------------------


def _append_staging_block(path: Path, block: str) -> None:
    """Append `block` to `path`. Create the file (with a header) if absent."""
    if path.exists():
        with path.open("a", encoding="utf-8") as fh:
            fh.write(block)
    else:
        with path.open("w", encoding="utf-8") as fh:
            fh.write("# Mining findings\n\nGenerated by `maury mine` per ADR-0022.\n\n")
            fh.write(block)


def _ensure_git_repo(repo_dir: Path) -> None:
    rc, _ = _run_git(["git", "rev-parse", "--git-dir"], cwd=repo_dir)
    if rc != 0:
        raise RunBranchError(f"{repo_dir}: not a git working tree (or git not on PATH)")


def _ensure_clean_worktree(repo_dir: Path) -> None:
    rc, out = _run_git(["git", "status", "--porcelain"], cwd=repo_dir)
    if rc != 0:
        raise RunBranchError(f"{repo_dir}: git status failed: {out[:200]}")
    if out.strip():
        raise RunBranchError(
            f"{repo_dir}: working tree has uncommitted changes; "
            f"commit or stash them before running a mining branch.\n{out[:400]}"
        )


def _ensure_branch_absent(repo_dir: Path, branch: str) -> None:
    rc, _out = _run_git(
        ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
        cwd=repo_dir,
    )
    if rc == 0:
        raise RunBranchError(
            f"{repo_dir}: branch {branch!r} already exists. Delete it (`git branch -D`) or re-run after a clock tick."
        )


def _head_sha(repo_dir: Path) -> str:
    rc, out = _run_git(["git", "rev-parse", "HEAD"], cwd=repo_dir)
    if rc != 0:
        raise RunBranchError(f"{repo_dir}: cannot resolve HEAD: {out[:200]}")
    return out.strip()


def _git_or_raise(cmd: list[str], *, cwd: Path, action: str) -> str:
    rc, out = _run_git(cmd, cwd=cwd)
    if rc != 0:
        raise RunBranchError(f"git failed to {action} (rc={rc}): {out[:400]}")
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
    "CONTENT_HASH_ALGORITHM_VERSION",
    "STAGING_FILE",
    "TRAILER_CONTENT_HASH",
    "TRAILER_REJECTED_CONTENT_HASH",
    "TRAILER_SOURCE_MODE",
    "RunBranchError",
    "RunBranchResult",
    "branch_name_for",
    "content_hash",
    "existing_content_hashes",
    "format_commit_body",
    "format_commit_subject",
    "format_finding_block",
    "generate_run_id",
    "write_run_branch",
]
