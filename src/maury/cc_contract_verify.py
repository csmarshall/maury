"""Verifier for the Claude Code documentation contract.

Re-fetches every URL recorded in `docs/claude-code-snapshots/<date>/MANIFEST.txt`
and compares against the snapshot file's SHA256. Reports drift per URL.

Pattern mirrors the other empirical verifiers (`verify-cc-projects-dir`,
`verify-cc-hook-timing`, `verify-cc-transcript-schema`) — pure analyzer
plus harness. Tests inject a fake HTTP getter; production uses urllib.

Why this exists: maury depends on documented Claude Code behavior (hook
semantics, transcript schema, settings.json shape). When Anthropic updates
the docs, the cited URLs may still resolve but the content under them can
change. This verifier is the regression signal — re-run after every
Anthropic-docs release that might touch behavior maury cites.
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Final


class EntryStatus(Enum):
    """Per-URL outcome of a contract verification run."""

    UNCHANGED = "unchanged"
    """Remote content matches the snapshot's SHA256."""

    DRIFT = "drift"
    """Remote content differs from the snapshot."""

    FETCH_ERROR = "fetch_error"
    """Network/HTTP error fetching the URL — couldn't compare."""

    SNAPSHOT_MISSING = "snapshot_missing"
    """The manifest names a file that isn't in the snapshot dir."""


@dataclass(frozen=True)
class ManifestEntry:
    """One line of the snapshot MANIFEST.txt."""

    name: str
    """Short name of the snapshot (also the filename stem)."""

    bytes_expected: int
    """Expected byte length of the snapshot file."""

    sha256_expected: str
    """SHA256 of the snapshot file's bytes."""

    url: str
    """Source URL the snapshot was fetched from."""


@dataclass(frozen=True)
class Finding:
    """Result of comparing one manifest entry against its remote URL."""

    entry: ManifestEntry
    status: EntryStatus
    actual_sha256: str | None = None
    """SHA256 of the freshly-fetched bytes (None on fetch error)."""

    actual_bytes: int | None = None
    """Length of the freshly-fetched bytes (None on fetch error)."""

    error: str | None = None
    """Error message when status is FETCH_ERROR or SNAPSHOT_MISSING."""


@dataclass(frozen=True)
class Report:
    """Aggregate result of a full verification run."""

    snapshot_dir: Path
    findings: tuple[Finding, ...] = field(default_factory=tuple)

    def summary_counts(self) -> dict[str, int]:
        """Counts per status, for status-line summaries."""
        counts: dict[str, int] = {s.value: 0 for s in EntryStatus}
        for f in self.findings:
            counts[f.status.value] += 1
        return counts

    def has_drift(self) -> bool:
        """True iff any finding has status DRIFT or FETCH_ERROR/SNAPSHOT_MISSING."""
        return any(f.status is not EntryStatus.UNCHANGED for f in self.findings)


# Default timeout (seconds) for a single URL fetch.
DEFAULT_TIMEOUT: Final[float] = 30.0

# Default User-Agent so the request is identifiable on the receiving end.
USER_AGENT: Final[str] = "maury-verify-cc-contract/1.0"


# Pure parsers --------------------------------------------------------------


def parse_manifest(manifest_path: Path) -> list[ManifestEntry]:
    """Parse a snapshot MANIFEST.txt into typed entries.

    Format (whitespace-delimited columns; lines starting with `#` and blank
    lines are skipped):

        <name>  <bytes>  <sha256>  <url>

    Raises `ValueError` on a malformed row so callers see schema breakage
    rather than silent drops.
    """
    if not manifest_path.is_file():
        raise FileNotFoundError(f"MANIFEST.txt not found at {manifest_path}")
    entries: list[ManifestEntry] = []
    for lineno, raw in enumerate(manifest_path.read_text().splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 4:
            raise ValueError(
                f"{manifest_path}:{lineno}: expected 4 whitespace-separated columns, got {len(parts)}: {raw!r}"
            )
        name, bytes_str, sha256, url = parts
        try:
            bytes_expected = int(bytes_str)
        except ValueError as exc:
            raise ValueError(f"{manifest_path}:{lineno}: bytes column not an integer: {bytes_str!r}") from exc
        entries.append(
            ManifestEntry(
                name=name,
                bytes_expected=bytes_expected,
                sha256_expected=sha256,
                url=url,
            )
        )
    return entries


def sha256_hex(data: bytes) -> str:
    """Lowercase hex SHA256 of `data`."""
    return hashlib.sha256(data).hexdigest()


# Network adapter -----------------------------------------------------------

# Signature: url -> (status_code, body_bytes). Raises on transport error.
HttpGetter = Callable[[str, float], tuple[int, bytes]]


def urllib_get(url: str, timeout: float) -> tuple[int, bytes]:
    """Default HTTP GET via stdlib urllib.

    Returns (status_code, body_bytes). Raises `urllib.error.URLError` on
    network failure. Non-2xx responses still return their body so the
    caller can decide how to record them.
    """
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
        return int(resp.status), body


# Pure analyzer -------------------------------------------------------------


def find_snapshot_file(snapshot_dir: Path, name: str) -> Path | None:
    """Look up `<name>.<ext>` inside snapshot_dir; .html preferred, else first match.

    The MANIFEST.txt records names without extension (`hooks`) but the
    snapshot files have extensions (`hooks.html`). Resolve by glob.
    """
    candidates = sorted(snapshot_dir.glob(f"{name}.*"))
    # Prefer .html over other extensions for stability.
    for cand in candidates:
        if cand.suffix == ".html":
            return cand
    return candidates[0] if candidates else None


def compare_entry(
    entry: ManifestEntry,
    snapshot_dir: Path,
    http_get: HttpGetter,
    timeout: float,
) -> Finding:
    """Compare one manifest entry against its remote URL.

    Returns a `Finding` whose `status` is one of:
      - UNCHANGED — remote SHA matches manifest SHA
      - DRIFT — remote differs from manifest
      - FETCH_ERROR — couldn't fetch the URL
      - SNAPSHOT_MISSING — the named file isn't in the snapshot dir
    """
    snap_path = find_snapshot_file(snapshot_dir, entry.name)
    if snap_path is None:
        return Finding(
            entry=entry,
            status=EntryStatus.SNAPSHOT_MISSING,
            error=f"no snapshot file matching {entry.name}.* in {snapshot_dir}",
        )

    try:
        status_code, body = http_get(entry.url, timeout)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return Finding(entry=entry, status=EntryStatus.FETCH_ERROR, error=str(exc))

    if status_code < 200 or status_code >= 300:
        return Finding(
            entry=entry,
            status=EntryStatus.FETCH_ERROR,
            error=f"HTTP {status_code} from {entry.url}",
        )

    actual_sha = sha256_hex(body)
    actual_bytes = len(body)

    if actual_sha == entry.sha256_expected:
        return Finding(
            entry=entry,
            status=EntryStatus.UNCHANGED,
            actual_sha256=actual_sha,
            actual_bytes=actual_bytes,
        )

    return Finding(
        entry=entry,
        status=EntryStatus.DRIFT,
        actual_sha256=actual_sha,
        actual_bytes=actual_bytes,
    )


# Harness -------------------------------------------------------------------


def run_contract_verification(
    snapshot_dir: Path,
    *,
    http_get: HttpGetter | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> Report:
    """Verify every URL in the snapshot's MANIFEST.txt against its remote.

    Pure with respect to its inputs: `http_get` is injectable so tests can
    avoid the network. Production code passes None to get the urllib
    default.
    """
    manifest_path = snapshot_dir / "MANIFEST.txt"
    entries = parse_manifest(manifest_path)
    getter: HttpGetter = http_get if http_get is not None else urllib_get
    findings = tuple(compare_entry(e, snapshot_dir, getter, timeout) for e in entries)
    return Report(snapshot_dir=snapshot_dir, findings=findings)


# Snapshot-dir discovery ----------------------------------------------------


def default_snapshot_dir(repo_root: Path) -> Path | None:
    """Return the most-recent snapshot dir under `docs/claude-code-snapshots/`.

    Snapshot dirs are named by date (YYYY-MM-DD); we return the alphabetically
    last one. Returns None if no snapshot dirs exist.
    """
    snapshots_root = repo_root / "docs" / "claude-code-snapshots"
    if not snapshots_root.is_dir():
        return None
    candidates = sorted(p for p in snapshots_root.iterdir() if p.is_dir() and (p / "MANIFEST.txt").is_file())
    return candidates[-1] if candidates else None


# Formatters ----------------------------------------------------------------


def format_report_text(report: Report) -> str:
    """Human-readable text format for the report."""
    lines: list[str] = []
    lines.append(f"snapshot: {report.snapshot_dir}")
    lines.append("")
    for f in report.findings:
        if f.status is EntryStatus.UNCHANGED:
            mark = "✅"
        elif f.status is EntryStatus.DRIFT:
            mark = "⚠️"
        else:
            mark = "❌"
        lines.append(f"  {mark} {f.entry.name:<25} {f.status.value:<18} {f.entry.url}")
        if f.status is EntryStatus.DRIFT and f.actual_sha256:
            lines.append(
                f"     expected sha256: {f.entry.sha256_expected[:16]}…  "
                f"actual: {f.actual_sha256[:16]}…  "
                f"({f.entry.bytes_expected} → {f.actual_bytes} bytes)"
            )
        elif f.error:
            lines.append(f"     {f.error}")
    counts = report.summary_counts()
    lines.append("")
    lines.append(
        f"summary: unchanged={counts['unchanged']} "
        f"drift={counts['drift']} "
        f"fetch_error={counts['fetch_error']} "
        f"snapshot_missing={counts['snapshot_missing']}"
    )
    return "\n".join(lines)


def format_report_json(report: Report) -> str:
    """Machine-readable JSON format for the report."""
    payload = {
        "snapshot_dir": str(report.snapshot_dir),
        "summary": report.summary_counts(),
        "findings": [
            {
                "name": f.entry.name,
                "url": f.entry.url,
                "status": f.status.value,
                "expected_sha256": f.entry.sha256_expected,
                "expected_bytes": f.entry.bytes_expected,
                "actual_sha256": f.actual_sha256,
                "actual_bytes": f.actual_bytes,
                "error": f.error,
            }
            for f in report.findings
        ],
    }
    return json.dumps(payload, indent=2)
