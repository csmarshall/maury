# maury — what works today

This is the **calibration page**: a new reader uses it to know
which parts of maury are real today vs. which are designed but
not yet implemented. README and the ADRs describe the *target*
shape; this file describes the *current* shape.

> **Last updated:** 2026-05-07
> **Branch:** `devel` (pre-v0.1, scaffolding stage)

For the authoritative scope split between v1 and v1.1, see
[ADR-0010 amendment](adr/0010-all-three-pillars-v1.md#amendment-2026-05-06-phase-additions--v11-deferrals).
This page is the implementation snapshot; ADR-0010 is the plan.

---

## Commands

| Command | Status | Notes |
|---|---|---|
| `maury init` | ✅ shipped | Bootstrap a host; generates `~/.maury-host-id`, runs capability probe, renders to `~/.claude/`. Per [ADR-0018](adr/0018-minimum-bootstrap-ux.md). |
| `maury render` | ✅ shipped | Compose base + profile chain + host overlay → target dir. Standalone of `init`/`sync`. |
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
| `maury profile use <name>` | ⏳ planned | Switch active profile with safeguards. Phase 5.x per [ADR-0025](adr/0025-profile-switching-session-safeguards.md). |
| `maury reconcile` | ⏳ planned | Drift menu (5 reconcile actions). Phase 5.x per [ADR-0017](adr/0017-drift-detection-and-reconciliation.md). |
| `maury sessions prune` | ⏳ planned | Clean up stale `active-sessions.jsonl` entries. Per [ADR-0025](adr/0025-profile-switching-session-safeguards.md). |
| `maury verify-cc-contract` | ⏳ planned | Re-fetch cited Claude Code docs and diff against `docs/claude-code-snapshots/`. Drift detection for upstream documentation changes. |
| `maury subscribe <url>` | ⏳ planned (v1.1+) | Subscribe to a published profile (gap K). Per planned gap-K ADRs. |
| `maury uninstall` | ⏳ planned | Strip maury hooks/scripts/state from a host. Per [ADR-0023 §8](adr/0023-hook-installation-and-tool-resolution.md). |

Legend: ✅ shipped (works on devel today) · 🟡 partially shipped · ⏳ planned for v1 · ⏳ (v1.1+) deferred

## Core capabilities

| Capability | Status | Notes |
|---|---|---|
| Rule engine + `rules.yaml` schema | ✅ shipped | Phase 1. |
| Manifest schema + validator | ✅ shipped | Phase 2. v2 schema. |
| Capability probe (per-host) | ✅ shipped | Phase 2. |
| Render engine (base + profile chain + host overlay) | ✅ shipped | Phase 3. Refinement-by-default semantics per [ADR-0019](adr/0019-inheritance-semantics-refine-by-default.md). |
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
| `maury refactor promote-common` | ⏳ deferred (v2) | Replacement→refinement migration. Per [ADR-0019 Open questions](adr/0019-inheritance-semantics-refine-by-default.md#open-questions--followups). |
| Published/subscribed profiles + `pr` repo mode | ⏳ planned (gap K) | Per planned gap-K ADRs. |

## Documentation

| Doc | Status |
|---|---|
| README.md | ✅ shipped |
| docs/tenets.md | ✅ shipped |
| docs/concepts.md | ✅ shipped (six core concepts + theoretical foundations) |
| docs/workflow.md | ✅ shipped (4 user-journey diagrams, mermaid) |
| docs/operations.md | ✅ shipped (8 per-command flowcharts, mermaid) |
| docs/claude-code-contract.md | ✅ shipped (9 documented + 5 assumed-but-unverified entries) |
| docs/claude-code-snapshots/ | ✅ shipped (8 HTML snapshots, MANIFEST with sha256) |
| docs/adr/ (0001-0025) | ✅ shipped |
| ADRs 0026 (profile-aware mining), 0027 (cross-context promotion via shared root), 0028 (offline behavior), 0029 (maury-state layout contract), 0030 (manifest schema migrations), 0031 (self-update), 0032 (backup + DR), 0033 (`pr` repo mode), 0034 (published/subscribed profiles) | ✅ shipped |
| Planned: Phase 10 audit log ADR (gap-M, not yet drafted; multiple ADRs reference it) | ⏳ planned |
| docs/patterns/team-upstream.md (gap K) | ⏳ planned |

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

Three load-bearing Claude Code behaviors are documented in
`docs/claude-code-contract.md` but not yet empirically verified:

1. **Hook shell + comment stripping** — load-bearing for ADR-0023's
   `# maury-managed` marker scheme.
2. **Hook subprocess PATH** — drives the absolute-path requirement
   in settings.json.
3. **Hook file-I/O permissions** — load-bearing for `claude-writes.jsonl`
   and `active-sessions.jsonl` (HIGH risk if it fails).

Implementation of any feature that depends on these (drift
detection, profile switching) **must** verify the empirical claims
first. See the `❓ Assumed but unverified behaviors` section of
`claude-code-contract.md`.
