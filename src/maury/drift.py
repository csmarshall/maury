"""Drift detection for `~/.claude/` vs the last successful render.

Per [ADR-0017](../../docs/adr/0017-drift-detection-and-reconciliation.md).

Three concerns live here:

1. **Render manifest persistence.** After every successful
   `apply_render`, the sync workflow writes `last-render.json` to
   `<target>/maury-state/last-render.json` capturing the path + sha256
   of every file maury wrote.
2. **Drift detection.** Compare the recorded SHAs against the on-disk
   SHAs and produce a structured `DriftReport`.
3. **Source attribution (partial).** v0 of this module classifies
   drift as `MODIFIED` (file present + sha differs), `MISSING` (file
   gone), `UNTRACKED` (file present in managed dir but not in last-
   render), or `EXPECTED` (file present + sha matches).

   The Claude-write log (used to attribute MODIFIED files to a
   tool-use vs a hand-edit) is a follow-up slice — without it, all
   modifications are conservatively classified as `human_unattributed`.

The reconcile menu (5 hand-edit actions, 3 Claude-write actions) is a
separate module (`maury/reconcile.py`) wired up in a later slice.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path

from maury.paths import state_dir as _state_dir

# Per ADR-0029 the last-render manifest lives at `paths.state_dir()`
# (`$XDG_STATE_HOME/maury`, default `~/.local/state/maury/`), decoupled
# from `~/.claude/`. STATE_SUBDIR is retained for the render-target scan's
# skip logic below (defensive; nothing maury-owned lives there anymore).
STATE_SUBDIR = "maury-state"
LAST_RENDER_FILENAME = "last-render.json"


# ---- file fingerprint ---------------------------------------------------


@dataclass(frozen=True)
class FileFingerprint:
    """One file's identity in a render manifest: relative path + content hash."""

    path: str  # POSIX-style relative path under the target dir
    sha256: str  # lowercase hex
    size: int  # bytes; convenience for human display + sanity check

    @classmethod
    def from_bytes(cls, *, path: str, content: bytes) -> FileFingerprint:
        return cls(
            path=path,
            sha256=hashlib.sha256(content).hexdigest(),
            size=len(content),
        )

    @classmethod
    def from_disk(cls, *, target_dir: Path, rel_path: str) -> FileFingerprint | None:
        """Read the file off disk; return None if it's missing."""
        full = target_dir / rel_path
        if not full.is_file():
            return None
        content = full.read_bytes()
        return cls.from_bytes(path=rel_path, content=content)


# ---- last-render manifest -----------------------------------------------


@dataclass
class LastRender:
    """The persisted record of what maury most recently wrote.

    Schema version: bump this when the format changes incompatibly so
    older states can be migrated forward (or rejected with a clear error
    rather than misinterpreted).
    """

    schema_version: int = 1
    rendered_at: str = ""  # ISO-8601 timestamp
    host_id: str = ""
    profile_id: str = ""
    files: list[FileFingerprint] = field(default_factory=list)

    def by_path(self) -> dict[str, FileFingerprint]:
        return {f.path: f for f in self.files}

    def to_json(self) -> str:
        payload = {
            "schema_version": self.schema_version,
            "rendered_at": self.rendered_at,
            "host_id": self.host_id,
            "profile_id": self.profile_id,
            "files": [asdict(f) for f in self.files],
        }
        return json.dumps(payload, indent=2, sort_keys=False) + "\n"

    @classmethod
    def from_json(cls, text: str) -> LastRender:
        data = json.loads(text)
        ver = data.get("schema_version", 1)
        if ver != 1:
            raise DriftStateError(
                f"unsupported last-render.json schema_version={ver}; this maury speaks v1. Re-render or upgrade."
            )
        return cls(
            schema_version=ver,
            rendered_at=data.get("rendered_at", ""),
            host_id=data.get("host_id", ""),
            profile_id=data.get("profile_id", ""),
            files=[FileFingerprint(**f) for f in data.get("files", [])],
        )


def state_path() -> Path:
    """Return the path to last-render.json.

    Per ADR-0029 last-render.json lives at `paths.state_dir()/last-render.json`.
    """
    return _state_dir() / LAST_RENDER_FILENAME


def write_last_render(last: LastRender) -> Path:
    """Persist the render manifest. Creates the state dir if needed."""
    sp = state_path()
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(last.to_json(), encoding="utf-8")
    return sp


def read_last_render() -> LastRender | None:
    """Load last-render.json if present; None if maury has never rendered."""
    sp = state_path()
    if not sp.is_file():
        return None
    return LastRender.from_json(sp.read_text(encoding="utf-8"))


# ---- drift report -------------------------------------------------------


class DriftKind(StrEnum):
    """Per-file drift classification."""

    EXPECTED = "expected"  # on disk, sha matches
    MODIFIED = "modified"  # on disk, sha differs from last render
    MISSING = "missing"  # in last render, not on disk
    UNTRACKED = "untracked"  # on disk under a managed prefix, not in last render


@dataclass(frozen=True)
class DriftEntry:
    """One file's drift status."""

    path: str
    kind: DriftKind
    expected_sha: str | None
    actual_sha: str | None
    expected_size: int | None
    actual_size: int | None
    note: str = ""  # free-form — e.g., reason for UNTRACKED rejection


@dataclass
class DriftReport:
    """Aggregate drift for one target dir vs last-render.

    `unattributed_modifications` is the count of MODIFIED files that
    couldn't be matched to a Claude-write log entry. v0 = all of them
    (no log integration yet); a later slice splits this into
    `claude_writes` vs `human_edits`.
    """

    entries: list[DriftEntry] = field(default_factory=list)
    has_last_render: bool = True
    last_render_at: str = ""

    @property
    def modified(self) -> list[DriftEntry]:
        return [e for e in self.entries if e.kind == DriftKind.MODIFIED]

    @property
    def missing(self) -> list[DriftEntry]:
        return [e for e in self.entries if e.kind == DriftKind.MISSING]

    @property
    def untracked(self) -> list[DriftEntry]:
        return [e for e in self.entries if e.kind == DriftKind.UNTRACKED]

    def has_drift(self) -> bool:
        """True if any file is MODIFIED, MISSING, or UNTRACKED."""
        return any(e.kind != DriftKind.EXPECTED for e in self.entries)

    def summary_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {k.value: 0 for k in DriftKind}
        for e in self.entries:
            counts[e.kind.value] += 1
        return counts


# ---- drift detection ----------------------------------------------------


def detect_drift(
    *,
    target_dir: Path,
    last: LastRender | None,
    untracked_scan_dirs: list[str] | None = None,
) -> DriftReport:
    """Compare on-disk state of `target_dir` against `last`.

    Args:
        target_dir: e.g., `~/.claude/`.
        last: the previously rendered manifest, or None if maury has
            never rendered here.
        untracked_scan_dirs: optional list of subdirs (relative to
            target_dir) to scan for files that aren't in `last`. If
            None, no untracked-file detection happens. Caller passes
            in the dirs maury actively manages — typically `["agents",
            "skills", "bin"]` plus the top-level maury-rendered files.

    Returns:
        DriftReport with one DriftEntry per file (managed + untracked).
    """
    if last is None:
        # No prior render. Everything currently in the managed dirs is
        # UNTRACKED — but we cannot say whether that's drift or simply
        # the user's pre-existing setup. Caller decides what to do.
        report = DriftReport(has_last_render=False)
        if untracked_scan_dirs:
            for fp in _scan_untracked(target_dir=target_dir, scan_dirs=untracked_scan_dirs, exclude=set()):
                report.entries.append(
                    DriftEntry(
                        path=fp.path,
                        kind=DriftKind.UNTRACKED,
                        expected_sha=None,
                        actual_sha=fp.sha256,
                        expected_size=None,
                        actual_size=fp.size,
                        note="no prior render to compare against",
                    )
                )
        return report

    report = DriftReport(has_last_render=True, last_render_at=last.rendered_at)
    expected_paths: set[str] = set()
    for f in last.files:
        expected_paths.add(f.path)
        actual = FileFingerprint.from_disk(target_dir=target_dir, rel_path=f.path)
        if actual is None:
            report.entries.append(
                DriftEntry(
                    path=f.path,
                    kind=DriftKind.MISSING,
                    expected_sha=f.sha256,
                    actual_sha=None,
                    expected_size=f.size,
                    actual_size=None,
                )
            )
            continue
        if actual.sha256 == f.sha256:
            report.entries.append(
                DriftEntry(
                    path=f.path,
                    kind=DriftKind.EXPECTED,
                    expected_sha=f.sha256,
                    actual_sha=actual.sha256,
                    expected_size=f.size,
                    actual_size=actual.size,
                )
            )
        else:
            report.entries.append(
                DriftEntry(
                    path=f.path,
                    kind=DriftKind.MODIFIED,
                    expected_sha=f.sha256,
                    actual_sha=actual.sha256,
                    expected_size=f.size,
                    actual_size=actual.size,
                )
            )

    if untracked_scan_dirs:
        for fp in _scan_untracked(
            target_dir=target_dir,
            scan_dirs=untracked_scan_dirs,
            exclude=expected_paths,
        ):
            report.entries.append(
                DriftEntry(
                    path=fp.path,
                    kind=DriftKind.UNTRACKED,
                    expected_sha=None,
                    actual_sha=fp.sha256,
                    expected_size=None,
                    actual_size=fp.size,
                )
            )

    return report


def _scan_untracked(
    *,
    target_dir: Path,
    scan_dirs: list[str],
    exclude: set[str],
) -> list[FileFingerprint]:
    """Walk the listed subdirs of target_dir; return files not in `exclude`.

    Skips the maury-state dir (we do not want to surface our own state
    file as untracked) and any dotfiles or hidden directories.
    """
    found: list[FileFingerprint] = []
    for sub in scan_dirs:
        root = target_dir / sub
        if not root.is_dir():
            continue
        for p in sorted(root.rglob("*")):
            if not p.is_file():
                continue
            # Skip hidden files / dirs (matches both .DS_Store and .git/...)
            try:
                rel_parts = p.relative_to(target_dir).parts
            except ValueError:
                continue
            if any(part.startswith(".") for part in rel_parts):
                continue
            if rel_parts[0] == STATE_SUBDIR:
                continue
            rel = "/".join(rel_parts)
            if rel in exclude:
                continue
            fp = FileFingerprint.from_disk(target_dir=target_dir, rel_path=rel)
            if fp is not None:
                found.append(fp)
    return found


# ---- error type ---------------------------------------------------------


class DriftStateError(ValueError):
    """Raised when last-render.json is malformed or unsupported."""


__all__ = [
    "LAST_RENDER_FILENAME",
    "STATE_SUBDIR",
    "DriftEntry",
    "DriftKind",
    "DriftReport",
    "DriftStateError",
    "FileFingerprint",
    "LastRender",
    "detect_drift",
    "read_last_render",
    "state_path",
    "write_last_render",
]
