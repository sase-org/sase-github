"""`#gh` ref resolution."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from sase.ace.patch import find_all_patches
from sase.ace.patch.project_spec_path import preferred_project_spec_path
from sase.workspace_provider import ResolvedRef
from sase.workspace_provider.utils import (
    get_default_branch,
    parse_workspace_dir,
    set_workspace_dir,
)
from sase_github.workspace.projects import (
    allocate_canonical_project_name,
    all_project_records,
    ensure_useful_repo_name,
    find_project_record_for_alias,
    find_project_record_for_workspace,
    github_workspace_dir,
    project_file_for,
    projects_base,
)
from sase_github.workspace.remotes import clone_gh_repo

if TYPE_CHECKING:
    from sase.core.project_lifecycle_wire import ProjectRecordWire


def _resolved_ref_for_record(
    record: ProjectRecordWire,
    *,
    read_only: bool = False,
) -> ResolvedRef:
    workspace_dir = record.workspace_dir or parse_workspace_dir(record.project_file)
    if not workspace_dir:
        raise ValueError(
            f"Project '{record.project_name}' is resolved by alias but "
            "WORKSPACE_DIR is not set"
        )
    checkout_target = "origin/main" if read_only else get_default_branch(workspace_dir)
    return ResolvedRef(
        project_file=record.project_file,
        project_name=record.project_name,
        primary_workspace_dir=workspace_dir,
        checkout_target=checkout_target,
        canonical_ref=record.project_name,
    )


def _peek_repo_path_ref(user: str, project: str) -> ResolvedRef | None:
    from sase_github.config import get_default_github_host

    base = projects_base()
    github_host = get_default_github_host()
    primary_workspace_dir = github_workspace_dir(user, project, host=github_host)
    if not os.path.isdir(primary_workspace_dir.rstrip("/")):
        return None

    records = all_project_records(base)
    existing_record = find_project_record_for_workspace(
        records,
        primary_workspace_dir,
    )
    if existing_record is not None:
        return _resolved_ref_for_record(existing_record, read_only=True)

    project_name = allocate_canonical_project_name(user, project, records)
    return ResolvedRef(
        project_file=project_file_for(base, project_name),
        project_name=project_name,
        primary_workspace_dir=primary_workspace_dir,
        checkout_target="origin/main",
        canonical_ref=project_name,
    )


def _resolve_repo_path_ref(user: str, project: str) -> ResolvedRef:
    from sase_github.config import get_default_github_host

    base = projects_base()
    github_host = get_default_github_host()
    primary_workspace_dir = github_workspace_dir(user, project, host=github_host)
    records = all_project_records(base)
    existing_record = find_project_record_for_workspace(
        records,
        primary_workspace_dir,
    )

    if not os.path.isdir(primary_workspace_dir.rstrip("/")):
        clone_gh_repo(user, project, primary_workspace_dir, host=github_host)

    if existing_record is None:
        project_name = allocate_canonical_project_name(user, project, records)
        project_file = project_file_for(base, project_name)
        if not set_workspace_dir(project_file, primary_workspace_dir):
            raise ValueError(f"Failed to write WORKSPACE_DIR for '{project_name}'")
        ensure_useful_repo_name(
            project_name,
            project,
            projects_base=base,
        )
    else:
        project_name = existing_record.project_name
        project_file = existing_record.project_file

    checkout_target = get_default_branch(primary_workspace_dir)

    return ResolvedRef(
        project_file=project_file,
        project_name=project_name,
        primary_workspace_dir=primary_workspace_dir,
        checkout_target=checkout_target,
        canonical_ref=project_name,
    )


def _resolve_existing_named_ref(
    gh_ref: str,
    *,
    read_only: bool,
    strict: bool,
) -> ResolvedRef | None:
    base = projects_base()
    alias_record = find_project_record_for_alias(
        all_project_records(base),
        gh_ref,
    )
    if alias_record is not None:
        return _resolved_ref_for_record(alias_record, read_only=read_only)

    project_dir = base / gh_ref
    project_file_path = Path(preferred_project_spec_path(str(project_dir), gh_ref))
    if project_dir.is_dir() and project_file_path.exists():
        workspace_dir = parse_workspace_dir(str(project_file_path))
        if workspace_dir:
            checkout_target = (
                "origin/main" if read_only else get_default_branch(workspace_dir)
            )
            return ResolvedRef(
                project_file=str(project_file_path),
                project_name=gh_ref,
                primary_workspace_dir=workspace_dir,
                checkout_target=checkout_target,
            )

    for patch in find_all_patches():
        if patch.name != gh_ref:
            continue
        workspace_dir = parse_workspace_dir(patch.file_path)
        if not workspace_dir:
            if strict:
                raise ValueError(
                    f"Patch '{gh_ref}' found in {patch.file_path} "
                    "but WORKSPACE_DIR is not set"
                )
            return None
        return ResolvedRef(
            project_file=patch.file_path,
            project_name=patch.project_basename,
            primary_workspace_dir=workspace_dir,
            checkout_target=f"origin/{gh_ref}",
        )

    return None


def peek_gh_ref(gh_ref: str) -> ResolvedRef | None:
    """Read-only variant of ``resolve_gh_ref``."""
    if "/" in gh_ref:
        parts = gh_ref.strip("/").split("/")
        if len(parts) != 2:
            return None
        return _peek_repo_path_ref(*parts)

    return _resolve_existing_named_ref(gh_ref, read_only=True, strict=False)


def resolve_gh_ref(gh_ref: str) -> ResolvedRef:
    """Resolve a ``#gh`` reference to workspace and branch information.

    Three dispatch modes:

    1. **Repo path** (contains ``/``): ``user/project`` → derive workspace
       from ``~/projects/github/<user>/<project>/``.
    2. **Project shorthand** (no ``/``, matching project dir): look up
       WORKSPACE_DIR from ``~/.sase/projects/<name>/<name>.sase``
       (with legacy ``.gp`` fallback).
    3. **Patch name**: search all Patches for a matching name,
       read WORKSPACE_DIR from its project file.

    Raises:
        ValueError: If the reference cannot be resolved.
    """
    # --- Mode 1: repo path (user/project) ---
    if "/" in gh_ref:
        parts = gh_ref.strip("/").split("/")
        if len(parts) != 2:
            raise ValueError(f"Invalid repo path '{gh_ref}': expected 'user/project'")
        return _resolve_repo_path_ref(*parts)

    resolved = _resolve_existing_named_ref(gh_ref, read_only=False, strict=True)
    if resolved is not None:
        return resolved

    raise ValueError(f"Cannot resolve gh_ref '{gh_ref}'")
