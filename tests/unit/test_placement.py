"""Tests for `maury.placement` — rule-driven placement targets + the
append-with-provenance write (ADR-0053 slice 2)."""

from __future__ import annotations

from pathlib import Path

from maury.manifest import Manifest, ProfileSpec
from maury.placement import (
    BASE_TARGET,
    format_placement_block,
    place_finding,
    placement_relpath,
)
from maury.rules.schema import Classification, Confidence

_BASE, _WORK = "p_base", "p_work"


def _manifest() -> Manifest:
    return Manifest(
        version=1,
        profiles={
            _BASE: ProfileSpec(name="base", extends=None),
            _WORK: ProfileSpec(name="work", extends=_BASE),
        },
        hosts={},
    )


def _cls(profile: str | None, host_overlay: str | None = None) -> Classification:
    return Classification(
        profile=profile,
        host_overlay=host_overlay,
        confidence=Confidence.HIGH,
        trace=(),
        forbidden_by=(),
    )


# ---- placement_relpath --------------------------------------------------


def test_no_match_returns_none() -> None:
    assert placement_relpath(_cls(None), manifest=_manifest()) is None


def test_base_goes_to_root_claude_md() -> None:
    assert placement_relpath(_cls("base"), manifest=_manifest()) == BASE_TARGET == "CLAUDE.md"


def test_mode_goes_to_fragment() -> None:
    assert placement_relpath(_cls("work"), manifest=_manifest()) == "profiles/work/CLAUDE.md.fragment"


def test_mode_with_host_overlay_goes_to_host_fragment() -> None:
    rel = placement_relpath(_cls("work", host_overlay="laptop"), manifest=_manifest())
    assert rel == "profiles/work/hosts/laptop/CLAUDE.md.fragment"


def test_colon_path_mode_name_preserved() -> None:
    m = Manifest(
        version=1,
        profiles={
            _BASE: ProfileSpec(name="base", extends=None),
            "p_acme": ProfileSpec(name="work:acme", extends=_BASE),
        },
        hosts={},
    )
    assert placement_relpath(_cls("work:acme"), manifest=m) == "profiles/work:acme/CLAUDE.md.fragment"


# ---- format_placement_block ---------------------------------------------


def test_block_carries_provenance() -> None:
    block = format_placement_block(
        finding_text="Prefer terse responses.",
        content_hash="deadbeef",
        source_run="maury/run/r1",
    )
    assert "Prefer terse responses." in block
    assert "Content-Hash: deadbeef" in block
    assert "placed from maury/run/r1" in block


# ---- place_finding ------------------------------------------------------


def test_place_finding_appends_and_creates_dirs(tmp_path: Path) -> None:
    path = place_finding(
        tmp_path,
        relpath="profiles/work/CLAUDE.md.fragment",
        finding_text="Use ruff.",
        content_hash="h1",
        source_run="maury/run/r1",
    )
    assert path == tmp_path / "profiles/work/CLAUDE.md.fragment"
    assert path.is_file()
    assert "Use ruff." in path.read_text()


def test_place_finding_accumulates(tmp_path: Path) -> None:
    place_finding(tmp_path, relpath="CLAUDE.md", finding_text="one", content_hash="a", source_run="r")
    place_finding(tmp_path, relpath="CLAUDE.md", finding_text="two", content_hash="b", source_run="r")
    text = (tmp_path / "CLAUDE.md").read_text()
    assert "one" in text
    assert "two" in text
    assert text.count("maury: placed from") == 2
