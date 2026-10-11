"""Prompt completion: `gh repo list` candidates and local owner namespaces."""

from __future__ import annotations

import json
import subprocess
from typing import Any, Literal

from sase.workspace_provider import (
    VcsNamespaceEntry,
    VcsRefNamespaces,
    VcsRepoCandidates,
    VcsRepoEntry,
)
from sase_github.workspace.gh_cli import (
    DEFAULT_GH_TIMEOUT_SECONDS,
    looks_like_auth_error,
    looks_like_network_error,
    looks_like_not_found_error,
    non_interactive_gh_env,
)
from sase_github.workspace.projects import (
    canonical_project_owner,
    enabled_project_records,
    projects_base,
)

_DEFAULT_REPO_COMPLETION_LIMIT = 200

_REPO_LIST_JSON_FIELDS = (
    "name,description,visibility,isArchived,isFork,pushedAt,id,viewerPermission,isEmpty"
)
_VcsRepoErrorKind = Literal[
    "auth",
    "network",
    "not_found",
    "tool_missing",
    "unsupported_namespace",
    "unknown",
]


def _repo_completion_limit() -> int:
    try:
        from sase.config import load_merged_config

        config = load_merged_config()
    except Exception:
        return _DEFAULT_REPO_COMPLETION_LIMIT

    section = config.get("vcs_repo_completion", {}) if isinstance(config, dict) else {}
    if not isinstance(section, dict):
        return _DEFAULT_REPO_COMPLETION_LIMIT
    value = section.get("max_repos")
    if isinstance(value, bool) or not isinstance(value, int):
        return _DEFAULT_REPO_COMPLETION_LIMIT
    return max(value, 1)


def list_github_repo_candidates(namespace: str) -> VcsRepoCandidates:
    from sase_github.config import get_default_github_host

    host = get_default_github_host()
    env = non_interactive_gh_env()
    env["GH_HOST"] = host
    limit = _repo_completion_limit()
    command = [
        "gh",
        "repo",
        "list",
        namespace,
        "--json",
        _REPO_LIST_JSON_FIELDS,
        "--limit",
        str(limit),
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=DEFAULT_GH_TIMEOUT_SECONDS,
            env=env,
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return repo_candidates_error("tool_missing", "install the gh CLI", host=host)
    except subprocess.TimeoutExpired:
        return repo_candidates_error(
            "network", "repo listing failed - network error", host=host
        )
    except OSError:
        return repo_candidates_error("tool_missing", "install the gh CLI", host=host)

    if result.returncode != 0:
        return _classify_gh_repo_list_error(result, host)

    try:
        entries = _repo_entries_from_gh_json(result.stdout, namespace)
    except ValueError:
        return repo_candidates_error(
            "unknown",
            "repo listing failed - unexpected gh output",
        )
    return VcsRepoCandidates(
        status="ok",
        provider_display="GitHub",
        entries=entries,
        host=host,
        truncated=len(entries) >= limit,
    )


def _repo_entries_from_gh_json(raw: str, namespace: str) -> tuple[VcsRepoEntry, ...]:
    try:
        data = json.loads(raw or "[]")
    except json.JSONDecodeError as e:
        raise ValueError("invalid gh JSON") from e
    if not isinstance(data, list):
        raise ValueError("expected gh JSON list")

    entries: list[VcsRepoEntry] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        name = _string_field(item, "name")
        if not name:
            continue
        raw_empty = item.get("isEmpty")
        entries.append(
            VcsRepoEntry(
                name=name,
                ref=f"{namespace}/{name}",
                description=_string_field(item, "description"),
                visibility=_string_field(item, "visibility").lower(),
                is_fork=bool(item.get("isFork")),
                is_archived=bool(item.get("isArchived")),
                pushed_at=_optional_string_field(item, "pushedAt"),
                repo_id=_string_field(item, "id"),
                viewer_permission=_string_field(item, "viewerPermission"),
                is_empty=bool(raw_empty) if isinstance(raw_empty, bool) else None,
            )
        )
    return tuple(entries)


def _string_field(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    return value if isinstance(value, str) else ""


def _optional_string_field(data: dict[str, Any], key: str) -> str | None:
    value = data.get(key)
    return value if isinstance(value, str) and value else None


def _classify_gh_repo_list_error(
    result: subprocess.CompletedProcess[str],
    host: str,
) -> VcsRepoCandidates:
    output = "\n".join(part for part in (result.stderr, result.stdout) if part).strip()
    normalized = output.casefold()
    if looks_like_auth_error(normalized):
        return repo_candidates_error(
            "auth",
            "run 'gh auth login'",
            host=host,
            remedy_argv=("gh", "auth", "login", "-h", host),
        )
    if looks_like_not_found_error(normalized):
        return repo_candidates_error(
            "not_found", "GitHub owner was not found", host=host
        )
    if looks_like_network_error(normalized):
        return repo_candidates_error(
            "network", "repo listing failed - network error", host=host
        )
    message = output.splitlines()[0] if output else "repo listing failed"
    return repo_candidates_error("unknown", message, host=host)


def repo_candidates_error(
    error_kind: _VcsRepoErrorKind,
    message: str,
    *,
    host: str = "",
    remedy_argv: tuple[str, ...] = (),
) -> VcsRepoCandidates:
    return VcsRepoCandidates(
        status="error",
        error_kind=error_kind,
        message=message,
        provider_display="GitHub",
        entries=(),
        host=host,
        remedy_argv=remedy_argv,
    )


def _pluralize_project_count(count: int) -> str:
    noun = "project" if count == 1 else "projects"
    return f"{count} enabled {noun}"


def list_github_ref_namespaces() -> VcsRefNamespaces:
    from sase_github.config import get_github_orgs
    from sase_github.workspace.onboarding import read_gh_account

    account = read_gh_account()

    owner_counts: dict[str, int] = {}
    owner_names: dict[str, str] = {}
    for record in enabled_project_records(projects_base()):
        owner = canonical_project_owner(record.project_name)
        if owner is None:
            continue
        key = owner.casefold()
        owner_counts[key] = owner_counts.get(key, 0) + 1
        owner_names.setdefault(key, owner)

    descriptions: dict[str, str] = {
        key: _pluralize_project_count(count) for key, count in owner_counts.items()
    }

    for org in get_github_orgs():
        name = org.strip()
        if not name:
            continue
        key = name.casefold()
        owner_names.setdefault(key, name)
        descriptions.setdefault(key, "from github_orgs")

    account_key = account.casefold() if account else ""
    if account_key and account_key not in owner_names:
        owner_names[account_key] = account
        descriptions[account_key] = "your repositories"

    entries = tuple(
        VcsNamespaceEntry(
            name=owner_names[key],
            description=descriptions[key],
            kind_label="you" if account_key and key == account_key else "org",
        )
        for key in sorted(owner_names, key=lambda item: owner_names[item].casefold())
    )
    return VcsRefNamespaces(entries=entries, account=account)
