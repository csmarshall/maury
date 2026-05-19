"""CLI-layer tests for `maury mode bootstrap` and `maury mode deregister`."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from maury.cli import main
from maury.ids import new_host_id, new_profile_id


def _seed(path: Path) -> tuple[str, str]:
    """Write a manifest with one profile and one already-registered host."""
    pid = new_profile_id()
    hid = new_host_id()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "profiles": {pid: {"name": "home", "extends": None}},
                "hosts": {
                    hid: {
                        "name": "alice-laptop",
                        "profile": pid,
                        "repos": {"base": {"url": "git@x:o/r.git", "mode": "rw"}},
                    }
                },
            }
        )
    )
    return pid, hid


# ---- mode bootstrap (alias of bootstrap host) ----------------------------


def test_mode_bootstrap_registers_host(tmp_path: Path) -> None:
    mpath = tmp_path / "manifest.json"
    _seed(mpath)
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "mode",
            "bootstrap",
            "--manifest-file",
            str(mpath),
            "--name",
            "bob-laptop",
            "--mode",
            "home",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "registered host 'bob-laptop'" in result.output
    body = json.loads(mpath.read_text())
    names = {h["name"] for h in body["hosts"].values()}
    assert names == {"alice-laptop", "bob-laptop"}


def test_mode_bootstrap_check_dry_run(tmp_path: Path) -> None:
    mpath = tmp_path / "manifest.json"
    _seed(mpath)
    before = mpath.read_text()
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "mode",
            "bootstrap",
            "--manifest-file",
            str(mpath),
            "--name",
            "bob-laptop",
            "--mode",
            "home",
            "--check",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "(--check;" in result.output
    assert mpath.read_text() == before


def test_mode_bootstrap_rejects_unknown_mode(tmp_path: Path) -> None:
    mpath = tmp_path / "manifest.json"
    _seed(mpath)
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "mode",
            "bootstrap",
            "--manifest-file",
            str(mpath),
            "--name",
            "bob-laptop",
            "--mode",
            "nonexistent-mode",
        ],
    )
    assert result.exit_code != 0
    combined = result.output + (result.stderr or "")
    assert "not found in manifest" in combined


# ---- mode deregister -----------------------------------------------------


def test_mode_deregister_by_name(tmp_path: Path) -> None:
    mpath = tmp_path / "manifest.json"
    _pid, hid = _seed(mpath)
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["mode", "deregister", "--manifest-file", str(mpath), "--host", "alice-laptop"],
    )
    assert result.exit_code == 0, result.output
    assert "deregistered host 'alice-laptop'" in result.output
    body = json.loads(mpath.read_text())
    assert hid not in body["hosts"]


def test_mode_deregister_by_host_id(tmp_path: Path) -> None:
    mpath = tmp_path / "manifest.json"
    _pid, hid = _seed(mpath)
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["mode", "deregister", "--manifest-file", str(mpath), "--host", hid],
    )
    assert result.exit_code == 0, result.output
    body = json.loads(mpath.read_text())
    assert hid not in body["hosts"]


def test_mode_deregister_check_dry_run(tmp_path: Path) -> None:
    mpath = tmp_path / "manifest.json"
    _seed(mpath)
    before = mpath.read_text()
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["mode", "deregister", "--manifest-file", str(mpath), "--host", "alice-laptop", "--check"],
    )
    assert result.exit_code == 0, result.output
    assert "(--check;" in result.output
    assert mpath.read_text() == before


def test_mode_deregister_rejects_unknown_host(tmp_path: Path) -> None:
    mpath = tmp_path / "manifest.json"
    _seed(mpath)
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["mode", "deregister", "--manifest-file", str(mpath), "--host", "does-not-exist"],
    )
    assert result.exit_code != 0
    combined = result.output + (result.stderr or "")
    assert "not found" in combined


def test_mode_bootstrap_deregister_roundtrip(tmp_path: Path) -> None:
    """Bootstrap + deregister cycles cleanly per ADR-0039's two-step mode change."""
    mpath = tmp_path / "manifest.json"
    _seed(mpath)
    runner = CliRunner()
    runner.invoke(
        main,
        [
            "mode",
            "bootstrap",
            "--manifest-file",
            str(mpath),
            "--name",
            "transient",
            "--mode",
            "home",
        ],
    )
    assert "transient" in {h["name"] for h in json.loads(mpath.read_text())["hosts"].values()}

    result = runner.invoke(
        main,
        ["mode", "deregister", "--manifest-file", str(mpath), "--host", "transient"],
    )
    assert result.exit_code == 0, result.output
    assert "transient" not in {h["name"] for h in json.loads(mpath.read_text())["hosts"].values()}
