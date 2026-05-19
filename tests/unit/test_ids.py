"""Tests for `src/maury/ids.py` — surrogate ID grammar + tag normalization.

Host IDs are `host_<8 hex>_<tag>` per ADR-0015 (the pre-release
"untagged" `host_<32 hex>` form was retired 2026-05-19).
"""

from __future__ import annotations

import re

import pytest

from maury.ids import (
    TAG_MAX_LEN,
    host_id_hex_prefix,
    is_host_id,
    is_id,
    is_profile_id,
    new_host_id,
    normalize_tag,
    short,
    split_host_id,
)

# ---- new_host_id --------------------------------------------------------


def test_new_host_id_uses_8_hex_plus_tag() -> None:
    hid = new_host_id("laptop")
    assert re.match(r"^host_[0-9a-f]{8}_laptop$", hid), hid
    assert is_host_id(hid)


def test_new_host_id_rejects_unnormalized_input() -> None:
    """`new_host_id` requires the caller to normalize first.
    Catches bugs where un-normalized user input slips through."""
    with pytest.raises(ValueError, match="grammar"):
        new_host_id("UPPERCASE")
    with pytest.raises(ValueError, match="grammar"):
        new_host_id("has spaces")
    with pytest.raises(ValueError, match="grammar"):
        new_host_id("")


def test_new_host_id_with_long_tag_at_max_length() -> None:
    tag = "a" * TAG_MAX_LEN
    hid = new_host_id(tag)
    assert hid.endswith("_" + tag)


def test_new_host_id_rejects_tag_over_max_length() -> None:
    with pytest.raises(ValueError, match="grammar"):
        new_host_id("a" * (TAG_MAX_LEN + 1))


def test_new_host_id_unique_across_calls() -> None:
    """uuid4 → effectively zero collision probability."""
    assert new_host_id("x") != new_host_id("x")


# ---- normalize_tag ------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("toad", "toad"),
        ("XADAM___", "xadam---"),
        ("Charles-MBP.local", "charles-mbp-local"),
        ("with space", "with-space"),
        ("AT&T", "at-t"),
        ("UNDER_score", "under-score"),
        ("123-numeric", "123-numeric"),
        ("a" * 50, "a" * TAG_MAX_LEN),  # truncation
        ("MIXED_CASE-123", "mixed-case-123"),
        ("dots.everywhere.here", "dots-everywhere-here"),
    ],
)
def test_normalize_tag_examples(raw: str, expected: str) -> None:
    assert normalize_tag(raw) == expected


def test_normalize_tag_empty_input_raises() -> None:
    with pytest.raises(ValueError, match="empty"):
        normalize_tag("")


def test_normalize_tag_all_special_chars_collapses_to_hyphens() -> None:
    """All-special input is preserved as repeated hyphens (not collapsed),
    matching the ADR-0015 example `XADAM___` -> `xadam---`. Per-char
    replacement only; no run-collapse or leading/trailing strip."""
    assert normalize_tag("@#$%") == "----"


def test_normalize_tag_unicode_replaced() -> None:
    """Non-ASCII downcases via .lower() but the regex still strips it."""
    assert normalize_tag("café") == "caf-"
    assert normalize_tag("日本") == "--"


def test_normalize_tag_truncates_after_replacement() -> None:
    """Replacement happens before truncation; long output gets capped."""
    raw = "AAAA_BBBB_CCCC_DDDD_EEEE_FFFF_GGGG_HHHH_IIII"  # 44 chars after downcase
    result = normalize_tag(raw)
    assert len(result) == TAG_MAX_LEN
    assert result == "aaaa-bbbb-cccc-dddd-eeee-ffff-gg"


# ---- is_host_id ---------------------------------------------------------


def test_is_host_id_accepts_tagged_form() -> None:
    assert is_host_id("host_24b2a0aa_laptop")
    assert is_host_id("host_24b2a0aa_a")  # min 1-char tag
    assert is_host_id("host_24b2a0aa_" + "x" * TAG_MAX_LEN)  # max tag length


def test_is_host_id_rejects_legacy_32_hex_no_tag() -> None:
    """The pre-release `host_<32 hex>` form was retired 2026-05-19."""
    assert not is_host_id("host_" + "a" * 32)
    assert not is_host_id("host_24b2a0aadfd3459fa2a21ed7d0d79333")


def test_is_host_id_rejects_non_8_hex_lengths() -> None:
    assert not is_host_id("host_aaaa_laptop")  # 4 hex
    assert not is_host_id("host_" + "a" * 16 + "_laptop")  # 16 hex


def test_is_host_id_rejects_invalid_tag_chars() -> None:
    assert not is_host_id("host_24b2a0aa_HasUpperCase")
    assert not is_host_id("host_24b2a0aa_has_underscore")
    assert not is_host_id("host_24b2a0aa_has space")
    assert not is_host_id("host_24b2a0aa_")  # empty tag


def test_is_host_id_rejects_wrong_prefix() -> None:
    assert not is_host_id("profile_24b2a0aa_laptop")
    assert not is_host_id("agency_24b2a0aa_laptop")


# ---- is_profile_id / is_id ----------------------------------------------


def test_is_profile_id_only_accepts_32_hex() -> None:
    assert is_profile_id("profile_" + "a" * 32)
    # Profile IDs do NOT carry a tag suffix (no parallel amendment).
    assert not is_profile_id("profile_aaaaaaaa_home")


def test_is_id_accepts_both_kinds() -> None:
    assert is_id("host_24b2a0aa_laptop")
    assert is_id("profile_" + "a" * 32)
    assert not is_id("not-an-id")
    assert not is_id("host_" + "a" * 32)  # legacy 32-hex form retired


# ---- split_host_id / host_id_hex_prefix --------------------------------


def test_split_host_id_tagged() -> None:
    hex_part, tag = split_host_id("host_24b2a0aa_laptop")
    assert hex_part == "24b2a0aa"
    assert tag == "laptop"


def test_split_host_id_rejects_malformed() -> None:
    with pytest.raises(ValueError):
        split_host_id("not-a-host-id")
    with pytest.raises(ValueError):
        split_host_id("host_BADHEX_laptop")
    with pytest.raises(ValueError):
        split_host_id("host_" + "a" * 32)  # legacy 32-hex form rejected


def test_host_id_hex_prefix_for_baseline_compare() -> None:
    """ADR-0042's identity guard uses this to compare the immutable
    8-hex prefix, never the tag."""
    assert host_id_hex_prefix("host_24b2a0aa_anything") == "24b2a0aa"
    assert host_id_hex_prefix("host_24b2a0aa_other") == "24b2a0aa"


# ---- short --------------------------------------------------------------


def test_short_profile_truncates_to_n() -> None:
    assert short("profile_3f1a8b2c4d5e6f7081a2b3c4d5e6f708") == "profile_3f1a8b2c"


def test_short_tagged_host_returns_full() -> None:
    """Tagged IDs are already compact AND carry the readable tag —
    truncating would throw away the readability win."""
    assert short("host_24b2a0aa_laptop") == "host_24b2a0aa_laptop"


def test_short_custom_n_on_profile() -> None:
    assert short("profile_3f1a8b2c4d5e6f7081a2b3c4d5e6f708", n=4) == "profile_3f1a"
