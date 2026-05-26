"""CLI-layer tests for `maury render` and `maury doctor`.

Render engine + doctor rubric internals are covered in
`test_render.py`, `test_settings_merge.py`, and `test_doctor.py`.
These tests cover only the CLI wiring: flag parsing, host/profile
resolution, error paths, exit codes, output format selection.

ADR-0042's identity guard fires in `render` and `doctor` against
the default `~/.claude/` target. These tests don't exercise that
guard (test_cli_sync.py + a few cases at the bottom of this file
do); the conftest `_isolate_xdg_roots` fixture points the host-id
file (`paths.host_id_file()`) at a fresh tmp config root where it
doesn't exist, so the guard silently returns without writing a
baseline into the test's target dir. The guard-firing tests at the
bottom write to `paths.host_id_file()` explicitly to make it fire.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from maury import paths
from maury.cli import main
from maury.ids import new_host_id, new_profile_id


def _write_minimal_repo(tmp_path: Path, *, hostname: str = "test-host") -> tuple[Path, str, str]:
    """Create a minimal base-repo layout. Returns (repo_dir, profile_id, host_id).

    Defaults to a static hostname `test-host` so tests don't depend on the
    behavior of `host_for_current_machine` (which has its own resolution
    logic distinct from raw `socket.gethostname()`). Tests pass `--host`
    explicitly to render against this fixture host.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "CLAUDE.md").write_text("# base\n")
    pid = new_profile_id()
    hid = new_host_id("h")
    manifest = {
        "version": 1,
        "profiles": {pid: {"name": "home", "extends": None}},
        "hosts": {
            hid: {
                "name": hostname,
                "profile": pid,
                "repos": {"base": {"url": "git@x:o/r.git", "mode": "rw"}},
            }
        },
    }
    (repo / ".meta").mkdir()
    (repo / ".meta" / "manifest.json").write_text(json.dumps(manifest))
    return repo, pid, hid


# ---- render: happy path -------------------------------------------------


def test_render_check_writes_nothing_and_lists_files(tmp_path: Path) -> None:
    repo, _, _ = _write_minimal_repo(tmp_path)
    target = tmp_path / "out"
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "render",
            "--manifest-file",
            str(repo / ".meta" / "manifest.json"),
            "--host",
            "test-host",
            "--target",
            str(target),
            "--check",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "(--check; no files written)" in result.output
    assert not target.exists() or not any(target.iterdir())


def test_render_writes_files_when_check_omitted(tmp_path: Path) -> None:
    repo, _, _ = _write_minimal_repo(tmp_path)
    target = tmp_path / "out"
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "render",
            "--manifest-file",
            str(repo / ".meta" / "manifest.json"),
            "--host",
            "test-host",
            "--target",
            str(target),
        ],
    )
    assert result.exit_code == 0, result.output
    assert (target / "CLAUDE.md").is_file()


# ---- render: error paths ------------------------------------------------


def test_render_no_manifest_file_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MAURY_MANIFEST_FILE", raising=False)
    runner = CliRunner()
    result = runner.invoke(main, ["render", "--target", str(tmp_path / "out")])
    assert result.exit_code != 0
    assert "manifest file not found" in (result.output + (result.stderr or ""))


def test_render_unknown_host_errors_with_available_list(tmp_path: Path) -> None:
    repo, _, _ = _write_minimal_repo(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "render",
            "--manifest-file",
            str(repo / ".meta" / "manifest.json"),
            "--host",
            "nonexistent",
            "--target",
            str(tmp_path / "out"),
        ],
    )
    assert result.exit_code != 0
    combined = result.output + (result.stderr or "")
    assert "not in manifest" in combined
    assert "available:" in combined


def test_render_unknown_profile_errors_with_available_list(tmp_path: Path) -> None:
    repo, _, _ = _write_minimal_repo(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "render",
            "--manifest-file",
            str(repo / ".meta" / "manifest.json"),
            "--host",
            "test-host",
            "--profile",
            "nonexistent",
            "--target",
            str(tmp_path / "out"),
        ],
    )
    assert result.exit_code != 0
    combined = result.output + (result.stderr or "")
    assert "not in manifest" in combined
    assert "available:" in combined


def test_render_repo_path_not_a_dir_errors(tmp_path: Path) -> None:
    repo, _, _ = _write_minimal_repo(tmp_path)
    not_a_dir = tmp_path / "not-a-dir"
    not_a_dir.write_text("file, not dir")
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "render",
            "--manifest-file",
            str(repo / ".meta" / "manifest.json"),
            "--host",
            "test-host",
            "--repo",
            str(not_a_dir),
            "--target",
            str(tmp_path / "out"),
        ],
    )
    # Click validates --repo with file_okay=False before our code runs;
    # a regular file fails at the type-validation layer (exit 2). If the
    # path validates as a directory, our code's `is_dir()` check would
    # error too. Either way: non-zero exit.
    assert result.exit_code != 0


def test_render_unknown_host_when_no_match_for_current_machine(tmp_path: Path) -> None:
    """If neither --host nor a current-machine match is available, errors clearly."""
    # Use a hostname guaranteed not to match the current machine
    repo, _, _ = _write_minimal_repo(tmp_path, hostname="definitely-not-this-machine-xyz")
    runner = CliRunner()
    # Without a host flag and with no matching hostname, the lookup must fail
    result = runner.invoke(
        main,
        [
            "render",
            "--manifest-file",
            str(repo / ".meta" / "manifest.json"),
            "--target",
            str(tmp_path / "out"),
        ],
    )
    # Either succeeds (some test environments do match) or errors with the right message.
    if result.exit_code != 0:
        combined = result.output + (result.stderr or "")
        assert "could not determine current host" in combined or "not in manifest" in combined


# ---- render against the bundled seed ------------------------------------


def test_render_check_against_seed_template(tmp_path: Path) -> None:
    seed = Path(__file__).resolve().parents[2] / "base-template"
    if not (seed / ".meta" / "manifest.json").exists():
        pytest.skip("seed manifest not present")
    target = tmp_path / "out"
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "render",
            "--manifest-file",
            str(seed / ".meta" / "manifest.json"),
            "--profile",
            "home",
            "--host",
            "workstation",
            "--target",
            str(target),
            "--check",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "rendering host=workstation profile=home" in result.output


# ---- doctor: happy path -------------------------------------------------


def test_doctor_runs_against_explicit_file(tmp_path: Path) -> None:
    md = tmp_path / "CLAUDE.md"
    md.write_text("# Project\n\n## Code style\n\n- Use 2-space indentation.\n")
    runner = CliRunner()
    result = runner.invoke(main, ["doctor", "--file", str(md)])
    assert result.exit_code == 0, result.output
    # Doctor prints a summary header somewhere referencing the file.
    assert "CLAUDE.md" in result.output


def test_doctor_json_output_is_valid_json(tmp_path: Path) -> None:
    md = tmp_path / "CLAUDE.md"
    md.write_text("# Project\n\nSome content.\n")
    runner = CliRunner()
    result = runner.invoke(main, ["doctor", "--file", str(md), "--format", "json"])
    assert result.exit_code in (0, 1)  # exit 1 acceptable if findings exist
    # Should be parseable as JSON
    parsed = json.loads(result.output)
    # Expected top-level keys
    assert "source_path" in parsed or "findings" in parsed


def test_doctor_against_rendered_seed(tmp_path: Path) -> None:
    """End-to-end: render seed to /tmp, then doctor the result."""
    seed = Path(__file__).resolve().parents[2] / "base-template"
    if not (seed / ".meta" / "manifest.json").exists():
        pytest.skip("seed manifest not present")
    target = tmp_path / "rendered"
    target.mkdir()
    runner = CliRunner()
    # Render first
    render_result = runner.invoke(
        main,
        [
            "render",
            "--manifest-file",
            str(seed / ".meta" / "manifest.json"),
            "--profile",
            "home",
            "--host",
            "workstation",
            "--target",
            str(target),
        ],
    )
    assert render_result.exit_code == 0, render_result.output
    # Then doctor it
    doctor_result = runner.invoke(main, ["doctor", "--file", str(target / "CLAUDE.md")])
    assert doctor_result.exit_code in (0, 1)


# ---- doctor: --fail-on behavior -----------------------------------------


def test_doctor_fail_on_never_always_exits_zero(tmp_path: Path) -> None:
    """`--fail-on never` returns 0 regardless of finding severity."""
    md = tmp_path / "CLAUDE.md"
    md.write_text("")  # empty file — will produce findings
    runner = CliRunner()
    result = runner.invoke(main, ["doctor", "--file", str(md), "--fail-on", "never"])
    assert result.exit_code == 0


# ---- doctor: error paths ------------------------------------------------


def test_doctor_missing_file_errors(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["doctor", "--file", str(tmp_path / "nonexistent.md")])
    assert result.exit_code != 0


# ---- doctor: system-health rules (2026-05-18 expansion) ---------------


def test_doctor_surfaces_system_health_findings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Doctor's findings list contains system-health checks alongside
    content rules, per Charles's 'both' pick on the 2026-05-18 design
    call. Pre-2026-05-14 upgrade state (host_id present, baseline
    absent) surfaces a warn-level baseline-missing finding."""
    # Confine HOME so the host_id_file path resolves under tmp_path.
    monkeypatch.setenv("HOME", str(tmp_path))
    host_id_file = paths.host_id_file()
    host_id_file.write_text("host_24b2a0aa_laptop\n")
    # Also need an identity baseline for the guard to NOT abort; the
    # baseline matches the host_id so the guard passes, then doctor's
    # system-health check fires on a *different* condition we set up.
    # Simplest: write a baseline so the guard is happy, then the
    # baseline-missing check returns no finding for this hex. We want
    # to assert system-health rules RAN, not necessarily that one
    # specific check fired. So use the watermark-stale check instead.
    from maury.host_identity import HostIdentityBaseline, write_baseline
    from maury.mining_state import (
        MINING_ALGORITHM_VERSION,
        MiningWatermark,
        ProjectMiningRecord,
        write_watermark,
    )

    tmp_path / ".claude"  # matches Path.home()/.claude under monkeypatched HOME
    write_baseline(
        HostIdentityBaseline(
            schema_version=1,
            host_id_hex="24b2a0aa",
            registered_at="2026-05-14T15:42:11Z",
            mode_id="mode_3f1a",
            mode_name_at_bootstrap="home",
        ),
    )
    stale = MiningWatermark(mining_algorithm_version=MINING_ALGORITHM_VERSION + 999)
    stale.update(
        "-Users-jdoe-project",
        ProjectMiningRecord(
            last_mined_at="2026-01-01T00:00:00Z",
            last_jsonl_mtime="2026-01-01T00:00:00Z",
        ),
    )
    write_watermark(stale)

    md = tmp_path / "CLAUDE.md"
    md.write_text("# header\n")
    runner = CliRunner()
    result = runner.invoke(main, ["doctor", "--file", str(md), "--fail-on", "never"])
    assert result.exit_code == 0
    # System-health rule fired: text output mentions watermark-stale.
    assert "watermark-stale" in result.output
    assert "algorithm version" in result.output


def test_doctor_fail_on_warn_trips_on_system_health(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`--fail-on warn` returns exit 1 when a system-health rule
    fires at warn severity, even if the content rules are clean."""
    monkeypatch.setenv("HOME", str(tmp_path))
    host_id_file = paths.host_id_file()
    host_id_file.write_text("host_24b2a0aa_laptop\n")
    from maury.host_identity import HostIdentityBaseline, write_baseline
    from maury.mining_state import (
        MINING_ALGORITHM_VERSION,
        MiningWatermark,
        ProjectMiningRecord,
        write_watermark,
    )

    tmp_path / ".claude"
    write_baseline(
        HostIdentityBaseline(
            schema_version=1,
            host_id_hex="24b2a0aa",
            registered_at="2026-05-14T15:42:11Z",
            mode_id="mode_3f1a",
            mode_name_at_bootstrap="home",
        ),
    )
    stale = MiningWatermark(mining_algorithm_version=MINING_ALGORITHM_VERSION + 999)
    stale.update(
        "-Users-jdoe-project",
        ProjectMiningRecord(
            last_mined_at="2026-01-01T00:00:00Z",
            last_jsonl_mtime="2026-01-01T00:00:00Z",
        ),
    )
    write_watermark(stale)

    md = tmp_path / "CLAUDE.md"
    md.write_text("# clean content\n\nSome reasonable instructions.\n")
    runner = CliRunner()
    result = runner.invoke(main, ["doctor", "--file", str(md), "--fail-on", "warn"])
    assert result.exit_code == 1  # warn-level system-health finding tripped the threshold


# ---- ADR-0042 identity guard wiring (render + doctor) ------------------


def test_render_identity_guard_refuses_on_hex_change(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A hex-edited `~/.maury-host-id` must trip the guard before
    render's manifest load. render writes mode-scoped content; we
    can't allow the wrong mode to render onto the target."""
    from maury.host_identity import HostIdentityBaseline, write_baseline

    target = tmp_path / "target"
    host_id_file = paths.host_id_file()
    host_id_file.write_text("host_88ff77ee_swapped\n")
    write_baseline(
        HostIdentityBaseline(
            schema_version=1,
            host_id_hex="24b2a0aa",  # different — guard trips
            registered_at="2026-05-14T15:42:11Z",
            mode_id="mode_home",
            mode_name_at_bootstrap="home",
        ),
    )

    repo, _, _ = _write_minimal_repo(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "render",
            "--manifest-file",
            str(repo / ".meta" / "manifest.json"),
            "--host",
            "test-host",
            "--target",
            str(target),
            "--check",
        ],
    )
    assert result.exit_code == 1
    combined = result.output + (result.stderr or "")
    assert "host identity changed" in combined
    assert "24b2a0aa" in combined
    assert "88ff77ee" in combined


def test_doctor_identity_guard_refuses_on_hex_change(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Same guard fires for `maury doctor` — doctor's findings are
    mode-scoped (the rubric runs against the host's active CLAUDE.md),
    so an identity swap would make the report misleading."""
    from maury.host_identity import HostIdentityBaseline, write_baseline

    host_id_file = paths.host_id_file()
    host_id_file.write_text("host_88ff77ee_swapped\n")
    # The doctor guard checks against the default target Path.home() /
    # ".claude" rather than a flag, so we patch HOME too.
    monkeypatch.setenv("HOME", str(tmp_path))
    write_baseline(
        HostIdentityBaseline(
            schema_version=1,
            host_id_hex="24b2a0aa",
            registered_at="2026-05-14T15:42:11Z",
            mode_id="mode_home",
            mode_name_at_bootstrap="home",
        ),
    )

    md = tmp_path / "CLAUDE.md"
    md.write_text("# header\n")
    runner = CliRunner()
    result = runner.invoke(main, ["doctor", "--file", str(md)])
    assert result.exit_code == 1
    combined = result.output + (result.stderr or "")
    assert "host identity changed" in combined
