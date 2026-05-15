"""Mining-watermark state (ADR-0043).

Per-project watermarks tracking what `maury mine` has already
processed, so incremental runs skip already-mined transcript ranges.
Mtime-based: each project entry records the highest JSONL mtime
processed in that project as of the last successful mining run.

Schema: `~/.claude/maury-state/last-mine.json`
  {
    "schema_version": 1,
    "mining_algorithm_version": 1,
    "projects": {
      "-Users-jdoe-work-project": {
        "last_mined_at": "2026-05-15T14:00:00Z",
        "last_jsonl_mtime": "2026-05-15T13:50:00Z",
        "windows_processed": 23,
        "findings_count": 8
      }
    }
  }

Bumping `mining_algorithm_version` invalidates all watermarks; the
next `maury mine` runs as if `--full` had been passed.

Per ADR-0029 invariant #4, writes use tmp+rename so a crashed mining
run never corrupts the watermark file.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

STATE_SUBDIR = "maury-state"
LAST_MINE_FILENAME = "last-mine.json"
SCHEMA_VERSION = 1

# Bump this when the mine extraction or cross-reference logic changes
# in a way that would produce different findings on the same input.
# A bump invalidates every per-project watermark (the next `maury mine`
# falls back to a full re-mine and rewrites the watermarks).
MINING_ALGORITHM_VERSION = 1


class MiningStateError(ValueError):
    """Raised when last-mine.json cannot be parsed or is at an
    unsupported schema version."""


@dataclass(frozen=True)
class ProjectMiningRecord:
    """Per-project watermark + audit metadata.

    `last_jsonl_mtime` is load-bearing — it's compared against current
    JSONL mtimes to decide what to mine. The other fields are audit
    data for human inspection via `maury status` (planned).
    """

    last_mined_at: str  # When this run completed (RFC 3339 UTC).
    last_jsonl_mtime: str  # Highest JSONL mtime processed (RFC 3339 UTC).
    windows_processed: int = 0
    findings_count: int = 0


@dataclass
class MiningWatermark:
    """Aggregate state for incremental mining across all projects.

    Mutable (unlike most maury dataclasses) because incremental updates
    add/replace one project's record at a time.
    """

    schema_version: int = SCHEMA_VERSION
    mining_algorithm_version: int = MINING_ALGORITHM_VERSION
    projects: dict[str, ProjectMiningRecord] = field(default_factory=dict)

    def to_json(self) -> str:
        payload = {
            "schema_version": self.schema_version,
            "mining_algorithm_version": self.mining_algorithm_version,
            "projects": {k: asdict(v) for k, v in self.projects.items()},
        }
        return json.dumps(payload, indent=2, sort_keys=False) + "\n"

    @classmethod
    def from_json(cls, text: str) -> MiningWatermark:
        data = json.loads(text)
        ver = data.get("schema_version", SCHEMA_VERSION)
        if ver != SCHEMA_VERSION:
            raise MiningStateError(
                f"unsupported last-mine.json schema_version={ver}; this maury speaks v{SCHEMA_VERSION}"
            )
        projects = {name: ProjectMiningRecord(**rec) for name, rec in data.get("projects", {}).items()}
        return cls(
            schema_version=ver,
            mining_algorithm_version=data.get("mining_algorithm_version", MINING_ALGORITHM_VERSION),
            projects=projects,
        )

    def is_stale(self) -> bool:
        """True if the recorded `mining_algorithm_version` is older than
        what this maury speaks — caller should treat all watermarks as
        invalid and force a full re-mine."""
        return self.mining_algorithm_version != MINING_ALGORITHM_VERSION

    def record_for(self, project_dir_name: str) -> ProjectMiningRecord | None:
        """Look up the watermark for one project; None if never mined."""
        return self.projects.get(project_dir_name)

    def update(self, project_dir_name: str, record: ProjectMiningRecord) -> None:
        """Replace one project's watermark. Caller writes the file
        atomically via `write_watermark()` afterward."""
        self.projects[project_dir_name] = record


def watermark_path(target_dir: Path) -> Path:
    """Return the canonical watermark-file path for a target dir."""
    return target_dir / STATE_SUBDIR / LAST_MINE_FILENAME


def write_watermark(target_dir: Path, watermark: MiningWatermark) -> Path:
    """Persist the watermark atomically via tmp+rename (ADR-0029 #4)."""
    wp = watermark_path(target_dir)
    wp.parent.mkdir(parents=True, exist_ok=True)
    tmp = wp.with_suffix(wp.suffix + ".tmp")
    tmp.write_text(watermark.to_json(), encoding="utf-8")
    os.replace(tmp, wp)
    return wp


def read_watermark(target_dir: Path) -> MiningWatermark | None:
    """Load the watermark if present; None if maury has never mined."""
    wp = watermark_path(target_dir)
    if not wp.is_file():
        return None
    return MiningWatermark.from_json(wp.read_text(encoding="utf-8"))


def load_or_init_watermark(target_dir: Path) -> MiningWatermark:
    """Return the current watermark or a fresh empty one if absent.

    Caller mutates the returned object and writes back via
    `write_watermark()`. Also handles the algorithm-version bump:
    if the loaded watermark is stale, returns a fresh one so the
    caller's subsequent comparisons fall through to "no record."
    """
    wm = read_watermark(target_dir)
    if wm is None or wm.is_stale():
        return MiningWatermark()
    return wm


__all__ = [
    "LAST_MINE_FILENAME",
    "MINING_ALGORITHM_VERSION",
    "SCHEMA_VERSION",
    "STATE_SUBDIR",
    "MiningStateError",
    "MiningWatermark",
    "ProjectMiningRecord",
    "load_or_init_watermark",
    "read_watermark",
    "watermark_path",
    "write_watermark",
]
