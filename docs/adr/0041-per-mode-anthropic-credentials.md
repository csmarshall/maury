# ADR-0041: Per-mode Anthropic credentials and the LLM trust boundary

**Status:** Accepted
**Date:** 2026-05-13

## Related tenets

- [Tenet 3 — Trust boundaries are physical, not policy](../tenets.md#3-trust-boundaries-are-physical-not-policy)
- [Tenet 4 — Sensitive data stays local](../tenets.md#4-sensitive-data-stays-local)
- [Tenet 5 — The user arbitrates ambiguity](../tenets.md#5-the-user-arbitrates-ambiguity)
- [Tenet 11 — Explicit beats implicit, with conservative defaults](../tenets.md#11-explicit-beats-implicit-with-conservative-defaults)

## Related ADRs

- [ADR-0002](0002-repo-per-trust-boundary.md) — one repo per trust boundary (structural isolation)
- [ADR-0003](0003-per-host-deploy-keys.md) — per-host SSH deploy keys (the structural enforcement layer)
- [ADR-0005](0005-local-only-mining.md) — local-only mining; the operation that introduces the LLM trust boundary
- [ADR-0012](0012-llm-backend.md) — LLM backend abstraction; default `claude -p`, opt-in Anthropic SDK
- [ADR-0025](0025-profile-switching-session-safeguards.md) — mode-switch session safeguards (closest sibling: also an operational discipline around mode transitions)
- [ADR-0037](0037-layer-taxonomy-and-repo-discovery.md) — mode as the unit of "what the user is doing"

## TL;DR

Maury's structural trust boundary (per-host SSH deploy keys per
[ADR-0003](0003-per-host-deploy-keys.md)) prevents repos from crossing
boundaries between hosts. It says nothing about the LLM trust boundary
that mining ([ADR-0005](0005-local-only-mining.md)) reuses every time
the rule engine asks an LLM to classify a candidate fragment. This
ADR adds a third piece to the mode trust-boundary contract:
**per-mode Anthropic credentials.** The user must maintain
account-per-mode discipline; mining in `mode:work` must run with a
work Anthropic account, and mining in `mode:home` must run with a
personal account. Maury **does not** structurally enforce this today
— Claude Code's auth model is single-account-per-install — so the
contract is **operational, not structural**, and the doc set must
name it explicitly. Anthropic's own [Workspaces model][cc-workspaces]
is the canonical server-side equivalent. A future ADR may add
maury-side detection of account-mode mismatch.

## Context and Problem Statement

Maury's structural isolation story rests on
[ADR-0002](0002-repo-per-trust-boundary.md) (one repo per trust
boundary) plus [ADR-0003](0003-per-host-deploy-keys.md) (per-host SSH
deploy keys). Together they make cross-boundary content access a
physical impossibility — a work laptop does not hold the deploy key
for the personal-mode repo, so the git provider refuses to serve
those bytes to that host. This is the load-bearing safety property
behind [Tenet 3](../tenets.md#3-trust-boundaries-are-physical-not-policy).

But [ADR-0005](0005-local-only-mining.md)'s mining flow is the one
operation in maury that **deliberately** crosses a network boundary.
Mining walks `~/.claude/projects/<X>/*.jsonl`, extracts candidate
fragments, and uses the LLM backend
([ADR-0012](0012-llm-backend.md) — default `claude -p`, opt-in
Anthropic SDK) to classify them. By design, fragments from a host's
transcripts leave the host on their way to Anthropic's API for
classification.

If the active Anthropic account on a work laptop is the user's
**personal** Anthropic account (a real failure mode — see Claude
Code [#377][cc-issue-377] and [#7210][cc-issue-7210] for the
same-email confusion bug, plus the general single-account-per-install
constraint), then:

1. The work-mode transcripts get mined against the personal account.
2. Anthropic's API logs / data-handling contract for that exchange
   is the **personal-account** contract, not the work-account
   contract.
3. The work-vs-personal trust boundary maury otherwise maintains is
   silently bypassed for this one operation, with no maury-visible
   evidence the leak occurred.

This is the gap the three-piece contract closes.

The structural-vs-operational distinction matters: maury's mode
isolation can only be as strong as the weakest of the three pieces,
and one of them (the user's discipline about Claude Code's active
account) is outside maury's enforcement reach. Pretending otherwise
would be exactly the kind of unstated assumption Tenet 11 forbids.

## Decision

### The mode trust-boundary contract has three pieces, not two

```
mode isolation holds when:
  ├─ per-host SSH deploy keys per ADR-0003       [STRUCTURAL]
  ├─ per-mode Anthropic credentials              [OPERATIONAL]
  └─ user keeps the right Claude Code account
     active for the mode being worked in         [OPERATIONAL]
```

Piece 1 is enforced server-side by the git provider. Pieces 2 and 3
are operational disciplines the user maintains. The security model
fails if any of the three breaks.

### Maury does not structurally enforce per-mode credentials today

[Claude Code][cc-overview]'s auth model is **empirically**
single-account-per-install on a given machine — there is no
documented multi-account-switching contract in the current Claude
Code docs, and the same-email confusion bugs ([#377][cc-issue-377],
[#7210][cc-issue-7210]) reinforce that the current model is one
active account at a time, switched via `claude` sign-in /
sign-out flows. There is no documented mechanism for maury to
gate a `claude -p` invocation on "this account matches the mode
I'm mining in." Detecting an account mismatch is theoretically
possible (the Claude Code session probably exposes the active
account in metadata somewhere) but no current ADR specifies how,
and Anthropic has not published a stable contract for reading it.

So maury's current posture is:

- **Document the contract explicitly.** This ADR + the planned
  security-model doc are the user-facing surface.
- **Recommend the discipline at every reasonable surface.**
  Bootstrap warnings, mode-change advisories, and `maury status`
  output are candidates for nudging the user toward
  account-per-mode hygiene.
- **Do not silently pretend the structural enforcement extends to
  the LLM boundary.** A user who runs `maury mine` with the wrong
  account active gets the documented contract's failure mode, not
  a silent surprise.

### Anthropic's Workspaces are the canonical server-side equivalent

Per Anthropic's [Workspaces documentation][cc-workspaces], a Claude
Console organization can host multiple workspaces, each scoping its
own API keys, members, rate limits, and spend limits, plus the
resources (Files, Message Batches, Skills, prompt caches) created
via those API keys. Workspaces provide a way to "separate different
projects, environments, or teams while maintaining centralized
billing and administration," with documented use cases including
environment separation (development/staging/production) and team
or department isolation. The maury mode trust boundary maps directly
onto an Anthropic workspace boundary: one mode == one workspace at
the credential level.

For a user running maury with both work and personal modes, the
recommended setup is:

- A **work Anthropic account** (or a work workspace inside a shared
  Anthropic organization) used as the active Claude Code identity
  when working in `mode:work` and its descendants.
- A **personal Anthropic account** (or personal workspace) used
  when working in `mode:home` and its descendants.
- Switching between modes is accompanied by switching the active
  Claude Code account.

For commercial / enterprise users, Anthropic's Commercial Terms of
Service plus the Data Processing Addendum (see [Anthropic's Privacy
Center][cc-privacy]) explicitly prohibit training on inputs. Per the
[Claude Code data-usage documentation][cc-data-usage], commercial
users (Team, Enterprise, API) get a **30-day standard retention
period** for Claude Code data, with [Zero Data Retention][cc-zdr]
available per-organization for Claude Code on Claude for Enterprise.
These are the data-handling guarantees that make a properly-
credentialed work-mode mining run safe-by-default.

### What this ADR does NOT specify

- **Maury-side credential verification.** Detecting "the active
  Claude Code account doesn't match what this mode expects" is a
  candidate future feature (planned hardening — see Followups). It
  requires either a stable Claude Code API for reading the active
  account, or a coupling to specific Claude Code versions. Out of
  scope for this ADR.
- **A maury-side credential store.** Per
  [ADR-0014](0014-host-local-secrets-with-metadata-sync.md), maury
  does not aspire to be a secret manager. The Anthropic credential
  lives in Claude Code's own auth state; maury's role is to
  document the contract, not to vault the creds.
- **Local-LLM alternatives that sidestep the boundary entirely.**
  [ADR-0012](0012-llm-backend.md)'s pluggable backend interface
  would in principle let a user run mining against a local model
  (no network boundary at all), but no local adapter ships today.
  When one does, it bypasses this whole concern for users who
  choose it.
- **Anthropic's internal authentication choices.** Whether users
  prefer SSO-via-WorkOS, API-keys-per-workspace, or any other
  Anthropic-internal mechanism is between the user and Anthropic.
  Maury commits to the trust-boundary statement; the credential
  mechanics live with the LLM vendor.

### Consequences

- ✅ **Good:** The trust-boundary contract is now explicit and
  testable. A user evaluating maury for work-use can read this ADR
  + the planned security-model doc and know exactly what's
  structurally enforced vs. operationally maintained.
- ✅ **Good:** Anthropic's existing Workspaces model is a clean
  server-side analog. Users following Anthropic's published
  organizational-account best practices automatically satisfy
  maury's per-mode-credential contract; no new abstraction to
  learn.
- ✅ **Good:** Honest about a real failure mode. Pretending the
  structural isolation extends to the LLM boundary would have been
  a Tenet 11 violation.
- ❌ **Bad:** A new operational discipline lands on the user. Bad
  account/mode hygiene results in a silent contract breach maury
  cannot today detect.
- ❌ **Bad:** Same-email personal-vs-organizational Claude Code
  bugs ([#377][cc-issue-377], [#7210][cc-issue-7210]) make the
  discipline harder than it should be. Some friction is upstream.
- ⚖️ **Neutral:** Local-LLM-only deployments per
  [ADR-0012](0012-llm-backend.md)'s pluggable interface sidestep
  this concern; once a local adapter ships, users who opt into it
  shrink the contract to two pieces again.

### Confirmation

This ADR's "Decision" is implemented when:

1. `docs/security-model.md` documents the three-piece contract as
   its core property (planned next).
2. The mode-change flow's advisory text references
   account-per-mode hygiene (a one-line addition to
   [ADR-0025](0025-profile-switching-session-safeguards.md)'s
   safeguards or its implementation).
3. The bootstrap UX ([ADR-0018](0018-minimum-bootstrap-ux.md) +
   [ADR-0039](0039-bootstrap-and-host-lifecycle.md)) names the
   discipline at first-host setup.

Items (2) and (3) are user-facing nudges, not structural
enforcement. Their absence does not invalidate this ADR's
*statement*; it only means the user has fewer reminders.

## Build-order placement

No code work required. This ADR codifies an existing implicit
contract. Implementation effort is the documentation work in (1)
above plus the small user-visible-nudges in (2) and (3), which can
land any time.

## Followups

- **Maury-side detection of account/mode mismatch.** A sibling to
  [ADR-0025](0025-profile-switching-session-safeguards.md) for the
  credential-vs-mode case. Requires identifying a stable Claude
  Code surface for reading the active account; deferred until
  Anthropic publishes a contract or until empirical investigation
  identifies a reliable mechanism.
- **`maury status` integration.** When a real check exists,
  surface it via the [`maury-status` skill][maury-status] so
  Claude can flag a mismatch mid-session.
- **Enterprise-account guidance in the porting doc.** Users
  setting up maury for work-use benefit from a concrete
  Anthropic-account setup recipe; that's tutorial-style content
  rather than ADR scope.

## Claude Code references

- [Claude Code overview][cc-overview] — establishes the
  single-account-per-install auth model maury inherits.
- [Workspaces — Claude API docs][cc-workspaces] — the canonical
  Anthropic-side trust-boundary mechanism.
- [Anthropic Privacy Center][cc-privacy] — commercial terms,
  retention windows, ZDR. The data-handling guarantees that make
  a properly-credentialed work-mode mining run safe-by-default.
- [Claude Code data-usage docs][cc-data-usage] — the canonical
  CC-level data-handling reference.
- [anthropics/claude-code #377][cc-issue-377] — *closed*.
  Same-email personal-vs-organizational account confusion. The
  underlying confusion-mode is documented in the thread; #7210
  was filed against the same behavior on a later CLI version.
- [anthropics/claude-code #7210][cc-issue-7210] — *closed*. Same
  bug, more recent reference. Together these surface the practical
  friction of the per-mode-account discipline as Anthropic-
  acknowledged but not currently being actively reworked.

[cc-overview]: https://code.claude.com/docs/en/overview
[cc-workspaces]: https://platform.claude.com/docs/en/manage-claude/workspaces
[cc-privacy]: https://privacy.claude.com/en/
[cc-data-usage]: https://code.claude.com/docs/en/data-usage
[cc-zdr]: https://code.claude.com/docs/en/zero-data-retention
[cc-issue-377]: https://github.com/anthropics/claude-code/issues/377
[cc-issue-7210]: https://github.com/anthropics/claude-code/issues/7210
[maury-status]: 0017-drift-detection-and-reconciliation.md#the-maury-status-skill

## Amendment history

- 2026-05-13 — factual-accuracy fixes against the cited Anthropic
  sources, per the doc-review on commit `1d05499`:
  - Retention window corrected from "7-day default" (which was
    sourced from a stale websearch summary) to "30-day standard
    retention for commercial users" per the current
    [Claude Code data-usage docs][cc-data-usage]; ZDR added with
    a direct reference.
  - Workspaces description tightened to enumerate only the
    per-workspace settings the [Workspaces docs][cc-workspaces]
    actually list (API keys, members, rate limits, spend limits,
    scoped resources). The earlier "data-residency settings"
    claim was wrong; removed. Direct quote tightened to text
    that actually appears on the page.
  - Workspaces URL retargeted to the canonical
    `platform.claude.com/docs/en/manage-claude/workspaces`
    (was redirecting from the earlier `build-with-claude`
    path).
  - CC issues #377 / #7210 marked CLOSED (verified via
    `gh issue view`); reframed as Anthropic-acknowledged
    friction rather than open work.
  - Single-account-per-install claim labeled "empirically"
    with reasoning, since the cc-overview page doesn't
    explicitly establish it.
