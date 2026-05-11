"""Tests for the rule engine.

Covers each rule shape (classify, forbid, scope) with positive and
negative inputs, plus priority resolution, symbolic forbid targets,
and the manual fallback.
"""

from __future__ import annotations

import pytest

from maury.rules import (
    Confidence,
    Rule,
    RuleActions,
    RuleConditions,
    RuleKind,
    classify_fragment,
    parse_rules,
    validate_rules,
)

# ---- helpers --------------------------------------------------------------


def _make_rule(
    rule_id: str,
    *,
    pattern: str | None = None,
    any_keyword: tuple[str, ...] = (),
    all_keywords: tuple[str, ...] = (),
    profile: str | None = None,
    host_overlay: str | None = None,
    forbid_profile: tuple[str, ...] = (),
    confidence: Confidence = Confidence.MEDIUM,
    priority: int = 0,
) -> Rule:
    return Rule(
        id=rule_id,
        when=RuleConditions(pattern=pattern, any_keyword=any_keyword, all_keywords=all_keywords),
        then=RuleActions(profile=profile, host_overlay=host_overlay, forbid_profile=forbid_profile),
        confidence=confidence,
        priority=priority,
    )


# ---- rule kind inference --------------------------------------------------


def test_rule_kind_classify() -> None:
    r = _make_rule("c", pattern=".", profile="home")
    assert r.kind == RuleKind.CLASSIFY


def test_rule_kind_forbid() -> None:
    r = _make_rule("f", pattern=".", forbid_profile=("base",))
    assert r.kind == RuleKind.FORBID


def test_rule_kind_scope() -> None:
    r = _make_rule("s", pattern=".", host_overlay="rosa")
    assert r.kind == RuleKind.SCOPE


def test_rule_kind_classify_with_overlay_is_classify() -> None:
    """If both profile and overlay are set, it's still classify."""
    r = _make_rule("c", pattern=".", profile="home", host_overlay="rosa")
    assert r.kind == RuleKind.CLASSIFY


# ---- classify rules -------------------------------------------------------


def test_classify_pattern_match_returns_profile() -> None:
    rules = [_make_rule("hostname-rosa", pattern=r"\brosa\b", profile="home")]
    result = classify_fragment("we deployed to rosa today", rules, {"home"})
    assert result.profile == "home"
    assert result.host_overlay is None


def test_classify_pattern_no_match_returns_manual() -> None:
    rules = [_make_rule("hostname-rosa", pattern=r"\brosa\b", profile="home")]
    result = classify_fragment("nothing relevant here", rules, {"home"})
    assert result.profile is None
    assert result.confidence == Confidence.LOW


def test_classify_any_keyword_match() -> None:
    rules = [
        _make_rule(
            "code-style",
            any_keyword=("ruff", "mypy", "type hint"),
            profile="base",
        )
    ]
    result = classify_fragment("we should add type hint coverage", rules, {"base"})
    assert result.profile == "base"


def test_classify_any_keyword_case_insensitive() -> None:
    rules = [
        _make_rule("code-style", any_keyword=("Ruff",), profile="base"),
    ]
    result = classify_fragment("ran RUFF check", rules, {"base"})
    assert result.profile == "base"


def test_classify_all_keywords_requires_all() -> None:
    rules = [
        _make_rule(
            "kamek-pf",
            all_keywords=("pf", "freebsd"),
            profile="home",
            host_overlay="kamek",
        ),
    ]
    # only one keyword present
    r1 = classify_fragment("freebsd is great", rules, {"home"})
    assert r1.profile is None
    # both present
    r2 = classify_fragment("freebsd pf rules", rules, {"home"})
    assert r2.profile == "home"
    assert r2.host_overlay == "kamek"


def test_classify_with_host_overlay() -> None:
    rules = [
        _make_rule("hostname-rosa", pattern=r"\brosa\b", profile="home", host_overlay="rosa"),
    ]
    result = classify_fragment("rosa deployed", rules, {"home"})
    assert result.profile == "home"
    assert result.host_overlay == "rosa"


# ---- priority -------------------------------------------------------------


def test_classify_higher_priority_wins() -> None:
    """When two classify rules match, the higher-priority one wins."""
    rules = [
        _make_rule("low", pattern=r"\bfrigate\b", profile="base", priority=1),
        _make_rule("high", pattern=r"\bfrigate\b", profile="home", priority=10),
    ]
    result = classify_fragment("set up frigate today", rules, {"home", "base"})
    assert result.profile == "home"


def test_classify_priority_tie_broken_by_id() -> None:
    """Equal priority — alphabetical id breaks the tie deterministically."""
    rules = [
        _make_rule("zzz", pattern=r"\bx\b", profile="work", priority=5),
        _make_rule("aaa", pattern=r"\bx\b", profile="home", priority=5),
    ]
    result = classify_fragment("x", rules, {"home", "work"})
    assert result.profile == "home"  # 'aaa' sorts before 'zzz'


# ---- forbid rules ---------------------------------------------------------


def test_forbid_blocks_classification() -> None:
    rules = [
        _make_rule("classify-base", pattern="x", profile="base"),
        _make_rule("forbid-base", pattern="x", forbid_profile=("base",)),
    ]
    result = classify_fragment("x", rules, {"base", "home", "work"})
    assert result.profile is None
    assert "forbid-base" in result.forbidden_by


def test_forbid_negation_target_only_allows_named_profile() -> None:
    """forbid_profile: ['!home'] means 'forbidden everywhere except home'."""
    rules = [
        _make_rule("classify-base", pattern="x", profile="base", priority=1),
        _make_rule("classify-home", pattern="x", profile="home", priority=1),
        _make_rule("forbid-non-home", pattern="x", forbid_profile=("!home",), priority=100),
    ]
    result = classify_fragment("x", rules, {"base", "home", "work"})
    # base is forbidden, home is allowed; tie broken alphabetically (classify-home wins)
    assert result.profile == "home"


def test_forbid_wildcard_blocks_everything() -> None:
    rules = [
        _make_rule("classify-base", pattern="x", profile="base"),
        _make_rule("forbid-all", pattern="x", forbid_profile=("*",)),
    ]
    result = classify_fragment("x", rules, {"base", "home"})
    assert result.profile is None


def test_forbid_does_not_fire_when_conditions_dont_match() -> None:
    rules = [
        _make_rule("classify-base", pattern="x", profile="base"),
        _make_rule("forbid-base", pattern="y", forbid_profile=("base",)),
    ]
    result = classify_fragment("x", rules, {"base"})
    assert result.profile == "base"


# ---- scope rules ----------------------------------------------------------


def test_scope_applies_overlay_when_classify_doesnt_specify() -> None:
    """A classify rule without host_overlay; a separate scope rule supplies one."""
    rules = [
        _make_rule("classify-home", pattern="x", profile="home"),
        _make_rule("scope-rosa", pattern="x", host_overlay="rosa"),
    ]
    result = classify_fragment("x", rules, {"home"})
    assert result.profile == "home"
    assert result.host_overlay == "rosa"


def test_scope_does_not_apply_when_classify_specifies_its_own() -> None:
    """If the classify rule already provides host_overlay, scope rules don't override."""
    rules = [
        _make_rule("classify-kamek", pattern="x", profile="home", host_overlay="kamek"),
        _make_rule("scope-rosa", pattern="x", host_overlay="rosa"),
    ]
    result = classify_fragment("x", rules, {"home"})
    assert result.host_overlay == "kamek"


# ---- empty / edge cases ---------------------------------------------------


def test_empty_ruleset_returns_manual() -> None:
    result = classify_fragment("any text", [], set())
    assert result.profile is None
    assert result.confidence == Confidence.LOW


def test_invalid_regex_treated_as_no_match() -> None:
    rules = [_make_rule("bad", pattern="(unclosed", profile="home")]
    result = classify_fragment("home stuff", rules, {"home"})
    assert result.profile is None


def test_empty_conditions_never_match() -> None:
    """A rule with no `when` content should never match."""
    rules = [Rule(id="empty", when=RuleConditions(), then=RuleActions(profile="home"))]
    result = classify_fragment("anything", rules, {"home"})
    assert result.profile is None


# ---- trace ----------------------------------------------------------------


def test_trace_records_hits_and_misses() -> None:
    rules = [
        _make_rule("hits", pattern=r"\bfoo\b", profile="home"),
        _make_rule("misses", pattern=r"\bbar\b", profile="work"),
    ]
    result = classify_fragment("foo only", rules, {"home", "work"})
    by_id = {t.rule_id: t for t in result.trace}
    assert by_id["hits"].matched is True
    assert by_id["misses"].matched is False


# ---- validation -----------------------------------------------------------


def test_validate_catches_duplicate_ids() -> None:
    rules = [
        _make_rule("dup", pattern="x", profile="home"),
        _make_rule("dup", pattern="y", profile="work"),
    ]
    errors = validate_rules(rules, {"home", "work"})
    assert any("duplicate" in e for e in errors)


def test_validate_catches_unknown_target_profile() -> None:
    rules = [_make_rule("classify", pattern="x", profile="ghost")]
    errors = validate_rules(rules, {"home", "work"})
    assert any("ghost" in e for e in errors)


def test_validate_skips_profile_check_when_no_profiles_known() -> None:
    """Without a manifest yet, we skip cross-profile validation."""
    rules = [_make_rule("classify", pattern="x", profile="ghost")]
    errors = validate_rules(rules, set())
    assert all("ghost" not in e for e in errors)


def test_validate_catches_empty_when() -> None:
    rules = [Rule(id="empty", when=RuleConditions(), then=RuleActions(profile="home"))]
    errors = validate_rules(rules, {"home"})
    assert any("no conditions" in e for e in errors)


def test_validate_catches_scope_without_overlay() -> None:
    rules = [Rule(id="bad-scope", when=RuleConditions(pattern="x"), then=RuleActions())]
    errors = validate_rules(rules, set())
    # This rule has no profile, no overlay, no forbid — kind defaults to CLASSIFY
    # because forbid_profile is empty. The validation message will be "missing target profile".
    assert any("missing target profile" in e for e in errors)


def test_validate_catches_forbid_unknown_literal_target() -> None:
    rules = [_make_rule("forbid", pattern="x", forbid_profile=("ghost",))]
    errors = validate_rules(rules, {"home", "work"})
    assert any("ghost" in e for e in errors)


def test_validate_allows_symbolic_forbid_targets() -> None:
    rules = [
        _make_rule("forbid-1", pattern="x", forbid_profile=("*",)),
        _make_rule("forbid-2", pattern="x", forbid_profile=("!home",)),
    ]
    errors = validate_rules(rules, {"home", "work"})
    assert not errors


# ---- end-to-end via parse_rules + seed-style YAML -------------------------


SEED_YAML = """
version: 1
rules:
  - id: hostname-rosa
    when:
      pattern: '\\brosa\\b'
    then:
      profile: home
      host_overlay: rosa
    confidence: high
    priority: 10

  - id: redact-home-net
    when:
      pattern: '\\b192\\.168\\.1\\.\\d{1,3}\\b'
    then:
      forbid_profile: ['!home']
    confidence: high
    priority: 100
"""


def test_seed_yaml_round_trip() -> None:
    rules = parse_rules(SEED_YAML)
    assert len(rules) == 2
    ids = {r.id for r in rules}
    assert ids == {"hostname-rosa", "redact-home-net"}


def test_seed_classify_via_parsed_rules() -> None:
    rules = parse_rules(SEED_YAML)
    r = classify_fragment("ssh charles@rosa", rules, {"home"})
    assert r.profile == "home"
    assert r.host_overlay == "rosa"


def test_seed_redact_blocks_non_home() -> None:
    rules = parse_rules(SEED_YAML)
    # No classify rule fires for plain IP, but the forbid still records its action
    # via the trace; classification is manual either way.
    r = classify_fragment("192.168.1.5", rules, {"home", "base", "work"})
    assert r.profile is None  # no classify rule fired
    # add a classify rule that targets base; verify forbid blocks it
    extra = [
        _make_rule("classify-base", pattern="192", profile="base"),
        *rules,
    ]
    r2 = classify_fragment("192.168.1.5", extra, {"home", "base", "work"})
    assert r2.profile is None
    assert "redact-home-net" in r2.forbidden_by


# ---- loader error cases ---------------------------------------------------


def test_loader_rejects_unknown_top_level_key() -> None:
    bad = """
version: 1
rules:
  - id: r1
    when:
      pattern: 'x'
    then:
      profile: home
    nope: oops
"""
    with pytest.raises(Exception) as ei:
        parse_rules(bad)
    assert "unknown top-level keys" in str(ei.value)


def test_loader_rejects_missing_id() -> None:
    bad = """
version: 1
rules:
  - when:
      pattern: 'x'
    then:
      profile: home
"""
    with pytest.raises(Exception) as ei:
        parse_rules(bad)
    assert "missing 'id'" in str(ei.value)


def test_loader_rejects_invalid_confidence() -> None:
    bad = """
version: 1
rules:
  - id: r1
    when:
      pattern: 'x'
    then:
      profile: home
    confidence: turbo
"""
    with pytest.raises(Exception) as ei:
        parse_rules(bad)
    assert "invalid confidence" in str(ei.value)


def test_loader_handles_empty_file() -> None:
    assert parse_rules("") == []
    assert parse_rules("rules: []") == []
