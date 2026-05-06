#!/usr/bin/env python3
"""Cross-reference prototype: classify a mined finding vs an existing CLAUDE.md.

Per Charles's question — distinguishing "duplicate" from "rule isn't being
followed" matters. The miner needs to put each finding into one of four
states relative to current config:

  NEW                         not in CLAUDE.md at all
  PRESENT_AND_CLEAR           in CLAUDE.md with clear phrasing; suppress
  PRESENT_BUT_UNCLEAR         in CLAUDE.md but wording may be why Claude
                              isn't following it; flag for revision
  PRESENT_AND_REINFORCED      in CLAUDE.md AND user is correcting Claude
                              in the surrounding transcript context;
                              strong signal the rule isn't working

Run:
    uv run python scripts/mining_crossref_prototype.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from string import Template
from textwrap import dedent

CLAUDE_MD = Path.home() / ".claude" / "CLAUDE.md"

CROSSREF_PROMPT = dedent(
    """\
    You are evaluating a single mined preference candidate against a user's
    existing CLAUDE.md. Classify the finding into one of four states.

    The four states:

      NEW                       — The finding's pattern is not represented
                                  anywhere in CLAUDE.md.
      PRESENT_AND_CLEAR         — The pattern IS in CLAUDE.md with clear
                                  phrasing. The user mentioning it again
                                  is just consistent with the rule. Suppress.
      PRESENT_BUT_UNCLEAR       — The pattern IS in CLAUDE.md but the wording
                                  might be ambiguous, buried, or contradicted
                                  elsewhere — possible reason Claude isn't
                                  following it.
      PRESENT_AND_REINFORCED    — The pattern IS in CLAUDE.md, and the
                                  surrounding transcript context shows the
                                  user CORRECTING Claude (telling it to do X
                                  or stop doing Y). Strong signal the rule
                                  exists but isn't being followed.

    Output format: ONE JSON object with this schema, NOTHING ELSE — no
    prose, no markdown:

      {
        "state": "NEW" | "PRESENT_AND_CLEAR" | "PRESENT_BUT_UNCLEAR" | "PRESENT_AND_REINFORCED",
        "claude_md_quote": "<the line(s) from CLAUDE.md that match, or empty>",
        "rationale": "<one sentence on why you picked this state>",
        "suggested_action": "propose-new" | "suppress" | "rephrase-existing" | "investigate-why-not-followed"
      }

    --- USER'S CURRENT CLAUDE.md ---
    $claude_md_text

    --- MINED FINDING ---
    text: $finding_text
    evidence (verbatim quote from transcript): $evidence
    surrounding transcript context:
    $context
    """
)


def call_claude_p(prompt: str, *, timeout: int = 120) -> str:
    proc = subprocess.run(
        ["claude", "-p"],
        input=prompt,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=True,
    )
    return proc.stdout


def evaluate(
    finding_text: str,
    evidence: str,
    context: str,
    *,
    claude_md: Path = CLAUDE_MD,
) -> dict:
    """Run the cross-reference LLM call. Returns the parsed JSON."""
    if not claude_md.is_file():
        raise SystemExit(f"CLAUDE.md not found at {claude_md}")
    md_text = claude_md.read_text(encoding="utf-8")

    prompt = Template(CROSSREF_PROMPT).substitute(
        claude_md_text=md_text,
        finding_text=finding_text,
        evidence=evidence,
        context=context,
    )
    output = call_claude_p(prompt)

    # Find the JSON object in the output
    start = output.find("{")
    end = output.rfind("}")
    if start == -1 or end == -1:
        raise SystemExit(f"could not find JSON in claude output:\n{output}")
    try:
        return json.loads(output[start : end + 1])
    except json.JSONDecodeError as e:
        raise SystemExit(f"invalid JSON: {e}\nraw: {output[start : end + 1]}") from e


def main() -> int:
    # The mined finding from the LLM prototype run earlier.
    finding_text = (
        "When suggesting commands for the user to run, append "
        "`2>&1 | tee <COMMAND_NAME>_$(date +%F-%H%M.%S).log` so output "
        "is captured to a timestamped log; user reports completion by "
        "typing 'done' and Claude reads the latest matching log."
    )
    evidence = (
        "when you tell me commands include a "
        "`2>&1 > | tee <COMMAND_NAME>_$(date +\"%F-%H%M.%S\")` "
        'so when I\'m done I just say "completed" and you\'ll know '
        "what log to review"
    )
    # The surrounding context — assistant gave a command WITHOUT the tee
    # pattern, then user said "from now on, do this." A clear instruction-
    # establishing moment, not a correction of an existing rule.
    context = dedent(
        """\
        [assistant — just before user message] Ready to run:
        sudo python3 tools/xu_capture.py --capture-only --only hdr

        [user — the message that produced the finding] when you tell me
        commands include a `2>&1 > | tee <COMMAND_NAME>_$(date +"%F-%H%M.%S")`
        so when I'm done I just say "completed" and you'll know what log to
        review

        [assistant — immediately after] Got it. Run:
        sudo python3 tools/xu_capture.py --capture-only --only hdr 2>&1
            | tee xu_capture_hdr_$(date +"%F-%H%M.%S").log
        """
    )

    print("Evaluating finding against ~/.claude/CLAUDE.md...")
    result = evaluate(finding_text, evidence, context)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
