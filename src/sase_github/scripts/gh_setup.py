"""Setup step for the #gh xprompt workflow."""

import os
import sys

from sase.running_field import (
    WorkspaceClaim,
    claim_next_axe_workspace,
    claim_workspace,
    get_claimed_workspaces,
    release_workspace,
)
from sase.sdd.store import materialize_sdd_store
from sase.workspace_provider import resolve_ref
from sase.workspace_provider.utils import ensure_workspace_checkout

_CALLER_TAG = "gh-setup"


def main(
    *,
    gh_ref: str,
    n: int | None,
    release: bool,
    workflow_label: str | None = None,
) -> None:
    """Resolve GitHub ref, claim a workspace, and print config.

    Prints key=value output for the workflow executor.
    """
    resolved = resolve_ref(gh_ref, "gh")

    project_name = resolved.project_name
    project_file = resolved.project_file
    workflow_name = workflow_label or f"gh-{gh_ref}"
    cl_name = None if "/" in gh_ref else gh_ref

    # Use the parent process PID, not our own.  This setup step runs as a
    # short-lived subprocess (via ``subprocess.run``), so ``os.getpid()``
    # would die immediately after setup.  ``stale_running_cleanup`` would
    # then incorrectly remove the RUNNING entry while the real workflow
    # (the parent process) is still alive.
    pid = os.getppid()

    # Skip claiming when pre-allocated — the launcher already claimed the workspace
    pre_allocated = os.environ.get("SASE_GH_PRE_ALLOCATED") == "1"
    if pre_allocated:
        workspace_num = int(os.environ["SASE_GH_WORKSPACE_NUM"])
        workspace_dir = os.environ["SASE_GH_WORKSPACE_DIR"]
        materialize_sdd_store(workspace_dir, workspace_num)
    elif n is not None:
        workspace_num, workspace_dir = _claim_pinned_workspace(
            project_file=project_file,
            primary_workspace_dir=resolved.primary_workspace_dir,
            workspace_num=n,
            workflow_name=workflow_name,
            pid=pid,
            cl_name=cl_name,
            pinned=not release,
        )
    else:
        workspace_num, workspace_dir = _claim_next_workspace(
            project_file=project_file,
            primary_workspace_dir=resolved.primary_workspace_dir,
            workflow_name=workflow_name,
            pid=pid,
            cl_name=cl_name,
            pinned=not release,
        )

    print(f"project_name={project_name}")
    print(f"project_file={project_file}")
    print(f"workspace_dir={workspace_dir}")
    print(f"workspace_num={workspace_num}")
    print(f"checkout_target={resolved.checkout_target}")
    print(f"primary_workspace_dir={resolved.primary_workspace_dir}")
    # Don't release pre-allocated workspaces — the launcher handles that
    should_release = release and not pre_allocated
    print(f"should_release={'true' if should_release else 'false'}")
    print(f"_chdir={workspace_dir}")
    print(f"meta_workspace={workspace_num}")
    print(f"workflow_name={workflow_name}")


def _claim_next_workspace(
    *,
    project_file: str,
    primary_workspace_dir: str,
    workflow_name: str,
    pid: int,
    cl_name: str | None,
    pinned: bool,
) -> tuple[int, str]:
    """Atomically allocate a slot, then materialize the checkout."""
    workspace_num = claim_next_axe_workspace(
        project_file,
        workflow_name,
        pid,
        cl_name=cl_name,
        pinned=pinned,
        caller_tag=_CALLER_TAG,
    )
    workspace_dir = _materialize_or_release(
        project_file=project_file,
        primary_workspace_dir=primary_workspace_dir,
        workspace_num=workspace_num,
        workflow_name=workflow_name,
        cl_name=cl_name,
    )
    return workspace_num, workspace_dir


def _claim_pinned_workspace(
    *,
    project_file: str,
    primary_workspace_dir: str,
    workspace_num: int,
    workflow_name: str,
    pid: int,
    cl_name: str | None,
    pinned: bool,
) -> tuple[int, str]:
    """Claim a pinned ``n=<num>`` target once. Occupied slots fail with the occupant named."""
    claim_result = claim_workspace(
        project_file,
        workspace_num,
        workflow_name,
        pid,
        cl_name,
        pinned=pinned,
        caller_tag=_CALLER_TAG,
    )
    if not claim_result.success:
        occupant = _describe_workspace_occupant(project_file, workspace_num)
        if occupant is not None:
            print(
                f"Pinned workspace #{workspace_num} is already claimed by {occupant}",
                file=sys.stderr,
            )
        else:
            print(
                f"Pinned workspace #{workspace_num} could not be claimed: "
                f"{claim_result.error or 'unknown reason'}",
                file=sys.stderr,
            )
        sys.exit(1)
    workspace_dir = _materialize_or_release(
        project_file=project_file,
        primary_workspace_dir=primary_workspace_dir,
        workspace_num=workspace_num,
        workflow_name=workflow_name,
        cl_name=cl_name,
    )
    return workspace_num, workspace_dir


def _materialize_or_release(
    *,
    project_file: str,
    primary_workspace_dir: str,
    workspace_num: int,
    workflow_name: str,
    cl_name: str | None,
) -> str:
    """Materialize after a successful claim; release the slot if setup fails."""
    try:
        workspace_dir = ensure_workspace_checkout(primary_workspace_dir, workspace_num)
        materialize_sdd_store(workspace_dir, workspace_num)
    except Exception:
        release_workspace(
            project_file,
            workspace_num,
            workflow_name,
            cl_name,
            caller_tag=_CALLER_TAG,
        )
        raise
    return workspace_dir


def _describe_workspace_occupant(project_file: str, workspace_num: int) -> str | None:
    occupants = [
        claim
        for claim in get_claimed_workspaces(project_file)
        if claim.workspace_num == workspace_num
    ]
    if not occupants:
        return None
    return "; ".join(_format_workspace_occupant(claim) for claim in occupants)


def _format_workspace_occupant(claim: WorkspaceClaim) -> str:
    name = claim.cl_name or claim.workflow
    liveness = "live" if _pid_is_alive(claim.pid) else "dead"
    detail = f"{name} (pid {claim.pid}, {liveness}, workflow {claim.workflow}"
    if claim.artifacts_timestamp:
        detail += f", artifacts {claim.artifacts_timestamp}"
    return detail + ")"


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
