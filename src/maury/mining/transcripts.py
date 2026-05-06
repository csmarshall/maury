"""Transcript JSONL parsing + noise filtering.

Reads `~/.claude/projects/*/...jsonl` files and yields user-authored
messages with provenance. Drops Claude Code's system-injected
pseudo-user content (the patterns the prototype identified — see
ADR-0020 prototype findings).
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

# Per ADR-0020 prototype findings: these are the pseudo-user content
# patterns that Claude Code injects under `type:"user"` events. They are
# not authored by the human user and must be dropped before mining.
SYSTEM_INJECTED_PREFIXES = (
    "[Request interrupted",
    "<system-reminder>",
    "<local-command-",
    "<command-name>",
    "<command-message>",
    "<command-args>",
    "<task-notification>",
    "<bash-input>",
    "<bash-stdout>",
    "<bash-stderr>",
    "<user-prompt-submit-hook>",
    "<ide_selection>",
    "<ide_opened_file>",
    "Caveat: The messages below were generated",
)

# Default length bounds — too short = noise/yes/no; too long = pasted file content.
DEFAULT_MIN_CHARS = 30
DEFAULT_MAX_CHARS = 4000


@dataclass(frozen=True)
class TranscriptMessage:
    """One user-authored message extracted from a transcript JSONL.

    All fields are stable across re-runs (same transcript file produces
    the same TranscriptMessage objects), so timestamps + session_id can
    be used for watermarking and de-duplication.
    """

    project: str  # e.g., "-Users-charles-work-link-ctl"
    transcript_path: Path
    session_id: str
    timestamp: str  # ISO-8601 from the JSONL event
    text: str  # the user's text, content-block-flattened


def walk_user_messages(
    project_dir: Path,
    *,
    min_chars: int = DEFAULT_MIN_CHARS,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> Iterable[TranscriptMessage]:
    """Yield filtered, real user-authored messages from one project's transcripts.

    Skips:
      - lines that don't parse as JSON
      - events whose type isn't "user" or whose role isn't "user"
      - system-injected pseudo-user content (per SYSTEM_INJECTED_PREFIXES)
      - messages outside the [min_chars, max_chars] length window
    """
    for jsonl_path in sorted(project_dir.rglob("*.jsonl")):
        yield from walk_user_messages_in_file(
            jsonl_path,
            project=project_dir.name,
            min_chars=min_chars,
            max_chars=max_chars,
        )


def walk_user_messages_in_file(
    jsonl_path: Path,
    *,
    project: str,
    min_chars: int = DEFAULT_MIN_CHARS,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> Iterable[TranscriptMessage]:
    """Same filters as `walk_user_messages` but for a single JSONL file."""
    try:
        with jsonl_path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("type") != "user":
                    continue
                msg_obj = event.get("message", {})
                if msg_obj.get("role") != "user":
                    continue
                text = _extract_content_text(msg_obj.get("content"))
                if text is None:
                    continue
                if not _is_real_user_text(text, min_chars=min_chars, max_chars=max_chars):
                    continue
                yield TranscriptMessage(
                    project=project,
                    transcript_path=jsonl_path,
                    session_id=event.get("sessionId", ""),
                    timestamp=event.get("timestamp", ""),
                    text=text,
                )
    except OSError:
        # Unreadable file — skip silently. (Production miner can log; this
        # is the parser layer.)
        return


def _extract_content_text(content: object) -> str | None:
    """The `message.content` can be a string OR a list of content blocks.

    Returns the joined text content, or None if the field is missing or
    contains no text.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        joined = "\n".join(p for p in parts if p)
        return joined or None
    return None


def _is_real_user_text(
    text: str,
    *,
    min_chars: int,
    max_chars: int,
) -> bool:
    """True if this looks like a real user-authored message we want to mine."""
    stripped = text.lstrip()
    if stripped.startswith(SYSTEM_INJECTED_PREFIXES):
        return False
    if not min_chars <= len(stripped) <= max_chars:
        return False
    # Mostly-non-word content (terminal output, file paths, etc.) is
    # noise. Threshold: at least 40% of chars must be alphabetic.
    word_chars = sum(c.isalpha() for c in text)
    return not word_chars < len(text) * 0.4


__all__ = [
    "DEFAULT_MAX_CHARS",
    "DEFAULT_MIN_CHARS",
    "SYSTEM_INJECTED_PREFIXES",
    "TranscriptMessage",
    "walk_user_messages",
    "walk_user_messages_in_file",
]
