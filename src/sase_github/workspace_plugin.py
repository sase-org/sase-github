"""GitHub workspace provider plugin for sase.

Implements the ``sase_workspace`` pluggy hooks for GitHub-hosted projects,
handling workflow detection, reference resolution, change labels, and
PR-based submission.
"""

import os
import re
import subprocess
from pathlib import Path

from sase.workspace_provider import ResolvedRef, WorkflowMetadata, hookimpl
from sase.workspace_utils import (
    get_default_branch,
    parse_workspace_dir,
    set_workspace_dir,
)


class GitHubWorkspacePlugin:
    """Workspace provider plugin for GitHub-hosted projects."""

    # ── Hook implementations ────────────────────────────────────────

    @hookimpl
    def ws_get_workflow_metadata(self) -> WorkflowMetadata | None:
        return WorkflowMetadata(
            workflow_type="gh",
            ref_pattern=r"(?:^|(?<=\s))#gh(?::([a-zA-Z0-9_./-]+)|\(([^)]+)\))",
            display_name="GitHub",
            pre_allocated_env_prefix="SASE_GH",
            vcs_family="git",
            vcs_provider_name="github",
        )

    @hookimpl
    def ws_detect_workflow_type(self, project_file: str) -> str | None:
        """Return ``'gh'`` if the project is GitHub-hosted, else ``None``."""
        workspace_dir = parse_workspace_dir(project_file)
        if not workspace_dir or not os.path.isdir(
            os.path.join(workspace_dir, ".git")
        ):
            return None

        from sase.workspace_utils import parse_bare_repo_dir

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
    def ws_resolve_ref(
        self, ref: str, workflow_type: str
    ) -> ResolvedRef | None:
        """Resolve a ``#gh`` reference to workspace and branch information."""
        if workflow_type != "gh":
            return None
        r = resolve_gh_ref(ref)
        return ResolvedRef(
            project_file=r.project_file,
            project_name=r.project_name,
            primary_workspace_dir=r.primary_workspace_dir,
            checkout_target=r.checkout_target,
        )

    @hookimpl
    def ws_extract_change_identifier(
        self, cl_url: str
    ) -> tuple[str, str] | None:
        """Extract PR number from a GitHub PR URL."""
        match = re.match(r"https?://github\.com/.+/pull/(\d+)", cl_url)
        if match:
            return (match.group(1), "git")
        return None

    @hookimpl
    def ws_generate_submitted_check_script(
        self, identifier: str, vcs_type: str
    ) -> str | None:
        """Generate script to check if a GitHub PR is merged."""
        if vcs_type != "git":
            return None
        return (
            f'state=$(gh pr view {identifier} --json state -q \'.state\' 2>/dev/null)\n'
            f'[ "$state" = "MERGED" ]'
        )

    @hookimpl
    def ws_supports_reviewer_comments(self, cl_url: str) -> bool | None:
        """GitHub does not support reviewer comments via critique_comments."""
        if re.match(r"https?://github\.com/", cl_url):
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
        from sase.workspace_utils import ensure_git_clone

        return ensure_git_clone(primary_workspace_dir, workspace_num)

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
        return _prepare_mail_git(
            changespec_name, project_basename, target_dir, console
        )

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
            (
                lambda msg: rich_console.print(
                    f"[cyan]{escape_markup(msg)}[/cyan]"
                )
            )
            if rich_console
            else None
        )
        kill_and_persist_all_running_processes(
            changespec,
            changespec_file,
            changespec_name,
            "Killed hook running on submitted CL.",
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
            ws_dir, _ = get_workspace_directory_for_num(
                workspace_num, project_basename
            )
        except RuntimeError as e:
            return (False, f"Failed to get workspace directory: {e}")

        if rich_console:
            rich_console.print(
                f"[cyan]Claiming workspace #{workspace_num}[/cyan]"
            )

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

            has_pr = _check_existing_pr(ws_dir)
            if has_pr:
                return _submit_via_pr_merge(
                    changespec, ws_dir, rich_console
                )
            return (
                False,
                "GitHub project has no PR for this branch. "
                "Create a PR first with #pr.",
            )
        finally:
            release_workspace(
                changespec_file,
                workspace_num,
                workflow_name,
                changespec_name,
            )
            if rich_console:
                rich_console.print(
                    f"[cyan]Released workspace #{workspace_num}[/cyan]"
                )


# ── Private helpers ─────────────────────────────────────────────────


def _clone_gh_repo(user: str, project: str, target_dir: str) -> None:
    """Clone a GitHub repo to the target directory."""
    from sase_github.config import get_github_username

    gh_user = get_github_username()
    if gh_user and gh_user == user:
        url = f"git@github.com:{user}/{project}.git"
    else:
        url = f"https://github.com/{user}/{project}.git"
    parent = os.path.dirname(target_dir.rstrip("/"))
    os.makedirs(parent, exist_ok=True)

    try:
        subprocess.run(
            ["git", "clone", url, target_dir.rstrip("/")],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        error_msg = f"git clone failed for {url}"
        if e.stderr:
            error_msg += f": {e.stderr.strip()}"
        raise RuntimeError(error_msg) from e


def resolve_gh_ref(gh_ref: str) -> ResolvedRef:
    """Resolve a ``#gh`` reference to workspace and branch information.

    Three dispatch modes:

    1. **Repo path** (contains ``/``): ``user/project`` → derive workspace
       from ``~/projects/github/<user>/<project>/``.
    2. **Project shorthand** (no ``/``, matching project dir): look up
       WORKSPACE_DIR from ``~/.sase/projects/<name>/<name>.gp``.
    3. **ChangeSpec name**: search all changespecs for a matching name,
       read WORKSPACE_DIR from its project file.

    Raises:
        ValueError: If the reference cannot be resolved.
    """
    from sase.ace.changespec import find_all_changespecs

    projects_base = Path.home() / ".sase" / "projects"

    # --- Mode 1: repo path (user/project) ---
    if "/" in gh_ref:
        parts = gh_ref.strip("/").split("/")
        if len(parts) != 2:
            raise ValueError(
                f"Invalid repo path '{gh_ref}': expected 'user/project'"
            )
        user, project = parts
        primary_workspace_dir = (
            str(Path.home() / "projects" / "github" / user / project) + "/"
        )
        project_file = str(projects_base / project / f"{project}.gp")

        existing = parse_workspace_dir(project_file)
        if existing and os.path.normpath(existing) != os.path.normpath(
            primary_workspace_dir
        ):
            raise ValueError(
                f"WORKSPACE_DIR conflict for '{project}': "
                f"existing={existing}, derived={primary_workspace_dir}"
            )

        if not os.path.isdir(primary_workspace_dir.rstrip("/")):
            _clone_gh_repo(user, project, primary_workspace_dir)

        set_workspace_dir(project_file, primary_workspace_dir)
        checkout_target = get_default_branch(primary_workspace_dir)

        return ResolvedRef(
            project_file=project_file,
            project_name=project,
            primary_workspace_dir=primary_workspace_dir,
            checkout_target=checkout_target,
        )

    # --- Mode 2: project shorthand ---
    project_dir = projects_base / gh_ref
    project_file_path = project_dir / f"{gh_ref}.gp"
    if project_dir.is_dir() and project_file_path.exists():
        workspace_dir = parse_workspace_dir(str(project_file_path))
        if workspace_dir:
            checkout_target = get_default_branch(workspace_dir)
            return ResolvedRef(
                project_file=str(project_file_path),
                project_name=gh_ref,
                primary_workspace_dir=workspace_dir,
                checkout_target=checkout_target,
            )

    # --- Mode 3: ChangeSpec name ---
    for cs in find_all_changespecs():
        if cs.name == gh_ref:
            workspace_dir = parse_workspace_dir(cs.file_path)
            if not workspace_dir:
                raise ValueError(
                    f"ChangeSpec '{gh_ref}' found in {cs.file_path} "
                    "but WORKSPACE_DIR is not set"
                )
            return ResolvedRef(
                project_file=cs.file_path,
                project_name=cs.project_basename,
                primary_workspace_dir=workspace_dir,
                checkout_target=f"origin/{gh_ref}",
            )

    raise ValueError(f"Cannot resolve gh_ref '{gh_ref}'")


def _check_existing_pr(cwd: str) -> bool:
    """Check if a PR exists for the current branch."""
    try:
        result = subprocess.run(
            ["gh", "pr", "view", "--json", "number"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode == 0
    except Exception:
        return False


def _submit_via_pr_merge(
    changespec: object,
    ws_dir: str,
    console: object | None,
) -> tuple[bool, str | None]:
    """Submit by merging the PR via ``gh pr merge``."""
    from sase_github.config import get_github_username

    username = get_github_username()
    if not username:
        return (
            False,
            "Cannot submit GitHub PR: 'github_username' is not configured "
            "in sase.yml. Add 'github_username: <your_username>' to "
            "~/.config/sase/sase.yml",
        )

    if console:
        from rich.console import Console as RichConsole

        if isinstance(console, RichConsole):
            console.print("[cyan]Merging PR via gh pr merge...[/cyan]")

    try:
        result = subprocess.run(
            ["gh", "pr", "merge", "--merge", "--delete-branch"],
            cwd=ws_dir,
            capture_output=True,
            text=True,
            check=False,
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

    from sase.submission_utils import finalize_submission

    return finalize_submission(changespec.file_path, changespec.name, console)  # type: ignore[arg-type]


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
