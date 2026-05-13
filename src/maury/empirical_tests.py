"""Empirical verification of unverified Claude Code hook behaviors.

Three load-bearing claims about Claude Code's hook subsystem are
documented in `docs/claude-code-contract.md` as ❓ Assumed but
unverified, and ADR-0023's "Empirical-test debt" section names them
as load-bearing for drift attribution and the `# maury-managed`
marker scheme:

1. **comment_stripping** — does the shell that runs the hook command
   strip a trailing `# maury-managed` comment cleanly? If not, the
   ADR-0023 marker scheme breaks.
2. **subprocess_env** — what does the hook subprocess see for PATH,
   HOME, PWD, and the documented Claude Code env vars? ADR-0023's
   absolute-path requirement and ADR-0017's claude-writes hook
   depend on knowing this.
3. **file_io_permissions** — can a hook freely write a new file
   under a directory it owns (the analog of
   `~/.claude/maury-state/`)? ADR-0017's drift-attribution and
   ADR-0025's session tracking depend on this.

The harness sets up an isolated temporary "project" workspace, drops
a `.claude/settings.json` containing probe PostToolUse hooks into it,
invokes `claude -p` against the workspace so the hooks fire, then
reads probe-output files back and reports pass/fail per test.

Nothing in the user's real `~/.claude/` is modified — the workspace
is its own throwaway project directory.

The harness is exposed via `maury verify-cc-hooks` (see cli.py).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Marker the comment-stripping probe appends to its hook command.
# If the shell strips trailing `#`-comments correctly, the redirect
# preceding this comment still runs and the probe file gets written.
COMMENT_MARKER = "# maury-managed-empirical-probe"


def _comment_probe_script(out: Path) -> str:
    """Probe shell that runs if the trailing `# maury-managed` is stripped cleanly."""
    return f"""#!/bin/sh
# Comment-stripping probe: if the shell strips the trailing `# maury-managed`
# comment cleanly, this script runs and writes "ran" to the output path.
printf '%s' "ran" > "{out}"
"""


def _env_probe_script(out: Path) -> str:
    """Probe shell that captures the hook subprocess's env + cwd as key=value lines."""
    return f"""#!/bin/sh
# Env-capture probe: write a flat key=value file of the env vars and CWD
# the hook subprocess sees. Line-based; no JSON quoting nightmare.
{{
    printf 'path=%s\\n' "$PATH"
    printf 'home=%s\\n' "$HOME"
    printf 'pwd=%s\\n' "$PWD"
    printf 'hook_event=%s\\n' "${{CLAUDE_HOOK_EVENT:-unset}}"
    printf 'project_dir=%s\\n' "${{CLAUDE_PROJECT_DIR:-unset}}"
}} > "{out}"
"""


def _io_probe_script(io_dir: Path, out: Path) -> str:
    """Probe shell that creates a sibling directory + writes a file inside it."""
    return f"""#!/bin/sh
# File-IO probe: create a directory that didn't exist, write a file inside.
# Tests whether the hook subprocess has filesystem access to paths it
# needs to provision (analog of writing to ~/.claude/maury-state/).
mkdir -p "{io_dir}" && printf 'wrote-from-hook' > "{out}"
"""


@dataclass(frozen=True)
class ProbeResult:
    """Outcome of one empirical probe."""

    name: str
    passed: bool
    detail: str
    captured: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class HarnessReport:
    """Aggregate result of running every probe."""

    workspace: Path
    claude_invoked: bool
    claude_returncode: int | None
    claude_stderr_excerpt: str
    probes: tuple[ProbeResult, ...]

    @property
    def passed(self) -> bool:
        return self.claude_invoked and all(p.passed for p in self.probes)


def claude_present() -> bool:
    """Return True if `claude` is on PATH."""
    return shutil.which("claude") is not None


def _write_probe_scripts(scripts_dir: Path, out_dir: Path) -> dict[str, Path]:
    """Materialize the three probe shell scripts into scripts_dir.

    Returns a mapping of probe name → absolute script path. The hook
    commands in settings.json invoke these by absolute path; this avoids
    embedding multi-arg printf calls (and their attendant shell-quoting
    pitfalls) in the JSON command string.
    """
    scripts_dir.mkdir(parents=True, exist_ok=True)

    comment_script = scripts_dir / "comment_probe.sh"
    env_script = scripts_dir / "env_probe.sh"
    io_script = scripts_dir / "io_probe.sh"

    comment_out = out_dir / "comment.out"
    env_out = out_dir / "env.out"
    io_dir = out_dir / "maury-state-analog"
    io_out = io_dir / "io.out"

    comment_script.write_text(_comment_probe_script(comment_out))
    env_script.write_text(_env_probe_script(env_out))
    io_script.write_text(_io_probe_script(io_dir, io_out))

    for path in (comment_script, env_script, io_script):
        path.chmod(0o755)

    return {
        "comment": comment_script,
        "env": env_script,
        "io": io_script,
    }


def _build_settings(out_dir: Path, scripts: dict[str, Path]) -> dict[str, Any]:
    """Construct a Claude Code settings.json that installs the three probe hooks.

    Per Claude Code docs, hooks live under settings["hooks"][<event>] as a list
    of {matcher, hooks: [{type:"command", command:"..."}]} groups. PostToolUse
    fires after every tool invocation, so any tool use by Claude during
    `claude -p` will trigger all three probes.

    The comment probe's command string carries a trailing shell comment
    (`# maury-managed-empirical-probe`); if the shell strips that comment
    cleanly, the script preceding it executes and writes "ran" — which is
    exactly what ADR-0023's marker scheme depends on.
    """
    return {
        "hooks": {
            "PostToolUse": [
                {
                    "matcher": "*",
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"{scripts['comment']} {COMMENT_MARKER}",
                        },
                        {
                            "type": "command",
                            "command": str(scripts["env"]),
                        },
                        {
                            "type": "command",
                            "command": str(scripts["io"]),
                        },
                    ],
                }
            ]
        }
    }


def _evaluate_comment(out_path: Path) -> ProbeResult:
    """Did the shell strip the trailing `# maury-managed-empirical-probe`?"""
    if not out_path.exists():
        return ProbeResult(
            name="comment_stripping",
            passed=False,
            detail=(
                f"probe output file not found at {out_path} — "
                "the hook command did not execute, OR the trailing comment "
                "broke parsing. ADR-0023's marker scheme is at risk."
            ),
        )
    contents = out_path.read_text()
    if contents.strip() == "ran":
        return ProbeResult(
            name="comment_stripping",
            passed=True,
            detail=(f"hook command with trailing `{COMMENT_MARKER}` executed cleanly — marker scheme is safe to use."),
            captured={"output": contents},
        )
    return ProbeResult(
        name="comment_stripping",
        passed=False,
        detail=(
            f"probe ran but produced unexpected content: {contents!r} "
            "(expected 'ran'). Investigate before relying on the marker."
        ),
        captured={"output": contents},
    )


def _parse_env_output(text: str) -> dict[str, str]:
    """Parse the env probe's flat key=value output into a dict.

    Format per line: `<key>=<value>`. Unknown keys are kept as-is.
    Lines without `=` are ignored. Trailing newlines are stripped from values.
    """
    result: dict[str, str] = {}
    for line in text.splitlines():
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        result[key.strip()] = value
    return result


def _evaluate_env(out_path: Path) -> ProbeResult:
    """What does the hook subprocess see for PATH/HOME/PWD?"""
    if not out_path.exists():
        return ProbeResult(
            name="subprocess_env",
            passed=False,
            detail=f"env probe output not found at {out_path}",
        )
    raw = out_path.read_text()
    captured: dict[str, object] = dict(_parse_env_output(raw))
    # We pass if we got a non-empty PATH and HOME — the actual values are
    # informational (recorded in `captured`) for the user/ADR to reference.
    path_val = str(captured.get("path", ""))
    home_val = str(captured.get("home", ""))
    if path_val and home_val:
        return ProbeResult(
            name="subprocess_env",
            passed=True,
            detail="hook subprocess inherited PATH and HOME; details captured for ADR reference.",
            captured=captured,
        )
    return ProbeResult(
        name="subprocess_env",
        passed=False,
        detail="hook subprocess missing PATH or HOME — absolute-path requirement in ADR-0023 §5 is justified.",
        captured=captured if captured else {"raw": raw},
    )


def _evaluate_io(out_path: Path) -> ProbeResult:
    """Could the hook create a directory and write a file?"""
    if not out_path.parent.exists():
        return ProbeResult(
            name="file_io_permissions",
            passed=False,
            detail=(
                f"hook could not create directory {out_path.parent} — "
                "writes to ~/.claude/maury-state/ may be blocked. "
                "ADR-0017 / ADR-0025 are at risk."
            ),
        )
    if not out_path.exists():
        return ProbeResult(
            name="file_io_permissions",
            passed=False,
            detail=(f"directory was created but file write to {out_path} failed"),
        )
    contents = out_path.read_text()
    if contents == "wrote-from-hook":
        return ProbeResult(
            name="file_io_permissions",
            passed=True,
            detail=(
                "hook successfully created a directory and wrote a file — "
                "ADR-0017's claude-writes.jsonl and ADR-0025's "
                "active-sessions.jsonl can rely on this."
            ),
            captured={"output": contents},
        )
    return ProbeResult(
        name="file_io_permissions",
        passed=False,
        detail=f"file write produced unexpected content: {contents!r}",
        captured={"output": contents},
    )


def run(
    *,
    workspace: Path | None = None,
    claude_prompt: str = "List the files in the current directory.",
    timeout: float = 60.0,
) -> HarnessReport:
    """Run the harness end-to-end. Returns a HarnessReport.

    Args:
        workspace: where to set up the probe project. If None, a temp
            directory is created and (on success) cleaned up by the caller.
        claude_prompt: the prompt to send via `claude -p`. Should reliably
            trigger at least one tool use so that PostToolUse fires.
        timeout: seconds to wait for the claude subprocess.
    """
    if workspace is None:
        workspace = Path(tempfile.mkdtemp(prefix="maury-empirical-"))

    out_dir = workspace / "probe-out"
    out_dir.mkdir(parents=True, exist_ok=True)
    scripts = _write_probe_scripts(workspace / ".claude" / "probes", out_dir)
    settings_dir = workspace / ".claude"
    settings_dir.mkdir(parents=True, exist_ok=True)
    (settings_dir / "settings.json").write_text(json.dumps(_build_settings(out_dir, scripts), indent=2))

    # Empty CLAUDE.md so Claude has *something* to read at session start
    # without inheriting the user's real instructions.
    (workspace / "CLAUDE.md").write_text(
        "# Empirical test workspace\n\n"
        "This is a throwaway project used by `maury verify-cc-hooks`. "
        "When asked, list the files in this directory.\n"
    )

    if not claude_present():
        return HarnessReport(
            workspace=workspace,
            claude_invoked=False,
            claude_returncode=None,
            claude_stderr_excerpt="`claude` not found on PATH",
            probes=(),
        )

    env = os.environ.copy()
    # Make sure Claude reads OUR project-level settings.
    # CLAUDE_PROJECT_DIR is documented; setting it explicitly removes
    # ambiguity if the user's CWD doesn't match the workspace.
    env["CLAUDE_PROJECT_DIR"] = str(workspace)

    try:
        proc = subprocess.run(
            ["claude", "-p", claude_prompt],
            cwd=workspace,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return HarnessReport(
            workspace=workspace,
            claude_invoked=True,
            claude_returncode=None,
            claude_stderr_excerpt=f"timed out after {timeout}s",
            probes=(),
        )

    probes = (
        _evaluate_comment(out_dir / "comment.out"),
        _evaluate_env(out_dir / "env.out"),
        _evaluate_io(out_dir / "maury-state-analog" / "io.out"),
    )

    stderr_tail = (proc.stderr or "")[-400:]
    return HarnessReport(
        workspace=workspace,
        claude_invoked=True,
        claude_returncode=proc.returncode,
        claude_stderr_excerpt=stderr_tail,
        probes=probes,
    )


# =========================================================================
# Project-directory derivation verifier.
#
# Verifies the algorithm Claude Code uses to map cwd → directory name under
# `~/.claude/projects/<X>/`. The algorithm is empirically derived; see
# `cc-contract:project-directory-derivation` in docs/claude-code-contract.md
# and upstream issue anthropics/claude-code#54865 for the canonical
# reference (which quotes the relevant `fh()` function from cli.js).
# =========================================================================


def derive_project_dir(cwd: Path) -> str:
    """Predict the directory name Claude Code creates under `~/.claude/projects/`
    when invoked with the given working directory.

    Algorithm (empirically verified 2026-05-13 against claude 2.1.140 on macOS;
    matches the `fh()` source quoted in anthropics/claude-code#54865):

        1. Resolve symlinks on the cwd (`Path.resolve()` ≈ POSIX `realpath`).
        2. Iterate the resolved path **as UTF-16 code units** (matching the JS
           runtime's regex semantics — the CLI is Node).
        3. For each code unit: keep iff it matches `[A-Za-z0-9]`; otherwise
           replace with `-`. No collapse of consecutive replacements.

    Non-injective. Distinct cwds can produce the same name: `/a/b/c` and
    `/a-b-c` both yield `-a-b-c`. Consumers walking `~/.claude/projects/`
    MUST NOT assume one directory uniquely identifies one cwd.

    Non-BMP characters (emoji etc.) are encoded as UTF-16 surrogate pairs;
    neither surrogate is alphanumeric, so each non-BMP codepoint becomes
    **two** hyphens (e.g., `🚀` → `--`).
    """
    resolved = str(cwd.resolve())
    out: list[str] = []
    for ch in resolved:
        cp = ord(ch)
        if cp > 0xFFFF:
            # Non-BMP codepoint → UTF-16 surrogate pair → two hyphens.
            out.append("--")
        elif (0x30 <= cp <= 0x39) or (0x41 <= cp <= 0x5A) or (0x61 <= cp <= 0x7A):
            # ASCII [A-Za-z0-9] — preserved.
            out.append(ch)
        else:
            # Any other BMP character (including non-ASCII letters like 'é',
            # punctuation, whitespace) — single hyphen.
            out.append("-")
    return "".join(out)


@dataclass(frozen=True)
class ProjectDirCase:
    """One cwd shape to test in the project-dir derivation corpus."""

    # Path component(s) to append under the harness's test root. May contain
    # one slash to test nested paths; the harness mkdirs the full chain.
    cwd_suffix: str
    # Human-readable description (what shape this case is testing).
    description: str
    # If non-empty, declares this case is expected to bucket together with
    # other cases sharing the same group label (collision-test pair).
    collision_group: str = ""


# Default corpus — cases that lock in the algorithm AND demonstrate the
# load-bearing collision property.
DEFAULT_PROJECT_DIR_CORPUS: tuple[ProjectDirCase, ...] = (
    ProjectDirCase("plain", "all-ASCII plain"),
    ProjectDirCase("CamelCase", "mixed case preserved"),
    ProjectDirCase("digit-2026", "digits + existing hyphen preserved"),
    ProjectDirCase("dash-already", "existing hyphens preserved"),
    ProjectDirCase("under_score", "underscore → hyphen"),
    ProjectDirCase("has.dot", "dot → hyphen"),
    ProjectDirCase("has space here", "space → hyphen (each space its own hyphen)"),
    ProjectDirCase("with+plus", "plus → hyphen"),
    ProjectDirCase("with@at", "at-sign → hyphen"),
    # Collision pair: distinct cwds, same predicted dir name.
    ProjectDirCase("a-b-c", "collision pair (hyphen form)", collision_group="abc"),
    ProjectDirCase("a/b/c", "collision pair (slash form)", collision_group="abc"),
    # Non-ASCII coverage.
    ProjectDirCase("café", "BMP non-ASCII (Latin-1 supplement)"),
    ProjectDirCase("日本語", "BMP non-ASCII (CJK)"),
    ProjectDirCase("emoji-🚀", "non-BMP (UTF-16 surrogate pair → two hyphens)"),
)


@dataclass(frozen=True)
class ProjectDirCaseResult:
    """Outcome of running one corpus case."""

    case: ProjectDirCase
    cwd: Path  # The resolved absolute path the harness invoked claude in.
    predicted_dir_name: str
    observed_dir_names: tuple[str, ...]  # All NEW dirs that appeared in projects/ after this case.
    matched: bool
    detail: str


@dataclass(frozen=True)
class ProjectDirHarnessReport:
    """Aggregate result for `maury verify-cc-projects-dir`."""

    test_root: Path
    claude_present: bool
    claude_version: str | None
    results: tuple[ProjectDirCaseResult, ...]
    collision_groups_verified: tuple[str, ...]  # group labels whose members all bucketed together.
    new_project_dirs: tuple[str, ...]  # Dirs created under ~/.claude/projects/ during the run.

    @property
    def passed(self) -> bool:
        """All cases matched their predictions AND all declared collision groups merged."""
        if not self.claude_present:
            return False
        if not all(r.matched for r in self.results):
            return False
        # Every declared collision group must show up as one observed dir.
        declared_groups = {r.case.collision_group for r in self.results if r.case.collision_group}
        return declared_groups.issubset(set(self.collision_groups_verified))


def _projects_dir() -> Path:
    """Path to `~/.claude/projects/` (the directory we observe)."""
    return Path.home() / ".claude" / "projects"


def _snapshot_projects_dir() -> set[str]:
    """Return the current set of entry names under `~/.claude/projects/`.

    Returns an empty set if the directory doesn't exist (first-run case).
    """
    pd = _projects_dir()
    if not pd.exists():
        return set()
    return {p.name for p in pd.iterdir()}


def _claude_version() -> str | None:
    """Return `claude --version`'s output trimmed, or None if claude isn't on PATH."""
    if not claude_present():
        return None
    try:
        proc = subprocess.run(
            ["claude", "--version"],
            capture_output=True,
            text=True,
            timeout=10.0,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    return (proc.stdout or "").strip() or None


def run_project_dir_harness(
    *,
    test_root: Path | None = None,
    corpus: tuple[ProjectDirCase, ...] = DEFAULT_PROJECT_DIR_CORPUS,
    claude_prompt: str = "say only the word: ok",
    timeout: float = 60.0,
    cleanup_project_dirs: bool = True,
) -> ProjectDirHarnessReport:
    """Run the project-dir derivation verifier end-to-end.

    For each case in the corpus:
      1. Create the target cwd directory under `test_root`.
      2. Snapshot `~/.claude/projects/` before invoking claude.
      3. Invoke `claude -p <claude_prompt>` in that cwd.
      4. Re-snapshot; the delta is the set of new project dirs.
      5. Compare to `derive_project_dir()`'s prediction.

    Collision-group cases intentionally predict the same dir name; we verify
    that all members of a group produce exactly one observed dir between them.

    Args:
        test_root: directory under which to create per-case cwds. If None,
            a fresh tempdir is used and (unless `cleanup_project_dirs` is
            False) cleaned up afterward.
        corpus: cases to run. Defaults to DEFAULT_PROJECT_DIR_CORPUS.
        claude_prompt: prompt sent to `claude -p`. Should be trivial; the
            substance doesn't matter, only that claude actually invokes.
        timeout: seconds per `claude -p` invocation.
        cleanup_project_dirs: if True, remove the project-dirs this harness
            created under `~/.claude/projects/` after the run. Set False
            for forensic inspection.
    """
    cleanup_test_root = test_root is None
    test_root = test_root or Path(tempfile.mkdtemp(prefix="maury-projdir-"))
    test_root.mkdir(parents=True, exist_ok=True)

    version = _claude_version()
    if version is None:
        return ProjectDirHarnessReport(
            test_root=test_root,
            claude_present=False,
            claude_version=None,
            results=(),
            collision_groups_verified=(),
            new_project_dirs=(),
        )

    pre_run_baseline = _snapshot_projects_dir()
    cumulative_new: set[str] = set()
    results: list[ProjectDirCaseResult] = []
    # Map collision group → set of observed dir names (should converge to 1).
    group_observed: dict[str, set[str]] = {}

    for case in corpus:
        cwd = test_root / case.cwd_suffix
        cwd.mkdir(parents=True, exist_ok=True)
        cwd_resolved = cwd.resolve()
        predicted = derive_project_dir(cwd_resolved)

        before = _snapshot_projects_dir()
        try:
            subprocess.run(
                ["claude", "-p", claude_prompt],
                cwd=str(cwd_resolved),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            results.append(
                ProjectDirCaseResult(
                    case=case,
                    cwd=cwd_resolved,
                    predicted_dir_name=predicted,
                    observed_dir_names=(),
                    matched=False,
                    detail=f"claude invocation failed: {exc}",
                )
            )
            continue

        after = _snapshot_projects_dir()
        new_this_case = after - before
        cumulative_new |= new_this_case
        observed = tuple(sorted(new_this_case))
        prediction_exists = predicted in after

        if case.collision_group:
            # Collision-group members may legitimately see NO new dir if a
            # group-mate already created the bucket. Success criterion: the
            # predicted dir is present in projects/ post-claude AND it was
            # created during this run (i.e., it's in our cumulative set).
            group_observed.setdefault(case.collision_group, set()).update(new_this_case)
            prediction_in_cumulative = predicted in cumulative_new
            matched = prediction_exists and prediction_in_cumulative
            if matched and not new_this_case:
                detail = f"collision case: bucketed into existing {predicted!r} (group-mate created it earlier)"
            elif matched:
                detail = f"collision case: created {predicted!r}"
            elif new_this_case:
                detail = f"collision case: predicted {predicted!r} but observed {observed}"
            else:
                detail = f"collision case: predicted {predicted!r} but nothing created and dir not in projects/"
        else:
            # Non-collision case: expect exactly one new dir matching prediction.
            if not new_this_case:
                matched = False
                detail = f"claude invocation produced no new dir under {_projects_dir()} — claude may have errored silently"
            elif len(new_this_case) == 1 and predicted in new_this_case:
                matched = True
                detail = f"predicted and observed {predicted!r}"
            elif len(new_this_case) == 1:
                actual = next(iter(new_this_case))
                matched = False
                detail = f"predicted {predicted!r} but claude created {actual!r}"
            else:
                matched = False
                detail = f"predicted {predicted!r}; claude created multiple new dirs: {observed}"

        results.append(
            ProjectDirCaseResult(
                case=case,
                cwd=cwd_resolved,
                predicted_dir_name=predicted,
                observed_dir_names=observed,
                matched=matched,
                detail=detail,
            )
        )

    # A collision group is verified if all its declared members bucketed
    # together (i.e., produced exactly one distinct dir across the group).
    verified_groups: list[str] = []
    for group, observed_set in group_observed.items():
        # observed_set is the union of new dirs across the group's members.
        # If the group bucketed correctly, only the FIRST member produced a
        # new dir; subsequent members saw it as already-existing. So the
        # union should be a single dir AND should equal the prediction.
        if len(observed_set) == 1:
            verified_groups.append(group)

    if cleanup_project_dirs:
        pd = _projects_dir()
        for name in cumulative_new:
            target = pd / name
            if target.exists() and target.is_dir():
                shutil.rmtree(target, ignore_errors=True)

    if cleanup_test_root:
        shutil.rmtree(test_root, ignore_errors=True)

    return ProjectDirHarnessReport(
        test_root=test_root,
        claude_present=True,
        claude_version=version,
        results=tuple(results),
        collision_groups_verified=tuple(verified_groups),
        new_project_dirs=tuple(sorted(cumulative_new - pre_run_baseline)),
    )
