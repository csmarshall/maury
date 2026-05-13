# maury — security model

What maury protects against, what it doesn't, and the discipline a
user must maintain for the structural isolation to hold. This is the
operational companion to [`tenets.md`](tenets.md) (the principles),
[`concepts.md`](concepts.md) (the vocabulary and theoretical
foundations), and the ADRs that establish individual mechanisms.

> **Who this is for.** An engineer evaluating maury for work-use,
> or a future maintainer auditing the safety properties. The
> first half is plain-language; the second half links to the
> literature and the ADRs that make each claim load-bearing.

> **Calibration.** Pre-v0.1 — see [`status.md`](status.md) for what
> ships today vs. what's designed. This doc describes the *target*
> security model; not every nudge mentioned below is wired into the
> shipping code yet, and those gaps are named explicitly under
> "Planned hardening."

---

## TL;DR

Maury structurally prevents bytes from crossing **trust boundaries**
between hosts: a work laptop literally cannot fetch personal-mode
content because the git provider won't serve those bytes to its
deploy key. That's enforced server-side; no client-side discipline
can break it.

What it doesn't structurally enforce: the **operational discipline**
that mining and LLM-classified work runs against the right
Anthropic credential for the active mode. That's the user's
responsibility, with maury providing nudges and an explicit
contract rather than enforcement.

If you keep work content under work credentials and home content
under personal credentials, maury's mode isolation holds end-to-end.
If you don't, the structural isolation between repos still holds —
but the LLM-extraction surface bypasses it for the time mining runs.

---

## The three-piece contract

Mode isolation in maury depends on three pieces. The first is
structural — the git provider enforces it server-side. The other
two are operational — the user maintains them, and maury nudges
rather than enforces.

| # | Piece | Type | Owner | Mechanism |
|---|---|---|---|---|
| 1 | Per-host SSH deploy keys | **Structural** | git provider | A host without the deploy key for a repo cannot fetch its bytes. See [ADR-0003](adr/0003-per-host-deploy-keys.md). |
| 2 | Per-mode Anthropic credentials | **Operational** | user | Mining sends transcript fragments to the LLM. The fragments cross a network boundary; the account they land at must match the mode the work happened in. See [ADR-0041](adr/0041-per-mode-anthropic-credentials.md). |
| 3 | Right Claude Code account active for the current mode | **Operational** | user | Claude Code is single-account-per-install; switching modes is also a moment to switch the active account. See [ADR-0041 §"Maury does not structurally enforce per-mode credentials today"](adr/0041-per-mode-anthropic-credentials.md). |

**If any one of these breaks, mode isolation is partially or fully
breached.** The doc set's job is to name the contract honestly; the
ADRs make the mechanics testable.

---

## Three threat walkthroughs

Concrete attacks and concrete answers.

### 1. Work laptop is compromised

> "Someone steals my work laptop. What can the attacker reach?"

**Reachable:** every git repo whose deploy key is on the work
laptop — typically the agency's `base` repo (read-only) plus the
`mode:work` repo (read-write). The attacker can pull, push, and
read the rendered `~/.claude/` content.

**Not reachable:** personal-mode content. The work laptop doesn't
hold the deploy key for `mode:home`'s repo, so the git provider
refuses to serve those bytes to that host. This is the load-bearing
property behind [Tenet 3](tenets.md#3-trust-boundaries-are-physical-not-policy).

**What to do:** revoke the work laptop's deploy keys from each
repo it had access to (your git provider's deploy-key management
UI). Per [ADR-0003](adr/0003-per-host-deploy-keys.md) the
revocation is surgical — only that host loses access; every other
host's deploy keys remain valid.

### 2. A deploy key leaks

> "I accidentally pasted a private key into a chat / pushed it to
> a public repo / mailed it to the wrong person."

**Blast radius:** whatever the deploy key was scoped to. A key
issued to a single repo on a single host gives the attacker that
repo's read or write access (depending on the key's `repo_mode` per
[ADR-0033](adr/0033-pr-repo-mode.md)). They cannot use it to reach
*other* repos.

**Not reachable from this leak:** any repo without a matching
deploy key. Bell-LaPadula non-interference applies — see
[concepts.md §"Non-interference (Goguen & Meseguer, 1982)"](concepts.md#non-interference-goguen--meseguer-1982).

**What to do:** revoke the leaked key at the git provider; rotate
the affected host's keypair via the standard
[ADR-0018](adr/0018-minimum-bootstrap-ux.md) /
[ADR-0039](adr/0039-bootstrap-and-host-lifecycle.md) deploy-key
step. Audit the repo's history for any pushes that landed during
the exposure window.

### 3. Wrong Claude Code account active during `maury mine`

> "I'm on my work laptop, but I'm signed into Claude Code with my
> personal Anthropic account. I run `maury mine`. What happens?"

**What happened:** mining walked the work transcripts and sent
candidate fragments to the LLM under your **personal** Anthropic
account. The fragments are now subject to your personal account's
data-handling contract, not the work account's. Anthropic's
[Workspaces model][cc-workspaces] would normally route work content
to a work workspace; the wrong-account-active state bypassed that.

**Structural isolation is unaffected.** The work-mode repo bytes
never crossed any maury-managed boundary; only the
mining-extraction fragments leaked. Other hosts and other modes
are untouched.

**What to do:** sign out of Claude Code, sign in with the correct
work account, and (if your org cares about the leak) coordinate
with Anthropic per the data-retention terms in your account's
Commercial Terms of Service. Per Anthropic's documented retention
window (default 7 days for API logs, per the
[Privacy Center][cc-privacy]) the data leaves their systems on a
short clock.

**This is the failure mode [ADR-0041](adr/0041-per-mode-anthropic-credentials.md)
documents.** Maury doesn't structurally prevent it today — that's
on the "Planned hardening" list below.

---

## What's NOT in maury's security scope

These are explicitly outside maury's enforcement responsibility.
Naming them keeps the security model honest.

- **Your Anthropic account's data-handling policies.** Maury
  inherits whatever your Anthropic plan (personal / Pro / Team /
  Enterprise / API) provides. Commercial customers get reduced
  retention + Zero Data Retention options per
  [Anthropic's Privacy Center][cc-privacy]; personal accounts get
  the consumer defaults. **Choose the account tier appropriate for
  the content you'll mine.**
- **Your git provider's auth model.** Maury depends on per-host
  deploy keys; how your provider authenticates those keys, rotates
  them, audits them, and revokes them is the provider's contract,
  not maury's.
- **Transport encryption.** SSH and HTTPS-over-TLS handle this;
  maury doesn't reimplement transport security.
- **Host OS security.** A compromised host gives the attacker
  whatever local access the user had. Disk encryption, screen
  locks, hardware-backed keystores, MDM — your OS, not maury.
- **Mining-LLM choice beyond Anthropic.** [ADR-0012](adr/0012-llm-backend.md)
  makes the LLM backend pluggable; users who replace Anthropic
  with a local or third-party model take on that provider's
  contract instead.
- **What you put in your transcripts.** Maury can't tell whether
  a CLAUDE.md fragment contains a literal secret, a hostname you
  didn't mean to publish, or PII. Redaction rules
  ([ADR-0004](adr/0004-rule-engine-classification.md)) catch what
  the user writes rules for; the rest is human judgment.

---

## Best-practice references

When in doubt, follow the published guidance from the upstream the
relevant boundary sits on.

- **Anthropic credential separation:** Anthropic's
  [Workspaces documentation][cc-workspaces] describes the
  organization-and-workspace model. A workspace is the canonical
  unit of isolation for billing, API keys, rate limits, and
  data-residency — map one maury mode to one workspace.
- **Anthropic data-handling for commercial customers:** the
  [Privacy Center commercial customers collection][cc-commercial]
  covers Commercial Terms of Service, the Data Processing
  Addendum, and Zero Data Retention as a negotiable option.
- **Claude Code data usage:** [Claude Code's data-usage docs][cc-data-usage]
  are the canonical CC-level reference for what Claude Code itself
  sends to Anthropic during normal use (separate from mining).
- **Deploy-key management on GitHub** (the most common backend per
  [ADR-0016](adr/0016-pluggable-repo-backends.md)): GitHub's
  deploy-key UI per repo settings → "Deploy keys." For GitLab,
  Gitea, Codeberg, self-hosted — equivalent UIs per provider.

---

## Theoretical foundations (links out)

The security properties above aren't invented from scratch; they
compose three established frameworks. For the detailed mapping see
[`concepts.md §"Theoretical foundations"`](concepts.md#theoretical-foundations).

- **Mandatory access control (Bell-LaPadula, 1973)** — maps to
  maury's trust-boundary-as-security-label model.
- **Non-interference (Goguen & Meseguer, 1982)** — formalizes the
  "personal content cannot affect work-laptop output" property.
- **Lexical scoping (Strachey, 1967)** — the right mental model
  for how mode chains compose at render time (and incidentally,
  why content from a child mode cannot leak to a sibling without
  going through their common ancestor first per
  [ADR-0027](adr/0027-cross-context-promotion-via-shared-root.md)).

If you're auditing maury against literature, those are the three
papers/frameworks to compare against. Maury implements a simplified
MAC system specialized for per-user-and-small-team Claude Code
config; the formal results are inherited.

---

## Planned hardening

Items the security model would benefit from but maury doesn't yet
ship. Each links the work that would need to land for it to graduate
from "documented discipline" to "structurally enforced."

- **Maury-side detection of account/mode mismatch.** A sibling to
  [ADR-0025](adr/0025-profile-switching-session-safeguards.md)'s
  session safeguards, for the credential-mode case. Would surface
  via the [`maury-status` skill](adr/0017-drift-detection-and-reconciliation.md#the-maury-status-skill)
  and via the mode-change advisory. Requires a stable Claude Code
  surface for reading the active Anthropic account — currently
  unspecified upstream. Tracked in
  [ADR-0041 §"Followups"](adr/0041-per-mode-anthropic-credentials.md).
- **Audit-log integration for credential events.**
  [ADR-0035](adr/0035-audit-log.md)'s audit log doesn't yet
  include a `cc-account-mismatch` event kind; would be additive.
- **Bootstrap UX nudges.**
  [ADR-0018](adr/0018-minimum-bootstrap-ux.md) +
  [ADR-0039](adr/0039-bootstrap-and-host-lifecycle.md) could name
  the per-mode-credential discipline at first-host setup, when the
  user is most likely to internalize it.
- **`scripts/check-doc-links.py` / `maury doctor --docs`.** Not
  security per se, but the same hygiene impulse — internal-link
  rot is a class of trust-boundary erosion in the doc set itself.
  Sketched in CLAUDE.local.md.

---

## How to use this document

- **If you're evaluating maury for work-use:** read the TL;DR,
  the three-piece contract, and the three threat walkthroughs.
  Decide whether the operational discipline is acceptable for
  your org. The "what's NOT in scope" section tells you where
  the responsibility passes to you.
- **If you're auditing safety properties:** the threat
  walkthroughs are concrete starting points; the theoretical
  foundations section gives you the literature to compare
  against; the ADRs cited from each piece of the contract have
  the formal decision records. The "planned hardening" list is
  the honest gap analysis.
- **If you're contributing to maury:** any change that touches
  the three-piece contract — a new render-time channel, a new
  network boundary, a new operational discipline — must update
  this doc and (if it's a decision-level change) land an ADR.
  Vague hand-waves about safety properties are the failure mode
  this doc is here to prevent.

[cc-workspaces]: https://platform.claude.com/docs/en/build-with-claude/workspaces
[cc-privacy]: https://privacy.claude.com/en/
[cc-commercial]: https://privacy.claude.com/en/collections/10663361-commercial-customers
[cc-data-usage]: https://docs.anthropic.com/en/docs/claude-code/data-usage
