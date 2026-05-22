"""Tests for `maury.promotion.proposal` — the ADR-0045 §1 proposal queue
write/read primitive + the graph-only `needs_promotion_proposal` trigger.
"""

from __future__ import annotations

from pathlib import Path

from maury.manifest import Manifest, ProfileSpec
from maury.mining.extractor import ExtractionWindow, Finding
from maury.mining.run_branch import content_hash
from maury.mining.transcripts import TranscriptMessage
from maury.promotion.proposal import (
    Proposal,
    format_proposal,
    needs_promotion_proposal,
    proposal_relpath,
    read_proposals,
    write_proposal,
)

_BASE, _PERSONAL, _WORK, _ACME = "p_base", "p_personal", "p_work", "p_acme"


def _manifest() -> Manifest:
    return Manifest(
        version=1,
        profiles={
            _BASE: ProfileSpec(name="base", extends=None),
            _PERSONAL: ProfileSpec(name="personal", extends=_BASE),
            _WORK: ProfileSpec(name="work", extends=_BASE),
            _ACME: ProfileSpec(name="work:acme-client", extends=_WORK),
        },
        hosts={},
    )


def _finding(text: str = "prefer terse responses", *, kind: str = "feedback", scope: str = "base") -> Finding:
    msg = TranscriptMessage(
        project="p",
        transcript_path=Path("/tmp/p/s.jsonl"),
        session_id="s",
        timestamp="2026-05-21T16:00:00Z",
        text=text,
    )
    return Finding(
        kind=kind,
        scope_hint=scope,
        text=text,
        evidence="ev",
        confidence="high",
        source_window=ExtractionWindow(project="p", index=0, messages=(msg,)),
    )


# ---- needs_promotion_proposal (graph-only trigger) ----------------------


def test_own_mode_needs_no_proposal() -> None:
    # host on work, finding destined for work → within writable subtree.
    assert needs_promotion_proposal(dest_mode_id=_WORK, host_mode_id=_WORK, manifest=_manifest()) is False


def test_descendant_needs_no_proposal() -> None:
    # host on work, finding destined for work:acme-client (descendant).
    assert needs_promotion_proposal(dest_mode_id=_ACME, host_mode_id=_WORK, manifest=_manifest()) is False


def test_ancestor_needs_proposal() -> None:
    # host on work, finding destined for base (ancestor → ro → propose).
    assert needs_promotion_proposal(dest_mode_id=_BASE, host_mode_id=_WORK, manifest=_manifest()) is True


def test_other_subtree_needs_proposal() -> None:
    # host on work, finding destined for personal (different subtree).
    assert needs_promotion_proposal(dest_mode_id=_PERSONAL, host_mode_id=_WORK, manifest=_manifest()) is True


# ---- path + format ------------------------------------------------------


def test_proposal_relpath_layout() -> None:
    rel = proposal_relpath(dest_mode="base", content_hash_value="abc123")
    assert rel == "proposals/promote-to-base/abc123.md"


def test_proposal_relpath_slugs_colon_paths() -> None:
    rel = proposal_relpath(dest_mode="personal:consulting", content_hash_value="h")
    assert ":" not in rel
    assert "promote-to-personal__consulting" in rel


def test_format_proposal_has_block_and_trailers() -> None:
    body = format_proposal(
        finding=_finding(),
        dest_mode="base",
        source_mode="work",
        source_host="host_aabbccdd",
        content_hash_value="deadbeef",
    )
    assert body.startswith("# maury promotion proposal")
    assert "prefer terse responses" in body  # the finding block
    assert "Promote-To: base" in body
    assert "Source-Mode: work" in body
    assert "Source-Host: host_aabbccdd" in body
    assert "Content-Hash: deadbeef" in body
    assert "Proposed-At: " in body


# ---- write + read round-trip --------------------------------------------


def test_write_and_read_roundtrip(tmp_path: Path) -> None:
    f = _finding()
    path = write_proposal(tmp_path, finding=f, dest_mode="base", source_mode="work", source_host="host_x")
    assert path.exists()
    expected_hash = content_hash(kind=f.kind, scope_hint=f.scope_hint, text=f.text)
    assert path.name == f"{expected_hash}.md"

    proposals = read_proposals(tmp_path)
    assert len(proposals) == 1
    p = proposals[0]
    assert isinstance(p, Proposal)
    assert p.dest_mode == "base"
    assert p.source_mode == "work"
    assert p.source_host == "host_x"
    assert p.content_hash == expected_hash


def test_write_is_idempotent_by_content_hash(tmp_path: Path) -> None:
    f = _finding()
    p1 = write_proposal(tmp_path, finding=f, dest_mode="base", source_mode="work", source_host="h")
    p2 = write_proposal(tmp_path, finding=f, dest_mode="base", source_mode="work", source_host="h")
    assert p1 == p2  # same path
    assert len(read_proposals(tmp_path)) == 1  # no duplicate


def test_read_filters_by_dest_mode(tmp_path: Path) -> None:
    write_proposal(tmp_path, finding=_finding("a"), dest_mode="base", source_mode="work", source_host="h")
    write_proposal(
        tmp_path, finding=_finding("b", kind="preference"), dest_mode="personal", source_mode="work", source_host="h"
    )
    assert len(read_proposals(tmp_path)) == 2
    base_only = read_proposals(tmp_path, dest_mode="base")
    assert len(base_only) == 1
    assert base_only[0].dest_mode == "base"


def test_read_empty_when_no_queue(tmp_path: Path) -> None:
    assert read_proposals(tmp_path) == []


def test_read_skips_malformed_files(tmp_path: Path) -> None:
    bad = tmp_path / "proposals" / "promote-to-base" / "junk.md"
    bad.parent.mkdir(parents=True)
    bad.write_text("not a real proposal\n")
    assert read_proposals(tmp_path) == []
