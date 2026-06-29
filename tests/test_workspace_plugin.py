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


def _write_project(home: Path, project_name: str, content: str) -> Path:
    project_dir = home / ".sase" / "projects" / project_name
    project_dir.mkdir(parents=True, exist_ok=True)
    project_file = project_dir / f"{project_name}.sase"
    project_file.write_text(content, encoding="utf-8")
    return project_file


def _github_workspace(home: Path, user: str, project: str) -> str:
    workspace = home / "projects" / "github" / user / project
    workspace.mkdir(parents=True, exist_ok=True)
    return str(workspace) + "/"


def _home_patches(home: Path) -> tuple[object, object]:
    return (
        patch("sase_github.workspace_plugin.Path.home", return_value=home),
        patch.dict(os.environ, {"SASE_HOME": str(home / ".sase")}),
    )


class TestResolveGhRef:
    @patch(
        "sase_github.workspace_plugin.get_default_branch", return_value="origin/main"
    )
    def test_repo_path_creates_canonical_project_and_name(
        self, mock_branch: MagicMock
    ) -> None:
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            primary = _github_workspace(home, "alice", "myrepo")
            path_patch, env_patch = _home_patches(home)
            with path_patch, env_patch:
                result = resolve_gh_ref("alice/myrepo")

            project_file = (
                home
                / ".sase"
                / "projects"
                / "gh_alice__myrepo"
                / "gh_alice__myrepo.sase"
            )
            content = project_file.read_text(encoding="utf-8")
            assert result.project_name == "gh_alice__myrepo"
            assert result.project_file == str(project_file)
            assert result.primary_workspace_dir == primary
            assert result.checkout_target == "origin/main"
            assert f"WORKSPACE_DIR: {primary}\n" in content
            assert "PROJECT_NAME: myrepo\n" in content
            assert "PROJECT_ALIASES" not in content

    @patch(
        "sase_github.workspace_plugin.get_default_branch", return_value="origin/main"
    )
    def test_duplicate_repo_basename_gets_distinct_name(
        self, mock_branch: MagicMock
    ) -> None:
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            _github_workspace(home, "foo-org", "foo")
            _github_workspace(home, "bar-org", "foo")
            path_patch, env_patch = _home_patches(home)
            with path_patch, env_patch:
                first = resolve_gh_ref("foo-org/foo")
                second = resolve_gh_ref("bar-org/foo")

            first_file = Path(first.project_file)
            second_file = Path(second.project_file)
            assert first.project_name == "gh_foo-org__foo"
            assert second.project_name == "gh_bar-org__foo"
            assert "PROJECT_NAME: foo\n" in first_file.read_text(encoding="utf-8")
            assert "PROJECT_NAME: foo_1\n" in second_file.read_text(encoding="utf-8")

    @patch(
        "sase_github.workspace_plugin.get_default_branch", return_value="origin/main"
    )
    def test_repo_path_reuses_legacy_basename_project(
        self, mock_branch: MagicMock
    ) -> None:
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            primary = _github_workspace(home, "alice", "myrepo")
            project_file = _write_project(
                home,
                "myrepo",
                f"WORKSPACE_DIR: {primary}\nNAME: legacy\n",
            )
            path_patch, env_patch = _home_patches(home)
            with path_patch, env_patch:
                result = resolve_gh_ref("alice/myrepo")

            content = project_file.read_text(encoding="utf-8")
            assert result.project_name == "myrepo"
            assert result.project_file == str(project_file)
            assert "PROJECT_ALIASES" not in content

    @patch(
        "sase_github.workspace_plugin.get_default_branch", return_value="origin/main"
    )
    def test_repo_path_reuses_existing_auto_aliased_project_without_migration(
        self, mock_branch: MagicMock
    ) -> None:
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            primary = _github_workspace(home, "alice", "myrepo")
            project_file = _write_project(
                home,
                "gh_alice__myrepo",
                f"WORKSPACE_DIR: {primary}\nPROJECT_ALIASES: myrepo\nNAME: legacy\n",
            )
            path_patch, env_patch = _home_patches(home)
            with path_patch, env_patch:
                result = resolve_gh_ref("alice/myrepo")

            content = project_file.read_text(encoding="utf-8")
            assert result.project_name == "gh_alice__myrepo"
            assert result.project_file == str(project_file)
            assert "PROJECT_ALIASES: myrepo\n" in content
            assert "PROJECT_NAME" not in content

    @patch(
        "sase_github.workspace_plugin.get_default_branch", return_value="origin/main"
    )
    def test_repo_path_duplicate_basename_no_longer_conflicts(
        self, mock_branch: MagicMock
    ) -> None:
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            primary = _github_workspace(home, "alice", "foo")
            _write_project(
                home,
                "foo",
                "WORKSPACE_DIR: /some/other/path/\nNAME: other\n",
            )
            path_patch, env_patch = _home_patches(home)
            with path_patch, env_patch:
                result = resolve_gh_ref("alice/foo")

            content = Path(result.project_file).read_text(encoding="utf-8")
            assert result.project_name == "gh_alice__foo"
            assert result.primary_workspace_dir == primary
            assert "PROJECT_NAME: foo_1\n" in content
            assert "PROJECT_ALIASES" not in content

    @patch(
        "sase_github.workspace_plugin.get_default_branch", return_value="origin/main"
    )
    def test_repo_path_suffixes_occupied_canonical_project_name(
        self, mock_branch: MagicMock
    ) -> None:
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            _github_workspace(home, "alice", "foo")
            _write_project(
                home,
                "gh_alice__foo",
                "WORKSPACE_DIR: /some/other/path/\nNAME: other\n",
            )
            path_patch, env_patch = _home_patches(home)
            with path_patch, env_patch:
                result = resolve_gh_ref("alice/foo")

            assert result.project_name == "gh_alice__foo-2"
            assert "PROJECT_NAME: foo\n" in Path(result.project_file).read_text(
                encoding="utf-8"
            )

    @patch(
        "sase_github.workspace_plugin.get_default_branch", return_value="origin/main"
    )
    def test_project_name_shorthand_resolves_canonical_project(
        self, mock_branch: MagicMock
    ) -> None:
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            _github_workspace(home, "foo-org", "foo")
            _github_workspace(home, "bar-org", "foo")
            path_patch, env_patch = _home_patches(home)
            with path_patch, env_patch:
                resolve_gh_ref("foo-org/foo")
                canonical = resolve_gh_ref("bar-org/foo")
                alias = resolve_gh_ref("foo_1")

            assert alias.project_name == canonical.project_name
            assert alias.project_file == canonical.project_file
            assert alias.primary_workspace_dir == canonical.primary_workspace_dir

    @patch(
        "sase_github.workspace_plugin.get_default_branch", return_value="origin/main"
    )
    def test_project_shorthand(self, mock_branch: MagicMock) -> None:
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            path_patch, env_patch = _home_patches(home)
            with path_patch, env_patch:
                proj_dir = os.path.join(d, ".sase", "projects", "myproj")
                os.makedirs(proj_dir)
                spec = os.path.join(proj_dir, "myproj.sase")
                with open(spec, "w") as f:
                    f.write("WORKSPACE_DIR: /work/myproj/\nNAME: cl\n")

                result = resolve_gh_ref("myproj")
                assert result.project_name == "myproj"
                assert result.primary_workspace_dir == "/work/myproj/"
                assert result.checkout_target == "origin/main"

    @patch(
        "sase_github.workspace_plugin.get_default_branch", return_value="origin/main"
    )
    def test_project_shorthand_legacy_gp_fallback(self, mock_branch: MagicMock) -> None:
        """Legacy ``.gp`` project spec is still resolvable when no ``.sase`` exists."""
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            path_patch, env_patch = _home_patches(home)
            with path_patch, env_patch:
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
            spec = os.path.join(d, "proj.sase")
            with open(spec, "w") as f:
                f.write("WORKSPACE_DIR: /work/proj/\nNAME: my-feature\n")

            cs = MagicMock()
            cs.name = "my-feature"
            cs.file_path = spec
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
        with tempfile.NamedTemporaryFile(mode="w", suffix=".sase", delete=False) as f:
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
        with tempfile.NamedTemporaryFile(mode="w", suffix=".sase", delete=False) as f:
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
            spec = os.path.join(d, "proj.sase")
            with open(spec, "w") as f:
                f.write(
                    f"WORKSPACE_DIR: {workspace}\n"
                    "BARE_REPO_DIR: /repos/proj.git\n"
                    "NAME: cl\n"
                )
            assert plugin.ws_detect_workflow_type(project_file=spec) is None

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
            spec = os.path.join(d, "proj.sase")
            with open(spec, "w") as f:
                f.write(f"WORKSPACE_DIR: {workspace}\nNAME: cl\n")
            assert plugin.ws_detect_workflow_type(project_file=spec) is None

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
            spec = os.path.join(d, "proj.sase")
            with open(spec, "w") as f:
                f.write(f"WORKSPACE_DIR: {workspace}\nNAME: cl\n")
            assert plugin.ws_detect_workflow_type(project_file=spec) == "gh"
