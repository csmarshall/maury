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
    hid = new_host_id()
    body = {
        "version": 2,
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


def test_mine_project_not_found(tmp_path: Path) -> None:
    projects = tmp_path / "projects"
    projects.mkdir()
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["mine", "--projects-dir", str(projects), "--project", "nonexistent-project"],
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
    result = runner.invoke(main, ["mine", "--projects-dir", str(projects)])
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
        ["mine", "--projects-dir", str(projects), "--project", "empty-project"],
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
    bogus_hid = new_host_id()
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
