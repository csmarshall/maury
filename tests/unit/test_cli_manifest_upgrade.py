"""CLI-layer tests for `maury manifest upgrade-v1-to-v2`."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from maury.cli import main


def _write_v1(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "profiles": {
                    "home": {"extends": None},
                    "work": {"extends": None},
                },
                "hosts": {
                    "workstation": {
                        "profile": "home",
                        "repos": {"base": {"url": "git@x:o/r.git", "mode": "rw"}},
                    }
                },
            }
        )
    )


def test_upgrade_in_place(tmp_path: Path) -> None:
    p = tmp_path / "manifest.json"
    _write_v1(p)
    runner = CliRunner()
    result = runner.invoke(main, ["manifest", "upgrade-v1-to-v2", "--in", str(p)])
    assert result.exit_code == 0, result.output
    assert "upgraded" in result.output
    assert "profile mapping:" in result.output
    assert "host mapping:" in result.output
    body = json.loads(p.read_text())
    assert body["version"] == 2


def test_upgrade_to_separate_out_path(tmp_path: Path) -> None:
    p = tmp_path / "manifest.json"
    out = tmp_path / "upgraded.json"
    _write_v1(p)
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["manifest", "upgrade-v1-to-v2", "--in", str(p), "--out", str(out)],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(p.read_text())["version"] == 1
    assert json.loads(out.read_text())["version"] == 2


def test_upgrade_check_does_not_write(tmp_path: Path) -> None:
    p = tmp_path / "manifest.json"
    _write_v1(p)
    before = p.read_text()
    runner = CliRunner()
    result = runner.invoke(main, ["manifest", "upgrade-v1-to-v2", "--in", str(p), "--check"])
    assert result.exit_code == 0, result.output
    assert "no files were written" in result.output
    assert p.read_text() == before


def test_upgrade_refuses_already_v2(tmp_path: Path) -> None:
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps({"version": 2, "profiles": {}, "hosts": {}}))
    runner = CliRunner()
    result = runner.invoke(main, ["manifest", "upgrade-v1-to-v2", "--in", str(p)])
    assert result.exit_code != 0
    combined = result.output + (result.stderr or "")
    assert "already version 2" in combined
