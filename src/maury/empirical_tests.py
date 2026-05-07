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


def _build_settings(out_dir: Path, scripts: dict[str, Path]) -> dict[str, object]:
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
