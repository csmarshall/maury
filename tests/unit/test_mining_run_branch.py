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


def test_commit_body_omits_source_mode_when_not_supplied() -> None:
    """Backward-compat: pre-ADR-0026 commits carry no Source-Mode."""
    body = format_commit_body(_finding(), run_id="rid")
    assert "Source-Mode:" not in body


def test_commit_body_includes_source_mode_when_supplied() -> None:
    body = format_commit_body(_finding(), run_id="rid", source_mode="personal:consulting:acme")
    assert "Source-Mode: personal:consulting:acme" in body
    # Ordered before Source-Transcript (a finding-context trailer).
    assert body.index("Source-Mode:") < body.index("Source-Transcript:")


def test_finding_block_includes_source_mode_bullet_when_supplied() -> None:
    block = format_finding_block(_finding(), run_id="rid", source_mode="work:acme-client")
    assert "- source mode: work:acme-client" in block


def test_finding_block_omits_source_mode_bullet_when_absent() -> None:
    block = format_finding_block(_finding(), run_id="rid")
    assert "source mode:" not in block


def test_finding_block_includes_proposed_rewrite_when_supplied() -> None:
    block = format_finding_block(_finding(), run_id="rid", proposed_rewrite="Answer in 1-3 sentences.\nNo preamble.")
    assert "proposed rewrite" in block
    assert "> Answer in 1-3 sentences." in block
    assert "> No preamble." in block


def test_finding_block_omits_rewrite_section_when_absent() -> None:
    block = format_finding_block(_finding(), run_id="rid")
    assert "proposed rewrite" not in block


def test_finding_block_omits_rewrite_section_when_blank() -> None:
    block = format_finding_block(_finding(), run_id="rid", proposed_rewrite="   ")
    assert "proposed rewrite" not in block


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


# ---- git layer (slice 2 + 3) -------------------------------------------

import json  # noqa: E402
import subprocess  # noqa: E402

import pytest  # noqa: E402

from maury.mining.run_branch import (  # noqa: E402
    FindingKeys,
    RunBranchError,
    existing_content_hashes,
    existing_finding_keys,
    write_run_branch,
)


def _git_available() -> bool:
    try:
        return subprocess.run(["git", "--version"], capture_output=True, check=False).returncode == 0
    except OSError:
        return False


def _run(args: list[str], cwd: Path) -> None:
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


def _seed_repo(tmp_path: Path) -> Path:
    """Initialize a git repo with one commit on main and a local
    user.email/user.name (required for `git commit` on CI runners)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(["git", "init", "-q", "-b", "main"], cwd=repo)
    _run(["git", "config", "user.email", "t@t.invalid"], cwd=repo)
    _run(["git", "config", "user.name", "t"], cwd=repo)
    (repo / "README.md").write_text("# repo\n")
    _run(["git", "add", "README.md"], cwd=repo)
    _run(["git", "commit", "-q", "-m", "initial"], cwd=repo)
    return repo


# ---- existing_content_hashes -------------------------------------------


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_existing_content_hashes_empty_when_no_findings(tmp_path: Path) -> None:
    repo = _seed_repo(tmp_path)
    assert existing_content_hashes(repo) == set()


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_existing_content_hashes_picks_up_accepted_hashes(tmp_path: Path) -> None:
    """A commit with a `Content-Hash:` trailer contributes its hash."""
    repo = _seed_repo(tmp_path)
    (repo / "f.md").write_text("hi\n")
    _run(["git", "add", "f.md"], cwd=repo)
    subprocess.run(
        ["git", "commit", "-m", "subject\n\nbody\n\nContent-Hash: aaa111\n", "--cleanup=verbatim"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    assert "aaa111" in existing_content_hashes(repo)


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_existing_content_hashes_picks_up_rejected_hashes(tmp_path: Path) -> None:
    """The rejection no-op metadata commit's `Rejected-Content-Hash:`
    trailers contribute too — sub-second dedup across years."""
    repo = _seed_repo(tmp_path)
    subprocess.run(
        [
            "git",
            "commit",
            "--allow-empty",
            "-m",
            "maury: rejected during curator review\n\nRejected-Content-Hash: bbb222\nRejected-Content-Hash: ccc333\n",
            "--cleanup=verbatim",
        ],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    hashes = existing_content_hashes(repo)
    assert "bbb222" in hashes
    assert "ccc333" in hashes


# ---- write_run_branch: preconditions -----------------------------------


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_write_run_branch_refuses_outside_git_repo(tmp_path: Path) -> None:
    with pytest.raises(RunBranchError, match="not a git working tree"):
        write_run_branch(repo_dir=tmp_path, findings=[_finding()], host_hex="aabbccdd")


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_write_run_branch_refuses_dirty_worktree(tmp_path: Path) -> None:
    repo = _seed_repo(tmp_path)
    (repo / "dirty.txt").write_text("uncommitted\n")
    with pytest.raises(RunBranchError, match="uncommitted changes"):
        write_run_branch(repo_dir=repo, findings=[_finding()], host_hex="aabbccdd")


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_write_run_branch_refuses_when_branch_already_exists(tmp_path: Path) -> None:
    repo = _seed_repo(tmp_path)
    # Pre-create the branch with the exact run-id we'll generate.
    when = datetime(2026, 5, 21, 16, 0, 0, tzinfo=UTC)
    rid = "2026-05-21T160000-aabbccdd"
    _run(["git", "branch", f"maury/run/{rid}"], cwd=repo)
    with pytest.raises(RunBranchError, match="already exists"):
        write_run_branch(repo_dir=repo, findings=[_finding()], host_hex="aabbccdd", when=when)


# ---- write_run_branch: happy paths -------------------------------------


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_write_run_branch_creates_branch_and_one_commit_per_finding(tmp_path: Path) -> None:
    repo = _seed_repo(tmp_path)
    findings = [
        _finding(kind="feedback", text="prefer terse responses"),
        _finding(kind="preference", text="use Python 3.11+"),
    ]
    result = write_run_branch(repo_dir=repo, findings=findings, host_hex="aabbccdd")
    assert result.commits_written == 2
    assert result.skipped_dedup == 0
    assert result.branch == f"maury/run/{result.run_id}"

    # Branch exists and is checked out.
    rc, out = (
        subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        ).returncode,
        subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        ).stdout,
    )
    assert rc == 0
    assert out.strip() == result.branch

    # Two commits ahead of main.
    proc = subprocess.run(
        ["git", "rev-list", "--count", f"main..{result.branch}"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.stdout.strip() == "2"

    # Staging file present with expected blocks.
    staging = (repo / "mining-findings.md").read_text()
    assert "prefer terse responses" in staging
    assert "use Python 3.11+" in staging


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_write_run_branch_commit_trailers_are_grep_indexable(tmp_path: Path) -> None:
    """The whole point of the trailer format: `git log --grep` finds them."""
    repo = _seed_repo(tmp_path)
    write_run_branch(repo_dir=repo, findings=[_finding()], host_hex="aabbccdd")
    # The new commit should be findable by Content-Hash grep.
    proc = subprocess.run(
        ["git", "log", "--all", "--grep=^Content-Hash:"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    assert "Content-Hash:" in proc.stdout
    expected = content_hash(kind="feedback", scope_hint="base", text="prefer terse responses")
    assert expected in proc.stdout


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_write_run_branch_skips_findings_with_seen_content_hash(tmp_path: Path) -> None:
    """A finding whose Content-Hash already lives in history is dedup-suppressed."""
    repo = _seed_repo(tmp_path)
    # First run lands two findings on main (we simulate by merging the branch).
    f1 = _finding(kind="feedback", text="prefer terse responses")
    f2 = _finding(kind="preference", text="use Python 3.11+")
    first = write_run_branch(repo_dir=repo, findings=[f1, f2], host_hex="aabbccdd")
    # Merge the run branch into main so its Content-Hash trailers are visible.
    _run(["git", "checkout", "main"], cwd=repo)
    _run(["git", "merge", "--no-ff", "-m", "merge first run", first.branch], cwd=repo)

    # Second run with one repeat + one new. Use a later timestamp so
    # run-ids don't collide.
    f1_again = _finding(kind="feedback", text="Prefer terse responses.")  # same idea, different wording
    f3 = _finding(kind="decision", text="adopt monorepo layout")
    when = datetime(2026, 5, 21, 17, 0, 0, tzinfo=UTC)
    second = write_run_branch(
        repo_dir=repo,
        findings=[f1_again, f3],
        host_hex="aabbccdd",
        when=when,
    )
    assert second.commits_written == 1
    assert second.skipped_dedup == 1
    # The skipped hash is f1's content hash.
    expected = content_hash(kind="feedback", scope_hint="base", text="prefer terse responses")
    assert expected in second.skipped_hashes


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_dedup_is_mode_scoped_same_idea_different_mode_not_suppressed(tmp_path: Path) -> None:
    """Per ADR-0026: the same finding mined under a different mode is a
    distinct proposal and must NOT be dedup-suppressed."""
    repo = _seed_repo(tmp_path)
    f = _finding(kind="feedback", text="prefer terse responses")
    first = write_run_branch(repo_dir=repo, findings=[f], host_hex="aabbccdd", source_mode="work")
    _run(["git", "checkout", "main"], cwd=repo)
    _run(["git", "merge", "--no-ff", "-m", "merge work run", first.branch], cwd=repo)

    # Same idea, different mode → not suppressed.
    when = datetime(2026, 5, 21, 17, 0, 0, tzinfo=UTC)
    second = write_run_branch(repo_dir=repo, findings=[f], host_hex="aabbccdd", source_mode="personal", when=when)
    assert second.commits_written == 1
    assert second.skipped_dedup == 0


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_dedup_is_mode_scoped_same_idea_same_mode_suppressed(tmp_path: Path) -> None:
    repo = _seed_repo(tmp_path)
    f = _finding(kind="feedback", text="prefer terse responses")
    first = write_run_branch(repo_dir=repo, findings=[f], host_hex="aabbccdd", source_mode="work")
    _run(["git", "checkout", "main"], cwd=repo)
    _run(["git", "merge", "--no-ff", "-m", "merge work run", first.branch], cwd=repo)

    when = datetime(2026, 5, 21, 17, 0, 0, tzinfo=UTC)
    second = write_run_branch(repo_dir=repo, findings=[f], host_hex="aabbccdd", source_mode="work", when=when)
    assert second.commits_written == 0
    assert second.skipped_dedup == 1


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_dedup_legacy_modeless_commit_is_wildcard(tmp_path: Path) -> None:
    """A commit predating the Source-Mode trailer (hash, no mode) dedups
    a new finding of ANY mode — ADR-0026 'treat as wildcard'."""
    repo = _seed_repo(tmp_path)
    f = _finding(kind="feedback", text="prefer terse responses")
    # First run with no source_mode (legacy style).
    first = write_run_branch(repo_dir=repo, findings=[f], host_hex="aabbccdd")
    _run(["git", "checkout", "main"], cwd=repo)
    _run(["git", "merge", "--no-ff", "-m", "merge legacy run", first.branch], cwd=repo)

    when = datetime(2026, 5, 21, 17, 0, 0, tzinfo=UTC)
    second = write_run_branch(repo_dir=repo, findings=[f], host_hex="aabbccdd", source_mode="work", when=when)
    assert second.commits_written == 0
    assert second.skipped_dedup == 1


def test_finding_keys_contains_logic() -> None:
    """Unit-level FindingKeys.contains matrix."""
    keys = FindingKeys(
        wildcard_hashes=frozenset({"wild"}),
        mode_pairs=frozenset({("h1", "work"), ("h1", "personal")}),
    )
    # wildcard hash matches any mode (or none).
    assert keys.contains(content_hash="wild", source_mode="anything") is True
    assert keys.contains(content_hash="wild", source_mode=None) is True
    # exact pair matches.
    assert keys.contains(content_hash="h1", source_mode="work") is True
    # same hash, unseen mode → not a dup.
    assert keys.contains(content_hash="h1", source_mode="globex") is False
    # modeless new finding dedups against any prior occurrence of the hash.
    assert keys.contains(content_hash="h1", source_mode=None) is True
    # unknown hash → not a dup.
    assert keys.contains(content_hash="nope", source_mode="work") is False


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_existing_finding_keys_parses_rejection_source_modes(tmp_path: Path) -> None:
    """A rejection commit carrying Rejected-Source-Mode contributes a
    mode-scoped pair; a bare Rejected-Content-Hash is a wildcard."""
    repo = _seed_repo(tmp_path)
    subprocess.run(
        [
            "git",
            "commit",
            "--allow-empty",
            "-m",
            "maury: rejected during curator review\n\n"
            "Rejected-Content-Hash: paired\nRejected-Source-Mode: work\nRejected-Reason: x\n"
            "Rejected-Content-Hash: bare\nRejected-Reason: y\n",
            "--cleanup=verbatim",
        ],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    keys = existing_finding_keys(repo)
    assert ("paired", "work") in keys.mode_pairs
    assert "bare" in keys.wildcard_hashes


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_write_run_branch_no_branch_when_every_finding_dedup_suppressed(tmp_path: Path) -> None:
    """If dedup eats every finding, don't leave an empty branch behind."""
    repo = _seed_repo(tmp_path)
    f = _finding(kind="feedback", text="prefer terse")
    first = write_run_branch(repo_dir=repo, findings=[f], host_hex="aabbccdd")
    _run(["git", "checkout", "main"], cwd=repo)
    _run(["git", "merge", "--no-ff", "-m", "merge first", first.branch], cwd=repo)

    when = datetime(2026, 5, 21, 17, 0, 0, tzinfo=UTC)
    result = write_run_branch(repo_dir=repo, findings=[f], host_hex="aabbccdd", when=when)
    assert result.commits_written == 0
    assert result.skipped_dedup == 1
    # No new branch created.
    rc, _out = (
        subprocess.run(
            ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{result.branch}"],
            cwd=repo,
            capture_output=True,
            check=False,
        ).returncode,
        "",
    )
    assert rc != 0  # branch absent


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_write_run_branch_carries_source_mode_into_commit(tmp_path: Path) -> None:
    """Per ADR-0026: the Source-Mode trailer rides on each finding commit
    and is grep-indexable for `maury promote`."""
    repo = _seed_repo(tmp_path)
    write_run_branch(
        repo_dir=repo,
        findings=[_finding()],
        host_hex="aabbccdd",
        source_mode="work:acme-client",
    )
    proc = subprocess.run(
        ["git", "log", "--all", "--grep=^Source-Mode: work:acme-client"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert "Source-Mode: work:acme-client" in proc.stdout


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_write_run_branch_omits_source_mode_when_none(tmp_path: Path) -> None:
    repo = _seed_repo(tmp_path)
    write_run_branch(repo_dir=repo, findings=[_finding()], host_hex="aabbccdd")
    proc = subprocess.run(
        ["git", "log", "--all", "--format=%b"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert "Source-Mode:" not in proc.stdout


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_write_run_branch_threads_rewrite_by_index(tmp_path: Path) -> None:
    """Phase 8: the proposed rewrite for finding idx N lands in N's block."""
    repo = _seed_repo(tmp_path)
    findings = [
        _finding(kind="feedback", text="first finding"),
        _finding(kind="preference", text="second finding"),
    ]
    write_run_branch(
        repo_dir=repo,
        findings=findings,
        host_hex="aabbccdd",
        rewrites={1: "STRENGTHENED wording for the second"},
    )
    staging = (repo / "mining-findings.md").read_text()
    assert "proposed rewrite" in staging
    assert "STRENGTHENED wording for the second" in staging
    # The rewrite is attached to the second finding's section, not the first.
    first_section = staging.split("second finding")[0]
    assert "proposed rewrite" not in first_section


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_write_run_branch_carries_crossref_state_into_commit(tmp_path: Path) -> None:
    repo = _seed_repo(tmp_path)
    write_run_branch(
        repo_dir=repo,
        findings=[_finding()],
        host_hex="aabbccdd",
        crossref_states={0: "PRESENT_AND_REINFORCED"},
    )
    proc = subprocess.run(
        ["git", "log", "--all", "--grep=^Crossref-State: PRESENT_AND_REINFORCED"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert "PRESENT_AND_REINFORCED" in proc.stdout


# Silence unused-import warnings in environments where the file imports
# are flagged by linters scanning the test file in isolation.
_unused_for_lint = (json,)
