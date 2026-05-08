"""Command-line entry point for maury."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from maury import __version__
from maury.bootstrap import InitError
from maury.bootstrap import init as run_init
from maury.capability import dumps as capabilities_dumps
from maury.capability import run_probe
from maury.doctor import Report, render_json, render_text, run_all
from maury.drift import DriftEntry, detect_drift, read_last_render
from maury.empirical_tests import HarnessReport, claude_present
from maury.empirical_tests import run as run_empirical
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
from maury.rules import (
    RuleParseError,
    classify_fragment,
    load_rules,
    render_trace,
    validate_rules,
)
from maury.sync import DRIFT_SCAN_DIRS, RepoSyncResult, SyncError
from maury.sync import sync as run_sync

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


# ---- manifest subgroup ---------------------------------------------------


@main.group()
def manifest() -> None:
    """Inspect and validate the manifest (profiles + hosts + repos registry)."""


@manifest.command("validate")
@click.option(
    "--manifest-file",
    "manifest_file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    envvar=DEFAULT_MANIFEST_ENV,
)
def manifest_validate(manifest_file: Path | None) -> None:
    """Parse the manifest and cross-check profile/host references."""
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


# ---- init (the user's first command on a new host, per ADR-0018) -------


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
    default=str(Path.home() / ".claude"),
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
def init_cmd(
    from_dir: Path | None,
    from_tarball: Path | None,
    target_dir: Path,
    dry_run: bool,
    force: bool,
    non_interactive: bool,
) -> None:
    """Initialize maury on a new host (first-run bootstrap)."""
    if from_dir is None and from_tarball is None:
        raise click.ClickException(
            "provide one of --from-dir <path> or --from-tarball <path>. "
            "(Future: --from-url <git-url>; not yet implemented.)"
        )
    if from_dir is not None and from_tarball is not None:
        raise click.ClickException("--from-dir and --from-tarball are mutually exclusive")
    if force and non_interactive:
        raise click.ClickException("--force and --non-interactive are mutually exclusive.")

    drift_mode = "force" if force else ("non-interactive" if non_interactive else "default")

    try:
        result = run_init(
            source_dir=from_dir,
            source_tarball=from_tarball,
            target_dir=target_dir,
            dry_run=dry_run,
            drift_mode=drift_mode,
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


@main.command()
def status() -> None:
    """Show reachable repos and sync state."""
    raise click.ClickException("not yet implemented")


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
    default=str(Path.home() / ".claude"),
    show_default=True,
    help="Target directory whose drift to reconcile.",
)
@click.option(
    "--repos-root",
    "repos_root",
    type=click.Path(file_okay=False, path_type=Path),
    default=str(Path.home() / ".config" / "maury" / "repos"),
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
    mpath = manifest_file or DEFAULT_MANIFEST_PATH
    if not mpath.exists():
        raise click.ClickException(f"manifest file not found: {mpath}")

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
    default=str(Path.home() / ".claude"),
    show_default=True,
    help="Target directory where rendered files would be written.",
)
@click.option(
    "--repos-root",
    "repos_root",
    type=click.Path(file_okay=False, path_type=Path),
    default=str(DEFAULT_REPOS_ROOT),
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
def sync_cmd(
    manifest_file: Path | None,
    target_dir: Path,
    repos_root: Path,
    check: bool,
    force: bool,
    non_interactive: bool,
) -> None:
    """Pull all reachable repos, render, and apply.

    v0: assumes the host's manifest entry declares a repo named 'base';
    multi-repo profile composition and push of pending proposals are
    deferred to subsequent slices. Drift detection per ADR-0017's three
    flows is wired in (--check / --non-interactive / --force / default).
    """
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
    default=str(Path.home() / ".claude"),
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
    show_default=True,
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
    show_default=True,
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
    # Pick the project if not specified.
    target_project_dir: Path
    if project_name:
        target_project_dir = projects_dir / project_name
        if not target_project_dir.is_dir():
            raise click.ClickException(f"project directory not found: {target_project_dir}")
    else:
        target_project_dir = _pick_busiest_project(projects_dir)
        click.echo(f"selected project (busiest): {target_project_dir.name}")

    # Walk + filter messages.
    msgs = list(walk_user_messages(target_project_dir))
    click.echo(f"signal-bearing user messages after noise filter: {len(msgs)}")
    if not msgs:
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
    show_default=True,
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
    """Evaluate a CLAUDE.md against Anthropic's best-practices rubric.

    Rubric source: https://code.claude.com/docs/en/best-practices
    """
    text = claude_md.read_text(encoding="utf-8")
    findings = run_all(text, source_path=str(claude_md))

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
