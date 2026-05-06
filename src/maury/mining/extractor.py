"""Windowed LLM extraction (Phase 6a per ADR-0020).

Takes a stream of TranscriptMessage objects, batches them into windows
of N messages, and asks the configured LLM to extract durable
preferences from each window.

Output: Finding objects with provenance (source window, evidence quote,
confidence). Findings then flow into Phase 6b (cross-window grouping)
and Phase 6c (cross-reference against current config) — both wired up
in subsequent commits.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from textwrap import dedent

from maury.llm import LLMClient

from .transcripts import TranscriptMessage

# The extraction prompt. Validated end-to-end against Charles's real
# transcripts in scripts/mining_prototype_llm.py — surfaced 18 real
# findings from 3 windows of 50 messages.
EXTRACTION_PROMPT = dedent(
    """\
    You are reviewing a window of user messages from one user's Claude Code
    conversation history. Your job is to identify any DURABLE patterns the
    user expresses — things they would want Claude to do the same way in
    future sessions and on other machines they use.

    Look for:
      - voice/style preferences ("be concise", "no preamble", "skip summaries")
      - workflow patterns (TDD, code review, deployment style, commit conventions)
      - tool preferences (specific linters, formatters, package managers)
      - communication style preferences
      - explicit corrections of Claude's behavior ("don't do X" or "always do Y")
      - host-specific facts (which OS, which paths, which services run where)
      - identity/role facts (job, team, common projects)

    Do NOT extract:
      - one-off project-specific requests ("add this feature to my codebase")
      - debugging frustration ("that's still wrong")
      - conversation noise ("yes", "ok", "thanks")
      - things derivable from code (file paths, function names)
      - system-injected content the user didn't write

    Output format: ONE JSON object per line, NOTHING ELSE — no prose, no
    markdown, no preamble, no closing remarks. Just JSONL. If the window
    has nothing durable, output a single line: {"finding": "none"}

    Schema for each finding:
      {
        "kind": "voice" | "workflow" | "tool" | "style" | "correction" | "host-fact" | "identity",
        "scope_hint": "base" | "personal" | "work" | "host-specific" | "project-specific",
        "text": "<one-sentence description of the pattern, in your own words>",
        "evidence": "<short verbatim quote from the input that motivated this>",
        "confidence": "high" | "medium" | "low"
      }

    INPUT MESSAGES (each prefixed with timestamp):
    """
)

DEFAULT_WINDOW_SIZE = 50


@dataclass(frozen=True)
class ExtractionWindow:
    """A batch of TranscriptMessages submitted to the LLM together."""

    messages: tuple[TranscriptMessage, ...]
    project: str
    index: int  # 0-based window index within the project

    @property
    def first_timestamp(self) -> str:
        return self.messages[0].timestamp if self.messages else ""

    @property
    def last_timestamp(self) -> str:
        return self.messages[-1].timestamp if self.messages else ""

    @property
    def session_ids(self) -> set[str]:
        return {m.session_id for m in self.messages}


@dataclass(frozen=True)
class Finding:
    """One durable preference candidate the LLM extracted from a window."""

    kind: str
    scope_hint: str
    text: str
    evidence: str
    confidence: str
    source_window: ExtractionWindow
    raw_response_line: str = ""  # for debugging; the JSON line as parsed


@dataclass
class ExtractionResult:
    """Aggregate of all findings + warnings from one extraction run."""

    findings: list[Finding] = field(default_factory=list)
    windows_processed: int = 0
    warnings: list[str] = field(default_factory=list)


# ---- public API ----------------------------------------------------------


def make_windows(
    messages: list[TranscriptMessage],
    project: str,
    *,
    window_size: int = DEFAULT_WINDOW_SIZE,
) -> list[ExtractionWindow]:
    """Chunk a flat list of messages into ExtractionWindow batches."""
    windows: list[ExtractionWindow] = []
    for i in range(0, len(messages), window_size):
        chunk = messages[i : i + window_size]
        windows.append(
            ExtractionWindow(
                messages=tuple(chunk),
                project=project,
                index=len(windows),
            )
        )
    return windows


def format_window_for_prompt(window: ExtractionWindow) -> str:
    """Render a window's messages as the body of the extraction prompt."""
    return "\n\n".join(f"[{m.timestamp[:19]}] {m.text}" for m in window.messages)


def extract_window(
    window: ExtractionWindow,
    *,
    llm: LLMClient,
    timeout: float = 120.0,
) -> tuple[list[Finding], str]:
    """Extract findings from one window. Returns (findings, raw_llm_response)."""
    prompt = EXTRACTION_PROMPT + format_window_for_prompt(window)
    raw = llm.call(prompt, timeout=timeout)
    findings = _parse_findings(raw, window)
    return findings, raw


def extract_from_messages(
    messages: list[TranscriptMessage],
    *,
    llm: LLMClient,
    project: str,
    window_size: int = DEFAULT_WINDOW_SIZE,
    max_windows: int | None = None,
    timeout: float = 120.0,
    on_window_start: Callable[[ExtractionWindow], None] | None = None,
    on_window_done: Callable[[ExtractionWindow, list[Finding]], None] | None = None,
) -> ExtractionResult:
    """Extract findings from a flat list of messages.

    Args:
        messages: Already filtered / deduped TranscriptMessage list.
        llm: An LLMClient (use `maury.llm.get_backend()` to instantiate).
        project: Name to attach to the windows for provenance.
        window_size: Messages per window (default 50; per ADR-0020).
        max_windows: Cap for cost control; None = process all.
        timeout: Per-window LLM timeout in seconds.
        on_window_start, on_window_done: Optional callbacks for progress
            reporting (CLI uses these for per-window status lines).

    Returns:
        ExtractionResult with findings + counters + warnings.
    """
    result = ExtractionResult()
    windows = make_windows(messages, project, window_size=window_size)
    if max_windows is not None:
        windows = windows[:max_windows]

    for window in windows:
        if on_window_start is not None:
            on_window_start(window)
        try:
            findings, _raw = extract_window(window, llm=llm, timeout=timeout)
        except Exception as e:
            result.warnings.append(f"window {window.index}: extraction failed: {e}")
            continue
        result.findings.extend(findings)
        result.windows_processed += 1
        if on_window_done is not None:
            on_window_done(window, findings)

    return result


# ---- internals -----------------------------------------------------------


def _parse_findings(raw_text: str, window: ExtractionWindow) -> list[Finding]:
    """Parse JSONL findings from the LLM output. Tolerates surrounding prose.

    Each Finding line must be valid JSON with the expected schema fields.
    Lines that don't parse as JSON are silently skipped (the LLM
    occasionally adds explanatory text); lines parsing as the sentinel
    `{"finding": "none"}` mean "this window had nothing durable."
    """
    out: list[Finding] = []
    for line in raw_text.splitlines():
        s = line.strip()
        if not s.startswith("{"):
            continue
        try:
            obj = json.loads(s)
        except json.JSONDecodeError:
            continue
        if obj.get("finding") == "none":
            continue
        # Guard against missing fields
        kind = obj.get("kind")
        scope = obj.get("scope_hint")
        text = obj.get("text")
        if not (kind and scope and text):
            continue
        out.append(
            Finding(
                kind=str(kind),
                scope_hint=str(scope),
                text=str(text),
                evidence=str(obj.get("evidence", "")),
                confidence=str(obj.get("confidence", "medium")),
                source_window=window,
                raw_response_line=s,
            )
        )
    return out


__all__ = [
    "DEFAULT_WINDOW_SIZE",
    "EXTRACTION_PROMPT",
    "ExtractionResult",
    "ExtractionWindow",
    "Finding",
    "extract_from_messages",
    "extract_window",
    "format_window_for_prompt",
    "make_windows",
]
