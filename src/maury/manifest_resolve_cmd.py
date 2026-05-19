"""CLI driver for `maury manifest resolve` (per ADR-0024).

Reads the three-way state from git (`:1:` / `:2:` / `:3:` for ancestor /
ours / theirs), runs the structured-merge engine in `manifest_merge.py`,
walks any unresolved conflicts with the user, validates the final
manifest, and writes it atomically.

Also exposes `try_auto_merge_manifest()` — a non-interactive entry
point used by `maury sync` (per ADR-0024 §"The flow") when a base-repo
pull surfaces a manifest conflict. Returns one of
`AutoMergeOutcome.AUTO_MERGED` (engine resolved everything; manifest
written atomically; caller does `git add` + `git commit`),
`AutoMergeOutcome.NEEDS_USER_RESOLVE` (real conflicts remain; caller
surfaces a pointer at `maury manifest resolve`), or
`AutoMergeOutcome.NOT_IN_CONFLICT` (the file isn't in merge state).
"""

from __future__ import annotations

import enum
import json
import os
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Final

from maury.manifest_merge import (
    DELETE_SENTINEL,
    Conflict,
    ConflictKind,
    MergeResult,
    PathT,
    apply_resolutions,
    compute_merge,
)


class ResolveError(RuntimeError):
    """Raised when the resolve command cannot proceed."""


@dataclass(frozen=True)
class SideMetadata:
    """Commit metadata for one side of a 3-way merge.

    Captured from `git log -1` on HEAD (side A / 'ours') and MERGE_HEAD
    (side B / 'theirs') so the user sees who made each change and when
    while resolving conflicts. Per ADR-0024 §"On clock skew", we surface
    both timestamps verbatim and let the user judge any inversions.
    """

    short_sha: str
    author: str
    """Author identity (typically "Name <email>")."""

    committer_date: str
    """ISO 8601 timestamp from the committer (used for "later wins" judgment)."""

    branch: str | None
    """Branch label if resolvable (`HEAD` -> current branch via
    `git rev-parse --abbrev-ref`; MERGE_HEAD -> the branch name git
    recorded in `.git/MERGE_MSG`). May be None if neither is available."""


@dataclass(frozen=True)
class ResolveSummary:
    """Outcome of a resolve run."""

    manifest_path: Path
    auto_merged_paths: int
    """Number of paths the engine auto-resolved (additive case + clean diffs)."""

    user_resolved_paths: int
    """Number of conflicts the user picked through."""

    aborted: bool = False
    """True iff the user chose to abort during a prompt."""

    chosen_sides: tuple[str, ...] = field(default_factory=tuple)
    """Per-conflict side selected (for the final commit-message audit trail)."""

    side_a_meta: SideMetadata | None = None
    """Metadata captured for side A (`HEAD`); None if lookup failed."""

    side_b_meta: SideMetadata | None = None
    """Metadata captured for side B (`MERGE_HEAD`); None if lookup failed."""


Prompter = Callable[[Conflict], str]
"""Injectable prompter for resolve's interactive UI.

Returns one of: 'a' (take side A), 'b' (take side B), 's' (skip
this conflict — leave ancestor in place), 'e' (edit by hand —
spawn $EDITOR with a template), 'q' (abort the whole run).
"""


EditorInvoker = Callable[[Path], int]
"""Editor adapter for edit-by-hand resolution.

Takes a path to a JSON template, mutates it in place via $EDITOR
(or whatever the implementation chooses), and returns the editor's
exit code. Tests inject a fake invoker that writes a known value.
"""


# ---- git plumbing ---------------------------------------------------------


def _run_git(cmd: list[str], *, cwd: Path) -> tuple[int, str]:
    """Run a git command in cwd; return (rc, combined output)."""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30.0,
            check=False,
            cwd=str(cwd),
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return -1, str(e)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _read_git_blob(repo_root: Path, ref: str) -> str | None:
    """Read a single blob via `git show <ref>` (returns stdout, not stderr).

    Returns None if the ref doesn't exist or is empty (e.g., one side
    deleted the file). The combined-output shape of _run_git mixes
    stdout and stderr, so this uses a direct subprocess call to keep
    stdout clean for the JSON parse.
    """
    try:
        proc = subprocess.run(
            ["git", "show", ref],
            capture_output=True,
            text=True,
            timeout=30.0,
            check=False,
            cwd=str(repo_root),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _repo_root(start: Path) -> Path:
    """Resolve the git repo root containing `start`. Raises ResolveError."""
    rc, out = _run_git(["git", "rev-parse", "--show-toplevel"], cwd=start)
    if rc != 0:
        raise ResolveError(f"{start}: not inside a git repository: {out.strip()}")
    return Path(out.strip())


def _is_unmerged(repo_root: Path, rel_path: Path) -> bool:
    """True iff the given path is in git's merge-conflict state."""
    rc, out = _run_git(
        ["git", "ls-files", "--unmerged", "--", str(rel_path)],
        cwd=repo_root,
    )
    if rc != 0:
        return False
    # Non-empty output = the file has unmerged stages 1/2/3.
    return bool(out.strip())


def _read_commit_metadata(repo_root: Path, ref: str) -> SideMetadata | None:
    """Look up commit metadata for `ref`. Returns None on any failure."""
    rc, out = _run_git(
        ["git", "log", "-1", "--pretty=format:%h%x09%an <%ae>%x09%cI", ref],
        cwd=repo_root,
    )
    if rc != 0:
        return None
    parts = out.strip().split("\t")
    if len(parts) != 3:
        return None
    short_sha, author, committer_date = parts
    branch = _resolve_branch_label(repo_root, ref)
    return SideMetadata(
        short_sha=short_sha,
        author=author,
        committer_date=committer_date,
        branch=branch,
    )


def _resolve_branch_label(repo_root: Path, ref: str) -> str | None:
    """Best-effort branch-name resolution for a ref.

    HEAD → `git rev-parse --abbrev-ref HEAD`.
    MERGE_HEAD → parse `.git/MERGE_MSG` (which git writes during
    a conflicted merge with "Merge branch 'foo' ..."). Falls back
    to None if neither path produces a clean answer.
    """
    if ref == "HEAD":
        rc, out = _run_git(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_root)
        if rc == 0:
            value = out.strip()
            return value if value and value != "HEAD" else None
        return None
    if ref == "MERGE_HEAD":
        merge_msg = repo_root / ".git" / "MERGE_MSG"
        if merge_msg.is_file():
            for line in merge_msg.read_text().splitlines():
                # Default merge-commit message: "Merge branch 'foo' [into bar]"
                stripped = line.strip()
                if stripped.startswith("Merge branch '") and "'" in stripped[len("Merge branch '") :]:
                    rest = stripped[len("Merge branch '") :]
                    end = rest.index("'")
                    return rest[:end]
        return None
    return None


def fetch_side_metadata(manifest_path: Path) -> tuple[SideMetadata | None, SideMetadata | None]:
    """Return (side_a_meta, side_b_meta) for the current merge state.

    Both fields are None-safe: if HEAD or MERGE_HEAD aren't readable
    the caller still gets a usable resolver, just without metadata.
    """
    manifest_path = manifest_path.resolve()
    try:
        repo_root = _repo_root(manifest_path.parent)
    except ResolveError:
        return None, None
    return (
        _read_commit_metadata(repo_root, "HEAD"),
        _read_commit_metadata(repo_root, "MERGE_HEAD"),
    )


def fetch_three_way(manifest_path: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Return (ancestor, ours, theirs) parsed from git's index stages.

    Raises ResolveError if the file isn't in a merge conflict or any
    side fails to parse.
    """
    manifest_path = manifest_path.resolve()
    repo_root = _repo_root(manifest_path.parent)
    try:
        rel = manifest_path.relative_to(repo_root)
    except ValueError as exc:
        raise ResolveError(f"{manifest_path}: path is not inside the repo root {repo_root}") from exc

    if not _is_unmerged(repo_root, rel):
        raise ResolveError(
            f"{manifest_path}: no merge conflict detected. "
            f"This command only runs against a manifest in git's merge-conflict state."
        )

    parsed: list[dict[str, Any]] = []
    for stage in ("1", "2", "3"):
        raw = _read_git_blob(repo_root, f":{stage}:{rel}")
        if raw is None:
            raise ResolveError(
                f"{manifest_path}: stage {stage} of the merge state is missing. "
                f"The file may be in a delete-vs-modify conflict on the file level, not the content level."
            )
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ResolveError(f"{manifest_path}: stage {stage} of the merge state is not valid JSON: {exc}") from exc
        if not isinstance(obj, dict):
            raise ResolveError(f"{manifest_path}: stage {stage} top-level is not a JSON object.")
        parsed.append(obj)
    return parsed[0], parsed[1], parsed[2]


# ---- atomic write ---------------------------------------------------------


def write_atomic(path: Path, content: str) -> None:
    """Write `content` to `path` via tmpfile + rename (POSIX atomic)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=".manifest.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


# ---- driver ---------------------------------------------------------------


def render_path(path: PathT) -> str:
    """Render a JSON path tuple as a dotted string for display."""
    return ".".join(path)


# ---- edit-by-hand --------------------------------------------------------


# Sentinel string the user can write in the resolution field to signal
# "delete this key" (for DELETE_VS_MODIFY conflicts where they want to
# accept the deletion even though it wasn't the value either raw side
# wrote). Distinguished from the literal string "<deleted>" only via
# the user's choice of context — same trade-off git's standard merge
# markers make.
EDIT_DELETE_TOKEN: Final[str] = "<deleted>"


def _fmt_for_template(value: Any) -> Any:
    """Convert DELETE_SENTINEL to the EDIT_DELETE_TOKEN string for templates."""
    if value is DELETE_SENTINEL:
        return EDIT_DELETE_TOKEN
    return value


def _default_editor_invoker(path: Path) -> int:
    """Spawn $EDITOR (or fallback) on `path`, return its exit code."""
    import os
    import shutil

    candidates = [os.environ.get("EDITOR"), "vim", "vi", "nano"]
    for editor in candidates:
        if not editor:
            continue
        # If $EDITOR is set to "code -w" or similar, shutil.which would
        # only find the first token. Resolve manually.
        first_token = editor.split()[0]
        if shutil.which(first_token) is None:
            continue
        try:
            proc = subprocess.run(
                [*editor.split(), str(path)],
                check=False,
            )
        except OSError:
            continue
        return proc.returncode
    raise ResolveError("no editor available: set $EDITOR or install one of vim/vi/nano on PATH.")


def edit_resolution(
    conflict: Conflict,
    *,
    editor_invoker: EditorInvoker | None = None,
) -> tuple[bool, Any]:
    """Spawn the user's editor on a JSON template; parse their resolution.

    Returns (applied, value):
    - applied=True, value=Any: user filled in the `resolution` field;
      use `value` (which may be the DELETE_SENTINEL if they wrote the
      EDIT_DELETE_TOKEN string).
    - applied=False, value=None: user left resolution as null, or
      the editor exited non-zero, or the file couldn't be parsed.
      Caller should treat as a skip.
    """
    import tempfile

    invoker = editor_invoker if editor_invoker is not None else _default_editor_invoker

    template = {
        "path": render_path(conflict.path),
        "kind": conflict.kind.value,
        "_help": (
            "Edit the 'resolution' field below to your chosen value (JSON-encoded). "
            f"Use the string {EDIT_DELETE_TOKEN!r} to delete this key. "
            "Leave as null (or save unchanged) to skip this conflict."
        ),
        "ancestor": _fmt_for_template(conflict.ancestor),
        "side_a": _fmt_for_template(conflict.side_a),
        "side_b": _fmt_for_template(conflict.side_b),
        "resolution": None,
    }

    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".json",
        prefix="maury-resolve-",
        delete=False,
    ) as fh:
        fh.write(json.dumps(template, indent=2) + "\n")
        tmp_path = Path(fh.name)

    try:
        rc = invoker(tmp_path)
        if rc != 0:
            return (False, None)
        try:
            parsed = json.loads(tmp_path.read_text())
        except json.JSONDecodeError:
            return (False, None)
        if not isinstance(parsed, dict):
            return (False, None)
        resolution = parsed.get("resolution")
        if resolution is None:
            return (False, None)
        if isinstance(resolution, str) and resolution == EDIT_DELETE_TOKEN:
            return (True, DELETE_SENTINEL)
        return (True, resolution)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def semantic_summary(
    conflict: Conflict,
    *,
    ancestor: dict[str, Any],
    side_a: dict[str, Any],
    side_b: dict[str, Any],
) -> str | None:
    """Compute a consequence-aware one-liner for known manifest paths.

    Returns None if the path doesn't match a known shape. Today we
    annotate:

    - `profiles.<profile_id>` deletion → count of hosts bound to it
      in the OPPOSITE side (so the user sees how many hosts would
      lose their binding if they accept the deletion).
    - `hosts.<host_id>` deletion → flags that the host is being
      retired.

    Additional shapes (extends-chain changes, repo-mode flips, etc.)
    are reasonable future enhancements.
    """
    path = conflict.path
    if len(path) >= 2 and path[0] == "profiles":
        return _summarize_profile_conflict(conflict, ancestor=ancestor, side_a=side_a, side_b=side_b)
    if len(path) >= 2 and path[0] == "hosts":
        return _summarize_host_conflict(conflict)
    return None


def _hosts_bound_to_profile(manifest: dict[str, Any], profile_id: str) -> int:
    """Count hosts whose `profile` field references the given profile ID."""
    hosts = manifest.get("hosts")
    if not isinstance(hosts, dict):
        return 0
    return sum(1 for spec in hosts.values() if isinstance(spec, dict) and spec.get("profile") == profile_id)


def _summarize_profile_conflict(
    conflict: Conflict,
    *,
    ancestor: dict[str, Any],
    side_a: dict[str, Any],
    side_b: dict[str, Any],
) -> str | None:
    """Summarize the consequence of a profile-level conflict.

    Matches both top-level (`profiles.<pid>`) and any nested change
    inside the same profile (`profiles.<pid>.name`, etc.). The
    deletion-of-profile case is by definition at the top level
    (`len(path) == 2`); deeper paths can only be BOTH_MODIFIED.
    """
    profile_id = conflict.path[1]
    if len(conflict.path) == 2 and conflict.kind is ConflictKind.DELETE_VS_MODIFY:
        deleting_side = "A" if conflict.side_a is DELETE_SENTINEL else "B"
        keeping_manifest = side_b if conflict.side_a is DELETE_SENTINEL else side_a
        bound = _hosts_bound_to_profile(keeping_manifest, profile_id)
        if bound > 0:
            return f"side {deleting_side} deletes this profile; {bound} host(s) on the other side still reference it."
        return f"side {deleting_side} deletes this profile; no other side hosts reference it."
    if conflict.kind is ConflictKind.BOTH_MODIFIED:
        bound_a = _hosts_bound_to_profile(side_a, profile_id)
        bound_b = _hosts_bound_to_profile(side_b, profile_id)
        return f"this profile is bound by {bound_a} host(s) on A and {bound_b} on B."
    return None


def _summarize_host_conflict(conflict: Conflict) -> str | None:
    """Summarize the consequence of a host-level conflict."""
    if conflict.kind is ConflictKind.DELETE_VS_MODIFY:
        deleting_side = "A" if conflict.side_a is DELETE_SENTINEL else "B"
        return f"side {deleting_side} retires this host; it will no longer sync."
    if conflict.kind is ConflictKind.BOTH_MODIFIED:
        return "both sides modified this host's record."
    return None


def resolve_manifest(
    manifest_path: Path,
    *,
    prompter: Prompter,
    write: bool = True,
    editor_invoker: EditorInvoker | None = None,
) -> ResolveSummary:
    """End-to-end resolve.

    Fetches the three-way state from git, runs the structured merge,
    walks conflicts via the prompter, validates the result, and writes
    atomically. Caller is responsible for `git add` + commit after a
    successful run.

    `editor_invoker` is injectable to make the edit-by-hand path
    testable; production passes None and the default-editor fallback
    chain ($EDITOR, vim, vi, nano) is used.
    """
    from maury.manifest import ManifestError, load_manifest, validate_manifest

    ancestor, ours, theirs = fetch_three_way(manifest_path)
    merge = compute_merge(ancestor, ours, theirs)
    side_a_meta, side_b_meta = fetch_side_metadata(manifest_path)

    auto_count = _count_auto_resolved(merge)
    chosen_sides: list[str] = []
    choices: dict[PathT, Any] = {}

    for raw_conflict in merge.conflicts:
        summary = semantic_summary(raw_conflict, ancestor=ancestor, side_a=ours, side_b=theirs)
        conflict = replace(raw_conflict, semantic_summary=summary) if summary else raw_conflict
        choice = prompter(conflict)
        if choice == "q":
            return ResolveSummary(
                manifest_path=manifest_path,
                auto_merged_paths=auto_count,
                user_resolved_paths=len(choices),
                aborted=True,
                chosen_sides=tuple(chosen_sides),
                side_a_meta=side_a_meta,
                side_b_meta=side_b_meta,
            )
        if choice == "a":
            if conflict.side_a is DELETE_SENTINEL:
                choices[conflict.path] = DELETE_SENTINEL
            else:
                choices[conflict.path] = conflict.side_a
            chosen_sides.append("A")
        elif choice == "b":
            if conflict.side_b is DELETE_SENTINEL:
                choices[conflict.path] = DELETE_SENTINEL
            else:
                choices[conflict.path] = conflict.side_b
            chosen_sides.append("B")
        elif choice == "s":
            # Skip: keep ancestor's value. For BOTH_ADDED_SAME_KEY_DIFFERENT_VALUE
            # the ancestor was absent → equivalent to deleting both sides' add.
            if conflict.ancestor is DELETE_SENTINEL:
                choices[conflict.path] = DELETE_SENTINEL
            else:
                choices[conflict.path] = conflict.ancestor
            chosen_sides.append("skip")
        elif choice == "e":
            applied, value = edit_resolution(conflict, editor_invoker=editor_invoker)
            if applied:
                choices[conflict.path] = value
                chosen_sides.append("edit")
            else:
                # User left the resolution null or editor exited non-zero
                # → treat as skip per the documented contract.
                if conflict.ancestor is DELETE_SENTINEL:
                    choices[conflict.path] = DELETE_SENTINEL
                else:
                    choices[conflict.path] = conflict.ancestor
                chosen_sides.append("skip")
        else:
            raise ResolveError(f"prompter returned unrecognised choice {choice!r} (expected 'a','b','s','e','q')")

    final = apply_resolutions(merge, choices)

    # Schema-validate before writing.
    if write:
        # Write to a temp location next to the real manifest so we can
        # round-trip-validate before clobbering.
        tmp_for_validation = manifest_path.parent / f".{manifest_path.name}.validate.tmp"
        try:
            tmp_for_validation.write_text(json.dumps(final, indent=2) + "\n")
            try:
                m = load_manifest(tmp_for_validation)
            except ManifestError as exc:
                raise ResolveError(f"resolved manifest failed schema validation: {exc}") from exc
            errors = validate_manifest(m)
            if errors:
                raise ResolveError("resolved manifest failed cross-reference validation:\n  " + "\n  ".join(errors))
        finally:
            if tmp_for_validation.exists():
                tmp_for_validation.unlink()

        write_atomic(manifest_path, json.dumps(final, indent=2) + "\n")

    return ResolveSummary(
        manifest_path=manifest_path,
        auto_merged_paths=auto_count,
        user_resolved_paths=len(merge.conflicts),
        aborted=False,
        chosen_sides=tuple(chosen_sides),
        side_a_meta=side_a_meta,
        side_b_meta=side_b_meta,
    )


def _count_auto_resolved(merge: MergeResult) -> int:
    """Count leaf keys in the auto-resolved portion. Approximation of 'merged paths'."""
    return _count_leaves(merge.resolved)


def _count_leaves(value: Any) -> int:
    if isinstance(value, dict):
        return sum(_count_leaves(v) for v in value.values()) if value else 0
    return 1


# ---- non-interactive auto-merge entry point ------------------------------


class AutoMergeOutcome(enum.StrEnum):
    """Result classes returned by `try_auto_merge_manifest()`."""

    NOT_IN_CONFLICT = "not_in_conflict"
    """The manifest is not in a merge-conflict state. Caller does nothing."""

    AUTO_MERGED = "auto_merged"
    """Structured-merge engine resolved every path. Manifest was
    re-validated and written atomically. Caller is responsible for
    `git add` + `git commit`."""

    NEEDS_USER_RESOLVE = "needs_user_resolve"
    """At least one path requires user arbitration. The manifest is
    left in its conflicted on-disk state. Caller surfaces a pointer
    at `maury manifest resolve`."""


@dataclass(frozen=True)
class AutoMergeReport:
    """Summary of a `try_auto_merge_manifest()` run."""

    outcome: AutoMergeOutcome
    manifest_path: Path
    auto_merged_paths: int = 0
    """Number of leaf paths the engine auto-resolved (when outcome is
    AUTO_MERGED). Zero for the other outcomes."""

    conflict_paths: tuple[PathT, ...] = field(default_factory=tuple)
    """JSON paths of the conflicts that would need user arbitration
    (when outcome is NEEDS_USER_RESOLVE). Empty otherwise."""


def try_auto_merge_manifest(manifest_path: Path) -> AutoMergeReport:
    """Attempt a non-interactive structured auto-merge of a conflicted manifest.

    Used by `maury sync` when a base-repo pull lands the working tree
    in a merge conflict on `.meta/manifest.json`. The caller has
    already done `git fetch` + `git merge` (or `git pull`) and the
    file is sitting in git's stage-1/2/3 state.

    Behavior:
      - If `manifest_path` is not in conflict → NOT_IN_CONFLICT.
      - Else read ancestor/ours/theirs from git's index, run the
        structured-merge engine.
        - If the engine produces zero conflicts: re-validate the
          merged manifest against the schema (and cross-references),
          write atomically, return AUTO_MERGED.
        - If conflicts remain: the manifest is left in its
          on-disk conflicted state, return NEEDS_USER_RESOLVE with
          the conflict path list for callers that want to surface
          a hint.

    Raises ResolveError if the file isn't a manifest (parse failure),
    if any stage is missing, or if validation of an otherwise-clean
    merge fails (schema breakage from auto-merge is a real bug to
    surface, not paper over).
    """
    from maury.manifest import ManifestError, load_manifest, validate_manifest

    manifest_path = manifest_path.resolve()
    repo_root = _repo_root(manifest_path.parent)
    rel = manifest_path.relative_to(repo_root)

    if not _is_unmerged(repo_root, rel):
        return AutoMergeReport(
            outcome=AutoMergeOutcome.NOT_IN_CONFLICT,
            manifest_path=manifest_path,
        )

    ancestor, ours, theirs = fetch_three_way(manifest_path)
    merge = compute_merge(ancestor, ours, theirs)

    if merge.conflicts:
        return AutoMergeReport(
            outcome=AutoMergeOutcome.NEEDS_USER_RESOLVE,
            manifest_path=manifest_path,
            conflict_paths=tuple(c.path for c in merge.conflicts),
        )

    # Engine resolved everything. Validate before clobbering.
    final = merge.resolved
    tmp_for_validation = manifest_path.parent / f".{manifest_path.name}.validate.tmp"
    try:
        tmp_for_validation.write_text(json.dumps(final, indent=2) + "\n")
        try:
            m = load_manifest(tmp_for_validation)
        except ManifestError as exc:
            raise ResolveError(f"auto-merged manifest failed schema validation: {exc}") from exc
        errors = validate_manifest(m)
        if errors:
            raise ResolveError("auto-merged manifest failed cross-reference validation:\n  " + "\n  ".join(errors))
    finally:
        if tmp_for_validation.exists():
            tmp_for_validation.unlink()

    write_atomic(manifest_path, json.dumps(final, indent=2) + "\n")
    return AutoMergeReport(
        outcome=AutoMergeOutcome.AUTO_MERGED,
        manifest_path=manifest_path,
        auto_merged_paths=_count_leaves(final),
    )


# ---- default interactive prompter ----------------------------------------


def default_prompter(conflict: Conflict) -> str:
    """Stdin-based prompter used by the CLI."""
    import click

    click.echo("")
    click.echo("─" * 60)
    click.echo(f"path: {render_path(conflict.path)}")
    click.echo(f"kind: {conflict.kind.value}")
    click.echo(f"  ancestor:  {_fmt(conflict.ancestor)}")
    click.echo(f"  side A:    {_fmt(conflict.side_a)}")
    click.echo(f"  side B:    {_fmt(conflict.side_b)}")
    if conflict.kind is ConflictKind.BOTH_MODIFIED:
        explanation = "Both sides changed the ancestor's value to different new values. Pick which side's value lands."
    elif conflict.kind is ConflictKind.DELETE_VS_MODIFY:
        explanation = (
            "One side deleted this key; the other modified it. 'a' takes side A's intent; 'b' takes side B's intent."
        )
    else:
        explanation = "Both sides added this key with different content. Pick which side's value lands."
    click.echo(f"  → {explanation}")
    if conflict.semantic_summary:
        click.echo(f"  ⓘ {conflict.semantic_summary}")
    click.echo("")
    choice: str = click.prompt(
        "  resolve [a=take A / b=take B / e=edit by hand / s=skip (keep ancestor) / q=abort]",
        default="s",
    )
    return choice.strip().lower()[:1]


def _fmt(value: Any) -> str:
    if value is DELETE_SENTINEL:
        return "<deleted>"
    return json.dumps(value, indent=None)


__all__ = [
    "AutoMergeOutcome",
    "AutoMergeReport",
    "Prompter",
    "ResolveError",
    "ResolveSummary",
    "default_prompter",
    "fetch_three_way",
    "render_path",
    "resolve_manifest",
    "try_auto_merge_manifest",
    "write_atomic",
]
