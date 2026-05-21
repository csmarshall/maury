"""Tests for `src/maury/doctor/system_health.py` — the 2026-05-18
doctor expansion (system-health checks alongside content rules)."""

from __future__ import annotations

from pathlib import Path

from maury.doctor import Severity, run_system_health_checks
from maury.host_identity import HostIdentityBaseline, write_baseline
from maury.mining_state import (
    MINING_ALGORITHM_VERSION,
    MiningWatermark,
    ProjectMiningRecord,
    write_watermark,
)

# ---- baseline-missing check --------------------------------------------


def test_no_host_id_file_no_finding(tmp_path: Path) -> None:
    """If there's no `~/.maury-host-id` at all, the baseline-missing
    check doesn't fire — there's no identity to anchor."""
    target = tmp_path / "target"
    host_id_file = tmp_path / ".maury-host-id"  # doesn't exist
    findings = run_system_health_checks(target_dir=target, host_id_file=host_id_file)
    assert all(f.check_id != "baseline-missing" for f in findings)


def test_host_id_present_baseline_present_no_finding(tmp_path: Path) -> None:
    """Normal post-init state — both files present, no finding."""
    target = tmp_path / "target"
    host_id_file = tmp_path / ".maury-host-id"
    host_id_file.write_text("host_24b2a0aa_laptop\n")
    write_baseline(
        target,
        HostIdentityBaseline(
            schema_version=1,
            host_id_hex="24b2a0aa",
            registered_at="2026-05-14T15:42:11Z",
            mode_id="mode_3f1a",
            mode_name_at_bootstrap="home",
        ),
    )
    findings = run_system_health_checks(target_dir=target, host_id_file=host_id_file)
    assert all(f.check_id != "baseline-missing" for f in findings)


def test_host_id_present_baseline_missing_warns(tmp_path: Path) -> None:
    """Pre-2026-05-14 upgrade state — host_id exists, baseline doesn't.
    The check fires with warn severity."""
    target = tmp_path / "target"
    host_id_file = tmp_path / ".maury-host-id"
    host_id_file.write_text("host_24b2a0aa_laptop\n")
    # No baseline written.

    findings = run_system_health_checks(target_dir=target, host_id_file=host_id_file)
    baseline_findings = [f for f in findings if f.check_id == "baseline-missing"]
    assert len(baseline_findings) == 1
    assert baseline_findings[0].severity == Severity.WARN
    assert "auto-create" in baseline_findings[0].message
    assert "maury sync" in baseline_findings[0].fix_suggestion


# ---- watermark-stale check ---------------------------------------------


def test_no_watermark_no_finding(tmp_path: Path) -> None:
    """First-ever-mine state — no watermark on disk, no finding."""
    target = tmp_path / "target"
    host_id_file = tmp_path / ".maury-host-id"  # doesn't matter for this check
    findings = run_system_health_checks(target_dir=target, host_id_file=host_id_file)
    assert all(f.check_id != "watermark-stale" for f in findings)


def test_current_watermark_no_finding(tmp_path: Path) -> None:
    """Normal post-mine state — watermark at the current algorithm
    version, no finding."""
    target = tmp_path / "target"
    host_id_file = tmp_path / ".maury-host-id"
    wm = MiningWatermark()  # defaults to MINING_ALGORITHM_VERSION
    wm.update(
        "-Users-jdoe-project",
        ProjectMiningRecord(
            last_mined_at="2026-05-18T14:00:00Z",
            last_jsonl_mtime="2026-05-18T13:50:00Z",
        ),
    )
    write_watermark(target, wm)
    findings = run_system_health_checks(target_dir=target, host_id_file=host_id_file)
    assert all(f.check_id != "watermark-stale" for f in findings)


def test_stale_watermark_warns(tmp_path: Path) -> None:
    """Watermark recorded under an old algorithm version → warn finding."""
    target = tmp_path / "target"
    host_id_file = tmp_path / ".maury-host-id"
    stale = MiningWatermark(mining_algorithm_version=MINING_ALGORITHM_VERSION + 999)
    stale.update(
        "-Users-jdoe-project",
        ProjectMiningRecord(
            last_mined_at="2026-01-01T00:00:00Z",
            last_jsonl_mtime="2026-01-01T00:00:00Z",
        ),
    )
    write_watermark(target, stale)

    findings = run_system_health_checks(target_dir=target, host_id_file=host_id_file)
    stale_findings = [f for f in findings if f.check_id == "watermark-stale"]
    assert len(stale_findings) == 1
    assert stale_findings[0].severity == Severity.WARN
    assert "algorithm version" in stale_findings[0].message
    assert "maury mine --full" in stale_findings[0].fix_suggestion


# ---- aggregation -------------------------------------------------------


def test_multiple_findings_aggregate(tmp_path: Path) -> None:
    """Both checks fire when both conditions hold."""
    target = tmp_path / "target"
    host_id_file = tmp_path / ".maury-host-id"
    host_id_file.write_text("host_24b2a0aa_laptop\n")
    stale = MiningWatermark(mining_algorithm_version=MINING_ALGORITHM_VERSION + 999)
    stale.update(
        "-Users-jdoe-project",
        ProjectMiningRecord(
            last_mined_at="2026-01-01T00:00:00Z",
            last_jsonl_mtime="2026-01-01T00:00:00Z",
        ),
    )
    write_watermark(target, stale)

    findings = run_system_health_checks(target_dir=target, host_id_file=host_id_file)
    check_ids = {f.check_id for f in findings}
    assert "baseline-missing" in check_ids
    assert "watermark-stale" in check_ids


def test_no_findings_on_clean_state(tmp_path: Path) -> None:
    """All-clean state — no findings."""
    target = tmp_path / "target"
    host_id_file = tmp_path / ".maury-host-id"
    findings = run_system_health_checks(target_dir=target, host_id_file=host_id_file)
    assert findings == []


# ---- focus-unreachable check (ADR-0052 §Confirmation) ------------------


def _seed_focus_manifest(tmp_path: Path) -> tuple[Path, str, str, str]:
    """Write a manifest with a personal/work tree. Returns
    (manifest_path, personal_mode_id, work_mode_id, acme_mode_id)."""
    import json

    from maury.ids import new_host_id, new_profile_id

    base_pid = new_profile_id()
    personal_pid = new_profile_id()
    acme_pid = new_profile_id()
    work_pid = new_profile_id()
    hid = new_host_id("h")

    body = {
        "version": 1,
        "profiles": {
            base_pid: {"name": "base", "extends": None},
            personal_pid: {"name": "personal", "extends": base_pid},
            acme_pid: {"name": "personal:consulting:acme", "extends": personal_pid},
            work_pid: {"name": "work", "extends": base_pid},
        },
        "hosts": {
            hid: {
                "name": "alice-laptop",
                "profile": personal_pid,
                "repos": {"base": {"url": "git@x:o/r.git", "mode": "rw"}},
            }
        },
    }
    mpath = tmp_path / "manifest.json"
    mpath.write_text(json.dumps(body))
    return mpath, personal_pid, work_pid, acme_pid


def test_focus_unreachable_no_baseline_no_finding(tmp_path: Path) -> None:
    """No baseline → check is silently skipped (not a focus problem)."""
    target = tmp_path / "target"
    host_id_file = tmp_path / ".maury-host-id"
    mpath, _personal, _work, _acme = _seed_focus_manifest(tmp_path)
    findings = run_system_health_checks(
        target_dir=target,
        host_id_file=host_id_file,
        manifest_path=mpath,
    )
    assert all(f.check_id != "focus-unreachable" for f in findings)


def test_focus_unreachable_no_active_focus_no_finding(tmp_path: Path) -> None:
    """Baseline present, active_focus=None → no finding."""
    target = tmp_path / "target"
    host_id_file = tmp_path / ".maury-host-id"
    mpath, personal_pid, _work, _acme = _seed_focus_manifest(tmp_path)
    write_baseline(
        target,
        HostIdentityBaseline(
            schema_version=1,
            host_id_hex="24b2a0aa",
            registered_at="2026-05-20T10:00:00Z",
            mode_id=personal_pid,
            mode_name_at_bootstrap="personal",
            active_focus=None,
        ),
    )
    findings = run_system_health_checks(
        target_dir=target,
        host_id_file=host_id_file,
        manifest_path=mpath,
    )
    assert all(f.check_id != "focus-unreachable" for f in findings)


def test_focus_unreachable_reachable_focus_no_finding(tmp_path: Path) -> None:
    """active_focus is a legitimate descendant of the registered mode → no finding."""
    target = tmp_path / "target"
    host_id_file = tmp_path / ".maury-host-id"
    mpath, personal_pid, _work, _acme = _seed_focus_manifest(tmp_path)
    write_baseline(
        target,
        HostIdentityBaseline(
            schema_version=1,
            host_id_hex="24b2a0aa",
            registered_at="2026-05-20T10:00:00Z",
            mode_id=personal_pid,
            mode_name_at_bootstrap="personal",
            active_focus="personal:consulting:acme",
        ),
    )
    findings = run_system_health_checks(
        target_dir=target,
        host_id_file=host_id_file,
        manifest_path=mpath,
    )
    assert all(f.check_id != "focus-unreachable" for f in findings)


def test_focus_unreachable_focus_crosses_trust_boundary_warns(tmp_path: Path) -> None:
    """Hand-edit drift: active_focus = "work" but registered mode is
    "personal" → focus-unreachable warn (work is in a sibling subtree)."""
    target = tmp_path / "target"
    host_id_file = tmp_path / ".maury-host-id"
    mpath, personal_pid, _work, _acme = _seed_focus_manifest(tmp_path)
    write_baseline(
        target,
        HostIdentityBaseline(
            schema_version=1,
            host_id_hex="24b2a0aa",
            registered_at="2026-05-20T10:00:00Z",
            mode_id=personal_pid,
            mode_name_at_bootstrap="personal",
            active_focus="work",
        ),
    )
    findings = run_system_health_checks(
        target_dir=target,
        host_id_file=host_id_file,
        manifest_path=mpath,
    )
    focus_findings = [f for f in findings if f.check_id == "focus-unreachable"]
    assert len(focus_findings) == 1
    assert focus_findings[0].severity == Severity.WARN
    assert "work" in focus_findings[0].message
    assert "personal" in focus_findings[0].message
    assert "maury focus use" in focus_findings[0].fix_suggestion


def test_focus_unreachable_focus_unknown_mode_warns(tmp_path: Path) -> None:
    """active_focus names a mode that doesn't exist in the manifest at
    all → focus-unreachable warn with a slightly different message."""
    target = tmp_path / "target"
    host_id_file = tmp_path / ".maury-host-id"
    mpath, personal_pid, _work, _acme = _seed_focus_manifest(tmp_path)
    write_baseline(
        target,
        HostIdentityBaseline(
            schema_version=1,
            host_id_hex="24b2a0aa",
            registered_at="2026-05-20T10:00:00Z",
            mode_id=personal_pid,
            mode_name_at_bootstrap="personal",
            active_focus="ghost-mode",
        ),
    )
    findings = run_system_health_checks(
        target_dir=target,
        host_id_file=host_id_file,
        manifest_path=mpath,
    )
    focus_findings = [f for f in findings if f.check_id == "focus-unreachable"]
    assert len(focus_findings) == 1
    assert "ghost-mode" in focus_findings[0].message
    assert "no mode by that name" in focus_findings[0].message


def test_focus_unreachable_no_manifest_silently_skipped(tmp_path: Path) -> None:
    """When doctor is run outside a base repo (no manifest available),
    the check is silently skipped even if active_focus is set."""
    target = tmp_path / "target"
    host_id_file = tmp_path / ".maury-host-id"
    _mpath, personal_pid, _work, _acme = _seed_focus_manifest(tmp_path)
    write_baseline(
        target,
        HostIdentityBaseline(
            schema_version=1,
            host_id_hex="24b2a0aa",
            registered_at="2026-05-20T10:00:00Z",
            mode_id=personal_pid,
            mode_name_at_bootstrap="personal",
            active_focus="work",  # would normally warn
        ),
    )
    # manifest_path=None → check should not fire
    findings = run_system_health_checks(
        target_dir=target,
        host_id_file=host_id_file,
        manifest_path=None,
    )
    assert all(f.check_id != "focus-unreachable" for f in findings)
