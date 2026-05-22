"""System-health checks for `maury doctor` (per the 2026-05-18 expansion).

These checks complement the content rules in `checks.py`. Content
rules evaluate a specific CLAUDE.md file (`--file`); system-health
rules evaluate maury-state files under the target dir (`--target`).
Both produce `Finding` objects with the same severity model so
`--fail-on info|warn|error` aggregates uniformly.

Why this exists: `maury status` already surfaces the same conditions
as ⚠️ markers, but status is interactive-diagnostic. Doctor is the
scripted-CI surface — `maury doctor --fail-on warn` should exit
non-zero when these conditions land, so CI catches them. Per the
"both" decision on the 2026-05-18 design call.

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

- **focus_unreachable** (severity: warn) — fires when
  `host-identity.json`'s `active_focus` points at a mode that
  isn't reachable from the registered mode in the manifest's
  current mode tree. Catches the hand-edit case where the
  operator manually changes the field to a value outside the
  trust-boundary subtree. The next `maury focus use <other>`
  would correct this; doctor flags it so the operator notices
  the drift. Per ADR-0052 §Confirmation. Soft-skipped when no
  manifest is available (e.g., doctor is run outside a base
  repo).
"""

from __future__ import annotations

from pathlib import Path

from .report import Finding, Severity


def run_system_health_checks(
    *,
    target_dir: Path,
    host_id_file: Path,
    manifest_path: Path | None = None,
) -> list[Finding]:
    """Run every system-health check; return the aggregated findings.

    `manifest_path` is optional — if provided and loadable, the
    focus-unreachable check runs against it. If absent or unreadable,
    that check is silently skipped (doctor remains usable from any
    directory, not just inside a base repo).
    """
    findings: list[Finding] = []
    findings.extend(_check_baseline_missing(target_dir=target_dir, host_id_file=host_id_file))
    findings.extend(_check_watermark_stale(target_dir=target_dir))
    findings.extend(_check_focus_unreachable(target_dir=target_dir, manifest_path=manifest_path))
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


def _check_focus_unreachable(
    *,
    target_dir: Path,
    manifest_path: Path | None,
) -> list[Finding]:
    """ADR-0052 §Confirmation: detect hand-edits to `active_focus`
    that point outside the host's trust-boundary subtree.

    Soft-skipped if (a) no baseline yet, (b) no active focus set,
    (c) no manifest available, or (d) the manifest fails to parse.
    The check is a safety net for accidental drift — it should
    never become the reason doctor fails on an otherwise-healthy
    host."""
    from maury.focus import is_reachable
    from maury.host_identity import read_baseline
    from maury.manifest import ManifestError, load_manifest

    try:
        baseline = read_baseline(target_dir)
    except Exception:
        return []  # Read errors surface elsewhere; not doctor's job.
    if baseline is None or baseline.active_focus is None:
        return []
    if manifest_path is None or not manifest_path.is_file():
        return []

    try:
        manifest = load_manifest(manifest_path)
    except ManifestError:
        return []

    target_mode_id = manifest.profile_id_by_name(baseline.active_focus)
    if target_mode_id is None:
        return [
            Finding(
                check_id="focus-unreachable",
                severity=Severity.WARN,
                message=(
                    f"`host-identity.json` has `active_focus`={baseline.active_focus!r}, "
                    f"but no mode by that name exists in {manifest_path}. The next "
                    f"mode-scoped command will render against the registered mode "
                    f"({baseline.mode_name_at_bootstrap!r}) instead."
                ),
                rubric_quote=(
                    "ADR-0052 §Confirmation: `maury doctor` gains a check that "
                    "`host-identity.json`'s `active_focus` is reachable from "
                    "`mode_name_at_bootstrap`."
                ),
                fix_suggestion=(
                    "Run `maury focus use <path>` with a known-good path, or "
                    "`maury focus use` (no argument) to clear the field and "
                    "resume the registered mode."
                ),
            )
        ]

    if is_reachable(
        target_mode_id=target_mode_id,
        registered_mode_id=baseline.mode_id,
        manifest=manifest,
    ):
        return []

    return [
        Finding(
            check_id="focus-unreachable",
            severity=Severity.WARN,
            message=(
                f"`host-identity.json` has `active_focus`={baseline.active_focus!r}, "
                f"which is not reachable from the registered mode "
                f"({baseline.mode_name_at_bootstrap!r}) in the current mode tree. "
                f"This is a hand-edit drift signal — `maury focus use` cannot "
                f"set a value outside the trust-boundary subtree."
            ),
            rubric_quote=(
                "ADR-0052 §Confirmation: `maury doctor` gains a check that "
                "`host-identity.json`'s `active_focus` is reachable from "
                "`mode_name_at_bootstrap`."
            ),
            fix_suggestion=(
                f"Run `maury focus use` (no argument) to clear the field, "
                f"or `maury focus use <path>` with a path inside the "
                f"{baseline.mode_name_at_bootstrap!r} subtree."
            ),
        )
    ]


__all__ = [
    "run_system_health_checks",
]
