#!/usr/bin/env python3
"""Mining prototype — validates whether bulk-mining of transcripts produces signal.

Per ADR-0020. This is a deliberately minimal one-file script: stdlib
only, no new deps, no LLM calls, no integration with the rest of
maury. Its purpose is to answer one question:

    "If we walk a real ~/.claude/projects/ corpus, pre-process the
    user messages, cluster them by similarity, and filter by
    frequency, do we find genuine recurring patterns — or noise?"

If the output looks like real preferences ("user keeps asking for
ruff", "user prefers tests-first refactor"), we invest in the full
Phase 6 build. If it's noise (random one-off questions), we revisit
the approach.

Run:
    uv run python scripts/mining_prototype.py [--projects-dir DIR] [--top N]

Defaults:
    projects-dir: ~/.claude/projects
    top:         30
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

# ---- Tunables (adjust freely while exploring) ----------------------------

MIN_CHARS = 30  # skip user messages shorter than this
MAX_CHARS = 1200  # skip user messages longer than this (likely pasted content / files)
MIN_NGRAM_SIZE = 3  # word-level n-grams: lower bound
MAX_NGRAM_SIZE = 5  # word-level n-grams: upper bound
JACCARD_THRESHOLD = 0.35  # minimum similarity to merge two messages into a cluster
MIN_OCCURRENCES = 2  # frequency floor for a cluster to be reported
SAMPLE_EXCERPT_COUNT = 3  # how many example excerpts to print per cluster

# Patterns to strip before tokenization
CODE_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)
INLINE_CODE_RE = re.compile(r"`[^`]+`")
URL_RE = re.compile(r"https?://\S+")
PATH_RE = re.compile(r"(?:/[A-Za-z0-9._-]+)+")
WHITESPACE_RE = re.compile(r"\s+")
WORD_RE = re.compile(r"[a-z][a-z0-9_-]+")  # word-tokens, lowercase

# Patterns that flag the entire message as Claude-Code-system-injected
# pseudo-user content (not actually user-authored). Drop these wholesale.
SYSTEM_INJECTED_PREFIXES = (
    "[Request interrupted",
    "<system-reminder>",
    "<local-command-caveat>",
    "<local-command-stdout>",
    "<local-command-stderr>",
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

# Substrings whose presence anywhere in the message likely means it's
# system-generated rather than user-authored (e.g., terminal output that
# the user pasted unconsciously, or shell wrappers).
SYSTEM_INJECTED_SUBSTRINGS = (
    "[Request interrupted by user",
    "% sudo ",
    "FAILED:",
    "Errno",
)

# Common boilerplate to filter out (these add no signal)
NOISE_PHRASES = {
    "yes",
    "no",
    "ok",
    "okay",
    "thanks",
    "thank you",
    "great",
    "perfect",
    "go",
    "go on",
    "continue",
    "keep going",
    "next",
    "?",
    "hmm",
    "do it",
    "make it so",
    "lgtm",
    "looks good",
    "lets go",
    "let's go",
    "sounds good",
    "sounds ok",
    "sounds great",
    "that's right",
}


@dataclass
class Message:
    """One pre-processed user message ready for clustering."""

    project: str  # path-hash dirname (e.g., "-Users-charles-work-claude-...")
    session_id: str  # session UUID
    text: str  # original message text (truncated)
    tokens: set[str]  # n-gram set for similarity computation
    timestamp: str  # ISO


@dataclass
class Cluster:
    """A group of similar messages."""

    members: list[Message] = field(default_factory=list)
    n_grams_combined: set[str] = field(default_factory=set)

    @property
    def size(self) -> int:
        return len(self.members)

    @property
    def distinct_sessions(self) -> int:
        return len({m.session_id for m in self.members})

    @property
    def distinct_projects(self) -> int:
        return len({m.project for m in self.members})

    def representative(self) -> Message:
        """Return the cluster medoid: member with highest mean similarity to others."""
        if len(self.members) == 1:
            return self.members[0]
        best = self.members[0]
        best_score = -1.0
        for candidate in self.members:
            score = sum(_jaccard(candidate.tokens, other.tokens) for other in self.members if other is not candidate)
            if score > best_score:
                best_score = score
                best = candidate
        return best


# ---- pipeline ------------------------------------------------------------


def walk_jsonl(projects_dir: Path) -> Iterable[tuple[Path, str]]:
    """Yield (file_path, line_text) for every JSONL line under projects_dir."""
    for jsonl_path in sorted(projects_dir.rglob("*.jsonl")):
        try:
            with jsonl_path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        yield jsonl_path, line
        except OSError as e:
            print(f"  skip {jsonl_path}: {e}", file=sys.stderr)


def extract_user_messages(projects_dir: Path) -> Iterable[Message]:
    """Walk JSONL, yield pre-processed Message objects for type:user events."""
    for jsonl_path, line in walk_jsonl(projects_dir):
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
        normalized = _normalize(text)
        if not _is_signal(normalized):
            continue

        tokens = _ngram_set(normalized)
        if len(tokens) < 3:
            continue  # too sparse to cluster meaningfully

        # The project name is the directory under ~/.claude/projects/
        # (e.g., "-Users-charles-work-claude-claude-code-config-updater").
        try:
            project = jsonl_path.relative_to(projects_dir).parts[0]
        except ValueError:
            project = jsonl_path.parent.name

        yield Message(
            project=project,
            session_id=event.get("sessionId", ""),
            text=normalized[:300],
            tokens=tokens,
            timestamp=event.get("timestamp", ""),
        )


def _extract_content_text(content: object) -> str | None:
    """The message.content field may be a string OR a list of content blocks."""
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


def _normalize(text: str) -> str:
    text = CODE_BLOCK_RE.sub(" ", text)
    text = INLINE_CODE_RE.sub(" ", text)
    text = URL_RE.sub(" ", text)
    text = PATH_RE.sub(" ", text)
    text = WHITESPACE_RE.sub(" ", text)
    return text.strip()


def _is_signal(text: str) -> bool:
    if len(text) < MIN_CHARS or len(text) > MAX_CHARS:
        return False
    # Wholesale-drop system-injected pseudo-user content
    stripped_left = text.lstrip()
    if stripped_left.startswith(SYSTEM_INJECTED_PREFIXES):
        return False
    if any(needle in text for needle in SYSTEM_INJECTED_SUBSTRINGS):
        return False
    low = text.lower().strip().rstrip(".!?")
    if low in NOISE_PHRASES:
        return False
    # Drop messages that are mostly non-word (e.g., punctuation/numbers)
    word_chars = sum(c.isalpha() for c in text)
    return not word_chars < len(text) * 0.4


def _ngram_set(text: str) -> set[str]:
    words = [w for w in WORD_RE.findall(text.lower()) if len(w) >= 3]
    grams: set[str] = set()
    for n in range(MIN_NGRAM_SIZE, MAX_NGRAM_SIZE + 1):
        for i in range(len(words) - n + 1):
            grams.add(" ".join(words[i : i + n]))
    return grams


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def cluster_messages(messages: list[Message], threshold: float = JACCARD_THRESHOLD) -> list[Cluster]:
    """Greedy single-pass clustering: each new message joins the highest-similarity
    existing cluster if score >= threshold; otherwise starts a new cluster.

    O(N * K) where K is number of clusters. For 14k messages with maybe a few
    thousand clusters, that's manageable on a laptop in seconds.
    """
    clusters: list[Cluster] = []
    for msg in messages:
        best_cluster: Cluster | None = None
        best_score = 0.0
        for cluster in clusters:
            score = _jaccard(msg.tokens, cluster.n_grams_combined)
            if score > best_score and score >= threshold:
                best_score = score
                best_cluster = cluster
        if best_cluster is None:
            new = Cluster(members=[msg], n_grams_combined=set(msg.tokens))
            clusters.append(new)
        else:
            best_cluster.members.append(msg)
            best_cluster.n_grams_combined |= msg.tokens
    return clusters


# ---- output --------------------------------------------------------------


def report(clusters: list[Cluster], top: int = 30) -> None:
    """Print the top N clusters, sorted to surface cross-session preferences.

    A pattern that appears 5 times across 5 sessions in 3 projects is real
    signal; a pattern that appears 5 times in one debug session is noise.
    Sort key prioritizes cross-session breadth, then cross-project breadth,
    then raw count.
    """
    # Require at least 2 distinct sessions OR 2 distinct projects — both
    # filters by themselves let through some single-session noise; together
    # they're a reasonable proxy for "this is a recurring preference."
    interesting = [
        c for c in clusters if c.size >= MIN_OCCURRENCES and (c.distinct_sessions >= 2 or c.distinct_projects >= 2)
    ]
    interesting.sort(key=lambda c: (-c.distinct_projects, -c.distinct_sessions, -c.size))

    print(
        f"\nFound {len(clusters)} total clusters; "
        f"{sum(1 for c in clusters if c.size >= MIN_OCCURRENCES)} pass occurrence filter; "
        f"{len(interesting)} pass cross-session/project filter.\n"
    )
    print(f"--- top {min(top, len(interesting))} clusters by cross-session/project breadth ---\n")

    for rank, cluster in enumerate(interesting[:top], start=1):
        rep = cluster.representative()
        print(
            f"#{rank}  occurrences={cluster.size}  "
            f"sessions={cluster.distinct_sessions}  "
            f"projects={cluster.distinct_projects}"
        )
        print(f"   medoid: {_truncate(rep.text, 200)}")
        # A few non-medoid example excerpts
        examples = [m for m in cluster.members if m is not rep][:SAMPLE_EXCERPT_COUNT]
        for ex in examples:
            print(f"   also  : {_truncate(ex.text, 160)}")
        print()


def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


# ---- entrypoint ----------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--projects-dir",
        type=Path,
        default=Path.home() / ".claude" / "projects",
        help="Directory containing project subdirs of *.jsonl transcripts.",
    )
    parser.add_argument("--top", type=int, default=30, help="How many clusters to report.")
    parser.add_argument(
        "--max-messages",
        type=int,
        default=20000,
        help="Cap on messages to process (sanity bound).",
    )
    args = parser.parse_args()

    if not args.projects_dir.is_dir():
        print(f"projects-dir not found: {args.projects_dir}", file=sys.stderr)
        return 2

    print(f"scanning {args.projects_dir} ...")
    messages: list[Message] = []
    per_project: Counter[str] = Counter()
    for msg in extract_user_messages(args.projects_dir):
        messages.append(msg)
        per_project[msg.project] += 1
        if len(messages) >= args.max_messages:
            print(f"  hit --max-messages cap of {args.max_messages}; stopping early")
            break

    print(f"\nextracted {len(messages):,} signal-bearing user messages from {len(per_project)} project(s).")
    if per_project:
        print("per-project counts:")
        for proj, n in per_project.most_common():
            print(f"  {n:>5}  {proj}")

    print("\nclustering...")
    clusters = cluster_messages(messages)
    print(f"  {len(clusters)} clusters formed (threshold jaccard>={JACCARD_THRESHOLD}).")

    report(clusters, top=args.top)
    return 0


if __name__ == "__main__":
    sys.exit(main())
