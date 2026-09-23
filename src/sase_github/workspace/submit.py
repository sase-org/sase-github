"""Patch submission: PR state checks and `gh pr merge`."""

from __future__ import annotations

import os
import re
import subprocess

from sase.ace.patch import Patch, find_all_patches
from sase.workspace_provider.utils import get_default_branch, parse_workspace_dir
from sase_github.workspace.gh_cli import non_interactive_gh_env

PR_URL_RE = re.compile(r"https?://[^/]+/.+?/pull/(\d+)")


def submit_patch(
    patch_file: str,
    patch_name: str,
    project_basename: str,
    console: object | None,
) -> tuple[bool, str | None]:
    from rich.console import Console as RichConsole
    from rich.markup import escape as escape_markup

    from sase.ace.hooks.processes import (
        kill_and_persist_all_running_processes,
    )
    from sase.ace.operations import has_active_children
    from sase.core.occupancy_guard import (
        OccupancyCaller,
        WorkspaceOccupiedError,
        ensure_workspace_not_occupied,
    )
    from sase.running_field import (
        WorkspaceClaimError,
        claim_next_axe_workspace_dir,
        release_workspace,
    )
    from sase.vcs_provider import get_vcs_provider

    rich_console: RichConsole | None = (
        console if isinstance(console, RichConsole) else None
    )

    patch: Patch | None = None
    for candidate in find_all_patches():
        if candidate.name == patch_name:
            patch = candidate
            break
    if patch is None:
        return (False, f"Patch '{patch_name}' not found")

    log_fn = (
        (lambda msg: rich_console.print(f"[cyan]{escape_markup(msg)}[/cyan]"))
        if rich_console
        else None
    )
    kill_and_persist_all_running_processes(
        patch,
        patch_file,
        patch_name,
        "Killed hook running on submitted PR.",
        log_fn=log_fn,
    )

    all_patches = find_all_patches()
    if has_active_children(
        patch,
        all_patches,
        terminal_statuses=("Submitted", "Reverted", "Archived"),
    ):
        return (
            False,
            "Cannot submit: other Patches have this one as their "
            "parent and are not Submitted, Reverted, or Archived",
        )

    workspace_dir = parse_workspace_dir(patch_file)
    if not workspace_dir:
        return (False, "WORKSPACE_DIR is not set for this project")

    workflow_name = f"submit-{patch_name}"
    try:
        workspace_num, ws_dir, _ = claim_next_axe_workspace_dir(
            patch_file,
            workflow_name,
            os.getpid(),
            project_basename,
            cl_name=patch_name,
            caller_tag="gh-submit",
        )
    except WorkspaceClaimError as exc:
        return (False, f"Failed to claim workspace: {exc}")

    if rich_console:
        rich_console.print(f"[cyan]Claiming workspace #{workspace_num}[/cyan]")

    try:
        if rich_console:
            rich_console.print(
                f"[cyan]Checking out {escape_markup(patch_name)}...[/cyan]"
            )

        try:
            ensure_workspace_not_occupied(
                ws_dir,
                project_file=patch_file,
                caller=OccupancyCaller(
                    pid=os.getpid(),
                    workspace_num=workspace_num,
                    project=project_basename,
                    workflow=workflow_name,
                ),
            )
        except WorkspaceOccupiedError as exc:
            return (False, f"Refusing checkout: {exc}")

        provider = get_vcs_provider(ws_dir)
        branch_name = provider.resolve_revision(patch_name, project_basename, ws_dir)
        success, error = provider.checkout(branch_name, ws_dir)
        if not success:
            return (False, f"Failed to checkout branch: {error}")

        default_branch_ref = get_default_branch(ws_dir)
        default_branch = default_branch_ref.rsplit("/", 1)[-1]

        if rich_console:
            rich_console.print(
                f"[cyan]Merging {escape_markup(patch_name)} into "
                f"{escape_markup(default_branch)}...[/cyan]"
            )

        # Prefer the recorded PR URL/number when available — this is
        # resilient to branch renames (e.g. suffix strip/append).
        pr_number = _extract_pr_number(patch.pr_url)
        if pr_number:
            pr_state = _check_pr_state(pr_number, ws_dir)
            if pr_state == "OPEN":
                return _submit_via_pr_merge(
                    patch, ws_dir, rich_console, pr_number=pr_number
                )
            elif pr_state == "CLOSED":
                return (
                    False,
                    f"PR #{pr_number} (from Patch PR field) is closed "
                    "and unmerged. Reopen it or create a new PR with #pr.",
                )
            elif pr_state == "MERGED":
                return (
                    False,
                    f"PR #{pr_number} (from Patch PR field) is already merged.",
                )
            # pr_state is None — fall through to branch-based check

        # Fallback: check for a PR on the current branch
        has_pr = _check_existing_pr(ws_dir)
        if has_pr:
            return _submit_via_pr_merge(patch, ws_dir, rich_console)
        return (
            False,
            "GitHub project has no PR for this branch. Create a PR first with #pr.",
        )
    finally:
        release_workspace(
            patch_file,
            workspace_num,
            workflow_name,
            patch_name,
            caller_tag="gh-submit",
        )
        if rich_console:
            rich_console.print(f"[cyan]Released workspace #{workspace_num}[/cyan]")


def _extract_pr_number(pr_url: str | None) -> str | None:
    """Extract a PR number from a GitHub PR URL, or return ``None``."""
    if not pr_url:
        return None
    match = PR_URL_RE.match(pr_url)
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
            env=non_interactive_gh_env(),
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
            env=non_interactive_gh_env(),
            stdin=subprocess.DEVNULL,
        )
        return result.returncode == 0
    except Exception:
        return False


def _submit_via_pr_merge(
    patch: object,
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
            env=non_interactive_gh_env(),
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

    return finalize_submission(patch.file_path, patch.name, console)  # type: ignore[attr-defined, arg-type]
