"""Tests for the cross-reference module (Phase 6c per ADR-0020)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from maury.mining.crossref import (
    CrossRefSummary,
    crossref_finding,
    crossref_findings,
    get_claude_md_at_timestamp,
)
from maury.mining.extractor import ExtractionWindow, Finding
from maury.mining.transcripts import TranscriptMessage

# ---- helpers ------------------------------------------------------------


def _msg(text: str = "x") -> TranscriptMessage:
    return TranscriptMessage(
        project="p",
        transcript_path=Path("/x.jsonl"),
        session_id="s",
        timestamp="2026-05-06T12:00:00Z",
        text=text,
    )


def _finding(*, kind: str = "workflow", scope: str = "base", text: str = "the rule") -> Finding:
    msgs = (_msg(),)
    win = ExtractionWindow(messages=msgs, project="p", index=0)
    return Finding(
        kind=kind,
        scope_hint=scope,
        text=text,
        evidence="some quote",
        confidence="high",
        source_window=win,
    )


class _StubLLM:
    name = "stub"

    def __init__(self, response: str):
        self.response = response
        self.calls: list[str] = []

    def call(self, prompt: str, *, timeout: float = 120.0) -> str:
        self.calls.append(prompt)
        return self.response


# ---- crossref_finding (single) ------------------------------------------


def test_crossref_parses_state_and_action() -> None:
    llm = _StubLLM(
        '{"state": "PRESENT_AND_REINFORCED", '
        '"claude_md_quote": "use ruff for linting", '
        '"rationale": "rule exists; transcript shows correction", '
        '"suggested_action": "investigate-why-not-followed"}'
    )
    result = crossref_finding(_finding(), llm=llm, current_claude_md="Use ruff for linting.\n")
    assert result.state == "PRESENT_AND_REINFORCED"
    assert "use ruff" in result.claude_md_quote.lower()
    assert "correction" in result.rationale
    assert result.suggested_action == "investigate-why-not-followed"


def test_crossref_includes_current_claude_md_in_prompt() -> None:
    llm = _StubLLM('{"state": "NEW", "claude_md_quote": "", "rationale": "x", "suggested_action": "propose-new"}')
    crossref_finding(_finding(), llm=llm, current_claude_md="THIS_IS_THE_CURRENT_MD")
    assert "THIS_IS_THE_CURRENT_MD" in llm.calls[0]


def test_crossref_prompt_distinguishes_with_vs_without_history() -> None:
    """The temporal-awareness header changes between modes."""
    llm = _StubLLM('{"state": "NEW", "claude_md_quote": "", "rationale": "x", "suggested_action": "propose-new"}')

    crossref_finding(_finding(), llm=llm, current_claude_md="C")
    no_history_prompt = llm.calls[-1]
    assert "NOT AVAILABLE" in no_history_prompt
    assert "Be conservative about REINFORCED" in no_history_prompt

    crossref_finding(_finding(), llm=llm, current_claude_md="C", historical_claude_md="OLD")
    with_history_prompt = llm.calls[-1]
    assert "AS IT WAS AT THE TRANSCRIPT TIMESTAMP" in with_history_prompt
    assert "OLD" in with_history_prompt
    assert "user was establishing the rule" in with_history_prompt


def test_crossref_handles_extra_prose_around_json() -> None:
    """LLM may wrap output in prose; we still pull out the JSON object."""
    llm = _StubLLM(
        "Here is my classification:\n"
        '{"state": "PRESENT_AND_CLEAR", "claude_md_quote": "x", "rationale": "y", '
        '"suggested_action": "suppress"}\n'
        "That's all."
    )
    result = crossref_finding(_finding(), llm=llm, current_claude_md="x")
    assert result.state == "PRESENT_AND_CLEAR"


def test_crossref_unparseable_response_falls_back_to_new() -> None:
    """If the LLM returns no JSON at all, default to NEW so nothing is silently suppressed."""
    llm = _StubLLM("I cannot parse this finding; sorry")
    result = crossref_finding(_finding(), llm=llm, current_claude_md="x")
    assert result.state == "NEW"
    assert "unparseable" in result.rationale
    assert result.suggested_action == "propose-new"


def test_crossref_invalid_json_falls_back_to_new() -> None:
    """If JSON is malformed, also fall back safely to NEW."""
    llm = _StubLLM('{"state": "NEW", "claude_md_quote": "x"')  # truncated
    result = crossref_finding(_finding(), llm=llm, current_claude_md="x")
    assert result.state == "NEW"
    assert "JSON invalid" in result.rationale or "unparseable" in result.rationale


def test_crossref_includes_finding_evidence_in_prompt() -> None:
    """Evidence quote and finding text should be present so the LLM can match them."""
    llm = _StubLLM('{"state": "NEW", "claude_md_quote": "", "rationale": "x", "suggested_action": "propose-new"}')
    f = _finding(text="USER_PREFERENCE_TEXT")
    crossref_finding(f, llm=llm, current_claude_md="x")
    p = llm.calls[0]
    assert "USER_PREFERENCE_TEXT" in p
    assert "some quote" in p  # the evidence string from _finding()


# ---- get_claude_md_at_timestamp -----------------------------------------


def test_temporal_lookup_returns_none_when_not_a_git_repo(tmp_path: Path) -> None:
    """Non-git dir -> None gracefully."""
    assert get_claude_md_at_timestamp(tmp_path, "CLAUDE.md", "2026-05-06T12:00:00Z") is None


def test_temporal_lookup_via_real_git(tmp_path: Path) -> None:
    """Smoke test against a real git repo on disk."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "T"], check=True)

    (repo / "CLAUDE.md").write_text("first version of the file\n")
    subprocess.run(["git", "-C", str(repo), "add", "CLAUDE.md"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "v1", "--date=2026-01-01T00:00:00Z"],
        env={
            "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z",
            "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
            "PATH": "/usr/bin:/bin:/usr/local/bin",
        },
        check=True,
    )

    (repo / "CLAUDE.md").write_text("second version with an added rule\n")
    subprocess.run(["git", "-C", str(repo), "add", "CLAUDE.md"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "v2", "--date=2026-06-01T00:00:00Z"],
        env={
            "GIT_AUTHOR_DATE": "2026-06-01T00:00:00Z",
            "GIT_COMMITTER_DATE": "2026-06-01T00:00:00Z",
            "PATH": "/usr/bin:/bin:/usr/local/bin",
        },
        check=True,
    )

    # Asking before v1 -> nothing
    out = get_claude_md_at_timestamp(repo, "CLAUDE.md", "2025-12-01T00:00:00Z")
    assert out is None

    # Asking between v1 and v2 -> v1
    out = get_claude_md_at_timestamp(repo, "CLAUDE.md", "2026-03-01T00:00:00Z")
    assert out is not None
    assert "first version" in out
    assert "added rule" not in out

    # Asking after v2 -> v2
    out = get_claude_md_at_timestamp(repo, "CLAUDE.md", "2026-12-01T00:00:00Z")
    assert out is not None
    assert "second version" in out


def test_temporal_lookup_returns_none_when_file_did_not_exist_at_commit(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@e.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "T"], check=True)
    (repo / "OTHER.md").write_text("not the file we'll ask for\n")
    subprocess.run(["git", "-C", str(repo), "add", "OTHER.md"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "x"], check=True)

    out = get_claude_md_at_timestamp(repo, "CLAUDE.md", "2030-01-01T00:00:00Z")
    assert out is None


def test_temporal_lookup_handles_git_command_failure_gracefully(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If git itself errors out, return None rather than raising."""
    (tmp_path / ".git").mkdir()  # pretend we're in a repo

    def boom(*a: object, **kw: object) -> None:
        raise subprocess.CalledProcessError(returncode=128, cmd=["git"])

    monkeypatch.setattr(subprocess, "run", boom)
    assert get_claude_md_at_timestamp(tmp_path, "CLAUDE.md", "2026-05-06T12:00:00Z") is None


# ---- crossref_findings (bulk) -------------------------------------------


def test_crossref_findings_buckets_by_state() -> None:
    findings = [_finding(text=t) for t in ["a", "b", "c"]]
    # Three different states across the three findings
    responses = iter(
        [
            '{"state": "NEW", "claude_md_quote": "", "rationale": "r", "suggested_action": "propose-new"}',
            '{"state": "PRESENT_AND_CLEAR", "claude_md_quote": "x", "rationale": "r", "suggested_action": "suppress"}',
            '{"state": "PRESENT_AND_REINFORCED", "claude_md_quote": "x", "rationale": "r", "suggested_action": "investigate-why-not-followed"}',
        ]
    )

    class _Multi:
        name = "multi"

        def call(self, prompt: str, *, timeout: float = 120.0) -> str:
            return next(responses)

    summary = crossref_findings(findings, llm=_Multi(), current_claude_md="X")
    assert summary.total() == 3
    assert len(summary.by_state["NEW"]) == 1
    assert len(summary.by_state["PRESENT_AND_CLEAR"]) == 1
    assert len(summary.by_state["PRESENT_AND_REINFORCED"]) == 1
    assert summary.by_state["PRESENT_BUT_UNCLEAR"] == []


def test_crossref_findings_calls_progress_callback() -> None:
    findings = [_finding(text="x"), _finding(text="y")]
    llm = _StubLLM('{"state": "NEW", "claude_md_quote": "", "rationale": "r", "suggested_action": "propose-new"}')
    seen: list[tuple[int, int]] = []

    def progress(i: int, total: int, finding: object, result: object) -> None:
        seen.append((i, total))

    crossref_findings(findings, llm=llm, current_claude_md="X", on_progress=progress)
    assert seen == [(1, 2), (2, 2)]


def test_crossref_findings_continues_past_failure() -> None:
    """One failed crossref call shouldn't kill the whole batch."""
    findings = [_finding(text="a"), _finding(text="b")]

    class _FailFirst:
        name = "f"

        def __init__(self) -> None:
            self.n = 0

        def call(self, prompt: str, *, timeout: float = 120.0) -> str:
            self.n += 1
            if self.n == 1:
                raise RuntimeError("network down")
            return '{"state": "NEW", "claude_md_quote": "", "rationale": "ok", "suggested_action": "propose-new"}'

    summary = crossref_findings(findings, llm=_FailFirst(), current_claude_md="X")
    assert summary.total() == 2
    # Both got bucketed; one had a "failed" rationale
    rationales = [r.rationale for bucket in summary.by_state.values() for _f, r in bucket]
    assert any("failed" in r for r in rationales)


def test_crossref_findings_uses_repo_for_temporal_lookup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When repo_path is given, temporal lookup is consulted per finding."""
    findings = [_finding(text="a")]
    llm = _StubLLM('{"state": "NEW", "claude_md_quote": "", "rationale": "r", "suggested_action": "propose-new"}')

    # Mock temporal lookup to return a known string
    called_with: list[tuple[Path, str, str]] = []

    def mock_temporal(repo: Path, rel: str, ts: str) -> str:
        called_with.append((repo, rel, ts))
        return "HISTORICAL_MD_CONTENT"

    monkeypatch.setattr("maury.mining.crossref.get_claude_md_at_timestamp", mock_temporal)
    crossref_findings(findings, llm=llm, current_claude_md="C", repo_path=tmp_path)

    assert called_with == [(tmp_path, "CLAUDE.md", "2026-05-06T12:00:00Z")]
    # And the prompt should contain the historical content
    assert "HISTORICAL_MD_CONTENT" in llm.calls[0]


def test_crossref_summary_empty_starts_with_all_buckets() -> None:
    s = CrossRefSummary.empty()
    assert set(s.by_state) == {"NEW", "PRESENT_AND_CLEAR", "PRESENT_BUT_UNCLEAR", "PRESENT_AND_REINFORCED"}
    assert s.total() == 0
