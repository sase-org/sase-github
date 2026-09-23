"""Home-rooted layout and SASE project-record helpers."""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from sase.ace.patch.project_spec_path import preferred_project_spec_path

if TYPE_CHECKING:
    from sase.core.project_lifecycle_wire import ProjectRecordWire


def projects_base() -> Path:
    return Path.home() / ".sase" / "projects"


def github_workspace_dir(user: str, project: str, host: str | None = None) -> str:
    from sase_github.config import DEFAULT_GITHUB_HOST, get_default_github_host

    github_host = host or get_default_github_host()
    base = Path.home() / "projects" / "github"
    if github_host == DEFAULT_GITHUB_HOST:
        return str(base / user / project) + "/"
    return str(base / github_host / user / project) + "/"


def _normalized_workspace_dir(workspace_dir: str | None) -> str | None:
    if not workspace_dir:
        return None
    return os.path.normcase(os.path.normpath(os.path.expanduser(workspace_dir)))


def all_project_records(projects_base: Path) -> list[ProjectRecordWire]:
    if not projects_base.is_dir():
        return []

    from sase.core.project_lifecycle_facade import list_project_records
    from sase.core.project_lifecycle_wire import PROJECT_LIFECYCLE_STATES

    return list_project_records(
        projects_base,
        list(PROJECT_LIFECYCLE_STATES),
        include_home=False,
    )


def enabled_project_records(projects_base: Path) -> list[ProjectRecordWire]:
    if not projects_base.is_dir():
        return []

    from sase.core.project_lifecycle_facade import list_project_records

    return list_project_records(
        projects_base,
        ["enabled"],
        include_home=False,
    )


def canonical_project_owner(project_name: str) -> str | None:
    if not project_name.startswith("gh_"):
        return None
    body = project_name[len("gh_") :]
    owner, separator, repo = body.partition("__")
    if not separator or not owner or not repo:
        return None
    return owner


def find_project_record_for_workspace(
    records: Sequence[ProjectRecordWire],
    workspace_dir: str,
) -> ProjectRecordWire | None:
    expected = _normalized_workspace_dir(workspace_dir)
    for record in records:
        if _normalized_workspace_dir(record.workspace_dir) == expected:
            return record
    return None


def find_project_record_for_alias(
    records: Sequence[ProjectRecordWire],
    alias: str,
) -> ProjectRecordWire | None:
    for record in records:
        if alias == getattr(record, "display_name", None) or alias in record.aliases:
            return record
    return None


def _is_valid_project_name(name: str) -> bool:
    from sase.core.paths import is_valid_sase_project_name

    return is_valid_sase_project_name(name)


def _canonical_project_name_base(user: str, project: str) -> str:
    base = f"gh_{user}__{project}"
    if not _is_valid_project_name(base):
        raise ValueError(
            f"Cannot derive a valid SASE project name for GitHub repo "
            f"'{user}/{project}'"
        )
    return base


def _project_refs(records: Sequence[ProjectRecordWire]) -> set[str]:
    """Return folded project refs occupying the canonical-name namespace.

    Refs compare case-insensitively, so a canonical name is never allocated
    as a case-variant duplicate of an existing key, name, or alias.
    """
    occupied: set[str] = set()
    for record in records:
        occupied.add(record.project_name.casefold())
        if display_name := getattr(record, "display_name", None):
            occupied.add(display_name.casefold())
        occupied.update(alias.casefold() for alias in record.aliases)
    return occupied


def allocate_canonical_project_name(
    user: str,
    project: str,
    records: Sequence[ProjectRecordWire],
) -> str:
    base = _canonical_project_name_base(user, project)
    occupied = _project_refs(records)

    candidate = base
    suffix = 2
    while candidate.casefold() in occupied:
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def project_file_for(projects_base: Path, project_name: str) -> str:
    return preferred_project_spec_path(str(projects_base / project_name), project_name)


def ensure_useful_repo_name(
    project_name: str,
    repo_name: str,
    *,
    projects_base: Path,
) -> None:
    if repo_name == project_name or not _is_valid_project_name(repo_name):
        return

    from sase.project_aliases import (
        allocate_project_name,
        ensure_project_name_locked,
    )

    attempts = 3
    for attempt in range(attempts):
        records = all_project_records(projects_base)
        display_name = allocate_project_name(
            repo_name,
            records,
            project_name=project_name,
        )
        if display_name == project_name:
            return
        try:
            ensure_project_name_locked(
                project_name,
                display_name,
                projects_root=projects_base,
            )
            return
        except ValueError:
            if attempt == attempts - 1:
                raise
