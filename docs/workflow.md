# maury — Claude Code workflow

How a maury-managed Claude Code setup actually flows over time, in
words and diagrams.

The user has three lifecycles to think about:

1. **One-time:** install + bootstrap.
2. **Daily:** Claude Code sessions, with maury keeping config in sync
   and capturing learnings.
3. **Periodic:** mining, review, and promotion of new content into
   the canonical config.

The diagrams below show each. They use [mermaid](https://mermaid.js.org/)
so they render cleanly in any modern Markdown viewer (GitHub, GitLab,
VS Code, Obsidian). Terminal viewers fall back to raw mermaid source.

For per-command operational detail (what happens *inside* each
`maury <command>`), see [`operations.md`](operations.md).

---

## 1. Install + first-run bootstrap (per host)

```mermaid
flowchart TD
    Fresh([fresh host]) --> Pipx[install pipx<br/>per ADR-0018]
    Pipx --> Ready[pipx + python ready]
    Ready --> Install{install<br/>method?}
    Install -->|published| Pip[pipx install maury<br/>someday]
    Install -->|source| UV[uv sync + uv run maury<br/>today]
    Pip --> Avail[maury available]
    UV --> Avail
    Avail --> Init[maury init --from-dir REPO<br/>or --from-tarball for offline / air-gap]
    Init --> HostID[~/.maury-host-id created]
    HostID --> Read[maury reads .meta/maury-marker.json<br/>identifies host: id-file or hostname fallback<br/>walks base + mode chain + sublayers<br/>renders to ~/.claude/]
    Read --> Populated[~/.claude/ populated]
    Populated --> Reg{host<br/>registered?}
    Reg -->|yes| Assigned[render with host's assigned profile]
    Reg -->|no| ProposeReg[write register-this-host proposal<br/>render proceeds with default-profile]
    Assigned --> Keys[deploy keys for additional repos<br/>per ADR-0018 step 5]
    ProposeReg --> Keys
    Keys --> KeysLoop[for each repo in this host's mode chain:<br/>generate ssh keypair<br/>print public key for deploy-key add<br/>gh CLI used when backend=github]
    KeysLoop --> Done([ready to use Claude Code])
```

---

## 2. Daily Claude Code session loop

```mermaid
flowchart TD
    Start([start session]) --> Read[Claude Code reads ~/.claude/CLAUDE.md<br/>+ skills, agents, settings, hooks]
    Read --> Context[Claude has full maury-managed context]
    Context --> Work[user works in session]
    Work --> Edits[sometimes user corrects Claude<br/>sometimes Claude writes via Write/Edit<br/>sometimes user hand-edits a file directly]
    Edits --> End([session ends])
    End --> Hook{Stop hook<br/>v1.1?}
    Hook -->|yes| Status["invoke maury-status skill<br/>'N captures pending; M drift items'"]
    Hook -->|no| Manual
    Status --> Manual[user notices pending work]
    Manual --> RunSync[maury status or maury sync]
    RunSync --> Drift[drift detected vs<br/>~/.claude/maury-state/last-render.json]
    Drift --> Kind{drift<br/>kind?}
    Kind -->|claude-write| Soft[soft-accepted, logged<br/>maury revert ID available]
    Kind -->|hand-edit| Menu[blocking-review:<br/>adopt / adapt / mark-managed / revert / skip-once<br/>per ADR-0017]
    Soft --> Sync2([next maury sync])
    Menu --> Sync2
```

The skill itself ships in v1; the auto-invocation Stop hook is v1.1.

---

## 3. Periodic mining + cross-reference + review

> The four-state bucket names (`NEW`, `PRESENT_AND_CLEAR`,
> `PRESENT_BUT_UNCLEAR`, `PRESENT_AND_REINFORCED`) shown in the
> diagram below are **owned by [ADR-0020](adr/0020-two-mining-modes-bulk-and-incremental.md)**.
> If those names change there, this diagram becomes stale — that
> ADR is the source of truth.

```mermaid
flowchart TD
    Start([user runs maury mine]) --> Walk[walk ~/.claude/projects/HASH/*.jsonl<br/>filter system-injected pseudo-user content]
    Walk --> Auth[user-authored messages]
    Auth --> Batch[batch into windows<br/>default 50 messages each]
    Batch --> Extract[per-window LLM extraction<br/>via claude -p default<br/>or anthropic SDK opt-in]
    Extract --> Findings[Findings:<br/>kind / scope_hint / text / evidence / confidence]
    Findings --> CR{--crossref<br/>flag?}
    CR -->|no| Branch
    CR -->|yes| Classify[rule engine + LLM classify each finding<br/>vs current and historical CLAUDE.md]
    Classify --> Buckets{four-state<br/>per ADR-0020}
    Buckets -->|PRESENT_AND_REINFORCED| Investigate[rule exists, Claude wasn't following<br/>investigate]
    Buckets -->|PRESENT_BUT_UNCLEAR| Rephrase[rule exists, wording suspect<br/>rephrase]
    Buckets -->|NEW| Propose[not in CLAUDE.md → propose]
    Buckets -->|PRESENT_AND_CLEAR| Suppress[already promoted → suppress]
    Investigate --> Branch
    Rephrase --> Branch
    Propose --> Branch
    Branch[create branch maury/run/RUN-ID with<br/>one commit per finding per ADR-0022] --> Review[user runs maury review RUN-ID]
    Review --> Walk2[walk commits oldest-first<br/>show diff + parsed trailers<br/>accept / reject / edit / skip]
    Walk2 --> Cherry[cherry-pick accepted commits<br/>onto maury/review/RUN-ID]
    Cherry --> RejCommit[append no-op rejection commit<br/>with Rejected-Content-Hash trailers]
    RejCommit --> Merge[merge maury/review/RUN-ID to main<br/>local merge or PR]
    Merge --> Sync[other hosts pull on next maury sync]
    Sync --> Done([all hosts have the same updated context — Tenet 2 satisfied])
```

---

## 4. Cross-host promotion (the ADR-0009 case)

```mermaid
flowchart TD
    Start([work-laptop session]) --> Express[user expresses base-worthy preference]
    Express --> Mine[mining produces NEW finding<br/>tagged scope_hint=base]
    Mine --> Constraint[work-laptop has rw on maury-work, ro on maury-base<br/>cannot push to base directly]
    Constraint --> RunBranch[finding lands as commit on<br/>maury/run/RUN-ID in maury-work]
    RunBranch --> Push[work-laptop pushes maury-work]
    Push --> Curator[curator host with rw on both repos]
    Curator --> Promote[maury promote --from maury-work --to maury-base<br/>walks src branches with same review UI]
    Promote --> Decide{accept or<br/>reject per finding}
    Decide -->|accept| Cherry[cherry-pick onto maury/promoted/ID in maury-base<br/>add Promoted-From trailer<br/>preserve Content-Hash]
    Decide -->|reject| RejCommit[no-op rejection commit<br/>so maury-base's dedup index covers it]
    Cherry --> Merge[merge maury/promoted/ID to maury-base main]
    RejCommit --> Merge
    Merge --> AllPull[all hosts pull base on next sync]
    AllPull --> Done([work-laptop sees the rule on next render —<br/>without ever having pushed to base])
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
