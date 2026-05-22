"""Promotion proposal queue per ADR-0045 §1 — the source side of the
proposal-queue mechanism.

When mining produces a finding whose destination mode is outside the
host's *writable subtree* (the host has rw on its own trust-boundary
repo but only ro on ancestors like `base`), maury can't push the finding
to the destination directly. Instead it writes a proposal to the host's
*own* repo at `proposals/promote-to-<dest>/<id>.md`. A curator with rw on
both repos later walks the queue with `maury promote-review` (slice 5).

Whether a finding needs a proposal is a graph-only question
(`needs_promotion_proposal`): the destination is writable iff the host's
registered mode is an ancestor-or-self of it. Findings targeting the
host's own mode or a descendant are handled by the normal run-branch /
review flow and need no proposal; findings targeting an ancestor (or a
different subtree) do.

The proposal file mirrors the finding's markdown block plus an RFC 822
trailer block (`Promote-To`, `Source-Mode`, `Source-Host`, `Content-Hash`,
`Proposed-At`) so it is both human-readable and machine-parseable by
`read_proposals`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from maury.mining.run_branch import (
    TRAILER_CONTENT_HASH,
    TRAILER_SOURCE_MODE,
    content_hash,
    format_finding_block,
)

if TYPE_CHECKING:
    from maury.manifest import Manifest
    from maury.mining.extractor import Finding

# Trailer keys unique to a proposal file (ADR-0045 §1).
TRAILER_PROMOTE_TO = "Promote-To"
TRAILER_SOURCE_HOST = "Source-Host"
TRAILER_PROPOSED_AT = "Proposed-At"

# Root of the proposal queue inside the host's own repo.
PROPOSALS_DIR = "proposals"


def needs_promotion_proposal(*, dest_mode_id: str, host_mode_id: str, manifest: Manifest) -> bool:
    """True if a finding destined for `dest_mode_id` cannot be written
    directly by a host registered to `host_mode_id`, and so needs a
    promotion proposal (ADR-0045 §1).

    The host has rw on its own subtree (its registered mode and all
    descendants), ro on ancestors. So the destination is writable iff
    the host's mode is an ancestor-or-self of it — i.e. `host_mode_id`
    appears in `dest`'s inheritance chain. If it does NOT, the
    destination is outside the writable subtree and needs a proposal.
    """
    chain = manifest.inheritance_chain(dest_mode_id)  # [root .. dest], inclusive
    return host_mode_id not in chain


def _slug(mode_name: str) -> str:
    """Filesystem-safe directory segment for a mode name. Mode names are
    colon-paths (`personal:consulting:acme`); colons become double
    underscores so the queue path is portable."""
    return re.sub(r"[^A-Za-z0-9_.-]", "__", mode_name)


def proposal_relpath(*, dest_mode: str, content_hash_value: str) -> str:
    """`proposals/promote-to-<dest>/<id>.md`. The id is the Content-Hash
    so re-mining the same idea is idempotent (overwrites, never
    duplicates)."""
    return f"{PROPOSALS_DIR}/promote-to-{_slug(dest_mode)}/{content_hash_value}.md"


def format_proposal(
    *,
    finding: Finding,
    dest_mode: str,
    source_mode: str,
    source_host: str,
    content_hash_value: str,
    when: datetime | None = None,
) -> str:
    """Render the proposal file: the finding's markdown block followed by
    the RFC 822 trailer block carrying promotion provenance."""
    moment = when if when is not None else datetime.now(UTC)
    block = format_finding_block(finding, run_id="(proposal)", source_mode=source_mode)
    trailers = [
        (TRAILER_PROMOTE_TO, dest_mode),
        (TRAILER_SOURCE_MODE, source_mode),
        (TRAILER_SOURCE_HOST, source_host),
        (TRAILER_CONTENT_HASH, content_hash_value),
        (TRAILER_PROPOSED_AT, moment.strftime("%Y-%m-%dT%H:%M:%SZ")),
    ]
    trailer_block = "\n".join(f"{k}: {v}" for k, v in trailers)
    return f"# maury promotion proposal\n\n{block}\n{trailer_block}\n"


def write_proposal(
    repo_dir: Path,
    *,
    finding: Finding,
    dest_mode: str,
    source_mode: str,
    source_host: str,
    when: datetime | None = None,
) -> Path:
    """Write (idempotently) a promotion proposal into the host's repo.

    The path is keyed by Content-Hash, so re-mining the same idea
    overwrites the same file rather than piling up duplicates. Returns
    the path written.
    """
    h = content_hash(kind=finding.kind, scope_hint=finding.scope_hint, text=finding.text)
    rel = proposal_relpath(dest_mode=dest_mode, content_hash_value=h)
    path = repo_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        format_proposal(
            finding=finding,
            dest_mode=dest_mode,
            source_mode=source_mode,
            source_host=source_host,
            content_hash_value=h,
            when=when,
        ),
        encoding="utf-8",
    )
    return path


@dataclass(frozen=True)
class Proposal:
    """One parsed promotion proposal from the queue."""

    dest_mode: str
    source_mode: str
    source_host: str
    content_hash: str
    proposed_at: str
    text: str
    """The full proposal file body (human-readable block + trailers)."""

    path: Path


def read_proposals(repo_dir: Path, *, dest_mode: str | None = None) -> list[Proposal]:
    """Parse every proposal under `proposals/promote-to-*/`.

    With `dest_mode`, only proposals targeting that mode are returned.
    Malformed files (missing the required trailers) are skipped rather
    than raising — a stray file in the queue must not break review.
    Sorted by (dest_mode, content_hash) for stable iteration order.
    """
    root = repo_dir / PROPOSALS_DIR
    if not root.is_dir():
        return []

    proposals: list[Proposal] = []
    for path in sorted(root.glob("promote-to-*/*.md")):
        text = path.read_text(encoding="utf-8")
        promote_to = _first_trailer(text, TRAILER_PROMOTE_TO)
        chash = _first_trailer(text, TRAILER_CONTENT_HASH)
        if promote_to is None or chash is None:
            continue  # not a well-formed proposal
        if dest_mode is not None and promote_to != dest_mode:
            continue
        proposals.append(
            Proposal(
                dest_mode=promote_to,
                source_mode=_first_trailer(text, TRAILER_SOURCE_MODE) or "",
                source_host=_first_trailer(text, TRAILER_SOURCE_HOST) or "",
                content_hash=chash,
                proposed_at=_first_trailer(text, TRAILER_PROPOSED_AT) or "",
                text=text,
                path=path,
            )
        )
    proposals.sort(key=lambda p: (p.dest_mode, p.content_hash))
    return proposals


def _first_trailer(text: str, key: str) -> str | None:
    prefix = f"{key}:"
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix):
            value = stripped.split(":", 1)[1].strip()
            if value:
                return value
    return None


__all__ = [
    "PROPOSALS_DIR",
    "TRAILER_PROMOTE_TO",
    "TRAILER_PROPOSED_AT",
    "TRAILER_SOURCE_HOST",
    "Proposal",
    "format_proposal",
    "needs_promotion_proposal",
    "proposal_relpath",
    "read_proposals",
    "write_proposal",
]
