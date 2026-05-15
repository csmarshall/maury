"""Stable opaque identifiers for maury entities.

Per ADR-0015 (amended 2026-05-14): hosts and profiles use surrogate
keys so renaming is safe. Host IDs additionally support an optional
cosmetic tag suffix (`host_<8 hex>_<tag>`) for human readability —
the 8-hex prefix is the lookup primitive, the tag is purely a label.
"""

from __future__ import annotations

import re
import uuid

HOST_PREFIX = "host_"
PROFILE_PREFIX = "profile_"

# Tag grammar: lowercase alphanumeric + hyphens, 1-32 chars.
# Matches DNS hostname rules per RFC 1123 §2.1.
TAG_MAX_LEN = 32
_TAG_VALID_RE = re.compile(r"^[a-z0-9-]{1,32}$")
_TAG_INVALID_CHAR_RE = re.compile(r"[^a-z0-9-]")

# Host IDs accept two forms:
#   - Legacy: prefix + 32 lowercase hex (pre-2026-05-14, still valid forever)
#   - Tagged: prefix + 8 lowercase hex + "_" + tag
# Profile IDs are 32-hex only (no tag form — modes aren't surfaced in the
# same human-facing places hosts are, so the readability payoff doesn't
# justify doubling the schema surface).
_HOST_ID_RE = re.compile(r"^host_(?:[0-9a-f]{32}|[0-9a-f]{8}_[a-z0-9-]{1,32})$")
_PROFILE_ID_RE = re.compile(r"^profile_[0-9a-f]{32}$")
_ANY_ID_RE = re.compile(r"^(?:host_(?:[0-9a-f]{32}|[0-9a-f]{8}_[a-z0-9-]{1,32})|profile_[0-9a-f]{32})$")


def new_host_id(tag: str | None = None) -> str:
    """Generate a new opaque host identifier.

    With no tag (the legacy / default path used by tests and curator-side
    bootstrap helpers): returns `host_<32 hex>`.

    With a tag: returns `host_<8 hex>_<tag>` per ADR-0015's 2026-05-14
    tagged-format amendment. The caller is responsible for normalizing
    the tag via `normalize_tag()` before passing it in.
    """
    if tag is None:
        return HOST_PREFIX + uuid.uuid4().hex
    if not _TAG_VALID_RE.match(tag):
        raise ValueError(f"tag {tag!r} does not match grammar [a-z0-9-]{{1,32}}. Run it through normalize_tag() first.")
    return HOST_PREFIX + uuid.uuid4().hex[:8] + "_" + tag


def new_profile_id() -> str:
    """Generate a new opaque profile identifier."""
    return PROFILE_PREFIX + uuid.uuid4().hex


def normalize_tag(raw: str) -> str:
    """Normalize a user-supplied tag to the grammar `[a-z0-9-]{1,32}`.

    Lossy pipeline per ADR-0015 §"Tag normalization":
      1. Downcase
      2. Replace any non-[a-z0-9-] character with `-`
      3. Truncate to 32 characters

    Raises ValueError if the result is empty (e.g., input was all
    whitespace or punctuation and the truncation+replace yielded
    nothing). Callers should prompt the user for a different value.

    Examples:
        normalize_tag("XADAM___") == "xadam---"
        normalize_tag("Charles-MBP.local") == "charles-mbp-local"
        normalize_tag("a" * 50) == "a" * 32
    """
    lowered = raw.lower()
    replaced = _TAG_INVALID_CHAR_RE.sub("-", lowered)
    truncated = replaced[:TAG_MAX_LEN]
    if not truncated:
        raise ValueError(f"tag {raw!r} normalized to empty string; provide alphanumeric content")
    return truncated


def split_host_id(host_id: str) -> tuple[str, str | None]:
    """Return `(hex_prefix, tag_or_none)` for a host ID.

    For legacy 32-hex IDs the prefix is the full 32 hex chars and the
    tag is None. For tagged IDs the prefix is 8 hex chars and the tag
    is the suffix string.

    Raises ValueError on malformed input.
    """
    if not is_host_id(host_id):
        raise ValueError(f"not a valid host id: {host_id!r}")
    body = host_id[len(HOST_PREFIX) :]
    if "_" in body:
        hex_part, tag = body.split("_", 1)
        return hex_part, tag
    return body, None


def host_id_hex_prefix(host_id: str) -> str:
    """Return the lookup-primitive hex prefix from a host ID.

    For ADR-0042's identity-baseline guard: the hex prefix is what
    gets compared, never the tag. For legacy 32-hex IDs the "prefix"
    is the full 32 chars; for tagged IDs it's the leading 8 chars.
    """
    return split_host_id(host_id)[0]


def is_host_id(s: str) -> bool:
    """True if `s` is a syntactically valid host identifier."""
    return _HOST_ID_RE.match(s) is not None


def is_profile_id(s: str) -> bool:
    """True if `s` is a syntactically valid profile identifier."""
    return _PROFILE_ID_RE.match(s) is not None


def is_id(s: str) -> bool:
    """True if `s` looks like any maury entity ID (host or profile)."""
    return _ANY_ID_RE.match(s) is not None


def short(id_: str, *, n: int = 8) -> str:
    """Return a short, human-friendly form of an ID for display.

    For legacy `host_<32 hex>` and `profile_<32 hex>`: truncates the
    post-prefix segment to `n` chars (default 8).

    For tagged `host_<8 hex>_<tag>`: returns the full ID unchanged —
    the tagged form is already compact, and the tag is the
    readability payoff we'd be throwing away by truncating.

    Examples:
        short("host_24b2a0aadfd3459fa2a21ed7d0d79333") == "host_24b2a0aa"
        short("host_24b2a0aa_laptop") == "host_24b2a0aa_laptop"
        short("profile_3f1a8b2c4d5e6f7081a2b3c4d5e6f708") == "profile_3f1a8b2c"
    """
    if "_" not in id_:
        return id_[:n]
    prefix, rest = id_.split("_", 1)
    if prefix == "host" and "_" in rest:
        # Tagged form — return unchanged.
        return id_
    return f"{prefix}_{rest[:n]}"
