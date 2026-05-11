# ADR-0014: Host-local secrets with metadata-only sync

**Status:** Accepted
**Date:** 2026-05-06

## Related tenets

- [Tenet 3 — Trust boundaries are physical](../tenets.md#3-trust-boundaries-are-physical-not-policy)
- [Tenet 4 — Sensitive data stays local](../tenets.md#4-sensitive-data-stays-local)
- [Tenet 6 — Identity is not name](../tenets.md#6-identity-is-not-name)
- [Tenet 9 — Defer to the platform](../tenets.md#9-defer-to-the-platform)
- [Tenet 10 — Modularity over hardcoding](../tenets.md#10-modularity-over-hardcoding)

## TL;DR

Some hosts hold credentials Claude needs to use locally — but
credential **values** must never cross hosts, while **metadata** (this
service exists, this credential's last-rotated timestamp, what's
running where) is genuinely useful for restore and migration. Maury
splits these into two stores: a **host-local value store**
(capability-driven backend: macOS Keychain / Linux Secret Service /
age-encrypted file fallback) and a **synced metadata manifest**
(`secrets.json` in the host overlay) carrying presence + endpoints +
timestamps but never values. Trade-off: three backends to maintain and
two stores for the user to reason about.

## Context and Problem Statement

Some hosts run local services that require credentials Claude needs to
interact with — typical homelab pattern. The credential **value** is
host-specific; persisting it across hosts or to git would be a
data-handling violation. But information *about* the credential
(that it exists, which service, which endpoint, when it was last
rotated) is genuinely useful:

- After a system crash + restore, you need to know what was set so you
  can re-populate it.
- When migrating a service from host A → host B, you need a checklist
  of credentials to bring along.
- When debugging "why doesn't Claude on workstation work with service X," it
  helps to see "X's credential is registered on linux-server, not workstation."

This is a clean two-tier separation that maury should support
explicitly: **values are host-local**, **presence metadata is
syncable**.

<details>
<summary><b>Decision drivers</b> (6 items — click to expand)</summary>

- **Tenet 3:** trust boundaries are physical. Credential
  values must never cross a host boundary; that has to be a
  structural property, not a policy hope.
- **Tenet 4:** sensitive data stays local. Values stay on
  the host that set them; only metadata syncs.
- **Tenet 6:** identity is not name. Credentials are
  identified by (service, name) tuples that survive
  endpoint/URL changes.
- **Tenet 9:** defer to the platform. Use the OS keychain
  where one exists; only fall back to file-based storage
  when no native keystore is available.
- **Tenet 10:** modularity over hardcoding. Backend choice
  must be capability-driven, not OS-hardcoded.
- **Recovery use cases are real.** "I crashed; what
  credentials did I have?" needs an answer.

</details>

<details>
<summary><b>Considered options</b> (5 options — click to expand)</summary>

- **Option A:** Sync values into encrypted git (chezmoi+age,
  git-crypt, etc.).
- **Option B:** No metadata at all; rely on operator memory.
- **Option C:** Single uniform backend (e.g., age-encrypted
  file everywhere).
- **Option D:** External secrets manager (Vault, 1Password)
  as the only backend.
- **Option E (chosen):** Two stores — host-local value store
  (capability-driven backend) + synced metadata manifest with
  no values, ever.

</details>

## Decision Outcome

**Chosen option:** Option E — two independent stores. The
host-local value store uses the OS keychain where available
(macOS Keychain, Linux Secret Service) and falls back to an
age-encrypted file. The synced metadata manifest records
presence + endpoints + rotation timestamps but never values.
This is the only option that satisfies the trust-boundary
requirement structurally (values can't leak because they're
never in synced storage) while still supporting
recovery/migration use cases.

### Implementation details

#### 1. Host-local secret-value store (NEVER synced)

Lives outside any synced repo path. Backend selected by capability
probe:

| Host capability | Backend |
|---|---|
| macOS | Keychain (`security add-generic-password`) |
| Linux with libsecret | Secret Service (`secret-tool`) |
| FreeBSD / fallback | age-encrypted file at `~/.maury-secrets.age` |

The encrypted-file fallback is the uniform substrate; the keychain
backends are convenience layers on top. The probe writes
`secret_backend: keychain | secret-service | age-file` into
`capabilities.json`.

#### 2. Synced metadata manifest (per-host overlay)

Lives at `<repo>/profiles/<profile>/hosts/<host>/secrets.json` —
inside the host overlay, so it travels with the host's other config.
Schema:

```json
{
  "version": 1,
  "services": [
    {
      "name": "service-alpha",
      "endpoint": "http://localhost:7878",
      "running_on_this_host": true,
      "added": "2026-05-04"
    }
  ],
  "credentials": [
    {
      "service": "service-alpha",
      "credential_name": "service-alpha-admin-token",
      "kind": "api-key",
      "description": "admin token for the local instance",
      "last_set": "2026-05-04T10:00:00Z",
      "last_used": "2026-05-06T15:00:00Z",
      "value_present": true
    }
  ]
}
```

**No values, ever, in this file.** Defense-in-depth:

- `value_present: bool` records "the value exists in the local store"
  but the value itself is in the keychain or age-file.
- A pre-commit hook in every synced repo refuses to commit any file
  whose content matches plausible-secret patterns (high-entropy
  strings, `BEGIN PRIVATE KEY`, etc.) within `secrets.json` paths.
- The render engine refuses to write any file under `~/.claude/`
  that contains a path or content that looks like a value-store
  destination — the value store path is reserved.

#### Service-presence vs credential-presence are separate

The `services` list captures what's *running* on the host (presence +
endpoint), independent of whether maury holds credentials for it. The
`credentials` list cross-references services by name. This way:

- A host can register "service-alpha is running here at port 7878"
  without yet having credentials for it.
- A migration story: "I'm moving service-alpha from linux-server → firewall"
  → update both the `services` entry on each host and the
  `credentials` entries; the values must be re-set on the new host.

#### CLI surface

```sh
maury secret set <name> [--service=...] [--kind=api-key]
    # Prompts for value (no echo); writes to host backend; updates manifest
maury secret list
    # Names + metadata only; values never displayed
maury secret get <name>
    # Reads value from host backend; for use by Claude/scripts
maury secret unset <name>
    # Removes both backend value and manifest entry
maury secret restore-checklist [--host=<other>]
    # Generates "you need to re-populate these N credentials" listing,
    # optionally targeting another host's manifest

maury service register <name> --endpoint=...
maury service list
maury service unregister <name>
```

#### Claude Code integration

- Claude on a host can call `maury secret get <name>` when it needs a
  credential value to perform a task. The value flows into Claude's
  context only at use time, not at idle.
- Claude observing a new credential being set during a task can call
  `maury secret register-pending <name> --service=...` to flag it; the
  user provides the value out-of-band. The transcript records that a
  credential exists, not its value.
- A `Stop` hook is added to remind: "secrets-pending: N credentials
  registered without values; run `maury secret set <name>` to populate."
  Per [ADR-0025](0025-profile-switching-session-safeguards.md)'s
  hook-cadence note, `Stop` fires once per *turn* (each
  user-prompt → assistant-response cycle), not once per session.
  For a low-noise reminder this is the right cadence ("remind me
  after each exchange while there are pending secrets"); if it
  proves too noisy in practice, switching to `SessionEnd`
  (once-per-session) is the trivial alternative.

#### Privacy of metadata itself

The metadata IS sensitive in some environments — knowing "host X has a
credential for service Y" can leak information. Three knobs:

- **`push_policy: disabled`** (already in ADR-0009) — paranoid hosts
  do not push the metadata manifest, only consume.
- **Per-credential `private: true`** — flagged credentials are stored
  locally in the manifest but **excluded from git**. The `secrets.json`
  file in the host overlay contains only non-private entries. A
  separate `secrets.local.json` (gitignored) holds private entries.
- **Service endpoint redaction** — endpoints can be tagged
  `endpoint_visibility: local-only`; in that case, the synced manifest
  records the credential's *existence* but not the URL.

### Consequences

- ✅ **Good:** After a host crash, `maury secret
  restore-checklist` produces an exact list of credentials and
  services that need re-population.
- ✅ **Good:** Service migrations gain a clear cross-host
  view — "what's running where, what credentials are tied to
  what, what needs to move."
- ✅ **Good:** Defense-in-depth at four layers — backend
  separation, content-pattern pre-commit hook, render-engine
  path reservation, and per-credential `private` flag.
- ⚖️ **Neutral:** Multi-backend support adds complexity but
  keeps the abstraction clean — the same `secret
  get/set/list` interface across macOS Keychain / Linux
  Secret Service / age-encrypted file.
- ❌ **Bad:** Two new schemas (`secrets.json`, capability
  probe gains a `secret_backend` field), one new module
  (`src/maury/secrets/`), four new CLI subcommands. Real
  surface-area cost.

### Confirmation

- `secrets.json` schema validated by the manifest loader;
  pre-commit hook refuses high-entropy or `BEGIN PRIVATE KEY`
  content within `secrets.json` paths.
- Capability probe surfaces `secret_backend ∈ {keychain,
  secret-service, age-file}`; render engine reserves the
  value-store path so no rendered file collides with it.
- `maury secret list` never displays values; `maury secret
  get` reads from the host backend at use time only.

<details>
<summary><b>Pros and cons of the options</b> (per-option ✅/❌ — click to expand)</summary>

#### Option A: Sync values into encrypted git

- ✅ **Good:** One store; familiar tooling (chezmoi+age,
  git-crypt).
- ❌ **Bad:** Mixes value bytes with sync bytes — one
  mistake in encryption config and a value leaks across
  every host with read access.
- ❌ **Bad:** Two-store separation is unconditionally safer
  for the same operational cost.

#### Option B: No metadata at all; rely on operator memory

- ✅ **Good:** Zero new schema; zero leak surface.
- ❌ **Bad:** Defeats the restore/migration use cases — the
  whole point of the feature.

#### Option C: Single uniform backend (age-file everywhere)

- ✅ **Good:** One backend to test and document.
- ❌ **Bad:** OS keychain where available is friendlier
  (no extra password prompt, integrates with system access
  patterns).
- ⚖️ **Neutral:** Falling back to age-file gives us
  uniformity where keychain isn't available — which is
  what Option E does.

#### Option D: External secrets manager (Vault, 1Password)

- ✅ **Good:** Battle-tested storage with rich access
  controls.
- ❌ **Bad:** External dependency on every host; defeats
  Tenet 9's "defer to the platform" — these are *another*
  platform.
- ⚖️ **Neutral:** Could be added as a fourth backend in v2.

#### Option E (chosen): Two stores — host-local values + synced metadata

- ✅ **Good:** Trust boundary is structural — values
  literally never enter synced storage.
- ✅ **Good:** Recovery and migration use cases work via
  metadata.
- ✅ **Good:** Capability-driven backend uses native
  keystores where present.
- ❌ **Bad:** Multi-backend complexity (three backends to
  maintain).
- ❌ **Bad:** Two stores for the user to reason about.

</details>

## Build-order placement

New phase **Phase 5.6 — Host-local secrets with metadata
sync.** Sits after Phase 5 (sync workflow) so the metadata
manifest can ride the existing render+sync mechanism.
Independent of mining. Tracked as task #15.

## Followups

- **External-secrets-manager backend** (Vault, 1Password) as
  a v2 fourth backend if a real use case appears.
- **Auto-memory consumption of credential events** —
  Anthropic's auto-memory may capture "user set a token"
  events; investigate consuming as a secondary signal so
  `register-pending` can be auto-suggested.
- **Cross-host secret-mismatch detection** — surface
  "linux-server has credential X but workstation doesn't"
  in `maury status`, since this is a common
  "why-isn't-it-working" failure mode.

## Claude Code references

Verified-as-of 2026-05-07 against Anthropic's official Claude
Code documentation:

- [`cc-hooks`][cc-hooks] — `Stop` event semantics. The
  pending-secrets reminder uses `Stop` because it fires once
  per turn (per the
  [`cc-contract:event-firing-cadence`](../claude-code-contract.md#cc-contractevent-firing-cadence)
  table), giving a low-noise "after each exchange" cadence.
  `SessionEnd` is the trivial alternative if `Stop` proves
  too noisy in practice.

[cc-hooks]: https://code.claude.com/docs/en/hooks

## Amendment history

- 2026-05-11 — "profile" vocabulary renamed to "mode" per ADR-0037 doctoral examination. References to "profile" in this ADR now read "mode"; no semantic changes.
