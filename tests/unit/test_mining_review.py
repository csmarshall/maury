"""Tests for `maury.mining.review` — Phase 7 / ADR-0022 pure-logic slice
(review-branch naming, rejection-commit body, stale-base predicate,
trailer parsing).

The git layer (`review_run`, `rebase_run`) and the interactive CLI are
tested separately in later slices.
"""

from __future__ import annotations

from maury.mining.review import (
    NO_REASON_SENTINEL,
    REJECTION_COMMIT_SUBJECT,
    RejectedFinding,
    content_hashes_in_log,
    format_rejection_commit_body,
    is_stale_base,
    parse_trailer_values,
    review_branch_name_for,
)

# ---- review_branch_name_for ---------------------------------------------


def test_review_branch_name_for() -> None:
    assert review_branch_name_for("2026-05-21T161234-24b2a0aa") == "maury/review/2026-05-21T161234-24b2a0aa"


def test_review_branch_name_distinct_from_run_branch() -> None:
    """The review branch must not collide with the run branch namespace."""
    from maury.mining.run_branch import branch_name_for

    rid = "rid-1"
    assert review_branch_name_for(rid) != branch_name_for(rid)
    assert review_branch_name_for(rid).startswith("maury/review/")
    assert branch_name_for(rid).startswith("maury/run/")


# ---- format_rejection_commit_body ---------------------------------------


def test_rejection_body_has_header_trailers() -> None:
    body = format_rejection_commit_body(
        run_id="2026-05-21T161234-aabbccdd",
        curator_host="host_e3844a43",
        rejected=[RejectedFinding(content_hash="9a21d4f5", reason="too narrow")],
    )
    assert "Original-Run: maury/run/2026-05-21T161234-aabbccdd" in body
    assert "Curator-Host: host_e3844a43" in body
    assert "Rejected-Count: 1" in body


def test_rejection_body_pairs_hash_with_reason() -> None:
    body = format_rejection_commit_body(
        run_id="rid",
        curator_host="h",
        rejected=[
            RejectedFinding(content_hash="aaa", reason="reason one"),
            RejectedFinding(content_hash="bbb", reason="reason two"),
        ],
    )
    lines = body.splitlines()
    # Each Rejected-Content-Hash line is immediately followed by its reason.
    idx_a = lines.index("Rejected-Content-Hash: aaa")
    assert lines[idx_a + 1] == "Rejected-Reason: reason one"
    idx_b = lines.index("Rejected-Content-Hash: bbb")
    assert lines[idx_b + 1] == "Rejected-Reason: reason two"
    assert "Rejected-Count: 2" in body


def test_rejection_body_blank_reason_becomes_sentinel() -> None:
    body = format_rejection_commit_body(
        run_id="rid",
        curator_host="h",
        rejected=[
            RejectedFinding(content_hash="aaa", reason=""),
            RejectedFinding(content_hash="bbb", reason="   "),
        ],
    )
    assert f"Rejected-Reason: {NO_REASON_SENTINEL}" in body
    # Both reasons collapsed to the sentinel (no stray empty-value lines).
    assert body.count("Rejected-Reason:") == 2
    assert "Rejected-Reason: \n" not in body


def test_rejection_body_default_reason_is_empty_string() -> None:
    """RejectedFinding.reason defaults to '' → sentinel in the body."""
    body = format_rejection_commit_body(
        run_id="rid",
        curator_host="h",
        rejected=[RejectedFinding(content_hash="aaa")],
    )
    assert f"Rejected-Reason: {NO_REASON_SENTINEL}" in body


def test_rejection_body_trailers_are_grep_indexable() -> None:
    """`git log --grep="^Rejected-Content-Hash:"` must match each line —
    so every hash sits at the start of its own line (no padding)."""
    body = format_rejection_commit_body(
        run_id="rid",
        curator_host="h",
        rejected=[RejectedFinding(content_hash="deadbeef", reason="x")],
    )
    assert "\nRejected-Content-Hash: deadbeef" in f"\n{body}"
    # Single space after the colon — matches run_branch's RFC 822 form.
    assert "Rejected-Content-Hash:  " not in body
    assert "Rejected-Content-Hash:deadbeef" not in body


def test_rejection_body_empty_list_is_header_only() -> None:
    body = format_rejection_commit_body(run_id="rid", curator_host="h", rejected=[])
    assert "Rejected-Count: 0" in body
    assert "Rejected-Content-Hash:" not in body


def test_rejection_commit_subject_is_stable() -> None:
    assert REJECTION_COMMIT_SUBJECT == "maury: rejected during curator review"


def test_rejection_body_roundtrips_through_parser() -> None:
    """The body we emit must be parseable by our own trailer parser —
    this is what the next mining run's dedup scan relies on."""
    body = format_rejection_commit_body(
        run_id="rid",
        curator_host="h",
        rejected=[
            RejectedFinding(content_hash="h1", reason="r1"),
            RejectedFinding(content_hash="h2", reason="r2"),
        ],
    )
    hashes = parse_trailer_values(body, "Rejected-Content-Hash")
    assert hashes == ["h1", "h2"]
    reasons = parse_trailer_values(body, "Rejected-Reason")
    assert reasons == ["r1", "r2"]


# ---- is_stale_base ------------------------------------------------------


def test_is_stale_base_false_when_main_unmoved() -> None:
    assert is_stale_base(main_sha="abc123", merge_base_sha="abc123") is False


def test_is_stale_base_true_when_main_moved() -> None:
    assert is_stale_base(main_sha="def456", merge_base_sha="abc123") is True


def test_is_stale_base_tolerates_whitespace() -> None:
    """`git rev-parse` output carries a trailing newline."""
    assert is_stale_base(main_sha="abc123\n", merge_base_sha="  abc123  ") is False


# ---- parse_trailer_values / content_hashes_in_log -----------------------


def test_parse_trailer_values_extracts_all_matches() -> None:
    message = "subject\n\nbody\n\nContent-Hash: aaa\nContent-Hash: bbb\n"
    assert parse_trailer_values(message, "Content-Hash") == ["aaa", "bbb"]


def test_parse_trailer_values_tolerates_log_indentation() -> None:
    """`git log` indents commit bodies by four spaces; the parser must
    still match."""
    message = "    commit subject\n\n    Content-Hash: indented\n"
    assert parse_trailer_values(message, "Content-Hash") == ["indented"]


def test_parse_trailer_values_skips_blank_values() -> None:
    message = "Content-Hash: \nContent-Hash: real\n"
    assert parse_trailer_values(message, "Content-Hash") == ["real"]


def test_parse_trailer_values_no_match_returns_empty() -> None:
    assert parse_trailer_values("nothing here\n", "Content-Hash") == []


def test_parse_trailer_values_does_not_match_substring_keys() -> None:
    """`Rejected-Content-Hash:` must not be picked up when asking for
    `Content-Hash:` — the prefix is anchored at line start after strip."""
    message = "Rejected-Content-Hash: rejected_one\nContent-Hash: accepted_one\n"
    assert parse_trailer_values(message, "Content-Hash") == ["accepted_one"]


def test_content_hashes_in_log_returns_set() -> None:
    log_text = "commit 1\n\n    Content-Hash: aaa\n\ncommit 2\n\n    Content-Hash: bbb\n    Content-Hash: aaa\n"
    assert content_hashes_in_log(log_text) == {"aaa", "bbb"}


def test_content_hashes_in_log_empty() -> None:
    assert content_hashes_in_log("no trailers here\n") == set()
