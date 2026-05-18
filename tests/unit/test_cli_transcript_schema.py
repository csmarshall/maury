"""Tests for `maury verify-cc-transcript-schema`.

The harness itself spawns `claude -p` and requires a real Claude Code
install — those tests are skip-if-no-claude and live elsewhere. Here
we cover:

- The pure `analyze_transcript_jsonl()` function (every branch).
- The CLI command's `--check-only` flag (monkeypatched).
- The `_print_transcript_schema_report()` formatter (fixture reports).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from maury.cli import _print_transcript_schema_report, main
from maury.empirical_tests import (
    TranscriptSchemaHarnessReport,
    TranscriptSchemaProbeResult,
    analyze_transcript_jsonl,
)

# ---- analyze_transcript_jsonl: shape detection -------------------------


def test_analyze_empty_text_returns_zero_counts() -> None:
    result = analyze_transcript_jsonl("")
    assert result.line_count == 0
    assert result.parsed_json_count == 0
    assert result.user_messages_total == 0
    assert result.passed is False  # No parseable JSON = no usable data


def test_analyze_single_well_formed_user_message() -> None:
    """The canonical mining-compatible user-message shape."""
    line = json.dumps(
        {
            "type": "user",
            "message": {"role": "user", "content": "hello there"},
            "sessionId": "session-abc",
            "timestamp": "2026-05-18T10:00:00Z",
        }
    )
    result = analyze_transcript_jsonl(line + "\n")
    assert result.parsed_json_count == 1
    assert result.user_messages_total == 1
    assert result.user_messages_mining_compatible == 1
    assert result.by_type == {"user": 1}
    assert result.content_shapes == {"string": 1}
    assert result.passed is True
    assert result.findings["all_lines_have_required_fields"] is True


def test_analyze_user_message_with_list_content_blocks() -> None:
    """Content as a list of `{type: text, text: ...}` blocks — also
    mining-compatible per `_extract_content_text`."""
    line = json.dumps(
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {"type": "text", "text": "block one"},
                    {"type": "text", "text": "block two"},
                ],
            },
            "sessionId": "s1",
            "timestamp": "2026-05-18T10:00:00Z",
        }
    )
    result = analyze_transcript_jsonl(line)
    assert result.content_shapes == {"list-of-text-blocks": 1}
    assert result.user_messages_mining_compatible == 1


def test_analyze_user_message_with_mixed_content_is_classified_mixed() -> None:
    """Content list mixing text + tool_use blocks → list-mixed shape."""
    line = json.dumps(
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {"type": "text", "text": "before"},
                    {"type": "tool_use", "id": "abc", "name": "Edit"},
                ],
            },
            "sessionId": "s1",
            "timestamp": "2026-05-18T10:00:00Z",
        }
    )
    result = analyze_transcript_jsonl(line)
    assert result.content_shapes == {"list-mixed": 1}
    # Mining still extracts the text block.
    assert result.user_messages_mining_compatible == 1


def test_analyze_empty_list_content_is_classified_list_empty() -> None:
    line = json.dumps(
        {
            "type": "user",
            "message": {"role": "user", "content": []},
            "sessionId": "s1",
            "timestamp": "t",
        }
    )
    result = analyze_transcript_jsonl(line)
    assert result.content_shapes == {"list-empty": 1}
    # Mining gets no extractable text → not mining-compatible.
    assert result.user_messages_mining_compatible == 0


def test_analyze_non_user_event_types_are_counted() -> None:
    """Assistant + system messages get type-counted but don't add to
    `user_messages_total`."""
    lines = [
        json.dumps(
            {"type": "assistant", "message": {"role": "assistant", "content": "hi"}, "sessionId": "s", "timestamp": "t"}
        ),
        json.dumps(
            {"type": "system", "message": {"role": "system", "content": "init"}, "sessionId": "s", "timestamp": "t"}
        ),
        json.dumps(
            {"type": "user", "message": {"role": "user", "content": "hello"}, "sessionId": "s", "timestamp": "t"}
        ),
    ]
    result = analyze_transcript_jsonl("\n".join(lines))
    assert result.by_type == {"assistant": 1, "system": 1, "user": 1}
    assert result.user_messages_total == 1


def test_analyze_malformed_json_lines_are_counted_but_not_parsed() -> None:
    text = (
        "not json\n"
        + json.dumps({"type": "user", "message": {"role": "user", "content": "x"}, "sessionId": "s", "timestamp": "t"})
        + "\nalso not json\n"
    )
    result = analyze_transcript_jsonl(text)
    assert result.line_count == 3
    assert result.parsed_json_count == 1


def test_analyze_user_message_without_role_is_not_mining_compatible() -> None:
    """`message.role` missing → mining's parser would reject."""
    line = json.dumps(
        {
            "type": "user",
            "message": {"content": "hello"},  # no role field
            "sessionId": "s",
            "timestamp": "t",
        }
    )
    result = analyze_transcript_jsonl(line)
    assert result.user_messages_total == 1
    assert result.user_messages_mining_compatible == 0


def test_analyze_user_message_with_role_assistant_not_mining_compatible() -> None:
    """`type=user` but `message.role=assistant` (a system-injected
    pseudo-user message) → mining rejects."""
    line = json.dumps(
        {
            "type": "user",
            "message": {"role": "assistant", "content": "x"},
            "sessionId": "s",
            "timestamp": "t",
        }
    )
    result = analyze_transcript_jsonl(line)
    assert result.user_messages_total == 1
    assert result.user_messages_mining_compatible == 0


def test_analyze_missing_required_fields_flagged_in_findings() -> None:
    """A line missing `sessionId` or `timestamp` should flip the
    `all_lines_have_required_fields` flag to False."""
    line = json.dumps({"type": "user", "message": {"role": "user", "content": "x"}})  # no sessionId, no timestamp
    result = analyze_transcript_jsonl(line)
    assert result.findings["all_lines_have_required_fields"] is False


def test_analyze_sample_lines_captures_first_five() -> None:
    """At most 5 sample lines kept for the human-display section."""
    lines = [
        json.dumps({"type": "user", "message": {"role": "user", "content": str(i)}, "sessionId": "s", "timestamp": "t"})
        for i in range(10)
    ]
    result = analyze_transcript_jsonl("\n".join(lines))
    assert len(result.sample_lines) == 5
    assert result.sample_lines[0].line_number == 1
    assert result.sample_lines[4].line_number == 5


# ---- CLI --check-only --------------------------------------------------


def test_verify_cc_transcript_schema_check_only_claude_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("maury.empirical_tests.shutil.which", lambda _name: None)
    runner = CliRunner()
    result = runner.invoke(main, ["verify-cc-transcript-schema", "--check-only"])
    assert result.exit_code == 1
    assert "`claude` not found on PATH" in result.output


def test_verify_cc_transcript_schema_check_only_claude_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("maury.empirical_tests.shutil.which", lambda _name: "/usr/local/bin/claude")
    runner = CliRunner()
    result = runner.invoke(main, ["verify-cc-transcript-schema", "--check-only"])
    assert result.exit_code == 0
    assert "`claude` is present on PATH" in result.output


# ---- _print_transcript_schema_report ----------------------------------


def _make_probe(
    *,
    jsonl_path: Path = Path("/tmp/fake.jsonl"),
    passed: bool = True,
    user_messages_total: int = 5,
    mining_compatible: int = 5,
    by_type: dict[str, int] | None = None,
    shapes: dict[str, int] | None = None,
    all_required: bool = True,
) -> TranscriptSchemaProbeResult:
    return TranscriptSchemaProbeResult(
        jsonl_path=jsonl_path,
        line_count=10,
        parsed_json_count=10,
        by_type=by_type or {"user": user_messages_total, "assistant": 5},
        content_shapes=shapes or {"string": user_messages_total},
        user_messages_total=user_messages_total,
        user_messages_mining_compatible=mining_compatible,
        sample_lines=(),
        passed=passed,
        detail="passed run",
        findings={"all_lines_have_required_fields": all_required},
    )


def test_print_report_text_no_claude(capsys: pytest.CaptureFixture[str]) -> None:
    report = TranscriptSchemaHarnessReport(
        workspace=Path("/tmp/ws"),
        claude_present=False,
        claude_version=None,
        probes=(),
        claude_returncode=None,
        claude_stderr_excerpt="missing",
    )
    _print_transcript_schema_report(report, "text")
    out = capsys.readouterr().out
    assert "`claude` not found" in out


def test_print_report_text_passing_run(capsys: pytest.CaptureFixture[str]) -> None:
    report = TranscriptSchemaHarnessReport(
        workspace=Path("/tmp/ws"),
        claude_present=True,
        claude_version="2.1.141",
        probes=(_make_probe(),),
        claude_returncode=0,
        claude_stderr_excerpt="",
    )
    _print_transcript_schema_report(report, "text")
    out = capsys.readouterr().out
    assert "2.1.141" in out
    assert "✅" in out
    assert "by type:" in out
    assert "mining-compatible" in out
    assert "✓ present on all sampled lines" in out


def test_print_report_text_flags_missing_required_fields(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """When `all_lines_have_required_fields` is False, the warning fires."""
    report = TranscriptSchemaHarnessReport(
        workspace=Path("/tmp/ws"),
        claude_present=True,
        claude_version="2.1.141",
        probes=(_make_probe(all_required=False),),
        claude_returncode=0,
        claude_stderr_excerpt="",
    )
    _print_transcript_schema_report(report, "text")
    out = capsys.readouterr().out
    assert "⚠️ missing on some lines" in out


def test_print_report_text_no_probes_means_no_jsonl_produced(
    capsys: pytest.CaptureFixture[str],
) -> None:
    report = TranscriptSchemaHarnessReport(
        workspace=Path("/tmp/ws"),
        claude_present=True,
        claude_version="2.1.141",
        probes=(),
        claude_returncode=0,
        claude_stderr_excerpt="",
    )
    _print_transcript_schema_report(report, "text")
    out = capsys.readouterr().out
    assert "no transcript JSONL files were produced" in out


def test_print_report_json_round_trips(capsys: pytest.CaptureFixture[str]) -> None:
    report = TranscriptSchemaHarnessReport(
        workspace=Path("/tmp/ws"),
        claude_present=True,
        claude_version="2.1.141",
        probes=(_make_probe(),),
        claude_returncode=0,
        claude_stderr_excerpt="",
    )
    _print_transcript_schema_report(report, "json")
    payload = json.loads(capsys.readouterr().out)
    assert payload["workspace"] == "/tmp/ws"
    assert payload["claude_version"] == "2.1.141"
    assert payload["passed"] is True
    assert len(payload["probes"]) == 1
    assert payload["probes"][0]["user_messages_total"] == 5
