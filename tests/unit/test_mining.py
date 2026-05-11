"""Tests for the mining module (Phase 6a)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from maury.mining import (
    EXTRACTION_PROMPT,
    SYSTEM_INJECTED_PREFIXES,
    ExtractionWindow,
    Finding,
    TranscriptMessage,
    extract_from_messages,
    format_window_for_prompt,
    make_windows,
    walk_user_messages,
    walk_user_messages_in_file,
)

# ---- helpers ------------------------------------------------------------


def _write_jsonl(path: Path, events: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n")


def _user_event(text: str, *, session: str = "s1", ts: str = "2026-05-06T12:00:00Z") -> dict[str, Any]:
    return {
        "type": "user",
        "sessionId": session,
        "timestamp": ts,
        "message": {"role": "user", "content": text},
    }


def _assistant_event(text: str) -> dict[str, Any]:
    return {
        "type": "assistant",
        "message": {"role": "assistant", "content": text},
    }


# ---- transcripts.walk_user_messages_in_file -----------------------------


def test_walk_yields_real_user_messages(tmp_path: Path) -> None:
    jsonl = tmp_path / "transcript.jsonl"
    _write_jsonl(
        jsonl,
        [
            _user_event("This is a real user message that's long enough to pass."),
            _assistant_event("Sure, I'll help with that."),
            _user_event("And another follow-up message of reasonable length."),
        ],
    )
    msgs = list(walk_user_messages_in_file(jsonl, project="test-project"))
    assert len(msgs) == 2
    assert all(isinstance(m, TranscriptMessage) for m in msgs)
    assert all(m.project == "test-project" for m in msgs)
    assert msgs[0].text.startswith("This is a real")


def test_walk_drops_system_injected_prefixes(tmp_path: Path) -> None:
    jsonl = tmp_path / "t.jsonl"
    _write_jsonl(
        jsonl,
        [
            _user_event("[Request interrupted by user for tool use]"),
            _user_event("<system-reminder>blah blah blah</system-reminder>"),
            _user_event("<task-notification><id>x</id></task-notification>"),
            _user_event("This is a genuine user message of sufficient length."),
        ],
    )
    msgs = list(walk_user_messages_in_file(jsonl, project="p"))
    assert len(msgs) == 1
    assert "genuine user message" in msgs[0].text


def test_walk_drops_too_short(tmp_path: Path) -> None:
    jsonl = tmp_path / "t.jsonl"
    _write_jsonl(
        jsonl,
        [
            _user_event("yes"),  # 3 chars, way under min
            _user_event("This message is sufficiently long to clear the minimum threshold."),
        ],
    )
    msgs = list(walk_user_messages_in_file(jsonl, project="p"))
    assert len(msgs) == 1


def test_walk_drops_too_long(tmp_path: Path) -> None:
    jsonl = tmp_path / "t.jsonl"
    _write_jsonl(
        jsonl,
        [
            _user_event("x" * 5000),  # too long, likely pasted file
            _user_event("Reasonable length message that should pass through."),
        ],
    )
    msgs = list(walk_user_messages_in_file(jsonl, project="p"))
    assert len(msgs) == 1


def test_walk_handles_content_blocks(tmp_path: Path) -> None:
    """message.content can be a list of blocks instead of a string."""
    jsonl = tmp_path / "t.jsonl"
    _write_jsonl(
        jsonl,
        [
            {
                "type": "user",
                "sessionId": "s",
                "timestamp": "2026-05-06T00:00:00Z",
                "message": {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "First block of real content here."},
                        {"type": "text", "text": "Second block of more content."},
                    ],
                },
            }
        ],
    )
    msgs = list(walk_user_messages_in_file(jsonl, project="p"))
    assert len(msgs) == 1
    assert "First block" in msgs[0].text
    assert "Second block" in msgs[0].text


def test_walk_skips_invalid_json_lines(tmp_path: Path) -> None:
    jsonl = tmp_path / "t.jsonl"
    jsonl.write_text(
        json.dumps(_user_event("Real message that should be picked up here.")) + "\n"
        "this is not json at all\n" + json.dumps(_user_event("Another real message of acceptable length here.")) + "\n"
    )
    msgs = list(walk_user_messages_in_file(jsonl, project="p"))
    assert len(msgs) == 2


def test_walk_skips_non_user_events(tmp_path: Path) -> None:
    jsonl = tmp_path / "t.jsonl"
    _write_jsonl(
        jsonl,
        [
            {"type": "permission-mode", "permissionMode": "default"},
            _assistant_event("I'm assistant text"),
            _user_event("This is the only user message we should yield."),
        ],
    )
    msgs = list(walk_user_messages_in_file(jsonl, project="p"))
    assert len(msgs) == 1


def test_walk_drops_mostly_non_alpha_content(tmp_path: Path) -> None:
    """Terminal output / file paths / mostly numeric content shouldn't pass."""
    jsonl = tmp_path / "t.jsonl"
    _write_jsonl(
        jsonl,
        [
            _user_event("12345 67890 //// /// /// /// /// /// /// /// /// /// /// "),
            _user_event("This is a real sentence with mostly alphabetic content."),
        ],
    )
    msgs = list(walk_user_messages_in_file(jsonl, project="p"))
    assert len(msgs) == 1


def test_walk_user_messages_recurses_subdirs(tmp_path: Path) -> None:
    """walk_user_messages walks all .jsonl files under a project dir."""
    proj = tmp_path / "project"
    _write_jsonl(proj / "a.jsonl", [_user_event("Message A from one transcript file.")])
    _write_jsonl(proj / "subdir" / "b.jsonl", [_user_event("Message B from another transcript file.")])
    msgs = list(walk_user_messages(proj))
    texts = {m.text for m in msgs}
    assert any("Message A" in t for t in texts)
    assert any("Message B" in t for t in texts)


def test_walk_unreadable_file_returns_empty(tmp_path: Path) -> None:
    """An unreadable file returns no messages, not an exception."""
    msgs = list(walk_user_messages_in_file(tmp_path / "nonexistent.jsonl", project="p"))
    assert msgs == []


# ---- extractor.make_windows ---------------------------------------------


def _msg(text: str, i: int = 0) -> TranscriptMessage:
    return TranscriptMessage(
        project="test",
        transcript_path=Path("/x.jsonl"),
        session_id="s",
        timestamp=f"2026-05-06T00:00:{i:02d}Z",
        text=text,
    )


def test_make_windows_chunks_correctly() -> None:
    msgs = [_msg(f"msg {i}", i) for i in range(120)]
    windows = make_windows(msgs, "p", window_size=50)
    assert len(windows) == 3
    assert len(windows[0].messages) == 50
    assert len(windows[1].messages) == 50
    assert len(windows[2].messages) == 20
    assert windows[0].index == 0
    assert windows[2].index == 2


def test_make_windows_empty_input() -> None:
    assert make_windows([], "p", window_size=50) == []


def test_make_windows_smaller_than_window_size() -> None:
    msgs = [_msg("a"), _msg("b")]
    windows = make_windows(msgs, "p", window_size=50)
    assert len(windows) == 1
    assert len(windows[0].messages) == 2


def test_window_first_last_timestamps() -> None:
    msgs = [_msg("a", 0), _msg("b", 1), _msg("c", 2)]
    windows = make_windows(msgs, "p", window_size=10)
    w = windows[0]
    assert w.first_timestamp.endswith("00Z")
    assert w.last_timestamp.endswith("02Z")


# ---- extractor.format_window_for_prompt ---------------------------------


def test_format_window_includes_timestamps_and_text() -> None:
    msgs = [_msg("first text", 0), _msg("second text", 1)]
    windows = make_windows(msgs, "p", window_size=10)
    out = format_window_for_prompt(windows[0])
    assert "first text" in out
    assert "second text" in out
    assert "2026-05-06" in out  # timestamp prefix


# ---- extractor.extract_from_messages (LLM call mocked) ------------------


class _StubLLM:
    """Test double for LLMClient."""

    name = "stub"

    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.calls: list[str] = []

    def call(self, prompt: str, *, timeout: float = 120.0) -> str:
        self.calls.append(prompt)
        if not self.responses:
            return ""
        return self.responses.pop(0)


def test_extract_from_messages_parses_valid_jsonl_response() -> None:
    msgs = [_msg(f"text {i}", i) for i in range(50)]
    llm = _StubLLM(
        [
            '{"kind": "workflow", "scope_hint": "base", "text": "user prefers TDD", '
            '"evidence": "tests first", "confidence": "high"}\n'
            '{"kind": "tool", "scope_hint": "base", "text": "ruff for linting", '
            '"evidence": "use ruff", "confidence": "medium"}'
        ]
    )
    result = extract_from_messages(msgs, llm=llm, project="test", window_size=50)
    assert result.windows_processed == 1
    assert len(result.findings) == 2
    assert result.findings[0].kind == "workflow"
    assert result.findings[0].text == "user prefers TDD"
    assert result.findings[0].confidence == "high"
    assert result.findings[1].text == "ruff for linting"
    assert llm.calls  # at least one call made


def test_extract_from_messages_handles_finding_none_sentinel() -> None:
    msgs = [_msg("text", 0)]
    llm = _StubLLM(['{"finding": "none"}'])
    result = extract_from_messages(msgs, llm=llm, project="test", window_size=50)
    assert result.windows_processed == 1
    assert result.findings == []


def test_extract_from_messages_skips_invalid_lines() -> None:
    """LLM might wrap output in prose; we still pick up the JSON."""
    msgs = [_msg("text", 0)]
    response = (
        "Here are my findings:\n"
        '{"kind": "voice", "scope_hint": "base", "text": "be concise", '
        '"evidence": "no preamble", "confidence": "high"}\n'
        "That's all I found.\n"
    )
    llm = _StubLLM([response])
    result = extract_from_messages(msgs, llm=llm, project="test", window_size=50)
    assert len(result.findings) == 1
    assert result.findings[0].text == "be concise"


def test_extract_from_messages_skips_findings_missing_required_fields() -> None:
    msgs = [_msg("text", 0)]
    response = (
        '{"kind": "workflow"}\n'  # missing scope_hint and text
        '{"scope_hint": "base", "text": "incomplete"}\n'  # missing kind
        '{"kind": "ok", "scope_hint": "base", "text": "complete one", '
        '"evidence": "x", "confidence": "low"}'
    )
    llm = _StubLLM([response])
    result = extract_from_messages(msgs, llm=llm, project="test", window_size=50)
    assert len(result.findings) == 1
    assert result.findings[0].text == "complete one"


def test_extract_from_messages_processes_multiple_windows() -> None:
    msgs = [_msg(f"text {i}", i) for i in range(120)]
    llm = _StubLLM(
        [
            '{"kind": "k1", "scope_hint": "base", "text": "from window 0", "evidence": "e", "confidence": "high"}',
            '{"kind": "k2", "scope_hint": "base", "text": "from window 1", "evidence": "e", "confidence": "high"}',
            '{"kind": "k3", "scope_hint": "base", "text": "from window 2", "evidence": "e", "confidence": "high"}',
        ]
    )
    result = extract_from_messages(msgs, llm=llm, project="test", window_size=50)
    assert result.windows_processed == 3
    assert len(result.findings) == 3
    texts = [f.text for f in result.findings]
    assert "from window 0" in texts
    assert "from window 2" in texts


def test_extract_from_messages_respects_max_windows() -> None:
    msgs = [_msg(f"text {i}", i) for i in range(200)]
    llm = _StubLLM(['{"finding": "none"}'] * 10)
    result = extract_from_messages(msgs, llm=llm, project="test", window_size=50, max_windows=2)
    assert result.windows_processed == 2
    assert len(llm.calls) == 2


def test_extract_from_messages_calls_progress_callbacks() -> None:
    msgs = [_msg("x", 0)] * 50
    llm = _StubLLM(['{"finding": "none"}'])
    starts: list[ExtractionWindow] = []
    dones: list[tuple[ExtractionWindow, list[Finding]]] = []
    extract_from_messages(
        msgs,
        llm=llm,
        project="test",
        window_size=50,
        on_window_start=lambda w: starts.append(w),
        on_window_done=lambda w, f: dones.append((w, f)),
    )
    assert len(starts) == 1
    assert len(dones) == 1
    assert dones[0][1] == []  # findings


def test_extract_from_messages_continues_past_window_failure() -> None:
    """If one window's LLM call raises, mining continues with the rest."""
    msgs = [_msg(f"x {i}", i) for i in range(150)]

    class _FailFirst:
        name = "stub"

        def __init__(self) -> None:
            self.n = 0

        def call(self, prompt: str, *, timeout: float = 120.0) -> str:
            self.n += 1
            if self.n == 1:
                raise RuntimeError("first call fails")
            return '{"kind": "k", "scope_hint": "base", "text": "later finding", "evidence": "e", "confidence": "high"}'

    result = extract_from_messages(msgs, llm=_FailFirst(), project="test", window_size=50)
    # 3 windows; window 0 failed, windows 1+2 succeeded
    assert result.windows_processed == 2
    assert len(result.findings) == 2
    assert any("extraction failed" in w for w in result.warnings)


# ---- extraction prompt sanity ------------------------------------------


def test_extraction_prompt_is_non_empty_and_has_schema() -> None:
    """Sanity: the prompt template is well-formed enough to be useful."""
    assert "ONE JSON object per line" in EXTRACTION_PROMPT
    assert "kind" in EXTRACTION_PROMPT
    assert "scope_hint" in EXTRACTION_PROMPT
    assert "evidence" in EXTRACTION_PROMPT


# ---- system-injected prefixes registry ----------------------------------


def test_system_injected_prefixes_includes_known_patterns() -> None:
    """Smoke: the registry of patterns includes the ones the prototype found."""
    expected = (
        "[Request interrupted",
        "<system-reminder>",
        "<local-command-",
        "<task-notification>",
    )
    for prefix in expected:
        assert prefix in SYSTEM_INJECTED_PREFIXES
