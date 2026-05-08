# ADR-0037: Layer taxonomy and repo discovery

**Status:** Accepted
**Date:** 2026-05-08
**Amended:**
- 2026-05-08 — renamed `standards` → `convention`; corrected render
  order (precept before convention at each level, so convention wins
  at same scope); corrected advisory storage (context-level overrides
  persist in the context overlay repo, not machine-local state).

## Related tenets

- [Tenet 1 — First, do no harm](../tenets.md#1-first-do-no-harm)
- [Tenet 6 — Identity is not name](../tenets.md#6-identity-is-not-name)
- [Tenet 10 — Modularity over hardcoding](../tenets.md#10-modularity-over-hardcoding)

## Related ADRs

- [ADR-0015](0015-surrogate-keys-for-hosts-and-profiles.md) — URL is the surrogate key for repos
- [ADR-0016](0016-pluggable-repo-backends.md) — backend pluralism; no platform-specific affordances
- [ADR-0017](0017-drift-detection-and-reconciliation.md) — drift detection; render engine
- [ADR-0030](0030-manifest-schema-migrations.md) — when manifest schema bumps are required

## TL;DR

Maury manages a fleet of git repos, each playing a distinct role
in the configuration hierarchy. This ADR establishes the **layer
taxonomy** (five types encoding structural position + content
character + default inheritance), a **fleet-wide repo registry**
in the manifest, and a **universal embedded marker file** in every
maury-managed repo. Platform-specific affordances (topic tags,
labels, badges) are explicitly out of scope.

<details>
<summary><b>Decision drivers</b> (5 items — click to expand)</summary>

- **Tenet 10 / ADR-0016:** every design decision must work across
  all git backends. No platform-specific affordances.
- **Discoverability without a registry query:** a curator who
  clones an unknown repo should be able to tell whether maury
  manages it and what role it plays.
- **Relationship clarity:** viewing a list of repos in a fleet,
  a human reader should understand how they relate without
  consulting external documentation.
- **Token efficiency:** the render engine concatenates content
  from multiple layers into a single CLAUDE.md. Layer semantics
  must support future tooling that detects and reduces redundancy.
- **Maintenance surface minimization:** anything documented in
  the spec accrues support burden. Only universal mechanisms
  (files in a repo, fields in a manifest) are in scope.

</details>

---

## The layer taxonomy

Every repo in a maury fleet has a **layer type**. The layer type
is a unified concept encoding three things:

1. **Structural position** — where in the configuration hierarchy
   does this repo participate?
2. **Content character** — what kind of content does it hold, and
   who owns/maintains it?
3. **Default inheritance** — how does its content cascade to child
   layers, and what happens when child layers override it?

### The five layer types

#### `base`
The fleet-wide foundation. Exactly one per fleet.

- Structural position: root of the hierarchy; all other layers
  inherit from it.
- Content character: global defaults — CLAUDE.md preamble, shared
  tool configs, fleet-wide conventions the curator maintains.
- Inheritance: cascades to all contexts and machines. Children
  can override freely.
- Managed by: curator (rw); all hosts read-only.

#### `context`
A named configuration mode — what the user is doing or where they
are working. Equivalent to what the manifest currently calls a
*profile*.

- Structural position: below `base`, above `machine`. One context
  is active at a time per host.
- Content character: mode-specific config — the "work" context
  has different strictness, metadata, and tooling expectations
  than the "home" context.
- Inheritance: cascades to machines running in this context.
  Children can override freely.
- Managed by: curator (rw); context-member hosts read-only.

#### `machine`
A specific host's working environment. One per physical or virtual
machine.

- Structural position: narrowest in the hierarchy; nothing
  inherits below it.
- Content character: machine-specific shortcuts, tool paths,
  hardware-aware hints, and local environment facts that Claude
  needs to accomplish work on this machine.
- Inheritance: local only. Content does not cascade further.
- Managed by: the host itself (rw).

#### `convention`
A malleable set of rules owned and maintained by this fleet or a
member of it.

- Structural position: floating — declared at any structural
  level (base, context, or machine) and cascades downward from
  there.
- Content character: team or personal rules the declaring party
  owns and can change freely. Examples: code style preferences,
  PR title formats, tool version pinning policies. The name
  "convention" signals that deviation is possible and expected —
  these are rules by consensus, not by decree.
- Inheritance: cascades to child layers. Child layers can
  override. When multiple child layers converge on similar
  modifications to the same convention, the render engine (post-V1)
  may advise consolidating the shared portion back into the parent.
- Managed by: the declaring party (rw for owner; pr for
  contributors; ro for pure consumers).

#### `precept`
An authoritative set of conventions inherited from an external
source — a team lead, an org-wide policy repo, or a published
community package (analogous to oh-my-zsh for zsh).

- Structural position: floating — inherited at any structural
  level and cascades downward from there.
- Content character: rules the consuming install follows but does
  not own. Examples: org-wide security requirements, a published
  Claude persona package, team coding standards set by a senior
  engineer.
- Inheritance: cascades to child layers. Child layers **can**
  override (no hard enforcement). When an override is detected,
  maury (post-V1) issues an **advisory** — not a warning — noting
  the divergence and suggesting an upstream PR if the change is
  broadly applicable. The advisory is acknowledgeable and will
  not re-surface once dismissed.
- Managed by: external source (rw for owner); consuming install
  has pr access to propose changes, ro at render time.

### Conventions vs precepts — the same data, different relationship

The same repo can be a `convention` layer for its owner and a
`precept` layer for its consumers. What differs is the consuming
install's relationship to it:

```
engineering-standards repo
  ├── team lead's install:   layer=convention, repo_mode=rw   (owns it)
  ├── senior dev's install:  layer=precept,    repo_mode=pr   (follows; can PR)
  └── junior dev's install:  layer=precept,    repo_mode=ro   (follows; read-only)
```

The PR flow for precept consumers: consumer detects a gap or
disagreement → opens a PR against the convention repo (if
`repo_mode=pr`) → owner reviews and merges → all consumers
receive the update on next `maury sync`.

### Render order

The render engine applies layers from widest to narrowest scope.
Within each structural level, precepts are applied before
conventions — so a convention at the same level can override a
precept at that level. Last applied wins when content exists at
the same scope:

```
base precepts      → base conventions
  context precepts → context conventions
    machine precepts → machine conventions   (narrowest; applied last)
```

This means a machine-level convention beats a context-level
precept at render time. That is intentional: a user's own
conventions should prevail over inherited formal rules. The
advisory system (post-V1) surfaces the divergence without
blocking it.

### Override advisory (post-V1)

When the render engine detects that a convention overrides content
inherited from a `precept` layer, it issues an advisory — not a
warning. The advisory is informational, acknowledgeable, and does
not re-surface once dismissed:

> "`my-workstation` (context: work) overrides `tabs-preference`
> from `team-standards` (precept, owner: `@lead`). If broadly
> applicable, an upstream PR would propagate this to 9 other
> consumers. `maury advisory acknowledge <id>` to dismiss."

**Advisory storage** is scoped to the context, not the machine.
A developer who uses the `work` context on two machines should
only need to acknowledge once. Acknowledged advisories are stored
in the context's overlay repo (versioned, synced via `maury sync`)
rather than in `~/.claude/maury-state/` (which is machine-local).
The exact storage schema is deferred to ADR-0038.

No advisory is issued for `convention` overrides — the declaring
party owns the content and child customization is expected.

### Token efficiency (post-V1)

Rendering multiple layers into a single CLAUDE.md has a token
cost. A future render engine capability — **rule decomposition** —
will analyze content across layers and identify opportunities to
split a rule into its general concept (lives in the parent layer)
and local specifics (lives in the child layer), reducing
redundancy. This is not V1 scope but the V1 provenance tracking
(see §"Render-time provenance" below) is designed to support it.

---

## Fleet repo registry

### Motivation

The manifest's existing `hosts` and `profiles` dicts record which
repos each host knows about (by local nickname and URL). There is
no fleet-wide index of what repos exist and what roles they play.
This ADR adds one.

### Schema

A new optional top-level `repos` dict is added to the manifest,
keyed by URL (consistent with ADR-0015: URL is the surrogate key
for repos):

```json
{
  "version": 2,
  "profiles": { ... },
  "hosts": { ... },
  "repos": {
    "git@example.com:org/dotfiles.git": {
      "name": "dotfiles",
      "layer": "base",
      "scope": null,
      "description": "Fleet-wide base config"
    },
    "git@example.com:org/work-overlay.git": {
      "name": "work-overlay",
      "layer": "context",
      "scope": "profile_3f1a...",
      "description": "Work context overlay"
    },
    "git@example.com:org/engineering-standards.git": {
      "name": "engineering-standards",
      "layer": "convention",
      "scope": "profile_3f1a...",
      "description": "Team coding conventions"
    }
  }
}
```

**Fields:**

- `name` — free-form display label. Not load-bearing; the URL is
  the identity.
- `layer` — one of `base | context | machine | convention |
  precept`. Required.
- `scope` — `null` for `base`; a `profile_<hex>` ID for
  `context` and `convention` layers scoped to a profile; a
  `host_<hex>` ID for `machine` layers. `precept` layers may
  scope to a profile or be fleet-wide.
- `description` — optional free-form note for curator reference.

**Schema version:** The `repos` field is optional and not
load-bearing. No version bump required per ADR-0030.

### CLI surface

```sh
maury repos list                  # all fleet repos, layer + scope
maury repos list --layer precept  # filter by layer type
maury repos show <url|name>       # full record + which hosts reference it
maury repos register <url>        # add to registry (interactive or --layer flag)
maury repos unregister <url>      # remove from registry
```

`maury manifest validate` will flag:
- URLs in host `repos` dicts that have no registry entry (orphans)
- Registry entries with no host references (unused)
- Tier-2 marker file presence and correctness on cloned repos

---

## Universal embedded marker (Tier-2 discovery)

Every maury-managed repo contains a marker file at
`.meta/maury-marker.json`. This provides off-network provenance:
a curator who clones an unfamiliar repo can read the marker and
immediately know its role and where the canonical manifest lives —
without querying any external registry.

### Schema (v1)

```json
{
  "schema_version": 1,
  "manifest_url": "git@example.com:org/dotfiles.git",
  "manifest_path": ".meta/manifest.json",
  "layer": "context",
  "scope": "profile_3f1a...",
  "registered_at": "2026-05-08"
}
```

**Location:** `.meta/maury-marker.json` at the repo root.
Co-located with `manifest.json` in base repos; present in all
other layer repos as well. One conventional metadata directory
per repo.

**Written by:** `maury bootstrap repo` (planned) commits the
marker file as part of the repo's initial setup. It is not
modified by `maury sync`.

**Greppable:** `find . -path '*/.meta/maury-marker.json'` locates
all maury-managed repos under a directory.

### No Tier-3

Platform-specific affordances (GitHub topic tags, labels, badges,
CI config) are entirely outside maury's scope per ADR-0016 and
the maintenance-surface principle: documenting an opportunistic
affordance — even labeled "not load-bearing" — accrues support
burden, sets a precedent for additions, and creates implicit
pressure on adopters to choose backends with those affordances.
Universal mechanisms only.

---

## Render-time provenance

The V1 render engine tracks, for every piece of content in the
final CLAUDE.md, which layer it originated from. This provenance
record enables:

- Correct override cascade (child overrides parent, with
  convention/precept awareness).
- V1 advisory generation (post-V1 scope): provenance is the
  prerequisite for detecting precept overrides and issuing
  acknowledgeable advisories.
- V1 rule decomposition (post-V1 scope): provenance is the
  prerequisite for detecting when child layers converge on similar
  modifications to the same rule.

Provenance is not surfaced to the user in V1; it is an internal
render-engine concern.

---

<details>
<summary><b>Considered options</b> (click to expand)</summary>

#### Option A: Names as the discovery mechanism
Enforce a naming convention (`maury-<role>-<context>`) so repos
are identifiable by sight in a hosting provider's UI.

- ✅ **Good:** No extra files or manifest fields.
- ❌ **Bad:** Names are mutable labels, not stable identifiers
  (Tenet 6). A rename breaks the convention silently.
- ❌ **Bad:** Curators may have existing repos with established
  names they cannot change.
- ❌ **Bad:** The convention is only meaningful to humans reading
  a list; it's not machine-queryable.

#### Option B: Platform-specific affordances (topic tags, labels)
Use GitHub topic tags or similar to mark repos and encode their
role.

- ✅ **Good:** Human-browsable without cloning.
- ❌ **Bad:** Not universal. GitHub topics don't exist on Gitea,
  Sourcehut, raw git, Perforce, or S3+age backends.
- ❌ **Bad:** Documenting a non-universal affordance — even as
  "opportunistic" — accrues support burden and erodes backend
  pluralism per ADR-0016.

#### Option C (chosen): Manifest registry (Tier-1) + marker file (Tier-2)
Two-tier universal discovery with no platform-specific surface.

- ✅ **Good:** Works on every git backend (it's just files).
- ✅ **Good:** Off-network provenance via marker file.
- ✅ **Good:** Machine-queryable via `maury repos list`.
- ⚖️ **Neutral:** Requires `maury bootstrap repo` to write the
  marker file on setup. Small one-time cost per repo.

</details>

---

## Consequences

- ✅ **Good:** Layer taxonomy gives the render engine clear
  semantics for cascade, override, and (post-V1) advisory
  generation.
- ✅ **Good:** Manifest registry gives curators a fleet-wide
  queryable index. `maury repos list` is the answer to "what
  repos does this fleet manage?"
- ✅ **Good:** Marker file gives off-network provenance. Any
  maury-aware tool can identify and classify a repo by reading
  one file.
- ✅ **Good:** No platform-specific affordances. Backend
  pluralism (ADR-0016) is preserved.
- ⚖️ **Neutral:** Five layer types is more taxonomy than a
  minimal design would have. The complexity is load-bearing:
  each type has distinct content character and inheritance
  semantics that the render engine needs to respect.
- ⚖️ **Neutral:** `repos` field added to manifest schema.
  Optional and not load-bearing; no version bump required.
- ❌ **Bad:** `maury bootstrap repo` must commit the marker file
  as part of every new repo setup. Curators setting up existing
  repos need a retrofit path (`maury repos add-markers`).

## Migration for existing fleets

Lazy migration strategy:

1. `maury manifest validate` warns about repos in host dicts
   with no registry entry and repos missing marker files.
2. `maury repos register --infer` populates registry entries
   from existing host repo dicts with best-guess layer inference.
3. `maury repos add-markers` writes marker files across the
   fleet's cloned repos in one curator-side pass.

No hard cutover; existing fleets continue to function without
the registry or markers — they just lose the discoverability
and provenance benefits until migrated.

## Open followups

- **ADR-0038** (planned): Precept layer acquisition model —
  how a context or machine declares which precept repos it
  acquires, the PR contribution flow, and acknowledgeable
  advisory storage.
- **`maury bootstrap repo`** (Phase 4, planned): curator command
  to scaffold a new repo entry and commit the marker file.
- **`maury bootstrap profile`** (Phase 4, planned): curator
  command to add a new context/profile to the manifest.
- **`maury repos` CLI verbs** (Phase 4, planned): `list`,
  `show`, `register`, `unregister`.
- **Rule decomposition** (post-V1): render engine analysis of
  content redundancy across layers; advisory system for precept
  overrides.
- **Non-git backend marker expression** (ADR-0016 amendment,
  post-V1): backends that cannot host a file at a known path
  (Perforce depot files, S3+age) may need backend-specific
  marker expression. Default: fall back to Tier-1 (manifest
  registry) only.
