"""Tests for the pure print helpers behind `maury verify-cc-*` commands.

These commands actually run `claude -p` against a probe workspace, so
their happy-path tests live in scope-checked integration tests gated
on `claude_present()`. Here we cover only the pure formatters that
turn `HarnessReport` / `ProjectDirHarnessReport` /
`HookTimingHarnessReport` into stdout — they're directly importable
and trivially testable with fixture reports.

We also exercise `verify-cc-hooks --check-only` via CliRunner +
monkeypatched `shutil.which` so the check is deterministic regardless
of whether `claude` is on PATH on the runner.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from maury.cli import (
    _print_empirical_report,
    _print_hook_timing_report,
    _print_project_dir_report,
    main,
)
from maury.empirical_tests import (
    HarnessReport,
    HookTimingHarnessReport,
    HookTimingProbeResult,
    ProbeResult,
    ProjectDirCase,
    ProjectDirCaseResult,
    ProjectDirHarnessReport,
)

# ---- _print_empirical_report -------------------------------------------


def _empirical_report(
    *,
    claude_invoked: bool = True,
    claude_returncode: int | None = 0,
    probes: tuple[ProbeResult, ...] = (),
    stderr: str = "",
) -> HarnessReport:
    return HarnessReport(
        workspace=Path("/tmp/fake-ws"),
        claude_invoked=claude_invoked,
        claude_returncode=claude_returncode,
        claude_stderr_excerpt=stderr,
        probes=probes,
    )


def test_empirical_text_no_claude_invoked(capsys: pytest.CaptureFixture[str]) -> None:
    """When claude isn't on PATH, print the stderr excerpt + PATH hint."""
    report = _empirical_report(
        claude_invoked=False,
        claude_returncode=None,
        stderr="claude binary not found",
    )
    _print_empirical_report(report, "text")
    out = capsys.readouterr().out
    assert "claude binary not found" in out
    assert "claude" in out  # The PATH hint references the binary


def test_empirical_text_claude_nonzero_returncode_prints_warning(capsys: pytest.CaptureFixture[str]) -> None:
    report = _empirical_report(
        claude_returncode=7,
        probes=(ProbeResult(name="p1", passed=True, detail="ok"),),
        stderr="boom",
    )
    _print_empirical_report(report, "text")
    out = capsys.readouterr().out
    assert "claude exited with code 7" in out
    assert "boom" in out


def test_empirical_text_no_probes_says_so(capsys: pytest.CaptureFixture[str]) -> None:
    report = _empirical_report(probes=())
    _print_empirical_report(report, "text")
    out = capsys.readouterr().out
    assert "no probes ran" in out


def test_empirical_text_passed_summary(capsys: pytest.CaptureFixture[str]) -> None:
    report = _empirical_report(
        probes=(
            ProbeResult(name="hooks-fired", passed=True, detail="ok", captured={"n": 1}),
            ProbeResult(name="ordered", passed=True, detail="ok"),
        )
    )
    _print_empirical_report(report, "text")
    out = capsys.readouterr().out
    assert "workspace:" in out
    assert "hooks-fired" in out
    assert "ordered" in out
    assert "ADR-0023" in out  # success footer references the ADR
    # Captured map keys + repr'd values appear per-line
    assert "n: 1" in out


def test_empirical_text_failed_summary(capsys: pytest.CaptureFixture[str]) -> None:
    report = _empirical_report(probes=(ProbeResult(name="hooks-fired", passed=False, detail="no marker"),))
    _print_empirical_report(report, "text")
    out = capsys.readouterr().out
    assert "hooks-fired" in out
    assert "Empirical-test debt" in out


def test_empirical_json_round_trips(capsys: pytest.CaptureFixture[str]) -> None:
    report = _empirical_report(
        probes=(ProbeResult(name="p1", passed=True, detail="ok"),),
    )
    _print_empirical_report(report, "json")
    payload = json.loads(capsys.readouterr().out)
    assert payload["workspace"] == "/tmp/fake-ws"
    assert payload["claude_invoked"] is True
    assert payload["passed"] is True
    assert payload["probes"][0]["name"] == "p1"


# ---- _print_project_dir_report -----------------------------------------


def _project_dir_case_result(*, suffix: str = "plain", matched: bool = True, group: str = "") -> ProjectDirCaseResult:
    case = ProjectDirCase(cwd_suffix=suffix, description=f"case {suffix}", collision_group=group)
    return ProjectDirCaseResult(
        case=case,
        cwd=Path(f"/tmp/{suffix}"),
        predicted_dir_name=f"-tmp-{suffix}",
        observed_dir_names=(f"-tmp-{suffix}",) if matched else (),
        matched=matched,
        detail="matched" if matched else "no observed dir",
    )


def _project_dir_report(
    *,
    claude_present: bool = True,
    results: tuple[ProjectDirCaseResult, ...] = (),
    verified_groups: tuple[str, ...] = (),
) -> ProjectDirHarnessReport:
    return ProjectDirHarnessReport(
        test_root=Path("/tmp/test-root"),
        claude_present=claude_present,
        claude_version="1.0.0",
        results=results,
        collision_groups_verified=verified_groups,
        new_project_dirs=(),
    )


def test_project_dir_text_no_claude(capsys: pytest.CaptureFixture[str]) -> None:
    report = _project_dir_report(claude_present=False)
    _print_project_dir_report(report, "text")
    out = capsys.readouterr().out
    assert "`claude` not found" in out


def test_project_dir_text_passed(capsys: pytest.CaptureFixture[str]) -> None:
    report = _project_dir_report(
        results=(_project_dir_case_result(),),
    )
    _print_project_dir_report(report, "text")
    out = capsys.readouterr().out
    assert "claude version" in out
    assert "matched" in out
    assert "Algorithm holds" in out
    assert "#54865" in out


def test_project_dir_text_failed_shows_remediation(capsys: pytest.CaptureFixture[str]) -> None:
    report = _project_dir_report(
        results=(_project_dir_case_result(matched=False),),
    )
    _print_project_dir_report(report, "text")
    out = capsys.readouterr().out
    assert "diverged" in out
    assert "derive_project_dir" in out


def test_project_dir_text_collision_summary(capsys: pytest.CaptureFixture[str]) -> None:
    report = _project_dir_report(
        results=(
            _project_dir_case_result(suffix="a-b", group="abc"),
            _project_dir_case_result(suffix="a/b", group="abc"),
        ),
        verified_groups=("abc",),
    )
    _print_project_dir_report(report, "text")
    out = capsys.readouterr().out
    assert "collision groups declared" in out
    assert "abc" in out


def test_project_dir_json_round_trips(capsys: pytest.CaptureFixture[str]) -> None:
    report = _project_dir_report(
        results=(_project_dir_case_result(),),
    )
    _print_project_dir_report(report, "json")
    payload = json.loads(capsys.readouterr().out)
    assert payload["test_root"] == "/tmp/test-root"
    assert payload["claude_version"] == "1.0.0"
    assert len(payload["results"]) == 1
    assert payload["results"][0]["matched"] is True


# ---- _print_hook_timing_report -----------------------------------------


def _hook_timing_report(
    *,
    claude_present: bool = True,
    probes: tuple[HookTimingProbeResult, ...] = (),
) -> HookTimingHarnessReport:
    return HookTimingHarnessReport(
        workspace=Path("/tmp/ht-ws"),
        claude_present=claude_present,
        claude_version="1.0.0",
        probes=probes,
        claude_returncode_combined=0,
        claude_returncode_timeout=0,
        claude_stderr_excerpt_combined="",
        claude_stderr_excerpt_timeout="",
    )


def test_hook_timing_text_no_claude(capsys: pytest.CaptureFixture[str]) -> None:
    report = _hook_timing_report(claude_present=False)
    _print_hook_timing_report(report, "text")
    out = capsys.readouterr().out
    assert "`claude` not found" in out


def test_hook_timing_text_passed(capsys: pytest.CaptureFixture[str]) -> None:
    report = _hook_timing_report(
        probes=(
            HookTimingProbeResult(
                name="combined",
                passed=True,
                detail="ran",
                findings={"order": "A,B"},
            ),
        )
    )
    _print_hook_timing_report(report, "text")
    out = capsys.readouterr().out
    assert "combined" in out
    assert "ADR-0023" in out
    assert "#57800" in out
    # Findings whitelist: 'order' is shown
    assert "order" in out


def test_hook_timing_text_filters_noisy_findings(capsys: pytest.CaptureFixture[str]) -> None:
    """Per the helper, noisy findings keys are hidden in text mode."""
    report = _hook_timing_report(
        probes=(
            HookTimingProbeResult(
                name="combined",
                passed=True,
                detail="ran",
                findings={
                    "raw_output": "lots of bytes here",
                    "order": "A,B",
                },
            ),
        )
    )
    _print_hook_timing_report(report, "text")
    out = capsys.readouterr().out
    assert "order" in out
    # Noisy key explicitly suppressed in text mode
    assert "lots of bytes here" not in out


def test_hook_timing_json_round_trips(capsys: pytest.CaptureFixture[str]) -> None:
    report = _hook_timing_report(
        probes=(
            HookTimingProbeResult(
                name="combined",
                passed=True,
                detail="ran",
                findings={"raw_output": "shown in json"},
            ),
        )
    )
    _print_hook_timing_report(report, "json")
    payload = json.loads(capsys.readouterr().out)
    assert payload["workspace"] == "/tmp/ht-ws"
    assert payload["probes"][0]["findings"]["raw_output"] == "shown in json"


# ---- verify-cc-hooks --check-only --------------------------------------


def test_verify_cc_hooks_check_only_claude_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Monkeypatched `shutil.which` so the test is portable across hosts."""
    monkeypatch.setattr("maury.empirical_tests.shutil.which", lambda _name: None)
    runner = CliRunner()
    result = runner.invoke(main, ["verify-cc-hooks", "--check-only"])
    assert result.exit_code == 1
    assert "`claude` not found on PATH" in result.output


def test_verify_cc_hooks_check_only_claude_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("maury.empirical_tests.shutil.which", lambda _name: "/usr/local/bin/claude")
    runner = CliRunner()
    result = runner.invoke(main, ["verify-cc-hooks", "--check-only"])
    assert result.exit_code == 0
    assert "`claude` is present on PATH" in result.output
