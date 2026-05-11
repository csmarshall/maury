# ADR-0019: Inheritance semantics — refine by default, replace explicitly

**Status:** Accepted
**Date:** 2026-05-06
**Amended:**
  - 2026-05-11 — "profile" renamed to "mode" per ADR-0037. Inheritance semantics (refine-by-default, replace-explicit) are unchanged; only vocabulary updated.

## Related tenets

- [Tenet 2 — Consistency within a profile](../tenets.md#2-consistency-within-a-profile-controlled-difference-across-profiles)
- [Tenet 8 — Hand-edits are first-class input](../tenets.md#8-hand-edits-are-first-class-input)

## TL;DR

When a child layer touches something the parent already defined, does
it refine or replace? Naive "later wins" silently throws away parent
content. Maury picks per-content-type defaults — refinement
(concatenate / deep-merge) for additive content like CLAUDE.md,
settings.json, hooks; replacement (file-overlay) for monolithic
content like agents and skills — with per-file frontmatter overrides,
loud per-line suppression annotations, and a `persist: required`
escape hatch. Trade-off: per-content-type merge logic +
suppression-annotation parsing + persist-flag enforcement is a
non-trivial render-engine surface that has to stay correct as new
content types are added.

## Context and Problem Statement

[ADR-0001](0001-n-profiles.md) introduced N profiles with optional
`extends` inheritance. [ADR-0002](0002-repo-per-trust-boundary.md)
introduced the layering `base + profile chain + host overlay →
~/.claude/`. The render engine resolves these layers into a single
output tree.

What's never been pinned down: **when a child layer touches a thing
the parent already defined, does it *refine* the parent or *replace*
it?** Naive file-overlay semantics (later wins) treat every child file
as a full replacement. That's wrong for our use case.

The mental model the user actually has:

> A more-narrow context (home, work, acme-client) usually **refines**
> the broader context. Base says "use ruff." Home says "Frigate runs
> on linux-server." Both apply — home doesn't replace base, it adds to it.
> Some things from the parent should be kept for persistence even
> when the child has its own version of the same content.

That's *refinement*, not *replacement*. Treating every layer as
"later wins" silently throws away parent rules every time a child
mentions the same file.

<details>
<summary><b>Decision drivers</b> (4 items — click to expand)</summary>

- **Tenet 2:** consistency within a profile. The user's
  mental model of inheritance must hold — child *adds to*
  parent, doesn't silently throw it away.
- **Tenet 8:** hand-edits are first-class input. Suppressions
  must be loud (visible in render output), not silent.
- **Per-content-type semantics:** CLAUDE.md, settings.json,
  agents, skills, hooks all have different "merge means
  what" answers; one global rule fits none of them.
- **Migration ergonomics matter:** real users start with
  replacements (it's what they already have); the system
  must help them get to refinement without forcing it on
  day one.

</details>

<details>
<summary><b>Considered options</b> (5 options — click to expand)</summary>

- **Option A:** Naive file-overlay everywhere ("later wins").
- **Option B:** Pure refinement everywhere — no replacement
  at all.
- **Option C:** JSON-merge-style `$op` annotations
  (RFC 7396) for all content types.
- **Option D:** Layer-policy declarations in `profile.yaml`
  (per-content-type policy override at the profile level).
- **Option E (chosen):** Per-content-type defaults
  (refinement for additive content, replacement for
  monolithic content) + per-file frontmatter overrides +
  per-line suppression annotations + a curator-controlled
  `persist: required` escape hatch.

</details>

## Decision Outcome

**Chosen option:** Option E — refinement is the destination.
Replacement is sometimes the starting state, but the system
actively helps you migrate from replacement to refinement
when the moment comes. The well-formed end state of any
maury config is "every child layer refines the parent;
replacement is rare and intentional, never accidental." This
is the only option that matches the user's mental model
without forcing replacement-style content (agents, skills,
bin scripts) into a merge mode that produces mush.

Two consequences:

- **Default semantics is refinement (additive / mergeable).**
  Replacement is explicit, by content type or by per-file annotation.
- **The render engine flags replacement patterns** as candidates for
  promotion-and-refinement, surfaced at reconcile time. The user
  arbitrates whether to migrate (see the
  *Promotion-and-refinement workflow* section below).

### Implementation details

#### Default semantics, per content type

| Content | Default merge | Why |
|---|---|---|
| `CLAUDE.md` (and fragments) | **Concatenate** with provenance markers | Each layer adds rules; nothing is silently lost. Order: base → profile chain (root → leaf) → host overlay. |
| `settings.json` | **Deep-merge** by key (child overrides specific keys, parent persists for others) | Standard JSON-config semantics. Arrays use add-and-dedupe by element identity (e.g., permissions strings). |
| `keybindings.json` | **Deep-merge** by binding key | Child can rebind specific shortcuts without dropping parent's other bindings. |
| `hooks.yaml` | **Concatenate by hook id** (additive); duplicate id = explicit override | Hooks are independently-firing event handlers; default is "all of them fire." Same id in two layers = child overrides parent's definition for that id. |
| `agents/*.md` | **File-overlay (child wins)** with loud warning when child shadows parent | Agents are usually monolithic prompts; refinement-style merging produces mush. Default is replace, but the renderer warns. |
| `skills/*/SKILL.md` | **File-overlay (child wins)** with loud warning | Same as agents. |
| `bin/*` | **File-overlay (child wins)** | Wrapper scripts; replacement is the normal case. |

#### Override mechanisms

For content types that **default to additive** (CLAUDE.md, hooks,
keybindings), the child can:

- **Suppress** an inherited entry via explicit annotation. In CLAUDE.md:

  ```markdown
  <!-- maury-remove: line-matching "Use ruff for linting" -->
  ```

  In hooks.yaml:

  ```yaml
  removed_hooks: [stop-show-pending-captures]
  ```

  In rules.yaml (profile-scoped rules, v2):

  ```yaml
  removed_rules: [code-style-python]
  ```

  Suppression is loud — render output annotates removals so the user
  can see what got dropped at this layer.

For content types that **default to replace** (agents, skills, bin),
the child can:

- **Extend instead of replace** via frontmatter:

  ```markdown
  ---
  name: code-review
  extends-policy: extend
  ---
  # additional content appended to the parent's version
  ```

  Supported extends-policies:
  - `replace` (default for file-overlay content) — child wins fully
  - `extend` — child content concatenated to parent's
  - `prepend` — child content prepended to parent's
  - `extend-section` — split parent at named section markers; child
    fills in named slots (most flexible, most complex)

#### "Keep for persistence" — the user's specific use case

When a parent's content should propagate to children even when the
child has its own version of the same file:

```markdown
---
name: shared-shell-conventions
persist: required   # children cannot suppress this; render fails if attempted
---
```

`persist: required` makes the content immutable across the inheritance
chain. Children may add to it (via `extends-policy: extend`) but
cannot remove or replace it. Useful for security-critical or
identity-defining content the curator wants pinned.

A weaker form, `persist: default`, marks content that auto-persists
unless the child explicitly sets `extends-policy: replace`.

#### Provenance in the rendered output

Every rendered file carries a provenance comment block at the top:

```markdown
<!-- maury rendered file. hand-edits flow through `maury reconcile` so they're captured with provenance (per Tenet 8 — hand-edits are first-class input).
     layers (root → leaf):
       base                    base/CLAUDE.md (sha 8a7f3c1d)
       profile:home            profiles/home/CLAUDE.md.fragment (sha 4e9b4a2c)
       host-overlay:workstation       profiles/home/hosts/workstation/CLAUDE.md.fragment (sha 8f1e7d5b)
     suppressions: 1 line removed by host overlay (see audit)
-->
```

This makes it obvious which layer contributed which content, and
shows when something has been suppressed. The audit log keeps the
full record.

#### What the renderer warns vs. what it errors

- **Warns (continues):** child silently replacing a file by file-overlay
  default (agents/, skills/, bin/). Output prints: "warning:
  `agents/code-review.md` is replaced by profile:home; if this is
  unintended, add `extends-policy: extend` to the child, or run
  `maury refactor promote-common` to migrate."
- **Errors (refuses to render):** child violates `persist: required`
  on a parent. Output: "error: profile:home attempts to suppress
  `shared-shell-conventions` which is marked `persist: required` in
  base. Either lift the persistence flag in base or remove the
  suppression in home."

#### Promotion-and-refinement workflow (replacement → refinement migration)

A common lifecycle:

1. You add `skills/code-review/SKILL.md` to the home profile because
   that's where you happened to be when you first wrote it. It
   doesn't exist in base; it's a "replacement" of nothing.
2. Later you find yourself wanting nearly the same skill in the work
   profile, with two specific differences (different tool list, more
   formal tone). You write a separate one in work. Now two profiles
   replace nothing-in-base with two 90%-similar files.
3. **The end state should be:** the common 90% lives in base; home
   and work each have a small refinement covering their specific
   differences. Refinement, not replacement.

`maury refactor promote-common <path>` (v2 — see followup) walks the
user through this:

```
$ maury refactor promote-common skills/code-review/SKILL.md

Found this file in 2 layers, with substantial overlap:
  profiles/home/skills/code-review/SKILL.md   (89 lines)
  profiles/work/skills/code-review/SKILL.md   (94 lines)
  common content: 78 lines (~85%)

Proposed split:
  base/skills/code-review/SKILL.md            (78 common lines)
  profiles/home/.../SKILL.md                  (extends-policy: extend, 11 unique lines)
  profiles/work/.../SKILL.md                  (extends-policy: extend, 16 unique lines)

  preview the diff for each? [y/N]: y
  ... [shows three-way diff] ...

  apply this refactor? [y/N]: y
  refactored. base now has the common content; home and work refine it.
```

For v1, the renderer's warning surfaces this opportunity but the
actual refactor is a manual operation (edit base/, simplify the
children). For v2, `maury refactor promote-common` automates it
with the same user-arbitration UX `maury reconcile` uses.

This is also the path for retroactively cleaning up a config that
grew up as replacements: run `maury refactor` against the whole tree
and migrate to refinement-everywhere.

### Consequences

- ✅ **Good:** Default behavior matches the user's mental
  model — layers refine, nothing silently disappears.
- ✅ **Good:** Replacement is still possible — just
  explicit. Per-file frontmatter for replaceable content;
  per-line annotations for suppressions in additive content.
- ✅ **Good:** `persist: required` provides a curator-
  controlled escape hatch for content that must propagate
  across all child contexts.
- ✅ **Good:** The renderer becomes the authoritative source
  for "what's actually applied," with provenance comments
  visible in every output file.
- ✅ **Good:** Renderer's provenance comments are key for
  `maury reconcile`
  ([ADR-0017](0017-drift-detection-and-reconciliation.md))
  to know what each layer contributed, so when a hand-edit
  is captured as a proposal it can be routed to the right
  layer.
- ❌ **Bad:** Implementation cost — render engine gains
  merge logic per content type, suppression-annotation
  parsing, and persist-flag enforcement. Each piece is small
  but adds up. Worth it because the alternative is the user
  discovering after the fact that "later wins" silently lost
  their parent rules.

### Confirmation

- The render engine (Phase 3, shipped) implements per-
  content-type merge per the table above.
- Provenance comments appear at the top of every rendered
  file (verifiable by inspecting `~/.claude/CLAUDE.md`
  after a sync).
- `persist: required` violations error out at render time
  with the documented message format.

<details>
<summary><b>Pros and cons of the options</b> (per-option ✅/❌ — click to expand)</summary>

#### Option A: Naive file-overlay everywhere ("later wins")

- ✅ **Good:** Trivially simple to implement.
- ❌ **Bad:** Silently throws away parent content the
  moment a child mentions the same file — the whole point
  of the user's question.

#### Option B: Pure refinement everywhere — no replacement

- ✅ **Good:** Single uniform semantics.
- ❌ **Bad:** Agents and skills don't compose cleanly when
  concatenated; the user legitimately needs the ability to
  fully shadow one in some contexts.

#### Option C: JSON-merge `$op` annotations (RFC 7396)

- ✅ **Good:** Standardized; well-known to JSON tooling.
- ❌ **Bad:** Too obscure for hand-authored content; we
  want annotations to be readable in the source files.

#### Option D: Profile-level layer-policy declarations

- ✅ **Good:** One declaration per profile, less per-file
  noise.
- ⚖️ **Neutral:** The defaults-with-per-file-annotation
  model is more local and easier to audit. Profile-level
  policies could be added later if a real need emerges.

#### Option E (chosen): Per-content-type defaults + frontmatter overrides + suppression annotations + persist flag

- ✅ **Good:** Matches the user's mental model out of the
  box.
- ✅ **Good:** Per-file ergonomics; no global config to
  reason about.
- ✅ **Good:** Suppressions are loud; persist is enforceable.
- ❌ **Bad:** Implementation surface is non-trivial (per-
  type merge + annotation parsing + persist enforcement).

</details>

## Build-order placement

Phase 3 (Render engine) — the per-content-type merge logic
ships with the render engine and was already shipped in
that phase. Per-line suppression annotations and the
`persist: required` enforcement lock down once an
inheritance chain longer than two layers has real content.

## Followups

This ADR's open questions are forward-tracked here:

- **Section markers for `extend-section` policy.** What's the
  delimiter syntax? Markdown headings? Custom comments? Punt to
  implementation; revisit when first user needs sectional extension.
- **Cross-layer line numbers in suppression annotations.** "Suppress
  the line 'Use ruff for linting' from base" — what if base later
  renames the line? Match by content (current proposal) is brittle;
  match by id (would require tagging every suppressible line in
  base) is heavyweight. v1: match by content with a warning when
  no matching line is found.
- **Interaction with mining proposals.** When a mined fragment lands
  in a child profile that defines an `extends-policy: extend` skill,
  does the new content go in the parent or the child? Likely
  child by default; this is captured in the proposal review UI.
- **`maury refactor promote-common`** (v2). The automated
  promotion-and-refinement migration described above. Diff algorithm
  TBD — line-LCS is the obvious starting point, AST-aware refactoring
  is overkill for v1 but interesting for skill content that's mostly
  prose. New task to track once v1 ships.

## Amendment history

- 2026-05-11 — "profile" renamed to "mode" per ADR-0037. Inheritance semantics (refine-by-default, replace-explicit) are unchanged; only vocabulary updated.
