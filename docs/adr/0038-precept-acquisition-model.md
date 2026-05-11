# ADR-0038: Rules acquisition model and advisory lifecycle

**Status:** Accepted
**Date:** 2026-05-08

## Related tenets

- [Tenet 1 — First, do no harm](../tenets.md#1-first-do-no-harm)
- [Tenet 5 — The user arbitrates ambiguity](../tenets.md#5-the-user-arbitrates-ambiguity)
- [Tenet 7 — Provenance is mandatory](../tenets.md#7-provenance-is-mandatory)
- [Tenet 8 — Hand-edits are first-class input](../tenets.md#8-hand-edits-are-first-class-input)
- [Tenet 10 — Modularity over hardcoding](../tenets.md#10-modularity-over-hardcoding)
- [Tenet 11 — Explicit beats implicit, with conservative defaults](../tenets.md#11-explicit-beats-implicit-with-conservative-defaults)

## Related ADRs

- [ADR-0015](0015-surrogate-keys-for-hosts-and-profiles.md) — URL is the surrogate key for repos; `host_<hex>` and `mode_<hex>` IDs
- [ADR-0016](0016-pluggable-repo-backends.md) — backend pluralism; no platform-specific affordances
- [ADR-0017](0017-drift-detection-and-reconciliation.md) — drift detection; render engine
- [ADR-0030](0030-manifest-schema-migrations.md) — when manifest schema bumps are required
- [ADR-0033](0033-pr-repo-mode.md) — `pr` repo mode; PR contribution side-channel
- [ADR-0037](0037-layer-taxonomy-and-repo-discovery.md) — layer taxonomy; `rules` layer; convention vs precept = `repo_mode`; render order; marker file schema
- [ADR-0039](0039-bootstrap-and-host-lifecycle.md) — bootstrap flow; mode-scoped sync; mode change resets advisory state

---

## Context

[ADR-0037](0037-layer-taxonomy-and-repo-discovery.md) establishes that
**convention** and **precept** are not separate layer types — they are
access subtypes of the unified `rules` layer, distinguished by the
consuming host's `repo_mode` (`rw` for convention, `pr`/`ro` for
precept). It also establishes that `rules` repos are declared as
sublayers of any other layer (`base` or any mode), and that the marker
file `.meta/maury-marker.json` records each sublayer entry's
`repo_mode` per consumer.

What ADR-0037 does **not** establish, and what this ADR is responsible
for:

1. **The acquisition model.** How does a layer pull a `rules` repo
   into its sublayer graph? Centrally enumerated, or per-layer
   declared? Where does the consumer-side `repo_mode` live?
2. **Governance metadata.** A `rules` repo consumed as a precept is
   prescriptive, not enforceable (Claude Code has no mechanism to
   block configuration overrides — see
   [the official Claude Code documentation][cc-memory]). Maury
   advises. For the advisory to suggest a contribution path, the
   `rules` repo must publish *who reviews PRs*, *where to send them*,
   and *what a PR is expected to contain*. Inferring this from
   server-side ACLs (GitHub teams, GitLab groups) is not portable
   across backends per ADR-0016.
3. **The advisory lifecycle.** When does the advisory fire? How is it
   dismissed? How is dismissal state stored, identified, and synced
   across hosts? When does a re-introduced override re-fire?
4. **Versioning.** Override decisions made against `v3.4.1` of a rules
   repo do not necessarily make sense against `v4.0.0`. Version-aware
   snooze requires rules repos to use semver — and a low-friction way
   for curators to keep semver tags in step with their commits.
5. **Bootstrap of a rules repo.** Rules-repo curators need a single
   command to establish the semver baseline and the post-commit
   tagging hook. This command is `maury repo init` (formerly called
   `maury repo levelset`).

The non-negotiables carried over from ADR-0037 and the broader maury
design:

- **Locality of change.** A team adopting a new `rules` repo for
  `mode:work` must not have to edit the base.
- **Backend pluralism.** Governance must travel with the rules repo,
  not depend on backend ACLs.
- **Tenet 1.** Override decisions must not silently propagate across
  contexts where they may not apply. Mode changes (per
  [ADR-0039](0039-bootstrap-and-host-lifecycle.md)) reset advisory
  state intentionally.
- **Tenet 5.** The user arbitrates every advisory; nothing is
  auto-accepted, auto-dismissed, or auto-merged.
- **Tenet 7.** Every advisory and every dismissal is provenance-
  bearing — what fired, what was overridden, when it was dismissed,
  at what version of the rules repo.

[cc-memory]: https://docs.claude.com/en/docs/claude-code/memory

## Decision

### Acquisition is per-layer

A `rules` repo is acquired by a layer when that layer's marker file
lists the rules repo as a sublayer entry, with `repo_mode` set to
the access the consuming side has.

Per [ADR-0037](0037-layer-taxonomy-and-repo-discovery.md)'s marker
schema:

```json
{
  "schema_version": 1,
  "layer": "mode",
  "agency_id": "550e8400-e29b-41d4-a716-446655440000",
  "sublayers": [
    { "url": "git@github.com:eng-standards/rules-linting.git", "repo_mode": "ro" },
    { "url": "git@github.com:acme-corp/rules-team.git",        "repo_mode": "pr" }
  ],
  "hosts": { "...": "..." }
}
```

Three properties of this shape:

1. **No base involvement.** The base marker is not edited when
   `mode:work` adopts a new `rules` repo. This is the locality
   property from ADR-0037.
2. **`repo_mode` lives on the sublayer entry.** The consumer-side
   access is recorded where the dependency is declared. Per
   [ADR-0033](0033-pr-repo-mode.md), valid values are `rw`, `pr`,
   `ro`.
3. **Inheritance is automatic.** A child mode (e.g.,
   `work:client-acme`) inherits `rules-linting` and `rules-team` by
   virtue of being a descendant in the mode tree (ADR-0037 §"Render
   order"). The child does not re-declare the rules repo; it acquires
   its own additional rules repos on top.

A `rules` repo carries its own `.meta/maury-marker.json` with
`layer: rules` (and `agency_id` recording the originating agency, as a
provenance claim — see ADR-0037). The acquisition is one-sided: the
consumer declares the dependency in their marker file. The rules repo
does not record its consumers.

#### Cross-agency consumption

A `rules` repo whose `agency_id` differs from the consuming
installation's `agency_id` is acquirable. Cross-agency rules sharing is
expected; the engineering-standards team's repo at `eng-standards` may
be consumed by every team in the company. The `agency_id` on the
rules repo is provenance, not gating.

`base` and `mode` repos with mismatched `agency_id` are a hard error
at registration; that is a membership claim, not provenance.

---

### Governance metadata: `.meta/maury-governance.json`

A `rules` repo that expects to be consumed as a precept publishes its
contribution rules as a tracked file. Maury reads this metadata at
registration time rather than inferring permissions from backend ACLs
(which, per [ADR-0016](0016-pluggable-repo-backends.md), are not
portable).

The presence of `.meta/maury-governance.json` in a rules repo enables
the PR contribution path in maury's advisory system. A `pr_required`
field would be redundant — the file's presence implies PRs are the
contribution model. Absence does not prevent the rules repo from being
registered or consumed; it means the advisory path will lack an
upstream-PR suggestion, and `maury repos register` surfaces this gap
so the consumer is not surprised at advisory time.

#### Schema

```json
{
  "schema_version": 1,
  "owners": [
    "alice@example.com",
    "bob@example.com"
  ],
  "pr_target": "main",
  "pr_standards": "All changes require a doc update; conventional commit messages.",
  "min_reviewers": 2
}
```

Future schema changes to this file follow the migration discipline in
[ADR-0030](0030-manifest-schema-migrations.md).

#### Required fields

- **`owners`** — list of identities (free-form strings, typically
  email addresses or platform handles) who review and merge PRs. At
  least one entry. Maury treats this list as opaque; it does not
  validate against any backend.
- **`pr_target`** — where to submit PRs. Either a branch name in the
  same repo (`"main"`, `"trunk"`) or a fork URL for projects that
  prefer fork-based workflows. Free-form string; maury surfaces it
  without inspection.

#### Optional fields

- **`pr_standards`** — free-form prose describing what a PR must
  contain (docs, tests, signed-off-by, etc.). Maury surfaces this to
  the user when they invoke the contribution path; it does not
  enforce.
- **`min_reviewers`** — integer; defaults to **1** when absent. Surfaced
  for the user's awareness; the backend's review gate (if any) is the
  actual enforcement. Single-owner repos with `min_reviewers: 1` may
  self-review — governance is opt-in and the owner sets their own bar.

#### Validation

`maury agency validate` warns about a `rules` repo that any host
consumes with `repo_mode: pr` and is missing
`.meta/maury-governance.json`. The warning is informational, not a
hard failure: legacy rules repos and in-flight migrations should not
be blocked.

`maury repos register` reads `.meta/maury-governance.json` at
registration time. If the file is absent, registration succeeds but
the consumer is informed that no PR target is recorded; the
override-advisory path will lack the upstream-PR suggestion.

---

### Contribution flow

The default contribution model is PR-based, **including for owners**.
The PR path is a workflow choice, not a permission workaround.

1. Consumer (with `repo_mode: pr`) detects a gap or disagreement,
   typically surfaced by an override advisory at render time.
2. Consumer opens a PR against the rules repo's `pr_target`. Per
   [ADR-0033](0033-pr-repo-mode.md), this uses the platform's PR
   mechanism (e.g., `gh pr create` for GitHub-hosted rules repos).
3. Owner(s) listed in `.meta/maury-governance.json` review and merge
   per the repo's `pr_standards`.
4. All consumers of the rules repo receive the update on the next
   `maury sync` (per the mode-scoped sync behavior in
   [ADR-0039](0039-bootstrap-and-host-lifecycle.md)).

**Owners use PRs too.** Even a consumer with `repo_mode: rw` should
open PRs by default rather than push directly to `pr_target`. The
reasons:

- Review is uniform whether the contributor is an owner or an outside
  subscriber.
- Audit trail is uniform — every change has an associated PR with
  discussion, not a mix of pushes and PRs.
- The `min_reviewers` field is meaningful only if owners themselves go
  through the gate.

A `repo_mode: rw` owner *can* override this by pushing directly, but
that path has no special blessing in the model — it is the user
choosing to bypass their own governance.

For consumers with `repo_mode: ro`, no contribution path exists
through maury; the override advisory still fires, but the
upstream-PR suggestion is replaced with "no contribution path
configured for this consumer."

---

### Semver versioning for rules repos

`rules` repos consumed with precept semantics **should** use semver
tags. Version-aware advisory snooze (below) treats a tagged version as
the unit of "has the upstream changed enough to surface my override
decision again?".

#### Why semver, not commit SHA

A consumer who dismisses an advisory at commit `abc1234` and then sees
the rules repo land a typo fix as commit `def5678` does not need to
re-decide whether their override still makes sense. Semver lets the
rules-repo owner signal:

- *patch* — no behavior change (typo fix, formatting),
- *minor* — new conventions added, existing ones unchanged,
- *major* — content reorganized in a way that may invalidate existing
  override decisions.

The advisory snooze granularity defaults to **major**: most evolution
is patch-level, and major bumps are the curator's explicit signal that
a re-decision is warranted.

#### `maury repo init`

`maury repo init <url>` is an **owner-only**, **idempotent**
bootstrap command for a rules repo's semver baseline. It:

1. Validates the host's access: the declaring layer's marker file
   must record `repo_mode: rw` against this rules repo. Write
   access is the curator role — there is no separate identity check
   against `owners` (the `owners` list in
   `.meta/maury-governance.json` is for humans reviewing PRs, not
   for programmatic access control; `host_<hex>` IDs and
   `owners` entries are different namespaces and never compared).
2. Checks for an existing initialization. The presence of either
   `.meta/maury-governance.json` or `.meta/maury-marker.json` means
   the repo is already initialized; the command refuses with a
   clear error and exits cleanly. A `--force` flag (with heavy warning)
   exists as an escape hatch for genuine reset cases:
   `maury repo init --force`.
3. Creates `.meta/maury-governance.json` with prompted `owners` and
   `pr_target` (and optional `pr_standards`, `min_reviewers`).
4. Writes `.meta/maury-marker.json` with
   `{ "schema_version": 1, "layer": "rules", "agency_id": "<current-agency-id>" }`.
   This is what makes the rules repo discoverable as a maury layer
   per [ADR-0037](0037-layer-taxonomy-and-repo-discovery.md).
5. Creates the **initial semver tag** (`v1.0.0` by default; the
   curator can override).
6. Installs a **post-commit git hook** in the rules repo that
   auto-increments the **patch** version on each commit. The hook tags
   `vMAJOR.MINOR.(PATCH+1)` after each commit lands.

Major and minor bumps remain explicit curator actions
(`maury repo bump --minor`, `--major`); they are not auto-incremented.
The curator runs `init` once per rules repo; subsequent commits get
patch tags automatically.

The command is named `maury repo init` because it is a one-time
bootstrap operation, not ongoing alignment. The earlier name
`maury repo levelset` has been retired.

##### Why owner-only

Only a host with `repo_mode: rw` against the rules repo can write the
governance file, the initial tag, or the post-commit hook. A consumer
with `pr` access cannot init: they could open a PR adding the file,
but the tag and hook installation require write access. The owner-only
constraint prevents accidentally initializing a repo from the wrong
side of the access boundary.

#### No-semver fallback

A rules repo with no semver tags continues to function. Maury treats
every commit as a patch revision in that case. The practical
consequence shows up at the advisory snooze menu: the major and minor
snooze options are **grayed out** because they have no meaning for a
repo without a version structure. Only patch-level snooze and
override-anchor are offered.

`maury repos register` for a precept-mode rules repo without semver
tags emits an informational note: *"Rules repo has no semver tags;
register without semver baseline, or run `maury repo init <url>` first
to establish one."*

---

### Advisory lifecycle

Per ADR-0037, an **advisory** fires when a layer the host accesses
with `repo_mode: pr` or `ro` (precept semantics) has content
overridden by a layer the host accesses with `repo_mode: rw`
(convention semantics — including the consuming layer itself, where
host-tagged or env-tagged content overrides a precept).

The advisory is informational. It does not warn or block. It is
acknowledgeable through one of two dismissal paths.

#### Two dismissal paths

##### 1. Version-snooze

> *"Suppress this advisory for this [major | minor | patch] version
> of the rules repo."*

The advisory re-surfaces when the rules repo bumps to a new version
at the chosen granularity:

- **Major snooze:** re-fires on next major bump (`v3.x.y → v4.0.0`).
- **Minor snooze:** re-fires on next minor bump (`v3.4.x → v3.5.0`)
  or on any major bump.
- **Patch snooze:** re-fires on the very next commit to the rules
  repo (every commit is at least a patch).

**Default granularity is major.** Most rules-repo evolution is
patch-level; the user does not want to re-decide on every patch.
Major bumps are the rules-repo owner's explicit signal that something
substantive has changed — exactly the moment to re-evaluate.

##### 2. Override-anchor

> *"Suppress this advisory while this override exists."*

Auto-clears in two cases:

- The override is removed from the overriding layer (the user changed
  their mind).
- The overridden field no longer exists in the rules repo (the
  upstream dropped the field).

This is the lighter-touch dismissal. It says "as long as I'm overriding
this, I know I'm overriding it; stop reminding me." If the override is
removed and then re-added later, the advisory fires again — the
auto-clear is real.

#### Snooze menu

When the advisory fires and the user invokes
`maury advisory acknowledge <id>`, the snooze menu is presented:

```
Suppress this advisory for:
  [M] this major version (default)
  [m] this minor version
  [p] this patch version
  [f] forever (override-anchor: until you stop overriding)
```

For a rules repo with no semver tags, the menu is:

```
Suppress this advisory for:
  [p] this patch version
  [f] forever (override-anchor: until you stop overriding)

(M and m grayed out — rules repo has no semver tags. Run
`maury repo init <url>` to enable major/minor snooze.)
```

`forever` maps to override-anchor rather than a true forever-suppress.
There is no "I never want to hear about this again, period" path; the
override-anchor's auto-clear is the safety net per Tenet 1.

---

### Advisory storage

Acknowledged advisories live in the **mode repo**, not in
`~/.claude/maury-state/`:

- **Why mode, not host-local.** A user who works in the same mode on
  two machines should only need to acknowledge once. Host-local state
  would force them to acknowledge each advisory per machine.
- **Why mode and not base.** A user in `mode:home` and `mode:work`
  may make legitimately different override decisions in each mode.
  Storing dismissals in the mode repo aligns dismissal scope with
  decision scope.
- **Why this is not a privacy concern.** Mode repos are private by
  design (they hold the user's mode-specific config). Storing
  per-host dismissals there does not leak the dismissals beyond the
  audience already authorized to read the mode.

Storage is in a separate file, `.meta/maury-advisory-state.json`,
kept distinct from `.meta/maury-marker.json` to avoid cluttering the
marker file with potentially large dismissal records.

#### Schema

```json
{
  "schema_version": 1,
  "dismissals": [
    {
      "advisory_id": "<content-hash>",
      "rules_url": "git@github.com:eng-standards/rules-linting.git",
      "field_path": "tabs.preference",
      "dismissed_at": "2026-05-08T14:32:00Z",
      "dismissed_by_host": "host_8a7f3b2c",
      "snooze_kind": "version",
      "snooze_version": "3",
      "snooze_granularity": "major"
    },
    {
      "advisory_id": "<content-hash>",
      "rules_url": "git@github.com:eng-standards/rules-linting.git",
      "field_path": "imports.style",
      "dismissed_at": "2026-05-08T14:33:00Z",
      "dismissed_by_host": "host_8a7f3b2c",
      "snooze_kind": "override-anchor"
    }
  ]
}
```

`snooze_kind` is `version` (with `snooze_version` recording the
version-string snapshot at dismissal time and `snooze_granularity`
in `{major, minor, patch}`) or `override-anchor` (no version needed;
auto-clears on override removal).

Future schema changes to this file follow the migration discipline in
[ADR-0030](0030-manifest-schema-migrations.md).

`dismissed_by_host` is a `host_<hex>` ID. Per
[ADR-0039](0039-bootstrap-and-host-lifecycle.md), `host_<hex>` IDs are
mode-scoped — every host listed in a mode's `dismissed_by_host`
field is registered in that same mode. There is no cross-mode host ID
to worry about.

#### Sync

Advisory state is synced via normal `maury sync`. Per
[ADR-0039](0039-bootstrap-and-host-lifecycle.md), `maury sync`
traverses the active mode's subgraph; the advisory-state file in the
active mode repo is fetched and updated as part of every sync.

A new host bootstrapping into an existing mode inherits the mode's
existing advisory state by cloning the mode repo. Acknowledgments
made on the user's other host(s) in the same mode are immediately in
effect for the new host.

#### Mode change resets dismissals — intentionally

Per Tenet 1, maury does not silently carry override decisions into a
new mode where they may not apply. Switching modes means switching
mode repos, which means starting from whatever the new mode's
`.meta/maury-advisory-state.json` already records — typically empty
for a host arriving fresh.

The user re-evaluates every override decision in the new mode. This
is a feature: an override choice that made sense in `mode:home` may
be exactly wrong in `mode:work`, and silently inheriting it would be
the harm Tenet 1 forbids.

[ADR-0039](0039-bootstrap-and-host-lifecycle.md) §"Mode change
process" describes the sequence in detail.

---

### Advisory identity: content-addressable

An advisory is identified by the **content** of what it is about, not
by an opaque sequence number:

```
advisory_id = sha256(
    rules_repo_url + "\n" +
    field_path
).hex()[:16]
```

Where:

- `rules_repo_url` is the canonical URL from the marker file (URL is
  the surrogate key per ADR-0015).
- `field_path` is the dotted-path identifier of the overridden content
  within the rules repo (e.g., `tabs.preference`,
  `commit_message.format`).

Three load-bearing properties:

1. **Re-firing after removal.** If the user dismisses an advisory,
   then later removes the override, the override-anchor dismissal
   auto-clears. If they re-introduce the same override later, the
   advisory fires again with the **same** `advisory_id` — and is
   **not** treated as already-dismissed (because the prior dismissal
   auto-cleared). The user sees a fresh advisory and decides anew.
2. **Stability across renames.** If the rules-repo owner renames a
   field (e.g., `tabs.preference` → `indentation.tabs`), the advisory
   ID changes. The previous dismissal no longer matches; the user is
   alerted to the field-shape change. This is the intended behavior
   — a renamed field is a substantive change worth re-deciding.
3. **No central ID issuer.** Every host computes the same
   `advisory_id` from the same inputs. No coordination needed.

#### Worked example: same override re-fires if re-introduced

```
day 1:  user overrides `tabs.preference` in their host-tagged section
        advisory fires (id = abc123...)
        user dismisses with snooze_kind = override-anchor
day 5:  user removes the override
        override-anchor auto-clears (no override = nothing to suppress)
day 9:  user adds the override back
        advisory fires again with id = abc123...
        prior dismissal does not match (it was auto-cleared)
        user sees a fresh advisory
```

This is the correct behavior. The user has changed their mind twice;
the second decision deserves the same surfacing as the first.

---

## Consequences

- **Good:** Acquisition is local to each layer's marker file. The
  base is not a gate.
- **Good:** Governance is portable across backends. Whether a rules
  repo lives on GitHub, Codeberg, Gitea, or self-hosted git, the
  PR-target and ownership data travels with the repo.
- **Good:** PR-by-default is uniform across owners and consumers.
  The contribution model does not depend on whether you happen to
  have `rw`.
- **Good:** Advisory dismissals sync via the mode repo, so a user
  who works in the same mode on two machines acknowledges once.
- **Good:** Content-addressable advisory identity means the same
  override re-fires cleanly after a removal-then-re-add cycle,
  without needing a central ID issuer.
- **Good:** Semver-keyed snooze defaults to major granularity — the
  right cadence for "should I revisit this decision?".
- **Good:** `maury repo init` is owner-only and idempotent. Consumers
  cannot accidentally initialize a repo they do not own; running
  `init` twice is safe (refuses with a clear error).
- **Neutral:** `.meta/maury-governance.json` is one more file per
  rules repo that wants to be consumed as a precept. Justified by
  backend-portability and self-description; it is also the signal
  for "this repo follows a PR contribution model," replacing what
  would otherwise be a boolean field.
- **Neutral:** Auto-incrementing patch tags via a post-commit hook is
  curator opt-in via `maury repo init`. Curators who decline get the
  no-semver fallback (patch-level snooze and override-anchor only).
- **Bad:** Mode change resets advisory dismissals. Users switching
  modes re-decide every override. This is intentional (Tenet 1) but
  a real cost.
- **Bad:** Existing rules repos consumed as precepts need a retrofit
  pass to add `.meta/maury-governance.json`. Registration-time
  warnings surface the gap; no hard failure.
- **Bad:** Advisory state lives in a synced mode repo. A user who
  acknowledges advisories on a host that loses sync briefly may see
  re-fires on another host until sync catches up. This is acceptable
  given the sync cadence is user-initiated.

### Confirmation

- `maury agency validate` warns about precept-mode `rules` repos
  missing `.meta/maury-governance.json` (informational, not failure).
- `maury repos register` reads `.meta/maury-governance.json` at
  registration time and surfaces missing-governance gaps.
- `maury repo init` (a) creates `.meta/maury-governance.json` with
  prompted owners and `pr_target`, (b) writes `.meta/maury-marker.json`
  with `layer: rules` and the current `agency_id`, (c) creates an
  initial semver tag if absent, (d) installs the post-commit
  auto-patch-bump hook, and (e) refuses re-init when either
  `.meta/maury-governance.json` or `.meta/maury-marker.json` already
  exists, unless `--force` is provided.
- `maury repo init` requires `repo_mode: rw` against the rules repo
  in the declaring layer's marker file; tested by attempting `init`
  from a `pr`-mode consumer and asserting refusal.
- The advisory system stores dismissals in the mode repo at
  `.meta/maury-advisory-state.json` per the schema above.
- `advisory_id` is computed as a content hash of `rules_url` +
  `field_path`; tested against the re-introduction property
  ("removed then re-added → same advisory_id, treated as fresh").

---

## Build-order placement

- **When implemented — marker schema additions.** The `sublayers`
  field with per-entry `repo_mode` is part of ADR-0037's marker
  schema. Rules-repo-side `.meta/maury-governance.json` is a new file
  that rules-repo curators write.
- **When implemented — `maury repo init`.** New CLI verb; pairs with
  `maury repo bump --minor|--major`.
- **When implemented — advisory engine.** Render-time advisory
  detection and the dismissal storage in the mode repo. Built
  alongside the render engine.
- **When implemented — content-addressable advisory ID.** Hash
  function and re-firing semantics covered by tests.

---

## Open followups

- **Rules-repo retrofit tooling.** A `maury repo add-governance <url>`
  helper that prompts for `owners`, `pr_target`, `pr_standards`,
  `min_reviewers` and commits `.meta/maury-governance.json` to the
  rules repo. Pairs with ADR-0037's `maury repos add-markers`
  retrofit.
- **`maury advisory list` / `maury advisory show`.** CLI verbs for
  browsing fired advisories, dismissals, and override history. Useful
  for periodic reviews of "what have I been ignoring?"
- **Per-field override-anchor scope.** The override-anchor currently
  auto-clears on removal of the specific override. Coarser
  granularity (anchor per layer, not per field) is under
  consideration if users find per-field anchors too noisy.
- **`pr_standards` machine-readable extension.** The current field is
  free-form prose. A future schema could split it into structured
  sub-fields (`docs_required: bool`, `tests_required: bool`,
  `commit_message_format: regex`). Not in initial scope.
- **Cross-rules-repo dependencies.** A rules repo that itself depends
  on another rules repo (e.g., `team-engineering` declares
  `org-baseline` as a precept). The traversal handles this uniformly
  via the distributed manifest, but the UX of multi-level override
  advisories needs design.
- **Advisory state housekeeping.** Old override-anchor entries
  auto-clear, but a periodic compaction pass could prune
  dismissed-and-re-decided history if the file grows large.

---

## Claude Code references

- [Claude Code memory: how CLAUDE.md is read][cc-memory] — establishes
  that CLAUDE.md content is guidance Claude reads at session start,
  not enforced configuration; the basis for the precepts-are-
  prescriptive-not-enforceable property and for the entire advisory
  (rather than blocking) model.
