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
from datetime import datetime
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
        Source-Transcript:  <jsonl path>
        Source-Window:      messages <start>-<end>
        Mining-Run:         maury/run/<run-id>

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


def format_finding_block(finding: Finding, *, run_id: str, crossref_state: str | None = None) -> str:
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
        f"- source: {_source_transcript_label(finding)}\n"
        f"- run: {branch_name_for(run_id)}\n"
        f"\n"
    )


__all__ = [
    "CONTENT_HASH_ALGORITHM_VERSION",
    "STAGING_FILE",
    "TRAILER_CONTENT_HASH",
    "TRAILER_REJECTED_CONTENT_HASH",
    "branch_name_for",
    "content_hash",
    "format_commit_body",
    "format_commit_subject",
    "format_finding_block",
    "generate_run_id",
]
