"""Unit tests for `maury.repo_init` (the `maury repo init` core)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from maury.ids import new_host_id
from maury.repo_init import (
    DEFAULT_INITIAL_TAG,
    DEFAULT_PR_TARGET,
    GOVERNANCE_REL_PATH,
    GOVERNANCE_SCHEMA_VERSION,
    MARKER_REL_PATH,
    MARKER_SCHEMA_VERSION,
    RepoInitError,
    init_repo,
)


def _git_available() -> bool:
    try:
        return subprocess.run(["git", "--version"], capture_output=True, check=False).returncode == 0
    except OSError:
        return False


AGENCY_ID = "11111111-2222-3333-4444-555555555555"


# ---- argument validation -------------------------------------------------


def test_init_repo_rejects_empty_agency_id(tmp_path: Path) -> None:
    with pytest.raises(RepoInitError, match="--agency-id"):
        init_repo(tmp_path, agency_id="   ", owners=["alice@example.com"], git_init=False)


def test_init_repo_rejects_empty_owners(tmp_path: Path) -> None:
    with pytest.raises(RepoInitError, match="--owner"):
        init_repo(tmp_path, agency_id=AGENCY_ID, owners=[], git_init=False)


# ---- governance + marker writers -----------------------------------------


def test_init_repo_writes_governance(tmp_path: Path) -> None:
    summary = init_repo(
        tmp_path,
        agency_id=AGENCY_ID,
        owners=["alice@example.com", "bob@example.com"],
        pr_target="trunk",
        pr_standards="all changes require a doc update",
        min_reviewers=2,
        git_init=False,
    )
    body = json.loads((tmp_path / GOVERNANCE_REL_PATH).read_text())
    assert body["schema_version"] == GOVERNANCE_SCHEMA_VERSION
    assert body["owners"] == ["alice@example.com", "bob@example.com"]
    assert body["pr_target"] == "trunk"
    assert body["pr_standards"] == "all changes require a doc update"
    assert body["min_reviewers"] == 2
    assert summary.owners == ("alice@example.com", "bob@example.com")


def test_init_repo_governance_omits_unset_optionals(tmp_path: Path) -> None:
    init_repo(
        tmp_path,
        agency_id=AGENCY_ID,
        owners=["alice@example.com"],
        git_init=False,
    )
    body = json.loads((tmp_path / GOVERNANCE_REL_PATH).read_text())
    assert "pr_standards" not in body
    assert "min_reviewers" not in body
    assert body["pr_target"] == DEFAULT_PR_TARGET


def test_init_repo_writes_marker(tmp_path: Path) -> None:
    init_repo(
        tmp_path,
        agency_id=AGENCY_ID,
        owners=["alice@example.com"],
        git_init=False,
    )
    body = json.loads((tmp_path / MARKER_REL_PATH).read_text())
    assert body["schema_version"] == MARKER_SCHEMA_VERSION
    assert body["layer"] == "rules"
    assert body["agency_id"] == AGENCY_ID
    assert body["sublayers"] == []


# ---- idempotence ---------------------------------------------------------


def test_init_repo_refuses_existing_marker(tmp_path: Path) -> None:
    init_repo(tmp_path, agency_id=AGENCY_ID, owners=["a@b.c"], git_init=False)
    with pytest.raises(RepoInitError, match=r"maury-marker\.json already exists"):
        init_repo(tmp_path, agency_id=AGENCY_ID, owners=["a@b.c"], git_init=False)


def test_init_repo_refuses_existing_governance(tmp_path: Path) -> None:
    """Even if marker absent, an existing governance file triggers refusal."""
    (tmp_path / GOVERNANCE_REL_PATH).parent.mkdir(parents=True)
    (tmp_path / GOVERNANCE_REL_PATH).write_text("{}")
    with pytest.raises(RepoInitError, match=r"maury-governance\.json already exists"):
        init_repo(tmp_path, agency_id=AGENCY_ID, owners=["a@b.c"], git_init=False)


def test_init_repo_force_overwrites(tmp_path: Path) -> None:
    init_repo(tmp_path, agency_id=AGENCY_ID, owners=["a@b.c"], git_init=False)
    new_aid = "ffffffff-ffff-ffff-ffff-ffffffffffff"
    summary = init_repo(tmp_path, agency_id=new_aid, owners=["c@d.e"], force=True, git_init=False)
    assert summary.force_used is True
    marker = json.loads((tmp_path / MARKER_REL_PATH).read_text())
    assert marker["agency_id"] == new_aid
    gov = json.loads((tmp_path / GOVERNANCE_REL_PATH).read_text())
    assert gov["owners"] == ["c@d.e"]


# ---- git behavior --------------------------------------------------------


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_init_repo_creates_tag(tmp_path: Path) -> None:
    summary = init_repo(
        tmp_path,
        agency_id=AGENCY_ID,
        owners=["alice@example.com"],
        git_init=True,
    )
    assert summary.initial_tag == DEFAULT_INITIAL_TAG
    rc = subprocess.run(
        ["git", "rev-parse", f"refs/tags/{DEFAULT_INITIAL_TAG}"],
        cwd=tmp_path,
        capture_output=True,
        check=False,
    )
    assert rc.returncode == 0


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_init_repo_skips_existing_tag(tmp_path: Path) -> None:
    """If the requested tag already exists, leave it alone and flag it."""
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("# preexisting\n")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t.x", "commit", "-m", "init"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(["git", "tag", DEFAULT_INITIAL_TAG], cwd=tmp_path, check=True)
    summary = init_repo(tmp_path, agency_id=AGENCY_ID, owners=["a@b.c"], git_init=True)
    assert summary.initial_tag is None
    assert summary.tag_already_existed is True


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_init_repo_no_tag_when_initial_tag_none(tmp_path: Path) -> None:
    summary = init_repo(
        tmp_path,
        agency_id=AGENCY_ID,
        owners=["a@b.c"],
        initial_tag=None,
        git_init=True,
    )
    assert summary.initial_tag is None
    assert summary.tag_already_existed is False


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_init_repo_commits_meta_files(tmp_path: Path) -> None:
    summary = init_repo(
        tmp_path,
        agency_id=AGENCY_ID,
        owners=["a@b.c"],
        git_init=True,
    )
    assert summary.git_commit_sha is not None
    rc = subprocess.run(
        ["git", "ls-files", str(MARKER_REL_PATH), str(GOVERNANCE_REL_PATH)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert "maury-marker.json" in rc.stdout
    assert "maury-governance.json" in rc.stdout


def test_init_repo_no_git_init_skips_all_git(tmp_path: Path) -> None:
    summary = init_repo(tmp_path, agency_id=AGENCY_ID, owners=["a@b.c"], git_init=False)
    assert summary.git_commit_sha is None
    assert summary.initial_tag is None
    assert not (tmp_path / ".git").exists()


# ---- owner-only access check (ADR-0038 §"Why owner-only") ---------------


def _seed_manifest_with_repo(
    tmp_path: Path,
    *,
    remote_url: str,
    mode: str,
    host_id: str | None = None,
) -> tuple[Path, str]:
    """Write a manifest where the current host has `remote_url` registered
    under the given mode. Also writes ~/.maury-host-id pointing at the
    host's surrogate ID. Returns (manifest_path, host_id).
    """
    import json as _json

    from maury.ids import new_host_id as _new_host_id
    from maury.ids import new_profile_id as _new_profile_id

    if host_id is None:
        host_id = _new_host_id()
    pid = _new_profile_id()
    manifest_path = tmp_path / "manifest.json"
    body: dict[str, object] = {
        "version": 1,
        "profiles": {pid: {"name": "home", "extends": None}},
        "hosts": {
            host_id: {
                "name": "current-host",
                "profile": pid,
                "repos": {
                    "base": {"url": "git@x:o/base.git", "mode": "rw"},
                    "rules-target": {"url": remote_url, "mode": mode},
                },
            }
        },
    }
    manifest_path.write_text(_json.dumps(body))
    return manifest_path, host_id


def _seed_target_dir_with_remote(tmp_path: Path, *, remote_url: str) -> Path:
    """Create a target dir that's a git repo with `origin` pointing at remote_url."""
    target = tmp_path / "target"
    target.mkdir()
    if not _git_available():
        pytest.skip("git not on PATH")
    subprocess.run(["git", "init", "-q"], cwd=target, check=True)
    subprocess.run(["git", "remote", "add", "origin", remote_url], cwd=target, check=True)
    return target


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_init_repo_access_check_passes_when_host_has_rw(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """rw entry → access check passes, init proceeds."""
    remote = "git@github.com:eng-standards/rules-linting.git"
    target = _seed_target_dir_with_remote(tmp_path, remote_url=remote)
    manifest_path, host_id = _seed_manifest_with_repo(tmp_path, remote_url=remote, mode="rw")
    # Fake ~/.maury-host-id by monkeypatching the constant module-level path.
    host_id_file = tmp_path / ".maury-host-id"
    host_id_file.write_text(host_id)
    monkeypatch.setattr("maury.manifest.HOST_ID_FILE", host_id_file)

    summary = init_repo(
        target,
        agency_id=AGENCY_ID,
        owners=["a@b.c"],
        manifest_path=manifest_path,
        check_access=True,
    )
    assert summary.access_check_note is not None
    assert "access OK" in summary.access_check_note


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_init_repo_access_check_refuses_when_host_has_ro(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ro entry → access check refuses with a clear message."""
    remote = "git@github.com:eng-standards/rules-linting.git"
    target = _seed_target_dir_with_remote(tmp_path, remote_url=remote)
    manifest_path, host_id = _seed_manifest_with_repo(tmp_path, remote_url=remote, mode="ro")
    host_id_file = tmp_path / ".maury-host-id"
    host_id_file.write_text(host_id)
    monkeypatch.setattr("maury.manifest.HOST_ID_FILE", host_id_file)

    with pytest.raises(RepoInitError, match="requires `rw`"):
        init_repo(
            target,
            agency_id=AGENCY_ID,
            owners=["a@b.c"],
            manifest_path=manifest_path,
            check_access=True,
        )


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_init_repo_access_check_refuses_when_remote_not_in_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Remote URL not in this host's repos → refuse."""
    target = _seed_target_dir_with_remote(tmp_path, remote_url="git@x:o/unregistered.git")
    manifest_path, host_id = _seed_manifest_with_repo(tmp_path, remote_url="git@x:o/different.git", mode="rw")
    host_id_file = tmp_path / ".maury-host-id"
    host_id_file.write_text(host_id)
    monkeypatch.setattr("maury.manifest.HOST_ID_FILE", host_id_file)

    with pytest.raises(RepoInitError, match="not in this host's manifest"):
        init_repo(
            target,
            agency_id=AGENCY_ID,
            owners=["a@b.c"],
            manifest_path=manifest_path,
            check_access=True,
        )


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_init_repo_access_check_refuses_when_host_not_registered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No host registration → refuse."""
    remote = "git@github.com:eng-standards/rules-linting.git"
    target = _seed_target_dir_with_remote(tmp_path, remote_url=remote)
    manifest_path, _ = _seed_manifest_with_repo(tmp_path, remote_url=remote, mode="rw", host_id=new_host_id())
    # Host-id file points at a DIFFERENT host than what's in the manifest.
    host_id_file = tmp_path / ".maury-host-id"
    host_id_file.write_text(new_host_id())
    monkeypatch.setattr("maury.manifest.HOST_ID_FILE", host_id_file)

    with pytest.raises(RepoInitError, match="current host not registered"):
        init_repo(
            target,
            agency_id=AGENCY_ID,
            owners=["a@b.c"],
            manifest_path=manifest_path,
            check_access=True,
        )


def test_init_repo_access_check_skipped_without_manifest_path(tmp_path: Path) -> None:
    """No --manifest-file → skip with informational note, init proceeds."""
    summary = init_repo(
        tmp_path,
        agency_id=AGENCY_ID,
        owners=["a@b.c"],
        git_init=False,
        manifest_path=None,
        check_access=True,
    )
    assert summary.access_check_note is not None
    assert "no --manifest-file" in summary.access_check_note


def test_init_repo_access_check_skipped_when_disabled(tmp_path: Path) -> None:
    """check_access=False explicitly skips even when a manifest path is provided."""
    manifest_path, _host_id = _seed_manifest_with_repo(tmp_path, remote_url="git@x:o/r.git", mode="ro")
    summary = init_repo(
        tmp_path,
        agency_id=AGENCY_ID,
        owners=["a@b.c"],
        git_init=False,
        manifest_path=manifest_path,
        check_access=False,
    )
    assert summary.access_check_note is not None
    assert "--no-check-access" in summary.access_check_note


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_init_repo_access_check_skips_when_no_origin_remote(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No `origin` remote → skip with note, init proceeds (offline scaffolding)."""
    target = tmp_path / "target"
    target.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=target, check=True)
    manifest_path, host_id = _seed_manifest_with_repo(tmp_path, remote_url="git@x:o/something.git", mode="rw")
    host_id_file = tmp_path / ".maury-host-id"
    host_id_file.write_text(host_id)
    monkeypatch.setattr("maury.manifest.HOST_ID_FILE", host_id_file)

    summary = init_repo(
        target,
        agency_id=AGENCY_ID,
        owners=["a@b.c"],
        manifest_path=manifest_path,
        check_access=True,
    )
    assert summary.access_check_note is not None
    assert "no origin remote" in summary.access_check_note
