"""Command-line entry point for maury."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from maury import __version__
from maury.capability import dumps as capabilities_dumps
from maury.capability import run_probe
from maury.doctor import Report, render_json, render_text, run_all
from maury.ids import short as short_id
from maury.manifest import (
    ManifestError,
    dump_manifest,
    known_profile_names_from,
    known_profiles_from,
    load_manifest,
    validate_manifest,
)
from maury.render import RenderError, apply_render, render
from maury.rules import (
    RuleParseError,
    classify_fragment,
    load_rules,
    render_trace,
    validate_rules,
)

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


@main.command()
def status() -> None:
    """Show reachable repos and sync state."""
    raise click.ClickException("not yet implemented")


@main.command()
def sync() -> None:
    """Pull all reachable repos, render, and apply with confirmation."""
    raise click.ClickException("not yet implemented")


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
