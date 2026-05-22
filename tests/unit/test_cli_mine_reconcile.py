"""CLI-layer tests for `maury mine` and `maury reconcile`.

Mining + reconciliation internals are covered in
`test_mining.py`, `test_crossref.py`, `test_reconcile.py`, and the
drift-detection tests in `test_drift.py`. These tests cover the CLI
wiring only: flag parsing, error paths, exit codes, and the pure
text/JSON formatting helpers.

The LLM-bound paths (actual `extract_from_messages` invocation) are
deliberately NOT tested here — they require either a real LLM
backend or substantial mock setup, which crosses into design-test
territory. Those paths are exercised by `test_mining.py` directly.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from click.testing import CliRunner

from maury.cli import _findings_as_json, _findings_as_text, main
from maury.ids import new_host_id, new_profile_id
from maury.mining import Finding
from maury.mining.extractor import ExtractionWindow


def _make_window(index: int = 0) -> ExtractionWindow:
    """Build a minimal ExtractionWindow for fixture findings."""
    return ExtractionWindow(messages=(), project="test-project", index=index)


def _make_finding(**overrides: object) -> Finding:
    """Build a Finding with sensible defaults; override anything per test."""
    fields: dict[str, Any] = {
        "kind": "preference",
        "scope_hint": "base",
        "text": "user prefers terse responses",
        "evidence": "user said 'be brief'",
        "confidence": "high",
        "source_window": _make_window(),
    }
    fields.update(overrides)
    return Finding(**fields)


def _write_minimal_manifest(path: Path) -> tuple[str, str]:
    pid = new_profile_id()
    hid = new_host_id("h")
    body = {
        "version": 1,
        "profiles": {pid: {"name": "home", "extends": None}},
        "hosts": {
            hid: {
                "name": "test-host",
                "profile": pid,
                "repos": {"base": {"url": "git@x:o/r.git", "mode": "rw"}},
            }
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body))
    return pid, hid


# ---- mine: error paths --------------------------------------------------


def _make_claude_md(tmp_path: Path) -> Path:
    """Write a minimal CLAUDE.md so Click's `--claude-md` default-path
    `exists=True` validator passes in CI (where ~/.claude/CLAUDE.md is absent)."""
    md = tmp_path / "CLAUDE.md"
    md.write_text("# fixture\n")
    return md


def test_mine_project_not_found(tmp_path: Path) -> None:
    projects = tmp_path / "projects"
    projects.mkdir()
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "mine",
            "--projects-dir",
            str(projects),
            "--project",
            "nonexistent-project",
            "--claude-md",
            str(_make_claude_md(tmp_path)),
        ],
    )
    assert result.exit_code != 0
    combined = result.output + (result.stderr or "")
    assert "project directory not found" in combined


def test_mine_no_projects_with_messages(tmp_path: Path) -> None:
    """Empty projects dir → 'no projects with user messages found'."""
    projects = tmp_path / "projects"
    projects.mkdir()
    runner = CliRunner()
    # No --project, so it tries to pick busiest; with empty dir, errors
    result = runner.invoke(
        main,
        ["mine", "--projects-dir", str(projects), "--claude-md", str(_make_claude_md(tmp_path))],
    )
    assert result.exit_code != 0
    combined = result.output + (result.stderr or "")
    assert "no projects with user messages found" in combined


def test_mine_empty_jsonl_in_project_says_nothing_to_mine(tmp_path: Path) -> None:
    """A project dir with empty JSONL → CLI says 'nothing to mine' without LLM call."""
    projects = tmp_path / "projects"
    project = projects / "empty-project"
    project.mkdir(parents=True)
    # An empty JSONL file = zero messages
    (project / "session.jsonl").write_text("")
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "mine",
            "--projects-dir",
            str(projects),
            "--project",
            "empty-project",
            "--claude-md",
            str(_make_claude_md(tmp_path)),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "nothing to mine" in result.output


def test_mine_projects_dir_must_exist(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["mine", "--projects-dir", str(tmp_path / "nonexistent")],
    )
    assert result.exit_code != 0


# ---- ADR-0043: incremental mining + cwd-derived default ---------------


def test_mine_cwd_override_derives_project(tmp_path: Path) -> None:
    """`--cwd <path>` derives the project dir via the verified algorithm
    and uses that dir. If derivation finds a matching dir, mine that one."""
    from maury.projects import derive_project_dir

    projects = tmp_path / "projects"
    # Make `tmp_path / "demo"` the "cwd"; pre-create the matching
    # project dir under projects/ with one empty jsonl so mine reaches
    # the "nothing to mine" branch.
    demo_cwd = tmp_path / "demo"
    demo_cwd.mkdir()
    derived_name = derive_project_dir(demo_cwd)
    project = projects / derived_name
    project.mkdir(parents=True)
    (project / "session.jsonl").write_text("")

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "mine",
            "--projects-dir",
            str(projects),
            "--cwd",
            str(demo_cwd),
            "--claude-md",
            str(_make_claude_md(tmp_path)),
        ],
    )
    assert result.exit_code == 0, result.output
    assert f"derived from {demo_cwd}" in result.output
    assert derived_name in result.output
    assert "nothing to mine" in result.output


def test_mine_cwd_no_match_falls_back_to_busiest_real(tmp_path: Path) -> None:
    """The fallback-to-busiest path with a real (but unmatched) cwd.

    The fallback notice should appear BEFORE `_pick_busiest_project`
    raises (in this fixture: projects/ has only an empty jsonl, so
    busiest will fail). What's under test is the notice, not whether
    the fallback target itself has content.
    """
    projects = tmp_path / "projects"
    busiest = projects / "busiest-project"
    busiest.mkdir(parents=True)
    (busiest / "session.jsonl").write_text("")

    other_cwd = tmp_path / "unrelated"
    other_cwd.mkdir()

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "mine",
            "--projects-dir",
            str(projects),
            "--cwd",
            str(other_cwd),
            "--claude-md",
            str(_make_claude_md(tmp_path)),
        ],
    )
    # The fallback notice must appear regardless of whether the busiest
    # fallback subsequently succeeds.
    combined = result.output + (result.stderr or "")
    assert "has no project dir" in combined
    assert "falling back to busiest" in combined


def test_mine_since_invalid_date_errors(tmp_path: Path) -> None:
    projects = tmp_path / "projects"
    project = projects / "p"
    project.mkdir(parents=True)
    (project / "s.jsonl").write_text("")

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "mine",
            "--projects-dir",
            str(projects),
            "--project",
            "p",
            "--since",
            "not-a-date",
            "--claude-md",
            str(_make_claude_md(tmp_path)),
        ],
    )
    assert result.exit_code != 0
    combined = result.output + (result.stderr or "")
    assert "--since" in combined


def test_mine_since_no_jsonls_after_cutoff_says_nothing_new(tmp_path: Path) -> None:
    """When `--since` cuts off all jsonls, the CLI emits 'nothing new'."""
    import os
    import time

    projects = tmp_path / "projects"
    project = projects / "p"
    project.mkdir(parents=True)
    f = project / "s.jsonl"
    f.write_text("")
    # Force mtime well in the past.
    past = time.time() - 86400 * 30  # 30 days ago
    os.utime(f, (past, past))

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "mine",
            "--projects-dir",
            str(projects),
            "--project",
            "p",
            "--since",
            "2099-01-01",
            "--claude-md",
            str(_make_claude_md(tmp_path)),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "nothing new" in result.output


def test_mine_invalid_backend_rejected_by_click(tmp_path: Path) -> None:
    projects = tmp_path / "projects"
    projects.mkdir()
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["mine", "--projects-dir", str(projects), "--backend", "nonsense"],
    )
    assert result.exit_code != 0
    combined = result.output + (result.stderr or "")
    assert "nonsense" in combined or "Invalid value" in combined


def test_mine_invalid_format_rejected_by_click(tmp_path: Path) -> None:
    projects = tmp_path / "projects"
    projects.mkdir()
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["mine", "--projects-dir", str(projects), "--format", "yaml"],
    )
    assert result.exit_code != 0


# ---- mine: pure formatting helpers --------------------------------------


def test_findings_as_text_empty() -> None:
    out = _findings_as_text(findings=[], windows_processed=5)
    assert "processed 5 window(s)" in out
    assert "nothing durable" in out


def test_findings_as_text_flat_list() -> None:
    findings = [
        _make_finding(text="prefer ruff", scope_hint="base"),
        _make_finding(text="use 2-space indent", scope_hint="base"),
    ]
    out = _findings_as_text(findings, windows_processed=3)
    assert "2 finding(s) across 3 window(s)" in out
    assert "prefer ruff" in out
    assert "use 2-space indent" in out
    assert "[high]" in out


def test_findings_as_json_round_trips() -> None:
    findings = [_make_finding(text="prefer ruff")]
    payload = _findings_as_json(findings)
    parsed = json.loads(payload)
    assert isinstance(parsed, (list, dict))


# ---- cross-ref formatters -----------------------------------------------


def test_findings_as_text_with_crossref_summary_groups_by_state() -> None:
    """When a CrossRefSummary is passed, output groups findings by state
    with the killer signal (REINFORCED) first."""
    from maury.cli import _findings_as_text  # local import to keep header tidy
    from maury.mining.crossref import CrossRefResult, CrossRefSummary

    f_new = _make_finding(text="prefer terse responses")
    f_reinforced = _make_finding(text="never amend commits")
    summary = CrossRefSummary.empty()
    summary.add(
        f_new,
        CrossRefResult(
            state="NEW",
            claude_md_quote="",
            rationale="not in claude.md",
            suggested_action="propose-new",
        ),
    )
    summary.add(
        f_reinforced,
        CrossRefResult(
            state="PRESENT_AND_REINFORCED",
            claude_md_quote="never amend",
            rationale="user corrected behavior",
            suggested_action="investigate-why-not-followed",
        ),
    )
    out = _findings_as_text(
        findings=[f_new, f_reinforced],
        windows_processed=4,
        summary=summary,
    )
    # Header counts both states
    assert "2 finding(s) across 4 window(s)" in out
    # The REINFORCED bucket appears before NEW (state_order)
    reinforced_pos = out.find("PRESENT_AND_REINFORCED")
    new_pos = out.find("NEW (")
    assert reinforced_pos != -1 and new_pos != -1
    assert reinforced_pos < new_pos
    # Both findings + their xref rationales appear
    assert "never amend commits" in out
    assert "user corrected behavior" in out
    assert "propose-new" in out
    # The quote is included when non-empty
    assert "matches CLAUDE.md" in out


def test_findings_as_json_with_crossref_summary_embeds_xref() -> None:
    """When summary is passed, each finding's JSON entry includes a
    `crossref` field with state + rationale + action."""
    from maury.cli import _findings_as_json
    from maury.mining.crossref import CrossRefResult, CrossRefSummary

    f = _make_finding(text="prefer ruff over black")
    summary = CrossRefSummary.empty()
    summary.add(
        f,
        CrossRefResult(
            state="PRESENT_AND_CLEAR",
            claude_md_quote="use ruff",
            rationale="already documented",
            suggested_action="suppress",
        ),
    )
    out = _findings_as_json([f], summary=summary)
    payload = json.loads(out)
    assert isinstance(payload, list)
    assert payload[0]["crossref"]["state"] == "PRESENT_AND_CLEAR"
    assert payload[0]["crossref"]["suggested_action"] == "suppress"
    assert payload[0]["crossref"]["claude_md_quote"] == "use ruff"


# ---- reconcile: error paths ---------------------------------------------


def test_reconcile_no_manifest_errors(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "reconcile",
            "--manifest-file",
            str(tmp_path / "nonexistent.json"),
            "--target",
            str(tmp_path / "target"),
        ],
    )
    assert result.exit_code != 0


def test_reconcile_no_last_render_exits_2(tmp_path: Path) -> None:
    """No `maury-state/last-render.json` baseline → exit 2 with friendly pointer."""
    mpath = tmp_path / "manifest.json"
    _write_minimal_manifest(mpath)
    target = tmp_path / "target"
    target.mkdir()
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "reconcile",
            "--manifest-file",
            str(mpath),
            "--target",
            str(target),
        ],
    )
    assert result.exit_code == 2, result.output
    assert "no last-render.json" in result.output
    assert "maury sync" in result.output


def test_reconcile_baseline_host_not_in_manifest_errors(tmp_path: Path) -> None:
    """If baseline references a host_id no longer in the manifest, error."""
    mpath = tmp_path / "manifest.json"
    _write_minimal_manifest(mpath)
    target = tmp_path / "target"
    state_dir = target / "maury-state"
    state_dir.mkdir(parents=True)
    # Construct a last-render.json that references a non-manifest host_id
    bogus_hid = new_host_id("h")
    last_render = {
        "host_id": bogus_hid,
        "files": [],
        "rendered_at": "2026-05-14T00:00:00Z",
    }
    (state_dir / "last-render.json").write_text(json.dumps(last_render))
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "reconcile",
            "--manifest-file",
            str(mpath),
            "--target",
            str(target),
        ],
    )
    assert result.exit_code != 0
    combined = result.output + (result.stderr or "")
    assert "no longer in the manifest" in combined or bogus_hid in combined


# ---- mine --write-run-branch wiring (ADR-0022 Phase 6 slice 4) -----------


def test_emit_mining_run_branch_requires_repo_path(tmp_path: Path) -> None:
    """The helper refuses without a --repo argument (ClickException);
    don't try to guess where to write the branch."""
    import click as _click
    import pytest

    from maury.cli import _emit_mining_run_branch

    with pytest.raises(_click.ClickException, match="--repo"):
        _emit_mining_run_branch(
            repo_path=None,
            findings=[_make_finding()],
            target_dir=tmp_path,
            crossref_states={},
            rewrites={},
        )


def test_emit_mining_run_branch_requires_baseline(tmp_path: Path) -> None:
    """Without a host-identity baseline the helper can't compute a
    run-id; errors with a `maury init` pointer."""
    import subprocess

    import click as _click
    import pytest

    from maury.cli import _emit_mining_run_branch

    # Set up a real git repo (so the next precondition wouldn't trip).
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True, capture_output=True)
    (repo / "f").write_text("x")
    subprocess.run(["git", "add", "f"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)

    target = tmp_path / "target"  # no baseline written

    with pytest.raises(_click.ClickException, match="maury init"):
        _emit_mining_run_branch(
            repo_path=repo,
            findings=[_make_finding()],
            target_dir=target,
            crossref_states={},
            rewrites={},
        )


def test_emit_mining_run_branch_writes_branch_and_audit_event(tmp_path: Path) -> None:
    """End-to-end: with a baseline + clean repo, the helper creates the
    `maury/run/<run-id>` branch with one commit per finding and emits a
    `mining_run_created` audit event."""
    import subprocess

    from maury.audit_log import read_events
    from maury.cli import _emit_mining_run_branch
    from maury.host_identity import SCHEMA_VERSION, HostIdentityBaseline, write_baseline

    # Set up the target dir with a baseline (provides host_hex).
    target = tmp_path / "target"
    write_baseline(
        target,
        HostIdentityBaseline(
            schema_version=SCHEMA_VERSION,
            host_id_hex="aabbccdd",
            registered_at="2026-05-21T10:00:00Z",
            mode_id="mode_xxx",
            mode_name_at_bootstrap="personal",
        ),
    )

    # Set up the base repo with a clean working tree.
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True, capture_output=True)
    (repo / "README.md").write_text("# repo\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)

    findings = [
        _make_finding(kind="feedback", text="prefer terse"),
        _make_finding(kind="preference", text="use Python 3.11+"),
    ]

    _emit_mining_run_branch(
        repo_path=repo,
        findings=findings,
        target_dir=target,
        crossref_states={},
        rewrites={},
    )

    # A branch matching the maury/run/<...>-aabbccdd shape now exists.
    proc = subprocess.run(
        ["git", "branch", "--list", "maury/run/*"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "aabbccdd" in proc.stdout
    # And the mining-findings.md file is present with both findings.
    staging = (repo / "mining-findings.md").read_text()
    assert "prefer terse" in staging
    assert "use Python 3.11+" in staging

    # Audit event landed.
    events = list(read_events(target))
    assert any(e.event == "mining_run_created" for e in events)
    mining_event = next(e for e in events if e.event == "mining_run_created")
    assert mining_event.details["commit_count"] == 2
    assert mining_event.details["skipped_dedup"] == 0
