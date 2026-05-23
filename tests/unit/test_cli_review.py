"""CLI-layer tests for `maury review` + `maury rebase-run` (ADR-0022).

The review engine is tested in `test_mining_review.py`. These cover the
click wiring: argument/flag validation, the --accept-all / --reject-all
batch paths, the interactive prompt loop (driven via CliRunner stdin),
output messaging, and exit codes.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from maury.cli import main
from maury.mining.extractor import ExtractionWindow, Finding
from maury.mining.run_branch import write_run_branch
from maury.mining.transcripts import TranscriptMessage


def _git_available() -> bool:
    try:
        return subprocess.run(["git", "--version"], capture_output=True, check=False).returncode == 0
    except OSError:
        return False


_skip = pytest.mark.skipif(not _git_available(), reason="git not on PATH")


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


def _finding(text: str, *, kind: str = "feedback") -> Finding:
    msg = TranscriptMessage(
        project="p",
        transcript_path=Path("/tmp/p/s.jsonl"),
        session_id="s",
        timestamp="2026-05-21T16:00:00Z",
        text=text,
    )
    return Finding(
        kind=kind,
        scope_hint="base",
        text=text,
        evidence=f"evidence for {text}",
        confidence="high",
        source_window=ExtractionWindow(project="p", index=0, messages=(msg,)),
    )


def _mine(repo: Path, findings: list[Finding]) -> str:
    result = write_run_branch(repo_dir=repo, findings=findings, host_hex="aabbccdd")
    _run(["git", "checkout", "-q", "main"], cwd=repo)
    return result.run_id


def _commit_count(repo: Path, rng: str) -> int:
    return int(_run(["git", "rev-list", "--count", rng], cwd=repo).strip())


# ---- argument / flag validation ----------------------------------------


def test_review_rejects_accept_all_and_reject_all_together(tmp_path: Path) -> None:
    # Validation runs before any git op; tmp_path just needs to exist.
    result = CliRunner().invoke(main, ["review", "rid", "--repo", str(tmp_path), "--accept-all", "--reject-all"])
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


def test_review_rejects_reason_without_reject_all(tmp_path: Path) -> None:
    result = CliRunner().invoke(main, ["review", "rid", "--repo", str(tmp_path), "--reason", "because"])
    assert result.exit_code != 0
    assert "--reason only applies with --reject-all" in result.output


def test_review_requires_repo(tmp_path: Path) -> None:
    result = CliRunner().invoke(main, ["review", "some-run-id"])
    assert result.exit_code != 0
    assert "--repo" in result.output


# ---- batch paths -------------------------------------------------------


@_skip
def test_review_accept_all(tmp_path: Path) -> None:
    repo = _seed_repo(tmp_path)
    rid = _mine(repo, [_finding("prefer terse"), _finding("use py 3.11", kind="preference")])
    result = CliRunner().invoke(main, ["review", rid, "--repo", str(repo), "--accept-all"])
    assert result.exit_code == 0, result.output
    assert f"maury/review/{rid}" in result.output
    assert "accepted: 2" in result.output
    assert _commit_count(repo, f"main..maury/review/{rid}") == 2


@_skip
def test_review_reject_all_with_reason(tmp_path: Path) -> None:
    repo = _seed_repo(tmp_path)
    rid = _mine(repo, [_finding("noise one"), _finding("noise two", kind="preference")])
    result = CliRunner().invoke(main, ["review", rid, "--repo", str(repo), "--reject-all", "--reason", "all noise"])
    assert result.exit_code == 0, result.output
    assert "rejected: 2" in result.output
    # One trailing rejection commit, no finding commits.
    assert _commit_count(repo, f"main..maury/review/{rid}") == 1
    log = _run(["git", "log", f"main..maury/review/{rid}"], cwd=repo)
    assert "all noise" in log
    assert "Rejected-Content-Hash:" in log


# ---- interactive prompt loop -------------------------------------------


@_skip
def test_review_interactive_accept_skip_quit(tmp_path: Path) -> None:
    repo = _seed_repo(tmp_path)
    rid = _mine(
        repo,
        [_finding("first"), _finding("second", kind="preference"), _finding("third", kind="decision")],
    )
    # accept first, skip second, quit before third.
    result = CliRunner().invoke(main, ["review", rid, "--repo", str(repo)], input="a\ns\nq\n")
    assert result.exit_code == 0, result.output
    assert "accepted: 1" in result.output
    assert "skipped: 1" in result.output
    assert "quit early" in result.output
    assert _commit_count(repo, f"main..maury/review/{rid}") == 1


@_skip
def test_review_interactive_reject_with_reason_prompt(tmp_path: Path) -> None:
    repo = _seed_repo(tmp_path)
    rid = _mine(repo, [_finding("only finding")])
    # reject, then supply a reason at the follow-up prompt.
    result = CliRunner().invoke(main, ["review", rid, "--repo", str(repo)], input="r\ntoo vague\n")
    assert result.exit_code == 0, result.output
    assert "rejected: 1" in result.output
    log = _run(["git", "log", f"main..maury/review/{rid}"], cwd=repo)
    assert "too vague" in log


@_skip
def test_review_interactive_reprompts_on_bad_input(tmp_path: Path) -> None:
    repo = _seed_repo(tmp_path)
    rid = _mine(repo, [_finding("only finding")])
    # garbage, then accept.
    result = CliRunner().invoke(main, ["review", rid, "--repo", str(repo)], input="zzz\na\n")
    assert result.exit_code == 0, result.output
    assert "please enter one of" in result.output
    assert "accepted: 1" in result.output


# ---- stale base / rebase-run -------------------------------------------


@_skip
def test_review_refuses_stale_base_points_at_rebase_run(tmp_path: Path) -> None:
    repo = _seed_repo(tmp_path)
    rid = _mine(repo, [_finding("x")])
    (repo / "other.txt").write_text("moved\n")
    _run(["git", "add", "other.txt"], cwd=repo)
    _run(["git", "commit", "-q", "-m", "advance"], cwd=repo)
    result = CliRunner().invoke(main, ["review", rid, "--repo", str(repo), "--accept-all"])
    assert result.exit_code != 0
    assert "has moved" in result.output
    assert "rebase-run" in result.output


@_skip
def test_rebase_run_noop_then_rebase(tmp_path: Path) -> None:
    repo = _seed_repo(tmp_path)
    rid = _mine(repo, [_finding("x")])
    # No-op first.
    noop = CliRunner().invoke(main, ["rebase-run", rid, "--repo", str(repo)])
    assert noop.exit_code == 0, noop.output
    assert "nothing to rebase" in noop.output
    # Advance main, then rebase succeeds.
    (repo / "other.txt").write_text("moved\n")
    _run(["git", "add", "other.txt"], cwd=repo)
    _run(["git", "commit", "-q", "-m", "advance"], cwd=repo)
    rebased = CliRunner().invoke(main, ["rebase-run", rid, "--repo", str(repo)])
    assert rebased.exit_code == 0, rebased.output
    assert "rebased" in rebased.output


@_skip
def test_rebase_run_missing_branch_errors(tmp_path: Path) -> None:
    repo = _seed_repo(tmp_path)
    result = CliRunner().invoke(main, ["rebase-run", "2026-01-01T000000-deadbeef", "--repo", str(repo)])
    assert result.exit_code != 0
    assert "does not exist" in result.output


# ---- ADR-0053: classify-on-accept placement + reclassify (CLI) ----------

import json  # noqa: E402

from maury.ids import new_profile_id  # noqa: E402


def _write_meta(repo: Path, *, rules_yaml: str) -> None:
    """Write committed .meta/manifest.json (base ← work) + rules.yaml."""
    meta = repo / ".meta"
    meta.mkdir(exist_ok=True)
    base, work = new_profile_id(), new_profile_id()
    (meta / "manifest.json").write_text(
        json.dumps(
            {
                "version": 1,
                "profiles": {
                    base: {"name": "base", "extends": None},
                    work: {"name": "work", "extends": base},
                },
                "hosts": {},
            }
        )
    )
    (meta / "rules.yaml").write_text(rules_yaml)
    _run(["git", "add", ".meta"], cwd=repo)
    _run(["git", "commit", "-q", "-m", "add meta"], cwd=repo)


_RULE_MATCHES_TERSE = """\
version: 1
rules:
  - id: terse-rule
    when:
      any_keyword: [terse]
    then:
      profile: work
    confidence: high
    priority: 5
    added: 2026-05-22
    reason: "route terseness prefs to work"
"""

_RULE_EMPTY = "version: 1\nrules: []\n"


@_skip
def test_review_accept_all_auto_places_matched_finding(tmp_path: Path) -> None:
    repo = _seed_repo(tmp_path)
    _write_meta(repo, rules_yaml=_RULE_MATCHES_TERSE)
    rid = _mine(repo, [_finding("prefer terse responses")])

    result = CliRunner().invoke(main, ["review", rid, "--repo", str(repo), "--accept-all"])
    assert result.exit_code == 0, result.output
    assert "auto-placed into source files: 1" in result.output
    frag = repo / "profiles" / "work" / "CLAUDE.md.fragment"
    assert frag.is_file()
    assert "prefer terse responses" in frag.read_text()


@_skip
def test_review_interactive_reclassify_places_into_chosen_mode(tmp_path: Path) -> None:
    repo = _seed_repo(tmp_path)
    _write_meta(repo, rules_yaml=_RULE_EMPTY)  # nothing matches → manual queue suggested
    rid = _mine(repo, [_finding("some unmatched preference")])

    # reclassify → work, blank host overlay. (No LLM backend in CI → places
    # but doesn't synthesize a rule; the engine path is covered elsewhere.)
    result = CliRunner().invoke(main, ["review", rid, "--repo", str(repo)], input="c\nwork\n\n")
    assert result.exit_code == 0, result.output
    assert "reclassified: 1" in result.output
    frag = repo / "profiles" / "work" / "CLAUDE.md.fragment"
    assert frag.is_file()
    assert "some unmatched preference" in frag.read_text()
