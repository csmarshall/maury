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


def test_rejection_body_includes_source_mode_when_set() -> None:
    """Per ADR-0026: rejection memory is mode-scoped — the rejected
    finding's Source-Mode rides in the commit between hash and reason."""
    body = format_rejection_commit_body(
        run_id="rid",
        curator_host="h",
        rejected=[RejectedFinding(content_hash="aaa", source_mode="work:acme-client", reason="nope")],
    )
    lines = body.splitlines()
    idx = lines.index("Rejected-Content-Hash: aaa")
    assert lines[idx + 1] == "Rejected-Source-Mode: work:acme-client"
    assert lines[idx + 2] == "Rejected-Reason: nope"


def test_rejection_body_omits_source_mode_when_blank() -> None:
    body = format_rejection_commit_body(
        run_id="rid",
        curator_host="h",
        rejected=[RejectedFinding(content_hash="aaa", reason="nope")],
    )
    assert "Rejected-Source-Mode:" not in body


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


# ---- git layer (slice 2) -----------------------------------------------

import subprocess  # noqa: E402
from datetime import datetime  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

from maury.mining.extractor import ExtractionWindow, Finding  # noqa: E402
from maury.mining.review import (  # noqa: E402
    Decision,
    DecisionKind,
    DecisionProvider,
    RebaseRunResult,
    ReviewError,
    rebase_run,
    review_run,
)
from maury.mining.run_branch import write_run_branch  # noqa: E402
from maury.mining.transcripts import TranscriptMessage  # noqa: E402


def _git_available() -> bool:
    try:
        return subprocess.run(["git", "--version"], capture_output=True, check=False).returncode == 0
    except OSError:
        return False


def _run(args: list[str], cwd: Path) -> str:
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout


def _seed_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(["git", "init", "-q", "-b", "main"], cwd=repo)
    _run(["git", "config", "user.email", "t@t.invalid"], cwd=repo)
    _run(["git", "config", "user.name", "t"], cwd=repo)
    (repo / "README.md").write_text("# repo\n")
    _run(["git", "add", "README.md"], cwd=repo)
    _run(["git", "commit", "-q", "-m", "initial"], cwd=repo)
    return repo


def _gw_finding(text: str, *, kind: str = "feedback", scope_hint: str = "base") -> Finding:
    msg = TranscriptMessage(
        project="p",
        transcript_path=Path("/tmp/p/s.jsonl"),
        session_id="s",
        timestamp="2026-05-21T16:00:00Z",
        text=text,
    )
    return Finding(
        kind=kind,
        scope_hint=scope_hint,
        text=text,
        evidence=f"evidence for {text}",
        confidence="high",
        source_window=ExtractionWindow(project="p", index=0, messages=(msg,)),
    )


def _mine(repo: Path, findings: list[Finding], *, when: datetime | None = None, source_mode: str | None = None) -> str:
    """Write a run branch; return its run_id. Leaves repo on main."""
    result = write_run_branch(repo_dir=repo, findings=findings, host_hex="aabbccdd", when=when, source_mode=source_mode)
    _run(["git", "checkout", "-q", "main"], cwd=repo)
    return result.run_id


def _scripted(decisions: list[Decision]) -> DecisionProvider:
    """Return decisions in walk order; raises if the walk asks for more."""
    it = iter(decisions)

    def provider(_view: object) -> Decision:
        return next(it)

    return provider


def _commit_count(repo: Path, rng: str) -> int:
    return int(_run(["git", "rev-list", "--count", rng], cwd=repo).strip())


_skip = pytest.mark.skipif(not _git_available(), reason="git not on PATH")


# ---- review_run: preconditions -----------------------------------------


@_skip
def test_review_run_refuses_missing_run_branch(tmp_path: Path) -> None:
    repo = _seed_repo(tmp_path)
    with pytest.raises(ReviewError, match="does not exist"):
        review_run(
            repo_dir=repo,
            run_id="2026-05-21T160000-aabbccdd",
            curator_host="h",
            decide=_scripted([]),
        )


@_skip
def test_review_run_refuses_dirty_worktree(tmp_path: Path) -> None:
    repo = _seed_repo(tmp_path)
    rid = _mine(repo, [_gw_finding("prefer terse")])
    (repo / "dirty.txt").write_text("x\n")
    with pytest.raises(ReviewError, match="uncommitted changes"):
        review_run(repo_dir=repo, run_id=rid, curator_host="h", decide=_scripted([]))


@_skip
def test_review_run_refuses_stale_base(tmp_path: Path) -> None:
    repo = _seed_repo(tmp_path)
    rid = _mine(repo, [_gw_finding("prefer terse")])
    # Advance main after mining → stale base.
    (repo / "other.txt").write_text("moved\n")
    _run(["git", "add", "other.txt"], cwd=repo)
    _run(["git", "commit", "-q", "-m", "advance main"], cwd=repo)
    with pytest.raises(ReviewError, match="has moved"):
        review_run(repo_dir=repo, run_id=rid, curator_host="h", decide=_scripted([]))


# ---- review_run: accept / reject / skip --------------------------------


@_skip
def test_review_run_accept_all(tmp_path: Path) -> None:
    repo = _seed_repo(tmp_path)
    rid = _mine(repo, [_gw_finding("prefer terse"), _gw_finding("use python 3.11", kind="preference")])
    result = review_run(
        repo_dir=repo,
        run_id=rid,
        curator_host="h",
        decide=_scripted([Decision(DecisionKind.ACCEPT), Decision(DecisionKind.ACCEPT)]),
    )
    assert result.accepted == 2
    assert result.rejected == 0
    assert result.rejection_commit_written is False
    # Two finding commits on the review branch.
    assert _commit_count(repo, f"main..{result.review_branch}") == 2
    # Staging file carries both blocks; HEAD is the review branch.
    staging = (repo / "mining-findings.md").read_text()
    assert "prefer terse" in staging
    assert "use python 3.11" in staging
    assert _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo).strip() == result.review_branch


@_skip
def test_review_run_accept_preserves_content_hash_trailer(tmp_path: Path) -> None:
    repo = _seed_repo(tmp_path)
    rid = _mine(repo, [_gw_finding("prefer terse")])
    result = review_run(repo_dir=repo, run_id=rid, curator_host="h", decide=_scripted([Decision(DecisionKind.ACCEPT)]))
    log = _run(["git", "log", f"main..{result.review_branch}", "--format=%b"], cwd=repo)
    assert "Content-Hash:" in log


@_skip
def test_review_run_reject_writes_noop_commit(tmp_path: Path) -> None:
    repo = _seed_repo(tmp_path)
    rid = _mine(repo, [_gw_finding("prefer terse"), _gw_finding("noise", kind="preference")])
    result = review_run(
        repo_dir=repo,
        run_id=rid,
        curator_host="host_x",
        decide=_scripted([Decision(DecisionKind.ACCEPT), Decision(DecisionKind.REJECT, reason="too narrow")]),
    )
    assert result.accepted == 1
    assert result.rejected == 1
    assert result.rejection_commit_written is True
    # review branch = 1 finding commit + 1 rejection commit.
    assert _commit_count(repo, f"main..{result.review_branch}") == 2
    log = _run(["git", "log", f"main..{result.review_branch}"], cwd=repo)
    assert "Rejected-Content-Hash:" in log
    assert "too narrow" in log
    assert "rejected during curator review" in log


@_skip
def test_review_run_reject_carries_source_mode_from_finding(tmp_path: Path) -> None:
    """A finding mined with a Source-Mode trailer rejects with a matching
    Rejected-Source-Mode in the no-op commit (ADR-0026)."""
    repo = _seed_repo(tmp_path)
    rid = _mine(repo, [_gw_finding("noise here")], source_mode="work:acme-client")
    result = review_run(
        repo_dir=repo,
        run_id=rid,
        curator_host="h",
        decide=_scripted([Decision(DecisionKind.REJECT, reason="not universal")]),
    )
    assert result.rejected == 1
    log = _run(["git", "log", f"main..{result.review_branch}"], cwd=repo)
    assert "Rejected-Source-Mode: work:acme-client" in log


@_skip
def test_review_run_reject_only_no_finding_commits(tmp_path: Path) -> None:
    repo = _seed_repo(tmp_path)
    rid = _mine(repo, [_gw_finding("noise one"), _gw_finding("noise two", kind="preference")])
    result = review_run(
        repo_dir=repo,
        run_id=rid,
        curator_host="h",
        decide=_scripted([Decision(DecisionKind.REJECT), Decision(DecisionKind.REJECT, reason="nope")]),
    )
    assert result.accepted == 0
    assert result.rejected == 2
    # Only the single rejection commit.
    assert _commit_count(repo, f"main..{result.review_branch}") == 1


@_skip
def test_review_run_skip_first_accept_second_is_gap_safe(tmp_path: Path) -> None:
    """The reconstruct-and-append model must not conflict when an earlier
    finding is skipped and a later one accepted."""
    repo = _seed_repo(tmp_path)
    rid = _mine(repo, [_gw_finding("first finding"), _gw_finding("second finding", kind="preference")])
    result = review_run(
        repo_dir=repo,
        run_id=rid,
        curator_host="h",
        decide=_scripted([Decision(DecisionKind.SKIP), Decision(DecisionKind.ACCEPT)]),
    )
    assert result.skipped == 1
    assert result.accepted == 1
    staging = (repo / "mining-findings.md").read_text()
    assert "second finding" in staging
    assert "first finding" not in staging


# ---- review_run: edit --------------------------------------------------


@_skip
def test_review_run_edit_applies_edited_content_keeps_hash(tmp_path: Path) -> None:
    repo = _seed_repo(tmp_path)
    rid = _mine(repo, [_gw_finding("original wording")])

    def editor(path: Path) -> None:
        path.write_text("## edited block\n\nrewritten by the curator\n\n")

    result = review_run(
        repo_dir=repo,
        run_id=rid,
        curator_host="h",
        decide=_scripted([Decision(DecisionKind.EDIT)]),
        editor=editor,
    )
    assert result.edited == 1
    staging = (repo / "mining-findings.md").read_text()
    assert "rewritten by the curator" in staging
    assert "original wording" not in staging
    # Content-Hash trailer is preserved from the original commit message.
    log = _run(["git", "log", f"main..{result.review_branch}", "--format=%b"], cwd=repo)
    assert "Content-Hash:" in log


# ---- review_run: quit --------------------------------------------------


@_skip
def test_review_run_quit_persists_accepts_discards_pending_rejections(tmp_path: Path) -> None:
    repo = _seed_repo(tmp_path)
    rid = _mine(
        repo,
        [
            _gw_finding("keep me"),
            _gw_finding("reject me", kind="preference"),
            _gw_finding("never seen", kind="decision"),
        ],
    )
    result = review_run(
        repo_dir=repo,
        run_id=rid,
        curator_host="h",
        decide=_scripted(
            [
                Decision(DecisionKind.ACCEPT),
                Decision(DecisionKind.QUIT),  # quit before deciding the rest
            ]
        ),
    )
    assert result.quit_early is True
    assert result.accepted == 1
    assert result.rejected == 0
    assert result.rejection_commit_written is False
    # Only the accepted finding's commit landed.
    assert _commit_count(repo, f"main..{result.review_branch}") == 1


# ---- review_run: resume ------------------------------------------------


@_skip
def test_review_run_resume_skips_already_applied(tmp_path: Path) -> None:
    repo = _seed_repo(tmp_path)
    rid = _mine(repo, [_gw_finding("first"), _gw_finding("second", kind="preference")])

    # First pass: accept the first, quit before the second.
    first = review_run(
        repo_dir=repo,
        run_id=rid,
        curator_host="h",
        decide=_scripted([Decision(DecisionKind.ACCEPT), Decision(DecisionKind.QUIT)]),
    )
    assert first.accepted == 1

    # Resume: the already-applied first finding is skipped without prompting;
    # only the second is presented.
    second = review_run(
        repo_dir=repo,
        run_id=rid,
        curator_host="h",
        decide=_scripted([Decision(DecisionKind.ACCEPT)]),
    )
    assert second.resumed_skipped == 1
    assert second.accepted == 1
    assert _commit_count(repo, f"main..{second.review_branch}") == 2


# ---- rebase_run --------------------------------------------------------


@_skip
def test_rebase_run_noop_when_main_unmoved(tmp_path: Path) -> None:
    repo = _seed_repo(tmp_path)
    rid = _mine(repo, [_gw_finding("x")])
    result = rebase_run(repo_dir=repo, run_id=rid)
    assert isinstance(result, RebaseRunResult)
    assert result.rebased is False


@_skip
def test_rebase_run_rebases_when_main_moved_then_review_works(tmp_path: Path) -> None:
    repo = _seed_repo(tmp_path)
    rid = _mine(repo, [_gw_finding("x")])
    # Advance main (a non-conflicting file).
    (repo / "other.txt").write_text("moved\n")
    _run(["git", "add", "other.txt"], cwd=repo)
    _run(["git", "commit", "-q", "-m", "advance main"], cwd=repo)

    rebased = rebase_run(repo_dir=repo, run_id=rid)
    assert rebased.rebased is True

    # After rebase, review no longer refuses on stale base.
    _run(["git", "checkout", "-q", "main"], cwd=repo)
    result = review_run(repo_dir=repo, run_id=rid, curator_host="h", decide=_scripted([Decision(DecisionKind.ACCEPT)]))
    assert result.accepted == 1


# ---- ADR-0053: classify-on-accept auto-placement ------------------------

from maury.manifest import Manifest, ProfileSpec  # noqa: E402
from maury.rules.schema import Rule, RuleActions, RuleConditions  # noqa: E402


def _placement_manifest() -> Manifest:
    return Manifest(
        version=1,
        profiles={
            "p_base": ProfileSpec(name="base", extends=None),
            "p_work": ProfileSpec(name="work", extends="p_base"),
        },
        hosts={},
    )


@_skip
def test_review_accept_places_into_classified_fragment(tmp_path: Path) -> None:
    repo = _seed_repo(tmp_path)
    rid = _mine(repo, [_gw_finding("prefer terse responses")], source_mode="work")
    rules = [Rule(id="terse", when=RuleConditions(any_keyword=("terse",)), then=RuleActions(profile="work"))]

    result = review_run(
        repo_dir=repo,
        run_id=rid,
        curator_host="h",
        decide=_scripted([Decision(DecisionKind.ACCEPT)]),
        rules=rules,
        manifest=_placement_manifest(),
    )
    assert result.accepted == 1
    assert result.placed == 1
    # Landed in the work fragment, carrying provenance — NOT mining-findings.md.
    frag = repo / "profiles" / "work" / "CLAUDE.md.fragment"
    assert frag.is_file()
    text = frag.read_text()
    assert "prefer terse responses" in text
    assert "maury: placed from" in text
    assert not (repo / "mining-findings.md").exists()


@_skip
def test_review_accept_unmatched_falls_to_manual_queue(tmp_path: Path) -> None:
    repo = _seed_repo(tmp_path)
    rid = _mine(repo, [_gw_finding("something unrelated")], source_mode="work")
    # A rule that does NOT match the finding → profile=None → manual queue.
    rules = [Rule(id="nope", when=RuleConditions(any_keyword=("zzz-no-match",)), then=RuleActions(profile="work"))]

    result = review_run(
        repo_dir=repo,
        run_id=rid,
        curator_host="h",
        decide=_scripted([Decision(DecisionKind.ACCEPT)]),
        rules=rules,
        manifest=_placement_manifest(),
    )
    assert result.accepted == 1
    assert result.placed == 0
    assert "something unrelated" in (repo / "mining-findings.md").read_text()
    assert not (repo / "profiles" / "work" / "CLAUDE.md.fragment").exists()
