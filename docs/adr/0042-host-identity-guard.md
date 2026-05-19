# ADR-0042: Host-identity guard against accidental mode swaps

**Status:** Accepted
**Date:** 2026-05-14

## Related tenets

- [Tenet 1 — First, do no harm](../tenets.md#1-first-do-no-harm) — guarding against accidental identity swap prevents a class of silent data loss (work credentials, work repos, work `~/.claude/` written into a home context or vice versa).
- [Tenet 3 — Trust boundaries are physical, not policy](../tenets.md#3-trust-boundaries-are-physical-not-policy) — the work/home boundary already rests on three physical artifacts (per-host SSH keys, per-mode Anthropic credentials, active-account discipline); the host-id file is a fourth physical artifact that must be similarly checked rather than trusted on faith.
- [Tenet 6 — Identity is not name](../tenets.md#6-identity-is-not-name) — the `host_<hex>` is identity; the tag is name. Editing the hex is an identity-change event, not a renaming.
- [Tenet 11 — Explicit beats implicit, with conservative defaults](../tenets.md#11-explicit-beats-implicit-with-conservative-defaults) — silently proceeding past an identity-change is implicit; aborting + requiring `--confirm-identity-change` is explicit.

## Related ADRs

- [ADR-0015](0015-surrogate-keys-for-hosts-and-profiles.md) — defines the `host_<hex>_<tag>` ID format and the hex/tag mutability split this ADR enforces at sync time.
- [ADR-0017](0017-drift-detection-and-reconciliation.md) — drift detection uses `~/.claude/maury-state/last-render.json` as the baseline for file content; this ADR adds an analogous baseline for host identity itself.
- [ADR-0029](0029-maury-state-layout-contract.md) — `~/.claude/maury-state/` inventory; this ADR adds `host-identity.json` to that inventory.
- [ADR-0039](0039-bootstrap-and-host-lifecycle.md) — bootstrap writes the baseline; mode-change deregistration clears it.
- [ADR-0041](0041-per-mode-anthropic-credentials.md) — the three-piece work/home trust contract this ADR extends with a fourth checkpoint.

## TL;DR

The `host_<hex>_<tag>` value in `~/.maury-host-id` is identity:
which manifest entry applies, which mode this hardware is in,
which Anthropic credentials get wired up, which repos sync. A
user who edits the hex prefix — accidentally or otherwise —
silently swaps the host into a different mode-registration, with
potentially catastrophic blast radius (work credentials in home
context, home repos rendered onto a work laptop). Maury records
a `host-identity.json` baseline at first sync; every subsequent
sync checks the current `~/.maury-host-id` hex against the
baseline; on mismatch, abort loudly and require
`maury sync --confirm-identity-change` (or
`maury init --reset`) to proceed. Pattern follows SSH's
`known_hosts` "trust on first use, refuse on change" model.

## Context and Problem Statement

ADR-0015's amended ID format makes `host_<hex>_<tag>` cosmetically
readable, which is a UX win — but it doesn't change the underlying
fact that the hex prefix in `~/.maury-host-id` is the lookup key
for every mode-scoped operation maury performs: render, sync,
reconcile, mining, doctor.

That file lives in `$HOME` and is plain text. There is no OS-level
mechanism that prevents the user from editing it. Three concrete
failure modes follow:

1. **The brain-fart edit.** User is debugging something, opens
   `~/.maury-host-id`, fat-fingers a character or pastes the wrong
   ID from a terminal scrollback. `maury sync` next time silently
   pulls a different mode's repos onto this machine.

2. **The "I want to move this host" attempt.** User wants to move
   their laptop from `mode:work` to `mode:home` and reasonably
   guesses that editing `~/.maury-host-id` does the swap. It does
   not (the manifest entry is what determines the mode), but the
   edited file gets read on next sync and the curator's
   `_identify_host` flow now refuses (host_id not in manifest), or
   matches a different existing entry that happens to have that
   hex.

3. **The cross-host paste.** User runs `maury status` on host A,
   copies the ID into a Slack message for help debugging, later
   pastes that ID into host B's `~/.maury-host-id` thinking it's
   what they were told to do. Host B silently inherits host A's
   identity.

None of these are exotic. All three are the user being a normal
human under time pressure. None are caught by the file system,
Claude Code, or maury's existing checks.

ADR-0041 establishes a three-piece **physical** trust contract for
the work/home boundary: per-host SSH deploy keys, per-mode
Anthropic credentials, and active-account discipline. That
contract relies on each host knowing which mode it's in. The
host-id file is the artifact that encodes "which mode this is."
Failing to guard it lets the user inadvertently bypass all three
of the existing trust-boundary mechanisms.

## Decision Drivers

- **Tenet 1.** Silently rendering work content onto a home laptop
  (or vice versa) is a Tenet 1 violation. Detection must be
  defensive, not opportunistic.
- **Tenet 3.** The work/home trust contract is physical: each
  piece must be cross-checked, not trusted. The host-id file is
  the fourth physical piece.
- **State coherence.** `maury init` writes both
  `~/.maury-host-id` AND the baseline atomically. The guard
  treats their independent absence/presence as state corruption,
  not a normal operating mode.
- **Legitimate re-anchoring exists.** A user wiping and
  re-imaging a laptop, or restoring from backup onto new
  hardware, is a real scenario. The guard must not block this;
  it must require explicit acknowledgement.
- **Stand on shoulders.** SSH's `known_hosts` model has solved
  this exact pattern for 30+ years: trust on first use, refuse
  silently on change, require explicit acknowledgement to
  re-establish.

## Considered Options

- **Option I:** Pure documentation — tell users not to edit the file.
- **Option II (chosen):** Baseline + warn-and-abort + explicit-acknowledgement flag (SSH `known_hosts` model).
- **Option III:** Hard refuse with no override flag.
- **Option IV:** Bind the hex to OS-level machine-id so edits can't actually swap identity.

## Decision Outcome

**Chosen option:** Option II — record a baseline at first sync;
cross-check on every subsequent sync; on mismatch abort with a
loud message and require `maury sync --confirm-identity-change`
to proceed deliberately. Mirrors SSH's `known_hosts` pattern.
Catches the brain-fart and accidental-paste cases without blocking
legitimate re-anchoring (re-image, backup restore).

### Implementation details

#### Baseline file: `~/.claude/maury-state/host-identity.json`

Single JSON document, written by `maury init` at the same time
as the marker-file commit (ADR-0039 step 8). Writes use the
tmp+rename pattern documented in
[ADR-0029 §"Cross-cutting invariants"](0029-maury-state-layout-contract.md#cross-cutting-invariants)
(invariant #4) so a crash mid-write never leaves a partial file:

```json
{
  "schema_version": 1,
  "host_id_hex": "24b2a0aa",
  "registered_at": "2026-05-14T15:42:11Z",
  "mode_id": "mode_3f1a8b2c4d5e6f7081a2b3c4d5e6f708",
  "mode_name_at_bootstrap": "home"
}
```

Only the `host_id_hex` field is load-bearing for the guard. The
others are audit metadata for a user who opens the file to
understand what their host was last anchored to.

`mode_name_at_bootstrap` is a snapshot for human readability;
it's never used for resolution. If the mode is renamed in the
manifest, the snapshot is correct-as-of-bootstrap-time.

#### Sync-time guard

Every command that does mode-scoped work — `maury sync`,
`maury reconcile`, `maury render`, `maury mine`, `maury status`,
`maury doctor` — performs the following check after reading
`~/.maury-host-id` but before any mode-scoped resolution:

```
1. Read host_id from ~/.maury-host-id
2. Extract the 8-hex prefix (strip optional _<tag>)
3. If ~/.claude/maury-state/host-identity.json exists:
   a. Read baseline.host_id_hex
   b. If baseline.host_id_hex != current_hex:
        ABORT with the identity-change message
4. Proceed normally
```

The check is **on the hex prefix only**, not the full tagged ID.
Tag edits never trigger the guard.

#### Identity-change abort message

When the guard trips, the user sees:

```
✗ host identity changed since last sync.

  baseline (at ~/.claude/maury-state/host-identity.json):
    host_id_hex: 24b2a0aa
    mode: home (mode_3f1a...)
    registered: 2026-05-14T15:42:11Z

  current (at ~/.maury-host-id):
    host_id_hex: 88ff77ee
    full: host_88ff77ee_work-laptop

  This means either:
    (a) Your ~/.maury-host-id was edited (deliberately or by
        accident).
    (b) You restored ~/.maury-host-id from a different host
        (e.g., backup restore from another laptop).
    (c) You intended to move this hardware to a different
        mode-registration.

  Maury refuses to sync, render, or mine across an identity
  change without explicit acknowledgement. Choose one:

    --confirm-identity-change   Proceed with the current host_id.
                                The baseline gets overwritten.
                                Use this if (a) was deliberate
                                or (b) was intentional.

    maury init --reset          Re-anchor as a fresh registration.
                                Generates a new host_id, writes
                                a new baseline, requires manifest
                                update. Use this if (c).
```

The message is verbose by design — this is the failure mode where
verbose is correct (cf. Tenet 11). A short message would invite
"why is sync failing? Let me just delete the baseline file" which
is the wrong recovery path.

#### `--confirm-identity-change` semantics

When the user passes the flag:

1. Maury logs the acknowledgement to the audit log (ADR-0035) with
   both old and new `host_id` values.
2. Maury overwrites `host-identity.json` with the new hex as
   baseline.
3. Operation proceeds normally.
4. **No flag persistence:** the next command does not inherit the
   acknowledgement. Each operation re-checks the (now updated)
   baseline.

#### `maury init --reset` flow

Distinct from `--confirm-identity-change`. Used when the user
intends a fresh registration rather than acknowledging an
existing one:

1. Refuses if the current `~/.maury-host-id` is still registered
   in the active mode's marker (would orphan that registration).
   User must first `maury mode deregister`.
2. Deletes `~/.maury-host-id` and `host-identity.json`.
3. Runs the full bootstrap flow from ADR-0039 §"New host
   bootstrap."
4. Writes fresh files on completion.

#### State-corruption case: host-id present, baseline absent

`maury init` writes `~/.maury-host-id` and `host-identity.json`
together (the host-id file is written first; the baseline is
written immediately after on the same code path). The only way
to observe one without the other is filesystem corruption,
manual deletion of one file, or a partial restore from backup.

The guard treats this as state corruption — `check_host_identity`
raises `HostIdentityError` pointing the user at `maury init
--reset` to re-anchor. There is no silent auto-recovery.

The pre-release "silently auto-create a baseline if missing"
path (drafted 2026-05-14 for pre-ADR-0042 hosts) was retired
2026-05-19 — no users existed who could have bootstrapped before
the ADR was added.

#### Per-mode-change interaction

A `maury mode change` operation (ADR-0039 §"Mode change process")
is the legitimate way to swap a host between modes. The flow
already clears `~/.maury-host-id` on deregister and writes a fresh
one on re-bootstrap. The mode-change code path **deletes
`host-identity.json` on deregister** and re-creates it during
the new bootstrap. From the guard's perspective, this is just a
normal bootstrap; no identity-change abort fires.

### Consequences

- ✅ **Good:** Accidental hex edits no longer silently swap a host
  into a different mode. Loud error catches the case at the
  exact moment it would have caused damage.
- ✅ **Good:** Backwards-compatible. Pre-2026-05-14 hosts get the
  guard automatically on first post-upgrade sync, with no
  re-bootstrap required.
- ✅ **Good:** Stands on shoulders of SSH `known_hosts`. Users
  familiar with that pattern transfer their mental model directly
  ("oh, like accepting a new host key").
- ⚖️ **Neutral:** The guard runs on every mode-scoped command (one
  file read + one comparison). Sub-millisecond overhead. Not a
  perf concern.
- ⚖️ **Neutral:** The verbose abort message is long. Long messages
  in failure modes are intentional per Tenet 11; this is the
  right tradeoff.
- ❌ **Bad:** One additional state file in `~/.claude/maury-state/`
  (`host-identity.json`). ADR-0029's File inventory grows by one.
  Real but tiny maintenance surface.
- ❌ **Bad:** The "legitimate re-image" case still requires the
  user to know about `--confirm-identity-change`. First-time
  encounter is friction. Mitigated by the verbose abort message
  describing the flag inline.

### Confirmation

- Unit tests in `tests/unit/test_host_identity_guard.py` cover:
  baseline auto-creation on first sync, hex-mismatch abort,
  `--confirm-identity-change` flag flow, `init --reset` flow,
  tag-only edits not triggering the guard, mode-change
  interaction.
- Integration test in `tests/integration/test_identity_swap.py`
  simulates the accidental-edit scenario end-to-end.
- ADR-0029 File inventory entry for `host-identity.json` keeps
  this ADR discoverable from the state-layout reference.

## Pros and Cons of the Options

### Option I: Pure documentation

- ✅ **Good:** No code. No state file. Trust the user.
- ❌ **Bad:** Fails the accidental case. Users will forget the doc rule;
  the failure mode is silent data exposure.
- ❌ **Bad:** Doesn't satisfy Tenet 3's "physical, not policy" check —
  the trust boundary depends on the user remembering a rule, which is
  policy, not physics.

### Option II (chosen): Baseline + warn-and-abort + flag

- ✅ **Good:** Catches accidents at the exact moment damage would
  occur. Loud failure, clear recovery path.
- ✅ **Good:** Mirrors SSH `known_hosts`, which is the proven
  pattern for "trust on first use, refuse on change."
- ✅ **Good:** Doesn't block legitimate re-anchoring (re-image,
  backup restore).
- ⚖️ **Neutral:** One additional state file + one comparison per
  mode-scoped command.

### Option III: Hard refuse with no override

- ✅ **Good:** Maximally strict.
- ❌ **Bad:** Re-image scenarios become genuinely painful. User
  must blow away the entire maury-state dir and re-bootstrap
  from scratch, losing local-only data (drift records, mining
  watermarks, etc.).
- ❌ **Bad:** Violates "users not stupid but human" — competent
  users with legitimate use cases get blocked behind ceremony.

### Option IV: Bind hex to OS-level machine-id

- ✅ **Good:** Edits literally cannot swap identity; the hex is
  derived from hardware, not stored in user-editable state.
- ❌ **Bad:** Platform-branching in `ids.py` for
  `/etc/machine-id` (Linux), `IOPlatformUUID` (macOS), smbios
  serial (FreeBSD), TPM (Windows). Substantial code + test
  surface.
- ❌ **Bad:** Machine-id is supposed to be stable but isn't in
  practice — VM clones, container migrations, motherboard
  swaps, OS reinstalls can all change it. Would require its own
  guard for the cases where the bound hex no longer matches
  hardware. Recursive problem.
- ❌ **Bad:** Over-engineered for the threat model. The "users
  not stupid but human" framing says the guard is for accidents,
  not for sophisticated attack. Anyone with shell access to the
  user's home dir has bigger blast radius available than the
  host-id file.

## Build-order placement

- **Phase 5.x.a** (current — drift detection + reconcile slice):
  ships the baseline file write at `init` time and the sync-time
  guard. Becomes a precondition for the eventually-consistent
  drift-attribution chain in ADR-0023 to work safely across
  identity edits.
- **Phase 6** (mining): the guard already covers `maury mine` since
  mining is mode-scoped.
- **Audit log integration** (ADR-0035, Phase 10): the
  `--confirm-identity-change` acknowledgement gets an audit log
  entry. Until Phase 10 lands, the acknowledgement logs to
  `mode-switches.jsonl` (the stub audit log per ADR-0025).

## Followups

- **`maury doctor` rule** flagging "baseline missing but
  `~/.maury-host-id` present" as a config-drift signal. Currently
  the guard silently auto-creates the baseline; a doctor rule
  would surface this explicitly so users notice the upgrade
  transition.
- **CLI affordance:** `maury status` could show the baseline state
  alongside the current ID for trust-debugging convenience.

## Claude Code references

This ADR does not make claims about Claude Code's documented
behavior. The guard runs entirely within maury's state files;
Claude Code itself is unaffected.

## Amendment history

- 2026-05-19 — pre-release cleanup: the silent "auto-create
  baseline on first sync after upgrade" path was retired (no
  users existed before the ADR who could need it). The guard
  now treats host-id-present-but-baseline-absent as state
  corruption and raises `HostIdentityError` pointing at
  `maury init --reset`. `IdentityCheckOutcome.FIRST_RUN_AUTO_BASELINE`
  and `auto_create_baseline_for_upgrade()` were deleted.
  §Drivers and §"State-corruption case" sections rewritten.
