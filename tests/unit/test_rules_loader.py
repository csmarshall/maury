"""Tests for `src/maury/rules/loader.py` — YAML rule file loading,
parsing, validation, and round-trip serialization.

Existing `test_rules_engine.py` exercises the classifier end-to-end
including some `parse_rules` paths. These tests focus specifically on
the loader's error paths, edge cases, and `dump_rules` round-trip.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from maury.rules.loader import (
    RuleParseError,
    dump_rules,
    load_rules,
    parse_rules,
)
from maury.rules.schema import Confidence, Rule, RuleActions, RuleConditions

# ---- load_rules (file path entry point) --------------------------------


def test_load_rules_reads_file(tmp_path: Path) -> None:
    """`load_rules` reads from disk and delegates to `parse_rules`."""
    path = tmp_path / "rules.yaml"
    path.write_text(
        "version: 1\n"
        "rules:\n"
        "  - id: example\n"
        "    when:\n"
        "      pattern: 'foo'\n"
        "    then:\n"
        "      profile: home\n"
    )
    rules = load_rules(path)
    assert len(rules) == 1
    assert rules[0].id == "example"
    assert rules[0].when.pattern == "foo"
    assert rules[0].then.profile == "home"


def test_load_rules_accepts_pathlib_or_str(tmp_path: Path) -> None:
    path = tmp_path / "rules.yaml"
    path.write_text("version: 1\nrules: []\n")
    # Same result whether path is Path or str.
    via_path = load_rules(path)
    via_str = load_rules(str(path))
    assert via_path == via_str == []


# ---- parse_rules top-level structural validation -----------------------


def test_parse_rules_empty_string_returns_empty_list() -> None:
    """A blank YAML doc parses to None, which the loader returns as []."""
    assert parse_rules("") == []


def test_parse_rules_yaml_only_comments_returns_empty_list() -> None:
    assert parse_rules("# just a comment\n") == []


def test_parse_rules_top_level_must_be_mapping() -> None:
    with pytest.raises(RuleParseError, match="top-level must be a mapping"):
        parse_rules("[1, 2, 3]\n")


def test_parse_rules_rules_must_be_list() -> None:
    with pytest.raises(RuleParseError, match="'rules' must be a list"):
        parse_rules("version: 1\nrules: not-a-list\n")


def test_parse_rules_rule_must_be_mapping() -> None:
    with pytest.raises(RuleParseError, match=r"rule #0 is not a mapping"):
        parse_rules("version: 1\nrules:\n  - just-a-string\n")


def test_parse_rules_malformed_yaml_raises_clean_error() -> None:
    """YAML library errors are wrapped in RuleParseError with a clear source."""
    with pytest.raises(RuleParseError, match="YAML parse error"):
        parse_rules("version: 1\nrules:\n  - id: [unbalanced\n", source="test.yaml")


def test_parse_rules_omits_rules_key_returns_empty() -> None:
    """If `rules` is absent, no rules are loaded."""
    assert parse_rules("version: 1\n") == []


# ---- per-rule validation -----------------------------------------------


def test_parse_rules_unknown_top_level_key_raises() -> None:
    with pytest.raises(RuleParseError, match="unknown top-level keys"):
        parse_rules(
            "version: 1\n"
            "rules:\n"
            "  - id: r1\n"
            "    when: {pattern: foo}\n"
            "    then: {profile: home}\n"
            "    bogus_field: x\n"
        )


def test_parse_rules_missing_id_raises() -> None:
    with pytest.raises(RuleParseError, match="missing 'id'"):
        parse_rules(
            "version: 1\n"
            "rules:\n"
            "  - when: {pattern: foo}\n"
            "    then: {profile: home}\n"
        )


def test_parse_rules_missing_when_raises() -> None:
    with pytest.raises(RuleParseError, match="missing 'when'"):
        parse_rules(
            "version: 1\n"
            "rules:\n"
            "  - id: r1\n"
            "    then: {profile: home}\n"
        )


def test_parse_rules_missing_then_raises() -> None:
    with pytest.raises(RuleParseError, match="missing 'then'"):
        parse_rules(
            "version: 1\n"
            "rules:\n"
            "  - id: r1\n"
            "    when: {pattern: foo}\n"
        )


def test_parse_rules_invalid_confidence_raises() -> None:
    with pytest.raises(RuleParseError, match="invalid confidence"):
        parse_rules(
            "version: 1\n"
            "rules:\n"
            "  - id: r1\n"
            "    when: {pattern: foo}\n"
            "    then: {profile: home}\n"
            "    confidence: maximum\n"
        )


def test_parse_rules_default_confidence_is_medium() -> None:
    rules = parse_rules(
        "version: 1\n"
        "rules:\n"
        "  - id: r1\n"
        "    when: {pattern: foo}\n"
        "    then: {profile: home}\n"
    )
    assert rules[0].confidence == Confidence.MEDIUM


def test_parse_rules_priority_must_be_int() -> None:
    with pytest.raises(RuleParseError, match="priority must be an integer"):
        parse_rules(
            "version: 1\n"
            "rules:\n"
            "  - id: r1\n"
            "    when: {pattern: foo}\n"
            "    then: {profile: home}\n"
            "    priority: high\n"
        )


def test_parse_rules_priority_default_is_zero() -> None:
    rules = parse_rules(
        "version: 1\n"
        "rules:\n"
        "  - id: r1\n"
        "    when: {pattern: foo}\n"
        "    then: {profile: home}\n"
    )
    assert rules[0].priority == 0


# ---- `when` block validation -------------------------------------------


def test_parse_when_must_be_mapping() -> None:
    with pytest.raises(RuleParseError, match="'when' must be a mapping"):
        parse_rules(
            "version: 1\n"
            "rules:\n"
            "  - id: r1\n"
            "    when: 'not-a-mapping'\n"
            "    then: {profile: home}\n"
        )


def test_parse_when_unknown_keys_raise() -> None:
    with pytest.raises(RuleParseError, match=r"unknown 'when' keys"):
        parse_rules(
            "version: 1\n"
            "rules:\n"
            "  - id: r1\n"
            "    when: {pattern: foo, bogus_condition: x}\n"
            "    then: {profile: home}\n"
        )


def test_parse_when_accepts_any_keyword_and_all_keywords() -> None:
    rules = parse_rules(
        "version: 1\n"
        "rules:\n"
        "  - id: r1\n"
        "    when:\n"
        "      any_keyword: [foo, bar]\n"
        "      all_keywords: [baz, qux]\n"
        "    then: {profile: home}\n"
    )
    assert rules[0].when.any_keyword == ("foo", "bar")
    assert rules[0].when.all_keywords == ("baz", "qux")
    assert rules[0].when.pattern is None


# ---- `then` block validation -------------------------------------------


def test_parse_then_must_be_mapping() -> None:
    with pytest.raises(RuleParseError, match="'then' must be a mapping"):
        parse_rules(
            "version: 1\n"
            "rules:\n"
            "  - id: r1\n"
            "    when: {pattern: foo}\n"
            "    then: 'not-a-mapping'\n"
        )


def test_parse_then_unknown_keys_raise() -> None:
    with pytest.raises(RuleParseError, match=r"unknown 'then' keys"):
        parse_rules(
            "version: 1\n"
            "rules:\n"
            "  - id: r1\n"
            "    when: {pattern: foo}\n"
            "    then: {profile: home, bogus_action: x}\n"
        )


def test_parse_then_accepts_forbid_profile() -> None:
    rules = parse_rules(
        "version: 1\n"
        "rules:\n"
        "  - id: r1\n"
        "    when: {pattern: foo}\n"
        "    then:\n"
        "      forbid_profile: [work, '!home']\n"
    )
    assert rules[0].then.forbid_profile == ("work", "!home")


# ---- dump_rules round-trip ---------------------------------------------


def _sample_rule() -> Rule:
    return Rule(
        id="example",
        when=RuleConditions(pattern="foo", any_keyword=("bar",)),
        then=RuleActions(profile="home", host_overlay="server"),
        confidence=Confidence.HIGH,
        priority=10,
        added="2026-05-13",
        reason="example for round-trip test",
    )


def test_dump_rules_produces_parseable_output() -> None:
    """Round-trip: parse → dump → parse should yield equivalent rules."""
    original = [_sample_rule()]
    yaml_text = dump_rules(original)
    reparsed = parse_rules(yaml_text)
    assert reparsed == original


def test_dump_rules_includes_version_field() -> None:
    yaml_text = dump_rules([_sample_rule()])
    assert "version: 1" in yaml_text or yaml_text.startswith("version:")


def test_dump_rules_omits_medium_confidence() -> None:
    """`Confidence.MEDIUM` is the default; dump should NOT serialize it."""
    rule = Rule(
        id="r1",
        when=RuleConditions(pattern="foo"),
        then=RuleActions(profile="home"),
        confidence=Confidence.MEDIUM,
        priority=0,
    )
    yaml_text = dump_rules([rule])
    assert "confidence" not in yaml_text


def test_dump_rules_omits_zero_priority() -> None:
    rule = Rule(
        id="r1",
        when=RuleConditions(pattern="foo"),
        then=RuleActions(profile="home"),
        confidence=Confidence.MEDIUM,
        priority=0,
    )
    yaml_text = dump_rules([rule])
    assert "priority" not in yaml_text


def test_dump_rules_omits_unset_optional_when_fields() -> None:
    """When pattern is None and keyword tuples are empty, those keys
    should be absent from the dumped `when:` block."""
    rule = Rule(
        id="r1",
        when=RuleConditions(any_keyword=("foo",)),  # only any_keyword set
        then=RuleActions(profile="home"),
        confidence=Confidence.MEDIUM,
        priority=0,
    )
    yaml_text = dump_rules([rule])
    # pattern not set → not in dump
    assert "pattern" not in yaml_text
    # any_keyword IS set → in dump
    assert "any_keyword" in yaml_text


def test_dump_rules_omits_unset_optional_then_fields() -> None:
    rule = Rule(
        id="r1",
        when=RuleConditions(pattern="foo"),
        then=RuleActions(profile="home"),  # no host_overlay, no forbid_profile
        confidence=Confidence.MEDIUM,
        priority=0,
    )
    yaml_text = dump_rules([rule])
    assert "host_overlay" not in yaml_text
    assert "forbid_profile" not in yaml_text


def test_dump_rules_includes_added_and_reason_when_present() -> None:
    rule = Rule(
        id="r1",
        when=RuleConditions(pattern="foo"),
        then=RuleActions(profile="home"),
        confidence=Confidence.MEDIUM,
        priority=0,
        added="2026-05-13",
        reason="test reason",
    )
    yaml_text = dump_rules([rule])
    assert "added" in yaml_text
    assert "2026-05-13" in yaml_text
    assert "reason" in yaml_text
    assert "test reason" in yaml_text


def test_dump_rules_empty_list_produces_empty_rules_block() -> None:
    yaml_text = dump_rules([])
    # Should still be valid YAML with version + empty rules.
    reparsed = parse_rules(yaml_text)
    assert reparsed == []


def test_round_trip_preserves_forbid_profile() -> None:
    """Sanity: a forbid-shape rule round-trips correctly."""
    rule = Rule(
        id="redact",
        when=RuleConditions(pattern=r"\b192\.168\.1\.\d+\b"),
        then=RuleActions(forbid_profile=("!home",)),
        confidence=Confidence.HIGH,
        priority=100,
    )
    yaml_text = dump_rules([rule])
    reparsed = parse_rules(yaml_text)
    assert reparsed == [rule]


def test_round_trip_preserves_scope_only_rule() -> None:
    """Sanity: a scope-shape rule (only host_overlay set) round-trips."""
    rule = Rule(
        id="scope-server",
        when=RuleConditions(pattern="server"),
        then=RuleActions(host_overlay="server"),  # no profile
        confidence=Confidence.MEDIUM,
        priority=5,
    )
    yaml_text = dump_rules([rule])
    reparsed = parse_rules(yaml_text)
    assert reparsed == [rule]
