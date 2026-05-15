"""Host-identity baseline tracking (ADR-0042).

Records a snapshot of `~/.maury-host-id`'s hex prefix at bootstrap so
subsequent mode-scoped commands can detect hex edits — accidental or
otherwise — that would silently swap the host into a different
mode-registration.

Pattern follows SSH's `known_hosts`: trust on first use (write baseline
at init), refuse silently on change (sync aborts with `--confirm-
identity-change` as the explicit acknowledgement gate). Bound to
ADR-0041's three-piece work/home trust contract as the fourth
physical checkpoint.

Schema: `~/.claude/maury-state/host-identity.json`
  {
    "schema_version": 1,
    "host_id_hex": "<8-or-32 hex>",
    "registered_at": "<RFC 3339 UTC>",
    "mode_id": "<mode_<hex>>",
    "mode_name_at_bootstrap": "<display label snapshot>"
  }

Per ADR-0029 invariant #4, writes use tmp+rename so a crashed write
never leaves a partial baseline on disk.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path

from maury.ids import host_id_hex_prefix

STATE_SUBDIR = "maury-state"
HOST_IDENTITY_FILENAME = "host-identity.json"
SCHEMA_VERSION = 1


class HostIdentityError(ValueError):
    """Raised when host-identity.json cannot be parsed or is at an
    unsupported schema version."""


@dataclass(frozen=True)
class HostIdentityBaseline:
    """Snapshot of which host_id this target dir was anchored to.

    The `host_id_hex` field is the load-bearing comparison value;
    everything else is audit metadata for human inspection.
    """

    schema_version: int
    host_id_hex: str
    registered_at: str
    mode_id: str
    mode_name_at_bootstrap: str

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=False) + "\n"

    @classmethod
    def from_json(cls, text: str) -> HostIdentityBaseline:
        data = json.loads(text)
        ver = data.get("schema_version", SCHEMA_VERSION)
        if ver != SCHEMA_VERSION:
            raise HostIdentityError(
                f"unsupported host-identity.json schema_version={ver}; this maury speaks v{SCHEMA_VERSION}"
            )
        return cls(
            schema_version=ver,
            host_id_hex=data["host_id_hex"],
            registered_at=data.get("registered_at", ""),
            mode_id=data.get("mode_id", ""),
            mode_name_at_bootstrap=data.get("mode_name_at_bootstrap", ""),
        )


def baseline_path(target_dir: Path) -> Path:
    """Return the canonical baseline-file path for a target dir."""
    return target_dir / STATE_SUBDIR / HOST_IDENTITY_FILENAME


def write_baseline(target_dir: Path, baseline: HostIdentityBaseline) -> Path:
    """Persist the baseline atomically via tmp+rename.

    Creates the maury-state subdirectory if missing. The temporary file
    lives in the same directory as the final path so the rename is
    atomic on POSIX filesystems (per ADR-0029 invariant #4).
    """
    bp = baseline_path(target_dir)
    bp.parent.mkdir(parents=True, exist_ok=True)
    tmp = bp.with_suffix(bp.suffix + ".tmp")
    tmp.write_text(baseline.to_json(), encoding="utf-8")
    os.replace(tmp, bp)
    return bp


def read_baseline(target_dir: Path) -> HostIdentityBaseline | None:
    """Load the baseline if present; None if this host has never been
    anchored via `maury init` (or the file was deleted)."""
    bp = baseline_path(target_dir)
    if not bp.is_file():
        return None
    return HostIdentityBaseline.from_json(bp.read_text(encoding="utf-8"))


class IdentityCheckOutcome(StrEnum):
    """Result classes returned by `check_host_identity()`.

    Callers branch on this to decide whether to proceed, abort, or
    apply the backwards-compat auto-baseline path.
    """

    OK = "ok"  # Baseline exists, current hex matches. Proceed normally.
    FIRST_RUN_AUTO_BASELINE = "first_run_auto_baseline"  # Pre-2026-05-14 upgrade path.
    CHANGED_REFUSED = "changed_refused"  # Mismatch detected; caller must abort.
    CHANGED_ACKNOWLEDGED = "changed_acknowledged"  # Caller passed allow_change=True; baseline rewritten.


@dataclass(frozen=True)
class IdentityCheckResult:
    """Outcome of a host-identity guard check.

    `current_hex` is always populated from `~/.maury-host-id` so the
    caller can include it in error messages. `baseline_hex` is None
    when there's no prior baseline on disk (first run).
    """

    outcome: IdentityCheckOutcome
    current_hex: str
    baseline_hex: str | None
    baseline: HostIdentityBaseline | None  # None on FIRST_RUN_AUTO_BASELINE before write


def check_host_identity(
    *,
    target_dir: Path,
    host_id_file: Path,
    allow_change: bool = False,
) -> IdentityCheckResult:
    """Cross-check the current host id against the recorded baseline.

    Args:
        target_dir: The target directory whose maury-state holds the
            baseline (typically `~/.claude/`).
        host_id_file: Path to `~/.maury-host-id`.
        allow_change: When True, a detected hex mismatch is treated as
            user-acknowledged (the caller is implementing
            `--confirm-identity-change`); the baseline gets rewritten
            with the new hex on disk and the outcome reports
            CHANGED_ACKNOWLEDGED. When False (default), a mismatch
            returns CHANGED_REFUSED without modifying any file.

    Behavior matrix:
        baseline exists, hex match           -> OK
        baseline absent, host_id_file exists -> FIRST_RUN_AUTO_BASELINE
                                                (caller writes a synthetic
                                                baseline from current state)
        baseline exists, hex differs:
            allow_change=False               -> CHANGED_REFUSED
            allow_change=True                -> CHANGED_ACKNOWLEDGED + rewrite

    Raises:
        HostIdentityError: if `host_id_file` is missing (no identity to
            check; caller should run `maury init` first).
    """
    if not host_id_file.is_file():
        raise HostIdentityError(f"host id file not found at {host_id_file}. Run `maury init` first.")
    current_id = host_id_file.read_text(encoding="utf-8").strip()
    current_hex = host_id_hex_prefix(current_id)

    baseline = read_baseline(target_dir)
    if baseline is None:
        # Pre-2026-05-14 upgrade path: no baseline on disk but the
        # host has an existing host_id_file. The caller writes a
        # synthetic baseline so subsequent runs are guarded normally.
        return IdentityCheckResult(
            outcome=IdentityCheckOutcome.FIRST_RUN_AUTO_BASELINE,
            current_hex=current_hex,
            baseline_hex=None,
            baseline=None,
        )

    if baseline.host_id_hex == current_hex:
        return IdentityCheckResult(
            outcome=IdentityCheckOutcome.OK,
            current_hex=current_hex,
            baseline_hex=baseline.host_id_hex,
            baseline=baseline,
        )

    # Hex mismatch — either accidental edit or deliberate re-anchor.
    if not allow_change:
        return IdentityCheckResult(
            outcome=IdentityCheckOutcome.CHANGED_REFUSED,
            current_hex=current_hex,
            baseline_hex=baseline.host_id_hex,
            baseline=baseline,
        )

    # allow_change=True: caller is implementing --confirm-identity-change.
    # Rewrite the baseline so subsequent runs see the new hex as canonical.
    new_baseline = HostIdentityBaseline(
        schema_version=baseline.schema_version,
        host_id_hex=current_hex,
        registered_at=baseline.registered_at,  # Keep original registration time
        mode_id=baseline.mode_id,
        mode_name_at_bootstrap=baseline.mode_name_at_bootstrap,
    )
    write_baseline(target_dir, new_baseline)
    return IdentityCheckResult(
        outcome=IdentityCheckOutcome.CHANGED_ACKNOWLEDGED,
        current_hex=current_hex,
        baseline_hex=baseline.host_id_hex,
        baseline=new_baseline,
    )


def format_identity_change_message(
    *,
    target_dir: Path,
    host_id_file: Path,
    result: IdentityCheckResult,
) -> str:
    """Render the verbose abort message per ADR-0042 §"Identity-change abort message".

    Verbose by design — this is a failure mode where verbose is correct
    (cf. Tenet 11). Short messages invite "let me just delete the baseline"
    which is the wrong recovery path.
    """
    assert result.outcome == IdentityCheckOutcome.CHANGED_REFUSED, "only valid for refused outcome"
    assert result.baseline is not None, "baseline must be present on a refused mismatch"
    current_id = host_id_file.read_text(encoding="utf-8").strip()
    return (
        "✗ host identity changed since last sync.\n"
        "\n"
        f"  baseline (at {baseline_path(target_dir)}):\n"
        f"    host_id_hex: {result.baseline.host_id_hex}\n"
        f"    mode: {result.baseline.mode_name_at_bootstrap} ({result.baseline.mode_id})\n"
        f"    registered: {result.baseline.registered_at}\n"
        "\n"
        f"  current (at {host_id_file}):\n"
        f"    host_id_hex: {result.current_hex}\n"
        f"    full: {current_id}\n"
        "\n"
        "  This means either:\n"
        f"    (a) Your {host_id_file.name} was edited (deliberately or by\n"
        "        accident).\n"
        f"    (b) You restored {host_id_file.name} from a different host\n"
        "        (e.g., backup restore from another laptop).\n"
        "    (c) You intended to move this hardware to a different\n"
        "        mode-registration.\n"
        "\n"
        "  Maury refuses to sync, render, or mine across an identity\n"
        "  change without explicit acknowledgement. Choose one:\n"
        "\n"
        "    --confirm-identity-change   Proceed with the current host_id.\n"
        "                                The baseline gets overwritten.\n"
        "                                Use this if (a) was deliberate\n"
        "                                or (b) was intentional.\n"
        "\n"
        "    maury init --reset          Re-anchor as a fresh registration.\n"
        "                                Generates a new host_id, writes\n"
        "                                a new baseline, requires manifest\n"
        "                                update. Use this if (c)."
    )


def auto_create_baseline_for_upgrade(
    *,
    target_dir: Path,
    current_hex: str,
    mode_id: str = "",
    mode_name: str = "",
) -> HostIdentityBaseline:
    """Pre-2026-05-14 upgrade convenience: synthesize a baseline from
    the host's current state.

    Per ADR-0042 §"Backwards compatibility": when this maury sees a
    `~/.maury-host-id` file but no `host-identity.json`, it auto-creates
    the baseline from current values so subsequent runs are guarded.
    Mode metadata is best-effort — the caller supplies it from the
    manifest if available, else empty strings (the audit fields stay
    blank but the load-bearing hex comparison still works).

    The grace path is intentionally silent (no prompt) — the worst case
    is the user gets one free identity swap on upgrade, which is a
    tolerable cost. A `maury doctor` rule (followup) could surface the
    auto-creation explicitly.
    """
    from datetime import UTC, datetime

    baseline = HostIdentityBaseline(
        schema_version=SCHEMA_VERSION,
        host_id_hex=current_hex,
        registered_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        mode_id=mode_id,
        mode_name_at_bootstrap=mode_name,
    )
    write_baseline(target_dir, baseline)
    return baseline


__all__ = [
    "HOST_IDENTITY_FILENAME",
    "SCHEMA_VERSION",
    "STATE_SUBDIR",
    "HostIdentityBaseline",
    "HostIdentityError",
    "IdentityCheckOutcome",
    "IdentityCheckResult",
    "auto_create_baseline_for_upgrade",
    "baseline_path",
    "check_host_identity",
    "format_identity_change_message",
    "read_baseline",
    "write_baseline",
]
