"""Tests for `maury.promotion.promote` — the branch-fetch cross-repo
promotion engine (ADR-0045 §4 + ADR-0022). Two real temp git repos: a
source mined into `maury/run/*` and a destination that receives
`maury/promoted/*`.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from maury.manifest import Manifest, ProfileSpec
from maury.mining.extractor import ExtractionWindow, Finding
from maury.mining.review import Decision, DecisionKind
from maury.mining.run_branch import write_run_branch
from maury.mining.transcripts import TranscriptMessage
from maury.promotion.promote import (
    PromoteError,
    PromotionCandidate,
    promote_run,
)

# Mode tree: base ← {personal ← consulting ← acme, work ← {acme_client, globex_client}}
_BASE, _PERSONAL, _CONSULTING, _ACME = "p_base", "p_personal", "p_consulting", "p_acme"
_WORK, _ACME_CLIENT, _GLOBEX = "p_work", "p_acme_client", "p_globex"


def _manifest() -> Manifest:
    return Manifest(
        version=1,
        profiles={
            _BASE: ProfileSpec(name="base", extends=None),
            _PERSONAL: ProfileSpec(name="personal", extends=_BASE),
            _CONSULTING: ProfileSpec(name="personal:consulting", extends=_PERSONAL),
            _ACME: ProfileSpec(name="personal:consulting:acme", extends=_CONSULTING),
            _WORK: ProfileSpec(name="work", extends=_BASE),
            _ACME_CLIENT: ProfileSpec(name="work:acme-client", extends=_WORK),
            _GLOBEX: ProfileSpec(name="work:globex-client", extends=_WORK),
        },
        hosts={},
    )


def _git_available() -> bool:
    try:
        return subprocess.run(["git", "--version"], capture_output=True, check=False).returncode == 0
    except OSError:
        return False


_skip = pytest.mark.skipif(not _git_available(), reason="git not on PATH")


def _run(args: list[str], cwd: Path) -> str:
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout


def _seed_repo(parent: Path, name: str) -> Path:
    repo = parent / name
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


def _mine_source(src: Path, findings: list[Finding], *, source_mode: str) -> None:
    """Write a run branch in the source repo; leave it on main."""
    write_run_branch(repo_dir=src, findings=findings, host_hex="aabbccdd", source_mode=source_mode)
    _run(["git", "checkout", "-q", "main"], cwd=src)


def _scripted(decisions: list[Decision]) -> Callable[[PromotionCandidate], Decision]:
    it = iter(decisions)

    def provider(_cand: PromotionCandidate) -> Decision:
        return next(it)

    return provider


def _commit_count(repo: Path, rng: str) -> int:
    return int(_run(["git", "rev-list", "--count", rng], cwd=repo).strip())


# ---- preconditions ------------------------------------------------------


@_skip
def test_promote_unknown_target_mode_raises(tmp_path: Path) -> None:
    src = _seed_repo(tmp_path, "src")
    dest = _seed_repo(tmp_path, "dest")
    _mine_source(src, [_finding("x")], source_mode="work")
    with pytest.raises(PromoteError, match="not a mode"):
        promote_run(
            source_repo=src,
            dest_repo=dest,
            target_mode_name="nonexistent",
            manifest=_manifest(),
            promoted_id="pid-1",
            curator_host="h",
            decide=_scripted([]),
        )


@_skip
def test_promote_refuses_dirty_dest(tmp_path: Path) -> None:
    src = _seed_repo(tmp_path, "src")
    dest = _seed_repo(tmp_path, "dest")
    _mine_source(src, [_finding("x")], source_mode="work")
    (dest / "dirty.txt").write_text("x\n")
    with pytest.raises(PromoteError, match="uncommitted changes"):
        promote_run(
            source_repo=src,
            dest_repo=dest,
            target_mode_name="base",
            manifest=_manifest(),
            promoted_id="pid-1",
            curator_host="h",
            decide=_scripted([]),
        )


# ---- accept (the happy path) -------------------------------------------


@_skip
def test_promote_accept_lands_with_promoted_from_trailer(tmp_path: Path) -> None:
    src = _seed_repo(tmp_path, "src")
    dest = _seed_repo(tmp_path, "dest")
    _mine_source(src, [_finding("prefer terse responses everywhere")], source_mode="work")

    result = promote_run(
        source_repo=src,
        dest_repo=dest,
        target_mode_name="base",  # work → base is a valid (ancestor) promotion
        manifest=_manifest(),
        promoted_id="pid-1",
        curator_host="curator_x",
        decide=_scripted([Decision(DecisionKind.ACCEPT)]),
    )
    assert result.promoted == 1
    assert result.graph_refused == 0
    assert result.promoted_branch == "maury/promoted/pid-1"
    assert _commit_count(dest, "main..maury/promoted/pid-1") == 1

    log = _run(["git", "log", "main..maury/promoted/pid-1", "--format=%B"], cwd=dest)
    assert "Promoted-From: src@" in log
    assert "Content-Hash:" in log  # rode through from the source message
    assert "Source-Mode: work" in log
    staging = (dest / "mining-findings.md").read_text()
    assert "prefer terse responses everywhere" in staging


# ---- graph constraint ---------------------------------------------------


@_skip
def test_promote_to_sibling_is_graph_refused_not_shown(tmp_path: Path) -> None:
    src = _seed_repo(tmp_path, "src")
    dest = _seed_repo(tmp_path, "dest")
    _mine_source(src, [_finding("client-specific thing")], source_mode="work:acme-client")

    seen: list[PromotionCandidate] = []

    def recording(cand: PromotionCandidate) -> Decision:
        seen.append(cand)
        return Decision(DecisionKind.ACCEPT)

    result = promote_run(
        source_repo=src,
        dest_repo=dest,
        target_mode_name="work:globex-client",  # sibling of acme-client → refused
        manifest=_manifest(),
        promoted_id="pid-1",
        curator_host="h",
        decide=recording,
    )
    assert result.graph_refused == 1
    assert result.promoted == 0
    assert seen == []  # refused findings never reach the curator
    assert any("shared ancestor is 'work'" in note for note in result.graph_refused_notes)


@_skip
def test_promote_unresolved_source_mode_is_skipped(tmp_path: Path) -> None:
    src = _seed_repo(tmp_path, "src")
    dest = _seed_repo(tmp_path, "dest")
    # Source mode name not present in the manifest → cannot graph-check.
    _mine_source(src, [_finding("x")], source_mode="ghost-mode")
    result = promote_run(
        source_repo=src,
        dest_repo=dest,
        target_mode_name="base",
        manifest=_manifest(),
        promoted_id="pid-1",
        curator_host="h",
        decide=_scripted([]),
    )
    assert result.unresolved_source_mode == 1
    assert result.promoted == 0


# ---- reject / skip / quit ----------------------------------------------


@_skip
def test_promote_reject_writes_noop_commit_with_source_mode(tmp_path: Path) -> None:
    src = _seed_repo(tmp_path, "src")
    dest = _seed_repo(tmp_path, "dest")
    _mine_source(src, [_finding("noise")], source_mode="work")
    result = promote_run(
        source_repo=src,
        dest_repo=dest,
        target_mode_name="base",
        manifest=_manifest(),
        promoted_id="pid-1",
        curator_host="h",
        decide=_scripted([Decision(DecisionKind.REJECT, reason="too narrow after all")]),
    )
    assert result.rejected == 1
    assert result.rejection_commit_written is True
    log = _run(["git", "log", "main..maury/promoted/pid-1"], cwd=dest)
    assert "Rejected-Content-Hash:" in log
    assert "Rejected-Source-Mode: work" in log
    assert "too narrow after all" in log


@_skip
def test_promote_quit_persists_accepts_discards_pending_rejections(tmp_path: Path) -> None:
    src = _seed_repo(tmp_path, "src")
    dest = _seed_repo(tmp_path, "dest")
    _mine_source(src, [_finding("keep me"), _finding("never seen", kind="preference")], source_mode="work")
    result = promote_run(
        source_repo=src,
        dest_repo=dest,
        target_mode_name="base",
        manifest=_manifest(),
        promoted_id="pid-1",
        curator_host="h",
        decide=_scripted([Decision(DecisionKind.ACCEPT), Decision(DecisionKind.QUIT)]),
    )
    assert result.quit_early is True
    assert result.promoted == 1
    assert _commit_count(dest, "main..maury/promoted/pid-1") == 1


# ---- resume -------------------------------------------------------------


@_skip
def test_promote_resume_skips_already_promoted(tmp_path: Path) -> None:
    src = _seed_repo(tmp_path, "src")
    dest = _seed_repo(tmp_path, "dest")
    _mine_source(src, [_finding("first"), _finding("second", kind="preference")], source_mode="work")

    first = promote_run(
        source_repo=src,
        dest_repo=dest,
        target_mode_name="base",
        manifest=_manifest(),
        promoted_id="pid-1",
        curator_host="h",
        decide=_scripted([Decision(DecisionKind.ACCEPT), Decision(DecisionKind.QUIT)]),
    )
    assert first.promoted == 1

    second = promote_run(
        source_repo=src,
        dest_repo=dest,
        target_mode_name="base",
        manifest=_manifest(),
        promoted_id="pid-1",
        curator_host="h",
        decide=_scripted([Decision(DecisionKind.ACCEPT)]),
    )
    assert second.resumed_skipped == 1
    assert second.promoted == 1
    assert _commit_count(dest, "main..maury/promoted/pid-1") == 2
