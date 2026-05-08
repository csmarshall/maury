"""Unit tests for the empirical-test harness.

The harness's `run()` function shells out to `claude`, which we don't
exercise here. Everything else — settings construction, per-probe
evaluation, and the contract of HarnessReport.passed — is pure-Python
and worth pinning.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from maury.empirical_tests import (
    COMMENT_MARKER,
    HarnessReport,
    ProbeResult,
    _build_settings,
    _evaluate_comment,
    _evaluate_env,
    _evaluate_io,
    _parse_env_output,
    _write_probe_scripts,
    claude_present,
)

# ---- _write_probe_scripts ------------------------------------------------


def _make_scripts(tmp_path: Path) -> tuple[dict[str, Path], Path]:
    """Test helper: materialize probe scripts into a fresh tmp tree."""
    scripts_dir = tmp_path / "scripts"
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    scripts = _write_probe_scripts(scripts_dir, out_dir)
    return scripts, out_dir


def test_write_probe_scripts_creates_three_executables(tmp_path: Path) -> None:
    scripts, _ = _make_scripts(tmp_path)
    assert set(scripts.keys()) == {"comment", "env", "io"}
    for path in scripts.values():
        assert path.exists()
        # Owner-execute bit must be set so the hook can run them.
        assert path.stat().st_mode & 0o100


def test_probe_scripts_are_posix_sh_shebang(tmp_path: Path) -> None:
    scripts, _ = _make_scripts(tmp_path)
    for path in scripts.values():
        first_line = path.read_text().splitlines()[0]
        assert first_line == "#!/bin/sh"


# ---- _build_settings -----------------------------------------------------


def test_build_settings_has_three_post_tool_use_probes(tmp_path: Path) -> None:
    scripts, out_dir = _make_scripts(tmp_path)
    settings = _build_settings(out_dir, scripts)

    assert "hooks" in settings
    assert "PostToolUse" in settings["hooks"]
    groups = settings["hooks"]["PostToolUse"]
    assert len(groups) == 1
    group = groups[0]
    assert group["matcher"] == "*"
    assert len(group["hooks"]) == 3, "expected three probe hooks"
    assert all(h["type"] == "command" for h in group["hooks"])


def test_comment_probe_includes_marker(tmp_path: Path) -> None:
    scripts, out_dir = _make_scripts(tmp_path)
    settings = _build_settings(out_dir, scripts)
    comment_probe = settings["hooks"]["PostToolUse"][0]["hooks"][0]
    assert COMMENT_MARKER in comment_probe["command"]
    # The marker must be a trailing shell comment, not a flag or arg.
    assert comment_probe["command"].rstrip().endswith(COMMENT_MARKER)


def test_settings_round_trips_through_json(tmp_path: Path) -> None:
    """Settings must be JSON-serializable so they can land in settings.json."""
    scripts, out_dir = _make_scripts(tmp_path)
    settings = _build_settings(out_dir, scripts)
    encoded = json.dumps(settings)
    decoded = json.loads(encoded)
    assert decoded == settings


# ---- _parse_env_output ---------------------------------------------------


def test_parse_env_output_basic() -> None:
    text = "path=/usr/bin\nhome=/Users/x\npwd=/tmp\n"
    parsed = _parse_env_output(text)
    assert parsed == {"path": "/usr/bin", "home": "/Users/x", "pwd": "/tmp"}


def test_parse_env_output_handles_equals_in_value() -> None:
    """Values containing `=` (e.g., URLs with query strings) preserve them."""
    text = "endpoint=https://x.example.com/?a=1&b=2\n"
    parsed = _parse_env_output(text)
    assert parsed["endpoint"] == "https://x.example.com/?a=1&b=2"


def test_parse_env_output_skips_lines_without_equals() -> None:
    text = "path=/usr/bin\n# comment\n\nhome=/x\n"
    parsed = _parse_env_output(text)
    assert parsed == {"path": "/usr/bin", "home": "/x"}


# ---- _evaluate_comment ---------------------------------------------------


def test_comment_eval_passes_when_probe_writes_expected_content(
    tmp_path: Path,
) -> None:
    out = tmp_path / "comment.out"
    out.write_text("ran")

    result = _evaluate_comment(out)

    assert result.passed
    assert result.name == "comment_stripping"
    assert "marker scheme is safe" in result.detail


def test_comment_eval_fails_when_probe_file_missing(tmp_path: Path) -> None:
    out = tmp_path / "absent.out"

    result = _evaluate_comment(out)

    assert not result.passed
    assert "ADR-0023's marker scheme is at risk" in result.detail


def test_comment_eval_fails_on_unexpected_content(tmp_path: Path) -> None:
    out = tmp_path / "comment.out"
    out.write_text("garbled output")

    result = _evaluate_comment(out)

    assert not result.passed
    assert "garbled output" in repr(result.captured)


# ---- _evaluate_env -------------------------------------------------------


def test_env_eval_passes_with_path_and_home(tmp_path: Path) -> None:
    out = tmp_path / "env.out"
    out.write_text(
        "path=/usr/local/bin:/usr/bin\nhome=/Users/probe\npwd=/tmp/x\nhook_event=unset\nproject_dir=/tmp/x\n"
    )

    result = _evaluate_env(out)

    assert result.passed
    assert result.captured["path"] == "/usr/local/bin:/usr/bin"
    assert result.captured["home"] == "/Users/probe"


def test_env_eval_fails_on_missing_file(tmp_path: Path) -> None:
    result = _evaluate_env(tmp_path / "absent.out")
    assert not result.passed


def test_env_eval_fails_when_path_or_home_missing(tmp_path: Path) -> None:
    out = tmp_path / "env.out"
    out.write_text("path=\nhome=\n")

    result = _evaluate_env(out)

    assert not result.passed
    assert "absolute-path requirement" in result.detail


# ---- _evaluate_io --------------------------------------------------------


def test_io_eval_passes_when_file_was_written(tmp_path: Path) -> None:
    io_dir = tmp_path / "maury-state-analog"
    io_dir.mkdir()
    out = io_dir / "io.out"
    out.write_text("wrote-from-hook")

    result = _evaluate_io(out)

    assert result.passed
    assert "claude-writes.jsonl" in result.detail


def test_io_eval_fails_when_directory_missing(tmp_path: Path) -> None:
    out = tmp_path / "missing-dir" / "io.out"

    result = _evaluate_io(out)

    assert not result.passed
    assert "ADR-0017 / ADR-0025 are at risk" in result.detail


def test_io_eval_fails_when_file_missing(tmp_path: Path) -> None:
    io_dir = tmp_path / "maury-state-analog"
    io_dir.mkdir()
    out = io_dir / "io.out"
    # directory exists; file does not

    result = _evaluate_io(out)

    assert not result.passed
    assert "directory was created but file write" in result.detail


# ---- HarnessReport.passed contract ---------------------------------------


def test_harness_report_passes_only_when_all_probes_pass(tmp_path: Path) -> None:
    pass_probe = ProbeResult(name="a", passed=True, detail="")
    fail_probe = ProbeResult(name="b", passed=False, detail="")

    all_pass = HarnessReport(
        workspace=tmp_path,
        claude_invoked=True,
        claude_returncode=0,
        claude_stderr_excerpt="",
        probes=(pass_probe, pass_probe),
    )
    assert all_pass.passed

    one_fail = HarnessReport(
        workspace=tmp_path,
        claude_invoked=True,
        claude_returncode=0,
        claude_stderr_excerpt="",
        probes=(pass_probe, fail_probe),
    )
    assert not one_fail.passed


def test_harness_report_fails_if_claude_was_not_invoked(tmp_path: Path) -> None:
    report = HarnessReport(
        workspace=tmp_path,
        claude_invoked=False,
        claude_returncode=None,
        claude_stderr_excerpt="claude not on PATH",
        probes=(),
    )
    assert not report.passed


# ---- claude_present ------------------------------------------------------


def test_claude_present_returns_bool() -> None:
    """Doesn't matter what it returns in test env, just must be a bool."""
    assert isinstance(claude_present(), bool)


# ---- Frozen-dataclass invariant ------------------------------------------


def test_probe_result_is_frozen() -> None:
    p = ProbeResult(name="x", passed=True, detail="ok")
    with pytest.raises(Exception):  # noqa: B017 — FrozenInstanceError on dataclasses.FrozenInstanceError
        # setattr to avoid `# type: ignore[misc]` — the runtime failure
        # is the assertion, mypy doesn't need to be involved.
        setattr(p, "passed", False)


def test_harness_report_is_frozen(tmp_path: Path) -> None:
    r = HarnessReport(
        workspace=tmp_path,
        claude_invoked=False,
        claude_returncode=None,
        claude_stderr_excerpt="",
        probes=(),
    )
    with pytest.raises(Exception):  # noqa: B017
        setattr(r, "claude_invoked", True)
