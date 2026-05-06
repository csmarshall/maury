# ADR-0014: Host-local secrets with metadata-only sync

**Status:** Accepted
**Date:** 2026-05-06

## Related tenets

- [Tenet 3 — Trust boundaries are physical](../tenets.md#3-trust-boundaries-are-physical-not-policy)
- [Tenet 4 — Sensitive data stays local](../tenets.md#4-sensitive-data-stays-local)
- [Tenet 6 — Identity is not name](../tenets.md#6-identity-is-not-name)
- [Tenet 10 — Modularity over hardcoding](../tenets.md#10-modularity-over-hardcoding)

## Context

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

## Decision

Two independent stores:

### 1. Host-local secret-value store (NEVER synced)

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

### 2. Synced metadata manifest (per-host overlay)

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

### Service-presence vs credential-presence are separate

The `services` list captures what's *running* on the host (presence +
endpoint), independent of whether maury holds credentials for it. The
`credentials` list cross-references services by name. This way:

- A host can register "service-alpha is running here at port 7878"
  without yet having credentials for it.
- A migration story: "I'm moving service-alpha from linux-server → firewall"
  → update both the `services` entry on each host and the
  `credentials` entries; the values must be re-set on the new host.

### CLI surface

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

### Claude Code integration

- Claude on a host can call `maury secret get <name>` when it needs a
  credential value to perform a task. The value flows into Claude's
  context only at use time, not at idle.
- Claude observing a new credential being set during a task can call
  `maury secret register-pending <name> --service=...` to flag it; the
  user provides the value out-of-band. The transcript records that a
  credential exists, not its value.
- A `Stop` hook is added to remind: "secrets-pending: N credentials
  registered without values; run `maury secret set <name>` to populate."

### Privacy of metadata itself

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

## Consequences

- After a host crash, `maury secret restore-checklist` produces an
  exact list of credentials and services that need re-population.
- Service migrations gain a clear cross-host view: "what's running
  where, what credentials are tied to what, what needs to move."
- Two new schemas (`secrets.json`, capability probe gains a
  `secret_backend` field), one new module (`src/maury/secrets/`), four
  new CLI subcommands.
- Defense-in-depth at four layers: backend separation, content-pattern
  pre-commit hook, render-engine path reservation, and per-credential
  `private` flag.
- Multi-backend support adds complexity but keeps the abstraction
  clean: the same `secret get/set/list` interface across macOS
  Keychain / Linux Secret Service / age-encrypted file.

## Build-order placement

New phase **Phase 5.6 — Host-local secrets with metadata sync.** Sits
after Phase 5 (sync workflow) so the metadata manifest can ride the
existing render+sync mechanism. Independent of mining.

## Alternatives considered

- **Sync values into encrypted git** (e.g., chezmoi + age, or
  git-crypt). Rejected: mixes value bytes with sync bytes; one mistake
  in encryption config and a value leaks. Two-store separation is
  unconditionally safer.
- **No metadata at all; rely on operator memory.** Rejected: defeats
  the restore/migration use cases.
- **Single uniform backend (age-file everywhere).** Considered. Using
  the OS keychain where available is friendlier (no extra password
  prompt, integrates with system access patterns). Falling back to the
  age-file gives us uniformity where keychain isn't available.
- **Vault / 1Password / external secrets manager.** Rejected for v1 as
  external dependencies. Could be added as a fourth backend in v2.
