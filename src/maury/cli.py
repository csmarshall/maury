"""Command-line entry point for maury."""

from __future__ import annotations

import json
import socket
import sys
from pathlib import Path

import click

from maury import __version__
from maury.active_sessions import (
    ACTIVE_SESSIONS_REL_PATH,
    DEFAULT_MAX_AGE,
)
from maury.active_sessions import prune as prune_active_sessions
from maury.agency import AgencyInitError, init_agency
from maury.bootstrap import BootstrapHostError, DeregisterError, InitError
from maury.bootstrap import bootstrap_host as run_bootstrap_host
from maury.bootstrap import deregister_host as run_deregister_host
from maury.bootstrap import init as run_init
from maury.capability import dumps as capabilities_dumps
from maury.capability import run_probe
from maury.cc_contract_verify import (
    default_snapshot_dir,
    run_contract_verification,
)
from maury.cc_contract_verify import (
    format_report_json as format_cc_contract_report_json,
)
from maury.cc_contract_verify import (
    format_report_text as format_cc_contract_report_text,
)
from maury.doctor import (
    Report,
    render_json,
    render_text,
    run_all,
    run_system_health_checks,
)
from maury.drift import DriftEntry, detect_drift, read_last_render
from maury.empirical_tests import (
    HarnessReport,
    HookTimingHarnessReport,
    ProjectDirHarnessReport,
    TranscriptSchemaHarnessReport,
    claude_present,
    run_hook_timing_harness,
    run_project_dir_harness,
    run_transcript_schema_harness,
)
from maury.empirical_tests import run as run_empirical
from maury.host_identity import (
    HostIdentityError,
    IdentityCheckOutcome,
    auto_create_baseline_for_upgrade,
    check_host_identity,
    format_identity_change_message,
)
from maury.ids import normalize_tag
from maury.ids import short as short_id
from maury.llm import BackendUnavailableError, get_backend
from maury.manifest import (
    ManifestError,
    dump_manifest,
    known_profile_names_from,
    known_profiles_from,
    load_manifest,
    validate_manifest,
)
from maury.mining import (
    CrossRefResult,
    CrossRefSummary,
    Finding,
    TranscriptMessage,
    extract_from_messages,
    walk_user_messages,
)
from maury.reconcile import (
    ALL_ACTIONS,
    ReconcileAction,
)
from maury.reconcile import (
    reconcile as run_reconcile,
)
from maury.render import RenderError, apply_render, render
from maury.repo_init import (
    DEFAULT_INITIAL_TAG,
    DEFAULT_PR_TARGET,
    RepoInitError,
    init_repo,
)
from maury.rules import (
    RuleParseError,
    classify_fragment,
    load_rules,
    render_trace,
    validate_rules,
)
from maury.sync import DRIFT_SCAN_DIRS, RepoSyncResult, SyncError
from maury.sync import sync as run_sync
from maury.uninstall import run_uninstall

BANNER = r"""
  ┌──────────────────────────┐
  │  MAURY SLINE             │
  │  ─────────────           │
  │  Talent Agent            │
  │  Configs · Hosts · Skills│
  │  Chicago, IL             │
  └──────────────────────────┘
   on a mission from Claude
"""

DEFAULT_RULES_ENV = "MAURY_RULES_FILE"
DEFAULT_RULES_PATH = Path.cwd() / ".meta" / "rules.yaml"

DEFAULT_MANIFEST_ENV = "MAURY_MANIFEST_FILE"
DEFAULT_MANIFEST_PATH = Path.cwd() / ".meta" / "manifest.json"


@click.group(help="On a mission from Claude — multi-host config sync, profile isolation, and learned-rule mining.")
@click.version_option(
    version=__version__,
    prog_name="maury",
    message=f"%(prog)s %(version)s\n{BANNER}",
)
def main() -> None:
    """maury — multi-host Claude Code config sync, profile isolation, and learned-rule mining."""


# ---- rules subgroup ------------------------------------------------------


@main.group()
def rules() -> None:
    """Inspect and edit the classification ruleset."""


@rules.command("trace")
@click.argument("text", nargs=-1, required=True)
@click.option(
    "--rules-file",
    "rules_file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    envvar=DEFAULT_RULES_ENV,
    help=f"Path to rules.yaml. Defaults to ${DEFAULT_RULES_ENV} or ./.meta/rules.yaml.",
)
@click.option(
    "--manifest-file",
    "manifest_file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    envvar=DEFAULT_MANIFEST_ENV,
    help="Manifest path (used to derive known profiles for symbolic forbid resolution).",
)
@click.option(
    "--profiles",
    "profiles_list",
    default="",
    help="Comma-separated list of known profile names. Overrides --manifest-file.",
)
@click.option("--quiet", is_flag=True, help="Suppress rules that did not match.")
def rules_trace(
    text: tuple[str, ...],
    rules_file: Path | None,
    manifest_file: Path | None,
    profiles_list: str,
    quiet: bool,
) -> None:
    """Dry-run a fragment against the ruleset and show how it would be classified."""
    path = rules_file or DEFAULT_RULES_PATH
    if not path.exists():
        raise click.ClickException(f"rules file not found: {path}\nset {DEFAULT_RULES_ENV} or pass --rules-file.")
    try:
        ruleset = load_rules(path)
    except RuleParseError as e:
        raise click.ClickException(str(e)) from e

    fragment = " ".join(text)
    if profiles_list:
        known_profiles = {p.strip() for p in profiles_list.split(",") if p.strip()}
    elif manifest_file:
        try:
            manifest = load_manifest(manifest_file)
        except ManifestError as e:
            raise click.ClickException(str(e)) from e
        known_profiles = known_profiles_from(manifest) | known_profile_names_from(manifest)
    else:
        known_profiles = set()
    result = classify_fragment(fragment, ruleset, known_profiles=known_profiles)
    click.echo(render_trace(result, show_misses=not quiet))


@rules.command("list")
@click.option(
    "--rules-file",
    "rules_file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    envvar=DEFAULT_RULES_ENV,
)
def rules_list(rules_file: Path | None) -> None:
    """List all rules in the ruleset."""
    path = rules_file or DEFAULT_RULES_PATH
    if not path.exists():
        raise click.ClickException(f"rules file not found: {path}")
    try:
        ruleset = load_rules(path)
    except RuleParseError as e:
        raise click.ClickException(str(e)) from e

    if not ruleset:
        click.echo("(no rules)")
        return
    id_w = max(len(r.id) for r in ruleset)
    kind_w = max(len(r.kind.value) for r in ruleset)
    for r in ruleset:
        target = r.then.profile or ("[" + ",".join(r.then.forbid_profile) + "]" if r.then.forbid_profile else "-")
        click.echo(
            f"  {r.id:<{id_w}}  {r.kind.value:<{kind_w}}  -> {target}  (priority={r.priority}, confidence={r.confidence.value})"
        )


@rules.command("validate")
@click.option(
    "--rules-file",
    "rules_file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    envvar=DEFAULT_RULES_ENV,
)
@click.option(
    "--manifest-file",
    "manifest_file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    envvar=DEFAULT_MANIFEST_ENV,
    help="Manifest path (used to derive known profiles for cross-validation).",
)
@click.option(
    "--profiles",
    "profiles_list",
    default="",
    help="Comma-separated list of known profile names. Overrides --manifest-file.",
)
def rules_validate(
    rules_file: Path | None,
    manifest_file: Path | None,
    profiles_list: str,
) -> None:
    """Validate a ruleset (parse + cross-check against the profile registry)."""
    path = rules_file or DEFAULT_RULES_PATH
    if not path.exists():
        raise click.ClickException(f"rules file not found: {path}")
    try:
        ruleset = load_rules(path)
    except RuleParseError as e:
        raise click.ClickException(str(e)) from e

    if profiles_list:
        known_profiles = {p.strip() for p in profiles_list.split(",") if p.strip()}
    elif manifest_file:
        try:
            manifest = load_manifest(manifest_file)
        except ManifestError as e:
            raise click.ClickException(str(e)) from e
        # Pass the union of profile IDs and names so rules using either
        # form (per ADR-0015's backward-compat for hand-authored YAML)
        # validate cleanly.
        known_profiles = known_profiles_from(manifest) | known_profile_names_from(manifest)
    else:
        known_profiles = set()

    errors = validate_rules(ruleset, known_profiles=known_profiles)
    if errors:
        for err in errors:
            click.echo(f"error: {err}", err=True)
        sys.exit(1)
    click.echo(f"OK: {len(ruleset)} rules valid.")


# ---- manifest + agency subgroups -----------------------------------------
#
# `maury agency validate` is the canonical name per ADR-0040; the older
# `maury manifest validate` continues to work as a deprecation alias that
# prints a one-line notice to stderr and delegates to the same handler.


def _run_manifest_validate(manifest_file: Path | None) -> None:
    """Shared body for `manifest validate` and `agency validate`."""
    path = manifest_file or DEFAULT_MANIFEST_PATH
    if not path.exists():
        raise click.ClickException(f"manifest file not found: {path}")
    try:
        m = load_manifest(path)
    except ManifestError as e:
        raise click.ClickException(str(e)) from e
    errors = validate_manifest(m)
    if errors:
        for err in errors:
            click.echo(f"error: {err}", err=True)
        sys.exit(1)
    click.echo(f"OK: {len(m.profiles)} profile(s), {len(m.hosts)} host(s).")


@main.group()
def agency() -> None:
    """Inspect and validate the agency (the canonical command surface)."""


@agency.command("validate")
@click.option(
    "--manifest-file",
    "manifest_file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    envvar=DEFAULT_MANIFEST_ENV,
)
def agency_validate(manifest_file: Path | None) -> None:
    """Validate the agency (manifest schema + cross-references)."""
    _run_manifest_validate(manifest_file)


@agency.command("init")
@click.option(
    "--target",
    "target_dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=".",
    show_default=True,
    help="Where the base repo will live (the agency's root).",
)
@click.option(
    "--force",
    is_flag=True,
    help="Re-initialize even if .meta/maury-marker.json exists. Generates a new agency_id; severs all existing host registrations.",
)
@click.option(
    "--no-git-init",
    "no_git_init",
    is_flag=True,
    help="Don't run `git init` or commit. Useful when target is already a git repo or for offline scaffolding.",
)
def agency_init(target_dir: Path, force: bool, no_git_init: bool) -> None:
    """Initialize a new agency: generate agency_id, write base marker, commit.

    Per ADR-0039 + ADR-0050 + ADR-0051. Idempotent without --force; refuses
    if a marker already exists.

    After this completes, the curator typically creates mode repos (each
    with its own marker carrying the same agency_id) and registers them
    as sublayers of the base.
    """
    target_dir = target_dir.expanduser().resolve()
    try:
        summary = init_agency(
            target_dir=target_dir,
            force=force,
            git_init=not no_git_init,
        )
    except AgencyInitError as e:
        raise click.ClickException(str(e)) from e

    click.echo(f"initialized agency: {summary.agency_id}")
    click.echo(f"  marker: {summary.marker_path}")
    if summary.git_initialized:
        if summary.git_commit_sha:
            click.echo(f"  commit: {summary.git_commit_sha[:12]} (chore(maury): initialize agency)")
        else:
            click.echo("  commit: skipped (nothing to commit)")
    else:
        click.echo("  git init: skipped (--no-git-init)")
    if summary.force_used:
        click.echo("  ⚠️ --force used: any existing host registrations referencing the prior agency_id are now severed.")


@main.group()
def manifest() -> None:
    """Inspect and validate the manifest (legacy alias of `agency`)."""


@manifest.command("validate")
@click.option(
    "--manifest-file",
    "manifest_file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    envvar=DEFAULT_MANIFEST_ENV,
)
def manifest_validate(manifest_file: Path | None) -> None:
    """Deprecated alias of `maury agency validate` (per ADR-0040)."""
    click.echo(
        "notice: `maury manifest validate` is the deprecated alias of "
        "`maury agency validate` (per ADR-0040). Both work today; the "
        "alias will be removed in a future release.",
        err=True,
    )
    _run_manifest_validate(manifest_file)


@manifest.command("show")
@click.option(
    "--manifest-file",
    "manifest_file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    envvar=DEFAULT_MANIFEST_ENV,
)
def manifest_show(manifest_file: Path | None) -> None:
    """Pretty-print the manifest."""
    path = manifest_file or DEFAULT_MANIFEST_PATH
    if not path.exists():
        raise click.ClickException(f"manifest file not found: {path}")
    try:
        m = load_manifest(path)
    except ManifestError as e:
        raise click.ClickException(str(e)) from e
    click.echo(dump_manifest(m))


@manifest.command("upgrade-v1-to-v2")
@click.option(
    "--in",
    "in_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Path to the v1 manifest.json to upgrade.",
)
@click.option(
    "--out",
    "out_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Where to write the v2 manifest. Defaults to --in (in-place).",
)
@click.option(
    "--check",
    "dry_run",
    is_flag=True,
    help="Compute the mapping without writing the upgraded manifest.",
)
def manifest_upgrade_v1_to_v2(in_path: Path, out_path: Path | None, dry_run: bool) -> None:
    """Upgrade a v1 manifest (name-keyed) to v2 (surrogate-ID-keyed).

    Per ADR-0015 §"Migration": generates fresh `profile_<hex>` and
    `host_<hex>` IDs, rewrites `extends` and `profile` references to
    use IDs, and embeds the original names as mutable `name` fields.

    Prints an old-name → new-id mapping report so callers can correlate
    audit/log entries that referenced the old names.
    """
    from maury.migrations import MigrationError, upgrade_v1_to_v2

    try:
        result = upgrade_v1_to_v2(in_path=in_path, out_path=out_path, dry_run=dry_run)
    except MigrationError as e:
        raise click.ClickException(str(e)) from e

    click.echo(f"upgraded {result.in_path} → {result.out_path}")
    if dry_run:
        click.echo("(--check; no files were written)")
    if result.mapping.profile_name_to_id:
        click.echo("profile mapping:")
        for name, pid in sorted(result.mapping.profile_name_to_id.items()):
            click.echo(f"  {name!r:<24} → {pid}")
    if result.mapping.host_name_to_id:
        click.echo("host mapping:")
        for name, hid in sorted(result.mapping.host_name_to_id.items()):
            click.echo(f"  {name!r:<24} → {hid}")


# ---- profile subgroup ----------------------------------------------------


@main.group()
def profile() -> None:
    """Manage profiles (home, work, per-client, etc.)."""


@profile.command("list")
@click.option(
    "--manifest-file",
    "manifest_file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    envvar=DEFAULT_MANIFEST_ENV,
)
def profile_list(manifest_file: Path | None) -> None:
    """List profiles from the manifest, with their inheritance chain."""
    path = manifest_file or DEFAULT_MANIFEST_PATH
    if not path.exists():
        raise click.ClickException(f"manifest file not found: {path}")
    try:
        m = load_manifest(path)
    except ManifestError as e:
        raise click.ClickException(str(e)) from e

    if not m.profiles:
        click.echo("(no profiles registered)")
        return
    # Display by name (mutable label); show short ID for canonical reference (per ADR-0015).
    name_w = max(len(spec.name) for spec in m.profiles.values())
    for pid, spec in m.profiles.items():
        # Render the chain in the human-friendly form (names, not IDs).
        chain_ids = m.inheritance_chain(pid)
        chain = " -> ".join(m.profiles[c].name for c in chain_ids)
        desc = f" — {spec.description}" if spec.description else ""
        click.echo(f"  {spec.name:<{name_w}}  ({short_id(pid)})  chain: {chain}{desc}")


@profile.command("hosts")
@click.option(
    "--manifest-file",
    "manifest_file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    envvar=DEFAULT_MANIFEST_ENV,
)
def profile_hosts(manifest_file: Path | None) -> None:
    """List hosts and which profile/repos each is bound to."""
    path = manifest_file or DEFAULT_MANIFEST_PATH
    if not path.exists():
        raise click.ClickException(f"manifest file not found: {path}")
    try:
        m = load_manifest(path)
    except ManifestError as e:
        raise click.ClickException(str(e)) from e

    if not m.hosts:
        click.echo("(no hosts registered)")
        return
    # Display by name (mutable label); resolve profile-id → profile-name for readability.
    name_w = max(len(spec.name) for spec in m.hosts.values())
    profile_name_w = max((len(p.name) for p in m.profiles.values()), default=4)
    for hid, spec in m.hosts.items():
        repo_summary = ", ".join(f"{rname}({r.mode.value})" for rname, r in spec.repos.items()) or "-"
        lock = " [locked]" if spec.lock else ""
        profile_name = m.profiles[spec.profile].name if spec.profile in m.profiles else f"<unknown:{spec.profile}>"
        click.echo(
            f"  {spec.name:<{name_w}}  ({short_id(hid)})  "
            f"profile={profile_name:<{profile_name_w}}  "
            f"push={spec.push_policy.value:<18} "
            f"repos=[{repo_summary}]{lock}"
        )


# ---- probe / bootstrap ---------------------------------------------------


@main.command()
@click.option(
    "--output",
    "output_path",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Write capabilities.json to this path. Default: stdout.",
)
@click.option(
    "--hostname",
    "hostname_override",
    help="Override the detected hostname (useful for cross-host testing).",
)
def probe(output_path: Path | None, hostname_override: str | None) -> None:
    """Probe this host's capabilities and emit capabilities.json.

    Detects OS, userland flavor (GNU vs BSD), shell, GUI presence,
    available tools, MDM/security posture, notification mechanism,
    privileged-write policy.
    """
    caps = run_probe(hostname_override=hostname_override)
    payload = capabilities_dumps(caps)
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(payload + "\n", encoding="utf-8")
        click.echo(f"wrote {output_path}")
    else:
        click.echo(payload)


@main.group()
def bootstrap() -> None:
    """Bootstrap a new host or repo."""


# ---- bootstrap host (curator-side host registration, per ADR-0018) ----


@bootstrap.command("host")
@click.option(
    "--manifest-file",
    "manifest_file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    envvar=DEFAULT_MANIFEST_ENV,
    help="Manifest file. Defaults to ./.meta/manifest.json or $MAURY_MANIFEST_FILE.",
)
@click.option("--name", "name", required=True, help="Display name for the new host (should match its hostname).")
@click.option(
    "--profile",
    "profile",
    required=True,
    help="Profile (name or ID) the new host belongs to.",
)
@click.option(
    "--base-url",
    "base_url",
    default=None,
    help="Base repo URL. If omitted, copied from another already-registered host's `base` entry.",
)
@click.option(
    "--base-mode",
    "base_mode",
    type=click.Choice(["ro", "rw", "pr"], case_sensitive=False),
    default="ro",
    show_default=True,
    help="Access mode for the new host's base repo.",
)
@click.option(
    "--push-policy",
    "push_policy",
    type=click.Choice(["permissive", "own_profile_only", "disabled"], case_sensitive=False),
    default="own_profile_only",
    show_default=True,
    help="Push policy for the new host.",
)
@click.option("--owner", "owner", default=None, help="Optional owner identifier (email, handle).")
@click.option("--check", "dry_run", is_flag=True, help="Dry-run: show what would happen, write nothing.")
def bootstrap_host_cmd(
    manifest_file: Path | None,
    name: str,
    profile: str,
    base_url: str | None,
    base_mode: str,
    push_policy: str,
    owner: str | None,
    dry_run: bool,
) -> None:
    """Register a new host in the manifest (curator-side)."""
    from maury.manifest import PushPolicy, RepoMode

    mpath = manifest_file or DEFAULT_MANIFEST_PATH
    if not mpath.exists():
        raise click.ClickException(f"manifest file not found: {mpath}")

    try:
        result = run_bootstrap_host(
            manifest_path=mpath,
            name=name,
            profile=profile,
            base_url=base_url,
            base_mode=RepoMode(base_mode.lower()),
            push_policy=PushPolicy(push_policy.lower()),
            owner=owner,
            dry_run=dry_run,
        )
    except BootstrapHostError as e:
        raise click.ClickException(str(e)) from e

    for action in result.actions:
        click.echo(f"  {action}")
    click.echo("")
    click.echo(result.message)
    if dry_run:
        click.echo("(--check; no files were written)")


# ---- init (the user's first command on a new host, per ADR-0018) -------


def _resolve_init_tag(*, explicit_tag: str | None) -> str | None:
    """Resolve the host-tag for `maury init` per ADR-0039 §"Tag UX at bootstrap".

    Returns None to fall back to the legacy 32-hex format (no tag). This
    happens only when the user passes `--tag ""` explicitly or future
    flags opt out — currently the function always produces a tag.

    Three paths:
      1. `--tag <value>` passed: normalize and use it. If normalization
         is lossy, echo the result so the user sees what got written.
      2. Interactive TTY without `--tag`: prompt with
         `socket.gethostname()` normalized as the default; one preview-
         and-confirm loop if the user types a custom value that needed
         lossy normalization.
      3. Non-interactive stdin without `--tag`: ClickException — bootstrap
         is meant to be deliberate, not silently auto-defaulted.
    """
    default_tag = _default_tag_from_hostname()

    if explicit_tag is not None:
        try:
            normalized = normalize_tag(explicit_tag)
        except ValueError as e:
            raise click.ClickException(str(e)) from e
        if normalized != explicit_tag:
            click.echo(f"note: tag {explicit_tag!r} normalized to {normalized!r} per ADR-0015 grammar")
        return normalized

    if not sys.stdin.isatty():
        raise click.ClickException(
            "no --tag provided and stdin is not a TTY. Pass `--tag <value>` "
            "explicitly; init refuses to silently auto-default in non-interactive mode."
        )

    candidate = default_tag
    while True:
        raw = click.prompt(
            f"Tag this host registration (RFC 1123 DNS-label grammar; default: {candidate})",
            default=candidate,
            show_default=False,
        )
        try:
            normalized = normalize_tag(raw)
        except ValueError as e:
            click.echo(f"  ✗ {e}; try again")
            continue
        if normalized == raw:
            return normalized
        click.echo(f"  normalized to {normalized!r}")
        if click.confirm("  accept this tag?", default=True):
            return normalized
        # Loop with the normalized form as the new default so the user
        # can edit from a clean starting point.
        candidate = normalized


def _default_tag_from_hostname() -> str:
    """Return a sensible default tag for init's interactive prompt.

    Runs the current hostname through `normalize_tag()`. Falls back to
    `"host"` if the hostname normalizes to empty (e.g., it's all
    non-ASCII glyphs)."""
    try:
        return normalize_tag(socket.gethostname())
    except ValueError:
        return "host"


def _enforce_identity_guard(*, target_dir: Path, allow_change: bool = False) -> None:
    """Run ADR-0042's sync-time host-identity guard before any mode-scoped op.

    Called by `sync`, `reconcile`, `mine` — every command that operates
    against the host's mode-registration. Branches on the
    `IdentityCheckResult`:

    - **OK**: silent; return.
    - **FIRST_RUN_AUTO_BASELINE**: pre-2026-05-14 upgrade path. Auto-
      write a synthetic baseline (best-effort metadata; empty mode
      fields are tolerable since the load-bearing hex still works) and
      print a one-line note so the user sees the transition.
    - **CHANGED_REFUSED**: print the verbose abort message and exit 1.
      The message describes both remediation paths
      (`--confirm-identity-change` and `maury init --reset`).
    - **CHANGED_ACKNOWLEDGED**: only when `allow_change=True`; print a
      one-line acknowledgement note and continue.

    Silently returns if `~/.maury-host-id` doesn't exist — that's the
    "user hasn't run init yet" case, which the downstream operation
    will surface with its own error.
    """
    from maury.bootstrap.init_cmd import current_host_id_file

    host_id_file = current_host_id_file()
    if not host_id_file.is_file():
        return  # downstream ops will surface "no host id" their own way

    try:
        result = check_host_identity(
            target_dir=target_dir,
            host_id_file=host_id_file,
            allow_change=allow_change,
        )
    except HostIdentityError as e:
        raise click.ClickException(str(e)) from e

    if result.outcome == IdentityCheckOutcome.OK:
        return
    if result.outcome == IdentityCheckOutcome.FIRST_RUN_AUTO_BASELINE:
        # Per ADR-0042 §"Backwards compatibility": silently establish a
        # baseline so subsequent runs are guarded. Mode metadata is
        # left empty here; `maury init --reset` would populate it.
        auto_create_baseline_for_upgrade(
            target_dir=target_dir,
            current_hex=result.current_hex,
        )
        click.echo(
            f"  note: established host-identity baseline at {target_dir}/maury-state/host-identity.json "
            f"(first run after 2026-05-14 ADR-0042 upgrade)"
        )
        return
    if result.outcome == IdentityCheckOutcome.CHANGED_REFUSED:
        click.echo(
            format_identity_change_message(
                target_dir=target_dir,
                host_id_file=host_id_file,
                result=result,
            ),
            err=True,
        )
        sys.exit(1)
    if result.outcome == IdentityCheckOutcome.CHANGED_ACKNOWLEDGED:
        click.echo(
            f"  note: --confirm-identity-change accepted; baseline updated to "
            f"host_id_hex={result.current_hex} (was {result.baseline_hex})"
        )
        return


@main.command("init")
@click.option(
    "--from-dir",
    "from_dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Initialize from a local directory containing the base repo.",
)
@click.option(
    "--from-tarball",
    "from_tarball",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Initialize from a tarball of the base repo.",
)
@click.option(
    "--target",
    "target_dir",
    type=click.Path(file_okay=False, path_type=Path),
    default="~/.claude",
    show_default=True,
    help="Where to write the rendered config tree.",
)
@click.option("--check", "dry_run", is_flag=True, help="Dry-run: show what would happen, write nothing.")
@click.option(
    "--force",
    "force",
    is_flag=True,
    help="Overwrite pre-existing content in the target dir. Hand-edits will be lost.",
)
@click.option(
    "--non-interactive",
    "non_interactive",
    is_flag=True,
    help="Refuse on any pre-existing content collision, exit 1. Cron/CI safe.",
)
@click.option(
    "--tag",
    "tag",
    type=str,
    default=None,
    help=(
        "Cosmetic suffix on the host id (`host_<hex>_<tag>`). Per ADR-0015, "
        "this is purely a label — never parsed for lookup. Grammar: "
        "`[a-z0-9-]{1,32}` (RFC 1123 DNS labels). If omitted, init prompts "
        "interactively with the current hostname as the default; required "
        "when stdin is not a TTY."
    ),
)
@click.option(
    "--reset",
    "reset",
    is_flag=True,
    help=(
        "Re-anchor as a fresh registration: delete the existing "
        "`~/.maury-host-id` and `host-identity.json`, then run the "
        "normal init flow. Per ADR-0042, this is the deliberate way to "
        "move this hardware to a different mode-registration. Mutually "
        "exclusive with the absence of `--tag` if a fresh host_id will "
        "be generated."
    ),
)
def init_cmd(
    from_dir: Path | None,
    from_tarball: Path | None,
    target_dir: Path,
    dry_run: bool,
    force: bool,
    non_interactive: bool,
    tag: str | None,
    reset: bool,
) -> None:
    """Initialize maury on a new host (first-run bootstrap)."""
    target_dir = target_dir.expanduser()
    if from_dir is None and from_tarball is None:
        raise click.ClickException(
            "provide one of --from-dir <path> or --from-tarball <path>. "
            "(Future: --from-url <git-url>; not yet implemented.)"
        )
    if from_dir is not None and from_tarball is not None:
        raise click.ClickException("--from-dir and --from-tarball are mutually exclusive")
    if force and non_interactive:
        raise click.ClickException("--force and --non-interactive are mutually exclusive.")

    # Tag is only needed if init will actually generate a new host_id
    # (i.e., `~/.maury-host-id` doesn't already exist). Resolve the
    # current value via `current_host_id_file()` so test monkeypatches
    # on `maury.bootstrap.init_cmd.HOST_ID_FILE` take effect.
    from maury.bootstrap.init_cmd import current_host_id_file
    from maury.host_identity import baseline_path

    if reset:
        # ADR-0042 §`maury init --reset` flow: blow away both the host-id
        # file and the identity baseline so a fresh registration runs
        # cleanly. The user is responsible for ensuring the previous
        # registration was retired in its mode marker (we can't refuse
        # here without a manifest in hand — that check belongs in the
        # full `maury mode deregister` flow, deferred).
        hid_file = current_host_id_file()
        if hid_file.exists():
            click.echo(f"  --reset: removing {hid_file}")
            hid_file.unlink()
        bp = baseline_path(target_dir)
        if bp.exists():
            click.echo(f"  --reset: removing {bp}")
            bp.unlink()

    needs_new_id = not current_host_id_file().exists()
    resolved_tag = _resolve_init_tag(explicit_tag=tag) if needs_new_id else None

    drift_mode = "force" if force else ("non-interactive" if non_interactive else "default")

    try:
        result = run_init(
            source_dir=from_dir,
            source_tarball=from_tarball,
            target_dir=target_dir,
            dry_run=dry_run,
            drift_mode=drift_mode,
            tag=resolved_tag,
        )
    except InitError as e:
        raise click.ClickException(str(e)) from e

    for action in result.actions:
        click.echo(f"  {action}")
    click.echo("")
    click.echo(result.message)
    if dry_run:
        click.echo("(--check; no files were written)")
    if result.has_errors():
        for err in result.errors:
            click.echo(f"  ✗ {err}", err=True)
        sys.exit(1)
    if not result.host_registered:
        sys.exit(2)  # distinct exit so scripts can detect "host not registered yet"


@main.command("status")
@click.option(
    "--manifest-file",
    "manifest_file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    envvar=DEFAULT_MANIFEST_ENV,
    help="Manifest file. Defaults to ./.meta/manifest.json or $MAURY_MANIFEST_FILE.",
)
@click.option(
    "--target",
    "target_dir",
    type=click.Path(file_okay=False, path_type=Path),
    default="~/.claude",
    show_default=True,
    help="Target directory whose maury-state to report.",
)
@click.option(
    "--repos-root",
    "repos_root",
    type=click.Path(file_okay=False, path_type=Path),
    default="~/.config/maury/repos",
    show_default=True,
    help="Where local clones live. Each repo nickname becomes a subdir.",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["text", "json"]),
    default="text",
    show_default=True,
)
def status_cmd(
    manifest_file: Path | None,
    target_dir: Path,
    repos_root: Path,
    output_format: str,
) -> None:
    """Diagnostic snapshot of this host's maury state.

    Reports — in order — host identity (id + tag + name + mode),
    identity-baseline freshness, last-render summary, current drift
    counts, and mining watermarks. Sections degrade gracefully: any
    missing state file is reported as `(not initialized)` rather than
    aborting.

    Per ADR-0042 separation: `status` REPORTS identity mismatches
    (with a ⚠️ marker), it does NOT enforce. The mode-scoped commands
    (sync, reconcile, mine) enforce; `status` is the diagnostic
    counterpart you reach for when you suspect something's off.
    """
    target_dir = target_dir.expanduser()
    repos_root = repos_root.expanduser()
    mpath = manifest_file or DEFAULT_MANIFEST_PATH
    report = _build_status_report(
        target_dir=target_dir,
        manifest_path=mpath,
        repos_root=repos_root,
    )
    if output_format == "json":
        click.echo(json.dumps(report, indent=2, sort_keys=False, default=str))
    else:
        click.echo(_render_status_text(report))


def _build_status_report(
    *,
    target_dir: Path,
    manifest_path: Path,
    repos_root: Path,
) -> dict[str, object]:
    """Aggregate every status section into a single dict for rendering.

    Each section's value is either a populated dict (data present) or
    a dict with `{"present": False, "note": "..."}` (graceful
    degradation). Callers render the same shape for both text and JSON
    output so they stay in sync.
    """
    return {
        "host_identity": _section_host_identity(manifest_path=manifest_path),
        "identity_baseline": _section_identity_baseline(target_dir=target_dir),
        "repos": _section_repos(manifest_path=manifest_path, repos_root=repos_root),
        "last_render": _section_last_render(target_dir=target_dir),
        "drift": _section_drift(target_dir=target_dir),
        "mining_watermarks": _section_mining_watermarks(target_dir=target_dir),
    }


def _section_host_identity(*, manifest_path: Path) -> dict[str, object]:
    """Read `~/.maury-host-id` and resolve against the manifest."""
    from maury.bootstrap.init_cmd import current_host_id_file
    from maury.ids import host_id_hex_prefix, split_host_id

    host_id_file = current_host_id_file()
    if not host_id_file.is_file():
        return {"present": False, "note": "no ~/.maury-host-id (run `maury init`)"}

    host_id = host_id_file.read_text(encoding="utf-8").strip()
    try:
        hex_part, tag = split_host_id(host_id)
    except ValueError as e:
        return {
            "present": True,
            "host_id": host_id,
            "note": f"malformed host id: {e}",
        }

    section: dict[str, object] = {
        "present": True,
        "host_id": host_id,
        "short": short_id(host_id),
        "hex_prefix": hex_part,
        "tag": tag,
        "host_id_file": str(host_id_file),
    }

    # Try to look up the host in the manifest.
    if not manifest_path.is_file():
        section["note"] = "manifest file not found; can't resolve name/mode"
        return section
    try:
        manifest = load_manifest(manifest_path)
    except ManifestError as e:
        section["note"] = f"manifest failed to load: {e}"
        return section
    section["manifest_file"] = str(manifest_path)
    if host_id not in manifest.hosts:
        section["registered"] = False
        section["note"] = "host id not in manifest (run `maury init` against this repo)"
        # Best-effort: search for prefix-match in case it's a renamed-tag form.
        for hid in manifest.hosts:
            if host_id_hex_prefix(hid) == hex_part:
                section["manifest_hex_match"] = hid
                section["note"] = (
                    f"host id not in manifest by full match; hex prefix matches {hid!r} — "
                    f"tag may have drifted after edit"
                )
                break
        return section

    host_spec = manifest.hosts[host_id]
    section["registered"] = True
    section["name"] = host_spec.name
    mode_id = host_spec.profile
    section["mode_id"] = mode_id
    mode_spec = manifest.profiles.get(mode_id)
    section["mode_name"] = mode_spec.name if mode_spec else "<unknown>"
    return section


def _section_identity_baseline(*, target_dir: Path) -> dict[str, object]:
    """Compare current `~/.maury-host-id` against the baseline."""
    from maury.bootstrap.init_cmd import current_host_id_file
    from maury.host_identity import baseline_path, read_baseline
    from maury.ids import host_id_hex_prefix

    bp = baseline_path(target_dir)
    if not bp.is_file():
        return {
            "present": False,
            "note": "no baseline (pre-2026-05-14 upgrade; will be created on next sync)",
            "path": str(bp),
        }

    try:
        baseline = read_baseline(target_dir)
    except Exception as e:
        return {"present": False, "note": f"baseline read error: {e}", "path": str(bp)}
    assert baseline is not None

    section: dict[str, object] = {
        "present": True,
        "path": str(bp),
        "baseline_hex": baseline.host_id_hex,
        "mode_id": baseline.mode_id,
        "mode_name_at_bootstrap": baseline.mode_name_at_bootstrap,
        "registered_at": baseline.registered_at,
    }

    host_id_file = current_host_id_file()
    if not host_id_file.is_file():
        section["current_hex"] = None
        section["status"] = "no current host-id file"
        return section

    current_id = host_id_file.read_text(encoding="utf-8").strip()
    try:
        current_hex = host_id_hex_prefix(current_id)
    except ValueError:
        section["status"] = "current host id is malformed"
        return section

    section["current_hex"] = current_hex
    if baseline.host_id_hex == current_hex:
        section["status"] = "match"
    else:
        section["status"] = "mismatch"
        section["remediation"] = (
            "run `maury sync --confirm-identity-change` if the edit was deliberate, "
            "or `maury init --reset` to re-anchor as a fresh registration"
        )
    return section


def _section_repos(*, manifest_path: Path, repos_root: Path) -> dict[str, object]:
    """For each repo declared in this host's manifest entry, show
    remote URL, access mode, local-clone path, and git status if cloned."""
    from maury.bootstrap.init_cmd import current_host_id_file

    host_id_file = current_host_id_file()
    if not host_id_file.is_file():
        return {"present": False, "note": "no host id (run `maury init`)"}
    host_id = host_id_file.read_text(encoding="utf-8").strip()

    if not manifest_path.is_file():
        return {"present": False, "note": f"manifest file not found at {manifest_path}"}
    try:
        manifest = load_manifest(manifest_path)
    except ManifestError as e:
        return {"present": False, "note": f"manifest failed to load: {e}"}

    if host_id not in manifest.hosts:
        return {"present": False, "note": "this host not registered in manifest"}

    host_spec = manifest.hosts[host_id]
    repos: dict[str, object] = {}
    for nickname, spec in host_spec.repos.items():
        clone_path = repos_root / nickname
        entry: dict[str, object] = {
            "url": spec.url,
            "mode": spec.mode.value if hasattr(spec.mode, "value") else str(spec.mode),
            "backend": spec.backend,
            "clone_path": str(clone_path),
            "cloned": False,
        }
        if clone_path.is_dir() and (clone_path / ".git").exists():
            entry["cloned"] = True
            entry.update(_git_status_snapshot(clone_path))
        repos[nickname] = entry
    return {
        "present": True,
        "repos_root": str(repos_root),
        "repos": repos,
    }


def _git_status_snapshot(repo_path: Path) -> dict[str, object]:
    """Capture branch + dirty flag + ahead/behind for a local clone.

    Defensive: any git error becomes an `error` field rather than an
    exception. Each git call is timeout-capped (5s) so a hung repo
    never blocks `maury status`.
    """
    import subprocess

    def run(args: list[str]) -> tuple[int, str, str]:
        try:
            r = subprocess.run(args, cwd=repo_path, capture_output=True, text=True, timeout=5)
            return r.returncode, r.stdout, r.stderr
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
            return 1, "", str(e)

    snapshot: dict[str, object] = {}

    rc, out, err = run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    if rc == 0:
        snapshot["branch"] = out.strip()
    else:
        snapshot["error"] = f"git rev-parse failed: {err.strip()[:120]}"
        return snapshot

    rc, out, _ = run(["git", "status", "--porcelain"])
    if rc == 0:
        dirty_lines = [ln for ln in out.splitlines() if ln.strip()]
        snapshot["dirty"] = bool(dirty_lines)
        snapshot["dirty_count"] = len(dirty_lines)

    # Upstream tracking: behind/ahead counts. The `@{u}` ref errors if
    # the branch isn't tracking; that's not a real failure, just no data.
    rc, out, _ = run(["git", "rev-list", "--left-right", "--count", "@{u}...HEAD"])
    if rc == 0 and out.strip():
        parts = out.strip().split()
        if len(parts) == 2:
            try:
                snapshot["behind"] = int(parts[0])
                snapshot["ahead"] = int(parts[1])
            except ValueError:
                pass

    return snapshot


def _section_last_render(*, target_dir: Path) -> dict[str, object]:
    """Summarize last-render.json."""
    last = read_last_render(target_dir)
    if last is None:
        return {
            "present": False,
            "note": "not yet rendered (run `maury sync` or `maury init`)",
        }
    return {
        "present": True,
        "rendered_at": last.rendered_at,
        "host_id": last.host_id,
        "profile_id": last.profile_id,
        "file_count": len(last.files),
    }


def _section_drift(*, target_dir: Path) -> dict[str, object]:
    """Walk the target dir against last-render and report drift counts."""
    last = read_last_render(target_dir)
    if last is None:
        return {"present": False, "note": "no baseline (run `maury sync` first)"}
    try:
        report = detect_drift(
            target_dir=target_dir,
            last=last,
            untracked_scan_dirs=DRIFT_SCAN_DIRS,
        )
    except Exception as e:
        return {"present": False, "note": f"drift scan failed: {e}"}
    counts = report.summary_counts()
    return {
        "present": True,
        "modified": counts["modified"],
        "missing": counts["missing"],
        "untracked": counts["untracked"],
        "clean": not report.has_drift(),
    }


def _section_mining_watermarks(*, target_dir: Path) -> dict[str, object]:
    """Per-project mining watermark summary."""
    from maury.mining_state import MINING_ALGORITHM_VERSION, read_watermark

    wm = read_watermark(target_dir)
    if wm is None:
        return {
            "present": False,
            "note": "not yet mined (run `maury mine`)",
        }
    return {
        "present": True,
        "algorithm_version": wm.mining_algorithm_version,
        "algorithm_version_current": MINING_ALGORITHM_VERSION,
        "stale": wm.is_stale(),
        "projects": {
            name: {
                "last_mined_at": rec.last_mined_at,
                "last_jsonl_mtime": rec.last_jsonl_mtime,
                "windows_processed": rec.windows_processed,
                "findings_count": rec.findings_count,
            }
            for name, rec in wm.projects.items()
        },
    }


def _render_status_text(report: dict[str, object]) -> str:
    """Render the status report as human-readable text. Mirrors the
    JSON shape section-for-section so the two stay in sync."""
    lines: list[str] = []

    def section(title: str) -> None:
        lines.append("")
        lines.append(f"== {title} ==")

    # ---- host identity ----
    section("host identity")
    h = report["host_identity"]
    assert isinstance(h, dict)
    if not h.get("present"):
        lines.append(f"  {h.get('note', 'unknown')}")
    else:
        lines.append(f"  id:    {h.get('host_id')}")
        lines.append(f"  short: {h.get('short')}")
        if h.get("tag"):
            lines.append(f"  tag:   {h.get('tag')}")
        if h.get("registered"):
            lines.append(f"  name:  {h.get('name')}")
            lines.append(f"  mode:  {h.get('mode_name')} ({short_id(str(h.get('mode_id', '')))})")
        else:
            lines.append(f"  ⚠️  {h.get('note', '')}")

    # ---- identity baseline ----
    section("identity baseline (ADR-0042)")
    b = report["identity_baseline"]
    assert isinstance(b, dict)
    if not b.get("present"):
        lines.append(f"  {b.get('note', 'unknown')}")
    else:
        lines.append(f"  baseline hex:        {b.get('baseline_hex')}")
        lines.append(f"  current hex:         {b.get('current_hex')}")
        lines.append(f"  mode at bootstrap:   {b.get('mode_name_at_bootstrap')}")
        lines.append(f"  registered at:       {b.get('registered_at')}")
        status_val = b.get("status")
        if status_val == "match":
            lines.append("  status:              ✓ match")
        else:
            lines.append(f"  status:              ⚠️  {status_val}")
            if b.get("remediation"):
                lines.append(f"  remediation:         {b.get('remediation')}")

    # ---- repos ----
    section("repos")
    rs = report["repos"]
    assert isinstance(rs, dict)
    if not rs.get("present"):
        lines.append(f"  {rs.get('note', 'unknown')}")
    else:
        lines.append(f"  repos-root: {rs.get('repos_root')}")
        repos_map = rs.get("repos", {})
        assert isinstance(repos_map, dict)
        if not repos_map:
            lines.append("  (no repos declared for this host)")
        for nickname, entry in repos_map.items():
            assert isinstance(entry, dict)
            lines.append(
                f"  • {nickname:<12} url={entry.get('url')} mode={entry.get('mode')} backend={entry.get('backend')}"
            )
            if not entry.get("cloned"):
                lines.append("    clone: (not cloned — run `maury sync`)")
                continue
            line_parts = [f"branch={entry.get('branch')}"]
            if "ahead" in entry or "behind" in entry:
                line_parts.append(f"ahead={entry.get('ahead', 0)}")
                line_parts.append(f"behind={entry.get('behind', 0)}")
            if entry.get("dirty"):
                line_parts.append(f"⚠️ dirty ({entry.get('dirty_count')} files)")
            elif entry.get("dirty") is False:
                line_parts.append("✓ clean")
            if entry.get("error"):
                line_parts.append(f"error={entry.get('error')}")
            lines.append("    " + "  ".join(line_parts))

    # ---- last render ----
    section("last render (ADR-0017)")
    r = report["last_render"]
    assert isinstance(r, dict)
    if not r.get("present"):
        lines.append(f"  {r.get('note', 'unknown')}")
    else:
        lines.append(f"  rendered at:  {r.get('rendered_at')}")
        lines.append(f"  files:        {r.get('file_count')}")
        lines.append(f"  host id:      {short_id(str(r.get('host_id', '')))}")
        lines.append(f"  mode id:      {short_id(str(r.get('profile_id', '')))}")

    # ---- drift ----
    section("drift")
    d = report["drift"]
    assert isinstance(d, dict)
    if not d.get("present"):
        lines.append(f"  {d.get('note', 'unknown')}")
    elif d.get("clean"):
        lines.append("  ✓ clean (no drift)")
    else:
        lines.append(f"  modified={d.get('modified')} missing={d.get('missing')} untracked={d.get('untracked')}")
        lines.append("  (run `maury reconcile` to address drift)")

    # ---- mining watermarks ----
    section("mining watermarks (ADR-0043)")
    m = report["mining_watermarks"]
    assert isinstance(m, dict)
    if not m.get("present"):
        lines.append(f"  {m.get('note', 'unknown')}")
    else:
        algo_v = m.get("algorithm_version")
        cur_v = m.get("algorithm_version_current")
        lines.append(f"  algorithm version: {algo_v} (current: {cur_v})")
        if m.get("stale"):
            lines.append("  ⚠️  watermarks stale — next mine will re-process everything")
        projects = m.get("projects", {})
        assert isinstance(projects, dict)
        if not projects:
            lines.append("  (no projects mined yet)")
        else:
            lines.append("  projects:")
            for name, rec in projects.items():
                short_name = name[:48] + "…" if len(name) > 49 else name
                lines.append(
                    f"    {short_name:<50} "
                    f"mined={rec['last_mined_at']} "
                    f"findings={rec['findings_count']} "
                    f"windows={rec['windows_processed']}"
                )

    return "\n".join(lines).lstrip("\n")


@main.command("reconcile")
@click.option(
    "--manifest-file",
    "manifest_file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    envvar=DEFAULT_MANIFEST_ENV,
    help="Manifest file. Defaults to ./.meta/manifest.json or $MAURY_MANIFEST_FILE.",
)
@click.option(
    "--target",
    "target_dir",
    type=click.Path(file_okay=False, path_type=Path),
    default="~/.claude",
    show_default=True,
    help="Target directory whose drift to reconcile.",
)
@click.option(
    "--repos-root",
    "repos_root",
    type=click.Path(file_okay=False, path_type=Path),
    default="~/.config/maury/repos",
    show_default=True,
    help="Where local clones live.",
)
def reconcile_cmd(
    manifest_file: Path | None,
    target_dir: Path,
    repos_root: Path,
) -> None:
    """Walk hand-edit drift on the target dir and prompt for each.

    Per ADR-0017's reconcile menu (5 actions for hand-edits): adopt /
    adapt / mark-managed / revert / skip-once. Run after `maury sync`
    refuses on drift, or any time you want to inspect/clean up drift.

    v0: hand-edit menu only. Claude-write drift menu (3 actions) lands
    when claude-writes.jsonl attribution is wired (Phase 5.x.a slice 5).
    """
    target_dir = target_dir.expanduser()
    repos_root = repos_root.expanduser()
    mpath = manifest_file or DEFAULT_MANIFEST_PATH
    if not mpath.exists():
        raise click.ClickException(f"manifest file not found: {mpath}")

    # ADR-0042 identity guard before any mode-scoped reconciliation.
    _enforce_identity_guard(target_dir=target_dir)

    # Read baseline; refuse if absent (no baseline = no drift to reconcile).
    last = read_last_render(target_dir)
    if last is None:
        click.echo(
            f"no last-render.json found at {target_dir}/maury-state/. Run `maury sync` first to establish a baseline."
        )
        sys.exit(2)

    try:
        m = load_manifest(mpath)
    except ManifestError as e:
        raise click.ClickException(str(e)) from e

    # Resolve current host + profile by host_id stored in last-render
    # (no need to re-do the full host-id lookup since the baseline
    # already encodes which host rendered this).
    host_spec = m.hosts.get(last.host_id)
    if host_spec is None:
        raise click.ClickException(
            f"baseline references host_id {last.host_id!r} which is no longer in the manifest. "
            f"Re-run `maury sync` to re-establish the baseline against the current manifest."
        )
    profile_id = host_spec.profile
    profile_spec = m.profiles.get(profile_id)
    profile_name = profile_spec.name if profile_spec else profile_id

    # Detect drift
    drift_report = detect_drift(
        target_dir=target_dir,
        last=last,
        untracked_scan_dirs=DRIFT_SCAN_DIRS,
    )
    if not drift_report.has_drift():
        click.echo(f"clean: no drift on {target_dir}.")
        return

    counts = drift_report.summary_counts()
    click.echo(f"drift: modified={counts['modified']} missing={counts['missing']} untracked={counts['untracked']}")
    click.echo("")

    # Re-render so REVERT has access to baseline content.
    base_path = repos_root / "base"
    if not base_path.is_dir():
        raise click.ClickException(
            f"base repo not present at {base_path}. Run `maury sync` first (it clones repos before render)."
        )
    try:
        rendered = render(
            repo_paths={"base": base_path},
            manifest=m,
            profile_id=profile_id,
            host_id=last.host_id,
        )
    except RenderError as e:
        raise click.ClickException(f"render failed: {e}") from e

    # Interactive prompter: for each entry, show the diff context and
    # the 5-action menu.
    def _prompter(entry: DriftEntry) -> ReconcileAction:
        click.echo(f"--- {entry.kind.value:<10} {entry.path}")
        if entry.expected_sha and entry.actual_sha:
            click.echo(f"    baseline: {entry.expected_sha[:12]}  on-disk: {entry.actual_sha[:12]}")
        prompt = "  action [{}]".format("/".join(a.value for a in ALL_ACTIONS))
        choice_text = click.prompt(prompt, default=ReconcileAction.SKIP_ONCE.value)
        try:
            return ReconcileAction(choice_text)
        except ValueError:
            click.echo(f"  unrecognized choice {choice_text!r}; defaulting to skip-once.")
            return ReconcileAction.SKIP_ONCE

    # Resolve the base repo path for hand-managed.json writes.
    summary = run_reconcile(
        drift_report=drift_report,
        target_dir=target_dir,
        rendered=rendered,
        prompter=_prompter,
        base_repo=base_path,
        profile_name=profile_name,
        host_name=host_spec.name,
    )

    click.echo("")
    click.echo(
        f"reconcile: "
        f"adopted={summary.captures_written - summary.paths_falling_back_to_adopt} "
        f"adapt-fallback={summary.paths_falling_back_to_adopt} "
        f"marked-managed={summary.paths_marked_managed} "
        f"reverted={summary.paths_reverted} "
        f"skipped={summary.paths_skipped}"
    )
    if summary.has_errors():
        click.echo("errors:")
        for err in summary.errors:
            click.echo(f"  ✗ {err}", err=True)
        sys.exit(1)


DEFAULT_REPOS_ROOT = Path.home() / ".config" / "maury" / "repos"


@main.command("sync")
@click.option(
    "--manifest-file",
    "manifest_file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    envvar=DEFAULT_MANIFEST_ENV,
    help="Manifest file. Defaults to ./.meta/manifest.json or $MAURY_MANIFEST_FILE.",
)
@click.option(
    "--target",
    "target_dir",
    type=click.Path(file_okay=False, path_type=Path),
    default="~/.claude",
    show_default=True,
    help="Target directory where rendered files would be written.",
)
@click.option(
    "--repos-root",
    "repos_root",
    type=click.Path(file_okay=False, path_type=Path),
    default="~/.config/maury/repos",
    show_default=True,
    help="Where local clones live. Each repo nickname becomes a subdir.",
)
@click.option(
    "--check",
    "check",
    is_flag=True,
    help="Dry-run: don't pull from remotes, don't write to target.",
)
@click.option(
    "--force",
    "force",
    is_flag=True,
    help="If drift is detected on the target dir, overwrite anyway. Hand-edits will be lost.",
)
@click.option(
    "--non-interactive",
    "non_interactive",
    is_flag=True,
    help="Refuse on any drift, exit 1. Cron/CI safe.",
)
@click.option(
    "--confirm-identity-change",
    "confirm_identity_change",
    is_flag=True,
    help=(
        "Acknowledge a detected change to `~/.maury-host-id` and rewrite "
        "the identity baseline at `~/.claude/maury-state/host-identity.json`. "
        "Per ADR-0042: use when (a) you deliberately edited the host-id file "
        "or (b) restored it from a different host. For mode re-anchoring, "
        "use `maury init --reset` instead."
    ),
)
def sync_cmd(
    manifest_file: Path | None,
    target_dir: Path,
    repos_root: Path,
    check: bool,
    force: bool,
    non_interactive: bool,
    confirm_identity_change: bool,
) -> None:
    """Pull all reachable repos, render, and apply.

    v0: assumes the host's manifest entry declares a repo named 'base';
    multi-repo profile composition and push of pending proposals are
    deferred to subsequent slices. Drift detection per ADR-0017's three
    flows is wired in (--check / --non-interactive / --force / default).
    """
    target_dir = target_dir.expanduser()
    repos_root = repos_root.expanduser()
    if force and non_interactive:
        raise click.ClickException("--force and --non-interactive are mutually exclusive.")
    mpath = manifest_file or DEFAULT_MANIFEST_PATH
    drift_mode = "force" if force else ("non-interactive" if non_interactive else "default")

    # Live progress: print each repo as it completes
    def _progress(rs: RepoSyncResult) -> None:
        marker = {
            "cloned": "+",
            "pulled": "↻",
            "up-to-date": "=",
            "skipped": "·",
            "error": "✗",
        }.get(rs.action, "?")
        line = f"  {marker} {rs.nickname:<24} {rs.action}"
        if rs.detail:
            line += f"  ({rs.detail[:80]})"
        click.echo(line)

    click.echo(f"sync: manifest={mpath}")
    click.echo(f"      target={target_dir}")
    click.echo(f"      repos-root={repos_root}")
    if check:
        click.echo("      --check (dry-run; no remote I/O, no writes)")
    if force:
        click.echo("      --force (clobber drift)")
    if non_interactive:
        click.echo("      --non-interactive (refuse on drift)")
    click.echo("")

    # ADR-0042 host-identity guard. Runs before any mode-scoped work so a
    # hex-edited `~/.maury-host-id` can't silently swap this host into a
    # different mode-registration. `--confirm-identity-change` lets the
    # user opt past the guard if the edit was deliberate.
    _enforce_identity_guard(target_dir=target_dir, allow_change=confirm_identity_change)

    try:
        result = run_sync(
            manifest_path=mpath,
            target_dir=target_dir,
            repos_root=repos_root,
            dry_run=check,
            drift_mode=drift_mode,
            on_repo_progress=_progress,
        )
    except SyncError as e:
        raise click.ClickException(str(e)) from e

    click.echo("")
    if result.host_id:
        click.echo(f"host:    {short_id(result.host_id)}")
    if result.profile_id:
        click.echo(f"profile: {short_id(result.profile_id)}")

    # Drift report (only present after first-render)
    if result.drift_report is not None:
        counts = result.drift_report.summary_counts()
        click.echo("")
        click.echo(
            f"drift:   modified={counts['modified']} "
            f"missing={counts['missing']} "
            f"untracked={counts['untracked']} "
            f"(action: {result.drift_action})"
        )
        for entry in result.drift_report.entries:
            if entry.kind.value == "expected":
                continue  # skip the boring rows
            click.echo(f"  {entry.kind.value:<10} {entry.path}")

    if result.warnings:
        click.echo("")
        click.echo("warnings:")
        for w in result.warnings:
            click.echo(f"  ! {w}")

    if result.has_errors():
        click.echo("")
        click.echo("errors:")
        for err in result.errors:
            click.echo(f"  ✗ {err}", err=True)
        sys.exit(1)

    if result.render_result is not None:
        click.echo("")
        click.echo(f"render:  {len(result.render_result.files)} file(s)")
        for action in result.apply_actions:
            click.echo(f"  {action}")

    if check:
        click.echo("")
        click.echo("(--check; no remote I/O performed and no files written)")


# ---- render ------------------------------------------------------------


@main.command("render")
@click.option(
    "--manifest-file",
    "manifest_file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    envvar=DEFAULT_MANIFEST_ENV,
    help="Manifest file. Defaults to ./.meta/manifest.json or $MAURY_MANIFEST_FILE.",
)
@click.option(
    "--repo",
    "repo_path",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Path to the repo containing base + profile content. Defaults to the manifest's parent dir.",
)
@click.option(
    "--profile",
    "profile_name_or_id",
    help="Profile name or ID to render for. Defaults to the current host's assigned profile.",
)
@click.option(
    "--host",
    "host_name_or_id",
    help="Host name or ID to render for. Defaults to the current machine.",
)
@click.option(
    "--target",
    "target_dir",
    type=click.Path(file_okay=False, path_type=Path),
    default="~/.claude",
    show_default=True,
    help="Target directory where rendered files would be written.",
)
@click.option("--check", is_flag=True, help="Dry-run: show what would be written without writing.")
def render_cmd(
    manifest_file: Path | None,
    repo_path: Path | None,
    profile_name_or_id: str | None,
    host_name_or_id: str | None,
    target_dir: Path,
    check: bool,
) -> None:
    """Render base + profile chain + host overlay into the target directory."""
    target_dir = target_dir.expanduser()

    # ADR-0042 identity guard. Render writes mode-scoped content to the
    # target dir; a hex-edited `~/.maury-host-id` would silently render
    # the wrong mode's content. Note that `render --check` (dry-run)
    # still guards — the same risk applies to "what would I render?"
    # queries against a swapped identity.
    _enforce_identity_guard(target_dir=target_dir)

    mpath = manifest_file or DEFAULT_MANIFEST_PATH
    if not mpath.exists():
        raise click.ClickException(f"manifest file not found: {mpath}")
    try:
        m = load_manifest(mpath)
    except ManifestError as e:
        raise click.ClickException(str(e)) from e

    repo = repo_path or mpath.parent.parent
    if not repo.is_dir():
        raise click.ClickException(f"repo path is not a directory: {repo}")

    # Resolve host: explicit arg, else current-machine lookup
    if host_name_or_id:
        hid = m.resolve_host(host_name_or_id)
        if hid is None:
            raise click.ClickException(
                f"host {host_name_or_id!r} not in manifest; available: {sorted(s.name for s in m.hosts.values())}"
            )
    else:
        match = m.host_for_current_machine()
        if match is None:
            raise click.ClickException(
                "could not determine current host (no ~/.maury-host-id and "
                f"hostname not in manifest). Pass --host explicitly. "
                f"Available: {sorted(s.name for s in m.hosts.values())}"
            )
        hid, _ = match

    host_spec = m.hosts[hid]

    # Resolve profile: explicit arg, else use the host's assigned profile
    if profile_name_or_id:
        pid = m.resolve_profile(profile_name_or_id)
        if pid is None:
            raise click.ClickException(
                f"profile {profile_name_or_id!r} not in manifest; "
                f"available: {sorted(s.name for s in m.profiles.values())}"
            )
    else:
        pid = host_spec.profile

    try:
        result = render(
            repo_paths={"base": repo},
            manifest=m,
            profile_id=pid,
            host_id=hid,
        )
    except RenderError as e:
        raise click.ClickException(str(e)) from e

    profile_name = m.profiles[pid].name
    click.echo(
        f"rendering host={host_spec.name} profile={profile_name} "
        f"(layers={len(result.layers)}, files={len(result.files)})"
    )
    for w in result.warnings:
        click.echo(f"  warn: {w}", err=True)

    actions = apply_render(result, target_dir, dry_run=check)
    for a in actions:
        click.echo(f"  {a}")
    if check:
        click.echo("(--check; no files written)")


@main.command()
def review() -> None:
    """Review queued proposals interactively."""
    raise click.ClickException("not yet implemented")


@main.command("promote-review")
def promote_review() -> None:
    """Review and execute cross-repo promotion proposals."""
    raise click.ClickException("not yet implemented")


# ---- mine ---------------------------------------------------------------


_DEFAULT_PROJECTS_DIR = Path.home() / ".claude" / "projects"


@main.command("mine")
@click.option(
    "--projects-dir",
    "projects_dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=str(_DEFAULT_PROJECTS_DIR),
    show_default="~/.claude/projects",
    help="Directory holding per-project transcript JSONL.",
)
@click.option(
    "--project",
    "project_name",
    help="Specific project hash dir to mine. Default: highest-volume project.",
)
@click.option(
    "--window-size",
    type=int,
    default=50,
    show_default=True,
    help="User messages per LLM extraction window.",
)
@click.option(
    "--max-windows",
    type=int,
    default=None,
    help="Cap on windows to process (cost control). Default: no cap.",
)
@click.option(
    "--backend",
    "backend_name",
    type=click.Choice(["cli", "sdk"]),
    default=None,
    help="LLM backend to use. Default: cli (uses Claude Code subscription quota).",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["text", "json"]),
    default="text",
    show_default=True,
)
@click.option(
    "--crossref",
    "crossref_enabled",
    is_flag=True,
    help=(
        "After extraction, classify each finding against ~/.claude/CLAUDE.md "
        "via the four-state model (NEW / PRESENT_AND_CLEAR / PRESENT_BUT_UNCLEAR / "
        "PRESENT_AND_REINFORCED). Per ADR-0020. Adds one LLM call per finding."
    ),
)
@click.option(
    "--claude-md",
    "claude_md_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=str(Path.home() / ".claude" / "CLAUDE.md"),
    show_default="~/.claude/CLAUDE.md",
    help="Path to current CLAUDE.md for cross-reference (only used with --crossref).",
)
@click.option(
    "--repo",
    "repo_path",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help=(
        "Synced-repo path for temporal cross-reference lookup (git history of "
        "CLAUDE.md). Only used with --crossref. None = current-only mode."
    ),
)
@click.option(
    "--cwd",
    "cwd_override",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help=(
        "Derive the project dir name from this path via the verified "
        "`derive_project_dir()` algorithm (ADR-0043). Overrides the "
        "no-args 'derive from current cwd' default. Ignored if --project "
        "is also passed."
    ),
)
@click.option(
    "--full",
    "full",
    is_flag=True,
    help=(
        "Ignore the per-project mining watermark and re-mine all "
        "transcripts. Use when the mining algorithm changes or you "
        "suspect missed signal. Per ADR-0043."
    ),
)
@click.option(
    "--since",
    "since",
    type=str,
    default=None,
    help=(
        "Mine transcripts modified since this ISO-8601 date (e.g., "
        "`2026-05-01`). Overrides the watermark for this run only; "
        "does NOT update the canonical watermark on success. Per ADR-0043."
    ),
)
def mine_cmd(
    projects_dir: Path,
    project_name: str | None,
    window_size: int,
    max_windows: int | None,
    backend_name: str | None,
    output_format: str,
    crossref_enabled: bool,
    claude_md_path: Path,
    repo_path: Path | None,
    cwd_override: Path | None,
    full: bool,
    since: str | None,
) -> None:
    """Mine transcripts for durable preference candidates (Phase 6a + 6c).

    Walks JSONL transcripts under --projects-dir, batches user messages
    into windows of --window-size, and asks the configured LLM backend
    to extract durable preferences from each window.

    With --crossref, each finding is then classified against your current
    CLAUDE.md (and historical CLAUDE.md from --repo's git history, if
    provided) into one of four states.

    By default, picks the project with the most user messages. Pass
    --project to target a specific one. Pass --max-windows to cap cost.
    """
    # ADR-0042 identity guard. Mining reads transcript history scoped to
    # this host's mode-registration; an accidental host-id swap would
    # surface the wrong sessions. The baseline lives at the standard
    # render-target location (`~/.claude/`).
    target_dir = Path.home() / ".claude"
    _enforce_identity_guard(target_dir=target_dir)

    # Resolve target project per ADR-0043:
    #   1. --project <name> → literal lookup
    #   2. --cwd <path> → derive name from given path
    #   3. else: derive from Path.cwd(); fall back to busiest if no match
    target_project_dir = _resolve_mining_project(
        projects_dir=projects_dir,
        project_name=project_name,
        cwd_override=cwd_override,
    )

    # Load the watermark and decide the cutoff for incremental mining
    # (ADR-0043 §"Default behavior: incremental mining"). `--since`
    # overrides for one run without updating the canonical watermark;
    # `--full` skips the watermark entirely.
    cutoff_mtime: float | None = _resolve_mining_cutoff(
        target_dir=target_dir,
        project_dir_name=target_project_dir.name,
        full=full,
        since=since,
    )

    # Walk + filter messages, tracking the highest jsonl mtime seen so
    # we can update the watermark on success.
    msgs, highest_mtime = _collect_messages_with_watermark(target_project_dir, cutoff_mtime)
    click.echo(f"signal-bearing user messages after noise filter: {len(msgs)}")
    if not msgs:
        if cutoff_mtime is not None:
            click.echo("nothing new to mine since the last watermark.")
        else:
            click.echo("nothing to mine.")
        return

    # Backend.
    try:
        llm = get_backend(backend_name)
    except (ValueError, BackendUnavailableError) as e:
        raise click.ClickException(f"LLM backend not available: {e}") from e
    click.echo(f"LLM backend: {llm.name}")

    # Run extraction with a tiny per-window status line.
    from maury.mining import ExtractionWindow as _Window

    def _on_window_start(w: _Window) -> None:
        click.echo(f"  window {w.index + 1}: {len(w.messages)} messages, {len(w.session_ids)} session(s)...")

    def _on_window_done(w: _Window, findings: list[Finding]) -> None:
        click.echo(f"    -> {len(findings)} finding(s)")

    result = extract_from_messages(
        msgs,
        llm=llm,
        project=target_project_dir.name,
        window_size=window_size,
        max_windows=max_windows,
        on_window_start=_on_window_start,
        on_window_done=_on_window_done,
    )

    # Cross-reference (Phase 6c).
    crossref_summary = None
    if crossref_enabled and result.findings:
        from maury.mining import crossref_findings

        click.echo("")
        click.echo(f"cross-referencing {len(result.findings)} finding(s) against {claude_md_path}...")
        if repo_path:
            click.echo(f"  using git history from: {repo_path}")
        current_md = claude_md_path.read_text(encoding="utf-8")

        def _on_xref_progress(i: int, total: int, finding: Finding, xref: object) -> None:
            state = getattr(xref, "state", "?")
            click.echo(f"  [{i}/{total}] {state}  ({finding.text[:60]})")

        crossref_summary = crossref_findings(
            result.findings,
            llm=llm,
            current_claude_md=current_md,
            repo_path=repo_path,
            on_progress=_on_xref_progress,
        )

    # Output.
    if output_format == "json":
        click.echo(_findings_as_json(result.findings, summary=crossref_summary))
    else:
        click.echo(_findings_as_text(result.findings, result.windows_processed, summary=crossref_summary))

    if result.warnings:
        click.echo("")
        click.echo("warnings:", err=True)
        for w in result.warnings:
            click.echo(f"  {w}", err=True)

    # Update the watermark on success per ADR-0043. Skipped if --since
    # was used (override does not touch the canonical watermark) or if
    # we processed no jsonls (highest_mtime is None).
    if since is None and highest_mtime is not None:
        _update_mining_watermark(
            target_dir=target_dir,
            project_dir_name=target_project_dir.name,
            highest_mtime=highest_mtime,
            windows_processed=result.windows_processed,
            findings_count=len(result.findings),
        )


def _resolve_mining_project(
    *,
    projects_dir: Path,
    project_name: str | None,
    cwd_override: Path | None,
) -> Path:
    """Resolve which project dir to mine per ADR-0043 §"Default behavior."

    Precedence: --project literal > --cwd derivation > Path.cwd()
    derivation > busiest-project fallback.
    """
    from maury.projects import derive_project_dir

    if project_name:
        target = projects_dir / project_name
        if not target.is_dir():
            raise click.ClickException(f"project directory not found: {target}")
        return target

    derive_source: Path = cwd_override if cwd_override is not None else Path.cwd()
    derived_name = derive_project_dir(derive_source)
    derived_dir = projects_dir / derived_name
    if derived_dir.is_dir():
        click.echo(f"selected project (derived from {derive_source}): {derived_name}")
        return derived_dir

    # No project dir for this cwd → fall back to busiest. Print the note
    # before the call so it's visible whether or not the fallback also
    # fails (e.g., projects/ has no minable content).
    click.echo(
        f"note: cwd {str(derive_source)!r} has no project dir under {projects_dir}; falling back to busiest project"
    )
    busiest = _pick_busiest_project(projects_dir)
    click.echo(f"selected project (busiest): {busiest.name}")
    return busiest


def _resolve_mining_cutoff(
    *,
    target_dir: Path,
    project_dir_name: str,
    full: bool,
    since: str | None,
) -> float | None:
    """Return the POSIX-mtime cutoff for incremental mining, or None to
    mine everything.

    --full       -> None (mine all)
    --since X    -> parse X, return epoch (overrides watermark for one run)
    watermark    -> use last_jsonl_mtime as cutoff
    else         -> None (first mine for this project)
    """
    from datetime import datetime

    from maury.mining_state import load_or_init_watermark

    if full:
        return None
    if since is not None:
        try:
            cutoff_dt = datetime.fromisoformat(since)
        except ValueError as e:
            raise click.ClickException(f"--since: could not parse {since!r} as ISO-8601 date: {e}") from e
        return cutoff_dt.timestamp()

    wm = load_or_init_watermark(target_dir)
    record = wm.record_for(project_dir_name)
    if record is None:
        return None
    try:
        return datetime.fromisoformat(record.last_jsonl_mtime.rstrip("Z")).timestamp()
    except ValueError:
        # Corrupt watermark date — be conservative and re-mine all.
        return None


def _collect_messages_with_watermark(
    project_dir: Path,
    cutoff_mtime: float | None,
) -> tuple[list[TranscriptMessage], float | None]:
    """Walk JSONLs under `project_dir`, filtering by mtime cutoff.

    Returns the collected messages and the highest mtime seen across the
    processed JSONLs (None if no JSONLs were processed). Mining writes
    that mtime as the new watermark on success.
    """
    from maury.mining import walk_user_messages_in_file

    msgs: list[TranscriptMessage] = []
    highest: float | None = None
    for jsonl in sorted(project_dir.rglob("*.jsonl")):
        try:
            mt = jsonl.stat().st_mtime
        except OSError:
            continue
        if cutoff_mtime is not None and mt <= cutoff_mtime:
            continue
        msgs.extend(walk_user_messages_in_file(jsonl, project=project_dir.name))
        if highest is None or mt > highest:
            highest = mt
    return msgs, highest


def _update_mining_watermark(
    *,
    target_dir: Path,
    project_dir_name: str,
    highest_mtime: float,
    windows_processed: int,
    findings_count: int,
) -> None:
    """Update one project's watermark after a successful mining run."""
    from datetime import UTC, datetime

    from maury.mining_state import (
        ProjectMiningRecord,
        load_or_init_watermark,
        write_watermark,
    )

    wm = load_or_init_watermark(target_dir)
    now_iso = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    mtime_iso = datetime.fromtimestamp(highest_mtime, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    wm.update(
        project_dir_name,
        ProjectMiningRecord(
            last_mined_at=now_iso,
            last_jsonl_mtime=mtime_iso,
            windows_processed=windows_processed,
            findings_count=findings_count,
        ),
    )
    write_watermark(target_dir, wm)


def _pick_busiest_project(projects_dir: Path) -> Path:
    """Default project: the one with the most signal-bearing user messages."""
    candidates: list[tuple[int, Path]] = []
    for child in projects_dir.iterdir():
        if not child.is_dir():
            continue
        n = sum(1 for _ in walk_user_messages(child))
        if n:
            candidates.append((n, child))
    if not candidates:
        raise click.ClickException(f"no projects with user messages found under {projects_dir}")
    candidates.sort(reverse=True)
    return candidates[0][1]


def _findings_as_text(
    findings: list[Finding],
    windows_processed: int,
    *,
    summary: CrossRefSummary | None = None,
) -> str:
    """Render findings as human-readable text. If summary is a CrossRefSummary, group by state."""
    if not findings:
        return f"\nprocessed {windows_processed} window(s), found nothing durable."

    if summary is None:
        # No cross-reference: flat list
        lines = [f"\n=== {len(findings)} finding(s) across {windows_processed} window(s) ==="]
        for f in findings:
            lines.append(f"\n[{f.confidence}] {f.kind}/{f.scope_hint}: {f.text}")
            if f.evidence:
                lines.append(f"   evidence: {f.evidence!r}")
        return "\n".join(lines)

    # Cross-referenced: group by state, with the killer signal (REINFORCED) first
    by_state = summary.by_state
    state_order = ("PRESENT_AND_REINFORCED", "PRESENT_BUT_UNCLEAR", "NEW", "PRESENT_AND_CLEAR")
    state_marker = {
        "PRESENT_AND_REINFORCED": "WARN",
        "PRESENT_BUT_UNCLEAR": "review",
        "NEW": "propose",
        "PRESENT_AND_CLEAR": "ok",
    }
    lines = [
        f"\n=== {summary.total()} finding(s) across {windows_processed} window(s), grouped by cross-reference state ==="
    ]
    counts = " | ".join(f"{state}={len(by_state.get(state, []))}" for state in state_order)
    lines.append(f"summary: {counts}")
    for state in state_order:
        bucket = by_state.get(state, [])
        if not bucket:
            continue
        marker = state_marker[state]
        lines.append(f"\n--- {state} ({len(bucket)}) [{marker}] ---")
        for f, xref in bucket:
            lines.append(f"\n[{f.confidence}] {f.kind}/{f.scope_hint}: {f.text}")
            if f.evidence:
                lines.append(f"   evidence: {f.evidence!r}")
            lines.append(f"   xref: {xref.rationale}")
            if xref.claude_md_quote:
                lines.append(f"   matches CLAUDE.md: {xref.claude_md_quote[:200]!r}")
            lines.append(f"   suggested action: {xref.suggested_action}")
    return "\n".join(lines)


def _findings_as_json(findings: list[Finding], *, summary: CrossRefSummary | None = None) -> str:
    """Render findings as JSON. With summary, includes per-finding xref state."""
    import json as _json

    xref_by_finding_id: dict[int, CrossRefResult] = {}
    if summary is not None:
        for bucket in summary.by_state.values():
            for f, xref in bucket:
                xref_by_finding_id[id(f)] = xref

    payload = []
    for f in findings:
        entry: dict[str, object] = {
            "kind": f.kind,
            "scope_hint": f.scope_hint,
            "text": f.text,
            "evidence": f.evidence,
            "confidence": f.confidence,
            "source_window_index": f.source_window.index,
            "source_project": f.source_window.project,
            "source_window_first_ts": f.source_window.first_timestamp,
            "source_window_last_ts": f.source_window.last_timestamp,
        }
        xref_opt: CrossRefResult | None = xref_by_finding_id.get(id(f))
        if xref_opt is not None:
            xref = xref_opt
            entry["crossref"] = {
                "state": xref.state,
                "claude_md_quote": xref.claude_md_quote,
                "rationale": xref.rationale,
                "suggested_action": xref.suggested_action,
            }
        payload.append(entry)
    return _json.dumps(payload, indent=2)


# ---- doctor ------------------------------------------------------------


_DEFAULT_CLAUDE_MD = Path.home() / ".claude" / "CLAUDE.md"


@main.command()
@click.option(
    "--file",
    "claude_md",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=str(_DEFAULT_CLAUDE_MD),
    show_default="~/.claude/CLAUDE.md",
    help="Path to CLAUDE.md to evaluate.",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["text", "json"]),
    default="text",
    show_default=True,
)
@click.option(
    "--fail-on",
    type=click.Choice(["error", "warn", "info", "never"]),
    default="error",
    show_default=True,
    help="Exit non-zero if findings at this severity or higher are present.",
)
def doctor(claude_md: Path, output_format: str, fail_on: str) -> None:
    """Evaluate a CLAUDE.md against Anthropic's best-practices rubric,
    plus system-health checks on this host's maury-state.

    Content rules evaluate `--file` against Anthropic's rubric:
      https://code.claude.com/docs/en/best-practices

    System-health rules (added 2026-05-18) check `~/.claude/maury-state/`
    for transition states `maury status` also surfaces but that CI
    pipelines wouldn't otherwise catch — baseline-missing-with-host-id
    (ADR-0042 pre-amendment upgrade case) and mining-watermark-stale
    (ADR-0043 algorithm-version bump).
    """
    # ADR-0042 identity guard. Doctor evaluates content scoped to this
    # host's mode-registration; if the host_id was edited, the content
    # under `~/.claude/` may belong to a different mode and a "clean"
    # doctor result would be misleading. Guard against the default
    # target (`~/.claude/`).
    target_dir = Path.home() / ".claude"
    _enforce_identity_guard(target_dir=target_dir)

    text = claude_md.read_text(encoding="utf-8")
    findings = run_all(text, source_path=str(claude_md))

    # Per the 2026-05-18 doctor expansion (Charles's "both" pick on
    # the design call): system-health rules contribute to the same
    # findings list. severity is unified; `--fail-on` aggregates over
    # content + system-health.
    from maury.bootstrap.init_cmd import current_host_id_file

    findings.extend(
        run_system_health_checks(
            target_dir=target_dir,
            host_id_file=current_host_id_file(),
        )
    )

    report = Report(
        source_path=str(claude_md),
        line_count=len(text.splitlines()),
        char_count=len(text),
        findings=findings,
    )

    if output_format == "json":
        click.echo(render_json(report))
    else:
        click.echo(render_text(report))

    if fail_on == "never":
        return
    severity_rank = {"info": 1, "warn": 2, "error": 3}
    threshold = severity_rank[fail_on]
    worst = max(
        (severity_rank[f.severity.value] for f in findings),
        default=0,
    )
    if worst >= threshold:
        sys.exit(1)


# ---- verify-cc-hooks command --------------------------------------------


def _print_empirical_report(report: HarnessReport, output_format: str) -> None:
    """Pretty-print or JSON-print a HarnessReport."""
    if output_format == "json":
        payload = {
            "workspace": str(report.workspace),
            "claude_invoked": report.claude_invoked,
            "claude_returncode": report.claude_returncode,
            "claude_stderr_excerpt": report.claude_stderr_excerpt,
            "passed": report.passed,
            "probes": [
                {
                    "name": p.name,
                    "passed": p.passed,
                    "detail": p.detail,
                    "captured": p.captured,
                }
                for p in report.probes
            ],
        }
        click.echo(json.dumps(payload, indent=2, default=str))
        return

    if not report.claude_invoked:
        click.echo(f"❌ {report.claude_stderr_excerpt}")
        click.echo("\nThis test requires `claude` (Claude Code CLI) on PATH.")
        return

    if report.claude_returncode != 0:
        click.echo(f"⚠️  claude exited with code {report.claude_returncode}")
        if report.claude_stderr_excerpt:
            click.echo(f"   stderr (last 400 chars): {report.claude_stderr_excerpt}")
        click.echo()

    if not report.probes:
        click.echo("(no probes ran — claude failed before any tool use)")
        return

    click.echo(f"workspace: {report.workspace}")
    click.echo()
    for p in report.probes:
        mark = "✅" if p.passed else "❌"
        click.echo(f"{mark} {p.name}")
        click.echo(f"   {p.detail}")
        if p.captured:
            for k, v in p.captured.items():
                click.echo(f"   {k}: {v!r}")
        click.echo()

    if report.passed:
        click.echo("All empirical hook claims verified. ADR-0023's marker scheme is safe.")
    else:
        click.echo("One or more probes failed. See ADR-0023 §'Empirical-test debt'.")


@main.command("verify-cc-hooks")
@click.option(
    "--workspace",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Persist the probe workspace at this path (default: a fresh temp dir, kept for inspection).",
)
@click.option(
    "--prompt",
    "claude_prompt",
    type=str,
    default="List the files in the current directory.",
    show_default=True,
    help="Prompt to send to `claude -p`. Should reliably trigger at least one tool use.",
)
@click.option(
    "--timeout",
    type=float,
    default=60.0,
    show_default=True,
    help="Seconds to wait for the claude subprocess.",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["text", "json"]),
    default="text",
    show_default=True,
)
@click.option(
    "--check-only",
    is_flag=True,
    help="Only check whether `claude` is present on PATH; don't run the harness.",
)
def verify_cc_hooks(
    workspace: Path | None,
    claude_prompt: str,
    timeout: float,
    output_format: str,
    check_only: bool,
) -> None:
    """Verify Claude Code's hook subprocess behaviors empirically.

    Three load-bearing assumptions about Claude Code's hook subsystem are
    documented in `docs/claude-code-contract.md` as ❓ Assumed but unverified.
    ADR-0023 names them as load-bearing for the `# maury-managed` marker
    scheme and for drift attribution.

    This command sets up an isolated workspace, drops probe `PostToolUse`
    hooks into it, runs `claude -p` against it so the hooks fire, and
    reports pass/fail for each empirical claim. Nothing in the user's
    real `~/.claude/` is modified.

    Exit code: 0 if all probes pass, 1 otherwise.
    """
    if check_only:
        if claude_present():
            click.echo("✅ `claude` is present on PATH.")
            sys.exit(0)
        else:
            click.echo("❌ `claude` not found on PATH.")
            sys.exit(1)

    report = run_empirical(
        workspace=workspace,
        claude_prompt=claude_prompt,
        timeout=timeout,
    )

    _print_empirical_report(report, output_format)
    sys.exit(0 if report.passed else 1)


# ---- verify-cc-projects-dir command -------------------------------------


def _print_project_dir_report(report: ProjectDirHarnessReport, output_format: str) -> None:
    """Pretty-print or JSON-print a ProjectDirHarnessReport."""
    if output_format == "json":
        payload = {
            "test_root": str(report.test_root),
            "claude_present": report.claude_present,
            "claude_version": report.claude_version,
            "passed": report.passed,
            "collision_groups_verified": list(report.collision_groups_verified),
            "new_project_dirs": list(report.new_project_dirs),
            "results": [
                {
                    "description": r.case.description,
                    "cwd_suffix": r.case.cwd_suffix,
                    "cwd": str(r.cwd),
                    "predicted": r.predicted_dir_name,
                    "observed": list(r.observed_dir_names),
                    "collision_group": r.case.collision_group,
                    "matched": r.matched,
                    "detail": r.detail,
                }
                for r in report.results
            ],
        }
        click.echo(json.dumps(payload, indent=2, default=str))
        return

    if not report.claude_present:
        click.echo("❌ `claude` not found on PATH.")
        click.echo("\nThis verifier requires Claude Code installed and authenticated.")
        return

    click.echo(f"claude version: {report.claude_version}")
    click.echo(f"test root: {report.test_root}")
    click.echo()
    for r in report.results:
        mark = "✅" if r.matched else "❌"
        group = f" (group={r.case.collision_group})" if r.case.collision_group else ""
        click.echo(f"{mark} {r.case.cwd_suffix!r}  — {r.case.description}{group}")
        click.echo(f"   predicted: {r.predicted_dir_name!r}")
        if r.observed_dir_names:
            click.echo(f"   observed : {list(r.observed_dir_names)}")
        else:
            click.echo("   observed : (none — bucketed into existing dir or claude failed)")
        click.echo(f"   {r.detail}")
        click.echo()

    declared_groups = {r.case.collision_group for r in report.results if r.case.collision_group}
    if declared_groups:
        click.echo(f"collision groups declared: {sorted(declared_groups)}")
        click.echo(f"collision groups verified: {sorted(report.collision_groups_verified)}")
        click.echo()

    if report.passed:
        click.echo(
            "All corpus cases matched the predictor. Algorithm holds at this claude version.\n"
            "See `cc-contract:project-directory-derivation` in docs/claude-code-contract.md\n"
            "and anthropics/claude-code#54865 for the canonical algorithm reference."
        )
    else:
        click.echo(
            "One or more cases diverged from the predictor. The algorithm has changed,\n"
            "or our prediction is wrong. Update `derive_project_dir()` in\n"
            "`src/maury/empirical_tests.py` and re-run."
        )


@main.command("verify-cc-projects-dir")
@click.option(
    "--test-root",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Directory to create per-case cwds under (default: a fresh temp dir, removed after run).",
)
@click.option(
    "--prompt",
    "claude_prompt",
    type=str,
    default="say only the word: ok",
    show_default=True,
    help="Prompt sent to `claude -p` in each test cwd. Trivial by design.",
)
@click.option(
    "--timeout",
    type=float,
    default=60.0,
    show_default=True,
    help="Seconds per claude invocation.",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["text", "json"]),
    default="text",
    show_default=True,
)
@click.option(
    "--keep-project-dirs",
    is_flag=True,
    help=(
        "Don't clean up the per-case directories the harness created under "
        "`~/.claude/projects/`. Useful for forensic inspection. By default the "
        "harness removes only the dirs it created."
    ),
)
@click.option(
    "--check-only",
    is_flag=True,
    help="Only check whether `claude` is present on PATH; don't run the harness.",
)
def verify_cc_projects_dir(
    test_root: Path | None,
    claude_prompt: str,
    timeout: float,
    output_format: str,
    keep_project_dirs: bool,
    check_only: bool,
) -> None:
    """Verify Claude Code's `~/.claude/projects/<X>/` directory-derivation algorithm.

    Runs `claude -p` in a corpus of test cwds (ASCII, punctuation, non-ASCII,
    emoji, plus a collision pair) and asserts that the directory each creates
    under `~/.claude/projects/` matches `derive_project_dir()`'s prediction.

    Algorithm (empirically verified 2026-05-13 against claude 2.1.140 on
    macOS; cross-referenced against the `fh()` source quoted in
    anthropics/claude-code#54865):

      1. Resolve symlinks (realpath).
      2. Iterate the path as UTF-16 code units (JS regex semantics).
      3. Per unit: keep `[A-Za-z0-9]`; else substitute `-`.

    The algorithm is **non-injective**: distinct cwds can produce the same
    dir name. The harness exercises this via a deliberate collision pair
    (`a-b-c` and `a/b/c`) and verifies they bucket into the same dir.

    Exit code: 0 if all cases match the predictor, 1 otherwise.
    """
    if check_only:
        if claude_present():
            click.echo("✅ `claude` is present on PATH.")
            sys.exit(0)
        else:
            click.echo("❌ `claude` not found on PATH.")
            sys.exit(1)

    report = run_project_dir_harness(
        test_root=test_root,
        claude_prompt=claude_prompt,
        timeout=timeout,
        cleanup_project_dirs=not keep_project_dirs,
    )

    _print_project_dir_report(report, output_format)
    sys.exit(0 if report.passed else 1)


# ---- verify-cc-hook-timing command ---------------------------------------


def _print_hook_timing_report(report: HookTimingHarnessReport, output_format: str) -> None:
    """Pretty-print or JSON-print a HookTimingHarnessReport."""
    if output_format == "json":
        payload = {
            "workspace": str(report.workspace),
            "claude_present": report.claude_present,
            "claude_version": report.claude_version,
            "passed": report.passed,
            "claude_returncode_combined": report.claude_returncode_combined,
            "claude_returncode_timeout": report.claude_returncode_timeout,
            "claude_stderr_excerpt_combined": report.claude_stderr_excerpt_combined,
            "claude_stderr_excerpt_timeout": report.claude_stderr_excerpt_timeout,
            "probes": [
                {
                    "name": p.name,
                    "passed": p.passed,
                    "detail": p.detail,
                    "findings": p.findings,
                    "captured": p.captured,
                }
                for p in report.probes
            ],
        }
        click.echo(json.dumps(payload, indent=2, default=str))
        return

    if not report.claude_present:
        click.echo("❌ `claude` not found on PATH.")
        click.echo("\nThis verifier requires Claude Code installed and authenticated.")
        return

    click.echo(f"claude version: {report.claude_version}")
    click.echo(f"workspace:      {report.workspace}")
    click.echo()
    for p in report.probes:
        mark = "✅" if p.passed else "❌"
        click.echo(f"{mark} {p.name}")
        click.echo(f"   {p.detail}")
        if p.findings:
            for k, v in p.findings.items():
                if k in {
                    "raw_output",
                    "markers_observed",
                    "post_a_markers",
                    "expected_post_a_present",
                    "markers_between_a_start_and_end",
                }:
                    continue  # noisy; shown via --format json
                click.echo(f"   {k}: {v!r}")
        click.echo()

    click.echo(
        "Each probe SUCCEEDS if claude was invoked and probe scripts ran to produce\n"
        "readable output. Interpretation of the observed behavior — whether maury's\n"
        "ADR-0023 assumptions (sequential, sync, short-circuit-on-exit-2) hold — is\n"
        "in the per-probe `findings` map. Maintainer reads findings, decides whether\n"
        "to promote `cc-contract:hook-execution-timing` from ❓ to 🧪.\n"
        "\n"
        "See `cc-contract:hook-execution-timing` in docs/claude-code-contract.md\n"
        "and anthropics/claude-code#57800 for the canonical contradiction this\n"
        "verifier resolves empirically."
    )


@main.command("verify-cc-hook-timing")
@click.option(
    "--workspace",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Persist the probe workspace at this path (default: a fresh temp dir, kept for inspection).",
)
@click.option(
    "--prompt",
    "claude_prompt",
    type=str,
    default="List the files in the current directory.",
    show_default=True,
    help="Prompt to send to `claude -p`. Should reliably trigger at least one PostToolUse fire.",
)
@click.option(
    "--per-probe-timeout",
    type=float,
    default=120.0,
    show_default=True,
    help="Seconds wall-clock cap per claude -p invocation.",
)
@click.option(
    "--timeout-probe-sleep",
    type=int,
    default=30,
    show_default=True,
    help="How long the timeout-probe hook sleeps. Longer = detects larger default timeouts but slower.",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["text", "json"]),
    default="text",
    show_default=True,
)
@click.option(
    "--check-only",
    is_flag=True,
    help="Only check whether `claude` is present on PATH; don't run the harness.",
)
def verify_cc_hook_timing(
    workspace: Path | None,
    claude_prompt: str,
    per_probe_timeout: float,
    timeout_probe_sleep: int,
    output_format: str,
    check_only: bool,
) -> None:
    """Verify Claude Code's hook execution-timing semantics empirically.

    Two probes:

    \b
    1. **Combined ordering / sync / short-circuit.** Four hooks on
       PostToolUse in declared order (A, B, C, D); A brackets a sleep
       so we can detect parallel execution; C exits with code 2 so we
       can detect the documented short-circuit behavior; D's presence
       or absence reveals whether short-circuit is real.
    2. **Default timeout.** One hook that sleeps a configurable
       duration. If claude has a default hook timeout shorter than
       the sleep, the hook is killed mid-sleep.

    Probes SUCCEED if claude was invoked and probe scripts produced
    readable output. The observed behavior (sequential vs. parallel,
    sync vs. async, short-circuit vs. not, default timeout duration)
    is reported as per-probe findings for the contract-doc maintainer
    to interpret.

    Background:

    \b
    - anthropics/claude-code#57800 documents a contradiction between
      the Agent SDK hooks docs ("hooks run in parallel") and the
      hooks guide ("hooks run in order; first failure blocks the
      rest"). This verifier produces empirical ground truth.
    - ADR-0023 (drift attribution via `PostToolUse log_tool_use`)
      depends on knowing the ordering and sync semantics.

    Exit code: 0 if all probes produced usable data, 1 otherwise.
    """
    if check_only:
        if claude_present():
            click.echo("✅ `claude` is present on PATH.")
            sys.exit(0)
        else:
            click.echo("❌ `claude` not found on PATH.")
            sys.exit(1)

    report = run_hook_timing_harness(
        workspace=workspace,
        claude_prompt=claude_prompt,
        per_probe_timeout=per_probe_timeout,
        timeout_probe_hook_sleep=timeout_probe_sleep,
    )

    _print_hook_timing_report(report, output_format)
    sys.exit(0 if report.passed else 1)


# ---- verify-cc-transcript-schema command -------------------------------


def _print_transcript_schema_report(report: TranscriptSchemaHarnessReport, output_format: str) -> None:
    """Pretty-print or JSON-print a TranscriptSchemaHarnessReport.

    Per the verify-cc-hook-timing convention: text mode is for humans
    skimming the result, JSON mode for `--format json` consumers (CI,
    scripts) that want every field.
    """
    if output_format == "json":
        payload = {
            "workspace": str(report.workspace),
            "claude_present": report.claude_present,
            "claude_version": report.claude_version,
            "passed": report.passed,
            "claude_returncode": report.claude_returncode,
            "claude_stderr_excerpt": report.claude_stderr_excerpt,
            "probes": [
                {
                    "jsonl_path": str(p.jsonl_path),
                    "line_count": p.line_count,
                    "parsed_json_count": p.parsed_json_count,
                    "by_type": p.by_type,
                    "content_shapes": p.content_shapes,
                    "user_messages_total": p.user_messages_total,
                    "user_messages_mining_compatible": p.user_messages_mining_compatible,
                    "passed": p.passed,
                    "detail": p.detail,
                    "findings": p.findings,
                    "sample_lines": [
                        {
                            "line_number": s.line_number,
                            "parsed_json": s.parsed_json,
                            "type_value": s.type_value,
                            "type_present": s.type_present,
                            "message_present": s.message_present,
                            "session_id_present": s.session_id_present,
                            "timestamp_present": s.timestamp_present,
                            "message_role_present": s.message_role_present,
                            "message_content_present": s.message_content_present,
                            "content_shape": s.content_shape,
                            "raw_excerpt": s.raw_excerpt,
                        }
                        for s in p.sample_lines
                    ],
                }
                for p in report.probes
            ],
        }
        click.echo(json.dumps(payload, indent=2, default=str))
        return

    if not report.claude_present:
        click.echo("❌ `claude` not found on PATH.")
        click.echo("\nThis verifier requires Claude Code installed and authenticated.")
        return

    click.echo(f"claude version: {report.claude_version}")
    click.echo(f"workspace:      {report.workspace}")
    if report.claude_returncode is not None and report.claude_returncode != 0:
        click.echo(f"⚠️  claude exited with returncode {report.claude_returncode}")
        if report.claude_stderr_excerpt:
            click.echo(f"   stderr (last 400 chars): {report.claude_stderr_excerpt}")
    click.echo()

    if not report.probes:
        click.echo("(no transcript JSONL files were produced — claude may have failed before writing one)")
        return

    for p in report.probes:
        mark = "✅" if p.passed else "❌"
        click.echo(f"{mark} {p.jsonl_path}")
        click.echo(f"   {p.detail}")
        click.echo(f"   by type:        {p.by_type}")
        click.echo(f"   content shapes: {p.content_shapes}")
        click.echo(f"   mining-compatible user messages: {p.user_messages_mining_compatible}/{p.user_messages_total}")
        core_ok = p.findings.get("all_lines_have_core_fields")
        if core_ok is True:
            click.echo("   core fields (type, sessionId, timestamp) on all lines: ✓")
        else:
            click.echo("   core fields (type, sessionId, timestamp) on all lines: ⚠️ missing on some")
        msg_ok = p.findings.get("message_bearing_lines_have_message_fields")
        if msg_ok is True:
            click.echo("   message fields (role+content) on user/assistant lines: ✓")
        else:
            click.echo(
                "   message fields (role+content) on user/assistant lines: "
                "⚠️ missing on some — mining WILL break on next `maury mine` run"
            )
        click.echo()

    click.echo(
        "Each probe SUCCEEDS if claude was invoked and the transcript JSONL\n"
        "file parsed. Whether the observed schema MATCHES maury's mining\n"
        "expectations is in the per-probe `findings` map. Maintainer reads\n"
        "findings, decides whether to promote `cc-contract:transcript-jsonl-\n"
        "stability` from ❓ to 🧪.\n"
        "\n"
        "See `cc-contract:transcript-jsonl-stability` in docs/claude-code-\n"
        "contract.md and anthropics/claude-code#53516, #49400 for the\n"
        "upstream feature requests this verifier supports."
    )


@main.command("verify-cc-transcript-schema")
@click.option(
    "--workspace",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Persist the probe workspace at this path (default: a fresh temp dir, kept for inspection).",
)
@click.option(
    "--prompt",
    "claude_prompt",
    type=str,
    default="Reply with the single word: ok",
    show_default=True,
    help="Prompt to send to `claude -p`. Should reliably trigger at least one assistant response so a JSONL gets written.",
)
@click.option(
    "--timeout",
    type=float,
    default=60.0,
    show_default=True,
    help="Seconds wall-clock cap on the claude -p invocation.",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["text", "json"]),
    default="text",
    show_default=True,
)
@click.option(
    "--check-only",
    is_flag=True,
    help="Only check whether `claude` is present on PATH; don't run the harness.",
)
def verify_cc_transcript_schema(
    workspace: Path | None,
    claude_prompt: str,
    timeout: float,
    output_format: str,
    check_only: bool,
) -> None:
    """Verify Claude Code's transcript JSONL line schema empirically.

    Spawns `claude -p <prompt>` in a fresh test cwd, locates the
    resulting JSONL under `~/.claude/projects/<derived>/`, and
    analyzes each line against the fields maury's mining parser
    consumes (`type`, `message.role`, `message.content`, `sessionId`,
    `timestamp`).

    Probes SUCCEED if claude was invoked and the JSONL file parsed.
    Whether the schema MATCHES maury's mining expectations is reported
    in per-probe findings (`by_type`, `content_shapes`,
    `user_messages_mining_compatible`,
    `all_lines_have_core_fields`,
    `message_bearing_lines_have_message_fields`).

    Background: `cc-contract:transcript-jsonl-stability` is still ❓
    (the third unresolved entry). Two upstream feature requests
    (anthropics/claude-code#53516, #49400) ask Anthropic to publish a
    stable, documented schema. Until they ship docs, this verifier is
    the regression signal — re-run after every Claude Code minor bump
    that might touch transcript output.

    Exit code: 0 if all probes produced usable data, 1 otherwise.
    """
    if check_only:
        if claude_present():
            click.echo("✅ `claude` is present on PATH.")
            sys.exit(0)
        else:
            click.echo("❌ `claude` not found on PATH.")
            sys.exit(1)

    report = run_transcript_schema_harness(
        workspace=workspace,
        claude_prompt=claude_prompt,
        timeout=timeout,
    )

    _print_transcript_schema_report(report, output_format)
    sys.exit(0 if report.passed else 1)


# ---- verify-cc-contract command ----------------------------------------


@main.command("verify-cc-contract")
@click.option(
    "--snapshot-dir",
    "snapshot_dir",
    type=click.Path(file_okay=False, exists=True, path_type=Path),
    default=None,
    help="Snapshot directory to verify against. Defaults to the most-recent dated dir under docs/claude-code-snapshots/.",
)
@click.option(
    "--timeout",
    "timeout",
    type=float,
    default=30.0,
    show_default=True,
    help="Per-URL fetch timeout in seconds.",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["text", "json"]),
    default="text",
    show_default=True,
)
@click.option(
    "--check-only",
    is_flag=True,
    help="Don't fetch; just report which snapshot dir would be verified and which URLs it covers.",
)
def verify_cc_contract(
    snapshot_dir: Path | None,
    timeout: float,
    output_format: str,
    check_only: bool,
) -> None:
    """Diff cited Claude Code docs against `docs/claude-code-snapshots/`.

    Re-fetches every URL listed in the snapshot's MANIFEST.txt and
    compares the bytes' SHA256 against the manifest. Drift signals that
    Anthropic updated a page maury cites — re-snapshot and re-evaluate
    every cc-contract entry that depends on the affected URL.

    Exit code: 0 if every URL matches its snapshot, 1 if any drift, fetch
    error, or missing snapshot file.
    """
    if snapshot_dir is None:
        # Walk up from this file to find the repo root (the dir containing
        # `docs/claude-code-snapshots/`).
        repo_root = Path.cwd()
        snapshot_dir = default_snapshot_dir(repo_root)
        if snapshot_dir is None:
            raise click.ClickException(
                f"No snapshot directory found under {repo_root}/docs/claude-code-snapshots/. "
                "Pass --snapshot-dir explicitly."
            )

    if check_only:
        from maury.cc_contract_verify import parse_manifest

        manifest_path = snapshot_dir / "MANIFEST.txt"
        try:
            entries = parse_manifest(manifest_path)
        except (FileNotFoundError, ValueError) as exc:
            raise click.ClickException(str(exc)) from exc
        click.echo(f"snapshot: {snapshot_dir}")
        click.echo(f"manifest: {manifest_path} ({len(entries)} entries)")
        for entry in entries:
            click.echo(f"  • {entry.name:<25} {entry.url}")
        return

    report = run_contract_verification(snapshot_dir, timeout=timeout)
    if output_format == "json":
        click.echo(format_cc_contract_report_json(report))
    else:
        click.echo(format_cc_contract_report_text(report))
    sys.exit(1 if report.has_drift() else 0)


# ---- sessions group ------------------------------------------------------


@main.group()
def sessions() -> None:
    """Manage the active-sessions.jsonl log."""


@sessions.command("prune")
@click.option(
    "--target",
    "target_dir",
    type=click.Path(file_okay=False, path_type=Path),
    default="~/.claude",
    show_default=True,
    help="Target directory containing maury-state/active-sessions.jsonl.",
)
@click.option(
    "--max-age-hours",
    "max_age_hours",
    type=float,
    default=DEFAULT_MAX_AGE.total_seconds() / 3600.0,
    show_default=True,
    help="Prune sessions whose session_end is older than this many hours.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show what would be pruned without modifying the log.",
)
def sessions_prune(target_dir: Path, max_age_hours: float, dry_run: bool) -> None:
    """Clean up stale session_end entries in active-sessions.jsonl.

    Removes events for sessions whose session_end is older than
    --max-age-hours. Sessions without a session_end are NEVER pruned
    (they may still be running). Per ADR-0025's separation: cleanup is
    opt-in, never a side effect of a safety check.
    """
    from datetime import timedelta

    target_dir = target_dir.expanduser()
    log_path = target_dir / ACTIVE_SESSIONS_REL_PATH
    max_age = timedelta(hours=max_age_hours)

    if not log_path.is_file():
        click.echo(f"clean: no active-sessions.jsonl at {log_path}.")
        return

    result = prune_active_sessions(log_path, max_age=max_age, dry_run=dry_run)

    verb = "would prune" if dry_run else "pruned"
    click.echo(
        f"{verb} {len(result.pruned_session_ids)} session(s); "
        f"kept {len(result.kept_session_ids)}; "
        f"lines {result.lines_in} → {result.lines_out}"
    )
    if result.skipped_unparseable:
        click.echo(f"  (skipped {result.skipped_unparseable} unparseable line(s) — kept verbatim in log)")
    if result.pruned_session_ids:
        for sid in result.pruned_session_ids:
            click.echo(f"  - {sid}")


# ---- uninstall command ----------------------------------------------------


@main.command("uninstall")
@click.option(
    "--target",
    "target_dir",
    type=click.Path(file_okay=False, path_type=Path),
    default="~/.claude",
    show_default=True,
    help="Target directory (typically ~/.claude).",
)
@click.option(
    "--home",
    "home_dir",
    type=click.Path(file_okay=False, path_type=Path),
    default="~",
    show_default=True,
    help="Home directory containing ~/.maury-host-id (override for tests).",
)
@click.option(
    "--yes",
    "skip_confirm",
    is_flag=True,
    help="Skip the confirmation prompt (for scripting).",
)
def uninstall(target_dir: Path, home_dir: Path, skip_confirm: bool) -> None:
    """Strip maury's footprint from this host (per ADR-0023 §8).

    Removes:
      1. maury-managed entries from ~/.claude/settings.json (hooks block).
      2. ~/.claude/bin/maury-* scripts.
      3. ~/.claude/maury-state/ (last-render.json, watermarks, etc).
      4. ~/.maury-host-id.

    Leaves alone:
      - User-authored hooks in settings.json (anything without `# maury-managed`).
      - User content in ~/.claude/CLAUDE.md / rules / skills / agents.
      - Repo clones at the configured repos_root (delete manually if desired).
      - The pip/uv-installed `maury` CLI itself (use `pipx uninstall maury`).
    """
    target_dir = target_dir.expanduser()
    home_dir = home_dir.expanduser()

    if not skip_confirm:
        click.echo(f"This will strip maury's footprint from {target_dir} and remove {home_dir}/.maury-host-id.")
        click.echo("User content (CLAUDE.md, hand-authored hooks, repo clones) will be left intact.")
        if not click.confirm("Proceed?", default=False):
            click.echo("cancelled.")
            sys.exit(0)

    summary = run_uninstall(target_dir=target_dir, home_dir=home_dir)

    click.echo(
        f"removed {summary.settings_hooks_removed} maury-managed hook(s); "
        f"left {summary.settings_hooks_kept} user hook(s) intact"
    )
    if summary.bin_files_removed:
        click.echo(f"deleted {len(summary.bin_files_removed)} maury script(s) from {target_dir}/bin/")
    else:
        click.echo(f"no maury scripts to delete under {target_dir}/bin/")
    if summary.state_dir_removed:
        click.echo(f"deleted {target_dir}/maury-state/")
    if summary.host_id_removed:
        click.echo(f"deleted {home_dir}/.maury-host-id")
    click.echo("repo clones (if any) were not touched; delete them manually if desired.")


# ---- repo group ----------------------------------------------------------


@main.group()
def repo() -> None:
    """Manage individual rules repos (per ADR-0038)."""


@repo.command("init")
@click.option(
    "--target",
    "target_dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=".",
    show_default=True,
    help="Path to the rules repo (will be created if absent).",
)
@click.option(
    "--agency-id",
    "agency_id",
    required=True,
    help="UUID of the agency this rules repo belongs to (or is being created by).",
)
@click.option(
    "--owner",
    "owners",
    multiple=True,
    required=True,
    help="Identity (email/handle) of a PR-reviewing owner. Pass multiple --owner flags for >1.",
)
@click.option(
    "--pr-target",
    "pr_target",
    default=DEFAULT_PR_TARGET,
    show_default=True,
    help="Where to submit PRs (branch name or fork URL).",
)
@click.option(
    "--pr-standards",
    "pr_standards",
    default=None,
    help="Free-form description of PR requirements.",
)
@click.option(
    "--min-reviewers",
    "min_reviewers",
    type=int,
    default=None,
    help="Reviewer-count expectation (informational; not enforced by maury).",
)
@click.option(
    "--initial-tag",
    "initial_tag",
    default=DEFAULT_INITIAL_TAG,
    show_default=True,
    help="Initial semver tag to create. Pass an empty string to skip tagging.",
)
@click.option(
    "--no-git-init",
    "no_git_init",
    is_flag=True,
    help="Don't run `git init`, commit, or tag. Useful for offline scaffolding.",
)
@click.option(
    "--force",
    is_flag=True,
    help="Re-initialize even if .meta/maury-marker.json or .meta/maury-governance.json exist.",
)
def repo_init(
    target_dir: Path,
    agency_id: str,
    owners: tuple[str, ...],
    pr_target: str,
    pr_standards: str | None,
    min_reviewers: int | None,
    initial_tag: str,
    no_git_init: bool,
    force: bool,
) -> None:
    """Initialize a new rules repo per ADR-0038.

    Writes .meta/maury-governance.json (owners, pr_target) and
    .meta/maury-marker.json (layer=rules, agency_id), then creates
    the initial semver tag.

    Idempotent: refuses if either .meta file exists. --force overwrites.
    """
    target_dir = target_dir.expanduser().resolve()
    initial_tag_val: str | None = initial_tag if initial_tag else None
    try:
        summary = init_repo(
            target_dir=target_dir,
            agency_id=agency_id,
            owners=list(owners),
            pr_target=pr_target,
            pr_standards=pr_standards,
            min_reviewers=min_reviewers,
            initial_tag=initial_tag_val,
            force=force,
            git_init=not no_git_init,
        )
    except RepoInitError as e:
        raise click.ClickException(str(e)) from e

    click.echo(f"initialized rules repo against agency {summary.agency_id}")
    click.echo(
        f"  governance: {summary.governance_path} (owners: {', '.join(summary.owners)}; pr_target: {summary.pr_target})"
    )
    click.echo(f"  marker: {summary.marker_path}")
    if summary.initial_tag:
        click.echo(f"  initial tag: {summary.initial_tag}")
    elif summary.tag_already_existed:
        click.echo(f"  initial tag: already present ({initial_tag})")
    elif initial_tag_val is None:
        click.echo("  initial tag: skipped (--initial-tag was empty)")
    if summary.git_commit_sha:
        click.echo(f"  commit: {summary.git_commit_sha[:12]}")
    elif no_git_init:
        click.echo("  git init: skipped (--no-git-init)")
    if summary.force_used:
        click.echo("  ⚠️ --force used: prior governance/marker overwritten.")


# ---- mode group ----------------------------------------------------------
#
# `mode` is the modern vocabulary per ADR-0039; `bootstrap host` is the
# legacy CLI surface that operates against the same manifest. Both work
# today; `mode bootstrap` reuses the same handler.


@main.group()
def mode() -> None:
    """Register and deregister this host's mode (curator-side, ADR-0039)."""


@mode.command("bootstrap")
@click.option(
    "--manifest-file",
    "manifest_file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    envvar=DEFAULT_MANIFEST_ENV,
    help="Manifest file. Defaults to ./.meta/manifest.json or $MAURY_MANIFEST_FILE.",
)
@click.option("--name", "name", required=True, help="Display name for the new host (should match its hostname).")
@click.option(
    "--mode",
    "mode_name",
    required=True,
    help="Mode (name or ID) the new host belongs to. ADR-0039 vocabulary for what was previously `--profile`.",
)
@click.option(
    "--base-url",
    "base_url",
    default=None,
    help="Base repo URL. If omitted, copied from another already-registered host's `base` entry.",
)
@click.option(
    "--base-mode",
    "base_mode",
    type=click.Choice(["ro", "rw", "pr"], case_sensitive=False),
    default="ro",
    show_default=True,
    help="Access mode for the new host's base repo.",
)
@click.option(
    "--push-policy",
    "push_policy",
    type=click.Choice(["permissive", "own_profile_only", "disabled"], case_sensitive=False),
    default="own_profile_only",
    show_default=True,
    help="Push policy for the new host.",
)
@click.option("--owner", "owner", default=None, help="Optional owner identifier (email, handle).")
@click.option("--check", "dry_run", is_flag=True, help="Dry-run: show what would happen, write nothing.")
def mode_bootstrap(
    manifest_file: Path | None,
    name: str,
    mode_name: str,
    base_url: str | None,
    base_mode: str,
    push_policy: str,
    owner: str | None,
    dry_run: bool,
) -> None:
    """Register the current host into a specified mode (ADR-0039).

    Modern-vocabulary form of `maury bootstrap host` — same handler,
    same v2-flat manifest target. The mode-based marker-file equivalent
    will land when ADR-0030's schema-migration machinery ships.
    """
    from maury.manifest import PushPolicy, RepoMode

    mpath = manifest_file or DEFAULT_MANIFEST_PATH
    if not mpath.exists():
        raise click.ClickException(f"manifest file not found: {mpath}")

    try:
        result = run_bootstrap_host(
            manifest_path=mpath,
            name=name,
            profile=mode_name,
            base_url=base_url,
            base_mode=RepoMode(base_mode.lower()),
            push_policy=PushPolicy(push_policy.lower()),
            owner=owner,
            dry_run=dry_run,
        )
    except BootstrapHostError as e:
        raise click.ClickException(str(e)) from e

    for action in result.actions:
        click.echo(f"  {action}")
    click.echo("")
    click.echo(result.message)
    if dry_run:
        click.echo("(--check; no files were written)")


@mode.command("deregister")
@click.option(
    "--manifest-file",
    "manifest_file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    envvar=DEFAULT_MANIFEST_ENV,
    help="Manifest file. Defaults to ./.meta/manifest.json or $MAURY_MANIFEST_FILE.",
)
@click.option(
    "--host",
    "host",
    required=True,
    help="Host name OR `host_<hex>` ID to deregister.",
)
@click.option("--check", "dry_run", is_flag=True, help="Dry-run: show what would happen, write nothing.")
def mode_deregister(
    manifest_file: Path | None,
    host: str,
    dry_run: bool,
) -> None:
    """Retire the named host's mode registration (ADR-0039).

    Mode change is two operations per ADR-0039: deregister, then
    bootstrap. Never a single atomic switch — the two-step form
    keeps the trust boundary explicit.
    """
    mpath = manifest_file or DEFAULT_MANIFEST_PATH
    if not mpath.exists():
        raise click.ClickException(f"manifest file not found: {mpath}")

    try:
        result = run_deregister_host(
            manifest_path=mpath,
            host=host,
            dry_run=dry_run,
        )
    except DeregisterError as e:
        raise click.ClickException(str(e)) from e

    for action in result.actions:
        click.echo(f"  {action}")
    click.echo("")
    click.echo(f"deregistered host {result.name!r} ({result.host_id}) from mode {result.profile_name!r}.")
    if dry_run:
        click.echo("(--check; no files were written)")
