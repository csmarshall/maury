"""Tests for `src/maury/host_identity.py` — ADR-0042 baseline + guard."""

from __future__ import annotations

from pathlib import Path

import pytest

from maury import paths
from maury.host_identity import (
    SCHEMA_VERSION,
    HostIdentityBaseline,
    HostIdentityError,
    IdentityCheckOutcome,
    baseline_path,
    check_host_identity,
    format_identity_change_message,
    read_baseline,
    write_baseline,
)

# ---- helpers ------------------------------------------------------------


def _make_baseline(host_id_hex: str = "24b2a0aa") -> HostIdentityBaseline:
    return HostIdentityBaseline(
        schema_version=SCHEMA_VERSION,
        host_id_hex=host_id_hex,
        registered_at="2026-05-14T15:42:11Z",
        mode_id="mode_3f1a8b2c4d5e6f7081a2b3c4d5e6f708",
        mode_name_at_bootstrap="home",
    )


def _write_host_id_file(path: Path, host_id: str) -> None:
    path.write_text(host_id + "\n")


# ---- schema + round-trip -----------------------------------------------


def test_baseline_json_round_trip(tmp_path: Path) -> None:
    """write → read produces the same object (modulo trailing whitespace)."""
    b = _make_baseline()
    target = tmp_path / "target"
    write_baseline(target, b)
    loaded = read_baseline(target)
    assert loaded == b


def test_baseline_path_is_under_maury_state(tmp_path: Path) -> None:
    """Per ADR-0029 invariant: baseline lives at
    `<target>/maury-state/host-identity.json`."""
    bp = baseline_path(tmp_path)
    assert bp == paths.state_dir() / "host-identity.json"


def test_read_baseline_absent_returns_none(tmp_path: Path) -> None:
    """A target that's never been initialized has no baseline yet."""
    assert read_baseline(tmp_path) is None


def test_read_baseline_unsupported_schema_version_raises(tmp_path: Path) -> None:
    bp = baseline_path(tmp_path)
    bp.parent.mkdir(parents=True, exist_ok=True)
    bp.write_text('{"schema_version": 99, "host_id_hex": "x"}')
    with pytest.raises(HostIdentityError, match="schema_version"):
        read_baseline(tmp_path)


def test_write_baseline_uses_tmp_rename(tmp_path: Path) -> None:
    """The temp file should be cleaned up after rename (atomic semantics).
    Per ADR-0029 invariant #4."""
    b = _make_baseline()
    write_baseline(tmp_path, b)
    state_dir = paths.state_dir()
    files = list(state_dir.iterdir())
    assert files == [state_dir / "host-identity.json"]


# ---- check_host_identity: happy path -----------------------------------


def test_check_returns_ok_when_baseline_matches(tmp_path: Path) -> None:
    target = tmp_path / "target"
    host_id_file = tmp_path / ".maury-host-id"
    write_baseline(target, _make_baseline(host_id_hex="24b2a0aa"))
    _write_host_id_file(host_id_file, "host_24b2a0aa_laptop")

    result = check_host_identity(target_dir=target, host_id_file=host_id_file)
    assert result.outcome == IdentityCheckOutcome.OK
    assert result.current_hex == "24b2a0aa"
    assert result.baseline_hex == "24b2a0aa"
    assert result.baseline is not None


def test_check_tag_only_edit_does_not_trigger_guard(tmp_path: Path) -> None:
    """The guard checks the 8-hex PREFIX only. Editing the tag (from
    `_laptop` to `_workstation` for instance) leaves the hex unchanged
    and must not trigger the abort."""
    target = tmp_path / "target"
    host_id_file = tmp_path / ".maury-host-id"
    write_baseline(target, _make_baseline(host_id_hex="24b2a0aa"))
    _write_host_id_file(host_id_file, "host_24b2a0aa_workstation")  # edited tag

    result = check_host_identity(target_dir=target, host_id_file=host_id_file)
    assert result.outcome == IdentityCheckOutcome.OK


# ---- check_host_identity: refused mismatch ------------------------------


def test_check_refuses_on_hex_mismatch_by_default(tmp_path: Path) -> None:
    """Hex differs from baseline → REFUSED, no baseline modification."""
    target = tmp_path / "target"
    host_id_file = tmp_path / ".maury-host-id"
    write_baseline(target, _make_baseline(host_id_hex="24b2a0aa"))
    _write_host_id_file(host_id_file, "host_88ff77ee_anything")

    result = check_host_identity(target_dir=target, host_id_file=host_id_file)
    assert result.outcome == IdentityCheckOutcome.CHANGED_REFUSED
    assert result.current_hex == "88ff77ee"
    assert result.baseline_hex == "24b2a0aa"
    # Baseline unchanged on disk
    assert read_baseline(target) is not None
    assert read_baseline(target).host_id_hex == "24b2a0aa"  # type: ignore[union-attr]


# ---- check_host_identity: acknowledged mismatch -------------------------


def test_check_acknowledged_rewrites_baseline(tmp_path: Path) -> None:
    """`--confirm-identity-change` path: caller passes allow_change=True,
    baseline gets rewritten with the new hex, outcome is ACKNOWLEDGED."""
    target = tmp_path / "target"
    host_id_file = tmp_path / ".maury-host-id"
    write_baseline(target, _make_baseline(host_id_hex="24b2a0aa"))
    _write_host_id_file(host_id_file, "host_88ff77ee_anything")

    result = check_host_identity(
        target_dir=target,
        host_id_file=host_id_file,
        allow_change=True,
    )
    assert result.outcome == IdentityCheckOutcome.CHANGED_ACKNOWLEDGED
    # Baseline now reflects the new hex
    new_baseline = read_baseline(target)
    assert new_baseline is not None
    assert new_baseline.host_id_hex == "88ff77ee"
    # Original registered_at and mode info preserved (audit trail)
    assert new_baseline.registered_at == "2026-05-14T15:42:11Z"
    assert new_baseline.mode_name_at_bootstrap == "home"


# ---- check_host_identity: error paths ----------------------------------


def test_check_raises_when_host_id_file_missing(tmp_path: Path) -> None:
    target = tmp_path / "target"
    host_id_file = tmp_path / ".maury-host-id"  # doesn't exist
    with pytest.raises(HostIdentityError, match="Run `maury init`"):
        check_host_identity(target_dir=target, host_id_file=host_id_file)


def test_check_raises_when_baseline_missing_but_host_id_present(tmp_path: Path) -> None:
    """Host-id file without a baseline is state corruption (init writes
    both atomically). The pre-release "auto-upgrade silently" path was
    retired 2026-05-19."""
    target = tmp_path / "target"
    host_id_file = tmp_path / ".maury-host-id"
    _write_host_id_file(host_id_file, "host_24b2a0aa_laptop")
    with pytest.raises(HostIdentityError, match="baseline missing"):
        check_host_identity(target_dir=target, host_id_file=host_id_file)


# ---- format_identity_change_message ------------------------------------


def test_format_change_message_includes_both_hex_values_and_full_id(tmp_path: Path) -> None:
    """The verbose message must show baseline hex, current hex, full
    current ID, mode info, and both remediation flags."""
    target = tmp_path / "target"
    host_id_file = tmp_path / ".maury-host-id"
    write_baseline(target, _make_baseline(host_id_hex="24b2a0aa"))
    _write_host_id_file(host_id_file, "host_88ff77ee_work-laptop")

    result = check_host_identity(target_dir=target, host_id_file=host_id_file)
    msg = format_identity_change_message(
        target_dir=target,
        host_id_file=host_id_file,
        result=result,
    )
    assert "24b2a0aa" in msg
    assert "88ff77ee" in msg
    assert "host_88ff77ee_work-laptop" in msg
    assert "home" in msg  # mode name from baseline
    assert "--confirm-identity-change" in msg
    assert "maury init --reset" in msg


# ---- ADR-0052: active_focus field on the baseline -----------------------


class TestActiveFocusField:
    def test_baseline_default_active_focus_is_none(self) -> None:
        """A baseline constructed without an explicit active_focus has None
        (registered mode is the active mode; no descendant is selected)."""
        b = _make_baseline()
        assert b.active_focus is None

    def test_baseline_roundtrips_active_focus(self, tmp_path: Path) -> None:
        b = HostIdentityBaseline(
            schema_version=SCHEMA_VERSION,
            host_id_hex="24b2a0aa",
            registered_at="2026-05-20T10:00:00Z",
            mode_id="mode_3f1a8b2c4d5e6f7081a2b3c4d5e6f708",
            mode_name_at_bootstrap="personal",
            active_focus="personal:consulting:acme",
        )
        write_baseline(tmp_path, b)
        loaded = read_baseline(tmp_path)
        assert loaded is not None
        assert loaded.active_focus == "personal:consulting:acme"
        assert loaded == b

    def test_baseline_loads_pre_2026_05_20_file_without_active_focus(self, tmp_path: Path) -> None:
        """Backwards-compat: a baseline written before active_focus was
        added (no field on disk) loads cleanly with active_focus=None.
        Per ADR-0052's 'additive field, no schema bump.'"""
        legacy = (
            '{"schema_version": 1, "host_id_hex": "24b2a0aa", '
            '"registered_at": "2026-05-14T15:42:11Z", '
            '"mode_id": "mode_3f1a", "mode_name_at_bootstrap": "personal"}'
        )
        bp = baseline_path(tmp_path)
        bp.parent.mkdir(parents=True, exist_ok=True)
        bp.write_text(legacy)
        loaded = read_baseline(tmp_path)
        assert loaded is not None
        assert loaded.active_focus is None
        assert loaded.host_id_hex == "24b2a0aa"
        assert loaded.mode_name_at_bootstrap == "personal"

    def test_baseline_explicit_null_active_focus_loads_as_none(self, tmp_path: Path) -> None:
        """A baseline written with `"active_focus": null` is semantically
        identical to one written without the field."""
        with_null = (
            '{"schema_version": 1, "host_id_hex": "24b2a0aa", '
            '"registered_at": "x", "mode_id": "m", '
            '"mode_name_at_bootstrap": "personal", "active_focus": null}'
        )
        bp = baseline_path(tmp_path)
        bp.parent.mkdir(parents=True, exist_ok=True)
        bp.write_text(with_null)
        loaded = read_baseline(tmp_path)
        assert loaded is not None
        assert loaded.active_focus is None

    def test_to_json_includes_active_focus_when_set(self) -> None:
        """The serialized form includes the field so it round-trips through
        write_baseline + read_baseline."""
        b = HostIdentityBaseline(
            schema_version=SCHEMA_VERSION,
            host_id_hex="24b2a0aa",
            registered_at="2026-05-20T10:00:00Z",
            mode_id="mode_xxx",
            mode_name_at_bootstrap="personal",
            active_focus="personal:consulting:acme",
        )
        body = b.to_json()
        assert '"active_focus": "personal:consulting:acme"' in body
