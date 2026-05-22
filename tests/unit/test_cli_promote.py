"""CLI-layer tests for `maury promote` (ADR-0045 Phase 9 slice 3b).

The promotion engine is tested in `test_promotion_promote.py`. These
cover the click wiring: flag validation, manifest resolution, the
--accept-all batch path, graph-refusal messaging, and the interactive
prompt loop.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from maury.cli import main
from maury.ids import new_profile_id
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


def _write_manifest(dest: Path) -> Path:
    """Write a base ← {personal, work ← {acme-client, globex-client}} manifest
    with real surrogate profile IDs (load_manifest validates the format)."""
    base, personal, work, acme, globex = (new_profile_id() for _ in range(5))
    meta = dest / ".meta"
    meta.mkdir(exist_ok=True)
    body = {
        "version": 1,
        "profiles": {
            base: {"name": "base", "extends": None},
            personal: {"name": "personal", "extends": base},
            work: {"name": "work", "extends": base},
            acme: {"name": "work:acme-client", "extends": work},
            globex: {"name": "work:globex-client", "extends": work},
        },
        "hosts": {},
    }
    path = meta / "manifest.json"
    path.write_text(json.dumps(body, indent=2))
    # Commit it so the dest worktree is clean for promotion.
    _run(["git", "add", ".meta/manifest.json"], cwd=dest)
    _run(["git", "commit", "-q", "-m", "add manifest"], cwd=dest)
    return path


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
    write_run_branch(repo_dir=src, findings=findings, host_hex="aabbccdd", source_mode=source_mode)
    _run(["git", "checkout", "-q", "main"], cwd=src)


# ---- flag validation ----------------------------------------------------


def test_promote_accept_all_and_reject_all_exclusive(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    src.mkdir()
    dest.mkdir()
    result = CliRunner().invoke(
        main,
        ["promote", "--from", str(src), "--to", str(dest), "--to-mode", "base", "--accept-all", "--reject-all"],
    )
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


def test_promote_reason_requires_reject_all(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    src.mkdir()
    dest.mkdir()
    result = CliRunner().invoke(
        main, ["promote", "--from", str(src), "--to", str(dest), "--to-mode", "base", "--reason", "x"]
    )
    assert result.exit_code != 0
    assert "--reason only applies with --reject-all" in result.output


@_skip
def test_promote_missing_manifest_errors(tmp_path: Path) -> None:
    src = _seed_repo(tmp_path, "src")
    dest = _seed_repo(tmp_path, "dest")  # no .meta/manifest.json
    _mine_source(src, [_finding("x")], source_mode="work")
    result = CliRunner().invoke(main, ["promote", "--from", str(src), "--to", str(dest), "--to-mode", "base"])
    assert result.exit_code != 0
    assert "manifest not found" in result.output


# ---- batch accept -------------------------------------------------------


@_skip
def test_promote_accept_all_lands_promoted_branch(tmp_path: Path) -> None:
    src = _seed_repo(tmp_path, "src")
    dest = _seed_repo(tmp_path, "dest")
    _write_manifest(dest)
    _mine_source(src, [_finding("prefer terse everywhere")], source_mode="work")

    result = CliRunner().invoke(
        main, ["promote", "--from", str(src), "--to", str(dest), "--to-mode", "base", "--accept-all"]
    )
    assert result.exit_code == 0, result.output
    assert "promoted: 1" in result.output
    assert "maury/promoted/" in result.output
    # A promoted branch exists in dest carrying the Promoted-From trailer.
    branch = _run(["git", "branch", "--list", "maury/promoted/*"], cwd=dest).strip().lstrip("* ").strip()
    log = _run(["git", "log", f"main..{branch}", "--format=%B"], cwd=dest)
    assert "Promoted-From: src@" in log


# ---- graph refusal ------------------------------------------------------


@_skip
def test_promote_sibling_target_is_refused_with_hint(tmp_path: Path) -> None:
    src = _seed_repo(tmp_path, "src")
    dest = _seed_repo(tmp_path, "dest")
    _write_manifest(dest)
    _mine_source(src, [_finding("client thing")], source_mode="work:acme-client")
    result = CliRunner().invoke(
        main,
        ["promote", "--from", str(src), "--to", str(dest), "--to-mode", "work:globex-client", "--accept-all"],
    )
    assert result.exit_code == 0, result.output
    assert "graph-refused: 1" in result.output
    assert "shared ancestor is 'work'" in result.output


# ---- interactive --------------------------------------------------------


@_skip
def test_promote_interactive_accept(tmp_path: Path) -> None:
    src = _seed_repo(tmp_path, "src")
    dest = _seed_repo(tmp_path, "dest")
    _write_manifest(dest)
    _mine_source(src, [_finding("only finding")], source_mode="work")
    result = CliRunner().invoke(
        main, ["promote", "--from", str(src), "--to", str(dest), "--to-mode", "base"], input="a\n"
    )
    assert result.exit_code == 0, result.output
    assert "promoted: 1" in result.output
