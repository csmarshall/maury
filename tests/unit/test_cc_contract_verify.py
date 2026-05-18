"""Unit tests for `maury.cc_contract_verify` (the maury verify-cc-contract verifier)."""

from __future__ import annotations

import hashlib
import urllib.error
from pathlib import Path

import pytest

from maury.cc_contract_verify import (
    DEFAULT_TIMEOUT,
    EntryStatus,
    ManifestEntry,
    compare_entry,
    default_snapshot_dir,
    find_snapshot_file,
    format_report_json,
    format_report_text,
    parse_manifest,
    run_contract_verification,
    sha256_hex,
)

# ---- parse_manifest -------------------------------------------------------


def test_parse_manifest_happy_path(tmp_path: Path) -> None:
    m = tmp_path / "MANIFEST.txt"
    m.write_text(
        "# Snapshot manifest — fixture\n"
        "\n"
        "hooks    100  aaaa  https://example.com/hooks\n"
        "memory   200  bbbb  https://example.com/memory\n"
    )
    entries = parse_manifest(m)
    assert len(entries) == 2
    assert entries[0] == ManifestEntry(
        name="hooks", bytes_expected=100, sha256_expected="aaaa", url="https://example.com/hooks"
    )
    assert entries[1].name == "memory"


def test_parse_manifest_skips_blanks_and_comments(tmp_path: Path) -> None:
    m = tmp_path / "MANIFEST.txt"
    m.write_text("# header\n\n# another comment\nhooks  100  aaaa  https://example.com/hooks\n\n")
    entries = parse_manifest(m)
    assert len(entries) == 1


def test_parse_manifest_raises_on_wrong_column_count(tmp_path: Path) -> None:
    m = tmp_path / "MANIFEST.txt"
    m.write_text("hooks 100 aaaa\n")  # only 3 columns; missing URL
    with pytest.raises(ValueError, match="4 whitespace-separated columns"):
        parse_manifest(m)


def test_parse_manifest_raises_on_non_integer_bytes(tmp_path: Path) -> None:
    m = tmp_path / "MANIFEST.txt"
    m.write_text("hooks notanint aaaa https://example.com/hooks\n")
    with pytest.raises(ValueError, match="not an integer"):
        parse_manifest(m)


def test_parse_manifest_raises_on_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        parse_manifest(tmp_path / "does-not-exist.txt")


# ---- sha256_hex -----------------------------------------------------------


def test_sha256_hex_matches_stdlib() -> None:
    data = b"hello world"
    assert sha256_hex(data) == hashlib.sha256(data).hexdigest()


# ---- find_snapshot_file ---------------------------------------------------


def test_find_snapshot_file_prefers_html(tmp_path: Path) -> None:
    (tmp_path / "hooks.html").write_text("html version")
    (tmp_path / "hooks.txt").write_text("text version")
    found = find_snapshot_file(tmp_path, "hooks")
    assert found is not None
    assert found.suffix == ".html"


def test_find_snapshot_file_returns_none_when_missing(tmp_path: Path) -> None:
    assert find_snapshot_file(tmp_path, "hooks") is None


def test_find_snapshot_file_falls_back_to_first_match(tmp_path: Path) -> None:
    (tmp_path / "hooks.txt").write_text("text")
    found = find_snapshot_file(tmp_path, "hooks")
    assert found is not None
    assert found.suffix == ".txt"


# ---- compare_entry --------------------------------------------------------


def _make_entry(name: str = "hooks", sha: str = "aaaa") -> ManifestEntry:
    return ManifestEntry(
        name=name,
        bytes_expected=10,
        sha256_expected=sha,
        url=f"https://example.com/{name}",
    )


def test_compare_entry_unchanged(tmp_path: Path) -> None:
    (tmp_path / "hooks.html").write_text("snapshot")
    remote_body = b"hello world"
    expected_sha = hashlib.sha256(remote_body).hexdigest()
    entry = _make_entry(sha=expected_sha)

    def fake_get(_url: str, _timeout: float) -> tuple[int, bytes]:
        return 200, remote_body

    f = compare_entry(entry, tmp_path, fake_get, timeout=1.0)
    assert f.status is EntryStatus.UNCHANGED
    assert f.actual_sha256 == expected_sha
    assert f.actual_bytes == len(remote_body)


def test_compare_entry_drift(tmp_path: Path) -> None:
    (tmp_path / "hooks.html").write_text("snapshot")
    entry = _make_entry(sha="aaaa")  # wrong expected sha

    def fake_get(_url: str, _timeout: float) -> tuple[int, bytes]:
        return 200, b"different content"

    f = compare_entry(entry, tmp_path, fake_get, timeout=1.0)
    assert f.status is EntryStatus.DRIFT
    assert f.actual_sha256 is not None
    assert f.actual_sha256 != entry.sha256_expected


def test_compare_entry_fetch_error_on_url_error(tmp_path: Path) -> None:
    (tmp_path / "hooks.html").write_text("snapshot")
    entry = _make_entry()

    def fake_get(_url: str, _timeout: float) -> tuple[int, bytes]:
        raise urllib.error.URLError("connection refused")

    f = compare_entry(entry, tmp_path, fake_get, timeout=1.0)
    assert f.status is EntryStatus.FETCH_ERROR
    assert f.error is not None
    assert "connection refused" in f.error


def test_compare_entry_fetch_error_on_non_2xx(tmp_path: Path) -> None:
    (tmp_path / "hooks.html").write_text("snapshot")
    entry = _make_entry()

    def fake_get(_url: str, _timeout: float) -> tuple[int, bytes]:
        return 503, b"upstream busy"

    f = compare_entry(entry, tmp_path, fake_get, timeout=1.0)
    assert f.status is EntryStatus.FETCH_ERROR
    assert f.error is not None
    assert "503" in f.error


def test_compare_entry_snapshot_missing(tmp_path: Path) -> None:
    entry = _make_entry()  # tmp_path has no hooks.* file
    f = compare_entry(entry, tmp_path, lambda _u, _t: (200, b""), timeout=1.0)
    assert f.status is EntryStatus.SNAPSHOT_MISSING
    assert f.actual_sha256 is None
    assert f.error is not None


# ---- run_contract_verification --------------------------------------------


def test_run_contract_verification_full_run(tmp_path: Path) -> None:
    (tmp_path / "hooks.html").write_text("snapshot")
    (tmp_path / "memory.html").write_text("snapshot")
    body_hooks = b"hooks content"
    body_memory = b"memory content"
    sha_hooks = hashlib.sha256(body_hooks).hexdigest()
    # memory's manifest sha is deliberately wrong → triggers DRIFT classification
    (tmp_path / "MANIFEST.txt").write_text(
        f"hooks  {len(body_hooks)}  {sha_hooks}  https://example.com/hooks\n"
        f"memory {len(body_memory)} bbbb https://example.com/memory\n"
    )

    def fake_get(url: str, _timeout: float) -> tuple[int, bytes]:
        if url.endswith("/hooks"):
            return 200, body_hooks
        return 200, body_memory

    report = run_contract_verification(tmp_path, http_get=fake_get, timeout=1.0)
    assert report.snapshot_dir == tmp_path
    counts = report.summary_counts()
    assert counts["unchanged"] == 1
    assert counts["drift"] == 1
    assert report.has_drift()


def test_run_contract_verification_no_drift(tmp_path: Path) -> None:
    (tmp_path / "hooks.html").write_text("snapshot")
    body = b"hooks content"
    sha = hashlib.sha256(body).hexdigest()
    (tmp_path / "MANIFEST.txt").write_text(f"hooks {len(body)} {sha} https://example.com/hooks\n")
    report = run_contract_verification(tmp_path, http_get=lambda _u, _t: (200, body), timeout=1.0)
    assert not report.has_drift()
    assert report.summary_counts()["unchanged"] == 1


def test_default_timeout_constant_is_reasonable() -> None:
    # Sanity: production code shouldn't ship a 0-timeout or unbounded.
    assert 1.0 < DEFAULT_TIMEOUT <= 120.0


# ---- default_snapshot_dir -------------------------------------------------


def test_default_snapshot_dir_returns_most_recent(tmp_path: Path) -> None:
    repo = tmp_path / "fake-repo"
    snaps = repo / "docs" / "claude-code-snapshots"
    older = snaps / "2026-01-01"
    newer = snaps / "2026-05-07"
    older.mkdir(parents=True)
    newer.mkdir(parents=True)
    (older / "MANIFEST.txt").write_text("")
    (newer / "MANIFEST.txt").write_text("")
    assert default_snapshot_dir(repo) == newer


def test_default_snapshot_dir_returns_none_when_missing(tmp_path: Path) -> None:
    assert default_snapshot_dir(tmp_path) is None


def test_default_snapshot_dir_skips_dirs_without_manifest(tmp_path: Path) -> None:
    repo = tmp_path / "fake-repo"
    snaps = repo / "docs" / "claude-code-snapshots"
    incomplete = snaps / "2026-05-07"
    incomplete.mkdir(parents=True)
    # No MANIFEST.txt — should be excluded.
    assert default_snapshot_dir(repo) is None


# ---- formatters -----------------------------------------------------------


def test_format_report_text_includes_summary(tmp_path: Path) -> None:
    (tmp_path / "hooks.html").write_text("snapshot")
    body = b"content"
    sha = hashlib.sha256(body).hexdigest()
    (tmp_path / "MANIFEST.txt").write_text(f"hooks {len(body)} {sha} https://example.com/hooks\n")
    report = run_contract_verification(tmp_path, http_get=lambda _u, _t: (200, body), timeout=1.0)
    out = format_report_text(report)
    assert "snapshot:" in out
    assert "hooks" in out
    assert "unchanged=1" in out
    assert "drift=0" in out


def test_format_report_json_round_trips(tmp_path: Path) -> None:
    import json

    (tmp_path / "hooks.html").write_text("snapshot")
    body = b"content"
    sha = hashlib.sha256(body).hexdigest()
    (tmp_path / "MANIFEST.txt").write_text(f"hooks {len(body)} {sha} https://example.com/hooks\n")
    report = run_contract_verification(tmp_path, http_get=lambda _u, _t: (200, body), timeout=1.0)
    out = format_report_json(report)
    parsed = json.loads(out)
    assert parsed["summary"]["unchanged"] == 1
    assert parsed["findings"][0]["name"] == "hooks"
    assert parsed["findings"][0]["status"] == "unchanged"


def test_format_report_text_marks_drift(tmp_path: Path) -> None:
    (tmp_path / "hooks.html").write_text("snapshot")
    (tmp_path / "MANIFEST.txt").write_text("hooks 10 wrongsha https://example.com/hooks\n")
    report = run_contract_verification(tmp_path, http_get=lambda _u, _t: (200, b"actual"), timeout=1.0)
    out = format_report_text(report)
    assert "drift" in out
    assert "expected sha256" in out
