"""GitHub workspace provider plugin for sase.

Implements the ``sase_workspace`` pluggy hooks for GitHub-hosted projects,
handling workflow detection, reference resolution, change labels, and
PR-based submission.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from sase.ace.changespec.project_spec_path import preferred_project_spec_path
from sase.workspace_provider import (
    ResolvedRef,
    SddCompanionPreflight,
    VcsNamespaceEntry,
    VcsRefNamespaces,
    VcsRepoCandidates,
    VcsRepoEntry,
    WorkflowMetadata,
    hookimpl,
)
from sase.workspace_provider.utils import (
    get_default_branch,
    non_interactive_git_env,
    parse_workspace_dir,
    set_workspace_dir,
)

if TYPE_CHECKING:
    from sase.core.project_lifecycle_wire import ProjectRecordWire
    from sase_github.config import GitHubRemote

_PR_URL_RE = re.compile(r"https?://[^/]+/.+?/pull/(\d+)")
_HOSTED_URL_RE = re.compile(r"https?://[^/]+/")
_GH_REPO_LIST_TIMEOUT_SECONDS = 10
_SDD_STORE_SCHEMA_VERSION = 1
_SDD_COMPANION_LABEL = "sase--sdd"
_SDD_COMPANION_LABEL_DESCRIPTION = "SASE SDD companion repository"
_SDD_COMPANION_LABEL_COLOR = "0e8a16"
_DEFAULT_REPO_COMPLETION_LIMIT = 200
_VcsRepoErrorKind = Literal[
    "auth",
    "network",
    "not_found",
    "tool_missing",
    "unsupported_namespace",
    "unknown",
]


class GitHubWorkspacePlugin:
    """Workspace provider plugin for GitHub-hosted projects."""

    # ── Hook implementations ────────────────────────────────────────

    @hookimpl
    def ws_get_workflow_metadata(self) -> WorkflowMetadata | None:
        return WorkflowMetadata(
            workflow_type="gh",
            ref_pattern=r"(?:^|(?<=\s))#gh(?:[_:]([a-zA-Z0-9_./-]+)|\(([^)]+)\))",
            display_name="GitHub",
            pre_allocated_env_prefix="SASE_GH",
            vcs_family="git",
            vcs_provider_name="github",
            sdd_storage_policy="separate_repo",
        )

    @hookimpl
    def ws_detect_workflow_type(self, project_file: str) -> str | None:
        """Return ``'gh'`` if the project is GitHub-hosted, else ``None``."""
        workspace_dir = parse_workspace_dir(project_file)
        if not workspace_dir or not os.path.isdir(os.path.join(workspace_dir, ".git")):
            return None

        from sase.workspace_provider.utils import parse_bare_repo_dir

        if parse_bare_repo_dir(project_file):
            return None  # bare-git plugin handles this

        try:
            result = subprocess.run(
                ["git", "config", "--get", "remote.origin.url"],
                cwd=workspace_dir,
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0:
                url = result.stdout.strip()
                if url and not url.startswith(
                    ("http://", "https://", "git@", "ssh://")
                ):
                    return None  # local path → bare-git
        except Exception:
            pass

        return "gh"

    @hookimpl
    def ws_get_change_label(self, project_file: str) -> str | None:
        """Return ``'PR'`` for GitHub projects."""
        if self.ws_detect_workflow_type(project_file=project_file) == "gh":
            return "PR"
        return None

    @hookimpl
    def ws_resolve_ref(self, ref: str, workflow_type: str) -> ResolvedRef | None:
        """Resolve a ``#gh`` reference to workspace and branch information."""
        if workflow_type != "gh":
            return None
        r = resolve_gh_ref(ref)
        return ResolvedRef(
            project_file=r.project_file,
            project_name=r.project_name,
            primary_workspace_dir=r.primary_workspace_dir,
            checkout_target=r.checkout_target,
            canonical_ref=r.canonical_ref,
        )

    @hookimpl
    def ws_peek_ref(self, ref: str, workflow_type: str) -> ResolvedRef | None:
        """Read-only ``#gh`` lookup for presentation paths."""
        if workflow_type != "gh":
            return None
        return peek_gh_ref(ref)

    @hookimpl
    def ws_list_repo_candidates(
        self, workflow_type: str, namespace: str
    ) -> VcsRepoCandidates | None:
        """List GitHub repositories for prompt completion."""
        if workflow_type != "gh":
            return None
        owner = namespace.strip()
        if not owner or "/" in owner:
            return _repo_candidates_error(
                "unsupported_namespace",
                "GitHub repo completion supports a single owner or organization.",
            )
        return _list_github_repo_candidates(owner)

    @hookimpl
    def ws_list_ref_namespaces(self, workflow_type: str) -> VcsRefNamespaces | None:
        """List locally-known GitHub owners for root ref completion."""
        if workflow_type != "gh":
            return None
        return _list_github_ref_namespaces()

    @hookimpl
    def ws_extract_change_identifier(self, pr_url: str) -> tuple[str, str] | None:
        """Extract PR number from a GitHub PR URL."""
        match = _PR_URL_RE.match(pr_url)
        if match:
            return (match.group(1), "git")
        return None

    @hookimpl
    def ws_generate_submitted_check_script(
        self, identifier: str, vcs_type: str
    ) -> str | None:
        """Generate script to check if a GitHub PR is merged or closed."""
        if vcs_type != "git":
            return None
        return (
            f"state=$(gh pr view {identifier} --json state -q '.state' 2>/dev/null)\n"
            'echo "PR state: ${state:-<unavailable>}"\n'
            'case "$state" in\n'
            "  MERGED) true ;;\n"
            "  # Keep this literal in sync with SUBMITTED_CHECK_EXIT_CODE_CLOSED.\n"
            "  CLOSED) (exit 20) ;;\n"
            "  *) false ;;\n"
            "esac"
        )

    @hookimpl
    def ws_supports_reviewer_comments(self, pr_url: str) -> bool | None:
        """GitHub does not support reviewer comments via critique_comments."""
        if _HOSTED_URL_RE.match(pr_url):
            return False
        return None

    @hookimpl
    def ws_get_workspace_directory(
        self,
        workflow_type: str,
        workspace_num: int,
        project_name: str,
        primary_workspace_dir: str,
    ) -> str | None:
        if workflow_type != "gh":
            return None
        from sase.workspace_provider.utils import ensure_workspace_checkout

        return ensure_workspace_checkout(primary_workspace_dir, workspace_num)

    @hookimpl
    def ws_materialize_sdd_store(
        self,
        primary_workspace_dir: str,
        workspace_dir: str,
        options: dict[str, object],
    ) -> dict[str, object] | None:
        """Find or create, label, and stage a GitHub companion repository."""
        origin = _read_github_origin(primary_workspace_dir)
        if origin is None:
            return None

        create_options = dict(options)
        create_options["create"] = True
        record = self.ws_create_sdd_remote(
            primary_workspace_dir,
            workspace_dir,
            create_options,
        )
        if record is None:
            return None
        if record.get("discovery") == "not_found":
            raise RuntimeError(
                "GitHub provider did not create the mandatory companion SDD repository"
            )

        repo_full_name = str(record.get("repo") or "")
        parts = repo_full_name.split("/", 1)
        if len(parts) != 2 or not all(parts):
            raise RuntimeError(
                "GitHub provider returned an invalid SDD repository name"
            )
        owner, repo = parts
        host = str(record.get("host") or origin.host)
        target_option = options.get("staging_dir")
        sdd_dir = (
            Path(str(target_option)).expanduser()
            if isinstance(target_option, str) and target_option
            else Path(primary_workspace_dir).expanduser() / ".sase" / "sdd"
        )
        existing_remote = (
            _read_git_origin(sdd_dir) if (sdd_dir / ".git").is_dir() else None
        )
        if existing_remote and _remote_matches_repo(existing_remote, host, owner, repo):
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

    @hookimpl
    def ws_preflight_sdd_companion(
        self,
        primary_workspace_dir: str,
        workspace_dir: str,
        options: dict[str, object],
    ) -> SddCompanionPreflight | None:
        """Authoritatively discover a companion without mutating state."""
        del workspace_dir
        origin = _read_github_origin(primary_workspace_dir)
        if origin is None:
            return None
        suffix = _sdd_companion_suffix(options)

        exact_target = _sdd_repo_target_from_options(options, default_host=origin.host)
        if exact_target is not None:
            host, owner, repo, _remote_url = exact_target
            repo_full_name = f"{owner}/{repo}"
            probe, message = _probe_github_repo_detail(host, repo_full_name)
        else:
            candidate = _discover_companion_sdd_repo_for_create(
                origin.host,
                _companion_sdd_candidates(origin.owner, origin.repo, suffix=suffix),
            )
            if candidate is None:
                return SddCompanionPreflight(
                    status="unavailable",
                    provider="GitHub",
                    host=origin.host,
                    repo=f"{origin.owner}/{origin.repo}--{suffix}",
                    visibility="public",
                    message="GitHub companion discovery returned no result",
                )
            owner, repo, probe, message = candidate
            host = origin.host
            repo_full_name = f"{owner}/{repo}"

        return SddCompanionPreflight(
            status=probe,
            provider="GitHub",
            host=host,
            repo=repo_full_name,
            visibility="public",
            message=message or "",
        )

    @hookimpl
    def ws_create_sdd_remote(
        self,
        primary_workspace_dir: str,
        workspace_dir: str,
        options: dict[str, object],
    ) -> dict[str, object] | None:
        """Verify or create a GitHub companion SDD repository."""
        origin = _read_github_origin(primary_workspace_dir)
        if origin is None:
            return None
        suffix = _sdd_companion_suffix(options)

        exact_target = _sdd_repo_target_from_options(options, default_host=origin.host)
        if exact_target is not None:
            host, owner, repo, remote_url = exact_target
            repo_full_name = f"{owner}/{repo}"
            probe, unavailable_message = _probe_github_repo_detail(host, repo_full_name)
            if probe == "found":
                _ensure_github_sdd_label(host, repo_full_name)
                return _sdd_store_record(
                    host,
                    repo_full_name,
                    remote_url,
                    discovery="found",
                    created=False,
                )
            if probe == "not_found":
                _require_sdd_creation_authorization(options, repo_full_name)
                created = _create_github_sdd_repo(
                    host,
                    repo_full_name,
                    source_repo_full_name=f"{origin.owner}/{origin.repo}",
                    companion_suffix=suffix,
                )
                _ensure_github_sdd_label(host, repo_full_name)
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

        candidate = _discover_companion_sdd_repo_for_create(
            origin.host,
            _companion_sdd_candidates(origin.owner, origin.repo, suffix=suffix),
        )
        if candidate is None:
            return None
        owner, repo, probe, unavailable_message = candidate
        repo_full_name = f"{owner}/{repo}"
        remote_url = _github_ssh_url(origin.host, owner, repo)
        if probe == "found":
            _ensure_github_sdd_label(origin.host, repo_full_name)
            return _sdd_store_record(
                origin.host,
                repo_full_name,
                remote_url,
                discovery="found",
                created=False,
            )
        if probe == "not_found":
            _require_sdd_creation_authorization(options, repo_full_name)
            created = _create_github_sdd_repo(
                origin.host,
                repo_full_name,
                source_repo_full_name=f"{origin.owner}/{origin.repo}",
                companion_suffix=suffix,
            )
            _ensure_github_sdd_label(origin.host, repo_full_name)
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

    @hookimpl
    def ws_prepare_mail(
        self,
        changespec_name: str,
        changespec_parent: str | None,
        project_basename: str,
        project_file: str,
        target_dir: str,
        console: object | None,
    ) -> object | None:
        if self.ws_detect_workflow_type(project_file=project_file) != "gh":
            return None
        return _prepare_mail_git(changespec_name, project_basename, target_dir, console)

    @hookimpl
    def ws_format_commit_description(
        self,
        file_path: str,
        project: str,
        workflow_type: str,
        bug: str | None,
        fixed_bug: str | None,
    ) -> bool | None:
        if workflow_type != "gh":
            return None
        with open(file_path, encoding="utf-8") as f:
            content = f.read()
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(f"[{project}] {content}\n")
        return True

    @hookimpl
    def ws_submit(
        self,
        changespec_file: str,
        changespec_name: str,
        project_basename: str,
        console: object | None = None,
    ) -> tuple[bool, str | None] | None:
        """Submit a GitHub ChangeSpec by merging its PR."""
        from sase.workspace_provider import detect_workflow_type

        vcs_type = detect_workflow_type(changespec_file)
        if vcs_type != "gh":
            return None

        from rich.console import Console as RichConsole
        from rich.markup import escape as escape_markup

        from sase.ace.changespec import ChangeSpec, find_all_changespecs
        from sase.ace.hooks.processes import (
            kill_and_persist_all_running_processes,
        )
        from sase.ace.operations import has_active_children
        from sase.running_field import (
            claim_workspace,
            get_first_available_axe_workspace,
            get_workspace_directory_for_num,
            release_workspace,
        )
        from sase.vcs_provider import get_vcs_provider

        rich_console: RichConsole | None = (
            console if isinstance(console, RichConsole) else None
        )

        # Find the ChangeSpec object
        changespec: ChangeSpec | None = None
        for cs in find_all_changespecs():
            if cs.name == changespec_name:
                changespec = cs
                break
        if changespec is None:
            return (False, f"ChangeSpec '{changespec_name}' not found")

        log_fn = (
            (lambda msg: rich_console.print(f"[cyan]{escape_markup(msg)}[/cyan]"))
            if rich_console
            else None
        )
        kill_and_persist_all_running_processes(
            changespec,
            changespec_file,
            changespec_name,
            "Killed hook running on submitted PR.",
            log_fn=log_fn,
        )

        all_changespecs = find_all_changespecs()
        if has_active_children(
            changespec,
            all_changespecs,
            terminal_statuses=("Submitted", "Reverted", "Archived"),
        ):
            return (
                False,
                "Cannot submit: other ChangeSpecs have this one as their "
                "parent and are not Submitted, Reverted, or Archived",
            )

        workspace_dir = parse_workspace_dir(changespec_file)
        if not workspace_dir:
            return (False, "WORKSPACE_DIR is not set for this project")

        workspace_num = get_first_available_axe_workspace(changespec_file)
        workflow_name = f"submit-{changespec_name}"
        pid = os.getpid()

        try:
            ws_dir, _ = get_workspace_directory_for_num(workspace_num, project_basename)
        except RuntimeError as e:
            return (False, f"Failed to get workspace directory: {e}")

        if rich_console:
            rich_console.print(f"[cyan]Claiming workspace #{workspace_num}[/cyan]")

        if not claim_workspace(
            changespec_file,
            workspace_num,
            workflow_name,
            pid,
            changespec_name,
        ):
            return (
                False,
                f"Failed to claim workspace #{workspace_num}",
            )

        try:
            if rich_console:
                rich_console.print(
                    f"[cyan]Checking out {escape_markup(changespec_name)}...[/cyan]"
                )

            provider = get_vcs_provider(ws_dir)
            branch_name = provider.resolve_revision(
                changespec_name, project_basename, ws_dir
            )
            success, error = provider.checkout(branch_name, ws_dir)
            if not success:
                return (False, f"Failed to checkout branch: {error}")

            default_branch_ref = get_default_branch(ws_dir)
            default_branch = default_branch_ref.rsplit("/", 1)[-1]

            if rich_console:
                rich_console.print(
                    f"[cyan]Merging {escape_markup(changespec_name)} into "
                    f"{escape_markup(default_branch)}...[/cyan]"
                )

            # Prefer the recorded PR URL/number when available — this is
            # resilient to branch renames (e.g. suffix strip/append).
            pr_number = _extract_pr_number(changespec.pr_url)
            if pr_number:
                pr_state = _check_pr_state(pr_number, ws_dir)
                if pr_state == "OPEN":
                    return _submit_via_pr_merge(
                        changespec, ws_dir, rich_console, pr_number=pr_number
                    )
                elif pr_state == "CLOSED":
                    return (
                        False,
                        f"PR #{pr_number} (from ChangeSpec PR field) is closed "
                        "and unmerged. Reopen it or create a new PR with #pr.",
                    )
                elif pr_state == "MERGED":
                    return (
                        False,
                        f"PR #{pr_number} (from ChangeSpec PR field) is already "
                        "merged.",
                    )
                # pr_state is None — fall through to branch-based check

            # Fallback: check for a PR on the current branch
            has_pr = _check_existing_pr(ws_dir)
            if has_pr:
                return _submit_via_pr_merge(changespec, ws_dir, rich_console)
            return (
                False,
                "GitHub project has no PR for this branch. Create a PR first with #pr.",
            )
        finally:
            release_workspace(
                changespec_file,
                workspace_num,
                workflow_name,
                changespec_name,
            )
            if rich_console:
                rich_console.print(f"[cyan]Released workspace #{workspace_num}[/cyan]")


# ── Private helpers ─────────────────────────────────────────────────


def _read_github_origin(workspace_dir: str) -> GitHubRemote | None:
    from sase_github.config import get_github_hosts, parse_github_remote_url

    origin = _read_git_origin(Path(workspace_dir).expanduser())
    parsed = parse_github_remote_url(origin)
    if parsed is None:
        return None
    if parsed.host not in get_github_hosts():
        return None
    return parsed


def _read_git_origin(cwd: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "config", "--get", "remote.origin.url"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
            env=non_interactive_git_env(),
            stdin=subprocess.DEVNULL,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _companion_sdd_candidates(
    owner: str, repo: str, *, suffix: str = "sdd"
) -> list[tuple[str, str]]:
    """Return companion candidates, preserving the legacy ``sdd`` override."""

    suffix = _validate_sdd_companion_suffix(suffix)
    if suffix != "sdd":
        return [(owner, f"{repo}--{suffix}")]

    from sase_github.config import get_sdd_repo_name_override

    override = get_sdd_repo_name_override()
    if override is None:
        return [(owner, f"{repo}--sdd")]

    parts = [part for part in override.strip("/").split("/") if part]
    if len(parts) == 1:
        return [(owner, parts[0])]
    if len(parts) == 2:
        return [(parts[0], parts[1])]
    raise ValueError("sdd.repo.name must be a repo name or owner/repo")


def _companion_sdd_repo(owner: str, repo: str) -> tuple[str, str]:
    return _companion_sdd_candidates(owner, repo)[0]


def _sdd_companion_suffix(options: Mapping[str, object]) -> str:
    raw = options.get("sdd_companion_suffix", options.get("companion_suffix", "sdd"))
    if not isinstance(raw, str):
        raise RuntimeError("SDD companion suffix must be a string")
    return _validate_sdd_companion_suffix(raw)


def _validate_sdd_companion_suffix(suffix: str) -> str:
    normalized = suffix.strip().removeprefix("--")
    if not normalized or re.fullmatch(r"[a-z0-9][a-z0-9-]*", normalized) is None:
        raise RuntimeError(f"invalid SDD companion suffix: {suffix!r}")
    return normalized


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
    remote_url = (
        raw_remote_url.strip()
        if isinstance(raw_remote_url, str) and raw_remote_url.strip()
        else _github_ssh_url(host, parts[0], parts[1])
    )
    return host, parts[0], parts[1], remote_url


_SddRepoProbe = Literal["found", "not_found", "unavailable"]


def _discover_companion_sdd_repo(
    host: str,
    candidates: Sequence[tuple[str, str]],
) -> tuple[str, str, _SddRepoProbe] | None:
    primary = candidates[0]
    for owner, repo in candidates:
        repo_full_name = f"{owner}/{repo}"
        probe = _probe_github_repo(host, repo_full_name)
        if probe == "found":
            return owner, repo, probe
        if probe != "not_found":
            return None
    return primary[0], primary[1], "not_found"


def _discover_companion_sdd_repo_for_create(
    host: str,
    candidates: Sequence[tuple[str, str]],
) -> tuple[str, str, _SddRepoProbe, str | None] | None:
    primary = candidates[0]
    for owner, repo in candidates:
        repo_full_name = f"{owner}/{repo}"
        probe, unavailable_message = _probe_github_repo_detail(host, repo_full_name)
        if probe == "found":
            return owner, repo, probe, None
        if probe != "not_found":
            return owner, repo, probe, unavailable_message
    return primary[0], primary[1], "not_found", None


def _probe_github_repo(host: str, repo_full_name: str) -> _SddRepoProbe:
    probe, _message = _probe_github_repo_detail(host, repo_full_name)
    return probe


def _probe_github_repo_detail(
    host: str, repo_full_name: str
) -> tuple[_SddRepoProbe, str | None]:
    env = _non_interactive_gh_env()
    env["GH_HOST"] = host
    try:
        result = subprocess.run(
            [
                "gh",
                "repo",
                "view",
                repo_full_name,
                "--json",
                "name,isArchived",
                "-q",
                "[.name, .isArchived] | @tsv",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=_sdd_network_timeout(),
            env=env,
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return (
            "unavailable",
            "gh not found. Install the GitHub CLI, then re-run `sase sdd init`.",
        )
    except subprocess.TimeoutExpired:
        return (
            "unavailable",
            f"could not reach {host}. Check your connection, then re-run "
            "`sase sdd init`.",
        )
    except OSError as exc:
        return (
            "unavailable",
            f"could not run gh repo view for {repo_full_name}: {exc}",
        )

    if result.returncode == 0:
        fields = result.stdout.strip().split("\t")
        if len(fields) >= 2 and fields[1].casefold() == "true":
            return (
                "unavailable",
                f"{repo_full_name} is archived and read-only; if this project "
                "migrated to split companions run `sase sdd init`, otherwise "
                "unarchive the repo",
            )
        if fields and fields[0]:
            return "found", None
        return (
            "unavailable",
            f"could not verify GitHub repository {repo_full_name}: empty response",
        )

    output = "\n".join(part for part in (result.stderr, result.stdout) if part).strip()
    normalized = output.casefold()
    if _looks_like_not_found_error(normalized):
        return "not_found", None
    if _looks_like_auth_error(normalized):
        return (
            "unavailable",
            "GitHub CLI is not authenticated. Run `gh auth login`, then "
            "re-run `sase sdd init`.",
        )
    if _looks_like_network_error(normalized):
        return (
            "unavailable",
            f"could not reach {host}. Check your connection, then re-run "
            "`sase sdd init`.",
        )
    detail = output.splitlines()[0] if output else "unknown gh repo view failure"
    return (
        "unavailable",
        f"could not verify GitHub repository {repo_full_name}: {detail}",
    )


def _create_github_sdd_repo(
    host: str,
    repo_full_name: str,
    *,
    source_repo_full_name: str,
    companion_suffix: str = "sdd",
) -> bool:
    env = _non_interactive_gh_env()
    env["GH_HOST"] = host
    try:
        result = subprocess.run(
            [
                "gh",
                "repo",
                "create",
                repo_full_name,
                "--public",
                "--description",
                _sdd_companion_description(
                    source_repo_full_name, companion_suffix=companion_suffix
                ),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=_sdd_network_timeout(),
            env=env,
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "gh not found. Install the GitHub CLI, then re-run `sase sdd init`."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"could not reach {host}. Check your connection, then re-run "
            "`sase sdd init`."
        ) from exc
    except OSError as exc:
        raise RuntimeError(f"gh repo create failed: {exc}") from exc

    if result.returncode == 0:
        return True

    if result.returncode != 0:
        detail = "\n".join(part for part in (result.stderr, result.stdout) if part)
        detail = detail.strip()
        normalized = detail.casefold()
        if _looks_like_already_exists_error(normalized) and (
            _probe_github_repo(host, repo_full_name) == "found"
        ):
            return False
        message = f"gh repo create failed for {repo_full_name}"
        if detail:
            message += f": {detail}"
        owner = repo_full_name.split("/", 1)[0]
        message += f". You may lack repo-create rights in {owner}."
        raise RuntimeError(message)

    return True


def _sdd_companion_description(
    source_repo_full_name: str, *, companion_suffix: str
) -> str:
    suffix = _validate_sdd_companion_suffix(companion_suffix)
    if suffix == "sdd":
        return f"SDD companion repository for {source_repo_full_name}"
    return f"SASE {suffix} companion repository for {source_repo_full_name}"


def _require_sdd_creation_authorization(
    options: Mapping[str, object],
    repo_full_name: str,
) -> None:
    """Enforce guarded explicit-init authorization when core supplies it."""
    if "sdd_creation_authorized" not in options:
        return
    if options.get("sdd_creation_authorized") is True:
        return
    raise RuntimeError(
        f"creation of GitHub SDD companion repository {repo_full_name} was not "
        "authorized; rerun `sase sdd init` and answer y/yes to its repository "
        "creation prompt"
    )


def _ensure_github_sdd_label(host: str, repo_full_name: str) -> None:
    """Best-effort creation of the companion marker label.

    The label is informational repository metadata; tokens without
    label-management rights must still be able to complete companion
    init, so failures surface as warnings instead of aborting.
    """
    try:
        _create_github_sdd_label(host, repo_full_name)
    except RuntimeError as exc:
        print(f"warning: {exc}", file=sys.stderr)


def _create_github_sdd_label(host: str, repo_full_name: str) -> None:
    env = _non_interactive_gh_env()
    env["GH_HOST"] = host
    try:
        result = subprocess.run(
            [
                "gh",
                "label",
                "create",
                _SDD_COMPANION_LABEL,
                "--repo",
                repo_full_name,
                "--description",
                _SDD_COMPANION_LABEL_DESCRIPTION,
                "--color",
                _SDD_COMPANION_LABEL_COLOR,
                "--force",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=_sdd_network_timeout(),
            env=env,
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "gh not found. Install the GitHub CLI, then re-run `sase sdd init`."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"could not reach {host}. Check your connection, then re-run "
            "`sase sdd init`."
        ) from exc
    except OSError as exc:
        raise RuntimeError(
            f"could not run gh label create for {repo_full_name}: {exc}"
        ) from exc

    if result.returncode == 0:
        return

    detail = "\n".join(part for part in (result.stderr, result.stdout) if part).strip()
    normalized = detail.casefold()
    if _looks_like_auth_error(normalized):
        raise RuntimeError(
            "GitHub CLI is not authenticated. Run `gh auth login`, then "
            "re-run `sase sdd init`."
        )
    if _looks_like_network_error(normalized):
        raise RuntimeError(
            f"could not reach {host}. Check your connection, then re-run "
            "`sase sdd init`."
        )

    message = (
        f"could not create or update GitHub label {_SDD_COMPANION_LABEL} on "
        f"{repo_full_name}"
    )
    if detail:
        message += f": {detail}"
    message += (
        ". Check that your GitHub token has write or label-management "
        "permissions, then re-run `sase sdd init`."
    )
    raise RuntimeError(message)


def _sdd_network_timeout() -> float:
    try:
        from sase.sdd._commit import network_git_timeout

        return network_git_timeout()
    except Exception:
        return float(_GH_REPO_LIST_TIMEOUT_SECONDS)


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


def _remote_matches_repo(
    remote_url: str,
    host: str,
    owner: str,
    repo: str,
) -> bool:
    from sase_github.config import parse_github_remote_url

    parsed = parse_github_remote_url(remote_url)
    if parsed is None:
        return False
    return (
        parsed.host == host
        and parsed.owner.casefold() == owner.casefold()
        and parsed.repo.casefold() == repo.casefold()
    )


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
        remote_url = _clone_gh_repo(user, project, str(temp_dir), host=host)
        if target_dir.exists():
            target_dir.rmdir()
        temp_dir.replace(target_dir)
        return remote_url
    except Exception:
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def _clone_gh_repo(
    user: str,
    project: str,
    target_dir: str,
    *,
    host: str | None = None,
) -> str:
    """Clone a GitHub repo to the target directory."""
    from sase_github.config import get_default_github_host

    github_host = host or get_default_github_host()
    parent = os.path.dirname(target_dir.rstrip("/"))
    os.makedirs(parent, exist_ok=True)

    attempts = (
        ("SSH", _github_ssh_url(github_host, user, project)),
        ("HTTPS", _github_https_url(github_host, user, project)),
    )
    failures: list[str] = []
    first_error: BaseException | None = None
    for label, url in attempts:
        try:
            subprocess.run(
                ["git", "clone", url, target_dir.rstrip("/")],
                capture_output=True,
                text=True,
                check=True,
                env=non_interactive_git_env(),
                stdin=subprocess.DEVNULL,
            )
            return url
        except subprocess.CalledProcessError as e:
            if first_error is None:
                first_error = e
            failures.append(_format_clone_failure(label, url, e))
        except FileNotFoundError as e:
            raise RuntimeError("git clone failed: git command not found") from e

    detail = "\n".join(failures)
    error_msg = f"git clone failed for {user}/{project}"
    if detail:
        error_msg += f":\n{detail}"
    raise RuntimeError(error_msg) from first_error


def _github_ssh_url(host: str, user: str, project: str) -> str:
    if ":" in host:
        return f"ssh://git@{host}/{user}/{project}.git"
    return f"git@{host}:{user}/{project}.git"


def _github_https_url(host: str, user: str, project: str) -> str:
    return f"https://{host}/{user}/{project}.git"


def _format_clone_failure(
    label: str,
    url: str,
    exc: subprocess.CalledProcessError,
) -> str:
    output = "\n".join(part for part in (exc.stderr, exc.stdout) if part).strip()
    message = f"{label} clone from {url} failed (exit code {exc.returncode})"
    if output:
        message += f": {output}"
    return message


def _projects_base() -> Path:
    return Path.home() / ".sase" / "projects"


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


def _list_github_repo_candidates(namespace: str) -> VcsRepoCandidates:
    from sase_github.config import get_default_github_host

    host = get_default_github_host()
    env = _non_interactive_gh_env()
    env["GH_HOST"] = host
    command = [
        "gh",
        "repo",
        "list",
        namespace,
        "--json",
        "name,description,visibility,isArchived,isFork,pushedAt",
        "--limit",
        str(_repo_completion_limit()),
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=_GH_REPO_LIST_TIMEOUT_SECONDS,
            env=env,
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return _repo_candidates_error("tool_missing", "install the gh CLI")
    except subprocess.TimeoutExpired:
        return _repo_candidates_error("network", "repo listing failed - network error")
    except OSError:
        return _repo_candidates_error("tool_missing", "install the gh CLI")

    if result.returncode != 0:
        return _classify_gh_repo_list_error(result)

    try:
        entries = _repo_entries_from_gh_json(result.stdout, namespace)
    except ValueError:
        return _repo_candidates_error(
            "unknown",
            "repo listing failed - unexpected gh output",
        )
    return VcsRepoCandidates(
        status="ok",
        provider_display="GitHub",
        entries=entries,
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
        entries.append(
            VcsRepoEntry(
                name=name,
                ref=f"{namespace}/{name}",
                description=_string_field(item, "description"),
                visibility=_string_field(item, "visibility").lower(),
                is_fork=bool(item.get("isFork")),
                is_archived=bool(item.get("isArchived")),
                pushed_at=_optional_string_field(item, "pushedAt"),
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
) -> VcsRepoCandidates:
    output = "\n".join(part for part in (result.stderr, result.stdout) if part).strip()
    normalized = output.casefold()
    if _looks_like_auth_error(normalized):
        return _repo_candidates_error("auth", "run 'gh auth login'")
    if _looks_like_not_found_error(normalized):
        return _repo_candidates_error("not_found", "GitHub owner was not found")
    if _looks_like_network_error(normalized):
        return _repo_candidates_error("network", "repo listing failed - network error")
    message = output.splitlines()[0] if output else "repo listing failed"
    return _repo_candidates_error("unknown", message)


def _looks_like_auth_error(text: str) -> bool:
    # HTTP 403 is deliberately absent: gh reports it for valid tokens
    # that lack permission (e.g. fine-grained PATs), where `gh auth
    # login` is the wrong remedy.
    markers = (
        "auth login",
        "authentication required",
        "not logged in",
        "requires authentication",
        "bad credentials",
        "http 401",
        "status code 401",
    )
    return any(marker in text for marker in markers)


def _looks_like_not_found_error(text: str) -> bool:
    markers = (
        "could not resolve to a user",
        "could not resolve to an organization",
        "could not resolve to a repository",
        "not found",
        "http 404",
        "status code 404",
    )
    return any(marker in text for marker in markers)


def _looks_like_already_exists_error(text: str) -> bool:
    markers = (
        "already exists",
        "already taken",
        "name already exists",
        "name is already taken",
    )
    return any(marker in text for marker in markers)


def _looks_like_network_error(text: str) -> bool:
    markers = (
        "could not resolve host",
        "failed to connect",
        "connection refused",
        "connection reset",
        "i/o timeout",
        "network",
        "no such host",
        "temporary failure",
        "tls handshake timeout",
        "timeout",
    )
    return any(marker in text for marker in markers)


def _repo_candidates_error(
    error_kind: _VcsRepoErrorKind,
    message: str,
) -> VcsRepoCandidates:
    return VcsRepoCandidates(
        status="error",
        error_kind=error_kind,
        message=message,
        provider_display="GitHub",
        entries=(),
    )


def _github_workspace_dir(user: str, project: str, host: str | None = None) -> str:
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


def _list_project_records(projects_base: Path) -> list[ProjectRecordWire]:
    if not projects_base.is_dir():
        return []

    from sase.core.project_lifecycle_facade import list_project_records
    from sase.core.project_lifecycle_wire import PROJECT_LIFECYCLE_STATES

    return list_project_records(
        projects_base,
        list(PROJECT_LIFECYCLE_STATES),
        include_home=False,
    )


def _list_active_project_records(projects_base: Path) -> list[ProjectRecordWire]:
    if not projects_base.is_dir():
        return []

    from sase.core.project_lifecycle_facade import list_project_records

    return list_project_records(
        projects_base,
        ["active"],
        include_home=False,
    )


def _canonical_project_owner(project_name: str) -> str | None:
    if not project_name.startswith("gh_"):
        return None
    body = project_name[len("gh_") :]
    owner, separator, repo = body.partition("__")
    if not separator or not owner or not repo:
        return None
    return owner


def _pluralize_project_count(count: int) -> str:
    noun = "project" if count == 1 else "projects"
    return f"{count} active {noun}"


def _list_github_ref_namespaces() -> VcsRefNamespaces:
    from sase_github.config import get_github_orgs

    owner_counts: dict[str, int] = {}
    owner_names: dict[str, str] = {}
    for record in _list_active_project_records(_projects_base()):
        owner = _canonical_project_owner(record.project_name)
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

    entries = tuple(
        VcsNamespaceEntry(
            name=owner_names[key],
            description=descriptions[key],
            kind_label="org",
        )
        for key in sorted(owner_names, key=lambda item: owner_names[item].casefold())
    )
    return VcsRefNamespaces(entries=entries)


def _find_project_record_for_workspace(
    records: Sequence[ProjectRecordWire],
    workspace_dir: str,
) -> ProjectRecordWire | None:
    expected = _normalized_workspace_dir(workspace_dir)
    for record in records:
        if _normalized_workspace_dir(record.workspace_dir) == expected:
            return record
    return None


def _find_project_record_for_alias(
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
    occupied: set[str] = set()
    for record in records:
        occupied.add(record.project_name)
        if display_name := getattr(record, "display_name", None):
            occupied.add(display_name)
        occupied.update(record.aliases)
    return occupied


def _allocate_canonical_project_name(
    user: str,
    project: str,
    records: Sequence[ProjectRecordWire],
) -> str:
    base = _canonical_project_name_base(user, project)
    occupied = _project_refs(records)

    candidate = base
    suffix = 2
    while candidate in occupied:
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def _project_file_for(projects_base: Path, project_name: str) -> str:
    return preferred_project_spec_path(str(projects_base / project_name), project_name)


def _ensure_useful_repo_name(
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
        records = _list_project_records(projects_base)
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

    projects_base = _projects_base()
    github_host = get_default_github_host()
    primary_workspace_dir = _github_workspace_dir(user, project, host=github_host)
    if not os.path.isdir(primary_workspace_dir.rstrip("/")):
        return None

    records = _list_project_records(projects_base)
    existing_record = _find_project_record_for_workspace(
        records,
        primary_workspace_dir,
    )
    if existing_record is not None:
        return _resolved_ref_for_record(existing_record, read_only=True)

    project_name = _allocate_canonical_project_name(user, project, records)
    return ResolvedRef(
        project_file=_project_file_for(projects_base, project_name),
        project_name=project_name,
        primary_workspace_dir=primary_workspace_dir,
        checkout_target="origin/main",
        canonical_ref=project_name,
    )


def _resolve_repo_path_ref(user: str, project: str) -> ResolvedRef:
    from sase_github.config import get_default_github_host

    projects_base = _projects_base()
    github_host = get_default_github_host()
    primary_workspace_dir = _github_workspace_dir(user, project, host=github_host)
    records = _list_project_records(projects_base)
    existing_record = _find_project_record_for_workspace(
        records,
        primary_workspace_dir,
    )

    if not os.path.isdir(primary_workspace_dir.rstrip("/")):
        _clone_gh_repo(user, project, primary_workspace_dir, host=github_host)

    if existing_record is None:
        project_name = _allocate_canonical_project_name(user, project, records)
        project_file = _project_file_for(projects_base, project_name)
        if not set_workspace_dir(project_file, primary_workspace_dir):
            raise ValueError(f"Failed to write WORKSPACE_DIR for '{project_name}'")
        _ensure_useful_repo_name(
            project_name,
            project,
            projects_base=projects_base,
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
    from sase.ace.changespec import find_all_changespecs

    projects_base = _projects_base()
    alias_record = _find_project_record_for_alias(
        _list_project_records(projects_base),
        gh_ref,
    )
    if alias_record is not None:
        return _resolved_ref_for_record(alias_record, read_only=read_only)

    project_dir = projects_base / gh_ref
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

    for cs in find_all_changespecs():
        if cs.name != gh_ref:
            continue
        workspace_dir = parse_workspace_dir(cs.file_path)
        if not workspace_dir:
            if strict:
                raise ValueError(
                    f"ChangeSpec '{gh_ref}' found in {cs.file_path} "
                    "but WORKSPACE_DIR is not set"
                )
            return None
        return ResolvedRef(
            project_file=cs.file_path,
            project_name=cs.project_basename,
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
    3. **ChangeSpec name**: search all changespecs for a matching name,
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


def _extract_pr_number(pr_url: str | None) -> str | None:
    """Extract a PR number from a GitHub PR URL, or return ``None``."""
    if not pr_url:
        return None
    match = _PR_URL_RE.match(pr_url)
    return match.group(1) if match else None


def _check_pr_state(pr_number: str, cwd: str) -> str | None:
    """Return the PR state (``OPEN``, ``CLOSED``, ``MERGED``) or ``None``."""
    try:
        result = subprocess.run(
            ["gh", "pr", "view", pr_number, "--json", "state", "-q", ".state"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            env=_non_interactive_gh_env(),
            stdin=subprocess.DEVNULL,
        )
        if result.returncode == 0:
            return result.stdout.strip() or None
    except Exception:
        pass
    return None


def _check_existing_pr(cwd: str) -> bool:
    """Check if a PR exists for the current branch."""
    try:
        result = subprocess.run(
            ["gh", "pr", "view", "--json", "number"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            env=_non_interactive_gh_env(),
            stdin=subprocess.DEVNULL,
        )
        return result.returncode == 0
    except Exception:
        return False


def _submit_via_pr_merge(
    changespec: object,
    ws_dir: str,
    console: object | None,
    *,
    pr_number: str | None = None,
) -> tuple[bool, str | None]:
    """Submit by merging the PR via ``gh pr merge``."""
    from sase_github.config import get_github_orgs

    gh_orgs = get_github_orgs()
    if not gh_orgs:
        return (
            False,
            "Cannot submit GitHub PR: 'github_orgs' is not configured "
            "in sase.yml. Add 'github_orgs: [your_username]' to "
            "~/.config/sase/sase.yml",
        )

    if console:
        from rich.console import Console as RichConsole

        if isinstance(console, RichConsole):
            console.print("[cyan]Merging PR via gh pr merge...[/cyan]")

    try:
        merge_cmd = ["gh", "pr", "merge", "--merge", "--delete-branch"]
        if pr_number:
            merge_cmd.insert(3, pr_number)
        result = subprocess.run(
            merge_cmd,
            cwd=ws_dir,
            capture_output=True,
            text=True,
            check=False,
            env=_non_interactive_gh_env(),
            stdin=subprocess.DEVNULL,
        )
        if result.returncode != 0:
            error_msg = result.stderr.strip() or result.stdout.strip()
            return (
                False,
                f"gh pr merge failed: {error_msg}"
                if error_msg
                else "gh pr merge failed",
            )
    except FileNotFoundError:
        return (False, "gh command not found")

    if console:
        from rich.console import Console as RichConsole

        if isinstance(console, RichConsole):
            console.print("[green]PR merged successfully[/green]")

    from sase.workspace_provider.submission_utils import finalize_submission

    return finalize_submission(changespec.file_path, changespec.name, console)  # type: ignore[attr-defined, arg-type]


def _non_interactive_gh_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    env = non_interactive_git_env(base)
    env["GH_PROMPT_DISABLED"] = "1"
    return env


def _prepare_mail_git(
    changespec_name: str,
    project_basename: str,
    target_dir: str,
    console: object | None,
) -> object | None:
    """Git-specific mail preparation: display branch info and confirm push."""
    from rich.console import Console as RichConsole
    from rich.markup import escape as escape_markup
    from rich.panel import Panel

    from sase.ace.mail_ops import MailPrepResult, get_cl_description
    from sase.vcs_provider import get_vcs_provider

    if not isinstance(console, RichConsole):
        return None

    provider = get_vcs_provider(target_dir)

    # Display current branch name
    branch_ok, branch_name = provider.get_branch_name(target_dir)
    if branch_ok and branch_name:
        console.print(f"\n[cyan]Branch: {branch_name}[/cyan]")

    # Display current description
    success, current_desc = get_cl_description(
        changespec_name,
        target_dir,
        console,
        project_basename=project_basename,
    )
    if success and current_desc:
        console.print(
            Panel(
                escape_markup(current_desc.rstrip()),
                title="Commit Description",
                border_style="cyan",
                padding=(1, 2),
            )
        )

    # Prompt user before pushing
    console.print(
        "\n[cyan]Do you want to push and create/update the PR now? (y/n):[/cyan] ",
        end="",
    )
    try:
        mail_response = input().strip().lower()
    except (EOFError, KeyboardInterrupt):
        console.print("\n[yellow]Aborted[/yellow]")
        return None

    should_mail = mail_response in ["y", "yes"]
    if not should_mail:
        console.print("[yellow]User declined to push[/yellow]")

    return MailPrepResult(should_mail=should_mail)
