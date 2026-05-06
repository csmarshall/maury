"""settings.json layer merging.

Per ADR-0019, settings.json (and the per-layer `settings.fragment.json`)
deep-merge with the following rules:

- **Object (dict) values**: merge by key — keys present in the child
  override the parent's value; keys not in the child come from the
  parent. Recurse into nested dicts.
- **Scalar values** (string, number, bool, null): later layer's value
  wins outright.
- **Array (list) values**: append with deduplication. Item identity is
  by deep-equality. Order within each layer is preserved; child items
  appear after parent items.

This matches Claude Code's actual settings.json semantics for things
like the `permissions` array (we don't want a child profile to *replace*
the base permissions list, we want it to *add* to it) while still
letting scalar config like `theme` override cleanly.

There is one explicit override mechanism for arrays: a child can write
`{"$replace": [...]}` to indicate "replace the parent's value entirely
with this list." This escape hatch is for the rare case where add-and-
dedupe is wrong. Documented in `docs/adr/0019-...`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def deep_merge(parent: Any, child: Any) -> Any:
    """Merge child into parent per ADR-0019 rules.

    Pure function; neither argument is mutated. Returns the merged value.
    """
    # Both objects: merge keys.
    if isinstance(parent, dict) and isinstance(child, dict):
        out: dict[str, Any] = dict(parent)
        for key, child_val in child.items():
            if key in out:
                out[key] = deep_merge(out[key], child_val)
            else:
                out[key] = child_val
        return out

    # Both arrays: append + dedupe (preserves order from parent then child).
    if isinstance(parent, list) and isinstance(child, list):
        # Honor explicit replace marker
        if len(child) == 1 and isinstance(child[0], dict) and "$replace" in child[0]:
            replacement = child[0]["$replace"]
            if not isinstance(replacement, list):
                raise SettingsMergeError("`$replace` must wrap a list")
            return list(replacement)

        out_list: list[Any] = list(parent)
        for item in child:
            if not _contains(out_list, item):
                out_list.append(item)
        return out_list

    # Type mismatch or scalars: child wins.
    return child


def _contains(haystack: list[Any], needle: Any) -> bool:
    """Deep-equality membership test for the dedupe pass."""
    return any(_deep_equal(item, needle) for item in haystack)


def _deep_equal(a: Any, b: Any) -> bool:
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        if a.keys() != b.keys():
            return False
        return all(_deep_equal(a[k], b[k]) for k in a)
    if isinstance(a, list):
        if len(a) != len(b):
            return False
        return all(_deep_equal(x, y) for x, y in zip(a, b, strict=False))
    return bool(a == b)


def merge_layers(layer_contents: list[tuple[str, str]]) -> dict[str, Any]:
    """Merge a sequence of `(layer_name, json_text)` tuples.

    First tuple is the root (base); each subsequent tuple is layered on
    top in order. Empty inputs → empty dict.

    Raises SettingsMergeError if any layer's JSON fails to parse or if
    a `$replace` marker is malformed.
    """
    if not layer_contents:
        return {}
    accumulator: Any = {}
    for layer_name, text in layer_contents:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as e:
            raise SettingsMergeError(f"layer {layer_name!r}: JSON parse error: {e}") from e
        accumulator = deep_merge(accumulator, parsed)
    if not isinstance(accumulator, dict):
        raise SettingsMergeError(
            f"merged settings.json must be an object at top level; got {type(accumulator).__name__}"
        )
    return accumulator


def collect_settings_layers(
    layer_paths: list[tuple[str, Path]],
) -> list[tuple[str, str]]:
    """For each (layer_name, dir_path), find settings.json / settings.fragment.json.

    Returns `(layer_name, file_text)` tuples in the order received,
    skipping layers that don't have a settings file.
    """
    out: list[tuple[str, str]] = []
    for layer_name, dir_path in layer_paths:
        candidates = [
            dir_path / "settings.json",
            dir_path / "settings.fragment.json",
        ]
        for candidate in candidates:
            if candidate.is_file():
                out.append((layer_name, candidate.read_text(encoding="utf-8")))
                break
    return out


class SettingsMergeError(ValueError):
    """Raised when a settings.json layer is malformed or merge fails."""


__all__ = [
    "SettingsMergeError",
    "collect_settings_layers",
    "deep_merge",
    "merge_layers",
]
