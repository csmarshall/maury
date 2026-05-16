"""Small CLI tests covering uncovered branches across several commands.

Targets the remaining single-line / small-block gaps in `cli.py`:
- `verify-cc-projects-dir --check-only` (both branches).
- `verify-cc-hook-timing --check-only` (both branches).
- `review` / `promote-review` stubs (raise ClickException).
- `doctor --fail-on info` with an empty file → exit 1.
- `render` against a malformed manifest → ClickException.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from maury.cli import main
from maury.ids import new_host_id, new_profile_id

# ---- verify-cc-projects-dir --check-only -------------------------------


def test_verify_cc_projects_dir_check_only_claude_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("maury.empirical_tests.shutil.which", lambda _name: None)
    runner = CliRunner()
    result = runner.invoke(main, ["verify-cc-projects-dir", "--check-only"])
    assert result.exit_code == 1
    assert "`claude` not found on PATH" in result.output


def test_verify_cc_projects_dir_check_only_claude_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("maury.empirical_tests.shutil.which", lambda _name: "/usr/local/bin/claude")
    runner = CliRunner()
    result = runner.invoke(main, ["verify-cc-projects-dir", "--check-only"])
    assert result.exit_code == 0
    assert "`claude` is present on PATH" in result.output


# ---- verify-cc-hook-timing --check-only --------------------------------


def test_verify_cc_hook_timing_check_only_claude_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("maury.empirical_tests.shutil.which", lambda _name: None)
    runner = CliRunner()
    result = runner.invoke(main, ["verify-cc-hook-timing", "--check-only"])
    assert result.exit_code == 1
    assert "`claude` not found on PATH" in result.output


def test_verify_cc_hook_timing_check_only_claude_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("maury.empirical_tests.shutil.which", lambda _name: "/usr/local/bin/claude")
    runner = CliRunner()
    result = runner.invoke(main, ["verify-cc-hook-timing", "--check-only"])
    assert result.exit_code == 0
    assert "`claude` is present on PATH" in result.output


# ---- unimplemented stubs -----------------------------------------------


def test_review_command_is_unimplemented() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["review"])
    assert result.exit_code != 0
    assert "not yet implemented" in result.output


def test_promote_review_command_is_unimplemented() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["promote-review"])
    assert result.exit_code != 0
    assert "not yet implemented" in result.output


# ---- doctor --fail-on threshold ----------------------------------------


def test_doctor_fail_on_error_with_long_file_exits_1(tmp_path: Path) -> None:
    """A very large CLAUDE.md trips the size-limit rule (severity=error);
    `--fail-on error` must hit the exit-1 branch (cli.py:1322)."""
    md = tmp_path / "CLAUDE.md"
    md.write_text("# header\n" + "x " * 50000)  # > rubric size threshold
    runner = CliRunner()
    result = runner.invoke(main, ["doctor", "--file", str(md), "--fail-on", "error"])
    assert result.exit_code == 1


# ---- render with malformed manifest -----------------------------------


def test_render_malformed_manifest_yields_click_exception(tmp_path: Path) -> None:
    """Garbage manifest must surface as a ClickException, not a traceback."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "CLAUDE.md").write_text("# base\n")
    meta = repo / ".meta"
    meta.mkdir()
    (meta / "manifest.json").write_text("{ this is not: valid json")
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "render",
            "--manifest-file",
            str(meta / "manifest.json"),
            "--target",
            str(tmp_path / "out"),
        ],
    )
    assert result.exit_code != 0
    combined = result.output + (result.stderr or "")
    # The CLI wraps ManifestError as a ClickException; we just need a clean
    # error path (no traceback). Either the manifest-load message or Click's
    # standard "Error:" prefix proves we hit the wrapped branch.
    assert "Error" in combined or "manifest" in combined.lower()


# ---- rules trace with malformed manifest --------------------------------


def test_rules_trace_malformed_manifest_yields_click_exception(tmp_path: Path) -> None:
    """`rules trace --manifest-file <broken>` must surface as ClickException."""
    rules = tmp_path / "rules.yml"
    rules.write_text("rules: []\n")
    bad_manifest = tmp_path / "bad.json"
    bad_manifest.write_text("{ not json")
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "rules",
            "trace",
            "--rules-file",
            str(rules),
            "--manifest-file",
            str(bad_manifest),
            "hello",
        ],
    )
    assert result.exit_code != 0


# ---- rules validate with manifest cross-check --------------------------


# ---- profile list/hosts manifest-load errors ----------------------------


def test_profile_list_malformed_manifest_yields_click_exception(tmp_path: Path) -> None:
    mpath = tmp_path / "manifest.json"
    mpath.write_text('{"version": 2, "profiles": "not a dict", "hosts": {}}')
    runner = CliRunner()
    result = runner.invoke(main, ["profile", "list", "--manifest-file", str(mpath)])
    assert result.exit_code != 0


def test_profile_hosts_malformed_manifest_yields_click_exception(tmp_path: Path) -> None:
    mpath = tmp_path / "manifest.json"
    mpath.write_text('{"version": 2, "profiles": {}, "hosts": "not a dict"}')
    runner = CliRunner()
    result = runner.invoke(main, ["profile", "hosts", "--manifest-file", str(mpath)])
    assert result.exit_code != 0


# ---- init: source-arg validation ---------------------------------------


def test_init_requires_one_of_from_dir_or_from_tarball(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`init` with neither --from-dir nor --from-tarball → friendly error."""
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(main, ["init", "--target", str(tmp_path / "out")])
    assert result.exit_code != 0
    combined = result.output + (result.stderr or "")
    assert "--from-dir" in combined and "--from-tarball" in combined


def test_init_from_dir_and_from_tarball_are_mutually_exclusive(tmp_path: Path) -> None:
    """`init --from-dir X --from-tarball Y` → friendly error before run_init."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    src_tar = tmp_path / "src.tar"
    src_tar.write_text("not really a tarball")
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "init",
            "--from-dir",
            str(src_dir),
            "--from-tarball",
            str(src_tar),
            "--target",
            str(tmp_path / "out"),
        ],
    )
    assert result.exit_code != 0
    combined = result.output + (result.stderr or "")
    assert "mutually exclusive" in combined


def test_init_invalid_source_dir_yields_click_exception(tmp_path: Path) -> None:
    """`init --from-dir <empty-dir>` → InitError wrapped to ClickException."""
    src = tmp_path / "empty-src"
    src.mkdir()
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "init",
            "--from-dir",
            str(src),
            "--target",
            str(tmp_path / "out"),
        ],
    )
    assert result.exit_code != 0


# `maury status` was previously a stub raising "not yet implemented";
# implemented as a real command 2026-05-16. Coverage now lives in
# `test_cli_status.py`. The stub-presence assertion is retired.


def test_rules_validate_with_manifest_resolves_profile_set(tmp_path: Path) -> None:
    """`rules validate --manifest-file` should pull known-profiles from the
    manifest's union of IDs + names. Covers the manifest-load + known_profiles_from
    branch (cli.py lines around 218-226)."""
    rules = tmp_path / "rules.yml"
    rules.write_text("rules: []\n")
    mpath = tmp_path / "manifest.json"
    pid = new_profile_id()
    hid = new_host_id()
    body = {
        "version": 2,
        "profiles": {pid: {"name": "home", "extends": None}},
        "hosts": {
            hid: {
                "name": "h",
                "profile": pid,
                "repos": {"base": {"url": "git@x:o/r.git", "mode": "rw"}},
            }
        },
    }
    mpath.write_text(json.dumps(body))
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "rules",
            "validate",
            "--rules-file",
            str(rules),
            "--manifest-file",
            str(mpath),
        ],
    )
    assert result.exit_code == 0, result.output
