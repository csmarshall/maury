# maury — what works today

This is the **calibration page**: a new reader uses it to know
which parts of maury are real today vs. which are designed but
not yet implemented. README and the ADRs describe the *target*
shape; this file describes the *current* shape.

> **Last updated:** 2026-05-13
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
| `maury sync` | 🟡 partially shipped | v0 slice: clones/pulls each repo in the host's manifest, renders, applies. **Drift detection not yet wired** ([ADR-0017](adr/0017-drift-detection-and-reconciliation.md) Phase 5.x); running on a real `~/.claude/` is hazardous until that lands. The `--check` flag is the safe way to inspect today. |
| `maury rules trace` | ✅ shipped | Dry-run any text against the ruleset; shows which rule matched. |
| `maury manifest validate` | ✅ shipped | Validate manifest schema. |
| `maury manifest show` | ✅ shipped | Display manifest contents. |
| `maury manifest resolve` | ⏳ planned | Interactive merge of concurrent manifest edits. Per [ADR-0024](adr/0024-manifest-concurrency-inclusive-merge.md). |
| `maury manifest upgrade-v1-to-v2` | ⏳ planned | One-shot manifest schema migration. Per [ADR-0015](adr/0015-surrogate-keys-for-hosts-and-profiles.md). |
| `maury doctor` | ✅ shipped | Evaluate `~/.claude/CLAUDE.md` against the Anthropic best-practices rubric. Per [ADR-0011](adr/0011-anthropic-rubric-integration.md). |
| `maury mine` | 🟡 partially shipped | Transcript walker, LLM extractor, four-state crossref classification all shipped. **Run-branch generation per [ADR-0022](adr/0022-branch-per-mining-run.md) not yet wired** — current mining produces in-memory findings; the branch-with-commits flow is Phase 6 finalization. |
| `maury review <run-id>` | ⏳ planned | Walk a mining run's commits with cherry-pick UI. Phase 7 per [ADR-0022](adr/0022-branch-per-mining-run.md). |
| `maury promote --from --to` | ⏳ planned | Cross-repo promotion via curator. Phase 9 per [ADR-0009](adr/0009-promotion-only-cross-boundary.md). |
| `maury mode use <name>` | ⏳ planned | Switch active mode with safeguards. Phase 5.x per [ADR-0025](adr/0025-profile-switching-session-safeguards.md). |
| `maury reconcile` | ⏳ planned | Drift menu (5 reconcile actions). Phase 5.x per [ADR-0017](adr/0017-drift-detection-and-reconciliation.md). |
| `maury sessions prune` | ⏳ planned | Clean up stale `active-sessions.jsonl` entries. Per [ADR-0025](adr/0025-profile-switching-session-safeguards.md). |
| `maury verify-cc-contract` | ⏳ planned | Re-fetch cited Claude Code docs and diff against `docs/claude-code-snapshots/`. Drift detection for upstream documentation changes. |
| `maury verify-cc-hooks` | ✅ shipped | Empirically verify the three load-bearing Claude Code hook behaviors (comment stripping, subprocess env, file-IO permissions) by spinning up an isolated workspace and invoking `claude -p` against it. Used 2026-05-07 to close out ADR-0023's "Empirical-test debt." |
| `maury verify-cc-projects-dir` | ✅ shipped | Empirically verify the algorithm Claude Code uses to derive `~/.claude/projects/<X>/` from the cwd. Runs `claude -p` against a 14-case corpus (ASCII, punctuation, BMP non-ASCII, non-BMP emoji, plus a collision pair) and asserts `derive_project_dir()` predictions match observation. Promoted `cc-contract:project-directory-derivation` from ❓ to 🧪 on 2026-05-13. Cross-referenced against [anthropics/claude-code#54865](https://github.com/anthropics/claude-code/issues/54865). |
| `maury subscribe <url>` | ⏳ planned (v1.1+) | Subscribe to a published profile (gap K). Per planned gap-K ADRs. |
| `maury agency init` | ⏳ planned | Bootstrap a new agency: generate `agency_id` UUID, create the first `base` repo with the right marker. Per [ADR-0039](adr/0039-bootstrap-and-host-lifecycle.md). |
| `maury agency validate` | ⏳ planned | Validate marker-file integrity, sublayer-graph cycles, host-mode mismatches across an agency. Per [ADR-0040](adr/0040-render-pipeline.md). |
| `maury mode bootstrap` | ⏳ planned | Register the current host into a specified mode (counterpart to deregister). Per [ADR-0039](adr/0039-bootstrap-and-host-lifecycle.md). |
| `maury mode deregister` | ⏳ planned | Retire the host's current mode registration (counterpart to bootstrap). Per [ADR-0039](adr/0039-bootstrap-and-host-lifecycle.md). Mode change is two operations: deregister then bootstrap, never a single atomic switch. |
| `maury repo init` | ⏳ planned | Initialize a new `rules` repo with marker + semver baseline + post-commit tagging hook. Per [ADR-0038](adr/0038-precept-acquisition-model.md). |
| `maury uninstall` | ⏳ planned | Strip maury hooks/scripts/state from a host. Per [ADR-0023 §8](adr/0023-hook-installation-and-tool-resolution.md). |

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
| Drift detection (`last-render.json` + reconcile) | ⏳ planned (Phase 5.x.a) | Module written on disk (`src/maury/drift.py`) but uncommitted; needs tests + CLI integration. |
| Manifest merge tool + `maury manifest resolve` | ⏳ planned (Phase 5.x.b) | Per [ADR-0024](adr/0024-manifest-concurrency-inclusive-merge.md). |
| Active-session detection + profile switch | ⏳ planned (Phase 5.x.c) | Per [ADR-0025](adr/0025-profile-switching-session-safeguards.md). |
| Active in-session capture (Claude- + user-initiated) | ⏳ planned (v1 maury-status skill; v1.1 full pipeline) | Per [ADR-0013 amendment](adr/0013-active-in-session-capture.md). |
| Proposal queue + review UI | ⏳ planned (Phase 7) | Per [ADR-0022](adr/0022-branch-per-mining-run.md). |
| Rule synthesis from reclassifications | ⏳ planned (Phase 8) | |
| Cross-boundary promotion | ⏳ planned (Phase 9) | Per [ADR-0009](adr/0009-promotion-only-cross-boundary.md). |
| Audit log + push-policy enforcement | ⏳ planned (Phase 10) | |
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
| docs/concepts.md | ✅ shipped (nine core concepts + theoretical foundations) |
| docs/glossary.md | ✅ shipped (alphabetical quick-lookup, lifted out of concepts.md 2026-05-13) |
| docs/elevator-pitch.md | ✅ shipped (two-minute flyby) |
| docs/quickstart.md | ✅ shipped (≈5-minute try-without-committing walkthrough against `base-template/`) |
| docs/security-model.md | ✅ shipped (three-piece contract operationalizing Tenet 3 + ADR-0041; threat walkthroughs; planned-hardening list) |
| docs/parallel-efforts.md | ✅ shipped (comparison vs. jean-claude / claude-diary / ccms / chezmoi / Anthropic-native; snapshot 2026-05-13) |
| docs/workflow.md | ✅ shipped (4 user-journey diagrams, mermaid) |
| docs/operations.md | ✅ shipped (8 per-command flowcharts, mermaid) |
| docs/claude-code-contract.md | ✅ shipped (9 documented + 4 empirically verified + 2 assumed-but-unverified entries) |
| docs/claude-code-snapshots/ | ✅ shipped (8 HTML snapshots, MANIFEST with sha256) |
| docs/adr/ (0001-0025) | ✅ shipped |
| ADRs 0026 (mode-aware mining), 0027 (cross-mode promotion via shared root), 0028 (offline behavior), 0029 (maury-state layout contract), 0030 (manifest schema migrations), 0031 (self-update), 0032 (backup + DR), 0033 (`pr` repo mode), 0034 (published/subscribed profiles — use-case framing; mechanics superseded by 0037/0038) | ✅ shipped |
| ADRs 0035 (audit log), 0036 (open-standards alignment), 0037 (layer taxonomy + repo discovery), 0038 (precept acquisition model), 0039 (bootstrap + host lifecycle), 0040 (render pipeline), 0041 (per-mode Anthropic credentials) | ✅ shipped |
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
