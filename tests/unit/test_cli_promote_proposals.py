"""Test the mine-time promotion-proposal emission helper
(`_emit_promotion_proposals`, ADR-0045 §1 / Phase 9 slice 4b).

Tested directly (not through the full `maury mine` pipeline) so the setup
stays small: a git repo with a manifest + a list of findings.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from maury.cli import _emit_promotion_proposals
from maury.ids import new_profile_id
from maury.mining.extractor import ExtractionWindow, Finding
from maury.mining.transcripts import TranscriptMessage
from maury.promotion.proposal import read_proposals


def _git_available() -> bool:
    try:
        return subprocess.run(["git", "--version"], capture_output=True, check=False).returncode == 0
    except OSError:
        return False


_skip = pytest.mark.skipif(not _git_available(), reason="git not on PATH")


def _run(args: list[str], cwd: Path) -> str:
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout


def _repo_with_manifest(tmp_path: Path, *, hosts: dict[str, object] | None = None) -> tuple[Path, dict[str, str]]:
    """base ← {personal, work}. Returns (repo, name→id)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(["git", "init", "-q", "-b", "main"], cwd=repo)
    _run(["git", "config", "user.email", "t@t.invalid"], cwd=repo)
    _run(["git", "config", "user.name", "t"], cwd=repo)
    base, personal, work = new_profile_id(), new_profile_id(), new_profile_id()
    meta = repo / ".meta"
    meta.mkdir()
    (meta / "manifest.json").write_text(
        json.dumps(
            {
                "version": 1,
                "profiles": {
                    base: {"name": "base", "extends": None},
                    personal: {"name": "personal", "extends": base},
                    work: {"name": "work", "extends": base},
                },
                "hosts": hosts or {},
            }
        )
    )
    _run(["git", "add", "."], cwd=repo)
    _run(["git", "commit", "-q", "-m", "init"], cwd=repo)
    return repo, {"base": base, "personal": personal, "work": work}


def _finding(text: str, *, scope: str, kind: str = "feedback") -> Finding:
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


@_skip
def test_proposal_written_for_ancestor_destination(tmp_path: Path) -> None:
    repo, ids = _repo_with_manifest(tmp_path)
    # Host on `work`; a finding destined for `base` (ancestor) → proposal.
    findings = [
        _finding("universal preference", scope="base"),
        _finding("work-specific thing", scope="work", kind="preference"),
    ]
    written = _emit_promotion_proposals(
        repo_path=repo,
        findings=findings,
        host_mode_id=ids["work"],
        source_mode="work",
        source_host="host_aabbccdd",
    )
    assert written == 1  # base proposed; work skipped (own subtree)
    proposals = read_proposals(repo)
    assert len(proposals) == 1
    assert proposals[0].dest_mode == "base"
    assert proposals[0].source_mode == "work"


@_skip
def test_proposals_committed_on_current_branch(tmp_path: Path) -> None:
    repo, ids = _repo_with_manifest(tmp_path)
    _emit_promotion_proposals(
        repo_path=repo,
        findings=[_finding("x", scope="base")],
        host_mode_id=ids["work"],
        source_mode="work",
        source_host="h",
    )
    # A commit landed and the tree is clean (proposals are versioned).
    status = _run(["git", "status", "--porcelain"], cwd=repo)
    assert status.strip() == ""
    log = _run(["git", "log", "-1", "--format=%s"], cwd=repo)
    assert "promotion proposal" in log


@_skip
def test_no_proposal_for_unresolved_or_own_subtree(tmp_path: Path) -> None:
    repo, ids = _repo_with_manifest(tmp_path)
    findings = [
        _finding("own", scope="work"),  # own mode → no proposal
        _finding("noise", scope="project-specific"),  # not a mode → skip
    ]
    written = _emit_promotion_proposals(
        repo_path=repo,
        findings=findings,
        host_mode_id=ids["work"],
        source_mode="work",
        source_host="h",
    )
    assert written == 0
    assert read_proposals(repo) == []


@_skip
def test_no_manifest_is_silent_noop(tmp_path: Path) -> None:
    repo = tmp_path / "bare"
    repo.mkdir()
    _run(["git", "init", "-q", "-b", "main"], cwd=repo)
    written = _emit_promotion_proposals(
        repo_path=repo,
        findings=[_finding("x", scope="base")],
        host_mode_id="whatever",
        source_mode="work",
        source_host="h",
    )
    assert written == 0


@_skip
def test_push_policy_disabled_skips_proposals(tmp_path: Path) -> None:
    """ADR-0045 §6: a host with push_policy: disabled emits no proposals."""
    from maury.ids import new_host_id, new_profile_id
    from maury.manifest import load_manifest

    # Build a manifest whose work host is push_policy: disabled, with a
    # host id whose 8-hex prefix we can hand to the helper as source_host.
    base, work = new_profile_id(), new_profile_id()
    hid = new_host_id("workbox")
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(["git", "init", "-q", "-b", "main"], cwd=repo)
    _run(["git", "config", "user.email", "t@t.invalid"], cwd=repo)
    _run(["git", "config", "user.name", "t"], cwd=repo)
    meta = repo / ".meta"
    meta.mkdir()
    (meta / "manifest.json").write_text(
        json.dumps(
            {
                "version": 1,
                "profiles": {
                    base: {"name": "base", "extends": None},
                    work: {"name": "work", "extends": base},
                },
                "hosts": {
                    hid: {
                        "name": "workbox",
                        "profile": work,
                        "push_policy": "disabled",
                        "repos": {"work": {"url": "git@github.com:acme-corp/work.git", "mode": "rw"}},
                    }
                },
            }
        )
    )
    _run(["git", "add", "."], cwd=repo)
    _run(["git", "commit", "-q", "-m", "init"], cwd=repo)

    from maury.ids import host_id_hex_prefix

    written = _emit_promotion_proposals(
        repo_path=repo,
        findings=[_finding("universal preference", scope="base")],  # would normally propose to base
        host_mode_id=work,
        source_mode="work",
        source_host=host_id_hex_prefix(hid),
    )
    assert written == 0
    assert read_proposals(repo) == []
    # Sanity: the manifest really did parse with the disabled policy.
    assert load_manifest(meta / "manifest.json").hosts[hid].push_policy.value == "disabled"
