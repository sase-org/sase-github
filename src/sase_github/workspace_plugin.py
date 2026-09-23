"""GitHub workspace provider plugin for sase.

Implements the ``sase_workspace`` pluggy hooks for GitHub-hosted projects,
handling workflow detection, reference resolution, change labels, and
PR-based submission.

The implementation behind each hook lives in :mod:`sase_github.workspace`.
"""

from __future__ import annotations

import os
import re
import subprocess

from sase.workspace_provider import (
    ExternalRepoCloneResult,
    ResolvedRef,
    SddSidecarPreflight,
    VcsRefNamespaces,
    VcsRepoCandidates,
    WorkflowMetadata,
    hookimpl,
)
from sase.workspace_provider.utils import get_default_branch, parse_workspace_dir
from sase_github.workspace.completion import (
    list_github_ref_namespaces,
    list_github_repo_candidates,
    repo_candidates_error,
)
from sase_github.workspace.mail import prepare_mail
from sase_github.workspace.refs import peek_gh_ref, resolve_gh_ref
from sase_github.workspace.remotes import clone_gh_repo
from sase_github.workspace.sdd_sidecar import (
    create_sdd_remote,
    materialize_sdd_store,
    preflight_sdd_sidecar,
)
from sase_github.workspace.submit import PR_URL_RE, submit_patch

_HOSTED_URL_RE = re.compile(r"https?://[^/]+/")


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
            external_repo_schemes=("gh",),
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
    def ws_clone_external_repo(
        self,
        scheme: str,
        ref: str,
        dest_dir: str,
    ) -> ExternalRepoCloneResult | None:
        """Clone a workspace-local GitHub repo for the ``gh`` scheme."""

        if scheme != "gh":
            return None
        parts = ref.split("/")
        if len(parts) != 2 or any(not part for part in parts):
            raise RuntimeError(
                f"Invalid GitHub external repo ref {ref!r}; expected owner/repo"
            )
        owner, repo = parts
        clone_gh_repo(owner, repo, dest_dir)
        default_branch = get_default_branch(dest_dir).removeprefix("origin/")
        return ExternalRepoCloneResult(
            canonical_name=f"gh:{owner}/{repo}",
            dest_dir=dest_dir,
            default_branch=default_branch,
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
            return repo_candidates_error(
                "unsupported_namespace",
                "GitHub repo completion supports a single owner or organization.",
            )
        return list_github_repo_candidates(owner)

    @hookimpl
    def ws_list_ref_namespaces(self, workflow_type: str) -> VcsRefNamespaces | None:
        """List locally-known GitHub owners for root ref completion."""
        if workflow_type != "gh":
            return None
        return list_github_ref_namespaces()

    @hookimpl
    def ws_extract_change_identifier(self, pr_url: str) -> tuple[str, str] | None:
        """Extract PR number from a GitHub PR URL."""
        match = PR_URL_RE.match(pr_url)
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
        """Find or create, label, and stage a GitHub sidecar repository."""
        return materialize_sdd_store(primary_workspace_dir, options)

    @hookimpl
    def ws_preflight_sdd_sidecar(
        self,
        primary_workspace_dir: str,
        workspace_dir: str,
        options: dict[str, object],
    ) -> SddSidecarPreflight | None:
        """Authoritatively discover a sidecar without mutating state."""
        return preflight_sdd_sidecar(primary_workspace_dir, options)

    @hookimpl
    def ws_create_sdd_remote(
        self,
        primary_workspace_dir: str,
        workspace_dir: str,
        options: dict[str, object],
    ) -> dict[str, object] | None:
        """Verify or create a GitHub sidecar SDD repository."""
        return create_sdd_remote(primary_workspace_dir, options)

    @hookimpl
    def ws_prepare_mail(
        self,
        # Legacy hook argument name retained for compatibility.
        changespec_name: str,
        # Legacy hook argument name retained for compatibility.
        changespec_parent: str | None,
        project_basename: str,
        project_file: str,
        target_dir: str,
        console: object | None,
    ) -> object | None:
        if self.ws_detect_workflow_type(project_file=project_file) != "gh":
            return None
        return prepare_mail(changespec_name, project_basename, target_dir, console)

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
        # Legacy hook argument name retained for compatibility.
        changespec_file: str,
        changespec_name: str,
        project_basename: str,
        console: object | None = None,
    ) -> tuple[bool, str | None] | None:
        """Submit a GitHub Patch by merging its PR."""
        from sase.workspace_provider import detect_workflow_type

        patch_file = changespec_file  # legacy hook argument name
        patch_name = changespec_name
        vcs_type = detect_workflow_type(patch_file)
        if vcs_type != "gh":
            return None

        return submit_patch(patch_file, patch_name, project_basename, console)
