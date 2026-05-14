#!/usr/bin/env python3
"""Verify internal-link integrity across maury's markdown docs.

Walks every `.md` file under `docs/` plus `README.md` and any other
top-level `.md` files, parses markdown links, and reports broken
internal references. External (https://) links are skipped by design
— per the project rule in CLAUDE.local.md, internal-link integrity is
the project's responsibility; external-link rot is best-effort.

What's checked:
- `[text](path/to/file.md)` — target file must exist (relative to the
  linking file's directory).
- `[text](file.md#anchor)` — file must exist AND the heading anchor
  must resolve under GitHub's slugifier rules.
- `[text](#anchor)` — anchor must exist in the same file.
- Reference-style links (`[text][label]` + `[label]: target`) — both
  forms handled.

What's skipped:
- `https://` / `http://` URLs (external; not our responsibility).
- `mailto:` and other URI schemes.

Exit code: 0 if every internal link resolves; 1 if any broken.

Usage:
    python scripts/check-doc-links.py           # scan repo (relative to script)
    python scripts/check-doc-links.py --quiet   # only print broken links
    python scripts/check-doc-links.py path/...  # restrict scan to a path

This script is stdlib-only (Python 3.11+); no project deps required.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# Markdown parsing
# ---------------------------------------------------------------------------

# Inline link `[text](target)` — captures target including optional #anchor.
# We deliberately do NOT match images `![...](...)`.
INLINE_LINK_RE = re.compile(
    r"(?<!\!)\[(?P<text>[^\]]+)\]\((?P<target>[^)\s]+)(?:\s+\"[^\"]*\")?\)"
)

# Reference-style link `[text][label]` (label can be empty meaning same as text).
REF_LINK_RE = re.compile(r"(?<!\!)\[(?P<text>[^\]]+)\]\[(?P<label>[^\]]*)\]")

# Link definition `[label]: target` at the top of a line.
LINK_DEF_RE = re.compile(
    r"^\s*\[(?P<label>[^\]]+)\]:\s*(?P<target>\S+)(?:\s+\"[^\"]*\")?\s*$"
)

# Heading `# text`, `## text`, etc. Captures the text after the #s.
HEADING_RE = re.compile(r"^(?P<hashes>#{1,6})\s+(?P<text>.+?)\s*$")

# Code-fence boundaries (``` or ~~~ with optional info-string).
CODE_FENCE_RE = re.compile(r"^\s*(```|~~~)")


def github_slug(heading_text: str) -> str:
    """Approximate GitHub's heading-anchor slugifier.

    Rules (matched against known concepts.md anchors):
    1. Lowercase.
    2. Strip code spans (`` `foo` `` → `foo` — backticks dropped, text kept).
    3. Strip characters not in `[a-z0-9_\\- ]` (punctuation, emoji, etc.).
    4. Replace spaces with hyphens (NOT collapsed — runs become runs).
    5. Strip leading/trailing hyphens.

    Verified against:
    - "Non-interference (Goguen & Meseguer, 1982)"
      → "non-interference-goguen--meseguer-1982"
    - "The `maury-status` skill" → "the-maury-status-skill"
    - "9. `repo_mode` (access subtype)" → "9-repo_mode-access-subtype"
    """
    text = heading_text.lower()
    text = re.sub(r"`([^`]+)`", r"\1", text)  # strip code spans, keep text
    text = re.sub(r"[^a-z0-9_\- ]", "", text)  # drop everything else
    text = text.replace(" ", "-")
    text = text.strip("-")
    return text


def extract_anchors(md_text: str) -> set[str]:
    """Return the set of heading-anchor slugs defined in `md_text`."""
    anchors: set[str] = set()
    in_code = False
    for line in md_text.splitlines():
        if CODE_FENCE_RE.match(line):
            in_code = not in_code
            continue
        if in_code:
            continue
        m = HEADING_RE.match(line)
        if m:
            anchors.add(github_slug(m.group("text")))
    return anchors


@dataclass(frozen=True)
class Link:
    """One markdown link from one source file."""

    source: Path
    line: int
    text: str
    target: str  # raw target string from `[text](target)`


@dataclass(frozen=True)
class LinkDef:
    """One reference-style link definition (`[label]: target`)."""

    source: Path
    line: int
    label: str
    target: str


def _strip_code_spans(line: str) -> str:
    """Replace `` `...` `` code-span content with same-length placeholders so
    link-regexes don't match links written as inline code examples (e.g.,
    a CLAUDE.local.md prose example like `` `[text](path/to/file.md)` ``).

    Length preservation keeps regex match offsets sensible if needed elsewhere.
    """
    def _blank(m: re.Match[str]) -> str:
        # Replace the entire match (including backticks) with same-length spaces.
        return " " * len(m.group(0))

    return re.sub(r"`[^`\n]+`", _blank, line)


def parse_file(path: Path) -> tuple[list[Link], dict[str, str]]:
    """Parse one markdown file. Returns (inline+expanded-ref links, link defs).

    Reference-style links `[text][label]` are expanded against the file's
    own definitions. Definitions outside the file's scope can't be expanded
    (the link's label is reported as-is, which will fail to resolve later).

    Links inside inline code spans (backtick-wrapped) are NOT extracted —
    they're prose examples, not real links.
    """
    md = path.read_text()
    inline: list[Link] = []
    refs: list[tuple[int, str, str]] = []  # (line, text, label)
    defs: dict[str, LinkDef] = {}

    in_code = False
    for lineno, raw_line in enumerate(md.splitlines(), start=1):
        if CODE_FENCE_RE.match(raw_line):
            in_code = not in_code
            continue
        if in_code:
            continue

        # Mask out inline code spans so [text](target) inside `…` doesn't match.
        line = _strip_code_spans(raw_line)

        # Link definitions take priority over inline matches in case the line
        # has both shapes — `[label]: target` is line-anchored.
        ld = LINK_DEF_RE.match(line)
        if ld:
            defs[ld.group("label").lower()] = LinkDef(
                source=path,
                line=lineno,
                label=ld.group("label").lower(),
                target=ld.group("target"),
            )
            continue

        for m in INLINE_LINK_RE.finditer(line):
            inline.append(
                Link(
                    source=path,
                    line=lineno,
                    text=m.group("text"),
                    target=m.group("target"),
                )
            )

        for m in REF_LINK_RE.finditer(line):
            text = m.group("text")
            label = (m.group("label") or text).lower()
            refs.append((lineno, text, label))

    # Expand reference-style links against this file's definitions.
    expanded: list[Link] = list(inline)
    for lineno, text, label in refs:
        d = defs.get(label)
        if d is None:
            # Unresolvable ref-style link — record with a sentinel target.
            expanded.append(Link(source=path, line=lineno, text=text, target=f"<undefined-ref:{label}>"))
        else:
            expanded.append(Link(source=path, line=lineno, text=text, target=d.target))

    return expanded, {label: d.target for label, d in defs.items()}


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def is_external(target: str) -> bool:
    """True iff the link target points at something outside this repo."""
    return target.startswith(("http://", "https://", "mailto:", "ftp://", "ftps://"))


@dataclass
class Issue:
    """One broken-link finding."""

    source: Path
    line: int
    target: str
    reason: str
    context: str = ""


def check_link(
    link: Link,
    anchors_by_path: dict[Path, set[str]],
    repo_root: Path,
) -> Issue | None:
    """Return an Issue if the link doesn't resolve, else None."""
    target = link.target

    if is_external(target):
        return None  # external — out of scope per project rule.

    if target.startswith("<undefined-ref:"):
        label = target[len("<undefined-ref:") : -1]
        return Issue(
            source=link.source,
            line=link.line,
            target=target,
            reason=f"reference-style link [{link.text}][{label}] — no matching `[{label}]:` definition in this file",
        )

    # Split out the anchor fragment, if any.
    path_part, _, anchor = target.partition("#")

    # Resolve the file part. Relative to the linking file's directory unless
    # the target starts with `/` (absolute repo paths — rare; treat as
    # repo-root-relative).
    if not path_part:
        # Pure anchor `#foo` → same-file anchor.
        target_path = link.source
    elif path_part.startswith("/"):
        target_path = (repo_root / path_part.lstrip("/")).resolve()
    else:
        target_path = (link.source.parent / path_part).resolve()

    # If the resolved path escapes the repo root, treat as external (the link
    # likely points at a sibling repo or a GitHub web URL via relative shorthand).
    try:
        target_path.relative_to(repo_root)
    except ValueError:
        return None

    if not target_path.exists():
        return Issue(
            source=link.source,
            line=link.line,
            target=target,
            reason=f"file not found: {target_path.relative_to(repo_root) if target_path.is_absolute() and repo_root in target_path.parents else target_path}",
        )

    if not anchor:
        return None  # file ref only; resolved.

    # Anchor check — only applies to markdown files we can parse.
    if target_path.suffix != ".md":
        return None  # non-md anchor; can't validate, assume OK.

    if target_path not in anchors_by_path:
        anchors_by_path[target_path] = extract_anchors(target_path.read_text())

    if anchor not in anchors_by_path[target_path]:
        # Try a friendly suggestion: closest matching anchor.
        candidates = sorted(anchors_by_path[target_path])
        suggestion = ""
        if candidates:
            # Pick anchors that share at least 3 characters with the bad one.
            close = [c for c in candidates if any(c[i:i + 3] in anchor for i in range(max(1, len(c) - 2)))]
            if close:
                suggestion = f"  did you mean: {', '.join(close[:3])}"
        return Issue(
            source=link.source,
            line=link.line,
            target=target,
            reason=f"anchor `#{anchor}` not found in {target_path.name}",
            context=suggestion,
        )

    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


# Files whose links are intentional placeholders / templates / non-public
# scratch and should be skipped wholesale.
SKIP_FILES = {
    "docs/adr/0000-template.md",  # ADR template — `NNNN-slug.md` placeholders.
    "CLAUDE.local.md",  # gitignored maintainer notes; not part of public docs.
}


def find_md_files(roots: list[Path], repo_root: Path) -> list[Path]:
    """Find every `.md` file under each root, deduped + sorted, skipping
    template / scratch files (SKIP_FILES) and standard junk dirs."""
    seen: set[Path] = set()
    for root in roots:
        if root.is_file() and root.suffix == ".md":
            seen.add(root.resolve())
            continue
        if not root.is_dir():
            continue
        for p in root.rglob("*.md"):
            if any(part in {".git", "node_modules", ".venv", "build", "dist", "__pycache__"} for part in p.parts):
                continue
            seen.add(p.resolve())
    # Apply SKIP_FILES (path-relative-to-repo-root).
    return sorted(
        p for p in seen
        if not _is_skipped(p, repo_root)
    )


def _is_skipped(p: Path, repo_root: Path) -> bool:
    try:
        rel = str(p.relative_to(repo_root))
    except ValueError:
        return False
    return rel in SKIP_FILES


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="files/dirs to scan. Defaults to repo root (auto-detected from this script's location).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="only print broken links; suppress the per-file file-count summary.",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent

    if args.paths:
        roots = [p.resolve() for p in args.paths]
    else:
        # Default scan set: docs/, README.md, top-level .md files in repo root.
        roots = [
            repo_root / "docs",
            repo_root / "README.md",
        ]
        for p in repo_root.glob("*.md"):
            roots.append(p)

    md_files = find_md_files(roots, repo_root)
    if not md_files:
        print("no markdown files found in scan paths", file=sys.stderr)
        return 1

    # Parse every file: collect (links, defs).
    all_links: list[Link] = []
    anchors_by_path: dict[Path, set[str]] = {}
    for p in md_files:
        try:
            links, _defs = parse_file(p)
        except OSError as exc:
            print(f"cannot read {p}: {exc}", file=sys.stderr)
            return 1
        all_links.extend(links)
        anchors_by_path[p] = extract_anchors(p.read_text())

    # Check every link.
    issues: list[Issue] = []
    skipped_external = 0
    checked_internal = 0
    for link in all_links:
        if is_external(link.target):
            skipped_external += 1
            continue
        checked_internal += 1
        issue = check_link(link, anchors_by_path, repo_root)
        if issue is not None:
            issues.append(issue)

    if not args.quiet:
        print(f"scanned {len(md_files)} markdown file(s); {checked_internal} internal link(s) checked, {skipped_external} external link(s) skipped.")

    if not issues:
        if not args.quiet:
            print("✅ all internal links resolve.")
        return 0

    print(f"\n❌ {len(issues)} broken internal link(s):\n", file=sys.stderr)
    for issue in issues:
        rel = issue.source.relative_to(repo_root) if repo_root in issue.source.parents or issue.source == repo_root else issue.source
        print(f"  {rel}:{issue.line}  {issue.target}", file=sys.stderr)
        print(f"    {issue.reason}{issue.context}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
