"""Tests for `maury.mining.run_branch` — Phase 6 / ADR-0022 pure-logic
slice (Content-Hash + trailer formatting + run-id generation).

The git-layer slice (`write_run_branch`) is tested separately.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from maury.mining.extractor import ExtractionWindow, Finding
from maury.mining.run_branch import (
    CONTENT_HASH_ALGORITHM_VERSION,
    STAGING_FILE,
    branch_name_for,
    content_hash,
    format_commit_body,
    format_commit_subject,
    format_finding_block,
    generate_run_id,
)
from maury.mining.transcripts import TranscriptMessage


def _msg(*, text: str = "user message", session: str = "sess-1") -> TranscriptMessage:
    return TranscriptMessage(
        project="test-project",
        transcript_path=Path("/tmp/test/sess-1.jsonl"),
        session_id=session,
        timestamp="2026-05-21T16:00:00Z",
        text=text,
    )


def _window(messages: tuple[TranscriptMessage, ...] | None = None, *, index: int = 0) -> ExtractionWindow:
    return ExtractionWindow(
        project="test-project",
        index=index,
        messages=messages if messages is not None else (_msg(),),
    )


def _finding(
    *,
    kind: str = "feedback",
    scope_hint: str = "base",
    text: str = "prefer terse responses",
    evidence: str = 'user said: "stop padding your answers"',
    confidence: str = "high",
    window: ExtractionWindow | None = None,
) -> Finding:
    return Finding(
        kind=kind,
        scope_hint=scope_hint,
        text=text,
        evidence=evidence,
        confidence=confidence,
        source_window=window or _window(),
    )


# ---- content_hash --------------------------------------------------------


def test_content_hash_is_deterministic_for_same_inputs() -> None:
    a = content_hash(kind="feedback", scope_hint="base", text="prefer terse")
    b = content_hash(kind="feedback", scope_hint="base", text="prefer terse")
    assert a == b
    assert len(a) == 64  # sha256 hex


def test_content_hash_normalizes_case_punctuation_whitespace() -> None:
    """Two findings with the same idea but different wording must
    produce the same hash. Per ADR-0022."""
    same_idea_variants = [
        "prefer terse responses",
        "Prefer terse responses.",
        "PREFER  TERSE   responses!",
        " prefer terse responses ",
        "prefer, terse responses;",
    ]
    hashes = {content_hash(kind="feedback", scope_hint="base", text=v) for v in same_idea_variants}
    assert len(hashes) == 1, hashes


def test_content_hash_differs_on_kind() -> None:
    a = content_hash(kind="feedback", scope_hint="base", text="x")
    b = content_hash(kind="preference", scope_hint="base", text="x")
    assert a != b


def test_content_hash_differs_on_scope() -> None:
    a = content_hash(kind="feedback", scope_hint="base", text="x")
    b = content_hash(kind="feedback", scope_hint="personal", text="x")
    assert a != b


def test_content_hash_differs_on_real_text_difference() -> None:
    a = content_hash(kind="feedback", scope_hint="base", text="prefer terse responses")
    b = content_hash(kind="feedback", scope_hint="base", text="prefer verbose responses")
    assert a != b


def test_content_hash_algorithm_version_is_pinned() -> None:
    """Per ADR-0022 §Followups: bump only with a documented migration."""
    assert CONTENT_HASH_ALGORITHM_VERSION == 1


# ---- generate_run_id -----------------------------------------------------


def test_generate_run_id_format() -> None:
    when = datetime(2026, 5, 21, 16, 12, 34, tzinfo=UTC)
    rid = generate_run_id(host_hex="24b2a0aa", when=when)
    assert rid == "2026-05-21T161234-24b2a0aa"


def test_generate_run_id_default_uses_now_utc() -> None:
    """No fixed value to assert — just verify the shape."""
    rid = generate_run_id(host_hex="ffffffff")
    # YYYY-MM-DDTHHMMSS-host_hex
    parts = rid.split("-")
    # YYYY, MM, DDTHHMMSS, host_hex
    assert len(parts) == 4
    assert parts[-1] == "ffffffff"


def test_branch_name_for() -> None:
    assert branch_name_for("2026-05-21T161234-24b2a0aa") == "maury/run/2026-05-21T161234-24b2a0aa"


# ---- format_commit_subject ----------------------------------------------


def test_commit_subject_uses_em_dash_and_kind() -> None:
    """Per ADR-0022's example: `maury: feedback — prefer terse...`."""
    s = format_commit_subject(_finding(kind="feedback", text="prefer terse responses"))
    assert s.startswith("maury: feedback — ")
    assert "prefer terse responses" in s


def test_commit_subject_truncates_long_text() -> None:
    s = format_commit_subject(_finding(text="x" * 200))
    # Subject must stay short — 72 chars of summary + the "maury: kind — " prefix.
    assert len(s) <= 100
    assert s.endswith("…")


def test_commit_subject_collapses_newlines() -> None:
    s = format_commit_subject(_finding(text="multi\nline\ntext"))
    assert "\n" not in s
    assert "multi line text" in s


# ---- format_commit_body -------------------------------------------------


def test_commit_body_has_all_required_trailers() -> None:
    body = format_commit_body(_finding(), run_id="rid")
    expected_keys = [
        "Kind:",
        "Scope-Hint:",
        "Confidence:",
        "Crossref-State:",
        "Content-Hash:",
        "Source-Transcript:",
        "Source-Window:",
        "Mining-Run:",
    ]
    for key in expected_keys:
        assert key in body, f"missing trailer key {key!r} in body:\n{body}"


def test_commit_body_trailer_format_is_rfc822() -> None:
    """Trailers are `<Key>: <value>` — single colon, single space.
    `git interpret-trailers` needs this exact form."""
    body = format_commit_body(_finding(), run_id="rid")
    # No double-spaces after the colon; no missing space.
    assert "Kind:  " not in body
    assert "Kind:feedback" not in body
    assert "Kind: feedback" in body


def test_commit_body_content_hash_is_stable() -> None:
    body = format_commit_body(_finding(), run_id="rid")
    expected = content_hash(kind="feedback", scope_hint="base", text="prefer terse responses")
    assert f"Content-Hash: {expected}" in body


def test_commit_body_crossref_state_defaults_to_new() -> None:
    body = format_commit_body(_finding(), run_id="rid")
    assert "Crossref-State: NEW" in body


def test_commit_body_carries_supplied_crossref_state() -> None:
    body = format_commit_body(_finding(), run_id="rid", crossref_state="PRESENT_AND_CLEAR")
    assert "Crossref-State: PRESENT_AND_CLEAR" in body
    assert "Crossref-State: NEW" not in body


def test_commit_body_uses_evidence_as_rationale() -> None:
    body = format_commit_body(_finding(evidence="The user repeatedly asks for X"), run_id="rid")
    assert "The user repeatedly asks for X" in body
    # Rationale comes BEFORE the trailer block (separated by a blank line).
    assert body.index("The user repeatedly asks for X") < body.index("Kind:")


def test_commit_body_handles_empty_evidence_gracefully() -> None:
    body = format_commit_body(_finding(evidence=""), run_id="rid")
    assert "(no evidence captured)" in body
    # Trailers still present.
    assert "Content-Hash:" in body


def test_commit_body_includes_mining_run_branch_name() -> None:
    body = format_commit_body(_finding(), run_id="2026-05-21T161234-aabbccdd")
    assert "Mining-Run: maury/run/2026-05-21T161234-aabbccdd" in body


# ---- format_finding_block (markdown for staging file) -------------------


def test_finding_block_is_self_contained_markdown_section() -> None:
    block = format_finding_block(_finding(), run_id="rid")
    assert block.startswith("## maury: feedback — ")
    # Trailing blank line so subsequent appends don't run into each other.
    assert block.endswith("\n\n")


def test_finding_block_includes_evidence_as_blockquote() -> None:
    block = format_finding_block(_finding(evidence="literal quote here"), run_id="rid")
    assert "> literal quote here" in block


def test_finding_block_includes_metadata_bullets() -> None:
    block = format_finding_block(
        _finding(kind="preference", scope_hint="personal", confidence="medium"),
        run_id="rid-1",
        crossref_state="PRESENT_BUT_UNCLEAR",
    )
    assert "- kind: preference" in block
    assert "- scope hint: personal" in block
    assert "- confidence: medium" in block
    assert "- crossref state: PRESENT_BUT_UNCLEAR" in block
    assert "- run: maury/run/rid-1" in block


def test_staging_file_constant() -> None:
    assert STAGING_FILE == "mining-findings.md"
