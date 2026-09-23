"""SDD hook flows: preflight, create-or-verify, and materialize."""

from __future__ import annotations

import os
import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path

from sase.workspace_provider import SddSidecarPreflight
from sase_github.workspace.remotes import (
    clone_gh_repo,
    github_ssh_url,
    read_github_origin,
    read_git_origin,
    remote_matches_repo,
)
from sase_github.workspace.sdd_repo import (
    SddRepoProbe,
    create_github_sdd_repo,
    ensure_github_sdd_label,
    probe_github_repo_detail,
    sdd_sidecar_suffix,
    sidecar_sdd_candidates,
)

_SDD_STORE_SCHEMA_VERSION = 1


def preflight_sdd_sidecar(
    primary_workspace_dir: str, options: dict[str, object]
) -> SddSidecarPreflight | None:
    """Authoritatively discover a sidecar without mutating state."""
    origin = read_github_origin(primary_workspace_dir)
    if origin is None:
        return None
    suffix = sdd_sidecar_suffix(options)

    exact_target = _sdd_repo_target_from_options(options, default_host=origin.host)
    if exact_target is not None:
        host, owner, repo, _remote_url = exact_target
        repo_full_name = f"{owner}/{repo}"
        probe, message = probe_github_repo_detail(host, repo_full_name)
    else:
        candidate = _discover_sidecar_sdd_repo_for_create(
            origin.host,
            sidecar_sdd_candidates(origin.owner, origin.repo, suffix=suffix),
        )
        if candidate is None:
            return SddSidecarPreflight(
                status="unavailable",
                provider="GitHub",
                host=origin.host,
                repo=f"{origin.owner}/{origin.repo}--{suffix}",
                visibility="public",
                message="GitHub sidecar discovery returned no result",
            )
        owner, repo, probe, message = candidate
        host = origin.host
        repo_full_name = f"{owner}/{repo}"

    return SddSidecarPreflight(
        status=probe,
        provider="GitHub",
        host=host,
        repo=repo_full_name,
        visibility="public",
        message=message or "",
    )


def create_sdd_remote(
    primary_workspace_dir: str, options: dict[str, object]
) -> dict[str, object] | None:
    """Verify or create a GitHub sidecar SDD repository."""
    origin = read_github_origin(primary_workspace_dir)
    if origin is None:
        return None
    suffix = sdd_sidecar_suffix(options)

    exact_target = _sdd_repo_target_from_options(options, default_host=origin.host)
    if exact_target is not None:
        host, owner, repo, remote_url = exact_target
        repo_full_name = f"{owner}/{repo}"
        probe, unavailable_message = probe_github_repo_detail(host, repo_full_name)
        if probe == "found":
            ensure_github_sdd_label(host, repo_full_name)
            return _sdd_store_record(
                host,
                repo_full_name,
                remote_url,
                discovery="found",
                created=False,
            )
        if probe == "not_found":
            _require_sdd_creation_authorization(options, repo_full_name)
            created = create_github_sdd_repo(
                host,
                repo_full_name,
                source_repo_full_name=f"{origin.owner}/{origin.repo}",
                sidecar_suffix=suffix,
            )
            ensure_github_sdd_label(host, repo_full_name)
            return _sdd_store_record(
                host,
                repo_full_name,
                remote_url,
                discovery="found",
                created=created,
            )
        if unavailable_message:
            raise RuntimeError(unavailable_message)
        return None

    candidate = _discover_sidecar_sdd_repo_for_create(
        origin.host,
        sidecar_sdd_candidates(origin.owner, origin.repo, suffix=suffix),
    )
    if candidate is None:
        return None
    owner, repo, probe, unavailable_message = candidate
    repo_full_name = f"{owner}/{repo}"
    remote_url = github_ssh_url(origin.host, owner, repo)
    if probe == "found":
        ensure_github_sdd_label(origin.host, repo_full_name)
        return _sdd_store_record(
            origin.host,
            repo_full_name,
            remote_url,
            discovery="found",
            created=False,
        )
    if probe == "not_found":
        _require_sdd_creation_authorization(options, repo_full_name)
        created = create_github_sdd_repo(
            origin.host,
            repo_full_name,
            source_repo_full_name=f"{origin.owner}/{origin.repo}",
            sidecar_suffix=suffix,
        )
        ensure_github_sdd_label(origin.host, repo_full_name)
        return _sdd_store_record(
            origin.host,
            repo_full_name,
            remote_url,
            discovery="found",
            created=created,
        )
    if unavailable_message:
        raise RuntimeError(unavailable_message)
    return None


def materialize_sdd_store(
    primary_workspace_dir: str, options: dict[str, object]
) -> dict[str, object] | None:
    """Find or create, label, and stage a GitHub sidecar repository."""
    origin = read_github_origin(primary_workspace_dir)
    if origin is None:
        return None

    record = create_sdd_remote(primary_workspace_dir, options)
    if record is None:
        return None
    if record.get("discovery") == "not_found":
        raise RuntimeError(
            "GitHub provider did not create the mandatory sidecar SDD repository"
        )

    repo_full_name = str(record.get("repo") or "")
    parts = repo_full_name.split("/", 1)
    if len(parts) != 2 or not all(parts):
        raise RuntimeError("GitHub provider returned an invalid SDD repository name")
    owner, repo = parts
    host = str(record.get("host") or origin.host)
    target_option = options.get("staging_dir")
    sdd_dir = (
        Path(str(target_option)).expanduser()
        if isinstance(target_option, str) and target_option
        else Path(primary_workspace_dir).expanduser() / ".sase" / "sdd"
    )
    existing_remote = read_git_origin(sdd_dir) if (sdd_dir / ".git").is_dir() else None
    if existing_remote and remote_matches_repo(existing_remote, host, owner, repo):
        record["remote_url"] = existing_remote
        return record
    if _path_has_content(sdd_dir):
        if target_option:
            raise RuntimeError(
                f"SDD materialization staging path is not empty: {sdd_dir}"
            )
        # Core owns lossless reconciliation for a legacy primary path.
        return record

    cloned_remote_url = _clone_sdd_repo(owner, repo, sdd_dir, host=host)
    record["remote_url"] = cloned_remote_url
    record["discovery"] = "found"
    return record


def _sdd_repo_target_from_options(
    options: Mapping[str, object],
    *,
    default_host: str,
) -> tuple[str, str, str, str] | None:
    raw_repo = options.get("sdd_repo")
    if not isinstance(raw_repo, str) or not raw_repo.strip():
        return None

    parts = [part for part in raw_repo.strip().strip("/").split("/") if part]
    if len(parts) != 2:
        raise RuntimeError(
            "materialized SDD store record has invalid GitHub repo metadata"
        )

    raw_host = options.get("sdd_host")
    host = (
        raw_host.strip()
        if isinstance(raw_host, str) and raw_host.strip()
        else default_host
    )
    raw_remote_url = options.get("sdd_remote_url")
    configured_remote = (
        raw_remote_url.strip()
        if isinstance(raw_remote_url, str) and raw_remote_url.strip()
        else None
    )
    remote_url = (
        configured_remote
        if configured_remote is not None
        and remote_matches_repo(configured_remote, host, parts[0], parts[1])
        else github_ssh_url(host, parts[0], parts[1])
    )
    return host, parts[0], parts[1], remote_url


def _discover_sidecar_sdd_repo_for_create(
    host: str,
    candidates: Sequence[tuple[str, str]],
) -> tuple[str, str, SddRepoProbe, str | None] | None:
    primary = candidates[0]
    for owner, repo in candidates:
        repo_full_name = f"{owner}/{repo}"
        probe, unavailable_message = probe_github_repo_detail(host, repo_full_name)
        if probe == "found":
            return owner, repo, probe, None
        if probe != "not_found":
            return owner, repo, probe, unavailable_message
    return primary[0], primary[1], "not_found", None


def _require_sdd_creation_authorization(
    options: Mapping[str, object],
    repo_full_name: str,
) -> None:
    """Fail closed unless core explicitly authorizes remote creation."""
    if options.get("create") is True and options.get("sdd_creation_authorized") is True:
        return
    raise RuntimeError(
        f"creation of GitHub SDD sidecar repository {repo_full_name} was not "
        "authorized; rerun `sase sdd init` and answer y/yes to its repository "
        "creation prompt"
    )


def _sdd_store_record(
    host: str,
    repo_full_name: str,
    remote_url: str,
    *,
    discovery: str,
    created: bool | None = None,
) -> dict[str, object]:
    record: dict[str, object] = {
        "schema_version": _SDD_STORE_SCHEMA_VERSION,
        "storage": "separate_repo",
        "provider": "github",
        "host": host,
        "repo": repo_full_name,
        "remote_url": remote_url,
        "discovery": discovery,
    }
    if created is not None:
        record["created"] = created
    return record


def _path_has_content(path: Path) -> bool:
    try:
        next(path.iterdir())
    except FileNotFoundError:
        return False
    except NotADirectoryError:
        return True
    except StopIteration:
        return False
    return True


def _clone_sdd_repo(user: str, project: str, target_dir: Path, *, host: str) -> str:
    parent = target_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    temp_dir = parent / f".{target_dir.name}.clone-tmp-{os.getpid()}"
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
    try:
        remote_url = clone_gh_repo(user, project, str(temp_dir), host=host)
        if target_dir.exists():
            target_dir.rmdir()
        temp_dir.replace(target_dir)
        return remote_url
    except Exception:
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
        raise
