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
    DEFAULT_PROJECT_DIR_CORPUS,
    HarnessReport,
    HookTimingHarnessReport,
    HookTimingProbeResult,
    ProbeResult,
    ProjectDirCase,
    ProjectDirCaseResult,
    ProjectDirHarnessReport,
    _build_settings,
    _evaluate_combined_probe,
    _evaluate_comment,
    _evaluate_env,
    _evaluate_io,
    _evaluate_timeout_probe,
    _parse_combined_output,
    _parse_env_output,
    _write_probe_scripts,
    claude_present,
    derive_project_dir,
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
        # Frozen-mutation invariant test: the runtime failure IS the
        # assertion. The `[misc]` ignore documents that we know mypy
        # would catch this statically — and that's exactly what we want.
        p.passed = False  # type: ignore[misc]


def test_harness_report_is_frozen(tmp_path: Path) -> None:
    r = HarnessReport(
        workspace=tmp_path,
        claude_invoked=False,
        claude_returncode=None,
        claude_stderr_excerpt="",
        probes=(),
    )
    with pytest.raises(Exception):  # noqa: B017
        # Frozen-mutation invariant test (see test_probe_result_is_frozen).
        r.claude_invoked = True  # type: ignore[misc]


# =========================================================================
# derive_project_dir — pure-Python predictor for Claude Code's projects-dir
# naming algorithm. The expected outputs below were empirically observed
# on 2026-05-13 against claude 2.1.140 / macOS 14.5; see
# `cc-contract:project-directory-derivation` in docs/claude-code-contract.md
# and anthropics/claude-code#54865 for the canonical reference.
# =========================================================================


def test_derive_keeps_ascii_alphanumerics(tmp_path: Path) -> None:
    cwd = tmp_path / "AlphaNum123"
    cwd.mkdir()
    out = derive_project_dir(cwd)
    # tmp_path varies between runs; the trailing component is what we care about.
    assert out.endswith("-AlphaNum123")


def test_derive_substitutes_punctuation_with_hyphen(tmp_path: Path) -> None:
    # All these non-alnum punctuation chars should each become a single hyphen.
    for suffix, expected_tail in [
        ("has.dot", "-has-dot"),
        ("under_score", "-under-score"),
        ("with+plus", "-with-plus"),
        ("with@at", "-with-at"),
        ("two..dots", "-two--dots"),
    ]:
        cwd = tmp_path / suffix
        cwd.mkdir()
        assert derive_project_dir(cwd).endswith(expected_tail), suffix


def test_derive_preserves_existing_hyphens(tmp_path: Path) -> None:
    cwd = tmp_path / "dash-already-here"
    cwd.mkdir()
    assert derive_project_dir(cwd).endswith("-dash-already-here")


def test_derive_substitutes_spaces(tmp_path: Path) -> None:
    # Each space is its own hyphen; consecutive spaces are NOT collapsed.
    cwd = tmp_path / "multi   spaces"
    cwd.mkdir()
    assert derive_project_dir(cwd).endswith("-multi---spaces")


def test_derive_collision_pair_produces_same_name(tmp_path: Path) -> None:
    """The load-bearing non-injectivity property. `a-b-c` and `a/b/c` collide."""
    hyphen_form = tmp_path / "a-b-c"
    slash_form = tmp_path / "a" / "b" / "c"
    hyphen_form.mkdir()
    slash_form.mkdir(parents=True, exist_ok=True)
    assert derive_project_dir(hyphen_form) == derive_project_dir(slash_form)


def test_derive_substitutes_bmp_non_ascii_one_per_codepoint(tmp_path: Path) -> None:
    # 'é' is BMP (U+00E9); one codepoint → one hyphen.
    cwd = tmp_path / "café"
    cwd.mkdir()
    assert derive_project_dir(cwd).endswith("-caf-")

    # Three CJK characters, all BMP; three codepoints → three hyphens.
    cwd2 = tmp_path / "日本語"
    cwd2.mkdir()
    assert derive_project_dir(cwd2).endswith("----")  # -<3 hyphens for 日本語>


def test_derive_non_bmp_emoji_becomes_two_hyphens(tmp_path: Path) -> None:
    """Non-BMP codepoints (e.g., 🚀 U+1F680) are surrogate pairs in UTF-16;
    each surrogate is non-alnum and substitutes to a hyphen, producing TWO
    hyphens per non-BMP codepoint. This is the JS-runtime fingerprint.
    """
    cwd = tmp_path / "rocket-🚀"
    cwd.mkdir()
    # Pre-emoji: "rocket-" → "-rocket-"; emoji → "--"; total trailing: "-rocket---"
    assert derive_project_dir(cwd).endswith("-rocket---")


def test_derive_resolves_symlinks(tmp_path: Path) -> None:
    """`Path.resolve()` follows symlinks before substitution, so two paths
    pointing at the same target via different symlink chains produce the
    same project-dir name.
    """
    target = tmp_path / "real-target"
    target.mkdir()
    link = tmp_path / "via-symlink"
    link.symlink_to(target)
    assert derive_project_dir(target) == derive_project_dir(link)


def test_derive_is_deterministic(tmp_path: Path) -> None:
    """Same input → same output across repeated calls."""
    cwd = tmp_path / "stable"
    cwd.mkdir()
    first = derive_project_dir(cwd)
    second = derive_project_dir(cwd)
    third = derive_project_dir(cwd)
    assert first == second == third


def test_derive_starts_with_hyphen_for_absolute_paths(tmp_path: Path) -> None:
    """Resolved absolute paths start with `/`, which is non-alnum and
    becomes the leading `-`. Matches the observed project-dir naming
    convention (`-Users-charles-…`, `-private-var-folders-…`, etc.).
    """
    cwd = tmp_path / "leading-check"
    cwd.mkdir()
    assert derive_project_dir(cwd).startswith("-")


# ---- DEFAULT_PROJECT_DIR_CORPUS contract -------------------------------


def test_default_corpus_contains_collision_pair() -> None:
    """The corpus MUST include a collision pair so the harness can verify
    the non-injectivity property, which is load-bearing for mining
    correctness analysis.
    """
    groups: dict[str, list[ProjectDirCase]] = {}
    for case in DEFAULT_PROJECT_DIR_CORPUS:
        if case.collision_group:
            groups.setdefault(case.collision_group, []).append(case)
    assert any(len(members) >= 2 for members in groups.values()), (
        "DEFAULT_PROJECT_DIR_CORPUS must contain at least one collision_group "
        "with 2+ members for the verifier to exercise the non-injectivity case"
    )


def test_default_corpus_covers_required_shapes() -> None:
    """Sanity check: the default corpus exercises every shape the algorithm
    treats specially (ASCII punctuation, BMP non-ASCII, non-BMP, mixed case).
    """
    suffixes = [c.cwd_suffix for c in DEFAULT_PROJECT_DIR_CORPUS]
    # Mixed case
    assert any(any(ch.isupper() for ch in s) and any(ch.islower() for ch in s) for s in suffixes)
    # BMP non-ASCII (anything outside ASCII but inside BMP)
    assert any(any(0x80 <= ord(ch) <= 0xFFFF for ch in s) for s in suffixes)
    # Non-BMP (emoji or similar)
    assert any(any(ord(ch) > 0xFFFF for ch in s) for s in suffixes)
    # Punctuation variety
    punct = {".", "_", "+", "@", " "}
    found_punct = set()
    for s in suffixes:
        for ch in s:
            if ch in punct:
                found_punct.add(ch)
    assert len(found_punct) >= 3, f"corpus only covers punct {found_punct}; expected ≥3"


def test_project_dir_case_is_frozen() -> None:
    c = ProjectDirCase(cwd_suffix="x", description="x")
    with pytest.raises(Exception):  # noqa: B017
        c.cwd_suffix = "y"  # type: ignore[misc]


def test_project_dir_harness_report_passed_requires_claude(tmp_path: Path) -> None:
    """If claude wasn't present, the harness can't have verified anything."""
    report = ProjectDirHarnessReport(
        test_root=tmp_path,
        claude_present=False,
        claude_version=None,
        results=(),
        collision_groups_verified=(),
        new_project_dirs=(),
    )
    assert not report.passed


def test_project_dir_harness_report_passed_requires_all_matched(tmp_path: Path) -> None:
    """One mismatch is enough to fail the whole report."""
    case_a = ProjectDirCase(cwd_suffix="a", description="a")
    case_b = ProjectDirCase(cwd_suffix="b", description="b")
    match = ProjectDirCaseResult(
        case=case_a,
        cwd=tmp_path / "a",
        predicted_dir_name="-a",
        observed_dir_names=("-a",),
        matched=True,
        detail="ok",
    )
    miss = ProjectDirCaseResult(
        case=case_b,
        cwd=tmp_path / "b",
        predicted_dir_name="-b",
        observed_dir_names=("-c",),
        matched=False,
        detail="diverged",
    )
    report = ProjectDirHarnessReport(
        test_root=tmp_path,
        claude_present=True,
        claude_version="2.1.140 (Claude Code)",
        results=(match, miss),
        collision_groups_verified=(),
        new_project_dirs=("-a", "-c"),
    )
    assert not report.passed


def test_project_dir_harness_report_passed_requires_collision_groups_verified(
    tmp_path: Path,
) -> None:
    """If the corpus declares a collision group but the harness didn't
    observe the group's members bucketing together, the report fails."""
    case = ProjectDirCase(cwd_suffix="x", description="x", collision_group="alpha")
    match = ProjectDirCaseResult(
        case=case,
        cwd=tmp_path / "x",
        predicted_dir_name="-x",
        observed_dir_names=("-x",),
        matched=True,
        detail="ok",
    )
    # Declared group `alpha`, but `collision_groups_verified` is empty.
    report = ProjectDirHarnessReport(
        test_root=tmp_path,
        claude_present=True,
        claude_version="2.1.140 (Claude Code)",
        results=(match,),
        collision_groups_verified=(),
        new_project_dirs=("-x",),
    )
    assert not report.passed

    # Same report but with the group verified → passes.
    report_passing = ProjectDirHarnessReport(
        test_root=tmp_path,
        claude_present=True,
        claude_version="2.1.140 (Claude Code)",
        results=(match,),
        collision_groups_verified=("alpha",),
        new_project_dirs=("-x",),
    )
    assert report_passing.passed


# =========================================================================
# Hook-timing probe — pure parsing + evaluation logic.
# The harness's run() function shells out to `claude`; not exercised here.
# Everything below verifies the parsers + per-probe evaluators against
# synthetic raw outputs derived from the actual empirical runs (2026-05-13,
# claude 2.1.141, macOS — see commit message for the findings).
# =========================================================================


# ---- combined-probe parser + evaluator ---------------------------------


def test_parse_combined_output_basic() -> None:
    assert _parse_combined_output("A:start\nB\nC\nD\nA:end\n") == [
        "A:start",
        "B",
        "C",
        "D",
        "A:end",
    ]


def test_parse_combined_output_strips_blank_lines() -> None:
    assert _parse_combined_output("A:start\n\nB\n\n") == ["A:start", "B"]


_EXPECTED_ORDER = ("A:start", "A:end", "B", "C", "D")


def test_combined_probe_sequential_with_short_circuit(tmp_path: Path) -> None:
    """ADR-0023's safer reading: hooks run sequentially in declaration
    order; exit 2 short-circuits subsequent hooks."""
    out = tmp_path / "combined.out"
    out.write_text("A:start\nA:end\nB\nC\n")  # no D — short-circuit on exit 2
    result = _evaluate_combined_probe(out, expected_order=_EXPECTED_ORDER)
    assert result.passed
    assert result.findings["synchronous_execution"] is True
    assert result.findings["execution_ordering"] == "declaration"
    assert result.findings["short_circuit_on_exit_2"] is True
    assert result.findings["d_marker_present"] is False


def test_combined_probe_sequential_no_short_circuit(tmp_path: Path) -> None:
    out = tmp_path / "combined.out"
    out.write_text("A:start\nA:end\nB\nC\nD\n")  # D present — no short-circuit
    result = _evaluate_combined_probe(out, expected_order=_EXPECTED_ORDER)
    assert result.passed
    assert result.findings["synchronous_execution"] is True
    assert result.findings["short_circuit_on_exit_2"] is False
    assert result.findings["d_marker_present"] is True


def test_combined_probe_ordered_fire_and_forget(tmp_path: Path) -> None:
    """The empirical model observed 2026-05-13: hooks fire in declaration
    order but each fires without waiting for the previous to complete.
    Markers B, C, D appear between A:start and A:end."""
    out = tmp_path / "combined.out"
    out.write_text("A:start\nB\nC\nD\nA:end\n")
    result = _evaluate_combined_probe(out, expected_order=_EXPECTED_ORDER)
    assert result.passed
    assert result.findings["synchronous_execution"] is False
    # Ordering is still "declaration" because B/C/D fire in registration order.
    assert result.findings["short_circuit_on_exit_2"] is False
    assert result.findings["d_marker_present"] is True


def test_combined_probe_missing_output_file(tmp_path: Path) -> None:
    result = _evaluate_combined_probe(tmp_path / "absent.out", expected_order=_EXPECTED_ORDER)
    assert not result.passed
    assert "not found" in result.detail


def test_combined_probe_empty_output_file(tmp_path: Path) -> None:
    out = tmp_path / "combined.out"
    out.write_text("")
    result = _evaluate_combined_probe(out, expected_order=_EXPECTED_ORDER)
    assert not result.passed
    assert "empty" in result.detail


def test_combined_probe_missing_a_markers(tmp_path: Path) -> None:
    """If A's start/end markers are absent, the probe can't classify
    sync — fail with a clear detail message."""
    out = tmp_path / "combined.out"
    out.write_text("B\nC\nD\n")
    result = _evaluate_combined_probe(out, expected_order=_EXPECTED_ORDER)
    assert not result.passed
    assert "A:start or A:end missing" in result.detail


# ---- timeout-probe evaluator -------------------------------------------


def test_timeout_probe_hook_completed(tmp_path: Path) -> None:
    out = tmp_path / "timeout.out"
    out.write_text("start=1000\nend=1010\n")
    result = _evaluate_timeout_probe(out, hook_sleep_seconds=10, elapsed_seconds=12.0)
    assert result.passed
    assert result.findings["hook_completed"] is True
    assert result.findings["hook_duration_seconds"] == 10
    assert "no default timeout below" in result.detail


def test_timeout_probe_hook_killed_mid_sleep(tmp_path: Path) -> None:
    """If only the start= line appears, claude killed the hook before
    the sleep completed — default timeout < hook_sleep_seconds."""
    out = tmp_path / "timeout.out"
    out.write_text("start=1000\n")
    result = _evaluate_timeout_probe(out, hook_sleep_seconds=30, elapsed_seconds=20.0)
    assert result.passed
    assert result.findings["hook_completed"] is False
    assert result.findings["hook_end_timestamp"] is None
    assert "default timeout < 30s" in result.detail


def test_timeout_probe_missing_output(tmp_path: Path) -> None:
    result = _evaluate_timeout_probe(tmp_path / "absent.out", hook_sleep_seconds=30, elapsed_seconds=5.0)
    assert not result.passed


def test_timeout_probe_no_start_line(tmp_path: Path) -> None:
    out = tmp_path / "timeout.out"
    out.write_text("garbage\n")
    result = _evaluate_timeout_probe(out, hook_sleep_seconds=30, elapsed_seconds=5.0)
    assert not result.passed
    assert "didn't write a start timestamp" in result.detail


# ---- HookTimingHarnessReport contract -----------------------------------


def test_hook_timing_report_requires_claude(tmp_path: Path) -> None:
    report = HookTimingHarnessReport(
        workspace=tmp_path,
        claude_present=False,
        claude_version=None,
        probes=(),
        claude_returncode_combined=None,
        claude_returncode_timeout=None,
        claude_stderr_excerpt_combined="not on PATH",
        claude_stderr_excerpt_timeout="not on PATH",
    )
    assert not report.passed


def test_hook_timing_report_requires_all_probes_passed(tmp_path: Path) -> None:
    pass_ = HookTimingProbeResult(name="x", passed=True, detail="ok")
    fail_ = HookTimingProbeResult(name="y", passed=False, detail="bad")
    report = HookTimingHarnessReport(
        workspace=tmp_path,
        claude_present=True,
        claude_version="2.1.141",
        probes=(pass_, fail_),
        claude_returncode_combined=0,
        claude_returncode_timeout=0,
        claude_stderr_excerpt_combined="",
        claude_stderr_excerpt_timeout="",
    )
    assert not report.passed

    report_passing = HookTimingHarnessReport(
        workspace=tmp_path,
        claude_present=True,
        claude_version="2.1.141",
        probes=(pass_, pass_),
        claude_returncode_combined=0,
        claude_returncode_timeout=0,
        claude_stderr_excerpt_combined="",
        claude_stderr_excerpt_timeout="",
    )
    assert report_passing.passed


def test_hook_timing_probe_result_is_frozen() -> None:
    p = HookTimingProbeResult(name="x", passed=True, detail="ok")
    with pytest.raises(Exception):  # noqa: B017
        p.passed = False  # type: ignore[misc]
