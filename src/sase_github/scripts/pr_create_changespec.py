"""Create changespec step for the #pr xprompt workflow."""

import os
import re
import subprocess

from sase.workspace_provider.utils import get_default_branch
from sase.vcs_provider import get_vcs_provider
from sase.workflows.utils import get_project_file_path
from sase.workspace_provider.changespec import create_changespec_for_workflow


def _rename_branch(old_name: str, new_name: str) -> bool:
    """Rename the current git branch and update the remote.

    Returns True on success.
    """
    # Rename local branch
    result = subprocess.run(
        ["git", "branch", "-m", old_name, new_name],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return False

    # Delete old remote branch (best-effort)
    subprocess.run(
        ["git", "push", "origin", "--delete", old_name],
        capture_output=True,
        text=True,
        check=False,
    )

    # Push new branch and set upstream
    result = subprocess.run(
        ["git", "push", "-u", "origin", new_name],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def main(*, name: str, prompt: str, response: str) -> None:
    """Derive project info from cwd and create a changespec.

    Prints key=value output for the workflow executor.
    """
    provider = get_vcs_provider(os.getcwd())
    ok, project_name = provider.get_workspace_name(os.getcwd())
    if not ok or not project_name:
        print("success=false")
        print("error=Could not determine project name from workspace")
        print("cl_name=")
        print("project_file=")
        print("default_branch=")
        print(f"branch_name={name}")
        return

    project_file = get_project_file_path(project_name)

    # Determine default branch
    default_branch_ref = get_default_branch(os.getcwd())
    default_branch = default_branch_ref.rsplit("/", 1)[-1]

    # Build CL name: {project}_{name_with_underscores}
    cl_name = f"{project_name}_{name.replace('-', '_')}"

    result = create_changespec_for_workflow(
        project_name=project_name,
        project_file=project_file,
        checkout_target=f"origin/{default_branch}",
        branch_name=name,
        prompt=prompt,
        response=response,
        workflow_name="pr",
        cl_name=cl_name,
    )

    if result:
        # Extract the _<N> suffix from the suffixed ChangeSpec name and
        # rename the git branch to include it (e.g. banana -> banana_1).
        branch_name = name
        match = re.search(r"_(\d+)$", result)
        if match:
            new_branch = f"{name}_{match.group(1)}"
            if _rename_branch(name, new_branch):
                branch_name = new_branch

        print("success=true")
        print(f"cl_name={result}")
        print(f"project_file={project_file}")
        print(f"default_branch={default_branch}")
        print(f"branch_name={branch_name}")
        print(f"meta_changespec={result}")
        print("error=")
    else:
        print("success=false")
        print("cl_name=")
        print(f"project_file={project_file}")
        print(f"default_branch={default_branch}")
        print(f"branch_name={name}")
        print("error=No new commits found")
