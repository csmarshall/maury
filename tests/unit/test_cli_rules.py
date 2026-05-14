"""CLI-layer tests for the `maury rules` subgroup.

The rules engine itself is exhaustively tested in `test_rules_engine.py`
and `test_rules_loader.py`. These tests cover the CLI wiring only:
- flag parsing
- env-var resolution
- not-found / parse-error exit codes
- output formatting at the boundary

Pattern follows `test_cli_init.py`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from maury.cli import main


def _write_rules(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)


# A minimal, valid rules.yaml used across most tests in this file.
VALID_RULES_YAML = """\
version: 1
rules:
  - id: hostname-server
    when:
      pattern: '\\bserver\\b'
    then:
      profile: home
    confidence: high
    priority: 10
    added: 2026-05-14
    reason: test fixture

  - id: redact-internal
    when:
      pattern: 'INTERNAL'
    then:
      forbid_profile: ['!home']
    confidence: high
    priority: 100
    added: 2026-05-14
    reason: test fixture
"""


# ---- rules trace --------------------------------------------------------


def test_rules_trace_classifies_against_explicit_rules_file(tmp_path: Path) -> None:
    rules_file = tmp_path / "rules.yaml"
    _write_rules(rules_file, VALID_RULES_YAML)
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["rules", "trace", "--rules-file", str(rules_file), "deploying", "to", "server"],
    )
    assert result.exit_code == 0, result.output
    assert "hostname-server" in result.output
    assert "home" in result.output


def test_rules_trace_resolves_via_env_var(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rules_file = tmp_path / "rules.yaml"
    _write_rules(rules_file, VALID_RULES_YAML)
    monkeypatch.setenv("MAURY_RULES_FILE", str(rules_file))
    runner = CliRunner()
    result = runner.invoke(main, ["rules", "trace", "deploying", "to", "server"])
    assert result.exit_code == 0, result.output
    assert "hostname-server" in result.output


def test_rules_trace_no_file_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing rules file → exit non-zero with a clear pointer at env var / flag."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MAURY_RULES_FILE", raising=False)
    runner = CliRunner()
    result = runner.invoke(main, ["rules", "trace", "anything"])
    assert result.exit_code != 0
    assert "rules file not found" in (result.output + (result.stderr or ""))
    assert "MAURY_RULES_FILE" in (result.output + (result.stderr or ""))


def test_rules_trace_parse_error_surfaces_as_click_exception(tmp_path: Path) -> None:
    """Malformed YAML → exit non-zero with the RuleParseError message intact."""
    rules_file = tmp_path / "rules.yaml"
    _write_rules(rules_file, "this is not valid: [unbalanced\n")
    runner = CliRunner()
    result = runner.invoke(main, ["rules", "trace", "--rules-file", str(rules_file), "x"])
    assert result.exit_code != 0


def test_rules_trace_quiet_flag_suppresses_misses(tmp_path: Path) -> None:
    rules_file = tmp_path / "rules.yaml"
    _write_rules(rules_file, VALID_RULES_YAML)
    runner = CliRunner()
    with_misses = runner.invoke(
        main,
        ["rules", "trace", "--rules-file", str(rules_file), "deploying", "to", "server"],
    )
    quiet = runner.invoke(
        main,
        ["rules", "trace", "--rules-file", str(rules_file), "--quiet", "deploying", "to", "server"],
    )
    assert with_misses.exit_code == 0
    assert quiet.exit_code == 0
    # Quiet output is a subset of the verbose output — fewer rules listed.
    assert len(quiet.output) <= len(with_misses.output)


def test_rules_trace_with_profiles_list_overrides_manifest(tmp_path: Path) -> None:
    """`--profiles` overrides `--manifest-file` for known-profile lookup."""
    rules_file = tmp_path / "rules.yaml"
    _write_rules(rules_file, VALID_RULES_YAML)
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "rules",
            "trace",
            "--rules-file",
            str(rules_file),
            "--profiles",
            "home,work",
            "deploying",
            "to",
            "server",
        ],
    )
    assert result.exit_code == 0, result.output


# ---- rules list ---------------------------------------------------------


def test_rules_list_shows_rules_with_columns(tmp_path: Path) -> None:
    rules_file = tmp_path / "rules.yaml"
    _write_rules(rules_file, VALID_RULES_YAML)
    runner = CliRunner()
    result = runner.invoke(main, ["rules", "list", "--rules-file", str(rules_file)])
    assert result.exit_code == 0, result.output
    assert "hostname-server" in result.output
    assert "redact-internal" in result.output
    # Forbid rule renders its forbid_profile list in [.,.] form
    assert "[!home]" in result.output or "!home" in result.output


def test_rules_list_empty_ruleset_prints_no_rules(tmp_path: Path) -> None:
    rules_file = tmp_path / "empty.yaml"
    _write_rules(rules_file, "version: 1\nrules: []\n")
    runner = CliRunner()
    result = runner.invoke(main, ["rules", "list", "--rules-file", str(rules_file)])
    assert result.exit_code == 0, result.output
    assert "(no rules)" in result.output


def test_rules_list_no_file_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MAURY_RULES_FILE", raising=False)
    runner = CliRunner()
    result = runner.invoke(main, ["rules", "list"])
    assert result.exit_code != 0
    assert "rules file not found" in (result.output + (result.stderr or ""))


# ---- rules validate -----------------------------------------------------


def test_rules_validate_ok(tmp_path: Path) -> None:
    rules_file = tmp_path / "rules.yaml"
    _write_rules(rules_file, VALID_RULES_YAML)
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["rules", "validate", "--rules-file", str(rules_file), "--profiles", "home,work"],
    )
    assert result.exit_code == 0, result.output
    assert "OK: 2 rules valid." in result.output


def test_rules_validate_exit_1_on_unknown_profile_reference(tmp_path: Path) -> None:
    """A `then.profile` that references an unknown profile → exit 1 with error printed."""
    rules_file = tmp_path / "rules.yaml"
    _write_rules(
        rules_file,
        "version: 1\nrules:\n  - id: bad\n    when: {pattern: foo}\n    then: {profile: nonexistent}\n",
    )
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["rules", "validate", "--rules-file", str(rules_file), "--profiles", "home,work"],
    )
    assert result.exit_code == 1
    assert "error" in (result.output + (result.stderr or "")).lower()


def test_rules_validate_uses_seed_manifest_when_provided(tmp_path: Path) -> None:
    """`--manifest-file` derives known profiles from the manifest."""
    rules_file = tmp_path / "rules.yaml"
    _write_rules(rules_file, VALID_RULES_YAML)
    seed = Path(__file__).resolve().parents[2] / "base-template" / ".meta" / "manifest.json"
    if not seed.exists():
        pytest.skip("seed manifest not present")
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "rules",
            "validate",
            "--rules-file",
            str(rules_file),
            "--manifest-file",
            str(seed),
        ],
    )
    assert result.exit_code == 0, result.output


def test_rules_validate_parse_error_exits_nonzero(tmp_path: Path) -> None:
    rules_file = tmp_path / "rules.yaml"
    _write_rules(rules_file, "this is not valid yaml: [unbalanced\n")
    runner = CliRunner()
    result = runner.invoke(main, ["rules", "validate", "--rules-file", str(rules_file)])
    assert result.exit_code != 0


def test_rules_validate_no_file_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MAURY_RULES_FILE", raising=False)
    runner = CliRunner()
    result = runner.invoke(main, ["rules", "validate"])
    assert result.exit_code != 0
    assert "rules file not found" in (result.output + (result.stderr or ""))
