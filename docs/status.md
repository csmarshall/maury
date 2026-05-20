# maury — what works today

This is the **calibration page**: a new reader uses it to know
which parts of maury are real today vs. which are designed but
not yet implemented. README and the ADRs describe the *target*
shape; this file describes the *current* shape.

> **Last updated:** 2026-05-19
> **Branch:** `devel` (pre-v0.1, scaffolding stage)

For the authoritative scope split between v1 and v1.1, see
[ADR-0010 amendment](adr/0010-all-three-pillars-v1.md#amendment-2026-05-06-phase-additions--v11-deferrals).
This page is the implementation snapshot; ADR-0010 is the plan.

---

## Commands

| Command | Status | Notes |
|---|---|---|
| `maury init` | ✅ shipped | Bootstrap a host; generates `~/.maury-host-id`, runs capability probe, renders to `~/.claude/`. Per [ADR-0018](adr/0018-minimum-bootstrap-ux.md). |
| `maury render` | ✅ shipped | Compose base + mode chain + rules sublayers + host-tagged sections → target dir. Standalone of `init`/`sync`. |
| `maury sync` | ✅ shipped | Clones/pulls each repo in the host's manifest, renders the base + mode chain + rules + host-tagged sections, runs ADR-0017 drift detection against the `last-render.json` baseline, applies. Three drift modes per ADR-0017: `default` (refuses on drift; points at `maury reconcile`), `--force` (clobbers with loud warning), `--non-interactive` (refuses, exits 1, cron/CI-safe). Identity-guard preflight per [ADR-0042](adr/0042-host-identity-guard.md) refuses when `~/.maury-host-id`'s 8-hex prefix differs from the baseline. Structured auto-merge on the base repo per ADR-0024 (see the `manifest resolve` row below). `--check` produces a dry-run report without writing. |
| `maury rules trace` | ✅ shipped | Dry-run any text against the ruleset; shows which rule matched. |
| `maury manifest show` | ✅ shipped | Display manifest contents. |
| `maury manifest resolve` | ✅ shipped 2026-05-19 | Three-way structured merge engine + interactive resolver. Auto-resolves the additive case, surfaces every other ambiguity with the 5-option prompter (`a`=take A, `b`=take B, `e`=edit by hand via $EDITOR, `s`=skip+keep ancestor, `q`=abort). Profile + host conflicts carry a **semantic summary**. **Author/timestamp/branch metadata** for both sides surfaces at the top of the session. **Edit-by-hand** spawns `$EDITOR` (fallback chain: `$EDITOR`/vim/vi/nano) on a JSON template with the user's `resolution` field; `<deleted>` token signals deletion; null/unparseable/non-zero-exit → skip. Validates the resolved manifest against schema + per-backend URL syntax before atomic write. Gated by `~/.config/maury/.lock` fcntl. `--non-interactive` refuses to prompt. **Auto-merge integration into `maury sync` shipped 2026-05-19**: when `git pull --ff-only` hits a non-FF rejection on a base repo, sync runs `git fetch` + `git merge --no-ff` and calls `try_auto_merge_manifest()`; if the engine resolves cleanly the merge finishes with a `manifest merge: structured auto-resolve` commit, otherwise the working tree is left in conflict state with a pointer at `maury manifest resolve`. Per [ADR-0024](adr/0024-manifest-concurrency-inclusive-merge.md). |
| `maury status` | ✅ shipped | Diagnostic snapshot of this host's maury state — host identity, identity baseline (ADR-0042), repos + git status, last render, drift counts, mining watermarks. JSON or text. Per the 2026-05-16 build. |
| `maury doctor` | ✅ shipped | Evaluate `~/.claude/CLAUDE.md` against the Anthropic best-practices rubric, plus system-health rules (`baseline-missing`, `watermark-stale`) added 2026-05-18 per the two-pronged design call. Per [ADR-0011](adr/0011-anthropic-rubric-integration.md) + [ADR-0042](adr/0042-host-identity-guard.md) + [ADR-0043](adr/0043-incremental-mining.md). |
| `maury mine` | 🟡 partially shipped | Transcript walker, LLM extractor, four-state crossref classification all shipped. **Run-branch generation per [ADR-0022](adr/0022-branch-per-mining-run.md) not yet wired** — current mining produces in-memory findings; the branch-with-commits flow is Phase 6 finalization. |
| `maury review <run-id>` | ⏳ planned | Walk a mining run's commits with cherry-pick UI. Phase 7 per [ADR-0022](adr/0022-branch-per-mining-run.md). |
| `maury promote --from --to` | ⏳ planned | Cross-repo promotion via curator. Phase 9 per [ADR-0045](adr/0045-cross-trust-boundary-promotion.md). |
| `maury focus use <path>` | ⏳ planned (Phase 5.x.d) | Lightweight switch to a child mode within the host's trust boundary (e.g., `personal:consulting:acme`). Refuses if the target would cross a trust boundary (points at `mode deregister` + `mode bootstrap` per ADR-0039) or if active Claude Code sessions are running (override: `--force-active-session`). Updates the `active_focus` field on `host-identity.json`, re-renders `~/.claude/`, emits `focus_switched`. Sister verbs `maury focus current` and `maury focus list` ship same time. Per [ADR-0052](adr/0052-focus-the-lightweight-intra-trust-boundary-mode-switch.md). |
| `maury statusline` | ⏳ planned (Phase 5.x.d) | One-line summary subcommand for Claude Code's `statusLine` setting; `maury init` wires it into `~/.claude/settings.json` by default (`--no-statusline` opts out). Reads `host-identity.json`; outputs `mode:<name>` (no focus set) or `mode:<name> focus:<path>` (focus set). Never crashes, never returns non-zero — a maury bug must not break the user's prompt. Per [ADR-0052](adr/0052-focus-the-lightweight-intra-trust-boundary-mode-switch.md). |
| `maury reconcile` | ✅ shipped | 5-action interactive drift menu (adopt/adapt/mark-managed/revert/skip-once). Claude-write drift menu (3 actions) lands when `claude-writes.jsonl` attribution is wired (Phase 5.x.a slice 5). Per [ADR-0017](adr/0017-drift-detection-and-reconciliation.md). |
| `maury sessions prune` | ✅ shipped 2026-05-18 | Removes events for sessions whose `session_end` is older than `--max-age-hours` (default 24h). Active sessions (no `session_end` yet) are never pruned. Atomic tmp-file rewrite. `--dry-run` previews. Per [ADR-0025](adr/0025-profile-switching-session-safeguards.md). |
| `maury verify-cc-contract` | ✅ shipped 2026-05-18 | Re-fetches every URL in the snapshot's `MANIFEST.txt`, compares SHA256 against the local snapshot, reports drift per URL. `--check-only` lists the manifest without fetching. Exit 1 on any drift/fetch-error. |
| `maury verify-cc-hooks` | ✅ shipped | Empirically verify the three load-bearing Claude Code hook behaviors (comment stripping, subprocess env, file-IO permissions) by spinning up an isolated workspace and invoking `claude -p` against it. Used 2026-05-07 to close out ADR-0023's "Empirical-test debt." |
| `maury verify-cc-projects-dir` | ✅ shipped | Empirically verify the algorithm Claude Code uses to derive `~/.claude/projects/<X>/` from the cwd. Runs `claude -p` against a 14-case corpus (ASCII, punctuation, BMP non-ASCII, non-BMP emoji, plus a collision pair) and asserts `derive_project_dir()` predictions match observation. Promoted `cc-contract:project-directory-derivation` from ❓ to 🧪 on 2026-05-13. Cross-referenced against [anthropics/claude-code#54865](https://github.com/anthropics/claude-code/issues/54865). |
| `maury verify-cc-hook-timing` | ✅ shipped | Empirically verify hook execution ordering, synchronicity, short-circuit-on-exit-2, and default-timeout behavior. Two probes: a combined four-hook ordering test + a sleep-bounded timeout test. Promoted `cc-contract:hook-execution-timing` from ❓ to 🧪 on 2026-05-13. Findings ("ordered fire-and-forget" — neither pure-parallel nor pure-sequential) contradict both upstream documented readings; resolves [anthropics/claude-code#57800](https://github.com/anthropics/claude-code/issues/57800)'s contradiction empirically. ADR-0023 amended 2026-05-14 to formalize eventually-consistent attribution. |
| `maury verify-cc-transcript-schema` | ✅ shipped | Empirically verify the transcript JSONL line schema (fields maury's mining parser consumes — `type`, `message.role`, `message.content`, `sessionId`, `timestamp`). Pure analyzer + real-claude harness; reports two predicates (`all_lines_have_core_fields`, `message_bearing_lines_have_message_fields`). Promoted `cc-contract:transcript-jsonl-stability` from ❓ to 🧪 on 2026-05-18; the ❓ section is now empty. Per [ADR-0044](adr/0044-strict-transcript-parser-and-schema-lock.md). |
| `maury subscribe <url>` | ⏳ planned (v1.1+) | Subscribe to a published profile (gap K). Per planned gap-K ADRs. |
| `maury agency init` | ✅ shipped 2026-05-18 | Generates `agency_id` UUID, writes `.meta/maury-marker.json` with `layer: base`, runs `git init` + initial commit (or `--no-git-init` to skip). Refuses if marker exists; `--force` overwrites with a clear severance warning. Per [ADR-0039](adr/0039-bootstrap-and-host-lifecycle.md) + [ADR-0050](adr/0050-agency-identity.md) + [ADR-0051](adr/0051-marker-file-and-distributed-manifest.md). |
| `maury agency validate` | ✅ shipped 2026-05-18; per-backend URL validation added 2026-05-19 | Canonical name; manifest schema + cross-reference + per-backend URL syntax validation (`github`/`gitlab`/`gitea` host-locked; `git` generic shape check; unknown backends fall through to generic). Marker-file integrity, sublayer-graph cycles, and host-mode mismatches across an agency are planned per [ADR-0040](adr/0040-render-pipeline.md). |
| `maury mode bootstrap` | ✅ shipped 2026-05-19 | Sole CLI form (the pre-release `bootstrap host` alias was removed 2026-05-19: no users exist to deprecate from). v2-flat manifest target; marker-based equivalent lands when ADR-0030's schema migration ships. Per [ADR-0039](adr/0039-bootstrap-and-host-lifecycle.md). |
| `maury mode deregister` | ✅ shipped 2026-05-19 | Removes a host entry from the manifest by name or `host_<hex>` ID. `--check` for dry-run. Per [ADR-0039](adr/0039-bootstrap-and-host-lifecycle.md) — mode change is two operations: deregister then bootstrap, never a single atomic switch. |
| `maury repo init` | ✅ shipped 2026-05-19 | Writes `.meta/maury-governance.json` (owners, pr_target, optional pr_standards/min_reviewers) + `.meta/maury-marker.json` (`layer: rules`, `agency_id`), creates initial semver tag (default `v1.0.0`), installs the auto-patch-bump post-commit hook (ADR-0038 step 6), commits. `--no-hook` skips the hook; `--force` overwrites .meta files + a non-maury post-commit hook. **Owner-only access check** (ADR-0038 §"Why owner-only"): pass `--manifest-file <path>` to enable — refuses unless the current host has `repo_mode: rw` against the target repo's `origin` URL in the manifest. `--no-check-access` opts out. Skipped (with note) when no origin remote or no manifest is provided. Per [ADR-0038](adr/0038-precept-acquisition-model.md). |
| `maury repo bump --major\|--minor` | ✅ shipped 2026-05-19 | Explicit curator action for non-patch bumps (patch bumps are the post-commit hook's job). Reads the highest `v<maj>.<min>.<patch>` tag, applies the bump (`--minor` resets patch to 0; `--major` resets minor + patch), tags HEAD with the new version. Refuses if no baseline tag exists or the target tag is already present. Per [ADR-0038](adr/0038-precept-acquisition-model.md) followup. |
| `maury uninstall` | ✅ shipped 2026-05-18 | Strips `# maury-managed` hook entries from `settings.json`, deletes `~/.claude/bin/maury-*`, `~/.claude/maury-state/`, and `~/.maury-host-id`. User content (CLAUDE.md, hand-authored hooks, repo clones) left intact. Confirmation prompt unless `--yes`. Per [ADR-0023 §8](adr/0023-hook-installation-and-tool-resolution.md). |

Legend: ✅ shipped (works on devel today) · 🟡 partially shipped · ⏳ planned for v1 · ⏳ (v1.1+) deferred

## Core capabilities

| Capability | Status | Notes |
|---|---|---|
| Rule engine + `rules.yaml` schema | ✅ shipped | Phase 1. |
| Manifest schema + validator | ✅ shipped | Phase 2. v2 schema. |
| Capability probe (per-host) | ✅ shipped | Phase 2. |
| Render engine (base + mode chain + rules sublayers + host-tagged sections) | ✅ shipped | Phase 3. Refinement-by-default semantics per [ADR-0019](adr/0019-inheritance-semantics-refine-by-default.md). |
| Bootstrap (`maury init`) | ✅ shipped | Phase 4 first slice. |
| Sync workflow (clone/pull/render/apply) | ✅ shipped | Phase 5 v0. Drift detection is Phase 5.x and not yet wired. |
| `maury doctor` evaluator | ✅ shipped | Phase 2.5. |
| Mining: transcript walk + LLM extraction + four-state crossref | ✅ shipped | Phase 6 a/b/c. Run-branch wiring (Phase 6 finalization) pending. |
| Drift detection (`last-render.json` + reconcile) | 🟡 partially shipped | `src/maury/drift.py` module + sync integration + `maury reconcile`'s 5-action interactive prompter all shipped. Remaining piece: Claude-write drift menu (3 actions) lands when `claude-writes.jsonl` attribution is wired (Phase 5.x.a slice 5). |
| Host-identity guard | ✅ shipped | `host-identity.json` baseline + sync-time hex-check + `--confirm-identity-change` flag + `--reset` flag. Per [ADR-0042](adr/0042-host-identity-guard.md). 2026-05-15. |
| Incremental mining | ✅ shipped | Per-project mtime watermarks (`last-mine.json`) + `--cwd`/`--full`/`--since` flags + cwd-derived default project. Per [ADR-0043](adr/0043-incremental-mining.md). 2026-05-15. |
| `claude-maury-pending` Stop hook | ✅ shipped | Notifies user of pending captures at end of each turn. Per [ADR-0013 §5](adr/0013-active-in-session-capture.md). 2026-05-18. |
| Pre-commit hook for `check-doc-links.py` | ✅ shipped | `.pre-commit-config.yaml`; contributors enable via `pip install pre-commit && pre-commit install`. 2026-05-18. |
| Manifest merge tool + `maury manifest resolve` | ✅ shipped 2026-05-19 | Three-way structured merge engine, interactive resolver, **and** auto-merge integration into `maury sync`. Sync's non-FF recovery (**base repo only** — gated on `.meta/manifest.json` presence): `git fetch` + `git merge --no-ff` + `try_auto_merge_manifest()`; clean merges finish automatically with a `manifest merge: structured auto-resolve` commit, real conflicts leave the working tree in git's merge state with a pointer at `maury manifest resolve`. Per [ADR-0024](adr/0024-manifest-concurrency-inclusive-merge.md). |
| Active-session detection + focus switch | ⏳ planned (Phase 5.x.d) | `~/.claude/maury-state/active-sessions.jsonl` event log + SessionStart/SessionEnd hooks ship per [ADR-0025](adr/0025-profile-switching-session-safeguards.md)'s carried-forward design; the consuming verb is `maury focus use` per [ADR-0052](adr/0052-focus-the-lightweight-intra-trust-boundary-mode-switch.md) (the `maury mode use` design from ADR-0025 was superseded by [ADR-0039](adr/0039-bootstrap-and-host-lifecycle.md) — mode change is now deregister + re-bootstrap, never a single command). |
| Active in-session capture (Claude- + user-initiated) | ⏳ planned (v1 maury-status skill; v1.1 full pipeline) | Per [ADR-0013 amendment](adr/0013-active-in-session-capture.md). |
| Proposal queue + review UI | ⏳ planned (Phase 7) | Per [ADR-0022](adr/0022-branch-per-mining-run.md). |
| Rule synthesis from reclassifications | ⏳ planned (Phase 8) | |
| Cross-boundary promotion | ⏳ planned (Phase 9) | Per [ADR-0045](adr/0045-cross-trust-boundary-promotion.md). |
| Audit log + push-policy enforcement | 🟡 partially shipped 2026-05-19 | Storage primitive `src/maury/audit_log.py` + `maury audit show` reader shipped. Event-kind wiring shipped 2026-05-19 for all V1 command sites: `sync_started` / `sync_completed` / `sync_aborted` / `drift_detected` / `render_applied` (sync), `init_completed` (init), `manifest_mutated` (mode bootstrap + deregister), `manifest_merge_resolved` (manifest resolve), `reconcile_action` (reconcile), `sessions_pruned` (sessions prune), `uninstall_completed` (uninstall). Best-effort: every call site wraps `log()` in `contextlib.suppress(AuditLogError)` so an audit-write failure can't abort the underlying operation. Remaining event kinds (`mode_switched`, `mode_switch_refused`, `manifest_upgraded`, `claude_revert`, `mining_run_created`, `review_completed`, `promotion_started`, `promotion_completed`, `pr_opened`, `subscription_added`, `subscription_pinned`, `backup_created`, `restore_completed`, `tool_use_logged`, `error`) wire up as their owning command sites ship. **Push-policy enforcement** is a separate followup. |
| Pluggable repo backends beyond git/github | ⏳ deferred (v1.1+) | Schema in v1; additional adapters per-need. Per [ADR-0016](adr/0016-pluggable-repo-backends.md). |
| Host-local secrets w/ metadata sync | ⏳ deferred (v1.1) | Per [ADR-0014](adr/0014-host-local-secrets-with-metadata-sync.md). |
| Background drift watcher | ⏳ deferred (v1.1) | Per [ADR-0017 Followups](adr/0017-drift-detection-and-reconciliation.md#followups). |
| `maury refactor promote-common` | ⏳ deferred (v2) | Replacement→refinement migration. Per [ADR-0019 §"Promotion-and-refinement workflow"](adr/0019-inheritance-semantics-refine-by-default.md#promotion-and-refinement-workflow-replacement--refinement-migration). |
| Published/subscribed profiles + `pr` repo mode | ⏳ planned (gap K) | Per planned gap-K ADRs. |

## Documentation

| Doc | Status |
|---|---|
| README.md | ✅ shipped |
| docs/tenets.md | ✅ shipped |
| docs/concepts.md | ✅ shipped (ten core concepts + theoretical foundations; §10 drift+reconcile added 2026-05-18) |
| docs/glossary.md | ✅ shipped (alphabetical quick-lookup, lifted out of concepts.md 2026-05-13) |
| docs/elevator-pitch.md | ✅ shipped (two-minute flyby) |
| docs/quickstart.md | ✅ shipped (≈5-minute try-without-committing walkthrough against `base-template/`) |
| docs/security-model.md | ✅ shipped (three-piece contract operationalizing Tenet 3 + ADR-0041; threat walkthroughs; planned-hardening list) |
| docs/parallel-efforts.md | ✅ shipped (comparison vs. jean-claude / claude-diary / ccms / chezmoi / Anthropic-native; snapshot 2026-05-13) |
| docs/workflow.md | ✅ shipped (4 user-journey diagrams, mermaid) |
| docs/operations.md | ✅ shipped (8 per-command flowcharts, mermaid) |
| docs/claude-code-contract.md | ✅ shipped (9 documented + 5 empirically verified + 0 assumed-but-unverified entries; ❓ section emptied 2026-05-18) |
| docs/claude-code-snapshots/ | ✅ shipped (8 HTML snapshots, MANIFEST with sha256) |
| docs/faq.md | ✅ shipped (9 evaluator-facing questions, each linked to the owning ADR) |
| docs/adr/ (0001-0025) | ✅ shipped |
| ADRs 0026 (mode-aware mining), 0027 (cross-mode promotion via shared root), 0028 (offline behavior), 0029 (maury-state layout contract), 0030 (manifest schema migrations), 0031 (self-update), 0032 (backup + DR), 0033 (`pr` repo mode), 0034 (published/subscribed profiles — use-case framing; mechanics superseded by 0037/0038) | ✅ shipped |
| ADRs 0035 (audit log), 0036 (open-standards alignment), 0037 (layer taxonomy + repo discovery), 0038 (precept acquisition model), 0039 (bootstrap + host lifecycle), 0040 (render pipeline), 0041 (per-mode Anthropic credentials) | ✅ shipped |
| ADRs 0042 (host-identity guard), 0043 (incremental mining), 0044 (strict transcript parser + schema lock — drafted proactively before drift) | ✅ shipped |
| docs/patterns/team-upstream.md (gap K) | ✅ shipped |
| docs/patterns/solo-dev.md | ✅ shipped (single-user mirror of team-upstream.md; recipe for the workstation+laptop+server case) |

## How to read this file alongside the ADRs

- **README** describes the target product.
- **ADRs** describe each architectural decision and its rationale.
- **`docs/concepts.md`** defines the terms.
- **`docs/workflow.md` and `docs/operations.md`** show the flows.
- **This file** says how much of all the above is real today.

If a command appears here as ⏳ planned, it doesn't mean the
design is incomplete — most planned items have full ADRs and
flowcharts. It means the implementation is the bottleneck.

## Empirical-test debt visible to users

The three load-bearing hook claims that previously sat here have
been **resolved** as of 2026-05-07 by running
`maury verify-cc-hooks` on a real macOS host. ADR-0023's marker
scheme, the absolute-path requirement, and the
`~/.claude/maury-state/` write contract are all verified safe.
Re-verify on FreeBSD + Linux when those hosts come online, and
on managed/MDM macOS when first encountered.

See [`docs/claude-code-contract.md` §"Empirically verified behaviors"](claude-code-contract.md#empirically-verified-behaviors)
for the verification records, and ADR-0023's
"Empirical-test debt — RESOLVED" section for what each finding
unblocks.

Other unverified Claude Code behaviors remain in
`claude-code-contract.md` §"Assumed but unverified behaviors" —
none of them block currently-shipping features, but new feature
work that touches them should run the same verify-then-document
loop.
