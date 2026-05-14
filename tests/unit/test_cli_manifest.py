"""CLI-layer tests for the `maury manifest` and `maury profile` subgroups.

The manifest parser + validator are exhaustively covered in
`test_manifest.py`. These tests cover only the CLI wiring:
- flag parsing
- env-var resolution
- not-found / parse-error exit codes
- output formatting at the boundary

Pattern follows `test_cli_init.py` and `test_cli_rules.py`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from maury.cli import main
from maury.ids import new_host_id, new_profile_id


def _write_manifest(path: Path, *, with_invalid: bool = False) -> tuple[str, str]:
    """Write a minimal valid (or deliberately broken) manifest. Returns (pid, hid)."""
    pid = new_profile_id()
    hid = new_host_id()
    body: dict[str, object] = {
        "version": 2,
        "profiles": {pid: {"name": "home", "extends": None}},
        "hosts": {
            hid: {
                "name": "workstation",
                "profile": pid if not with_invalid else "profile_deadbeefdeadbeefdeadbeefdeadbeef",
                "repos": {"base": {"url": "git@x:o/r.git", "mode": "rw"}},
            }
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body))
    return pid, hid


# ---- manifest validate --------------------------------------------------


def test_manifest_validate_ok(tmp_path: Path) -> None:
    mpath = tmp_path / "manifest.json"
    _write_manifest(mpath)
    runner = CliRunner()
    result = runner.invoke(main, ["manifest", "validate", "--manifest-file", str(mpath)])
    assert result.exit_code == 0, result.output
    assert "OK:" in result.output
    assert "1 profile(s)" in result.output
    assert "1 host(s)" in result.output


def test_manifest_validate_resolves_via_env_var(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mpath = tmp_path / "manifest.json"
    _write_manifest(mpath)
    monkeypatch.setenv("MAURY_MANIFEST_FILE", str(mpath))
    runner = CliRunner()
    result = runner.invoke(main, ["manifest", "validate"])
    assert result.exit_code == 0, result.output
    assert "OK:" in result.output


def test_manifest_validate_exit_1_on_cross_reference_error(tmp_path: Path) -> None:
    """A host pointing at a nonexistent profile_id → exit 1 with error."""
    mpath = tmp_path / "manifest.json"
    _write_manifest(mpath, with_invalid=True)
    runner = CliRunner()
    result = runner.invoke(main, ["manifest", "validate", "--manifest-file", str(mpath)])
    assert result.exit_code == 1
    combined = result.output + (result.stderr or "")
    assert "error" in combined.lower()


def test_manifest_validate_parse_error_on_malformed_json(tmp_path: Path) -> None:
    mpath = tmp_path / "manifest.json"
    mpath.write_text("{ this is not: valid json")
    runner = CliRunner()
    result = runner.invoke(main, ["manifest", "validate", "--manifest-file", str(mpath)])
    assert result.exit_code != 0


def test_manifest_validate_no_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MAURY_MANIFEST_FILE", raising=False)
    runner = CliRunner()
    result = runner.invoke(main, ["manifest", "validate"])
    assert result.exit_code != 0
    assert "manifest file not found" in (result.output + (result.stderr or ""))


def test_manifest_validate_against_seed(tmp_path: Path) -> None:
    """The shipped seed manifest must validate cleanly through the CLI."""
    seed = Path(__file__).resolve().parents[2] / "base-template" / ".meta" / "manifest.json"
    if not seed.exists():
        pytest.skip("seed manifest not present")
    runner = CliRunner()
    result = runner.invoke(main, ["manifest", "validate", "--manifest-file", str(seed)])
    assert result.exit_code == 0, result.output
    assert "3 profile(s)" in result.output
    assert "2 host(s)" in result.output


# ---- manifest show ------------------------------------------------------


def test_manifest_show_renders_json(tmp_path: Path) -> None:
    mpath = tmp_path / "manifest.json"
    pid, hid = _write_manifest(mpath)
    runner = CliRunner()
    result = runner.invoke(main, ["manifest", "show", "--manifest-file", str(mpath)])
    assert result.exit_code == 0, result.output
    # Round-trip: output should re-parse as JSON with the same IDs present.
    body = json.loads(result.output)
    assert body["version"] == 2
    assert pid in body["profiles"]
    assert hid in body["hosts"]


def test_manifest_show_no_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MAURY_MANIFEST_FILE", raising=False)
    runner = CliRunner()
    result = runner.invoke(main, ["manifest", "show"])
    assert result.exit_code != 0
    assert "manifest file not found" in (result.output + (result.stderr or ""))


def test_manifest_show_parse_error_on_malformed_json(tmp_path: Path) -> None:
    mpath = tmp_path / "manifest.json"
    mpath.write_text("{ this is not: valid json")
    runner = CliRunner()
    result = runner.invoke(main, ["manifest", "show", "--manifest-file", str(mpath)])
    assert result.exit_code != 0


# ---- profile list -------------------------------------------------------


def test_profile_list_shows_profiles_with_chain_and_short_id(tmp_path: Path) -> None:
    mpath = tmp_path / "manifest.json"
    _write_manifest(mpath)
    runner = CliRunner()
    result = runner.invoke(main, ["profile", "list", "--manifest-file", str(mpath)])
    assert result.exit_code == 0, result.output
    assert "home" in result.output
    assert "chain:" in result.output


def test_profile_list_empty_manifest_says_no_profiles(tmp_path: Path) -> None:
    """A v2 manifest with empty profiles map → friendly empty message."""
    mpath = tmp_path / "manifest.json"
    mpath.write_text('{"version": 2, "profiles": {}, "hosts": {}}')
    runner = CliRunner()
    result = runner.invoke(main, ["profile", "list", "--manifest-file", str(mpath)])
    assert result.exit_code == 0, result.output
    assert "no profiles" in result.output.lower()


def test_profile_list_no_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MAURY_MANIFEST_FILE", raising=False)
    runner = CliRunner()
    result = runner.invoke(main, ["profile", "list"])
    assert result.exit_code != 0
    assert "manifest file not found" in (result.output + (result.stderr or ""))


# ---- profile hosts ------------------------------------------------------


def test_profile_hosts_shows_hosts_with_profile_resolution(tmp_path: Path) -> None:
    mpath = tmp_path / "manifest.json"
    _write_manifest(mpath)
    runner = CliRunner()
    result = runner.invoke(main, ["profile", "hosts", "--manifest-file", str(mpath)])
    assert result.exit_code == 0, result.output
    assert "workstation" in result.output
    # Hosts row should resolve profile_id → profile_name for readability
    assert "home" in result.output


def test_profile_hosts_empty_manifest_says_no_hosts(tmp_path: Path) -> None:
    mpath = tmp_path / "manifest.json"
    mpath.write_text('{"version": 2, "profiles": {}, "hosts": {}}')
    runner = CliRunner()
    result = runner.invoke(main, ["profile", "hosts", "--manifest-file", str(mpath)])
    assert result.exit_code == 0, result.output
    assert "no hosts" in result.output.lower()


def test_profile_hosts_no_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MAURY_MANIFEST_FILE", raising=False)
    runner = CliRunner()
    result = runner.invoke(main, ["profile", "hosts"])
    assert result.exit_code != 0
    assert "manifest file not found" in (result.output + (result.stderr or ""))
