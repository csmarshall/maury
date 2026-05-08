"""Tests for the drift module (Phase 5.x.a — drift detection)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from maury.drift import (
    LAST_RENDER_FILENAME,
    STATE_SUBDIR,
    DriftEntry,
    DriftKind,
    DriftReport,
    DriftStateError,
    FileFingerprint,
    LastRender,
    detect_drift,
    read_last_render,
    state_path,
    write_last_render,
)

# ---- helpers ------------------------------------------------------------


def _write(target_dir: Path, rel_path: str, content: bytes | str) -> None:
    """Write a file under target_dir, creating parents as needed."""
    full = target_dir / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, str):
        full.write_text(content, encoding="utf-8")
    else:
        full.write_bytes(content)


def _sha256_hex(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# ---- FileFingerprint ----------------------------------------------------


class TestFileFingerprint:
    def test_from_bytes_computes_sha_and_size(self):
        fp = FileFingerprint.from_bytes(path="x.md", content=b"hello")
        assert fp.path == "x.md"
        assert fp.sha256 == _sha256_hex(b"hello")
        assert fp.size == 5

    def test_from_disk_returns_none_when_missing(self, tmp_path):
        assert FileFingerprint.from_disk(target_dir=tmp_path, rel_path="ghost.md") is None

    def test_from_disk_reads_existing_file(self, tmp_path):
        _write(tmp_path, "CLAUDE.md", b"content")
        fp = FileFingerprint.from_disk(target_dir=tmp_path, rel_path="CLAUDE.md")
        assert fp is not None
        assert fp.path == "CLAUDE.md"
        assert fp.sha256 == _sha256_hex(b"content")
        assert fp.size == 7

    def test_from_disk_returns_none_for_directory(self, tmp_path):
        (tmp_path / "subdir").mkdir()
        assert FileFingerprint.from_disk(target_dir=tmp_path, rel_path="subdir") is None

    def test_fingerprint_is_frozen(self):
        from dataclasses import FrozenInstanceError

        fp = FileFingerprint.from_bytes(path="a", content=b"")
        with pytest.raises(FrozenInstanceError):
            # setattr to avoid `# type: ignore[misc]` — the runtime
            # failure is the assertion, mypy doesn't need to be involved.
            setattr(fp, "path", "b")


# ---- LastRender ---------------------------------------------------------


class TestLastRender:
    def test_default_construction(self):
        lr = LastRender()
        assert lr.schema_version == 1
        assert lr.files == []
        assert lr.host_id == ""
        assert lr.profile_id == ""

    def test_by_path_indexes_files(self):
        lr = LastRender(
            files=[
                FileFingerprint(path="a", sha256="x", size=1),
                FileFingerprint(path="b", sha256="y", size=2),
            ]
        )
        idx = lr.by_path()
        assert set(idx.keys()) == {"a", "b"}
        assert idx["a"].sha256 == "x"

    def test_json_roundtrip_preserves_fields(self):
        original = LastRender(
            schema_version=1,
            rendered_at="2026-05-07T14:00:00Z",
            host_id="host_e3844a43abcd",
            profile_id="profile_a3f9c421efgh",
            files=[
                FileFingerprint(path="CLAUDE.md", sha256="abc", size=10),
                FileFingerprint(path="settings.json", sha256="def", size=20),
            ],
        )
        text = original.to_json()
        restored = LastRender.from_json(text)
        assert restored == original

    def test_from_json_rejects_unknown_schema_version(self):
        text = json.dumps({"schema_version": 99, "files": []})
        with pytest.raises(DriftStateError) as ei:
            LastRender.from_json(text)
        assert "schema_version=99" in str(ei.value)

    def test_from_json_defaults_missing_fields(self):
        text = json.dumps({"schema_version": 1, "files": []})
        lr = LastRender.from_json(text)
        assert lr.host_id == ""
        assert lr.profile_id == ""
        assert lr.rendered_at == ""
        assert lr.files == []

    def test_to_json_ends_with_newline(self):
        lr = LastRender()
        assert lr.to_json().endswith("\n")


# ---- state file persistence --------------------------------------------


class TestStatePersistence:
    def test_state_path_locates_under_maury_state(self, tmp_path):
        sp = state_path(tmp_path)
        assert sp == tmp_path / STATE_SUBDIR / LAST_RENDER_FILENAME
        assert sp.parent.name == STATE_SUBDIR

    def test_read_last_render_returns_none_when_absent(self, tmp_path):
        assert read_last_render(tmp_path) is None

    def test_write_then_read_roundtrip(self, tmp_path):
        original = LastRender(
            rendered_at="2026-05-07T15:00:00Z",
            host_id="host_x",
            profile_id="profile_y",
            files=[FileFingerprint(path="a", sha256="abc", size=3)],
        )
        path = write_last_render(tmp_path, original)
        assert path.is_file()
        restored = read_last_render(tmp_path)
        assert restored == original

    def test_write_creates_parent_dir(self, tmp_path):
        # maury-state/ doesn't exist yet
        assert not (tmp_path / STATE_SUBDIR).exists()
        write_last_render(tmp_path, LastRender())
        assert (tmp_path / STATE_SUBDIR).is_dir()
        assert (tmp_path / STATE_SUBDIR / LAST_RENDER_FILENAME).is_file()

    def test_write_overwrites_existing(self, tmp_path):
        write_last_render(tmp_path, LastRender(host_id="first"))
        write_last_render(tmp_path, LastRender(host_id="second"))
        result = read_last_render(tmp_path)
        assert result is not None
        assert result.host_id == "second"

    def test_read_raises_on_corrupted_schema_version(self, tmp_path):
        sp = state_path(tmp_path)
        sp.parent.mkdir(parents=True, exist_ok=True)
        sp.write_text(json.dumps({"schema_version": 42, "files": []}))
        with pytest.raises(DriftStateError):
            read_last_render(tmp_path)


# ---- DriftReport --------------------------------------------------------


class TestDriftReport:
    def _report(self, *kinds: DriftKind) -> DriftReport:
        entries = [
            DriftEntry(
                path=f"f{i}",
                kind=k,
                expected_sha=None,
                actual_sha=None,
                expected_size=None,
                actual_size=None,
            )
            for i, k in enumerate(kinds)
        ]
        return DriftReport(entries=entries)

    def test_has_drift_false_when_all_expected(self):
        report = self._report(DriftKind.EXPECTED, DriftKind.EXPECTED)
        assert report.has_drift() is False

    def test_has_drift_true_with_modified(self):
        assert self._report(DriftKind.EXPECTED, DriftKind.MODIFIED).has_drift() is True

    def test_has_drift_true_with_missing(self):
        assert self._report(DriftKind.MISSING).has_drift() is True

    def test_has_drift_true_with_untracked(self):
        assert self._report(DriftKind.UNTRACKED).has_drift() is True

    def test_has_drift_false_for_empty_report(self):
        # No entries → nothing to drift on. Vacuously False.
        assert DriftReport().has_drift() is False

    def test_filter_properties_only_return_their_kind(self):
        report = self._report(
            DriftKind.MODIFIED,
            DriftKind.MISSING,
            DriftKind.UNTRACKED,
            DriftKind.EXPECTED,
            DriftKind.MODIFIED,
        )
        assert len(report.modified) == 2
        assert len(report.missing) == 1
        assert len(report.untracked) == 1
        assert all(e.kind == DriftKind.MODIFIED for e in report.modified)

    def test_summary_counts_includes_all_kinds(self):
        report = self._report(DriftKind.MODIFIED, DriftKind.MODIFIED, DriftKind.EXPECTED)
        counts = report.summary_counts()
        # All kinds present in the dict, even with zero count.
        assert set(counts.keys()) == {k.value for k in DriftKind}
        assert counts["modified"] == 2
        assert counts["expected"] == 1
        assert counts["missing"] == 0
        assert counts["untracked"] == 0


# ---- detect_drift -------------------------------------------------------


class TestDetectDriftNoPriorRender:
    """detect_drift with last=None — no prior render baseline."""

    def test_empty_target_no_scan_dirs(self, tmp_path):
        report = detect_drift(target_dir=tmp_path, last=None)
        assert report.has_last_render is False
        assert report.entries == []

    def test_files_present_no_scan_dirs(self, tmp_path):
        # Without scan_dirs, files go undetected.
        _write(tmp_path, "CLAUDE.md", "hi")
        report = detect_drift(target_dir=tmp_path, last=None)
        assert report.entries == []

    def test_files_in_scan_dirs_marked_untracked(self, tmp_path):
        _write(tmp_path, "skills/foo/SKILL.md", "skill content")
        _write(tmp_path, "agents/bar.md", "agent content")
        report = detect_drift(target_dir=tmp_path, last=None, untracked_scan_dirs=["skills", "agents"])
        assert len(report.entries) == 2
        assert all(e.kind == DriftKind.UNTRACKED for e in report.entries)
        assert all("no prior render" in (e.note or "") for e in report.entries)


class TestDetectDriftWithBaseline:
    """detect_drift with a populated last-render baseline."""

    def test_unchanged_file_is_expected(self, tmp_path):
        content = b"hello world"
        _write(tmp_path, "CLAUDE.md", content)
        last = LastRender(files=[FileFingerprint.from_bytes(path="CLAUDE.md", content=content)])
        report = detect_drift(target_dir=tmp_path, last=last)
        assert len(report.entries) == 1
        assert report.entries[0].kind == DriftKind.EXPECTED
        assert report.has_drift() is False

    def test_modified_file_is_modified(self, tmp_path):
        original = b"v1"
        _write(tmp_path, "CLAUDE.md", b"v2-different")
        last = LastRender(files=[FileFingerprint.from_bytes(path="CLAUDE.md", content=original)])
        report = detect_drift(target_dir=tmp_path, last=last)
        assert len(report.entries) == 1
        e = report.entries[0]
        assert e.kind == DriftKind.MODIFIED
        assert e.expected_sha == _sha256_hex(b"v1")
        assert e.actual_sha == _sha256_hex(b"v2-different")

    def test_deleted_file_is_missing(self, tmp_path):
        # File was rendered but is no longer on disk.
        last = LastRender(files=[FileFingerprint(path="gone.md", sha256="abc", size=10)])
        report = detect_drift(target_dir=tmp_path, last=last)
        assert len(report.entries) == 1
        e = report.entries[0]
        assert e.kind == DriftKind.MISSING
        assert e.actual_sha is None
        assert e.expected_sha == "abc"

    def test_mixed_drift_kinds(self, tmp_path):
        # One unchanged, one modified, one missing, one untracked.
        unchanged = b"u"
        _write(tmp_path, "unchanged.md", unchanged)
        _write(tmp_path, "modified.md", b"new")
        _write(tmp_path, "skills/extra.md", b"untracked")
        last = LastRender(
            files=[
                FileFingerprint.from_bytes(path="unchanged.md", content=unchanged),
                FileFingerprint.from_bytes(path="modified.md", content=b"old"),
                FileFingerprint(path="missing.md", sha256="def", size=4),
            ]
        )
        report = detect_drift(target_dir=tmp_path, last=last, untracked_scan_dirs=["skills"])
        kinds = sorted(e.kind for e in report.entries)
        assert kinds == sorted([DriftKind.EXPECTED, DriftKind.MODIFIED, DriftKind.MISSING, DriftKind.UNTRACKED])
        assert report.has_drift() is True

    def test_untracked_scan_skips_dotfiles(self, tmp_path):
        # .DS_Store should not surface as drift even in scan dir.
        _write(tmp_path, "skills/.DS_Store", b"junk")
        _write(tmp_path, "skills/legit/SKILL.md", b"real")
        last = LastRender(files=[])
        report = detect_drift(target_dir=tmp_path, last=last, untracked_scan_dirs=["skills"])
        assert len(report.entries) == 1
        assert report.entries[0].path == "skills/legit/SKILL.md"

    def test_untracked_scan_skips_maury_state_dir(self, tmp_path):
        # If maury-state somehow appears as a scan_dir entry, it's still skipped.
        _write(tmp_path, f"{STATE_SUBDIR}/last-render.json", "{}")
        last = LastRender(files=[])
        report = detect_drift(
            target_dir=tmp_path,
            last=last,
            untracked_scan_dirs=[STATE_SUBDIR],
        )
        assert report.entries == []

    def test_last_render_at_propagates_to_report(self, tmp_path):
        last = LastRender(rendered_at="2026-05-07T15:30:00Z")
        report = detect_drift(target_dir=tmp_path, last=last)
        assert report.last_render_at == "2026-05-07T15:30:00Z"
        assert report.has_last_render is True

    def test_scan_dir_not_present_on_disk_is_no_op(self, tmp_path):
        # We list a scan_dir that simply doesn't exist; should not error.
        last = LastRender(files=[])
        report = detect_drift(target_dir=tmp_path, last=last, untracked_scan_dirs=["agents"])
        assert report.entries == []

    def test_untracked_scan_excludes_files_already_in_baseline(self, tmp_path):
        # File is in baseline AND in a scan_dir — should appear once as EXPECTED, not also UNTRACKED.
        content = b"x"
        _write(tmp_path, "skills/foo/SKILL.md", content)
        last = LastRender(files=[FileFingerprint.from_bytes(path="skills/foo/SKILL.md", content=content)])
        report = detect_drift(target_dir=tmp_path, last=last, untracked_scan_dirs=["skills"])
        assert len(report.entries) == 1
        assert report.entries[0].kind == DriftKind.EXPECTED


class TestDriftEntryFields:
    """Spot checks that DriftEntry carries the right metadata in each case."""

    def test_expected_entry_carries_matching_shas_and_sizes(self, tmp_path):
        content = b"abcdef"
        _write(tmp_path, "x", content)
        last = LastRender(files=[FileFingerprint.from_bytes(path="x", content=content)])
        report = detect_drift(target_dir=tmp_path, last=last)
        e = report.entries[0]
        assert e.expected_sha == e.actual_sha
        assert e.expected_size == e.actual_size == len(content)

    def test_modified_entry_carries_both_shas(self, tmp_path):
        _write(tmp_path, "x", b"new content")
        last = LastRender(files=[FileFingerprint.from_bytes(path="x", content=b"old")])
        report = detect_drift(target_dir=tmp_path, last=last)
        e = report.entries[0]
        assert e.expected_sha != e.actual_sha
        assert e.expected_sha is not None
        assert e.actual_sha is not None

    def test_missing_entry_actual_sha_none(self, tmp_path):
        last = LastRender(files=[FileFingerprint(path="x", sha256="abc", size=1)])
        report = detect_drift(target_dir=tmp_path, last=last)
        e = report.entries[0]
        assert e.actual_sha is None
        assert e.actual_size is None

    def test_untracked_entry_expected_sha_none(self, tmp_path):
        _write(tmp_path, "skills/x.md", b"content")
        last = LastRender(files=[])
        report = detect_drift(target_dir=tmp_path, last=last, untracked_scan_dirs=["skills"])
        e = report.entries[0]
        assert e.expected_sha is None
        assert e.expected_size is None
