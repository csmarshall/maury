# ADR-0039: Bootstrap and host lifecycle

**Status:** Accepted
**Date:** 2026-05-08

## Related tenets

- [Tenet 1 — First, do no harm](../tenets.md#1-first-do-no-harm)
- [Tenet 2 — Consistency within a mode; controlled difference across modes](../tenets.md#2-consistency-within-a-mode-controlled-difference-across-modes)
- [Tenet 3 — Trust boundaries are physical, not policy](../tenets.md#3-trust-boundaries-are-physical-not-policy)
- [Tenet 5 — The user arbitrates ambiguity](../tenets.md#5-the-user-arbitrates-ambiguity)
- [Tenet 6 — Identity is not name](../tenets.md#6-identity-is-not-name)
- [Tenet 11 — Explicit beats implicit, with conservative defaults](../tenets.md#11-explicit-beats-implicit-with-conservative-defaults)

## Related ADRs

- [ADR-0003](0003-per-host-deploy-keys.md) — per-host deploy keys; SSH-key-driven access
- [ADR-0015](0015-surrogate-keys-for-hosts-and-profiles.md) — `host_<hex>` UUID; `~/.maury-host-id` lifecycle (this ADR scopes the ID to mode)
- [ADR-0016](0016-pluggable-repo-backends.md) — backend pluralism; SSH-key-driven access probing
- [ADR-0018](0018-minimum-bootstrap-ux.md) — `maury init` UX; explicit inputs, no auto-detection magic
- [ADR-0029](0029-maury-state-layout-contract.md) — `~/.claude/maury-state/` layout contract (host-local state)
- [ADR-0037](0037-layer-taxonomy-and-repo-discovery.md) — layer taxonomy; distributed manifest; mode tree; marker file schema; `agency_id`
- [ADR-0038](0038-precept-acquisition-model.md) — advisory dismissal storage in mode repo; reset on mode change

---

## TL;DR

The host lifecycle has four operations: **agency init** (creates the
first base repo + UUID `agency_id`), **new-host bootstrap**
(probes accessible repos, lets the user pick a mode, registers the
host against that mode), **mode change** (two operations:
deregister-then-bootstrap, never a single atomic switch), and
**sync** (mode-scoped subgraph traversal). Host identity is
**mode-scoped**: a given physical machine accumulates one `host_<hex>`
per mode-registration over its lifetime; the IDs never transfer
across mode changes. Advisory dismissals reset on mode change per
ADR-0038. Trade-off: replacement-hardware recovery is a content-copy
rather than an identity-transfer (consistent with Tenet 6: identity
is not name).

## Context

[ADR-0037](0037-layer-taxonomy-and-repo-discovery.md) establishes the
layer taxonomy (`base`, `mode`, `rules`), the **agency** as the
bounded management unit (with stable UUID `agency_id`), and the
distributed manifest in which each layer's marker file declares its
own sublayers. ADR-0037 also establishes that hosts register against
modes — the mode repo's marker file carries the `hosts` dict — not
against base.

[ADR-0038](0038-precept-acquisition-model.md) establishes that
advisory dismissals live in the mode repo, keyed by `host_<hex>`, and
must reset on mode change to honor Tenet 1.

What those ADRs do **not** specify, and what this ADR is responsible
for:

1. **Agency creation.** How does the very first repo of an agency come
   into existence? Where does `agency_id` come from?
2. **Bootstrap.** How does a new host enter an agency for the first
   time? How does it pick a mode? What happens if the user wants a
   mode that does not yet exist?
3. **Host identity.** ADR-0015 specifies `host_<hex>` UUIDs stored in
   `~/.maury-host-id` and described as "never modified after first
   init." That invariant needs reconciling with the fact that a host
   may participate in multiple modes over its lifetime (work, home,
   client engagements). Are IDs stable across modes, or scoped to a
   mode-registration?
4. **Mode change.** How does a host already in `mode:work` move to
   `mode:home` (or to a new mode created at switch time)? How is the
   limbo state ("bootstrapped into the new mode but not yet
   deregistered from the old") avoided?
5. **Sync.** With a distributed manifest spanning many modes, what
   does `maury sync` actually pull?

The non-negotiables carried over from earlier ADRs and the maury
tenets:

- **Tenet 1.** Never leave a host in a limbo state. Validate the new
  mode before deregistering from the old. Never silently carry
  override decisions across mode boundaries.
- **Tenet 3.** Access is enforced server-side via SSH keys. Maury
  probes those keys at bootstrap; nothing else gates the mode choice.
- **Tenet 5.** The user picks the mode. Maury suggests a default; the
  user confirms or overrides.
- **Tenet 6.** Host identity is a `host_<hex>` UUID, not a hostname.
  Hostnames are mutable display labels. Identity must be stable
  within whatever scope is meaningful and never silently transferred
  across substantive changes (such as a different mode of operation).
- **Backend pluralism (Tenet 10 / ADR-0016).** Bootstrap and sync
  must work on every git-compatible backend. Access probing has a
  best-effort fallback for backends without an API for permission
  introspection.

## Decision

### `maury agency init` — agency creation

Before any host can bootstrap, the agency itself must exist. The
first command run when standing up maury for the first time is
`maury agency init`.

Effects:

1. Generate a fresh UUID — the **`agency_id`**. This identifier is
   recorded in every layer's marker file from this moment forward
   and never changes.
2. Initialize a fresh git repo (locally, then pushed to the chosen
   remote) that will be the agency's `base`.
3. Write `.meta/maury-marker.json` with:
   ```json
   {
     "schema_version": 1,
     "layer": "base",
     "agency_id": "<freshly-generated-uuid>",
     "sublayers": []
   }
   ```
4. Commit and push.

After `agency init`, the curator typically creates one or more `mode`
repos (each with its own `.meta/maury-marker.json` carrying the same
`agency_id`) and registers them as sublayers of the base. From there,
hosts can bootstrap.

`maury agency init` is **idempotent** — before generating a new UUID it
checks for an existing `.meta/maury-marker.json` in the target directory.
If one is found, the command refuses and exits with a clear error. A
`--force` flag exists as an escape hatch for genuine resets (e.g.,
the original base repo was lost and a new one must be established), but
it requires explicit acknowledgment because reusing the same repo root
with a new `agency_id` severs all existing host registrations. There is
no inverse command — once an `agency_id` is generated and distributed
to sublayer repos, it is permanent.

---

### Host identity is mode-scoped

The `host_<hex>` ID scheme from ADR-0015 remains in use, with one
critical refinement made explicit here: **IDs are mode-scoped, not
device-stable.**

A given physical machine that bootstraps into multiple modes over its
lifetime accumulates **multiple** `host_<hex>` IDs — one per
mode-registration — and each ID is permanently tied to "this hardware
in that mode."

#### Why mode-scoped

A device-stable ID would create historical ambiguity: looking at a
`host_<hex>` value in a mode's `hosts` dict or in an audit log entry,
you could not tell from the ID alone whether a given change happened
while that hardware was operating in `work` mode or `home` mode. The
lookup would need additional state.

Mode-scoping eliminates the ambiguity at the cost of generating new
IDs on mode change. The trade is worth it: the maury data model is
structured around modes, and an identity scheme that aligns with the
structural grain produces simpler audit semantics.

It also aligns with ADR-0037's structural fact that hosts register
against modes (not against base) — there is nowhere agency-wide to
record a stable cross-mode host ID even if the design wanted one.

#### What `~/.maury-host-id` stores

`~/.maury-host-id` stores the **current mode-registration ID** — the
`host_<hex>` for whichever mode this hardware is presently
bootstrapped into. Not a permanent device ID.

- Bootstrap into `mode:work` → `host_abc...` written to
  `~/.maury-host-id`.
- Deregister from `work` → `~/.maury-host-id` is cleared.
- Bootstrap into `mode:home` → fresh `host_xyz...` written to
  `~/.maury-host-id`.

`host_abc...` and `host_xyz...` are different identities. The old ID
stays retired in `mode:work`'s `hosts` dict (retained for audit;
never reused).

ADR-0015's "never modified after first init" invariant applies **per
registration**: within a single mode-registration, `~/.maury-host-id`
is written exactly once at bootstrap and never modified.
Deregistration ends the registration; the next bootstrap is a fresh
registration with a fresh write.

#### Recovery: replacement hardware

A user whose laptop fails (dropped in a pool, stolen, etc.) recovers
by bootstrapping a new laptop fresh in the relevant mode. The new
bootstrap mints a new `host_<hex>` ID. The curator then copies the
**content** of any host-tagged sections (or the contents of the old
private narrow-scoped child mode repo, if the escape hatch was in
use — see ADR-0037) from the old hardware to the new.

The old `host_<hex>` ID stays retired in the mode's `hosts` dict. It
is never re-assigned to the new hardware. Audit history that
references the old ID continues to mean exactly what it always meant:
"the hardware that was once this `host_<hex>` in this mode."

This recovery flow is intentionally explicit. There is no "transfer
identity" command, because identity should not be transferable —
Tenet 6 says identity is not name, and a device-replacement event is
precisely the kind of substantive change that must not silently
inherit identity from the predecessor.

---

### New host bootstrap

#### Inputs

The curator (or the user themselves, if they are their own curator)
provides exactly two pieces of bootstrap input via the environment:

1. **Base repo URL** — the canonical entry point into the agency
   (per ADR-0037, exactly one base per agency).
2. **SSH key(s)** — already installed in the user's SSH agent or
   referenced by path. Per [ADR-0003](0003-per-host-deploy-keys.md),
   these are per-host deploy keys; maury does not generate or rotate
   them at bootstrap.

These inputs match
[ADR-0018](0018-minimum-bootstrap-ux.md)'s "explicit inputs, no
auto-detection magic" principle. Maury asks the user for them; it
does not search the filesystem for plausible candidates.

#### Flow (eight steps)

```
1. Curator provides base repo URL + SSH keys via environment.

2. Maury clones the base, reads .meta/maury-marker.json, and
   traverses the full distributed-manifest sublayer graph
   (every layer reachable from base, including all modes in
   the tree and every rules repo).

3. Probes read/write access per repo using the available SSH keys.

4. Filters the set of modes to those for which every required repo
   (base + ancestors of the candidate mode + that mode's own
   declared sublayers) is technically accessible.

5. Suggests a default mode based on environment signals:
     - git user.email
     - hostname
     - SSH key fingerprint

6. User confirms suggestion OR picks from the filtered accessible list.

7. Optionally, user creates a NEW mode at bootstrap time, declaring
   an existing mode as ancestor (see "Step 7: new mode creation"
   below).

8. Maury generates a fresh host_<hex> UUID (per ADR-0015), records
   the host entry in the chosen mode's marker file under "hosts"
   (with registered_at and environment_tags), commits and pushes
   the mode marker, stores the new host_<hex> locally at
   ~/.maury-host-id.
```

#### Step 2: full-graph traversal

Maury traverses the **entire** distributed manifest graph starting
from the base, not just the candidate mode's subgraph. The reason is
the filter step (step 4) — to know which modes are technically
accessible, maury must walk each mode's dependency chain end-to-end.

This is the **only** time the full agency graph is traversed during
normal operation. After bootstrap, `maury sync` is mode-scoped (see
"Sync behavior" below).

#### Step 3: access probing

Per repo in the graph, maury attempts:

- **Read probe.** A shallow `git ls-remote` (or backend equivalent
  per ADR-0016's adapter interface) using the available SSH keys.
  Success = repo is technically readable.
- **Write probe.** Backend-specific. For platform backends with a
  permissions API (e.g., a `github` adapter using `gh api
  repos/.../keys`), maury inspects whether the deploy key has push
  permission. For plain `git` backends without a permissions API,
  write permission cannot be probed without an actual write attempt,
  which maury declines to perform at bootstrap. In that case
  write-mode is recorded as **unknown** rather than guessed.

Probing is best-effort. A repo that times out or returns an
ambiguous error is treated as inaccessible **for filtering purposes**
at this step; the user can override the filter and proceed manually
if they know the probe is wrong.

#### Step 4: filtering modes to accessible ones

A candidate mode `C` is **technically accessible** if and only if
every repo in `C`'s required dependency set is reachable read-only or
better. Required dependency set = base + every ancestor mode's
sublayers + `C`'s own sublayers.

Inaccessible modes are filtered **out** of the user-facing
suggestion list. Maury surfaces a one-line note for each filtered-out
mode: *"mode `work:client-acme` is not accessible from this host
(cannot reach `git@github.com:acme-corp/rules-team.git`)."* This is
informational; it is not an error.

#### Step 5: default suggestion

Maury suggests a default mode using environment signals:

- **git `user.email`.** A `user.email` of `alice@acme.com` hints at
  `mode:work` over `mode:home`.
- **Hostname.** A hostname containing `work-laptop` or `client-`
  hints at the corresponding mode.
- **SSH key fingerprint.** A key whose fingerprint matches one
  recorded in a mode's known-host metadata is a strong signal.

Signals are combined heuristically. Maury surfaces the reasoning
(*"suggesting `work:client-acme` because git user.email matches
`*@acme.com` and SSH key fingerprint matches `work-laptop`'s known
key"*) so the user can verify before confirming.

The suggestion is never auto-accepted. Per Tenet 5, the user confirms
or picks an alternative.

#### Step 6: user confirmation

The user is presented with:

- The suggested default mode.
- The full filtered list of accessible modes.
- An option to **create a new mode** (step 7).
- A prompt to declare the host's `environment_tags` (free-form
  list of strings — e.g., `["macos", "laptop", "home-office"]` — used
  by the render engine to apply env-tagged sections per ADR-0037).

They confirm. There is no `--non-interactive` path for first
bootstrap; the user is in front of the host the first time it joins
an agency.

#### Step 7: new mode creation at bootstrap time

If the user wants a mode that does not exist (e.g., a new client
engagement they are setting up on this laptop), they can create one
inline:

- Maury asks for the new mode's name and target repo URL.
- Maury asks for the **ancestor mode** — an existing mode in the
  tree the new one will inherit from. Per ADR-0037 the mode tree is
  arbitrary-depth; the new mode becomes a child of the chosen
  ancestor.
- Maury creates the new mode repo via the backend's `create_remote`
  capability (per ADR-0016, where the backend implements it) and
  writes its `.meta/maury-marker.json`:
  ```json
  {
    "schema_version": 1,
    "layer": "mode",
    "agency_id": "<the agency's id>",
    "sublayers": [],
    "hosts": {}
  }
  ```
- Maury edits the **ancestor mode's** marker file (or the base
  marker if the new mode is top-level) to add a `sublayers` entry
  pointing at the new mode repo. Per ADR-0037, this requires the
  user to have `repo_mode: rw` against the ancestor; otherwise the
  edit must go via PR.
- Maury commits and pushes both updates.
- Bootstrap proceeds with the newly-created mode as if it had
  existed all along.

This path closes the chicken-and-egg gap: a host arriving fresh can
set up a previously-undefined working mode without first switching
to another machine to register the mode.

The "curator" role is rules-only (ADR-0038's notion of "who reviews
PRs against this rules repo"). For modes there is no separate curator
role; access level on the parent mode is the only gate. A user with
`repo_mode: rw` on the parent mode can add a child mode by editing
its `sublayers`. A user with `repo_mode: pr` must contribute the
sublayer addition via PR. A user with `repo_mode: ro` cannot add
child modes.

#### Step 8: host identity creation and mode registration

- Maury generates a fresh `host_<32 hex>` UUID via `uuid4().hex`. This
  is the **mode-registration ID** for this hardware in this mode.
- The ID is written to `~/.maury-host-id`. Within this registration,
  the file is never modified again.
- Maury edits the chosen mode's `.meta/maury-marker.json` to add the
  host entry under `hosts`:
  ```json
  "hosts": {
    "host_<new-id>": {
      "registered_at": "<RFC 3339 now>",
      "environment_tags": ["<tags from step 6>"]
    }
  }
  ```
- Maury commits and pushes the mode marker update.

The host is now bootstrapped into the chosen mode.

---

### Host identity conflict detection

`maury` commands that need "which host am I" read `~/.maury-host-id`
and look up the matching entry in the active mode's `hosts` dict.
The three states are:

| State | Local file | Active mode marker | Action |
|---|---|---|---|
| **Normal** | matches `host_<id>` | entry for `host_<id>` exists; metadata consistent | proceed |
| **Unregistered** | matches `host_<id>` | no entry for `host_<id>` | run bootstrap (or refuse the operation if the user has not yet bootstrapped); a registration may have been retired |
| **Conflict** | matches `host_<id>` | entry exists but metadata is inconsistent | **WARN** |

#### Metadata consistency check

"Consistent" means the data we expect to be stable matches:

- The mode the local file thinks the host is registered in matches
  the mode whose marker file lists this `host_<hex>`.
- `environment_tags` recorded against the host entry match the local
  bootstrap-time declaration (best-effort: tags may legitimately
  evolve via `maury host retag`).

A mismatch is most often the `~/.maury-host-id` file having been
**copied from another machine** — a duplicate that creates a real
risk of two pieces of hardware thinking they are the same
registration. The warning is loud and the user must arbitrate (Tenet
5).

The recovery path for a copied host-id file is:

1. Delete `~/.maury-host-id` on the host that copied it in.
2. Re-run bootstrap. A fresh `host_<hex>` is generated; the host
   registers as a distinct mode-registration.

Maury does **not** auto-correct this. The decision belongs to the
user.

#### "Unregistered" can also mean "previously deregistered"

If the local `~/.maury-host-id` matches an ID that has been
deregistered from its original mode (and is therefore no longer
present in any mode's `hosts` dict), the host appears as
**Unregistered** to maury. The recovery path is the same as for a
never-bootstrapped host: run bootstrap, which generates a new ID for
a fresh registration.

The retired ID stays retired; it is never reactivated. This is a
property of the mode-scoped identity model — re-using a retired ID
would re-introduce exactly the historical-ambiguity problem
mode-scoping was introduced to eliminate.

---

### Mode change process

A mode change is **two separate atomic operations**, not one
transaction. Because IDs are mode-scoped, the result is also a fresh
`host_<hex>` registration in the new mode:

```
1. VALIDATE the new mode (dry-run traversal; confirm all repos
   accessible; surface any blocking conditions).
        |
        | fails -> ABORT. No state change. User can fix the gap
        |          (request access, install missing key) and retry.
        v
2. DEREGISTER from the current mode (atomic):
   - Force state storage flush (commit + push pending writes).
   - Retire the current host_<hex>: remove the entry from the
     current mode's hosts dict; commit + push the mode marker.
   - Clear ~/.maury-host-id.
        |
        v
3. BOOTSTRAP into the new mode (atomic), using the standard bootstrap
   flow (steps 2-8 above). A NEW host_<hex> is generated for this
   new registration; a NEW host entry is written into the new mode's
   marker file.
```

The validate step is the load-bearing part. It is a **dry-run** of
step 4 of the bootstrap flow targeted at the new mode: is every repo
in the new mode's dependency set accessible? If no, the mode change
fails before any state is written — **before** the host is
deregistered from the old mode. There is no limbo state.

Both deregister and bootstrap are individually atomic (each
completes or leaves the host in a recoverable state with a single
`~/.maury-host-id` write). Crucially, there is no single "switch from
A to B atomically" transaction — that would require cross-repo
distributed commits, which do not exist in git and which the model
forbids (Tenet 1 forbids inventing distributed-systems failure modes
where they are avoidable).

#### Why two operations, not one

A single transaction "switch from A to B atomically" would require
either a distributed-commit primitive across multiple git repos (the
old mode's marker, the new mode's marker, possibly the base marker)
— which does not exist — or a maury-side coordinator that can roll
back partial writes across repos — which is the distributed-systems
failure mode Tenet 1 forbids.

Splitting into two operations with an explicit pre-flight validate
gives the same safety property (the host is never in a state where
neither old-registration nor new-registration is true *and the user
has been led to believe one is*) without inventing cross-repo
transactions. If deregister succeeds and bootstrap fails (e.g.,
network outage between the two), `~/.maury-host-id` is cleared and no
mode is active — the host is in a recoverable state and can rerun
bootstrap when the network comes back.

#### Advisory dismissals reset

Per [ADR-0038](0038-precept-acquisition-model.md), advisory
dismissals are stored in the **mode repo** at
`.meta/maury-advisory-state.json`. Switching modes means switching
mode repos, which means starting from whatever the new mode's
advisory-state file already records — typically empty for a host
arriving fresh.

This is intentional. Per Tenet 1, override decisions made under one
mode cannot silently carry into a new one. The user re-evaluates
every override decision in the new mode.

#### What carries over

- **Hardware.** The physical machine itself is the same.
- **`~/.claude/maury-state/` host-local files** (per ADR-0029). The
  host-local audit log, drift baseline, etc., do not reset on mode
  change. They may want to be archived per registration; that is a
  separate concern handled by ADR-0029 conventions.
- **SSH keys, OS-level config, anything outside maury's data model.**

#### What does not carry over

- **The `host_<hex>` ID.** The old ID is retired; a new ID is minted
  by the new bootstrap.
- **`~/.maury-host-id`.** Cleared during deregistration; rewritten
  with the new ID during the next bootstrap.
- **The host's entry in the old mode's marker.** Removed during
  deregister.
- **Advisory dismissals** (per ADR-0038, intentionally).
- **Active mode** (replaced).
- **Per-mode state** (a different mode repo is now in scope).

#### Replacement-hardware recovery, restated

A common scenario where the new-bootstrap-creates-a-new-ID behavior
matters: the user's laptop dies and they buy a new one in the same
mode (e.g., still working at the same job). Steps:

1. New laptop runs bootstrap → fresh `host_<hex>` ID.
2. If the old setup used host-tagged sections in the mode repo: the
   user copies the relevant sections (or asks maury to clone them
   under the new `host_<hex>` key as a starting point).
3. If the old setup used the private-narrow-scoped-child-mode-repo
   escape hatch: the curator re-registers the new host's child mode
   repo as a sublayer of the parent mode and copies content forward.
4. The old `host_<hex>` ID stays retired in the mode marker; audit
   history that references it remains accurate.

There is no "I want to keep the old ID for this new laptop" path.
The model treats the laptop as new hardware, even though the user's
experience is "I just got a replacement."

---

### Sync behavior

#### Mode-scoped subgraph traversal

`maury sync` traverses **only the subgraph** required by the host's
currently active mode-registration:

- Base.
- The full ancestor chain of the active mode.
- The active mode's own sublayers, including every `rules` repo
  declared at any level in the chain.

Repos outside this subgraph are not fetched. They are not warned
about. They are simply outside the scope of work the host needs to
do for the active mode.

This is the locality property at sync time: a host on `mode:work`
does not pull `mode:home`'s repos every time it syncs, even though
the agency's distributed manifest knows about both.

#### Full-graph traversal each sync

There is **no caching** of the per-mode subgraph between syncs.
`maury sync` traverses the full distributed manifest graph from the
base outward to determine which subgraph is in scope, then fetches
only that subgraph.

The redundancy is a deliberate maintenance-surface decision: caching
the subgraph would create coherence problems (when does the cache
invalidate? what if the manifest moves a layer between modes?). At
agency scale (tens to hundreds of repos), the traversal cost is
small.

#### Unreachable repos within the active subgraph

A repo within the host's active-mode subgraph that fails to fetch
(network outage, key revocation, server down) does not abort the
sync. Maury:

- Warns: *"could not reach `git@github.com:acme-corp/mode-work.git`;
  using last known state."*
- Uses the last successfully fetched state of that repo for
  rendering.
- Marks the sync as **partial** in the audit log.

A partial sync is operational, not failed. The host is not left in a
broken state; it is rendering with last-known content for the
unreachable repos and current content for the rest. Subsequent syncs
will retry and succeed once the repo becomes reachable.

#### Unreachable repos outside the active subgraph

Repos outside the active-mode subgraph are not fetched at all. Their
reachability is not probed. Their unavailability is not warned
about. A user on `mode:work` has no reason to be told that
`mode:home`'s mode repo is unreachable from their work network —
that's the expected state.

#### `--all` for curators

`maury sync --all` overrides the mode-scoped behavior and traverses
the full agency graph, fetching every repo the host has access to.
This is for curator workflows: validating the agency's health,
generating a complete `maury repos list`, preparing for a base
replacement.

`--all` is a curator-side affordance. It is not the default; the
default sync is mode-scoped per the locality principle.

---

## Consequences

- **Good:** New hosts cannot bootstrap into an unreachable mode. The
  probe-and-filter step is the gate.
- **Good:** Mode change has no limbo state visible to the user.
  Validate-then-deregister-then-bootstrap is safe across multiple git
  repos without inventing cross-repo transactions.
- **Good:** Mode-scoped host identity makes audit history
  unambiguous: any `host_<hex>` reference always means a specific
  hardware-in-mode registration. Tenet 6 is honored without
  device-stable IDs.
- **Good:** Sync is mode-scoped. A host on one mode does not waste
  time fetching another mode's repos.
- **Good:** Curator-side `--all` is available when the full agency
  view is needed, without leaking that cost into the default UX.
- **Good:** New modes can be created at bootstrap time, closing the
  chicken-and-egg gap.
- **Good:** Hosts register against modes (per ADR-0037's marker
  schema), aligning the structural location of host data with the
  scope where it is meaningful.
- **Good:** `maury agency init` produces a stable agency identity
  (`agency_id`) that survives every subsequent rename, re-home, or
  base replacement.
- **Neutral:** Full graph traversal each sync. No cache; no coherence
  problem. Acceptable at agency scale.
- **Neutral:** Access probing is best-effort. Plain `git` backends
  cannot probe write permission; recorded as "unknown" rather than
  guessed.
- **Neutral:** The default-suggestion heuristic (`user.email`,
  hostname, key fingerprint) is informational only. The user always
  confirms.
- **Bad:** Advisory dismissals reset on mode change. Users who switch
  modes pay the cost of re-evaluating override decisions. This is
  intentional (Tenet 1).
- **Bad:** Mode change generates a new `host_<hex>` ID; there is no
  "transfer" of identity. The old ID stays retired. Replacement-
  hardware recovery is a content-copy, not an identity-transfer.
- **Bad:** Conflict detection on `~/.maury-host-id` mismatch surfaces
  a warning but does not auto-correct. Users must understand the
  recovery path (delete and re-bootstrap).

### Confirmation

- `maury agency init` generates a UUID, writes `.meta/maury-marker.json`
  with `layer: base`, commits and pushes; tested by asserting the
  resulting marker contains a valid UUID and `sublayers: []`.
- `maury init` (or `maury bootstrap` flow) implements the eight-step
  bootstrap described above; tested against an agency with
  mixed-accessibility modes to confirm filtering.
- `~/.maury-host-id` lifecycle:
  - Within a single mode-registration: written exactly once at
    bootstrap; never modified.
  - On deregistration: cleared.
  - On the next bootstrap: a fresh ID written; the prior ID remains
    retired in the prior mode's marker.
- Conflict detection: a host whose `~/.maury-host-id` does not match
  the active mode's marker entry surfaces a WARN per the table
  above; covered by an integration test that copies the file between
  two test hosts.
- Mode change is implemented as a sequence of two atomic commands
  (`maury mode deregister`, then `maury init` / `maury mode
  bootstrap`) with a pre-flight validate. There is no single
  `maury mode switch` that wraps both into a pseudo-transaction.
- The new bootstrap mints a new `host_<hex>` ID; tested by asserting
  that the `~/.maury-host-id` value after a mode change differs from
  the value before, and that the old ID is present-but-retired in
  the old mode's marker.
- `maury sync` traverses only the active mode's subgraph by default;
  `maury sync --all` traverses the full agency. Tested against an
  agency with two top-level modes to confirm one is not fetched
  while the other is active.

---

## Build-order placement

- **When implemented — `maury agency init`.** The first command in
  the agency creation pathway; must land before any host can
  bootstrap.
- **When implemented — bootstrap flow.** Lands alongside the Phase
  4-style bootstrap commands once the layer-taxonomy primitives from
  ADR-0037 are in place.
- **When implemented — access probing.** Per-backend probe capability
  lives in the adapter interface from ADR-0016. Initial scope: `git`
  backend (read-probe only) and `github` backend (read-probe +
  write-probe via `gh api`).
- **When implemented — mode-change atomic sequence.** Builds on the
  bootstrap flow.
- **When implemented — mode-scoped sync.** Builds on ADR-0037's
  distributed manifest traversal.
- **When implemented — `--all` flag.** Trivial extension of the sync
  verb once mode-scoped traversal exists.

---

## Open followups

- **Curator-side `maury bootstrap host`.** ADR-0015 mentions a
  curator-side command that registers manifest data for a new host
  from a different machine. Reconciling that with the mode-scoped
  identity model: the curator cannot mint the ID in advance (the ID
  is generated by the new hardware on its own bootstrap), but the
  curator can pre-write metadata that the new bootstrap reads
  (e.g., "this hardware should default to `mode:work`"). The full
  UX of that pre-staging remains under consideration.
- **Probing write permission for plain `git` backend.** Today the
  result is "unknown" rather than a guess. A future backend extension
  could attempt a no-op write (e.g., `git push --dry-run` against a
  `refs/maury/probe/*` namespace) but the cost-benefit is unclear;
  documented as a known limit.
- **Mode-tree visualization.** A `maury mode tree` command that
  prints the available modes in tree form, accessible vs.
  inaccessible, with the current mode highlighted. Useful for users
  with deep nesting.
- **Recovery from a partially-completed mode change.** If deregister
  succeeds and bootstrap fails (network outage between the two
  atomic operations), the host is in a recoverable state —
  `~/.maury-host-id` is cleared, no mode is active. The recovery
  path is "re-run bootstrap"; the validate step on retry ensures
  the user does not silently slide into a worse state.
- **Default-suggestion heuristic refinement.** The current signals
  (`user.email`, hostname, SSH key fingerprint) are reasonable but
  not exhaustive. A future iteration may consult environment-tag
  declarations (per ADR-0037's environment-content model) to produce
  stronger suggestions.
- **Decommission.** A piece of hardware that is being retired (sold,
  scrapped) should have its current registration deregistered
  cleanly. The mode-deregister flow handles per-mode membership;
  full decommission is a separate concern (touches host backups,
  audit log retention, etc.) and a future ADR.
- **Replacement-hardware tooling.** A `maury host copy-content
  <old-host-id> <new-host-id>` curator command would reduce the "I
  just got a new laptop" friction by copying host-tagged sections
  forward in one step. Today the steps are explicit edits; a wrapper
  is a quality-of-life followup, not a model change.
- **Agency rename / re-home.** Today the `agency_id` is permanent; the
  base repo URL can change (re-host the base elsewhere, then update
  every consuming layer's marker). A `maury agency rehome` command
  would automate the URL update across an agency's layers.

---

## Claude Code references

This ADR makes no claims about Claude Code behavior. Bootstrap, host
identity, mode change, agency creation, and sync traversal are
entirely maury-internal concerns. Once a mode is bootstrapped and
synced, the render engine concatenates layer content into a single
CLAUDE.md per the contract documented in
[`cc-contract:startup-files-loaded`](../claude-code-contract.md#cc-contractstartup-files-loaded);
Claude Code is unaware of the bootstrap, sync, or mode-change
machinery that produced the rendered file.

## Amendment history

None.
