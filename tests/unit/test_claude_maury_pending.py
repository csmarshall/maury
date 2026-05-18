"""Tests for `base-template/bin/claude-maury-pending`.

The script is the Stop-hook notifier shipped per ADR-0013 §5. Each
test sets a tmp `HOME`, populates `$HOME/.claude/maury-staging/captures.jsonl`
in some shape, runs the script, and asserts the resulting exit code +
stderr output.

Skipped on Windows (POSIX-only); fine since maury currently targets
macOS / Linux / FreeBSD per the porting doc.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

if sys.platform == "win32":
    pytest.skip("POSIX-only shell script", allow_module_level=True)


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "base-template" / "bin" / "claude-maury-pending"


def _run_script(tmp_home: Path) -> tuple[int, str, str]:
    """Invoke the script with HOME set to tmp_home; return (rc, stdout, stderr)."""
    env = os.environ.copy()
    env["HOME"] = str(tmp_home)
    proc = subprocess.run(
        [str(SCRIPT_PATH)],
        env=env,
        capture_output=True,
        text=True,
        timeout=5.0,
    )
    return proc.returncode, proc.stdout, proc.stderr


def test_script_ships_executable() -> None:
    """The shipped script must have its executable bit set on disk so
    `maury render`'s file-overlay copy preserves the mode (engine.py
    forces 0o755 for `bin/*` regardless, but this also catches
    accidental commits that lose the bit)."""
    assert SCRIPT_PATH.is_file()
    assert os.access(SCRIPT_PATH, os.X_OK), f"{SCRIPT_PATH} is not executable"


def test_missing_staging_file_is_silent(tmp_path: Path) -> None:
    """No `~/.claude/maury-staging/captures.jsonl` → exit 0, no output."""
    # tmp_path has no .claude/maury-staging dir at all.
    rc, out, err = _run_script(tmp_path)
    assert rc == 0
    assert out == ""
    assert err == ""


def test_empty_staging_file_is_silent(tmp_path: Path) -> None:
    """Zero-byte staging file → exit 0, no output (the `[ -s ]` check)."""
    staging = tmp_path / ".claude" / "maury-staging"
    staging.mkdir(parents=True)
    (staging / "captures.jsonl").write_text("")
    rc, out, err = _run_script(tmp_path)
    assert rc == 0
    assert out == ""
    assert err == ""


def test_non_empty_staging_prints_count(tmp_path: Path) -> None:
    """One line in the staging file → '📌 maury: 1 capture(s) pending …'
    to stderr; stdout stays clean so it doesn't interleave with
    assistant output."""
    staging = tmp_path / ".claude" / "maury-staging" / "captures.jsonl"
    staging.parent.mkdir(parents=True)
    staging.write_text('{"text":"hello"}\n')
    rc, out, err = _run_script(tmp_path)
    assert rc == 0
    assert out == ""
    assert "📌 maury: 1 capture(s) pending" in err
    assert "maury review" in err


def test_count_reflects_line_count(tmp_path: Path) -> None:
    """The count is the number of JSONL lines (each line = one capture)."""
    staging = tmp_path / ".claude" / "maury-staging" / "captures.jsonl"
    staging.parent.mkdir(parents=True)
    staging.write_text('{"t":"a"}\n{"t":"b"}\n{"t":"c"}\n{"t":"d"}\n{"t":"e"}\n')
    rc, _, err = _run_script(tmp_path)
    assert rc == 0
    assert "5 capture(s) pending" in err


def test_corrupted_staging_does_not_explode(tmp_path: Path) -> None:
    """Defensive: if the staging file is non-JSONL garbage, the script
    still uses line-count and prints accordingly (or exits silent if
    the line count somehow comes out non-numeric). Mustn't crash."""
    staging = tmp_path / ".claude" / "maury-staging" / "captures.jsonl"
    staging.parent.mkdir(parents=True)
    staging.write_text("this is not jsonl\nbut wc -l still works\n")
    rc, _, _ = _run_script(tmp_path)
    assert rc == 0  # No crash; clean exit regardless of content shape


# ---- integration: the rendered base-template includes the hook --------


def test_seed_template_settings_json_has_stop_hook() -> None:
    """The shipped `base-template/settings.json` declares the
    Stop-hook entry with the `# maury-managed` marker, so `maury render`
    materializes it into the user's `~/.claude/settings.json`."""
    import json

    seed = Path(__file__).resolve().parents[2] / "base-template" / "settings.json"
    body = json.loads(seed.read_text())
    stop_entries = body.get("hooks", {}).get("Stop", [])
    assert stop_entries, "expected Stop hook entry in base-template settings.json"
    # At least one command references claude-maury-pending and carries
    # the marker per ADR-0023 §1.
    commands = [cmd_entry.get("command", "") for group in stop_entries for cmd_entry in group.get("hooks", [])]
    assert any("claude-maury-pending" in c and "# maury-managed" in c for c in commands), (
        f"expected a claude-maury-pending command with the marker; got {commands}"
    )
