"""maury rules — classification rule engine.

The ruleset is the learned artifact: a hand-readable YAML file at
`<base-repo>/.meta/rules.yaml`. Mining produces candidate fragments;
this engine assigns each one a profile (or routes it to manual review)
based on rules that the user has reviewed and approved over time.
"""

from .engine import classify_fragment, validate_rules
from .loader import RuleParseError, dump_rules, load_rules, parse_rules
from .schema import (
    Classification,
    Confidence,
    Rule,
    RuleActions,
    RuleConditions,
    RuleKind,
    TraceEntry,
)
from .trace import render_trace

__all__ = [
    "Classification",
    "Confidence",
    "Rule",
    "RuleActions",
    "RuleConditions",
    "RuleKind",
    "RuleParseError",
    "TraceEntry",
    "classify_fragment",
    "dump_rules",
    "load_rules",
    "parse_rules",
    "render_trace",
    "validate_rules",
]
