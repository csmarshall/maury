"""maury mining — extract durable preferences from transcripts.

Per ADR-0020: bulk onboarding mode + incremental maintenance mode share
infrastructure. This Phase 6a slice ships the windowed-LLM-extraction
core; subsequent phases add cross-window grouping (6b), temporal
cross-reference (6c), and the proposal-review UI (Phase 7).
"""

from .crossref import (
    CrossRefResult,
    CrossRefSummary,
    crossref_finding,
    crossref_findings,
    get_claude_md_at_timestamp,
)
from .extractor import (
    DEFAULT_WINDOW_SIZE,
    EXTRACTION_PROMPT,
    ExtractionResult,
    ExtractionWindow,
    Finding,
    extract_from_messages,
    extract_window,
    format_window_for_prompt,
    make_windows,
)
from .transcripts import (
    DEFAULT_MAX_CHARS,
    DEFAULT_MIN_CHARS,
    SYSTEM_INJECTED_PREFIXES,
    TranscriptMessage,
    walk_user_messages,
    walk_user_messages_in_file,
)

__all__ = [
    "DEFAULT_MAX_CHARS",
    "DEFAULT_MIN_CHARS",
    "DEFAULT_WINDOW_SIZE",
    "EXTRACTION_PROMPT",
    "SYSTEM_INJECTED_PREFIXES",
    "CrossRefResult",
    "CrossRefSummary",
    "ExtractionResult",
    "ExtractionWindow",
    "Finding",
    "TranscriptMessage",
    "crossref_finding",
    "crossref_findings",
    "extract_from_messages",
    "extract_window",
    "format_window_for_prompt",
    "get_claude_md_at_timestamp",
    "make_windows",
    "walk_user_messages",
    "walk_user_messages_in_file",
]
