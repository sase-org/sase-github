"""Atomic workspace acquisition for #gh setup, submit, and release."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sase.core.occupancy_guard import WorkspaceOccupiedError
from sase.logs.workspace_claim_ledger import read_ledger_records
from sase.running_field import (
    ClaimResult,
    WorkspaceClaim,
    WorkspaceClaimError,
    get_claimed_workspaces,
)
from sase.workspace_provider import ResolvedRef
from sase.workspace_provider.occupant import (
    new_occupant_record,
    read_occupant_record,
    write_occupant_record,
)

from sase_github.scripts import gh_setup
from sase_github.workspace_plugin import GitHubWorkspacePlugin

ROOT = Path(__file__).resolve().parents[1]


def _write_project_file(
    tmp_path: Path, running_claims: list[WorkspaceClaim] | None = None
) -> str:
    project_file = tmp_path / "project.sase"
    lines = ["# Test Project\n"]
    if running_claims:
        lines.append("RUNNING:\n")
        for claim in running_claims:
            lines.append(claim.to_line() + "\n")
    lines.extend(["NAME: Test Feature\n", "STATUS: Ready\n"])
    project_file.write_text("".join(lines), encoding="utf-8")
    return str(project_file)


def _resolved(project_file: str, primary: str = "/work/widget/") -> ResolvedRef:
    return ResolvedRef(
        project_file=project_file,
        project_name="gh_acme__widget",
        primary_workspace_dir=primary,
        checkout_target="origin/main",
        canonical_ref="gh_acme__widget",
    )


def _isolate_ledger(tmp_path: Path) -> object:
    return patch(
        "sase.logs.workspace_claim_ledger.LEDGER_FILE",
        str(tmp_path / "workspace_claims.jsonl"),
    )


class TestGhSetupAtomicClaim:
    def test_unpinned_setup_skips_number_claimed_at_claim_time(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        occupied = WorkspaceClaim(10, "ace(run)-other", "other-agent", pid=11111)
        project_file = _write_project_file(tmp_path, running_claims=[occupied])
        resolved = {11: "/work/widget_11/"}

        with (
            _isolate_ledger(tmp_path),
            patch(
                "sase_github.scripts.gh_setup.resolve_ref",
                return_value=_resolved(project_file),
            ),
            patch.dict(os.environ, {"SASE_GH_PRE_ALLOCATED": "0"}),
            patch(
                "sase_github.scripts.gh_setup.ensure_workspace_checkout",
                side_effect=lambda _primary, num: resolved[num],
            ),
            patch("sase_github.scripts.gh_setup.materialize_sdd_store"),
        ):
            gh_setup.main(gh_ref="acme/widget", n=None, release=False)

        claimed_nums = {
            claim.workspace_num for claim in get_claimed_workspaces(project_file)
        }
        assert 10 in claimed_nums
        assert 11 in claimed_nums
        assert "workspace_num=11" in capsys.readouterr().out

    def test_materialization_failure_after_claim_releases_slot(
        self, tmp_path: Path
    ) -> None:
        project_file = _write_project_file(tmp_path)

        with (
            _isolate_ledger(tmp_path),
            patch(
                "sase_github.scripts.gh_setup.resolve_ref",
                return_value=_resolved(project_file),
            ),
            patch.dict(os.environ, {"SASE_GH_PRE_ALLOCATED": "0"}),
            patch(
                "sase_github.scripts.gh_setup.ensure_workspace_checkout",
                return_value="/work/widget_10/",
            ),
            patch(
                "sase_github.scripts.gh_setup.materialize_sdd_store",
                side_effect=RuntimeError("sidecar setup failed"),
            ),
            pytest.raises(RuntimeError, match="sidecar setup failed"),
        ):
            gh_setup.main(gh_ref="acme/widget", n=None, release=False)

        assert get_claimed_workspaces(project_file) == []

    def test_checkout_failure_after_claim_releases_slot(self, tmp_path: Path) -> None:
        project_file = _write_project_file(tmp_path)

        with (
            _isolate_ledger(tmp_path),
            patch(
                "sase_github.scripts.gh_setup.resolve_ref",
                return_value=_resolved(project_file),
            ),
            patch.dict(os.environ, {"SASE_GH_PRE_ALLOCATED": "0"}),
            patch(
                "sase_github.scripts.gh_setup.ensure_workspace_checkout",
                side_effect=RuntimeError("clone failed"),
            ),
            patch("sase_github.scripts.gh_setup.materialize_sdd_store") as materialize,
            pytest.raises(RuntimeError, match="clone failed"),
        ):
            gh_setup.main(gh_ref="acme/widget", n=None, release=False)

        materialize.assert_not_called()
        assert get_claimed_workspaces(project_file) == []

    def test_pinned_target_held_by_live_pid_fails_with_occupant(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        occupant_pid = os.getpid()
        occupied = WorkspaceClaim(
            17,
            "ace(run)-260818_125956",
            "06e--plan",
            pid=occupant_pid,
            artifacts_timestamp="260818_125956",
        )
        project_file = _write_project_file(tmp_path, running_claims=[occupied])

        with (
            _isolate_ledger(tmp_path),
            patch(
                "sase_github.scripts.gh_setup.resolve_ref",
                return_value=_resolved(project_file),
            ),
            patch.dict(os.environ, {"SASE_GH_PRE_ALLOCATED": "0"}),
            patch("sase_github.scripts.gh_setup.ensure_workspace_checkout") as checkout,
            patch("sase_github.scripts.gh_setup.materialize_sdd_store") as materialize,
            pytest.raises(SystemExit) as exc_info,
        ):
            gh_setup.main(gh_ref="acme/widget", n=17, release=False)

        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "Pinned workspace #17 is already claimed by" in err
        assert "06e--plan" in err
        assert f"pid {occupant_pid}" in err
        assert "live" in err
        assert "artifacts 260818_125956" in err
        checkout.assert_not_called()
        materialize.assert_not_called()
        claims = get_claimed_workspaces(project_file)
        assert len(claims) == 1
        assert claims[0].pid == occupant_pid
        assert claims[0].workspace_num == 17

    def test_pinned_target_claim_is_single_shot(self, tmp_path: Path) -> None:
        claim_workspace_mock = MagicMock(
            return_value=ClaimResult(
                success=False, error="workspace #17 is already claimed"
            )
        )

        with (
            patch(
                "sase_github.scripts.gh_setup.resolve_ref",
                return_value=_resolved("proj.sase"),
            ),
            patch.dict(os.environ, {"SASE_GH_PRE_ALLOCATED": "0"}),
            patch("sase_github.scripts.gh_setup.claim_workspace", claim_workspace_mock),
            patch(
                "sase_github.scripts.gh_setup.get_claimed_workspaces",
                return_value=[
                    WorkspaceClaim(17, "ace(run)-other", "other-agent", pid=os.getpid())
                ],
            ),
            patch("sase_github.scripts.gh_setup.ensure_workspace_checkout") as checkout,
            pytest.raises(SystemExit),
        ):
            gh_setup.main(gh_ref="acme/widget", n=17, release=False)

        assert claim_workspace_mock.call_count == 1
        checkout.assert_not_called()

    def test_pre_allocated_branch_does_not_claim(self, tmp_path: Path) -> None:
        workspace_dir = str(tmp_path / "prealloc")
        with (
            patch(
                "sase_github.scripts.gh_setup.resolve_ref",
                return_value=_resolved("proj.sase"),
            ),
            patch.dict(
                os.environ,
                {
                    "SASE_GH_PRE_ALLOCATED": "1",
                    "SASE_GH_WORKSPACE_NUM": "13",
                    "SASE_GH_WORKSPACE_DIR": workspace_dir,
                },
            ),
            patch(
                "sase_github.scripts.gh_setup.claim_next_axe_workspace"
            ) as claim_next,
            patch("sase_github.scripts.gh_setup.claim_workspace") as claim,
            patch("sase_github.scripts.gh_setup.materialize_sdd_store") as materialize,
        ):
            gh_setup.main(gh_ref="acme/widget", n=None, release=True)

        claim_next.assert_not_called()
        claim.assert_not_called()
        materialize.assert_called_once_with(workspace_dir, 13)

    def test_setup_ledger_records_carry_gh_setup_tag(self, tmp_path: Path) -> None:
        project_file = _write_project_file(tmp_path)
        ledger_file = str(tmp_path / "workspace_claims.jsonl")

        with (
            patch("sase.logs.workspace_claim_ledger.LEDGER_FILE", ledger_file),
            patch(
                "sase_github.scripts.gh_setup.resolve_ref",
                return_value=_resolved(project_file),
            ),
            patch.dict(os.environ, {"SASE_GH_PRE_ALLOCATED": "0"}),
            patch(
                "sase_github.scripts.gh_setup.ensure_workspace_checkout",
                return_value="/work/widget_10/",
            ),
            patch("sase_github.scripts.gh_setup.materialize_sdd_store"),
        ):
            gh_setup.main(gh_ref="acme/widget", n=None, release=False)
            records = read_ledger_records(ledger_file=ledger_file)

        assert records
        assert all(record["caller_tag"] == "gh-setup" for record in records)
        assert any(
            record["success"] is True and record["operation"] == "claim_next_axe"
            for record in records
        )

    def test_pinned_setup_ledger_records_carry_gh_setup_tag(
        self, tmp_path: Path
    ) -> None:
        project_file = _write_project_file(tmp_path)
        ledger_file = str(tmp_path / "workspace_claims.jsonl")

        with (
            patch("sase.logs.workspace_claim_ledger.LEDGER_FILE", ledger_file),
            patch(
                "sase_github.scripts.gh_setup.resolve_ref",
                return_value=_resolved(project_file),
            ),
            patch.dict(os.environ, {"SASE_GH_PRE_ALLOCATED": "0"}),
            patch(
                "sase_github.scripts.gh_setup.ensure_workspace_checkout",
                return_value="/work/widget_17/",
            ),
            patch("sase_github.scripts.gh_setup.materialize_sdd_store"),
        ):
            gh_setup.main(gh_ref="acme/widget", n=17, release=False)
            records = read_ledger_records(ledger_file=ledger_file)

        assert records
        assert all(record["caller_tag"] == "gh-setup" for record in records)
        assert any(
            record["success"] is True and record["operation"] == "claim"
            for record in records
        )


def _dead_pid() -> int:
    """Return a pid that is guaranteed not to be alive right now."""
    proc = os.fork() if hasattr(os, "fork") else None
    if proc is None:
        pytest.skip("os.fork unavailable on this platform")
    if proc == 0:  # pragma: no cover - child branch
        os._exit(0)
    os.waitpid(proc, 0)
    return proc


class TestGhSetupOccupancyGuard:
    def test_unpinned_setup_writes_occupant_record(self, tmp_path: Path) -> None:
        project_file = _write_project_file(tmp_path)
        workspace_dir = tmp_path / "widget_10"
        workspace_dir.mkdir()

        with (
            _isolate_ledger(tmp_path),
            patch(
                "sase_github.scripts.gh_setup.resolve_ref",
                return_value=_resolved(project_file),
            ),
            patch.dict(os.environ, {"SASE_GH_PRE_ALLOCATED": "0"}),
            patch(
                "sase_github.scripts.gh_setup.ensure_workspace_checkout",
                return_value=str(workspace_dir),
            ),
            patch("sase_github.scripts.gh_setup.materialize_sdd_store"),
        ):
            gh_setup.main(gh_ref="acme/widget", n=None, release=False)

        record = read_occupant_record(str(workspace_dir))
        assert record is not None
        assert record.pid == os.getppid()
        assert record.workflow == "gh-acme/widget"
        assert record.project == "gh_acme__widget"
        assert record.workspace_num == 10
        assert record.cl_name is None

    def test_pinned_setup_writes_occupant_record(self, tmp_path: Path) -> None:
        project_file = _write_project_file(tmp_path)
        workspace_dir = tmp_path / "widget_17"
        workspace_dir.mkdir()

        with (
            _isolate_ledger(tmp_path),
            patch(
                "sase_github.scripts.gh_setup.resolve_ref",
                return_value=_resolved(project_file),
            ),
            patch.dict(os.environ, {"SASE_GH_PRE_ALLOCATED": "0"}),
            patch(
                "sase_github.scripts.gh_setup.ensure_workspace_checkout",
                return_value=str(workspace_dir),
            ),
            patch("sase_github.scripts.gh_setup.materialize_sdd_store"),
        ):
            gh_setup.main(gh_ref="acme/widget", n=17, release=False)

        record = read_occupant_record(str(workspace_dir))
        assert record is not None
        assert record.workspace_num == 17

    def test_pre_allocated_branch_does_not_write_occupant_record(
        self, tmp_path: Path
    ) -> None:
        workspace_dir = tmp_path / "prealloc"
        workspace_dir.mkdir()

        with (
            patch(
                "sase_github.scripts.gh_setup.resolve_ref",
                return_value=_resolved("proj.sase"),
            ),
            patch.dict(
                os.environ,
                {
                    "SASE_GH_PRE_ALLOCATED": "1",
                    "SASE_GH_WORKSPACE_NUM": "13",
                    "SASE_GH_WORKSPACE_DIR": str(workspace_dir),
                },
            ),
            patch("sase_github.scripts.gh_setup.materialize_sdd_store"),
        ):
            gh_setup.main(gh_ref="acme/widget", n=None, release=True)

        assert read_occupant_record(str(workspace_dir)) is None

    def test_setup_refuses_when_occupant_is_other_live_pid(
        self, tmp_path: Path
    ) -> None:
        # No conflicting RUNNING claim: our own claim of #10 succeeds
        # normally. The stale `.sase/occupant.json` marker left behind by a
        # rival that never cleared it is what the guard must catch.
        other_pid = os.getpid()
        project_file = _write_project_file(tmp_path)
        workspace_dir = tmp_path / "widget_10"
        workspace_dir.mkdir()
        write_occupant_record(
            str(workspace_dir),
            new_occupant_record(
                pid=other_pid,
                workflow="gh-acme/widget",
                project="gh_acme__widget",
                workspace_num=10,
                agent_name="rival-agent",
            ),
        )

        with (
            _isolate_ledger(tmp_path),
            patch(
                "sase_github.scripts.gh_setup.resolve_ref",
                return_value=_resolved(project_file),
            ),
            patch.dict(os.environ, {"SASE_GH_PRE_ALLOCATED": "0"}),
            patch(
                "sase_github.scripts.gh_setup.ensure_workspace_checkout",
                return_value=str(workspace_dir),
            ),
            patch("sase_github.scripts.gh_setup.materialize_sdd_store"),
            pytest.raises(WorkspaceOccupiedError, match="rival-agent"),
        ):
            gh_setup.main(gh_ref="acme/widget", n=None, release=False)

        # The refusal must happen before setup hands the checkout to
        # `prepare`/`checkout` — the occupant record on disk must still
        # name the rival agent, not this run.
        record = read_occupant_record(str(workspace_dir))
        assert record is not None
        assert record.pid == other_pid

    def test_setup_proceeds_when_occupant_is_self(self, tmp_path: Path) -> None:
        project_file = _write_project_file(tmp_path)
        workspace_dir = tmp_path / "widget_10"
        workspace_dir.mkdir()
        write_occupant_record(
            str(workspace_dir),
            new_occupant_record(
                pid=os.getppid(),
                workflow="gh-acme/widget",
                project="gh_acme__widget",
                workspace_num=10,
            ),
        )

        with (
            _isolate_ledger(tmp_path),
            patch(
                "sase_github.scripts.gh_setup.resolve_ref",
                return_value=_resolved(project_file),
            ),
            patch.dict(os.environ, {"SASE_GH_PRE_ALLOCATED": "0"}),
            patch(
                "sase_github.scripts.gh_setup.ensure_workspace_checkout",
                return_value=str(workspace_dir),
            ),
            patch("sase_github.scripts.gh_setup.materialize_sdd_store"),
        ):
            gh_setup.main(gh_ref="acme/widget", n=None, release=False)

        assert read_occupant_record(str(workspace_dir)) is not None

    def test_setup_proceeds_when_occupant_pid_is_dead(self, tmp_path: Path) -> None:
        project_file = _write_project_file(tmp_path)
        workspace_dir = tmp_path / "widget_10"
        workspace_dir.mkdir()
        write_occupant_record(
            str(workspace_dir),
            new_occupant_record(
                pid=_dead_pid(),
                workflow="stale-workflow",
                project="gh_acme__widget",
                workspace_num=10,
            ),
        )

        with (
            _isolate_ledger(tmp_path),
            patch(
                "sase_github.scripts.gh_setup.resolve_ref",
                return_value=_resolved(project_file),
            ),
            patch.dict(os.environ, {"SASE_GH_PRE_ALLOCATED": "0"}),
            patch(
                "sase_github.scripts.gh_setup.ensure_workspace_checkout",
                return_value=str(workspace_dir),
            ),
            patch("sase_github.scripts.gh_setup.materialize_sdd_store"),
        ):
            gh_setup.main(gh_ref="acme/widget", n=None, release=False)

        record = read_occupant_record(str(workspace_dir))
        assert record is not None
        assert record.pid == os.getppid()

    def test_setup_releases_its_claim_when_the_guard_refuses(
        self, tmp_path: Path
    ) -> None:
        # `setup` failing means the workflow's `release` step never runs, so
        # the slot this run claimed moments earlier has to go back here or it
        # stays held for the lifetime of the executor process.
        project_file = _write_project_file(tmp_path)
        workspace_dir = tmp_path / "widget_10"
        workspace_dir.mkdir()
        write_occupant_record(
            str(workspace_dir),
            new_occupant_record(
                pid=os.getpid(),
                workflow="gh-acme/widget",
                project="gh_acme__widget",
                workspace_num=10,
                agent_name="rival-agent",
            ),
        )

        with (
            _isolate_ledger(tmp_path),
            patch(
                "sase_github.scripts.gh_setup.resolve_ref",
                return_value=_resolved(project_file),
            ),
            patch.dict(os.environ, {"SASE_GH_PRE_ALLOCATED": "0"}),
            patch(
                "sase_github.scripts.gh_setup.ensure_workspace_checkout",
                return_value=str(workspace_dir),
            ),
            patch("sase_github.scripts.gh_setup.materialize_sdd_store"),
            pytest.raises(WorkspaceOccupiedError, match="rival-agent"),
        ):
            gh_setup.main(gh_ref="acme/widget", n=None, release=False)

        assert get_claimed_workspaces(project_file) == []

    def test_pre_allocated_guard_refusal_releases_nothing(self, tmp_path: Path) -> None:
        # The launcher owns the pre-allocated claim; a refusal here must not
        # hand its slot back out from under it.
        launcher_claim = WorkspaceClaim(10, "ace(run)-launcher", "feature", pid=11111)
        project_file = _write_project_file(tmp_path, running_claims=[launcher_claim])
        workspace_dir = tmp_path / "widget_10"
        workspace_dir.mkdir()
        write_occupant_record(
            str(workspace_dir),
            new_occupant_record(
                pid=os.getpid(),
                workflow="ace(run)-rival",
                project="gh_acme__widget",
                workspace_num=10,
                agent_name="rival-agent",
            ),
        )

        with (
            _isolate_ledger(tmp_path),
            patch(
                "sase_github.scripts.gh_setup.resolve_ref",
                return_value=_resolved(project_file),
            ),
            patch.dict(
                os.environ,
                {
                    "SASE_GH_PRE_ALLOCATED": "1",
                    "SASE_GH_WORKSPACE_NUM": "10",
                    "SASE_GH_WORKSPACE_DIR": str(workspace_dir),
                },
            ),
            patch("sase_github.scripts.gh_setup.materialize_sdd_store"),
            pytest.raises(WorkspaceOccupiedError, match="rival-agent"),
        ):
            gh_setup.main(gh_ref="acme/widget", n=None, release=False)

        assert [
            claim.workspace_num for claim in get_claimed_workspaces(project_file)
        ] == [10]


class TestWsSubmitAtomicClaim:
    def test_submit_acquires_through_claim_next_dir_and_releases(
        self, tmp_path: Path
    ) -> None:
        patch_file = _write_project_file(tmp_path)
        plugin = GitHubWorkspacePlugin()
        provider = MagicMock()
        provider.resolve_revision.return_value = "feat-branch"
        provider.checkout.return_value = (False, "checkout failed")
        claim_next = MagicMock(return_value=(12, "/work/widget_12", "widget_12"))
        release = MagicMock()

        with (
            patch(
                "sase.workspace_provider.detect_workflow_type",
                return_value="gh",
            ),
            patch(
                "sase_github.workspace_plugin.find_all_patches",
                return_value=[SimpleNamespace(name="feat-branch", pr_url=None)],
            ),
            patch("sase.ace.hooks.processes.kill_and_persist_all_running_processes"),
            patch("sase.ace.operations.has_active_children", return_value=False),
            patch(
                "sase_github.workspace_plugin.parse_workspace_dir",
                return_value="/work/widget/",
            ),
            patch(
                "sase.running_field.claim_next_axe_workspace_dir",
                claim_next,
            ),
            patch("sase.running_field.release_workspace", release),
            patch("sase.vcs_provider.get_vcs_provider", return_value=provider),
        ):
            ok, error = plugin.ws_submit(patch_file, "feat-branch", "widget")

        assert ok is False
        assert error == "Failed to checkout branch: checkout failed"
        claim_next.assert_called_once_with(
            patch_file,
            "submit-feat-branch",
            os.getpid(),
            "widget",
            cl_name="feat-branch",
            caller_tag="gh-submit",
        )
        release.assert_called_once_with(
            patch_file,
            12,
            "submit-feat-branch",
            "feat-branch",
            caller_tag="gh-submit",
        )

    def test_submit_claim_error_does_not_release(self, tmp_path: Path) -> None:
        patch_file = _write_project_file(tmp_path)
        plugin = GitHubWorkspacePlugin()
        release = MagicMock()

        with (
            patch(
                "sase.workspace_provider.detect_workflow_type",
                return_value="gh",
            ),
            patch(
                "sase_github.workspace_plugin.find_all_patches",
                return_value=[SimpleNamespace(name="feat-branch", pr_url=None)],
            ),
            patch("sase.ace.hooks.processes.kill_and_persist_all_running_processes"),
            patch("sase.ace.operations.has_active_children", return_value=False),
            patch(
                "sase_github.workspace_plugin.parse_workspace_dir",
                return_value="/work/widget/",
            ),
            patch(
                "sase.running_field.claim_next_axe_workspace_dir",
                side_effect=WorkspaceClaimError("all workspaces claimed"),
            ),
            patch("sase.running_field.release_workspace", release),
        ):
            ok, error = plugin.ws_submit(patch_file, "feat-branch", "widget")

        assert ok is False
        assert error is not None
        assert "Failed to claim workspace" in error
        assert "all workspaces claimed" in error
        release.assert_not_called()

    def test_submit_ledger_records_carry_gh_submit_tag(self, tmp_path: Path) -> None:
        patch_file = _write_project_file(tmp_path)
        ledger_file = str(tmp_path / "workspace_claims.jsonl")
        plugin = GitHubWorkspacePlugin()
        provider = MagicMock()
        provider.resolve_revision.return_value = "feat-branch"
        provider.checkout.return_value = (False, "checkout failed")

        with (
            patch("sase.logs.workspace_claim_ledger.LEDGER_FILE", ledger_file),
            patch(
                "sase.workspace_provider.detect_workflow_type",
                return_value="gh",
            ),
            patch(
                "sase_github.workspace_plugin.find_all_patches",
                return_value=[SimpleNamespace(name="feat-branch", pr_url=None)],
            ),
            patch("sase.ace.hooks.processes.kill_and_persist_all_running_processes"),
            patch("sase.ace.operations.has_active_children", return_value=False),
            patch(
                "sase_github.workspace_plugin.parse_workspace_dir",
                return_value="/work/widget/",
            ),
            patch(
                "sase.running_field._workspace.get_workspace_directory_for_num",
                return_value=("/work/widget_10", "widget_10"),
            ),
            patch("sase.vcs_provider.get_vcs_provider", return_value=provider),
        ):
            ok, error = plugin.ws_submit(patch_file, "feat-branch", "widget")
            records = read_ledger_records(ledger_file=ledger_file)

        assert ok is False
        assert error == "Failed to checkout branch: checkout failed"
        assert records
        assert all(record["caller_tag"] == "gh-submit" for record in records)
        operations = {record["operation"] for record in records}
        assert "claim_next_axe" in operations
        assert "release" in operations

    def test_submit_refuses_checkout_occupied_by_other_live_pid(
        self, tmp_path: Path
    ) -> None:
        patch_file = _write_project_file(tmp_path)
        ws_dir = tmp_path / "widget_12"
        ws_dir.mkdir()
        other_pid = os.getppid()
        write_occupant_record(
            str(ws_dir),
            new_occupant_record(
                pid=other_pid,
                workflow="w",
                project="widget",
                workspace_num=12,
                agent_name="rival-agent",
            ),
        )
        plugin = GitHubWorkspacePlugin()
        provider = MagicMock()
        claim_next = MagicMock(return_value=(12, str(ws_dir), "widget_12"))
        release = MagicMock()

        with (
            patch(
                "sase.workspace_provider.detect_workflow_type",
                return_value="gh",
            ),
            patch(
                "sase_github.workspace_plugin.find_all_patches",
                return_value=[SimpleNamespace(name="feat-branch", pr_url=None)],
            ),
            patch("sase.ace.hooks.processes.kill_and_persist_all_running_processes"),
            patch("sase.ace.operations.has_active_children", return_value=False),
            patch(
                "sase_github.workspace_plugin.parse_workspace_dir",
                return_value="/work/widget/",
            ),
            patch(
                "sase.running_field.claim_next_axe_workspace_dir",
                claim_next,
            ),
            patch("sase.running_field.release_workspace", release),
            patch("sase.vcs_provider.get_vcs_provider", return_value=provider),
        ):
            ok, error = plugin.ws_submit(patch_file, "feat-branch", "widget")

        assert ok is False
        assert error is not None
        assert "Refusing checkout" in error
        assert "rival-agent" in error
        provider.checkout.assert_not_called()
        release.assert_called_once_with(
            patch_file,
            12,
            "submit-feat-branch",
            "feat-branch",
            caller_tag="gh-submit",
        )


def test_gh_release_step_tags_caller() -> None:
    text = (ROOT / "src" / "sase_github" / "xprompts" / "gh.yml").read_text(
        encoding="utf-8"
    )
    assert 'caller_tag="gh-release"' in text


def test_gh_release_step_clears_occupant_record() -> None:
    text = (ROOT / "src" / "sase_github" / "xprompts" / "gh.yml").read_text(
        encoding="utf-8"
    )
    assert "clear_occupant_record(" in text
    assert "setup.workspace_dir" in text.split("clear_occupant_record(", 1)[1]
