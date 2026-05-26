"""CLI-layer tests for `maury verify-cc-contract`."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from maury.cli import main


def _seed_snapshot(snap_dir: Path, name: str = "hooks", body: bytes = b"sample content") -> str:
    """Create a snapshot dir with one entry and return its SHA256."""
    snap_dir.mkdir(parents=True, exist_ok=True)
    (snap_dir / f"{name}.html").write_bytes(body)
    sha = hashlib.sha256(body).hexdigest()
    (snap_dir / "MANIFEST.txt").write_text(f"{name} {len(body)} {sha} https://example.com/{name}\n")
    return sha


def test_verify_cc_contract_check_only_lists_entries(tmp_path: Path) -> None:
    snap = tmp_path / "2026-05-07"
    _seed_snapshot(snap)
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["verify-cc-contract", "--snapshot-dir", str(snap), "--check-only"],
    )
    assert result.exit_code == 0, result.output
    assert "snapshot:" in result.output
    assert "hooks" in result.output
    assert "https://example.com/hooks" in result.output


def test_verify_cc_contract_check_only_errors_on_missing_manifest(tmp_path: Path) -> None:
    snap = tmp_path / "2026-05-07"
    snap.mkdir(parents=True, exist_ok=True)
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["verify-cc-contract", "--snapshot-dir", str(snap), "--check-only"],
    )
    assert result.exit_code != 0


def test_verify_cc_contract_no_default_snapshot_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """If no --snapshot-dir and no default discoverable, exit non-zero with hint."""
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(main, ["verify-cc-contract", "--check-only"])
    assert result.exit_code != 0
    assert "No snapshot directory found" in (result.output + (result.stderr or ""))


def test_verify_cc_contract_full_run_unchanged(tmp_path: Path) -> None:
    snap = tmp_path / "2026-05-07"
    body = b"content"
    _seed_snapshot(snap, body=body)

    def fake_get(_url: str, _timeout: float) -> tuple[int, bytes]:
        return 200, body

    runner = CliRunner()
    with patch("maury.cc_contract_verify.urllib_get", fake_get):
        result = runner.invoke(
            main,
            ["verify-cc-contract", "--snapshot-dir", str(snap)],
        )
    assert result.exit_code == 0, result.output
    assert "unchanged=1" in result.output
    assert "drift=0" in result.output


def test_verify_cc_contract_drift_exits_1(tmp_path: Path) -> None:
    snap = tmp_path / "2026-05-07"
    _seed_snapshot(snap, body=b"original")

    def fake_get(_url: str, _timeout: float) -> tuple[int, bytes]:
        return 200, b"changed remote content"

    runner = CliRunner()
    with patch("maury.cc_contract_verify.urllib_get", fake_get):
        result = runner.invoke(
            main,
            ["verify-cc-contract", "--snapshot-dir", str(snap)],
        )
    assert result.exit_code == 1
    assert "drift=1" in result.output


def test_verify_cc_contract_json_format(tmp_path: Path) -> None:
    snap = tmp_path / "2026-05-07"
    body = b"content"
    _seed_snapshot(snap, body=body)

    def fake_get(_url: str, _timeout: float) -> tuple[int, bytes]:
        return 200, body

    runner = CliRunner()
    with patch("maury.cc_contract_verify.urllib_get", fake_get):
        result = runner.invoke(
            main,
            ["verify-cc-contract", "--snapshot-dir", str(snap), "--format", "json"],
        )
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert parsed["summary"]["unchanged"] == 1
    assert parsed["findings"][0]["name"] == "hooks"


def test_verify_cc_contract_falls_back_to_default_snapshot_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When --snapshot-dir is omitted, the most-recent dated dir under docs/claude-code-snapshots/ is used."""
    repo = tmp_path / "fake-repo"
    snap = repo / "docs" / "claude-code-snapshots" / "2026-05-07"
    _seed_snapshot(snap)
    monkeypatch.chdir(repo)
    runner = CliRunner()
    result = runner.invoke(main, ["verify-cc-contract", "--check-only"])
    assert result.exit_code == 0, result.output
    assert "2026-05-07" in result.output
