"""Tests for sase_github.workspace_plugin module (GitHub-specific functions)."""

import json
import os
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sase.workspace_provider import ResolvedRef, SUBMITTED_CHECK_EXIT_CODE_CLOSED

from sase_github.workspace_plugin import (
    GitHubWorkspacePlugin,
    _clone_gh_repo,
    _extract_pr_number,
    _github_workspace_dir,
    _list_active_project_records,
    peek_gh_ref,
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


def _run_submitted_check_script(
    tmp_path: Path, gh_body: str
) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(f"#!/bin/bash\n{gh_body}\n", encoding="utf-8")
    gh.chmod(0o755)

    script = GitHubWorkspacePlugin().ws_generate_submitted_check_script("42", "git")
    assert script is not None

    script_path = tmp_path / "check.sh"
    script_path.write_text(f"#!/bin/bash\n{script}\n", encoding="utf-8")
    script_path.chmod(0o755)

    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
    }
    return subprocess.run(
        [str(script_path)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _completed_gh_repo_list(
    *,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["gh", "repo", "list"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def _completed(
    *,
    args: list[str] | None = None,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=args or ["cmd"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


@pytest.fixture(autouse=True)
def _default_github_host() -> object:
    with patch("sase_github.config.get_default_github_host", return_value="github.com"):
        yield


@pytest.mark.parametrize(
    ("gh_body", "expected_code", "expected_state"),
    [
        ("printf 'MERGED\\n'", 0, "MERGED"),
        ("printf 'CLOSED\\n'", SUBMITTED_CHECK_EXIT_CODE_CLOSED, "CLOSED"),
        ("printf 'OPEN\\n'", 1, "OPEN"),
        ("exit 2", 1, "<unavailable>"),
    ],
)
def test_submitted_check_script_reports_pr_state(
    tmp_path: Path,
    gh_body: str,
    expected_code: int,
    expected_state: str,
) -> None:
    result = _run_submitted_check_script(tmp_path, gh_body)

    assert result.returncode == expected_code
    assert f"PR state: {expected_state}" in result.stdout


def test_submitted_check_script_has_no_bare_exit_statement() -> None:
    script = GitHubWorkspacePlugin().ws_generate_submitted_check_script("42", "git")
    assert script is not None

    for line in script.splitlines():
        stripped = line.strip()
        assert stripped != "exit"
        assert not stripped.startswith("exit ")


def test_submitted_check_closed_literal_matches_sase_contract() -> None:
    script = GitHubWorkspacePlugin().ws_generate_submitted_check_script("42", "git")
    assert script is not None
    assert f"(exit {SUBMITTED_CHECK_EXIT_CODE_CLOSED})" in script


class TestHostAwareWorkspace:
    def test_github_com_workspace_path_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            with patch("sase_github.workspace_plugin.Path.home", return_value=home):
                assert _github_workspace_dir("alice", "repo", host="github.com") == (
                    str(home / "projects" / "github" / "alice" / "repo") + "/"
                )

    def test_enterprise_workspace_path_is_namespaced_by_host(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            with patch("sase_github.workspace_plugin.Path.home", return_value=home):
                assert _github_workspace_dir(
                    "alice",
                    "repo",
                    host="github.enterprise.test",
                ) == (
                    str(
                        home
                        / "projects"
                        / "github"
                        / "github.enterprise.test"
                        / "alice"
                        / "repo"
                    )
                    + "/"
                )

    def test_clone_uses_enterprise_ssh_url_first(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            target = str(Path(d) / "repo")
            with patch("sase_github.workspace_plugin.subprocess.run") as mock_run:
                _clone_gh_repo(
                    "alice",
                    "repo",
                    target,
                    host="github.enterprise.test",
                )

        assert mock_run.call_args[0][0] == [
            "git",
            "clone",
            "git@github.enterprise.test:alice/repo.git",
            target,
        ]
        assert mock_run.call_args.kwargs["stdin"] is subprocess.DEVNULL
        assert mock_run.call_args.kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"

    def test_clone_uses_ssh_url_form_when_host_has_port(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            target = str(Path(d) / "repo")
            with patch("sase_github.workspace_plugin.subprocess.run") as mock_run:
                _clone_gh_repo(
                    "alice",
                    "repo",
                    target,
                    host="github.enterprise.test:2222",
                )

        assert mock_run.call_args[0][0] == [
            "git",
            "clone",
            "ssh://git@github.enterprise.test:2222/alice/repo.git",
            target,
        ]

    def test_clone_falls_back_to_https_after_ssh_failure(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            target = str(Path(d) / "repo")
            ssh_failure = subprocess.CalledProcessError(
                128,
                ["git", "clone"],
                stderr="ssh denied",
            )
            with patch(
                "sase_github.workspace_plugin.subprocess.run",
                side_effect=[ssh_failure, MagicMock(returncode=0)],
            ) as mock_run:
                _clone_gh_repo(
                    "alice",
                    "repo",
                    target,
                    host="github.enterprise.test",
                )

        assert mock_run.call_args_list[0][0][0] == [
            "git",
            "clone",
            "git@github.enterprise.test:alice/repo.git",
            target,
        ]
        assert mock_run.call_args_list[1][0][0] == [
            "git",
            "clone",
            "https://github.enterprise.test/alice/repo.git",
            target,
        ]

    def test_clone_both_fail_error_includes_both_attempts(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            target = str(Path(d) / "repo")
            ssh_failure = subprocess.CalledProcessError(
                128,
                ["git", "clone"],
                stderr="ssh denied",
            )
            https_failure = subprocess.CalledProcessError(
                128,
                ["git", "clone"],
                stderr="https denied",
            )
            with patch(
                "sase_github.workspace_plugin.subprocess.run",
                side_effect=[ssh_failure, https_failure],
            ):
                with pytest.raises(RuntimeError) as exc_info:
                    _clone_gh_repo(
                        "alice",
                        "repo",
                        target,
                        host="github.enterprise.test",
                    )

        message = str(exc_info.value)
        assert "SSH clone" in message
        assert "ssh denied" in message
        assert "HTTPS clone" in message
        assert "https denied" in message

    @patch(
        "sase_github.workspace_plugin.get_default_branch", return_value="origin/main"
    )
    def test_repo_path_uses_default_enterprise_host(
        self,
        mock_branch: MagicMock,
    ) -> None:
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            path_patch, env_patch = _home_patches(home)
            with (
                path_patch,
                env_patch,
                patch(
                    "sase_github.config.get_default_github_host",
                    return_value="github.enterprise.test",
                ),
                patch("sase_github.workspace_plugin.subprocess.run") as mock_run,
            ):
                result = resolve_gh_ref("alice/repo")

        expected = (
            str(
                home
                / "projects"
                / "github"
                / "github.enterprise.test"
                / "alice"
                / "repo"
            )
            + "/"
        )
        assert result.primary_workspace_dir == expected
        assert mock_run.call_args[0][0] == [
            "git",
            "clone",
            "git@github.enterprise.test:alice/repo.git",
            expected.rstrip("/"),
        ]


class TestRepoCandidateCompletion:
    def test_does_not_claim_other_workflow_types(self) -> None:
        assert GitHubWorkspacePlugin().ws_list_repo_candidates("git", "alice") is None

    def test_nested_namespace_is_unsupported(self) -> None:
        result = GitHubWorkspacePlugin().ws_list_repo_candidates("gh", "group/sub")

        assert result is not None
        assert result.status == "error"
        assert result.error_kind == "unsupported_namespace"
        assert result.provider_display == "GitHub"
        assert result.entries == ()

    def test_maps_gh_repo_list_fields(self) -> None:
        payload = [
            {
                "name": "sase",
                "description": "Structured agents",
                "visibility": "PRIVATE",
                "isArchived": False,
                "isFork": True,
                "pushedAt": "2026-07-07T17:00:00Z",
            },
            {
                "name": "empty",
                "description": None,
                "visibility": "PUBLIC",
                "isArchived": True,
                "isFork": False,
                "pushedAt": None,
            },
        ]

        with (
            patch(
                "sase_github.workspace_plugin._repo_completion_limit",
                return_value=2,
            ),
            patch(
                "sase_github.workspace_plugin.subprocess.run",
                return_value=_completed_gh_repo_list(stdout=json.dumps(payload)),
            ) as mock_run,
        ):
            result = GitHubWorkspacePlugin().ws_list_repo_candidates("gh", "alice")

        assert result is not None
        assert result.status == "ok"
        assert result.provider_display == "GitHub"
        assert len(result.entries) == 2
        assert result.entries[0].name == "sase"
        assert result.entries[0].ref == "alice/sase"
        assert result.entries[0].description == "Structured agents"
        assert result.entries[0].visibility == "private"
        assert result.entries[0].is_fork is True
        assert result.entries[0].is_archived is False
        assert result.entries[0].pushed_at == "2026-07-07T17:00:00Z"
        assert result.entries[1].description == ""
        assert result.entries[1].visibility == "public"
        assert result.entries[1].is_archived is True
        assert result.entries[1].pushed_at is None
        assert mock_run.call_args[0][0] == [
            "gh",
            "repo",
            "list",
            "alice",
            "--json",
            "name,description,visibility,isArchived,isFork,pushedAt",
            "--limit",
            "2",
        ]
        assert mock_run.call_args.kwargs["stdin"] is subprocess.DEVNULL
        assert mock_run.call_args.kwargs["env"]["GH_PROMPT_DISABLED"] == "1"
        assert mock_run.call_args.kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"

    def test_uses_default_github_host_for_gh_host_env(self) -> None:
        with (
            patch(
                "sase_github.config.get_default_github_host",
                return_value="github.enterprise.test",
            ),
            patch(
                "sase_github.workspace_plugin.subprocess.run",
                return_value=_completed_gh_repo_list(stdout="[]"),
            ) as mock_run,
        ):
            result = GitHubWorkspacePlugin().ws_list_repo_candidates("gh", "alice")

        assert result is not None
        assert result.status == "ok"
        assert mock_run.call_args.kwargs["env"]["GH_HOST"] == "github.enterprise.test"
        assert mock_run.call_args.kwargs["env"]["GH_PROMPT_DISABLED"] == "1"

    @pytest.mark.parametrize(
        ("side_effect", "return_value", "expected_kind", "expected_message"),
        [
            (FileNotFoundError(), None, "tool_missing", "install the gh CLI"),
            (
                subprocess.TimeoutExpired(["gh"], timeout=10),
                None,
                "network",
                "network error",
            ),
            (
                None,
                _completed_gh_repo_list(
                    returncode=1,
                    stderr="To get started with GitHub CLI, run: gh auth login",
                ),
                "auth",
                "gh auth login",
            ),
            (
                None,
                _completed_gh_repo_list(
                    returncode=1,
                    stderr="Could not resolve to a User with the login of 'alice'",
                ),
                "not_found",
                "not found",
            ),
            (
                None,
                _completed_gh_repo_list(
                    returncode=1,
                    stderr="dial tcp: lookup api.github.com: no such host",
                ),
                "network",
                "network error",
            ),
            (
                None,
                _completed_gh_repo_list(returncode=1, stderr="GraphQL failed"),
                "unknown",
                "GraphQL failed",
            ),
            (
                None,
                _completed_gh_repo_list(returncode=0, stdout="{"),
                "unknown",
                "unexpected gh output",
            ),
        ],
    )
    def test_error_mapping(
        self,
        side_effect: BaseException | None,
        return_value: subprocess.CompletedProcess[str] | None,
        expected_kind: str,
        expected_message: str,
    ) -> None:
        with patch(
            "sase_github.workspace_plugin.subprocess.run",
            side_effect=side_effect,
            return_value=return_value,
        ):
            result = GitHubWorkspacePlugin().ws_list_repo_candidates("gh", "alice")

        assert result is not None
        assert result.status == "error"
        assert result.error_kind == expected_kind
        assert expected_message in result.message
        assert result.provider_display == "GitHub"
        assert result.entries == ()


class TestRefNamespaceCompletion:
    def test_does_not_claim_other_workflow_types(self) -> None:
        assert GitHubWorkspacePlugin().ws_list_ref_namespaces("git") is None

    def test_lists_owners_from_active_canonical_project_records(self) -> None:
        records = [
            SimpleNamespace(project_name="gh_sase-org__sase"),
            SimpleNamespace(project_name="gh_bbugyi200__dotfiles"),
            SimpleNamespace(project_name="gh_sase-org__sase-core"),
            SimpleNamespace(project_name="sase"),
            SimpleNamespace(project_name="gh_missing_separator"),
            SimpleNamespace(project_name="gh___repo"),
            SimpleNamespace(project_name="gh_owner__"),
        ]

        with (
            patch(
                "sase_github.workspace_plugin._list_active_project_records",
                return_value=records,
            ),
            patch("sase_github.config.get_github_orgs", return_value=[]),
        ):
            result = GitHubWorkspacePlugin().ws_list_ref_namespaces("gh")

        assert result is not None
        assert [
            (entry.name, entry.description, entry.kind_label)
            for entry in result.entries
        ] == [
            ("bbugyi200", "1 active project", "org"),
            ("sase-org", "2 active projects", "org"),
        ]

    def test_unions_config_orgs_with_case_insensitive_dedupe(self) -> None:
        records = [SimpleNamespace(project_name="gh_sase-org__sase")]

        with (
            patch(
                "sase_github.workspace_plugin._list_active_project_records",
                return_value=records,
            ),
            patch(
                "sase_github.config.get_github_orgs",
                return_value=["SASE-ORG", "bbugyi200", ""],
            ),
        ):
            result = GitHubWorkspacePlugin().ws_list_ref_namespaces("gh")

        assert result is not None
        assert [(entry.name, entry.description) for entry in result.entries] == [
            ("bbugyi200", "from github_orgs"),
            ("sase-org", "1 active project"),
        ]

    def test_active_records_helper_filters_to_active_lifecycle(
        self, tmp_path: Path
    ) -> None:
        projects_base = tmp_path / "projects"
        projects_base.mkdir()

        with patch(
            "sase.core.project_lifecycle_facade.list_project_records",
            return_value=[],
        ) as list_records:
            assert _list_active_project_records(projects_base) == []

        list_records.assert_called_once_with(
            projects_base,
            ["active"],
            include_home=False,
        )

    def test_ref_namespace_listing_does_not_spawn_subprocesses(self) -> None:
        records = [SimpleNamespace(project_name="gh_sase-org__sase")]

        with (
            patch(
                "sase_github.workspace_plugin._list_active_project_records",
                return_value=records,
            ),
            patch("sase_github.config.get_github_orgs", return_value=["bbugyi200"]),
            patch(
                "sase_github.workspace_plugin.subprocess.run",
                side_effect=AssertionError("namespace completion must stay local"),
            ),
        ):
            result = GitHubWorkspacePlugin().ws_list_ref_namespaces("gh")

        assert result is not None
        assert [entry.name for entry in result.entries] == ["bbugyi200", "sase-org"]


class TestSddMaterialization:
    def test_metadata_declares_separate_repo_policy(self) -> None:
        metadata = GitHubWorkspacePlugin().ws_get_workflow_metadata()

        assert metadata is not None
        assert metadata.sdd_storage_policy == "separate_repo"

    def test_found_companion_repo_clones_and_returns_positive_record(
        self,
        tmp_path: Path,
    ) -> None:
        primary = tmp_path / "widget"
        checkout = tmp_path / "widget_2"
        primary.mkdir()
        checkout.mkdir()

        def run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            if cmd == ["git", "config", "--get", "remote.origin.url"]:
                return _completed(
                    stdout="https://github.enterprise.test/acme/widget.git\n"
                )
            if cmd[:3] == ["gh", "repo", "view"]:
                return _completed(stdout="widget-sdd\n")
            if cmd[:2] == ["git", "clone"]:
                Path(cmd[-1]).mkdir(parents=True)
                return _completed()
            raise AssertionError(f"unexpected command: {cmd}")

        with (
            patch(
                "sase_github.config.load_merged_config",
                return_value={"github_hosts": ["github.enterprise.test"]},
            ),
            patch(
                "sase_github.workspace_plugin._sdd_network_timeout",
                return_value=7.0,
            ),
            patch(
                "sase_github.workspace_plugin.subprocess.run", side_effect=run
            ) as mock_run,
        ):
            record = GitHubWorkspacePlugin().ws_materialize_sdd_store(
                str(primary),
                str(checkout),
                {},
            )

        assert record == {
            "schema_version": 1,
            "storage": "separate_repo",
            "provider": "github",
            "host": "github.enterprise.test",
            "repo": "acme/widget-sdd",
            "remote_url": "git@github.enterprise.test:acme/widget-sdd.git",
            "discovery": "found",
        }
        assert (primary / ".sase" / "sdd").is_dir()
        gh_call = mock_run.call_args_list[1]
        assert gh_call[0][0][:4] == ["gh", "repo", "view", "acme/widget-sdd"]
        assert gh_call.kwargs["env"]["GH_HOST"] == "github.enterprise.test"
        assert gh_call.kwargs["timeout"] == 7.0
        clone_call = mock_run.call_args_list[2]
        assert clone_call[0][0] == [
            "git",
            "clone",
            "git@github.enterprise.test:acme/widget-sdd.git",
            str(primary / ".sase" / f".sdd.clone-tmp-{os.getpid()}"),
        ]

    def test_not_found_probe_returns_negative_record(self, tmp_path: Path) -> None:
        primary = tmp_path / "widget"
        primary.mkdir()

        def run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            if cmd == ["git", "config", "--get", "remote.origin.url"]:
                return _completed(stdout="https://github.com/acme/widget.git\n")
            if cmd[:3] == ["gh", "repo", "view"]:
                return _completed(returncode=1, stderr="repository not found")
            raise AssertionError(f"unexpected command: {cmd}")

        with patch("sase_github.workspace_plugin.subprocess.run", side_effect=run):
            record = GitHubWorkspacePlugin().ws_materialize_sdd_store(
                str(primary),
                str(primary),
                {},
            )

        assert record is not None
        assert record["discovery"] == "not_found"
        assert record["repo"] == "acme/widget-sdd"
        assert not (primary / ".sase" / "sdd").exists()

    def test_transport_probe_failure_does_not_cache_negative_record(
        self,
        tmp_path: Path,
    ) -> None:
        primary = tmp_path / "widget"
        primary.mkdir()

        def run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            if cmd == ["git", "config", "--get", "remote.origin.url"]:
                return _completed(stdout="https://github.com/acme/widget.git\n")
            if cmd[:3] == ["gh", "repo", "view"]:
                return _completed(
                    returncode=1, stderr="lookup api.github.com: no such host"
                )
            raise AssertionError(f"unexpected command: {cmd}")

        with patch("sase_github.workspace_plugin.subprocess.run", side_effect=run):
            record = GitHubWorkspacePlugin().ws_materialize_sdd_store(
                str(primary),
                str(primary),
                {},
            )

        assert record is None
        assert not (primary / ".sase" / "sdd").exists()

    def test_existing_local_sdd_content_is_not_clobbered(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        primary = tmp_path / "widget"
        sdd_dir = primary / ".sase" / "sdd"
        sdd_dir.mkdir(parents=True)
        (sdd_dir / "README.md").write_text("local", encoding="utf-8")

        def run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            if cmd == ["git", "config", "--get", "remote.origin.url"]:
                return _completed(stdout="https://github.com/acme/widget.git\n")
            if cmd[:3] == ["gh", "repo", "view"]:
                return _completed(stdout="widget-sdd\n")
            if cmd[:2] == ["git", "clone"]:
                raise AssertionError("local SDD content must not be clobbered")
            raise AssertionError(f"unexpected command: {cmd}")

        with patch("sase_github.workspace_plugin.subprocess.run", side_effect=run):
            record = GitHubWorkspacePlugin().ws_materialize_sdd_store(
                str(primary),
                str(primary),
                {},
            )

        assert record is not None
        assert record["discovery"] == "not_found"
        assert (sdd_dir / "README.md").read_text(encoding="utf-8") == "local"
        assert "sase sdd migrate" in capsys.readouterr().err

    def test_existing_matching_sdd_remote_is_adopted(self, tmp_path: Path) -> None:
        primary = tmp_path / "widget"
        sdd_dir = primary / ".sase" / "sdd"
        (sdd_dir / ".git").mkdir(parents=True)

        def run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            if cmd == ["git", "config", "--get", "remote.origin.url"]:
                cwd = Path(str(kwargs["cwd"]))
                if cwd == primary:
                    return _completed(stdout="https://github.com/acme/widget.git\n")
                if cwd == sdd_dir:
                    return _completed(stdout="git@github.com:acme/widget-sdd.git\n")
            if cmd[:3] == ["gh", "repo", "view"]:
                return _completed(stdout="widget-sdd\n")
            if cmd[:2] == ["git", "clone"]:
                raise AssertionError("matching SDD remote should be adopted")
            raise AssertionError(f"unexpected command: {cmd}")

        with patch("sase_github.workspace_plugin.subprocess.run", side_effect=run):
            record = GitHubWorkspacePlugin().ws_materialize_sdd_store(
                str(primary),
                str(primary),
                {},
            )

        assert record is not None
        assert record["discovery"] == "found"
        assert record["remote_url"] == "git@github.com:acme/widget-sdd.git"

    def test_sdd_repo_name_override_can_choose_owner_and_name(
        self,
        tmp_path: Path,
    ) -> None:
        primary = tmp_path / "widget"
        primary.mkdir()

        def run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            if cmd == ["git", "config", "--get", "remote.origin.url"]:
                return _completed(stdout="https://github.com/acme/widget.git\n")
            if cmd[:3] == ["gh", "repo", "view"]:
                return _completed(stdout="custom-sdd\n")
            if cmd[:2] == ["git", "clone"]:
                Path(cmd[-1]).mkdir(parents=True)
                return _completed()
            raise AssertionError(f"unexpected command: {cmd}")

        with (
            patch(
                "sase_github.config.load_merged_config",
                return_value={"sdd": {"repo": {"name": "other/custom-sdd"}}},
            ),
            patch(
                "sase_github.workspace_plugin.subprocess.run", side_effect=run
            ) as mock_run,
        ):
            record = GitHubWorkspacePlugin().ws_materialize_sdd_store(
                str(primary),
                str(primary),
                {},
            )

        assert record is not None
        assert record["repo"] == "other/custom-sdd"
        assert mock_run.call_args_list[1][0][0][3] == "other/custom-sdd"
        assert mock_run.call_args_list[2][0][0][2] == (
            "git@github.com:other/custom-sdd.git"
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
            assert result.canonical_ref == "gh_alice__myrepo"
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
    def test_alias_resolves_to_canonical_ref_after_repo_path_first_use(
        self, mock_branch: MagicMock
    ) -> None:
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            _github_workspace(home, "alice", "myrepo")
            path_patch, env_patch = _home_patches(home)
            with path_patch, env_patch:
                first = resolve_gh_ref("alice/myrepo")
                alias = resolve_gh_ref("myrepo")

            assert alias.project_name == first.project_name
            assert alias.project_file == first.project_file
            assert alias.primary_workspace_dir == first.primary_workspace_dir
            assert alias.canonical_ref == first.project_name

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
            assert alias.canonical_ref == canonical.project_name

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
                assert result.canonical_ref is None

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
                assert result.canonical_ref is None

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
                assert result.canonical_ref is None

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


class TestPeekGhRef:
    def test_repo_path_existing_workspace_resolves_without_subprocess(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            primary = _github_workspace(home, "alice", "myrepo")
            path_patch, env_patch = _home_patches(home)
            with (
                path_patch,
                env_patch,
                patch(
                    "sase_github.workspace_plugin.subprocess.run",
                    side_effect=AssertionError("peek must not spawn subprocesses"),
                ),
            ):
                result = GitHubWorkspacePlugin().ws_peek_ref("alice/myrepo", "gh")

        assert result is not None
        assert result.project_name == "gh_alice__myrepo"
        assert result.primary_workspace_dir == primary
        assert result.checkout_target == "origin/main"
        assert result.canonical_ref == "gh_alice__myrepo"

    def test_repo_path_missing_workspace_returns_none_without_subprocess(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            path_patch, env_patch = _home_patches(home)
            with (
                path_patch,
                env_patch,
                patch(
                    "sase_github.workspace_plugin.subprocess.run",
                    side_effect=AssertionError("peek must not spawn subprocesses"),
                ),
            ):
                result = peek_gh_ref("alice/myrepo")

        assert result is None

    def test_project_shorthand_resolves_without_default_branch_subprocess(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            path_patch, env_patch = _home_patches(home)
            with (
                path_patch,
                env_patch,
                patch(
                    "sase_github.workspace_plugin.subprocess.run",
                    side_effect=AssertionError("peek must not spawn subprocesses"),
                ),
            ):
                proj_dir = home / ".sase" / "projects" / "myproj"
                proj_dir.mkdir(parents=True)
                spec = proj_dir / "myproj.sase"
                spec.write_text("WORKSPACE_DIR: /work/myproj/\nNAME: cl\n")

                result = peek_gh_ref("myproj")

        assert result is not None
        assert result.project_name == "myproj"
        assert result.primary_workspace_dir == "/work/myproj/"
        assert result.checkout_target == "origin/main"

    def test_does_not_claim_other_workflow_types(self) -> None:
        assert GitHubWorkspacePlugin().ws_peek_ref("alice/myrepo", "git") is None


class TestWsResolveRef:
    def test_passes_canonical_ref_through(self) -> None:
        resolved = ResolvedRef(
            project_file="/tmp/gh_alice__myrepo.sase",
            project_name="gh_alice__myrepo",
            primary_workspace_dir="/tmp/myrepo/",
            checkout_target="origin/main",
            canonical_ref="gh_alice__myrepo",
        )

        with patch(
            "sase_github.workspace_plugin.resolve_gh_ref", return_value=resolved
        ):
            result = GitHubWorkspacePlugin().ws_resolve_ref("alice/myrepo", "gh")

        assert result is not None
        assert result.project_name == "gh_alice__myrepo"
        assert result.canonical_ref == "gh_alice__myrepo"


class TestGhSetup:
    def test_materializes_sdd_store_after_checkout(self) -> None:
        from sase_github.scripts import gh_setup

        resolved = ResolvedRef(
            project_file="/tmp/gh_acme__widget.sase",
            project_name="gh_acme__widget",
            primary_workspace_dir="/work/widget/",
            checkout_target="origin/main",
            canonical_ref="gh_acme__widget",
        )

        with (
            patch("sase_github.scripts.gh_setup.resolve_ref", return_value=resolved),
            patch.dict(os.environ, {"SASE_GH_PRE_ALLOCATED": "0"}),
            patch(
                "sase_github.scripts.gh_setup.ensure_workspace_checkout",
                return_value="/work/widget_7/",
            ),
            patch(
                "sase_github.scripts.gh_setup.get_first_available_axe_workspace",
                return_value=7,
            ),
            patch("sase_github.scripts.gh_setup.claim_workspace"),
            patch("sase_github.scripts.gh_setup.materialize_sdd_store") as materialize,
        ):
            gh_setup.main(gh_ref="acme/widget", n=None, release=False)

        materialize.assert_called_once_with("/work/widget_7/", 7)


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


class TestPrUrlParsing:
    def test_extract_change_identifier_accepts_enterprise_pr_url(self) -> None:
        plugin = GitHubWorkspacePlugin()

        assert plugin.ws_extract_change_identifier(
            "https://github.enterprise.test/user/repo/pull/42"
        ) == ("42", "git")

    def test_extract_pr_number_accepts_enterprise_pr_url(self) -> None:
        assert (
            _extract_pr_number("https://github.enterprise.test/user/repo/pull/42")
            == "42"
        )

    def test_supports_reviewer_comments_accepts_enterprise_url(self) -> None:
        plugin = GitHubWorkspacePlugin()

        assert (
            plugin.ws_supports_reviewer_comments(
                "https://github.enterprise.test/user/repo/pull/42"
            )
            is False
        )
