# maury — operations reference

Per-command operational flowcharts. Companion to
[`workflow.md`](workflow.md), which shows the user-journey level;
this file shows what happens *inside* each `maury <command>`.

Diagrams use [mermaid](https://mermaid.js.org/) so they render
cleanly in any modern Markdown viewer (GitHub, GitLab, VS Code,
Obsidian). Terminal viewers fall back to raw mermaid source, which
is still legible for diagnosis.

---

## Index

- [`maury init`](#maury-init)
- [`maury sync`](#maury-sync)
- [`maury reconcile`](#maury-reconcile)
- [`maury mine`](#maury-mine)
- [`maury review <run-id>`](#maury-review-run-id)
- [`maury promote --from --to`](#maury-promote---from---to)
- [`maury profile use <name>`](#maury-profile-use-name)
- [`maury uninstall`](#maury-uninstall)

---

## `maury init`

Bootstrap a host. One-time per host.
See [ADR-0018](adr/0018-minimum-bootstrap-ux.md).

```mermaid
flowchart TD
    Start([maury init --from-dir REPO]) --> Validate[validate inputs]
    Validate --> Load[load source manifest<br/>.meta/manifest.json]
    Load --> Schema{schema version<br/>known?}
    Schema -->|no| FailSchema([error: unknown version])
    Schema -->|yes| HostID{~/.maury-host-id<br/>present?}
    HostID -->|yes| InMan{id in<br/>manifest?}
    InMan -->|yes| Identify[reuse existing entry]
    InMan -->|no| FailStale([error: stale id])
    HostID -->|no| Hostname{hostname<br/>in manifest?}
    Hostname -->|yes| ReuseByName[reuse entry<br/>write ~/.maury-host-id]
    Hostname -->|no| Propose[propose register-this-host commit<br/>continue with default profile]
    Identify --> Probe
    ReuseByName --> Probe
    Propose --> Probe
    Probe[run capability probe] --> WriteCaps[write host overlay<br/>capabilities.json]
    WriteCaps --> Render[render base + profile chain + host overlay<br/>→ ~/.claude/]
    Render --> WriteState[write last-render.json]
    WriteState --> SSH[for each repo beyond base:<br/>gen ssh keypair, print pub key]
    SSH --> Done([done — exit 0 if registered, 2 if pending])
```

---

## `maury sync`

Daily-driver loop. Pull, drift-check, render, apply.
See [ADR-0017](adr/0017-drift-detection-and-reconciliation.md) for
the drift contract.

```mermaid
flowchart TD
    Start([maury sync]) --> Load[load manifest<br/>identify host]
    Load --> ForRepos[for each repo:<br/>git pull --ff-only or git clone]
    ForRepos --> RepoOK{all<br/>repos ok?}
    RepoOK -->|no| FailRepo([abort — record errors])
    RepoOK -->|yes| HasBase{base repo<br/>present?}
    HasBase -->|no| FailBase([error: v0 requires nickname 'base'])
    HasBase -->|yes| Drift[detect drift<br/>last-render SHAs vs disk SHAs]
    Drift --> DriftKind{drift<br/>kind?}
    DriftKind -->|none| RenderStep
    DriftKind -->|claude-write| SoftAccept[soft-accept<br/>maury revert ID available]
    DriftKind -->|hand-edit| Mode{flag<br/>mode?}
    Mode -->|--force| WarnForce[loud warning to stderr]
    Mode -->|--non-interactive| FailNonInt([refuse — exit 1])
    Mode -->|default| Reconcile[invoke reconcile menu<br/>see maury reconcile]
    SoftAccept --> RenderStep
    WarnForce --> RenderStep
    Reconcile --> RenderStep
    RenderStep[render base + profile + host overlay<br/>→ in-memory tree] --> Apply[apply: write iff content differs<br/>settings.json hooks per ADR-0023 marker]
    Apply --> Tools[regenerate ~/.claude/bin/maury-tools.sh]
    Tools --> UpdateState[update last-render.json]
    UpdateState --> Summary([print summary])
```

---

## `maury reconcile`

Drift menu — invoked inline from `maury sync`, or standalone.
Five actions per ADR-0017 §"The five reconcile actions for hand-edits".

```mermaid
flowchart TD
    Start([drift detected on file F]) --> Show[show three views:<br/>last-rendered, current on-disk,<br/>what render would write]
    Show --> Prompt{user picks}
    Prompt -->|adopt| Adopt[capture diff hunk + 3-5 lines context<br/>classify via rule engine<br/>queue as commit on next mine run<br/>local file UNCHANGED]
    Prompt -->|adapt| Adapt[normalize first<br/>then same as adopt]
    Prompt -->|mark-managed| Mark[add path to hand-managed.json<br/>maury never renders here again on this host]
    Prompt -->|revert| Revert[restore F to last-rendered<br/>audit log records the revert]
    Prompt -->|skip-once| Skip[leave F as-is for this sync<br/>resurfaces next time]
    Adopt --> Next
    Adapt --> Next
    Mark --> Next
    Revert --> Next
    Skip --> Next([continue to next drift item, then to render])
```

For Claude-write drift, the menu is shorter: default = soft-accept
silently; `revert` available via `maury revert <id>` /
`maury revert --session <sid>` / `maury revert --last`; `promote`
converts the soft-accept into a proposal (same path as `adopt`).

---

## `maury mine`

Local transcript mining per [ADR-0005](adr/0005-local-only-mining.md).
Produces a run branch per [ADR-0022](adr/0022-branch-per-mining-run.md).

```mermaid
flowchart TD
    Start([maury mine]) --> Walk[walk ~/.claude/projects/HASH/*.jsonl<br/>filter system-injected pseudo-user content]
    Walk --> Batch[batch into windows<br/>default 50 messages per window]
    Batch --> Extract[per-window LLM extraction<br/>claude -p default, anthropic SDK opt-in]
    Extract --> Findings[Findings:<br/>kind / scope_hint / text / evidence / confidence]
    Findings --> Hash[compute Content-Hash per finding<br/>sha256 kind + scope + normalized_text]
    Hash --> Dedup[git log --all --grep Content-Hash<br/>git log --all --grep Rejected-Content-Hash<br/>build set, suppress matches]
    Dedup --> CrossrefQ{--crossref<br/>flag?}
    CrossrefQ -->|no| Branch
    CrossrefQ -->|yes| Crossref[classify each finding vs current<br/>and historical CLAUDE.md per ADR-0020]
    Crossref --> CrossrefDrop[drop PRESENT_AND_CLEAR<br/>already promoted]
    CrossrefDrop --> Branch[create branch maury/run/RUN-ID off main]
    Branch --> Commit[for each surviving finding:<br/>compute proposed change<br/>commit with structured trailers per ADR-0022]
    Commit --> Done([wrote N commits — run maury review RUN-ID])
```

---

## `maury review <run-id>`

Walk the run branch's commits with a curator. Per ADR-0022.

```mermaid
flowchart TD
    Start([maury review RUN-ID]) --> CheckMain{main moved<br/>since branch?}
    CheckMain -->|yes| FailRebase([refuse — run maury rebase-run RUN-ID first])
    CheckMain -->|no| MakeReview[create branch<br/>maury/review/RUN-ID off main]
    MakeReview --> Walk[walk commits oldest-first<br/>git log main..maury/run/RUN-ID]
    Walk --> ForEach[for each commit C:<br/>show diff and parsed trailers]
    ForEach --> Choice{user picks}
    Choice -->|accept| Pick[git cherry-pick C onto review branch]
    Pick --> Conflict{conflict?}
    Conflict -->|yes| Reprompt[re-prompt with conflict context<br/>user picks one or merges by hand]
    Conflict -->|no| NextCommit
    Reprompt --> NextCommit
    Choice -->|edit| EditOpen[open editor on diff<br/>cherry-pick edited version]
    EditOpen --> NextCommit
    Choice -->|reject| Reject[record Content-Hash<br/>in pending-rejections list]
    Reject --> NextCommit
    Choice -->|skip| Skip[leave for next session<br/>recorded in run-state]
    Skip --> NextCommit
    NextCommit{more<br/>commits?}
    NextCommit -->|yes| ForEach
    NextCommit -->|no| RejNonEmpty{any<br/>rejections?}
    RejNonEmpty -->|yes| RejCommit[append no-op metadata commit:<br/>Original-Run / Curator-Host /<br/>Rejected-Content-Hash * N]
    RejNonEmpty -->|no| MergePrompt
    RejCommit --> MergePrompt{merge<br/>now?}
    MergePrompt -->|yes| Merge[git merge --no-ff<br/>or push + open PR via gh]
    MergePrompt -->|no| LeaveBranch([leave branch in place — user merges later])
    Merge --> Cleanup([delete maury/run/RUN-ID<br/>keep maury/review/RUN-ID until pruned])
```

---

## `maury promote --from --to`

Cross-repo promotion (e.g., work → base). Curator host has rw on
both. Per [ADR-0009](adr/0009-promotion-only-cross-boundary.md) +
ADR-0022.

```mermaid
flowchart TD
    Start([maury promote --from SRC --to DST]) --> Verify{rw on dst,<br/>read on src?}
    Verify -->|no| FailAuth([error — manifest check failed])
    Verify -->|yes| Fetch[fetch src repo's maury/run/* branches<br/>into local read-only mirror]
    Fetch --> Review[same review UI as maury review<br/>sourced from src branches]
    Review --> ForAccept[for each accepted commit:<br/>cherry-pick onto maury/promoted/ID in dst<br/>add Promoted-From: SRC-REPO@SHA<br/>PRESERVE original Content-Hash]
    ForAccept --> ForReject{any<br/>rejections?}
    ForReject -->|yes| RejCommit[no-op rejection commit on promoted branch<br/>dst's dedup index covers cross-repo rejections]
    ForReject -->|no| MergeQ
    RejCommit --> MergeQ{merge<br/>now?}
    MergeQ -->|yes| Merge[git merge to dst's main]
    MergeQ -->|no| Leave([leave promoted branch for later])
    Merge --> Done([src repo unchanged — work host gets no callback by design])
```

---

## `maury profile use <name>`

Switch the host's active profile. Re-renders. Hook teardown is
implicit via the marker scheme.

```mermaid
flowchart TD
    Start([maury profile use NAME]) --> Validate{NAME is a<br/>known profile?}
    Validate -->|no| Fail([error — unknown profile])
    Validate -->|yes| UpdateOverlay[update host overlay<br/>old profile inactive, new profile active]
    UpdateOverlay --> Render[invoke render pipeline]
    Render --> RewriteSettings[settings.json hooks block:<br/>preserve non-marked hooks<br/>replace # maury-managed hooks with new profile's set]
    RewriteSettings --> RegenTools[regenerate ~/.claude/bin/maury-tools.sh<br/>tool catalog may differ between profiles]
    RegenTools --> RewriteCLAUDE[~/.claude/CLAUDE.md replaced with new chain output]
    RewriteCLAUDE --> UpdateState[update last-render.json]
    UpdateState --> Push[commit + push manifest change to profile repo<br/>so peer hosts see the switch]
    Push --> Done([restart Claude Code to pick up new hooks])
```

---

## `maury uninstall`

Clean removal. Reversible up to the point of running it (then
re-run `maury init` to restore). Per ADR-0023 §8.

```mermaid
flowchart TD
    Start([maury uninstall]) --> Confirm{user<br/>confirms?}
    Confirm -->|no| Abort([cancelled])
    Confirm -->|yes| StripHooks[strip every # maury-managed entry<br/>from settings.json hooks block<br/>user hooks untouched]
    StripHooks --> DelBin[delete ~/.claude/bin/maury-*<br/>and ~/.claude/bin/maury-tools.sh]
    DelBin --> DelState[delete ~/.claude/maury-state/<br/>last-render, claude-writes, watermarks]
    DelState --> DelID[delete ~/.maury-host-id]
    DelID --> Done([print: removed maury hooks<br/>left N user hooks intact<br/>clones at REPOS_ROOT not touched])
```

The pip/uv-installed `maury` CLI itself is not removed; that's a
package-manager operation (`pipx uninstall maury` or
`uv pip uninstall maury`).

---

## What these diagrams don't show

- **Audit log writes** (per Phase 10) — every state-changing branch
  in these flows appends to the local audit log. Omitted from
  every diagram for brevity.
- **Manifest version checks** — every command that loads the
  manifest validates the schema version and refuses unknown
  versions. Implicit in the "load manifest" step.
- **Concurrency** — two `maury sync` invocations on the same host
  racing each other. Not yet designed; documented as a v1.1 gap.
- **Network failures during git ops** — `git pull` / `git clone`
  failures bubble up as errors and are surfaced in the per-repo
  result; the diagrams show only the happy path's downstream steps.
- **Capability-failure render refusal** — per ADR-0006, a hook
  whose required capability is absent causes render to fail loud.
  The diagrams show the success path; failure surfaces as
  "render failed: <reason>" before the apply step.

## Convention

Flowcharts use mermaid `flowchart TD` (top-down). If we ever need
true sequence diagrams (multiple actors over time), use mermaid
`sequenceDiagram` for those — but flowcharts always stay
`flowchart TD` for visual consistency across the project.
