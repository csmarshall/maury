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

# Hook command template for the comment-stripping probe.
# Trailing `# maury-managed-empirical-probe` is the comment-strip test.
COMMENT_PROBE_TEMPLATE = '/bin/sh -c \'printf "%s" "ran" > "{out}"\' {marker}'

# Env-capture probe writes a JSON document of relevant env + cwd.
ENV_PROBE_TEMPLATE = (
    "/bin/sh -c '"
    'printf "{{\\"path\\":\\"%s\\",\\"home\\":\\"%s\\",\\"pwd\\":\\"%s\\","'
    '"hook_event_name\\":\\"%s\\",\\"claude_project_dir\\":\\"%s\\"}}" '
    '"$PATH" "$HOME" "$PWD" "${{CLAUDE_HOOK_EVENT:-unset}}" '
    '"${{CLAUDE_PROJECT_DIR:-unset}}" > "{out}"'
    "'"
)

# File-IO probe creates a sibling directory + writes a file inside it.
# Tests whether the hook subprocess has filesystem access to a path
# it didn't pre-exist (the analog of writing to ~/.claude/maury-state/).
IO_PROBE_TEMPLATE = '/bin/sh -c \'mkdir -p "{io_dir}" && printf "wrote-from-hook" > "{out}"\''


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


def _build_settings(out_dir: Path) -> dict[str, object]:
    """Construct a Claude Code settings.json that installs the three probe hooks.

    Per Claude Code docs, hooks live under settings["hooks"][<event>] as a list
    of {matcher, hooks: [{type:"command", command:"..."}]} groups. PostToolUse
    fires after every tool invocation, so any tool use by Claude during
    `claude -p` will trigger all three probes.
    """
    comment_out = out_dir / "comment.out"
    env_out = out_dir / "env.out.json"
    io_dir = out_dir / "maury-state-analog"
    io_out = io_dir / "io.out"

    return {
        "hooks": {
            "PostToolUse": [
                {
                    "matcher": "*",
                    "hooks": [
                        {
                            "type": "command",
                            "command": COMMENT_PROBE_TEMPLATE.format(
                                out=comment_out,
                                marker=COMMENT_MARKER,
                            ),
                        },
                        {
                            "type": "command",
                            "command": ENV_PROBE_TEMPLATE.format(out=env_out),
                        },
                        {
                            "type": "command",
                            "command": IO_PROBE_TEMPLATE.format(
                                io_dir=io_dir,
                                out=io_out,
                            ),
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


def _evaluate_env(out_path: Path) -> ProbeResult:
    """What does the hook subprocess see for PATH/HOME/PWD?"""
    if not out_path.exists():
        return ProbeResult(
            name="subprocess_env",
            passed=False,
            detail=f"env probe output not found at {out_path}",
        )
    try:
        captured = json.loads(out_path.read_text())
    except json.JSONDecodeError as exc:
        return ProbeResult(
            name="subprocess_env",
            passed=False,
            detail=f"env probe output is not JSON: {exc}",
            captured={"raw": out_path.read_text()},
        )
    # We pass if we got a non-empty PATH and HOME — the actual values are
    # informational (recorded in `captured`) for the user/ADR to reference.
    path_val = str(captured.get("path", ""))
    home_val = str(captured.get("home", ""))
    if path_val and home_val:
        return ProbeResult(
            name="subprocess_env",
            passed=True,
            detail=("hook subprocess inherited PATH and HOME; details captured for ADR reference."),
            captured=captured,
        )
    return ProbeResult(
        name="subprocess_env",
        passed=False,
        detail=("hook subprocess missing PATH or HOME — absolute-path requirement in ADR-0023 §5 is justified."),
        captured=captured,
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
    settings_dir = workspace / ".claude"
    settings_dir.mkdir(parents=True, exist_ok=True)
    (settings_dir / "settings.json").write_text(json.dumps(_build_settings(out_dir), indent=2))

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
        _evaluate_env(out_dir / "env.out.json"),
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
