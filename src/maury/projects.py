"""Claude Code project-directory derivation (ADR-0043).

Maps a working directory path to the directory name Claude Code creates
under `~/.claude/projects/`. Per ADR-0043 + `cc-contract:project-
directory-derivation`, this algorithm is **load-bearing in production**
— maury commands derive the current cwd's project dir to drive default
project selection for `maury mine`, with the verifier
(`maury verify-cc-projects-dir`) acting as the regression signal that
the algorithm still matches Claude Code's actual behavior.

The algorithm has been empirically verified (🧪 since 2026-05-13) and
matches the `fh()` source referenced in
anthropics/claude-code#54865. See `cc-contract:project-directory-
derivation` for the verification record.
"""

from __future__ import annotations

from pathlib import Path


def derive_project_dir(cwd: Path) -> str:
    """Predict the directory name Claude Code creates under `~/.claude/projects/`
    when invoked with the given working directory.

    Algorithm (empirically verified 2026-05-13 against claude 2.1.140 on macOS;
    matches the `fh()` source quoted in anthropics/claude-code#54865):

        1. Resolve symlinks on the cwd (`Path.resolve()` ≈ POSIX `realpath`).
        2. Substitute every non-`[A-Za-z0-9]` character with `-`, matching
           the JS runtime's regex semantics. The reference `fh()` in cli.js
           iterates UTF-16 code units (the CLI is Node); this Python port
           iterates codepoints and special-cases non-BMP codepoints by
           emitting two hyphens (one per UTF-16 surrogate half). Output is
           byte-for-byte identical to a literal UTF-16-code-unit iteration
           for any input we can construct.
        3. No collapse of consecutive replacements.

    Non-injective. Distinct cwds can produce the same name: `/a/b/c` and
    `/a-b-c` both yield `-a-b-c`. Consumers walking `~/.claude/projects/`
    MUST NOT assume one directory uniquely identifies one cwd.

    Non-BMP characters (emoji etc.) are encoded as UTF-16 surrogate pairs;
    neither surrogate is alphanumeric, so each non-BMP codepoint becomes
    **two** hyphens (e.g., `🚀` → `--`).
    """
    resolved = str(cwd.resolve())
    out: list[str] = []
    for ch in resolved:
        cp = ord(ch)
        if cp > 0xFFFF:
            # Non-BMP codepoint → UTF-16 surrogate pair → two hyphens.
            out.append("--")
        elif (0x30 <= cp <= 0x39) or (0x41 <= cp <= 0x5A) or (0x61 <= cp <= 0x7A):
            # ASCII [A-Za-z0-9] — preserved.
            out.append(ch)
        else:
            # Any other BMP character (including non-ASCII letters like 'é',
            # punctuation, whitespace) — single hyphen.
            out.append("-")
    return "".join(out)


def claude_projects_root() -> Path:
    """Return `~/.claude/projects/` — where Claude Code stores transcripts."""
    return Path.home() / ".claude" / "projects"


def project_dir_for(cwd: Path) -> Path:
    """Return the full project-dir path under `~/.claude/projects/`
    that Claude Code would use for the given cwd. Doesn't check
    existence — caller decides whether to treat absence as a signal
    (`mine` falls back to the busiest project, etc.)."""
    return claude_projects_root() / derive_project_dir(cwd)


__all__ = [
    "claude_projects_root",
    "derive_project_dir",
    "project_dir_for",
]
