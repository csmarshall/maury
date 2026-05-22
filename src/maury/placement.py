"""Rule-driven placement of accepted findings into source files (ADR-0053).

When `maury review` accepts a finding, the rule engine
(`rules/engine.classify_fragment`) has classified it into a
`Classification(profile, host_overlay, ...)`. This module maps that
classification onto the repo-relative source file the render pipeline
reads, and appends the finding's content there (with provenance) on the
review branch.

Target files mirror `render/engine.py`'s layer sources:

- `profile=None` (no rule matched) → **manual queue** (`mining-findings.md`),
  ADR-0004's permanent escape hatch — callers handle this; here it is
  signalled by `placement_relpath` returning `None`.
- base content → repo-root `CLAUDE.md`.
- a mode → `profiles/<mode>/CLAUDE.md.fragment`.
- a mode with `host_overlay` → `profiles/<mode>/hosts/<host>/CLAUDE.md.fragment`.

`classify_fragment` only sets `host_overlay` when a classify rule is
chosen, so `host_overlay` is never present without a `profile`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from maury.promotion.graph import base_mode_id

if TYPE_CHECKING:
    from pathlib import Path

    from maury.manifest import Manifest
    from maury.rules.schema import Classification

# Base content lives at the repo-root `CLAUDE.md` (render reads `CLAUDE.md`
# then `CLAUDE.md.fragment`); mode and host content live in `.fragment`
# files under `profiles/`.
BASE_TARGET = "CLAUDE.md"


def placement_relpath(classification: Classification, *, manifest: Manifest) -> str | None:
    """Repo-relative source file a matched classification places into.

    Returns `None` when `classification.profile is None` (no rule matched
    → the caller routes to the manual queue per ADR-0004).
    """
    profile = classification.profile
    if profile is None:
        return None

    if classification.host_overlay:
        return f"profiles/{profile}/hosts/{classification.host_overlay}/CLAUDE.md.fragment"

    base_name = manifest.profiles[base_mode_id(manifest)].name
    if profile == base_name:
        return BASE_TARGET
    return f"profiles/{profile}/CLAUDE.md.fragment"


def format_placement_block(*, finding_text: str, content_hash: str, source_run: str) -> str:
    """A provenance-marked block to append to a source file.

    The HTML comment carries the originating mining run and the finding's
    `Content-Hash` (Tenet 7), so a placed fragment is traceable and a
    future re-surface of the same idea still dedups against it.
    """
    return f"\n<!-- maury: placed from {source_run}; Content-Hash: {content_hash} -->\n{finding_text.strip()}\n"


def place_finding(
    repo_dir: Path,
    *,
    relpath: str,
    finding_text: str,
    content_hash: str,
    source_run: str,
) -> Path:
    """Append the finding's content (provenance-marked) to `repo_dir/relpath`,
    creating parent directories as needed. Returns the written path.

    Does not commit — the caller (`maury review`) commits the placement on
    the review branch alongside any other changes in the session.
    """
    path = repo_dir / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    block = format_placement_block(finding_text=finding_text, content_hash=content_hash, source_run=source_run)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(block)
    return path


__all__ = [
    "BASE_TARGET",
    "format_placement_block",
    "place_finding",
    "placement_relpath",
]
