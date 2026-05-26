"""CLI-layer tests for `maury uninstall`."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from maury import paths
from maury.cli import main


def _seed(target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "Stop": [
                        {"type": "command", "command": "/m # maury-managed"},
                        {"type": "command", "command": "/user/script.sh"},
                    ]
                }
            }
        )
    )
    bin_dir = target_dir / "bin"
    bin_dir.mkdir()
    (bin_dir / "maury-log").write_text("#!/bin/sh\n")
    state = paths.state_dir()
    state.mkdir(parents=True, exist_ok=True)
    (state / "last-render.json").write_text("{}")
    paths.host_id_file().write_text("host_abc\n")


def test_uninstall_with_yes_flag_skips_confirm(tmp_path: Path) -> None:
    target = tmp_path / "claude"
    _seed(target)
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["uninstall", "--target", str(target), "--yes"],
    )
    assert result.exit_code == 0, result.output
    assert "removed 1 maury-managed hook(s)" in result.output
    assert "left 1 user hook(s) intact" in result.output
    assert not paths.host_id_file().exists()
    # Per ADR-0035, audit.jsonl is preserved as the forensic trail.
    # The state dir survives because of it; everything else inside it is gone.
    assert (paths.state_dir() / "audit.jsonl").is_file()
    assert not (paths.state_dir() / "last-render.json").exists()


def test_uninstall_prompts_and_aborts_on_no(tmp_path: Path) -> None:
    target = tmp_path / "claude"
    _seed(target)
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["uninstall", "--target", str(target)],
        input="n\n",
    )
    assert result.exit_code == 0, result.output
    assert "cancelled" in result.output
    # Nothing should have been removed
    assert paths.host_id_file().exists()
    assert (paths.state_dir()).exists()


def test_uninstall_prompts_and_proceeds_on_yes(tmp_path: Path) -> None:
    target = tmp_path / "claude"
    _seed(target)
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["uninstall", "--target", str(target)],
        input="y\n",
    )
    assert result.exit_code == 0, result.output
    assert not paths.host_id_file().exists()


def test_uninstall_handles_clean_host(tmp_path: Path) -> None:
    target = tmp_path / "claude"
    target.mkdir()
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["uninstall", "--target", str(target), "--yes"],
    )
    assert result.exit_code == 0, result.output
    assert "removed 0 maury-managed hook(s)" in result.output
    assert "left 0 user hook(s) intact" in result.output
