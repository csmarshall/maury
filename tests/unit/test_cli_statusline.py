"""Tests for `maury statusline` per ADR-0052.

The statusline subcommand outputs a one-line summary that Claude Code
wires into the prompt via `settings.json`'s `statusLine` setting. The
load-bearing invariant: **never crash, never exit non-zero** — a maury
bug must not break the user's prompt.
"""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from maury import paths
from maury.cli import main
from maury.host_identity import SCHEMA_VERSION, HostIdentityBaseline, write_baseline


def _write_baseline(
    target_dir: Path,
    *,
    mode_name: str = "personal",
    active_focus: str | None = None,
) -> None:
    write_baseline(
        HostIdentityBaseline(
            schema_version=SCHEMA_VERSION,
            host_id_hex="24b2a0aa",
            registered_at="2026-05-20T10:00:00Z",
            mode_id="mode_xxx",
            mode_name_at_bootstrap=mode_name,
            active_focus=active_focus,
        ),
    )


# ---- happy paths --------------------------------------------------------


def test_statusline_shows_mode_only_when_no_focus_set(tmp_path: Path) -> None:
    _write_baseline(tmp_path, mode_name="personal")
    runner = CliRunner()
    result = runner.invoke(main, ["statusline", "--target", str(tmp_path)])
    assert result.exit_code == 0
    assert result.output.strip() == "mode:personal"


def test_statusline_shows_mode_and_focus_when_set(tmp_path: Path) -> None:
    _write_baseline(tmp_path, mode_name="personal", active_focus="personal:consulting:acme")
    runner = CliRunner()
    result = runner.invoke(main, ["statusline", "--target", str(tmp_path)])
    assert result.exit_code == 0
    assert result.output.strip() == "mode:personal focus:personal:consulting:acme"


def test_statusline_treats_focus_equal_to_mode_as_no_focus(tmp_path: Path) -> None:
    """If active_focus literally equals the registered mode name, treat
    it as 'no focus' in the display (avoid `mode:personal focus:personal`
    redundancy)."""
    _write_baseline(tmp_path, mode_name="personal", active_focus="personal")
    runner = CliRunner()
    result = runner.invoke(main, ["statusline", "--target", str(tmp_path)])
    assert result.exit_code == 0
    assert result.output.strip() == "mode:personal"


# ---- identity-missing path ---------------------------------------------


def test_statusline_no_identity_outputs_friendly_placeholder(tmp_path: Path) -> None:
    """No baseline → '(no maury identity)' — never crashes."""
    runner = CliRunner()
    result = runner.invoke(main, ["statusline", "--target", str(tmp_path)])
    assert result.exit_code == 0
    assert result.output.strip() == "(no maury identity)"


# ---- crash-resistance invariant ----------------------------------------


def test_statusline_with_corrupted_baseline_does_not_crash(tmp_path: Path) -> None:
    """Garbled host-identity.json must not break the user's prompt.
    Per ADR-0052 the command catches every exception and prints a
    fallback string."""
    bp = paths.state_dir() / "host-identity.json"
    bp.parent.mkdir(parents=True, exist_ok=True)
    bp.write_text("{ this is not valid json")
    runner = CliRunner()
    result = runner.invoke(main, ["statusline", "--target", str(tmp_path)])
    # MUST exit 0 even though the file is garbage.
    assert result.exit_code == 0
    # Output is the fallback placeholder, NOT a python traceback.
    assert "Traceback" not in result.output
    assert "maury statusline error" in result.output


def test_statusline_with_unsupported_schema_version_does_not_crash(tmp_path: Path) -> None:
    """A future schema_version we can't read still must not break the
    user's prompt."""
    bp = paths.state_dir() / "host-identity.json"
    bp.parent.mkdir(parents=True, exist_ok=True)
    bp.write_text('{"schema_version": 99, "host_id_hex": "x", "mode_name_at_bootstrap": "personal"}')
    runner = CliRunner()
    result = runner.invoke(main, ["statusline", "--target", str(tmp_path)])
    assert result.exit_code == 0
    assert "Traceback" not in result.output


def test_statusline_with_target_path_pointing_at_nothing(tmp_path: Path) -> None:
    """A `--target` pointing at a directory that doesn't exist at all
    must still exit 0."""
    runner = CliRunner()
    result = runner.invoke(main, ["statusline", "--target", str(tmp_path / "does-not-exist")])
    assert result.exit_code == 0


# ---- format invariants per ADR-0052 ------------------------------------


def test_statusline_output_is_ascii_only(tmp_path: Path) -> None:
    """No non-ASCII glyphs in the output — the operator's terminal may not
    render them and the line is meant to be scriptable."""
    _write_baseline(tmp_path, mode_name="personal", active_focus="personal:consulting:acme")
    runner = CliRunner()
    result = runner.invoke(main, ["statusline", "--target", str(tmp_path)])
    assert result.exit_code == 0
    result.output.encode("ascii")  # raises UnicodeEncodeError if non-ASCII present


def test_statusline_output_is_single_line(tmp_path: Path) -> None:
    """`statusLine` displays the first line; emitting more would be wasted
    but mustn't break the prompt. Verify the production case is one line."""
    _write_baseline(tmp_path, mode_name="personal", active_focus="personal:consulting:acme")
    runner = CliRunner()
    result = runner.invoke(main, ["statusline", "--target", str(tmp_path)])
    assert result.exit_code == 0
    # One line of content + trailing newline from click.echo
    assert result.output.count("\n") == 1
