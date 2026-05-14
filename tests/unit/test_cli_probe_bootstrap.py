"""CLI-layer tests for `maury probe` and `maury bootstrap host`.

The capability probe internals are covered in `test_capability_probe.py`;
the bootstrap_host engine is covered in `test_bootstrap_host.py`. These
tests cover the CLI wiring: flag parsing, env-var resolution, error
paths, exit codes, output format.

Pattern follows `test_cli_init.py` and `test_cli_manifest.py`.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from maury.cli import main
from maury.ids import new_host_id, new_profile_id


def _write_seed_manifest(tmp_path: Path) -> tuple[Path, str, str]:
    """Create a manifest with a single host so bootstrap_host has a base to copy from."""
    pid = new_profile_id()
    hid = new_host_id()
    body = {
        "version": 2,
        "profiles": {pid: {"name": "home", "extends": None}},
        "hosts": {
            hid: {
                "name": "existing-host",
                "profile": pid,
                "repos": {"base": {"url": "git@codeberg.org:user/maury-base.git", "mode": "rw"}},
            }
        },
    }
    mpath = tmp_path / "manifest.json"
    mpath.write_text(json.dumps(body))
    return mpath, pid, hid


# ---- probe --------------------------------------------------------------


def test_probe_writes_to_stdout_by_default() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["probe"])
    assert result.exit_code == 0, result.output
    # Output should be parseable JSON
    payload = json.loads(result.output)
    # Top-level fields per Capability schema
    assert "hostname" in payload
    assert "os" in payload


def test_probe_writes_to_file_with_output_flag(tmp_path: Path) -> None:
    out = tmp_path / "subdir" / "capabilities.json"
    runner = CliRunner()
    result = runner.invoke(main, ["probe", "--output", str(out)])
    assert result.exit_code == 0, result.output
    assert out.is_file()
    payload = json.loads(out.read_text())
    assert "hostname" in payload
    # CLI confirms the write location
    assert str(out) in result.output


def test_probe_hostname_override_appears_in_output() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["probe", "--hostname", "test-override-host"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["hostname"] == "test-override-host"


def test_probe_hostname_override_writes_to_file(tmp_path: Path) -> None:
    out = tmp_path / "caps.json"
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["probe", "--hostname", "test-override-host", "--output", str(out)],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(out.read_text())
    assert payload["hostname"] == "test-override-host"


# ---- bootstrap host -----------------------------------------------------


def test_bootstrap_host_dry_run_writes_no_changes(tmp_path: Path) -> None:
    mpath, _, _ = _write_seed_manifest(tmp_path)
    original = mpath.read_text()
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "bootstrap",
            "host",
            "--manifest-file",
            str(mpath),
            "--name",
            "new-host",
            "--profile",
            "home",
            "--check",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "(--check; no files were written)" in result.output
    # Manifest unchanged
    assert mpath.read_text() == original


def test_bootstrap_host_persists_when_not_dry_run(tmp_path: Path) -> None:
    mpath, _, _ = _write_seed_manifest(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "bootstrap",
            "host",
            "--manifest-file",
            str(mpath),
            "--name",
            "new-host",
            "--profile",
            "home",
        ],
    )
    assert result.exit_code == 0, result.output
    # Manifest now has the new host
    body = json.loads(mpath.read_text())
    host_names = {h["name"] for h in body["hosts"].values()}
    assert "new-host" in host_names


def test_bootstrap_host_explicit_base_url_overrides_inheritance(tmp_path: Path) -> None:
    mpath, _, _ = _write_seed_manifest(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "bootstrap",
            "host",
            "--manifest-file",
            str(mpath),
            "--name",
            "new-host",
            "--profile",
            "home",
            "--base-url",
            "git@codeberg.org:user/different-base.git",
            "--base-mode",
            "ro",
        ],
    )
    assert result.exit_code == 0, result.output
    body = json.loads(mpath.read_text())
    new_host = next(h for h in body["hosts"].values() if h["name"] == "new-host")
    assert new_host["repos"]["base"]["url"] == "git@codeberg.org:user/different-base.git"
    assert new_host["repos"]["base"]["mode"] == "ro"


def test_bootstrap_host_unknown_profile_errors(tmp_path: Path) -> None:
    mpath, _, _ = _write_seed_manifest(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "bootstrap",
            "host",
            "--manifest-file",
            str(mpath),
            "--name",
            "new-host",
            "--profile",
            "nonexistent",
        ],
    )
    assert result.exit_code != 0


def test_bootstrap_host_no_manifest_errors(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "bootstrap",
            "host",
            "--manifest-file",
            str(tmp_path / "nonexistent.json"),
            "--name",
            "new-host",
            "--profile",
            "home",
        ],
    )
    assert result.exit_code != 0


def test_bootstrap_host_missing_required_args_fails_with_usage(tmp_path: Path) -> None:
    """Click should refuse without required --name or --profile."""
    mpath, _, _ = _write_seed_manifest(tmp_path)
    runner = CliRunner()
    # Missing --profile
    result = runner.invoke(main, ["bootstrap", "host", "--manifest-file", str(mpath), "--name", "new-host"])
    assert result.exit_code != 0
    combined = result.output + (result.stderr or "")
    assert "profile" in combined.lower()


def test_bootstrap_host_invalid_repo_mode_rejected_by_click(tmp_path: Path) -> None:
    """`--base-mode foo` is not a valid choice; Click rejects at parse time."""
    mpath, _, _ = _write_seed_manifest(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "bootstrap",
            "host",
            "--manifest-file",
            str(mpath),
            "--name",
            "new-host",
            "--profile",
            "home",
            "--base-mode",
            "foo",
        ],
    )
    assert result.exit_code != 0
    combined = result.output + (result.stderr or "")
    assert "foo" in combined or "Invalid value" in combined


def test_bootstrap_host_invalid_push_policy_rejected_by_click(tmp_path: Path) -> None:
    mpath, _, _ = _write_seed_manifest(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "bootstrap",
            "host",
            "--manifest-file",
            str(mpath),
            "--name",
            "new-host",
            "--profile",
            "home",
            "--push-policy",
            "yolo",
        ],
    )
    assert result.exit_code != 0
