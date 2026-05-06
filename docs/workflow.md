# maury — Claude Code workflow

How a maury-managed Claude Code setup actually flows over time, in
words and ASCII.

The user has three lifecycles to think about:

1. **One-time:** install + bootstrap.
2. **Daily:** Claude Code sessions, with maury keeping config in sync
   and capturing learnings.
3. **Periodic:** mining, review, and promotion of new content into
   the canonical config.

The diagrams below show each. They use minimal ASCII so they render
the same in the GitHub web UI, in your terminal, and in Markdown
preview.

---

## 1. Install + first-run bootstrap (per host)

```
[fresh host]
     │
     │ install pipx (per docs/adr/0018-...)
     ▼
[pipx + python ready]
     │
     │ pipx install maury        (someday — published)
     │ -- or --
     │ uv sync + uv run maury    (today — clone-from-source)
     ▼
[maury available]
     │
     │ maury init --from-dir <repo>
     │   (or --from-tarball for offline / air-gap)
     ▼
[~/.maury-host-id created]
     │
     │ maury reads .meta/manifest.json
     │ → identifies this host (id-file or hostname fallback)
     │ → walks base + profile chain + host overlay
     │ → renders to ~/.claude/
     ▼
[~/.claude/ populated]
     │
     │ host is registered (in manifest)?
     │   yes → render with the host's assigned profile
     │   no  → write a register-this-host proposal; render proceeds
     │         with default-profile until curator approves on next sync
     ▼
[deploy keys generated for additional repos (per ADR-0018 step 5)]
     │
     │ for each repo in this host's manifest entry:
     │   - generate ssh keypair on this host
     │   - print public key + ask user to add as a deploy key
     │     (gh CLI used automatically when backend=github)
     ▼
[ready to use Claude Code]
```

---

## 2. Daily Claude Code session loop

```
[start session]
     │
     │ Claude Code reads ~/.claude/CLAUDE.md
     │ + skills, agents, settings, hooks
     ▼
[Claude has full maury-managed context]
     │
     │ user works in session
     │ — sometimes user corrects Claude
     │ — sometimes Claude writes via Write/Edit tool
     │ — sometimes user hand-edits a file directly
     ▼
[session ends]
     │
     │ (v1.1) Stop hook fires and invokes
     │   the (v1) maury-status skill, which outputs
     │   "(*) N captures pending; M drift items"
     │   (skill itself ships in v1; the auto-invocation Stop hook is v1.1)
     ▼
[user notices pending work]
     │
     │ maury status (or maury sync)
     ▼
[drift detected vs ~/.claude/maury-state/last-render.json]
     │
     ├──→ Claude-write drift   ──→ soft-accepted, logged
     │       (see maury revert <id>)
     │
     └──→ human hand-edit      ──→ blocking-review:
                                   adopt | adapt | mark-managed | revert | skip-once
                                   (per ADR-0017)
```

---

## 3. Periodic mining + cross-reference + review

```
[user runs maury mine]
     │
     │ walk ~/.claude/projects/<hash>/*.jsonl
     │ filter system-injected pseudo-user content
     ▼
[user-authored messages]
     │
     │ batch into windows (default 50 messages each)
     ▼
[per-window LLM extraction]                ─── via claude -p (default)
     │                                          or anthropic SDK (opt-in)
     │ each window -> JSONL of Findings
     ▼
[Findings: kind / scope_hint / text / evidence / confidence]
     │
     │ if --crossref: rule engine + LLM classify each finding
     │                against current CLAUDE.md (and historical
     │                CLAUDE.md from git when available)
     ▼
[four-state buckets per ADR-0020]
     │
     ├── PRESENT_AND_REINFORCED  (!)  rule exists, Claude wasn't following → investigate
     ├── PRESENT_BUT_UNCLEAR          rule exists, wording suspect → rephrase
     ├── NEW                          not in CLAUDE.md → propose
     └── PRESENT_AND_CLEAR            already promoted → suppress
     │
     │ user runs maury review
     ▼
[per-finding accept / reject / reclassify]
     │
     │ accepted findings → proposals queue
     │ reclassifications → rule-synthesis call;
     │                     proposes a new rule for rules.yaml
     ▼
[proposals/ in synced repo]
     │
     │ if same trust boundary as origin:
     │   commit + push directly
     │ if cross-boundary (work-host insight → base):
     │   land in proposals/promote-to-base/<id>.md
     │   wait for curator on a host with both keys
     ▼
[next maury sync from another host]
     │
     │ git pull
     ▼
[updated CLAUDE.md / skills / rules render to ~/.claude/]
     │
     ▼
[other hosts now have the same updated context — Tenet 2 satisfied]
```

---

## 4. Cross-host promotion (the ADR-0009 case)

```
[work-laptop session]
     │
     │ user expresses a base-worthy preference
     ▼
[mining produces NEW finding tagged scope_hint=base]
     │
     │ work-laptop has rw on maury-work, ro on maury-base
     │ — cannot push to base directly
     ▼
[proposal lands at maury-work:proposals/promote-to-base/<id>.md]
     │
     │ work-laptop pushes to maury-work
     ▼
[<curator host> with rw on both repos]
     │
     │ maury promote-review
     │ → reads proposals/promote-to-base/* across all reachable repos
     ▼
[per-proposal accept / reject]
     │
     │ accepted: copy into maury-base + commit
     │           audit log records who approved + when
     ▼
[maury-base updated]
     │
     │ all hosts pull base on next sync
     ▼
[work-laptop sees the rule on next render — without the work-laptop
 ever having pushed to base]
```

---

## How these tie back to the codebase

| Diagram | Implementing module(s) | Status |
|---|---|---|
| 1. Install + bootstrap | `src/maury/bootstrap/init_cmd.py` | Phase 4 first slice ✅ |
| 2. Daily session loop | `src/maury/render/`, drift detection (Phase 5.x) | render ✅; drift planned |
| 3. Mining + crossref + review | `src/maury/mining/{transcripts,extractor,crossref}.py`; review TBD | extractor + crossref ✅; review Phase 7 |
| 4. Cross-host promotion | promotion module | Phase 9 |

---

## What these diagrams don't show

- **Concurrent access** (two `maury sync` invocations colliding). Not designed yet.
- **The audit log** (Phase 10) — every state-changing event in these diagrams writes an entry.
- **`maury doctor`** ([ADR-0011](adr/0011-anthropic-rubric-integration.md))
  — orthogonal CLAUDE.md health-check evaluating against the Anthropic
  best-practices rubric. User invokes it directly; doesn't fit any of
  these flows.
- **The rule engine** ([ADR-0004](adr/0004-rule-engine-classification.md))
  — implicit in Diagram 3's "four-state buckets" step. The deterministic
  classifier sits between the LLM extractor's output and the proposal
  queue, applying rules from `.meta/rules.yaml`.
- **Active capture** ([ADR-0013](adr/0013-active-in-session-capture.md),
  deferred to v1.1) — would add a "user-in-session ↔ maury-stage skill
  ↔ staging file" loop inside Diagram 2.
- **Secrets** ([ADR-0014](adr/0014-host-local-secrets-with-metadata-sync.md),
  deferred to v1.1) — orthogonal to these flows.
