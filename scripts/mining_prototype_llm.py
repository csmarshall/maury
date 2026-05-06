#!/usr/bin/env python3
"""LLM-based mining prototype — the real validation question.

Runs `claude -p` (per ADR-0012; uses your Claude Code subscription quota
rather than a separate API key) over windowed chunks of your real
transcript history, extracts candidate durable preferences, and shows
what comes out.

This is the prototype that *actually* validates whether mining produces
useful signal — the previous lexical-clustering version proved only that
n-grams don't capture semantic patterns.

Run:
    uv run python scripts/mining_prototype_llm.py [--project NAME] [--max-windows N]

Defaults: process the project with the most user messages; cap at 3
windows of 50 user messages each (~150 messages total) to keep a first
run fast and cheap.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from textwrap import dedent

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
    has nothing durable, output a single line: `{"finding": "none"}`.

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


def extract_user_messages(jsonl_path: Path) -> list[dict]:
    """Pull user-authored messages from one JSONL transcript."""
    out: list[dict] = []
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
                msg = event.get("message", {})
                if msg.get("role") != "user":
                    continue
                text = _content_text(msg.get("content"))
                if text is None:
                    continue
                out.append(
                    {
                        "session": event.get("sessionId", ""),
                        "ts": event.get("timestamp", ""),
                        "text": text,
                    }
                )
    except OSError:
        pass
    return out


def _content_text(content: object) -> str | None:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(p for p in parts if p) or None
    return None


def filter_real_user_messages(msgs: list[dict]) -> list[dict]:
    """Drop system-injected pseudo-user content; keep authored messages of useful length."""
    out: list[dict] = []
    for m in msgs:
        text_left = m["text"].lstrip()
        if text_left.startswith(SYSTEM_INJECTED_PREFIXES):
            continue
        if 30 <= len(text_left) <= 4000:
            out.append(m)
    return out


def window(items: list[dict], n: int) -> Iterable[list[dict]]:
    for i in range(0, len(items), n):
        yield items[i : i + n]


def call_claude_p(prompt: str, *, timeout: int = 120) -> str:
    """Invoke `claude -p` non-interactively. Returns stdout."""
    proc = subprocess.run(
        ["claude", "-p"],
        input=prompt,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=True,
    )
    return proc.stdout


def parse_jsonl_output(text: str) -> list[dict]:
    """Parse JSONL lines from the model's stdout, ignoring prose/whitespace."""
    findings: list[dict] = []
    for raw in text.splitlines():
        s = raw.strip()
        if not s.startswith("{"):
            continue
        try:
            obj = json.loads(s)
        except json.JSONDecodeError:
            continue
        if "finding" in obj and obj["finding"] == "none":
            continue
        findings.append(obj)
    return findings


def format_window(msgs: list[dict]) -> str:
    return "\n\n".join(f"[{m['ts'][:19]}] {m['text']}" for m in msgs)


def pick_default_project(projects_dir: Path) -> str:
    """Pick the project with the most user-authored messages by default."""
    counts: Counter[str] = Counter()
    for jsonl in projects_dir.rglob("*.jsonl"):
        try:
            project = jsonl.relative_to(projects_dir).parts[0]
        except ValueError:
            project = jsonl.parent.name
        for m in filter_real_user_messages(extract_user_messages(jsonl)):
            counts[project] += 1
    if not counts:
        raise SystemExit(f"no user messages found under {projects_dir}")
    return counts.most_common(1)[0][0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--projects-dir",
        type=Path,
        default=Path.home() / ".claude" / "projects",
    )
    parser.add_argument("--project", help="Project hash dir name (default: highest-volume project).")
    parser.add_argument("--window-size", type=int, default=50, help="Messages per window.")
    parser.add_argument("--max-windows", type=int, default=3, help="Cap windows per run (cost control).")
    args = parser.parse_args()

    if not args.projects_dir.is_dir():
        print(f"projects-dir not found: {args.projects_dir}", file=sys.stderr)
        return 2

    project = args.project or pick_default_project(args.projects_dir)
    project_dir = args.projects_dir / project
    if not project_dir.is_dir():
        print(f"project not found: {project_dir}", file=sys.stderr)
        return 2

    print(f"project: {project}")
    print(f"window-size: {args.window_size}, max-windows: {args.max_windows}")

    msgs: list[dict] = []
    for jsonl in sorted(project_dir.glob("*.jsonl")):
        msgs.extend(extract_user_messages(jsonl))
    real = filter_real_user_messages(msgs)
    print(f"raw user messages: {len(msgs)}; after noise filter: {len(real)}")

    windows = list(window(real, args.window_size))
    take = windows[: args.max_windows]
    print(f"windows: {len(windows)} (processing first {len(take)})")

    all_findings: list[dict] = []
    for i, w in enumerate(take, start=1):
        prompt = EXTRACTION_PROMPT + format_window(w)
        approx_tokens = len(prompt) // 4
        print(f"\n--- window {i}/{len(take)}: {len(w)} messages, ~{approx_tokens} input tokens ---")
        try:
            output = call_claude_p(prompt)
        except subprocess.CalledProcessError as e:
            print(f"  claude -p failed (rc={e.returncode}): {e.stderr[:400]}")
            return 1
        except subprocess.TimeoutExpired:
            print("  claude -p timed out (120s); skipping window")
            continue
        findings = parse_jsonl_output(output)
        for f in findings:
            f["_window"] = i
        print(f"  {len(findings)} candidate finding(s)")
        if not findings:
            print("  (raw output, first 500 chars):")
            print("  " + output[:500].replace("\n", "\n  "))
        all_findings.extend(findings)

    print(f"\n=== {len(all_findings)} total findings across {len(take)} window(s) ===\n")
    for f in all_findings:
        conf = f.get("confidence", "?")
        kind = f.get("kind", "?")
        scope = f.get("scope_hint", "?")
        text = f.get("text", "?")
        evidence = (f.get("evidence") or "")[:200]
        print(f"[{conf}] {kind}/{scope}: {text}")
        if evidence:
            print(f"   evidence: {evidence!r}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
