# Porting maury to a new operating system

> A primer for adding maury support on an OS beyond the three we
> currently exercise (macOS, Ubuntu Linux, FreeBSD). Not an ADR —
> this is the *how-to* for making maury work on your platform.
>
> If you're trying to *use* maury on a Tier-1 OS, read the
> [README](../README.md) instead. This page is for porters.

## Tiers of support

We classify OS support in three tiers. The tier governs what
maury promises and where the work to add a new OS goes.

| Tier | Promise | Examples |
|---|---|---|
| **Tier 1** | First-class. CI runs the test suite on every PR. Hooks resolve to OS-specific commands; capability probe knows the platform. | macOS, Ubuntu Linux, FreeBSD |
| **Tier 2** | Best-effort. Capability probe recognizes the platform; wrappers fall through to safe defaults; some hooks may resolve to `logger` instead of native notifications. | (none yet — your port could land here) |
| **Tier 3** | Unsupported but not actively rejected. Capability probe returns `OS.OTHER`; render engine refuses to install hooks whose required capabilities are absent (Tenet 1 — first, do no harm). | Everything else |

Adding a new OS is the work of getting it from Tier 3 → Tier 2,
then Tier 2 → Tier 1.

## What maury actually requires from any host

Before any porting work, your candidate OS must satisfy these
**hard prerequisites**. If it can't, the rest is moot.

- **Python ≥ 3.11** with a working CPython interpreter.
- **`uv`** ([astral.sh/uv](https://docs.astral.sh/uv/)) for env
  and dependency management. If `uv` doesn't have native
  binaries for your platform, you may need to vendor or build
  it.
- **Git ≥ 2.30** with SSH support.
- **`ssh` client** that supports per-host `IdentityFile`
  config (per [ADR-0003](adr/0003-per-host-deploy-keys.md)).
- **POSIX `sh`** (any of `dash`, `bash`, `zsh`, `mksh`, ...) —
  the wrapper scripts in `base-template/bin/` are POSIX-sh.
- **POSIX advisory locks (fcntl)** in `~/.claude/maury-state/`
  for safe concurrent reader/writer access (per
  [ADR-0029](adr/0029-maury-state-layout-contract.md)).

If your platform is missing any of these (e.g., a
sandboxed-only mobile environment), maury fundamentally
doesn't fit and a port is out of scope.

## Where the OS-specific assumptions live

Three layers, each in a known location. A port modifies all
three.

### 1. The capability probe

`src/maury/capability/schema.py` defines the typed enum
`OS = {DARWIN, LINUX, FREEBSD, OTHER}`. `src/maury/capability/probe.py`
implements the per-host detection.

To add a new OS, add a value to the `OS` enum and a branch
in `detect_os()`. Then walk the rest of probe.py and decide
how each detector behaves on your platform:

- `detect_userland` — does your platform ship GNU coreutils,
  BSD coreutils, busybox, or something else?
- `detect_notifications` — takes the OS plus pre-detected
  presence flags for `osascript`, `notify-send`, and `logger`.
  What's your platform's native notification mechanism?
  (`osascript` on macOS, `notify-send` on most Linux, none on
  FreeBSD by default.)
- `detect_gui` — is there a GUI surface for hooks to surface
  to?
- `detect_security_posture` — does your platform have an
  MDM/EDR equivalent maury should respect?
- `detect_privileged_writes` — can maury sudo automatically
  (`AUTO`), or must it write scripts for the user to run
  (`MANUAL`)?

The probe runs on every `maury sync` (per
[ADR-0006](adr/0006-capability-probe-hook-abstraction.md)) and
writes its output to
`<repo>/profiles/<profile>/hosts/<host>/capabilities.json`.

### 2. The hook action resolver

> **Status:** the action-form resolver (Phase 3.6) is *not yet
> built*. Today, `src/maury/render/engine.py` knows about action
> names but does not yet contain a per-action, per-OS resolver
> branch. This section documents where porting work will land
> *once that resolver exists* — porters arriving today should
> file an issue on a new OS rather than implementing resolver
> arms that don't yet have somewhere to go.

When the resolver lands in `src/maury/render/` (likely as a new
`hooks.py` module), it will take a hook definition like:

```yaml
on: PostToolUse
do: notify
message: "Build done"
on_unavailable: log
```

…and resolve `notify` against the host's `capabilities.json`
into a concrete shell command. The expected porting pattern at
that point will be:

1. Open the resolver and find the `notify`-action branch.
2. Add a new arm for your OS. Example skeleton:

```python
case OS.HAIKU:  # hypothetical
    if caps.notifications == NotificationMech.HAIKU_NOTIFY:
        return f'notify -m {shlex.quote(message)}'
    return self._fallback(action.on_unavailable, message)
```

3. Add a corresponding `NotificationMech` value to the schema
   if your platform's mechanism isn't already enumerated.

### 3. The wrapper scripts

`base-template/bin/` will hold POSIX-sh wrappers that branch on
`uname -s` internally for cases where shell logic is genuinely
simpler than action-form abstraction. The directory is
currently empty; the planned wrappers are:

- `claude-notify` — branch by OS, call native notification.
- `claude-readlink-f` — `readlink -f` on GNU, `greadlink -f`
  on macOS, `realpath` fallback on BSD.
- `claude-clipboard` — `pbcopy` on macOS, `xclip`/`wl-copy` on
  Linux, OS-specific elsewhere.

To add platform branching, edit the wrapper and add a new
`uname` arm. Wrappers are POSIX-sh (`/bin/sh`), not bash —
keep them portable.

## Porting checklist

A complete port from Tier 3 to Tier 2 looks like:

1. [ ] **Add the OS enum value.** `OS.<YOUR_OS>` in
       `src/maury/capability/schema.py`.
2. [ ] **Wire `detect_os()`.** Recognize your platform via
       `platform.system()` or `uname -s`.
3. [ ] **Wire detectors.** Userland flavor, notification
       mechanism, GUI presence, security posture, privileged-
       write mode. Conservative defaults if you're unsure
       (Tenet 1: first, do no harm).
4. [ ] **Run `maury doctor`.** Confirm the probe output is
       sensible and no critical capabilities are missing.
5. [ ] **Run `maury render --check`** against a sample
       profile. Confirm it doesn't refuse hooks that should
       be installable.
6. [ ] **Add wrapper-script branches** for any wrappers your
       platform needs (`uname -s` arm in `claude-notify` etc.).
7. [ ] **Smoke-test hooks end-to-end.** Trigger a Claude
       Code session, confirm `notify` resolves to something
       that visibly fires on your host.
8. [ ] **Document any platform quirks** in
       `docs/claude-code-contract.md` if your platform
       affects assumptions about Claude Code's behavior
       (e.g., a different transcript path).

To go from Tier 2 to Tier 1:

9. [ ] **Add CI coverage.** A workflow job that installs
       Python + uv on your platform and runs `pytest`.
10. [ ] **Run an end-to-end install on a real host.** The
        `maury bootstrap init` flow succeeds without
        platform-specific hand-tweaks. (Additional bootstrap
        subcommands ship as Phase 4 progresses; verify each
        as it lands.)

## Testing the port

Three layers of verification, each progressively more
end-to-end. The first works on a bare new-OS host; the second
and third require a working maury install with at least one
manifest + profile.

```sh
# 1. Probe layer — does maury see your platform correctly?
#    Works pre-bootstrap; the only sanity check available
#    on a fresh host with no manifest yet.
uv run maury doctor

# 2. Render layer — does maury build a hook config that
#    won't silently no-op?
#    Requires a manifest. Run after `maury bootstrap init`
#    has produced one.
uv run maury render --check --target /tmp/maury-render-test

# 3. End-to-end smoke — does a Claude Code session actually
#    fire the hooks?
uv run maury sync
# ... start a Claude Code session, do a Tool Use, verify
# the configured hooks fire.
```

## Examples of the kinds of OSes someone might want

- **NixOS** — Tier 2 likely straightforward; the GNU userland
  + flake-based packaging actually simplifies dependency
  pinning. Notification mechanism is the main detector branch.
- **Alpine Linux** — busybox userland is the trick;
  `UserlandFlavor` may need a third value (`BUSYBOX`) since
  busybox flag syntax differs from both GNU and BSD.
- **OpenBSD / NetBSD** — Tier 2 likely a small extension of
  the FreeBSD path; `pkg_add` instead of `pkg`, but the
  userland is BSD-shaped.
- **illumos / SmartOS** — older `uname -s`, slightly different
  `realpath` story, but the SunOS-derived utilities are
  reasonable.
- **Windows-via-WSL** — works as Linux from maury's
  perspective; the host is `linux` to the probe. Native
  Windows is harder — different filesystem-permission model,
  different `~/.claude/` resolution. Treat WSL as Tier 1
  Linux; native Windows would need a substantial new
  detector.
- **Termux / Android** — Linux kernel + bionic libc + busybox.
  Filesystem-permission quirks (no traditional Unix
  permissions in some scoped-storage modes) need
  investigation before promising support.

## Contributing your port back

If you've got a port working on a new OS:

1. Open an issue using the "Design question" template on the
   maury repo, describing what tier you reached and what
   assumptions you had to add.
2. Submit a PR with the probe + resolver + wrapper changes.
3. Tag any platform-specific quirks you discovered for
   inclusion in this doc.
4. If your port pushes maury toward Tier 1 on a new OS,
   discuss CI coverage in the PR — that's the boundary
   between "best-effort" and "supported."

We're explicitly *not* promising tier-1 support for every
platform a port might land. Maintaining tier-1 means
tracking platform changes over time. Tier-2 is the
right answer for most platforms — maury works, but you're
responsible for keeping your port healthy as the platform
evolves.

## Related ADRs

- [ADR-0003 — Per-host deploy keys](adr/0003-per-host-deploy-keys.md)
  — the SSH primitive a port must satisfy.
- [ADR-0006 — Capability probe + hook abstraction](adr/0006-capability-probe-hook-abstraction.md)
  — the architectural reason this porting model exists.
- [ADR-0007 — Python with uv](adr/0007-python-with-uv.md) — the
  language/toolchain assumption every port inherits.
- [ADR-0029 — `~/.claude/maury-state/` layout contract](adr/0029-maury-state-layout-contract.md)
  — what maury writes to the local filesystem.
