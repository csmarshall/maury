"""Tests for the reconcile module (Phase 5.x.a slice 3)."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from maury.drift import DriftEntry, DriftKind, DriftReport
from maury.reconcile import (
    ALL_ACTIONS,
    CAPTURES_FILENAME,
    HAND_MANAGED_FILENAME,
    ReconcileAction,
    ReconcileOutcome,
    ReconcileSummary,
    captures_path,
    hand_managed_path,
    reconcile,
)
from maury.render import RenderedFile, RenderResult

# ---- helpers ------------------------------------------------------------


def _drift_entry(
    path: str,
    kind: DriftKind = DriftKind.MODIFIED,
    *,
    expected_sha: str | None = "expected_sha_abc",
    actual_sha: str | None = "actual_sha_xyz",
) -> DriftEntry:
    return DriftEntry(
        path=path,
        kind=kind,
        expected_sha=expected_sha,
        actual_sha=actual_sha,
        expected_size=10,
        actual_size=20,
    )


def _make_render_result(*files: tuple[str, bytes]) -> RenderResult:
    return RenderResult(
        files=[
            RenderedFile(target_path=path, content=content, source_layer="test", mode=0o644) for path, content in files
        ],
        warnings=[],
    )


def _scripted_prompter(answers: dict[str, ReconcileAction]) -> Callable[[DriftEntry], ReconcileAction]:
    """Returns a prompter that maps drift entries to pre-decided actions by path."""

    def _prompter(entry: DriftEntry) -> ReconcileAction:
        if entry.path not in answers:
            raise AssertionError(f"unexpected prompt for path {entry.path!r}")
        return answers[entry.path]

    return _prompter


# ---- module surface -----------------------------------------------------


class TestModuleSurface:
    def test_all_actions_lists_every_enum_member(self) -> None:
        assert set(ALL_ACTIONS) == set(ReconcileAction)
        # Order matters for the menu UI; verify the documented order.
        assert list(ALL_ACTIONS) == [
            ReconcileAction.ADOPT,
            ReconcileAction.ADAPT,
            ReconcileAction.MARK_MANAGED,
            ReconcileAction.REVERT,
            ReconcileAction.SKIP_ONCE,
        ]

    def test_action_values_are_kebab_lowercase(self) -> None:
        # Values are stable identifiers; user-typeable.
        assert ReconcileAction.ADOPT.value == "adopt"
        assert ReconcileAction.ADAPT.value == "adapt"
        assert ReconcileAction.MARK_MANAGED.value == "mark-managed"
        assert ReconcileAction.REVERT.value == "revert"
        assert ReconcileAction.SKIP_ONCE.value == "skip-once"

    def test_captures_path_lives_under_staging(self, tmp_path: Path) -> None:
        cp = captures_path(tmp_path)
        assert cp.name == CAPTURES_FILENAME
        assert cp.parent.name == "maury-staging"

    def test_hand_managed_path_layout(self, tmp_path: Path) -> None:
        hm = hand_managed_path(base_repo=tmp_path, profile_name="personal", host_name="ws1")
        assert hm == tmp_path / "profiles" / "personal" / "hosts" / "ws1" / HAND_MANAGED_FILENAME


# ---- empty / no-op cases -----------------------------------------------


class TestEmptyDrift:
    def test_no_drift_no_outcomes(self, tmp_path: Path) -> None:
        report = DriftReport(entries=[], has_last_render=True)
        summary = reconcile(
            drift_report=report,
            target_dir=tmp_path,
            rendered=_make_render_result(),
            prompter=_scripted_prompter({}),
        )
        assert summary.outcomes == []
        assert not summary.has_errors()

    def test_only_expected_entries_no_outcomes(self, tmp_path: Path) -> None:
        report = DriftReport(
            entries=[_drift_entry("ok.md", kind=DriftKind.EXPECTED)],
            has_last_render=True,
        )
        summary = reconcile(
            drift_report=report,
            target_dir=tmp_path,
            rendered=_make_render_result(),
            prompter=_scripted_prompter({}),
        )
        assert summary.outcomes == []


# ---- skip-once ----------------------------------------------------------


class TestSkipOnce:
    def test_skip_once_takes_no_action(self, tmp_path: Path) -> None:
        # Set up a hand-edited file
        (tmp_path / "CLAUDE.md").write_text("hand-edited\n")
        report = DriftReport(entries=[_drift_entry("CLAUDE.md")], has_last_render=True)
        summary = reconcile(
            drift_report=report,
            target_dir=tmp_path,
            rendered=_make_render_result(("CLAUDE.md", b"original")),
            prompter=_scripted_prompter({"CLAUDE.md": ReconcileAction.SKIP_ONCE}),
        )
        assert len(summary.outcomes) == 1
        assert summary.outcomes[0].action == ReconcileAction.SKIP_ONCE
        assert summary.paths_skipped == 1
        # File is unchanged
        assert (tmp_path / "CLAUDE.md").read_text() == "hand-edited\n"


# ---- revert -------------------------------------------------------------


class TestRevert:
    def test_revert_restores_modified_file_to_render_content(self, tmp_path: Path) -> None:
        (tmp_path / "CLAUDE.md").write_text("hand-edited content\n")
        report = DriftReport(entries=[_drift_entry("CLAUDE.md")], has_last_render=True)
        rendered = _make_render_result(("CLAUDE.md", b"original render\n"))
        summary = reconcile(
            drift_report=report,
            target_dir=tmp_path,
            rendered=rendered,
            prompter=_scripted_prompter({"CLAUDE.md": ReconcileAction.REVERT}),
        )
        assert summary.paths_reverted == 1
        assert (tmp_path / "CLAUDE.md").read_bytes() == b"original render\n"

    def test_revert_recreates_missing_file(self, tmp_path: Path) -> None:
        # File was deleted by the user — revert should re-create it from render.
        report = DriftReport(
            entries=[_drift_entry("CLAUDE.md", kind=DriftKind.MISSING, actual_sha=None)],
            has_last_render=True,
        )
        rendered = _make_render_result(("CLAUDE.md", b"recreated\n"))
        reconcile(
            drift_report=report,
            target_dir=tmp_path,
            rendered=rendered,
            prompter=_scripted_prompter({"CLAUDE.md": ReconcileAction.REVERT}),
        )
        assert (tmp_path / "CLAUDE.md").read_bytes() == b"recreated\n"

    def test_revert_removes_untracked_file(self, tmp_path: Path) -> None:
        # User added a file maury doesn't render. Revert = remove it.
        target = tmp_path / "skills" / "user-added.md"
        target.parent.mkdir(parents=True)
        target.write_text("user added this\n")
        report = DriftReport(
            entries=[_drift_entry("skills/user-added.md", kind=DriftKind.UNTRACKED, expected_sha=None)],
            has_last_render=True,
        )
        summary = reconcile(
            drift_report=report,
            target_dir=tmp_path,
            rendered=_make_render_result(),
            prompter=_scripted_prompter({"skills/user-added.md": ReconcileAction.REVERT}),
        )
        assert not target.exists()
        assert summary.outcomes[0].detail == "untracked file removed"

    def test_revert_modified_file_not_in_render_raises(self, tmp_path: Path) -> None:
        # Should not normally happen; flag as an error (render-vs-baseline mismatch).
        (tmp_path / "vanished.md").write_text("local content\n")
        report = DriftReport(entries=[_drift_entry("vanished.md")], has_last_render=True)
        # Render output has nothing for this path
        rendered = _make_render_result()
        summary = reconcile(
            drift_report=report,
            target_dir=tmp_path,
            rendered=rendered,
            prompter=_scripted_prompter({"vanished.md": ReconcileAction.REVERT}),
        )
        assert summary.has_errors()
        assert any("not present in current render output" in e for e in summary.errors)
        # File NOT touched (we errored before writing)
        assert (tmp_path / "vanished.md").read_text() == "local content\n"

    def test_revert_creates_parent_dirs(self, tmp_path: Path) -> None:
        # Render output has a file deep in subdirs that doesn't exist on disk yet.
        report = DriftReport(
            entries=[_drift_entry("agents/deep/nested.md", kind=DriftKind.MISSING)],
            has_last_render=True,
        )
        rendered = _make_render_result(("agents/deep/nested.md", b"deep file\n"))
        reconcile(
            drift_report=report,
            target_dir=tmp_path,
            rendered=rendered,
            prompter=_scripted_prompter({"agents/deep/nested.md": ReconcileAction.REVERT}),
        )
        assert (tmp_path / "agents" / "deep" / "nested.md").read_bytes() == b"deep file\n"


# ---- mark-managed -------------------------------------------------------


class TestMarkManaged:
    def test_mark_managed_creates_hand_managed_file(self, tmp_path: Path) -> None:
        target = tmp_path / "target"
        target.mkdir()
        (target / "CLAUDE.md").write_text("hand-edited\n")
        base_repo = tmp_path / "base-repo"
        report = DriftReport(entries=[_drift_entry("CLAUDE.md")], has_last_render=True)
        summary = reconcile(
            drift_report=report,
            target_dir=target,
            rendered=_make_render_result(("CLAUDE.md", b"original\n")),
            prompter=_scripted_prompter({"CLAUDE.md": ReconcileAction.MARK_MANAGED}),
            base_repo=base_repo,
            profile_name="personal",
            host_name="ws1",
        )
        assert summary.paths_marked_managed == 1
        hm = base_repo / "profiles" / "personal" / "hosts" / "ws1" / "hand-managed.json"
        assert hm.is_file()
        data = json.loads(hm.read_text())
        assert data["schema_version"] == 1
        assert data["paths"] == ["CLAUDE.md"]
        # Original on-disk file untouched
        assert (target / "CLAUDE.md").read_text() == "hand-edited\n"

    def test_mark_managed_appends_to_existing(self, tmp_path: Path) -> None:
        base_repo = tmp_path / "base-repo"
        # Pre-existing hand-managed.json with one entry
        hm = base_repo / "profiles" / "personal" / "hosts" / "ws1" / "hand-managed.json"
        hm.parent.mkdir(parents=True)
        hm.write_text(json.dumps({"schema_version": 1, "paths": ["existing.md"]}))

        report = DriftReport(entries=[_drift_entry("new.md")], has_last_render=True)
        reconcile(
            drift_report=report,
            target_dir=tmp_path,
            rendered=_make_render_result(("new.md", b"")),
            prompter=_scripted_prompter({"new.md": ReconcileAction.MARK_MANAGED}),
            base_repo=base_repo,
            profile_name="personal",
            host_name="ws1",
        )
        data = json.loads(hm.read_text())
        # Both paths present, sorted
        assert data["paths"] == ["existing.md", "new.md"]

    def test_mark_managed_idempotent(self, tmp_path: Path) -> None:
        """Marking the same path twice doesn't duplicate it."""
        base_repo = tmp_path / "base-repo"
        report = DriftReport(entries=[_drift_entry("CLAUDE.md")], has_last_render=True)
        for _ in range(2):
            reconcile(
                drift_report=report,
                target_dir=tmp_path,
                rendered=_make_render_result(("CLAUDE.md", b"")),
                prompter=_scripted_prompter({"CLAUDE.md": ReconcileAction.MARK_MANAGED}),
                base_repo=base_repo,
                profile_name="p",
                host_name="h",
            )
        hm = base_repo / "profiles" / "p" / "hosts" / "h" / "hand-managed.json"
        data = json.loads(hm.read_text())
        assert data["paths"] == ["CLAUDE.md"]  # single entry

    def test_mark_managed_without_args_errors(self, tmp_path: Path) -> None:
        report = DriftReport(entries=[_drift_entry("CLAUDE.md")], has_last_render=True)
        summary = reconcile(
            drift_report=report,
            target_dir=tmp_path,
            rendered=_make_render_result(("CLAUDE.md", b"")),
            prompter=_scripted_prompter({"CLAUDE.md": ReconcileAction.MARK_MANAGED}),
            # base_repo / profile_name / host_name omitted
        )
        assert summary.has_errors()
        assert any("mark-managed requires" in e for e in summary.errors)

    def test_mark_managed_refuses_unknown_schema_version(self, tmp_path: Path) -> None:
        base_repo = tmp_path / "base-repo"
        hm = base_repo / "profiles" / "p" / "hosts" / "h" / "hand-managed.json"
        hm.parent.mkdir(parents=True)
        hm.write_text(json.dumps({"schema_version": 99, "paths": []}))
        report = DriftReport(entries=[_drift_entry("CLAUDE.md")], has_last_render=True)
        summary = reconcile(
            drift_report=report,
            target_dir=tmp_path,
            rendered=_make_render_result(("CLAUDE.md", b"")),
            prompter=_scripted_prompter({"CLAUDE.md": ReconcileAction.MARK_MANAGED}),
            base_repo=base_repo,
            profile_name="p",
            host_name="h",
        )
        assert summary.has_errors()
        assert any("schema_version" in e for e in summary.errors)


# ---- adopt --------------------------------------------------------------


class TestAdopt:
    def test_adopt_writes_capture_file(self, tmp_path: Path) -> None:
        (tmp_path / "CLAUDE.md").write_text("My personal preference.\n")
        report = DriftReport(entries=[_drift_entry("CLAUDE.md")], has_last_render=True)
        summary = reconcile(
            drift_report=report,
            target_dir=tmp_path,
            rendered=_make_render_result(("CLAUDE.md", b"original\n")),
            prompter=_scripted_prompter({"CLAUDE.md": ReconcileAction.ADOPT}),
            session_id="abc123",
        )
        assert summary.captures_written == 1
        cp = captures_path(tmp_path)
        assert cp.is_file()
        records = [json.loads(line) for line in cp.read_text().splitlines() if line]
        assert len(records) == 1
        rec = records[0]
        assert rec["source"] == "drift-adopt"
        assert rec["drift_path"] == "CLAUDE.md"
        assert rec["session_id"] == "abc123"
        assert rec["body_excerpt"] == "My personal preference.\n"

    def test_adopt_does_not_modify_local_file(self, tmp_path: Path) -> None:
        (tmp_path / "CLAUDE.md").write_text("hand-edited\n")
        report = DriftReport(entries=[_drift_entry("CLAUDE.md")], has_last_render=True)
        reconcile(
            drift_report=report,
            target_dir=tmp_path,
            rendered=_make_render_result(("CLAUDE.md", b"original\n")),
            prompter=_scripted_prompter({"CLAUDE.md": ReconcileAction.ADOPT}),
        )
        # File unchanged per ADR-0017
        assert (tmp_path / "CLAUDE.md").read_text() == "hand-edited\n"

    def test_adopt_appends_to_existing_captures_file(self, tmp_path: Path) -> None:
        cp = captures_path(tmp_path)
        cp.parent.mkdir(parents=True)
        cp.write_text(json.dumps({"existing": "record"}) + "\n")
        (tmp_path / "CLAUDE.md").write_text("new\n")
        report = DriftReport(entries=[_drift_entry("CLAUDE.md")], has_last_render=True)
        reconcile(
            drift_report=report,
            target_dir=tmp_path,
            rendered=_make_render_result(("CLAUDE.md", b"")),
            prompter=_scripted_prompter({"CLAUDE.md": ReconcileAction.ADOPT}),
        )
        lines = cp.read_text().splitlines()
        assert len(lines) == 2
        # Existing record preserved, new appended
        assert json.loads(lines[0]) == {"existing": "record"}

    def test_adopt_truncates_long_body_excerpt(self, tmp_path: Path) -> None:
        big = "x" * 5000
        (tmp_path / "big.md").write_text(big)
        report = DriftReport(entries=[_drift_entry("big.md")], has_last_render=True)
        reconcile(
            drift_report=report,
            target_dir=tmp_path,
            rendered=_make_render_result(("big.md", b"")),
            prompter=_scripted_prompter({"big.md": ReconcileAction.ADOPT}),
        )
        cp = captures_path(tmp_path)
        rec = json.loads(cp.read_text().strip())
        assert "(truncated)" in rec["body_excerpt"]
        assert len(rec["body_excerpt"]) < 5000

    def test_adopt_handles_binary_files(self, tmp_path: Path) -> None:
        (tmp_path / "bin/data").parent.mkdir()
        (tmp_path / "bin/data").write_bytes(b"\x00\x01\x02\xff")
        report = DriftReport(entries=[_drift_entry("bin/data")], has_last_render=True)
        reconcile(
            drift_report=report,
            target_dir=tmp_path,
            rendered=_make_render_result(("bin/data", b"")),
            prompter=_scripted_prompter({"bin/data": ReconcileAction.ADOPT}),
        )
        cp = captures_path(tmp_path)
        rec = json.loads(cp.read_text().strip())
        assert rec["body_excerpt"] == "(binary content; not embedded)"


# ---- adapt (deferred fallback) ------------------------------------------


class TestAdaptFallback:
    def test_adapt_falls_back_to_adopt_with_marker(self, tmp_path: Path) -> None:
        (tmp_path / "CLAUDE.md").write_text("text\n")
        report = DriftReport(entries=[_drift_entry("CLAUDE.md")], has_last_render=True)
        summary = reconcile(
            drift_report=report,
            target_dir=tmp_path,
            rendered=_make_render_result(("CLAUDE.md", b"")),
            prompter=_scripted_prompter({"CLAUDE.md": ReconcileAction.ADAPT}),
        )
        assert summary.captures_written == 1
        assert summary.paths_falling_back_to_adopt == 1
        # The capture record's source field marks it as a fallback
        cp = captures_path(tmp_path)
        rec = json.loads(cp.read_text().strip())
        assert rec["source"] == "drift-adopt-fallback"


# ---- mixed scenarios ----------------------------------------------------


class TestMixedScenarios:
    def test_walk_processes_each_actionable_entry(self, tmp_path: Path) -> None:
        # 4 drift entries: one each of MODIFIED, MISSING, UNTRACKED, EXPECTED
        # The EXPECTED one shouldn't be prompted.
        target = tmp_path / "target"
        target.mkdir()
        (target / "modified.md").write_text("hand-edited\n")
        (target / "skills" / "untracked.md").parent.mkdir()
        (target / "skills" / "untracked.md").write_text("u\n")

        report = DriftReport(
            entries=[
                _drift_entry("modified.md", kind=DriftKind.MODIFIED),
                _drift_entry("missing.md", kind=DriftKind.MISSING),
                _drift_entry("skills/untracked.md", kind=DriftKind.UNTRACKED),
                _drift_entry("ok.md", kind=DriftKind.EXPECTED),
            ],
            has_last_render=True,
        )
        rendered = _make_render_result(
            ("modified.md", b"original\n"),
            ("missing.md", b"recreated\n"),
        )
        summary = reconcile(
            drift_report=report,
            target_dir=target,
            rendered=rendered,
            prompter=_scripted_prompter(
                {
                    "modified.md": ReconcileAction.SKIP_ONCE,
                    "missing.md": ReconcileAction.REVERT,
                    "skills/untracked.md": ReconcileAction.REVERT,
                }
            ),
        )
        # Three actionable entries processed (EXPECTED skipped)
        assert len(summary.outcomes) == 3
        # missing.md was recreated by revert
        assert (target / "missing.md").read_bytes() == b"recreated\n"
        # untracked file was removed
        assert not (target / "skills" / "untracked.md").exists()
        # modified.md left as-is (skip-once)
        assert (target / "modified.md").read_text() == "hand-edited\n"

    def test_one_action_error_does_not_abort_the_rest(self, tmp_path: Path) -> None:
        # First entry's action errors (mark-managed without args);
        # second entry's skip-once should still process.
        report = DriftReport(
            entries=[
                _drift_entry("a.md", kind=DriftKind.MODIFIED),
                _drift_entry("b.md", kind=DriftKind.MODIFIED),
            ],
            has_last_render=True,
        )
        summary = reconcile(
            drift_report=report,
            target_dir=tmp_path,
            rendered=_make_render_result(("a.md", b""), ("b.md", b"")),
            prompter=_scripted_prompter(
                {
                    "a.md": ReconcileAction.MARK_MANAGED,  # errors (no base_repo)
                    "b.md": ReconcileAction.SKIP_ONCE,
                }
            ),
            # base_repo intentionally omitted to trigger the mark-managed error
        )
        assert len(summary.errors) == 1
        # b.md still processed
        assert any(o.path == "b.md" and o.action == ReconcileAction.SKIP_ONCE for o in summary.outcomes)


# ---- ReconcileSummary ---------------------------------------------------


class TestReconcileSummary:
    def test_default_summary_is_empty_and_clean(self) -> None:
        s = ReconcileSummary()
        assert s.outcomes == []
        assert s.captures_written == 0
        assert s.paths_marked_managed == 0
        assert s.paths_reverted == 0
        assert s.paths_skipped == 0
        assert s.paths_falling_back_to_adopt == 0
        assert not s.has_errors()

    def test_has_errors_reflects_errors_list(self) -> None:
        s = ReconcileSummary(errors=["something"])
        assert s.has_errors()


# ---- ReconcileOutcome ---------------------------------------------------


class TestReconcileOutcome:
    def test_outcome_is_frozen(self) -> None:
        from dataclasses import FrozenInstanceError

        o = ReconcileOutcome(path="x", action=ReconcileAction.SKIP_ONCE)
        with pytest.raises(FrozenInstanceError):
            # Frozen-mutation invariant test — runtime failure is the
            # assertion; the `[misc]` ignore acknowledges mypy would
            # statically catch this (which is exactly what we want).
            o.path = "y"  # type: ignore[misc]


# ---- audit-log integration (ADR-0035 `reconcile_action`) ----------------


class TestAuditLogIntegration:
    def test_each_prompt_emits_reconcile_action_event(self, tmp_path: Path) -> None:
        """Every user choice in reconcile fires one `reconcile_action`
        audit event with the path and chosen action."""
        from maury.audit_log import read_events

        # Two drift entries → two prompts → two audit events.
        report = DriftReport(
            entries=[
                _drift_entry("a.md", DriftKind.MODIFIED),
                _drift_entry("b.md", DriftKind.MODIFIED),
            ],
            has_last_render=True,
        )
        rendered = _make_render_result(("a.md", b"a"), ("b.md", b"b"))
        prompter = _scripted_prompter(
            {
                "a.md": ReconcileAction.SKIP_ONCE,
                "b.md": ReconcileAction.SKIP_ONCE,
            }
        )

        reconcile(
            drift_report=report,
            target_dir=tmp_path,
            rendered=rendered,
            prompter=prompter,
            session_id="sess-abc",
        )

        events = list(reversed(list(read_events(tmp_path))))
        kinds = [e.event for e in events]
        assert kinds == ["reconcile_action", "reconcile_action"]
        assert {e.details["path"] for e in events} == {"a.md", "b.md"}
        assert all(e.details["action"] == "skip-once" for e in events)
        assert all(e.session_id == "sess-abc" for e in events)
