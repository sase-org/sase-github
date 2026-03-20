"""Tests for sase_github.workspace_plugin module (GitHub-specific functions)."""

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from sase_github.workspace_plugin import (
    GitHubWorkspacePlugin,
    resolve_gh_ref,
)


class TestResolveGhRef:
    @patch(
        "sase_github.workspace_plugin.get_default_branch", return_value="origin/main"
    )
    @patch("sase_github.workspace_plugin.set_workspace_dir", return_value=True)
    @patch("sase_github.workspace_plugin.parse_workspace_dir", return_value=None)
    @patch("sase_github.workspace_plugin.os.path.isdir", return_value=True)
    def test_repo_path(
        self,
        mock_isdir: MagicMock,
        mock_parse: MagicMock,
        mock_set: MagicMock,
        mock_branch: MagicMock,
    ) -> None:
        result = resolve_gh_ref("alice/myrepo")
        assert result.project_name == "myrepo"
        assert result.checkout_target == "origin/main"
        assert "alice/myrepo" in result.primary_workspace_dir
        mock_set.assert_called_once()

    @patch(
        "sase_github.workspace_plugin.get_default_branch", return_value="origin/main"
    )
    @patch("sase_github.workspace_plugin.set_workspace_dir", return_value=True)
    @patch("sase_github.workspace_plugin.parse_workspace_dir")
    def test_repo_path_conflict(
        self,
        mock_parse: MagicMock,
        mock_set: MagicMock,
        mock_branch: MagicMock,
    ) -> None:
        mock_parse.return_value = "/some/other/path/"
        with pytest.raises(ValueError, match="WORKSPACE_DIR conflict"):
            resolve_gh_ref("alice/myrepo")

    @patch(
        "sase_github.workspace_plugin.get_default_branch", return_value="origin/main"
    )
    def test_project_shorthand(self, mock_branch: MagicMock) -> None:
        with tempfile.TemporaryDirectory() as d:
            with patch("sase_github.workspace_plugin.Path.home", return_value=Path(d)):
                proj_dir = os.path.join(d, ".sase", "projects", "myproj")
                os.makedirs(proj_dir)
                gp = os.path.join(proj_dir, "myproj.gp")
                with open(gp, "w") as f:
                    f.write("WORKSPACE_DIR: /work/myproj/\nNAME: cl\n")

                result = resolve_gh_ref("myproj")
                assert result.project_name == "myproj"
                assert result.primary_workspace_dir == "/work/myproj/"
                assert result.checkout_target == "origin/main"

    @patch(
        "sase_github.workspace_plugin.get_default_branch", return_value="origin/main"
    )
    @patch("sase.ace.changespec.find_all_changespecs")
    def test_changespec_name(
        self,
        mock_find: MagicMock,
        mock_branch: MagicMock,
    ) -> None:
        with tempfile.TemporaryDirectory() as d:
            gp = os.path.join(d, "proj.gp")
            with open(gp, "w") as f:
                f.write("WORKSPACE_DIR: /work/proj/\nNAME: my-feature\n")

            cs = MagicMock()
            cs.name = "my-feature"
            cs.file_path = gp
            cs.project_basename = "proj"
            mock_find.return_value = [cs]

            # Need to fail mode 2 (project shorthand) first
            with patch(
                "sase_github.workspace_plugin.Path.home",
                return_value=Path("/nonexistent"),
            ):
                result = resolve_gh_ref("my-feature")
                assert result.checkout_target == "origin/my-feature"
                assert result.project_name == "proj"

    @patch("sase.ace.changespec.find_all_changespecs")
    def test_changespec_no_workspace_dir(self, mock_find: MagicMock) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".gp", delete=False) as f:
            f.write("NAME: my-feature\n")
            f.flush()

            cs = MagicMock()
            cs.name = "my-feature"
            cs.file_path = f.name
            cs.project_basename = "proj"
            mock_find.return_value = [cs]

            with patch(
                "sase_github.workspace_plugin.Path.home",
                return_value=Path("/nonexistent"),
            ):
                with pytest.raises(ValueError, match="WORKSPACE_DIR is not set"):
                    resolve_gh_ref("my-feature")
            os.unlink(f.name)

    @patch("sase.ace.changespec.find_all_changespecs", return_value=[])
    def test_not_found(self, mock_find: MagicMock) -> None:
        with patch(
            "sase_github.workspace_plugin.Path.home",
            return_value=Path("/nonexistent"),
        ):
            with pytest.raises(ValueError, match="Cannot resolve"):
                resolve_gh_ref("unknown-thing")

    def test_invalid_repo_path(self) -> None:
        with pytest.raises(ValueError, match="expected 'user/project'"):
            resolve_gh_ref("a/b/c")


# ── detect_workflow_type (via plugin) ────────────────────────────────


class TestDetectWorkflowTypeForProject:
    def test_hg_no_git(self) -> None:
        """Returns None when no WORKSPACE_DIR or no .git directory."""
        plugin = GitHubWorkspacePlugin()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".gp", delete=False) as f:
            f.write("NAME: cl\n")
            f.flush()
            assert plugin.ws_detect_workflow_type(project_file=f.name) is None
            os.unlink(f.name)

    @patch("sase_github.workspace_plugin.subprocess.run")
    def test_git_bare_repo_dir_set(self, mock_run: MagicMock) -> None:
        """Returns None when BARE_REPO_DIR is set in project file."""
        plugin = GitHubWorkspacePlugin()
        with tempfile.TemporaryDirectory() as d:
            workspace = os.path.join(d, "repo")
            os.makedirs(os.path.join(workspace, ".git"))
            gp = os.path.join(d, "proj.gp")
            with open(gp, "w") as f:
                f.write(
                    f"WORKSPACE_DIR: {workspace}\n"
                    "BARE_REPO_DIR: /repos/proj.git\n"
                    "NAME: cl\n"
                )
            assert plugin.ws_detect_workflow_type(project_file=gp) is None

    @patch("sase_github.workspace_plugin.subprocess.run")
    def test_git_local_origin_url(self, mock_run: MagicMock) -> None:
        """Returns None when origin remote URL is a local path."""
        mock_run.return_value = MagicMock(
            returncode=0, stdout="/home/user/repos/proj.git\n"
        )
        plugin = GitHubWorkspacePlugin()
        with tempfile.TemporaryDirectory() as d:
            workspace = os.path.join(d, "repo")
            os.makedirs(os.path.join(workspace, ".git"))
            gp = os.path.join(d, "proj.gp")
            with open(gp, "w") as f:
                f.write(f"WORKSPACE_DIR: {workspace}\nNAME: cl\n")
            assert plugin.ws_detect_workflow_type(project_file=gp) is None

    @patch("sase_github.workspace_plugin.subprocess.run")
    def test_gh_github_origin_url(self, mock_run: MagicMock) -> None:
        """Returns 'gh' when origin remote URL is a GitHub URL."""
        mock_run.return_value = MagicMock(
            returncode=0, stdout="https://github.com/user/repo.git\n"
        )
        plugin = GitHubWorkspacePlugin()
        with tempfile.TemporaryDirectory() as d:
            workspace = os.path.join(d, "repo")
            os.makedirs(os.path.join(workspace, ".git"))
            gp = os.path.join(d, "proj.gp")
            with open(gp, "w") as f:
                f.write(f"WORKSPACE_DIR: {workspace}\nNAME: cl\n")
            assert plugin.ws_detect_workflow_type(project_file=gp) == "gh"
