"""Render orchestrator.

Walks the layer chain (base + profile inheritance + host overlay) and
produces an in-memory representation of `~/.claude/` for one host. The
caller decides whether to apply (write) or just check (dry-run).

Per ADR-0019, the merge semantics are content-type-specific:

  CLAUDE.md (and fragments)  : concatenate with provenance markers
  settings.json              : deep merge by key (Phase 3.5 — not yet)
  agents/*.md                : file overlay (later wins, with warning)
  skills/<name>/SKILL.md     : file overlay (later wins, with warning)
  bin/*                      : file overlay (later wins)
  keybindings.json           : deep merge (Phase 3.5 — not yet)
  hooks.yaml                 : action-resolver (Phase 3.6 — not yet)

This file provides the v1 minimum: CLAUDE.md concatenation + file
overlay for agents/skills/bin. Other content types are stubbed.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from maury.manifest import Manifest

from .settings_merge import (
    SettingsMergeError,
    collect_settings_layers,
    merge_layers,
)

# ---- result types ---------------------------------------------------------


@dataclass(frozen=True)
class LayerSource:
    """One source layer contributing content during render."""

    name: str  # "base" / "profile:home" / "host-overlay:workstation"
    repo_root: Path  # absolute path to the repo this layer's content lives in
    relative_root: str  # subpath within the repo, e.g., "" or "profiles/home" or "profiles/home/hosts/workstation"


@dataclass(frozen=True)
class RenderedFile:
    """One file the render engine wants to write to the target tree."""

    target_path: str  # path relative to target dir, e.g., "CLAUDE.md" or "skills/x/SKILL.md"
    content: bytes
    source_layer: str  # which layer produced this file (last writer for overlay; "merged" for concatenated)
    mode: int = 0o644

    @property
    def sha256(self) -> str:
        """Lowercase hex sha256 of the content."""
        return hashlib.sha256(self.content).hexdigest()


@dataclass
class RenderResult:
    """Output of one render operation."""

    files: list[RenderedFile] = field(default_factory=list)
    layers: list[LayerSource] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def by_path(self) -> dict[str, RenderedFile]:
        """Index the rendered files by target path."""
        return {f.target_path: f for f in self.files}


# ---- main entry ----------------------------------------------------------


def render(
    *,
    repo_paths: dict[str, Path],
    manifest: Manifest,
    profile_id: str,
    host_id: str,
    profile_repo: str = "base",
) -> RenderResult:
    """Render one host's view of `~/.claude/`.

    Args:
        repo_paths: Mapping from manifest repo nicknames (e.g., "base",
            "personal") to the local clone path.
        manifest: The loaded manifest.
        profile_id: Surrogate ID of the profile to render for.
        host_id: Surrogate ID of the host to render for.
        profile_repo: Which repo nickname holds the profiles/ tree.
            For v1 single-repo case, the same repo holds both base
            content and all profiles. Defaults to "base".

    Returns:
        RenderResult containing the in-memory file tree plus warnings.
    """
    if profile_id not in manifest.profiles:
        raise RenderError(f"profile {profile_id!r} not in manifest")
    if host_id not in manifest.hosts:
        raise RenderError(f"host {host_id!r} not in manifest")

    host_spec = manifest.hosts[host_id]
    if host_spec.profile != profile_id:
        raise RenderError(
            f"host {host_spec.name!r} ({host_id}) is bound to profile {host_spec.profile!r}, not {profile_id!r}"
        )

    if profile_repo not in repo_paths:
        raise RenderError(f"profile_repo {profile_repo!r} not in repo_paths {sorted(repo_paths)}")

    layers = _build_layer_chain(
        repo_paths=repo_paths,
        manifest=manifest,
        profile_id=profile_id,
        host_id=host_id,
        profile_repo=profile_repo,
    )

    result = RenderResult(layers=layers)

    # CLAUDE.md: concatenate from base + each profile in chain + host overlay.
    _render_claude_md(layers, result)

    # settings.json: deep-merge per ADR-0019.
    _render_settings_json(layers, result)

    # File-overlay content (later wins, with warning when child shadows parent):
    for subdir in ("agents", "skills", "bin"):
        _render_file_overlay(layers, result, subdir=subdir)

    return result


# ---- layer chain --------------------------------------------------------


def _build_layer_chain(
    *,
    repo_paths: dict[str, Path],
    manifest: Manifest,
    profile_id: str,
    host_id: str,
    profile_repo: str,
) -> list[LayerSource]:
    """Construct the ordered list of layers that contribute to this render.

    Order (root to leaf):
      1. base (the repo's top-level CLAUDE.md, settings.json, etc.)
      2. each profile in inheritance chain (root profile first)
      3. host overlay
    """
    repo_root = repo_paths[profile_repo]
    layers: list[LayerSource] = [
        LayerSource(name="base", repo_root=repo_root, relative_root=""),
    ]

    chain = manifest.inheritance_chain(profile_id)
    for pid in chain:
        profile_name = manifest.profiles[pid].name
        layers.append(
            LayerSource(
                name=f"profile:{profile_name}",
                repo_root=repo_root,
                relative_root=f"profiles/{profile_name}",
            )
        )

    leaf_profile_name = manifest.profiles[profile_id].name
    host_name = manifest.hosts[host_id].name
    layers.append(
        LayerSource(
            name=f"host-overlay:{host_name}",
            repo_root=repo_root,
            relative_root=f"profiles/{leaf_profile_name}/hosts/{host_name}",
        )
    )

    return layers


# ---- CLAUDE.md concatenation --------------------------------------------


_PROVENANCE_HEADER = """\
<!-- maury rendered file. Do not hand-edit through this path; use
     `maury reconcile` to capture changes via the proposal pipeline.
     Layers (root to leaf):
{layer_lines}
-->
"""


def _render_claude_md(layers: list[LayerSource], result: RenderResult) -> None:
    """Concatenate CLAUDE.md content across layers with a provenance header."""
    sections: list[tuple[str, bytes]] = []  # (layer_name, content)
    for layer in layers:
        candidates = [
            layer.repo_root / layer.relative_root / "CLAUDE.md",
            layer.repo_root / layer.relative_root / "CLAUDE.md.fragment",
        ]
        for candidate in candidates:
            if candidate.is_file():
                content = candidate.read_bytes()
                sections.append((layer.name, content))
                break

    if not sections:
        return  # no CLAUDE.md content anywhere — no file to write

    # Provenance header lists each contributing layer + sha of its content
    layer_lines = "\n".join(
        f"       {name:<30} sha {hashlib.sha256(content).hexdigest()[:8]}" for name, content in sections
    )
    header = _PROVENANCE_HEADER.format(layer_lines=layer_lines).encode()

    body_parts: list[bytes] = []
    for layer_name, content in sections:
        marker = f"\n<!-- maury: from {layer_name} -->\n".encode()
        body_parts.append(marker + content.rstrip(b"\n") + b"\n")

    final_content = header + b"".join(body_parts)
    result.files.append(
        RenderedFile(
            target_path="CLAUDE.md",
            content=final_content,
            source_layer="merged",
        )
    )


# ---- settings.json deep-merge -------------------------------------------


def _render_settings_json(layers: list[LayerSource], result: RenderResult) -> None:
    """Deep-merge settings.json across layers and emit the result."""
    layer_paths = [(layer.name, layer.repo_root / layer.relative_root) for layer in layers]
    contents = collect_settings_layers(layer_paths)
    if not contents:
        return
    try:
        merged = merge_layers(contents)
    except SettingsMergeError as e:
        result.errors.append(f"settings.json: {e}")
        return
    payload = json.dumps(merged, indent=2).encode() + b"\n"
    result.files.append(
        RenderedFile(
            target_path="settings.json",
            content=payload,
            source_layer="merged",
        )
    )


# ---- file overlay (agents/, skills/, bin/) ------------------------------


def _render_file_overlay(
    layers: list[LayerSource],
    result: RenderResult,
    *,
    subdir: str,
) -> None:
    """Walk a per-layer subdirectory; later layers override earlier files.

    A warning is emitted when a child layer replaces a file that an
    earlier layer also provided (per ADR-0019: file-overlay defaults to
    replace, but the user should know when shadowing happens so they
    can opt into `extends-policy: extend` if desired).
    """
    seen: dict[str, str] = {}  # rel_path -> layer_name that contributed it (last writer)
    contents: dict[str, RenderedFile] = {}

    for layer in layers:
        layer_subdir = layer.repo_root / layer.relative_root / subdir
        if not layer_subdir.is_dir():
            continue
        for src in sorted(layer_subdir.rglob("*")):
            if not src.is_file():
                continue
            rel = src.relative_to(layer_subdir).as_posix()
            target_rel = f"{subdir}/{rel}"
            if target_rel in seen:
                result.warnings.append(
                    f"{target_rel}: replaced by {layer.name} (was from {seen[target_rel]}); "
                    f"add `extends-policy: extend` to the child to merge instead"
                )
            seen[target_rel] = layer.name
            contents[target_rel] = RenderedFile(
                target_path=target_rel,
                content=src.read_bytes(),
                source_layer=layer.name,
                mode=0o755 if subdir == "bin" else 0o644,
            )

    # Append in deterministic order
    for path in sorted(contents):
        result.files.append(contents[path])


# ---- apply -------------------------------------------------------------


def apply_render(
    result: RenderResult,
    target_dir: Path,
    *,
    dry_run: bool = False,
) -> list[str]:
    """Write the rendered files to the target directory.

    Returns a list of human-readable status lines describing what was
    (or would be) done. Does NOT delete files in the target dir that
    aren't in the render result — that's a separate orphan-cleanup
    operation handled by the sync workflow with user confirmation.
    """
    actions: list[str] = []
    for f in result.files:
        target = target_dir / f.target_path
        existing = target.read_bytes() if target.is_file() else None
        if existing == f.content:
            actions.append(f"unchanged    {f.target_path}")
            continue
        verb = "would write" if dry_run else "wrote"
        if existing is None:
            actions.append(f"{verb:<12} {f.target_path}  (new, {len(f.content)} bytes)")
        else:
            actions.append(f"{verb:<12} {f.target_path}  (modified, {len(existing)} -> {len(f.content)} bytes)")
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(f.content)
            target.chmod(f.mode)
    return actions


# ---- error type --------------------------------------------------------


class RenderError(ValueError):
    """Raised when render inputs are invalid."""


__all__ = [
    "LayerSource",
    "RenderError",
    "RenderResult",
    "RenderedFile",
    "apply_render",
    "render",
]
