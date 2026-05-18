"""System-health checks for `maury doctor` (per the 2026-05-18 expansion).

These checks complement the content rules in `checks.py`. Content
rules evaluate a specific CLAUDE.md file (`--file`); system-health
rules evaluate maury-state files under the target dir (`--target`).
Both produce `Finding` objects with the same severity model so
`--fail-on info|warn|error` aggregates uniformly.

Why this exists: `maury status` already surfaces the same conditions
as ⚠️ markers, but status is interactive-diagnostic. Doctor is the
scripted-CI surface — `maury doctor --fail-on warn` should exit
non-zero when these conditions land, so CI catches them. Per
Charles's "both" decision on the 2026-05-18 design call.

Checks implemented:

- **baseline_missing_with_host_id** (severity: warn) — fires when
  `~/.maury-host-id` exists but `<target>/maury-state/host-identity.json`
  doesn't. The pre-2026-05-14-upgrade case. Maury's sync will
  auto-create the baseline on next run; doctor flags it so CI sees
  the transition state.

- **mining_watermark_stale** (severity: warn) — fires when
  `<target>/maury-state/last-mine.json` records a
  `mining_algorithm_version` that doesn't match what this maury
  speaks. The next `maury mine` will discard all watermarks and
  re-mine from scratch; doctor flags it so the user isn't surprised
  by the large first run.
"""

from __future__ import annotations

from pathlib import Path

from .report import Finding, Severity


def run_system_health_checks(*, target_dir: Path, host_id_file: Path) -> list[Finding]:
    """Run every system-health check; return the aggregated findings."""
    findings: list[Finding] = []
    findings.extend(_check_baseline_missing(target_dir=target_dir, host_id_file=host_id_file))
    findings.extend(_check_watermark_stale(target_dir=target_dir))
    return findings


def _check_baseline_missing(*, target_dir: Path, host_id_file: Path) -> list[Finding]:
    """ADR-0042: a host_id file without a baseline is a pre-amendment
    upgrade case. Sync will fix it; doctor surfaces the transition state."""
    from maury.host_identity import baseline_path

    if not host_id_file.is_file():
        return []  # No host id yet → nothing to baseline against; not a finding
    if baseline_path(target_dir).is_file():
        return []
    return [
        Finding(
            check_id="baseline-missing",
            severity=Severity.WARN,
            message=(
                f"`{host_id_file}` exists but `{baseline_path(target_dir)}` does not. "
                "This is the pre-2026-05-14 upgrade state — maury's identity guard "
                "will auto-create the baseline on the next sync, but until then there's "
                "no protection against accidental `~/.maury-host-id` edits."
            ),
            rubric_quote=(
                "ADR-0042 §'Backwards compatibility for pre-2026-05-14 hosts': "
                "Maury treats this as 'first sync after upgrade' and writes the "
                "baseline from the current ~/.maury-host-id value."
            ),
            fix_suggestion=(
                "Run `maury sync` (or `maury status` then any mode-scoped command) once to auto-establish the baseline."
            ),
        )
    ]


def _check_watermark_stale(*, target_dir: Path) -> list[Finding]:
    """ADR-0043: a mining watermark recorded under an older
    `mining_algorithm_version` will be silently invalidated on the next
    `maury mine` run. Doctor surfaces this so the user knows."""
    from maury.mining_state import MINING_ALGORITHM_VERSION, read_watermark

    try:
        wm = read_watermark(target_dir)
    except Exception:
        return []  # Read errors are surfaced by other tooling, not doctor
    if wm is None:
        return []  # No watermark yet → nothing to be stale
    if wm.mining_algorithm_version == MINING_ALGORITHM_VERSION:
        return []
    return [
        Finding(
            check_id="watermark-stale",
            severity=Severity.WARN,
            message=(
                f"Mining watermark uses algorithm version {wm.mining_algorithm_version}, "
                f"but this maury speaks version {MINING_ALGORITHM_VERSION}. The next "
                f"`maury mine` run will discard every project's watermark and re-mine "
                f"from scratch — expect a larger LLM cost on the first run after this."
            ),
            rubric_quote=(
                "ADR-0043: bumping `mining_algorithm_version` invalidates all "
                "watermarks; the next `maury mine` falls back to a full re-mine."
            ),
            fix_suggestion=(
                "Either run `maury mine --full` to acknowledge the re-mine "
                "deliberately, or wait and let the next regular `maury mine` "
                "rebuild the watermarks."
            ),
        )
    ]


__all__ = [
    "run_system_health_checks",
]
