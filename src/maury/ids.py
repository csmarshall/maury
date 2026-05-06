"""Stable opaque identifiers for maury entities.

Per ADR-0015: hosts and profiles use surrogate keys so renaming is safe.
IDs are prefixed with the entity type for self-describing audit logs and
error messages.
"""

from __future__ import annotations

import re
import uuid

HOST_PREFIX = "host_"
PROFILE_PREFIX = "profile_"

# Validation: prefix + 32 lowercase hex chars (uuid4().hex format).
_ID_RE = re.compile(r"^(host|profile)_[0-9a-f]{32}$")


def new_host_id() -> str:
    """Generate a new opaque host identifier."""
    return HOST_PREFIX + uuid.uuid4().hex


def new_profile_id() -> str:
    """Generate a new opaque profile identifier."""
    return PROFILE_PREFIX + uuid.uuid4().hex


def is_host_id(s: str) -> bool:
    """True if the string is a syntactically valid host identifier."""
    return s.startswith(HOST_PREFIX) and _ID_RE.match(s) is not None


def is_profile_id(s: str) -> bool:
    """True if the string is a syntactically valid profile identifier."""
    return s.startswith(PROFILE_PREFIX) and _ID_RE.match(s) is not None


def is_id(s: str) -> bool:
    """True if `s` looks like any maury entity ID (host or profile)."""
    return _ID_RE.match(s) is not None


def short(id_: str, *, n: int = 8) -> str:
    """Return a short, human-friendly form of an ID for display.

    Example: short("host_8a7f3c1d4e9b4a2c8f1e7d5b6c2a9e4f") == "host_8a7f3c1d"
    """
    if "_" not in id_:
        return id_[:n]
    prefix, rest = id_.split("_", 1)
    return f"{prefix}_{rest[:n]}"
