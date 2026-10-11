"""Tests for the GitHub onboarding hooks (sase-1jx gh-provider phase).

Never creates live repositories: `gh` is faked through `subprocess.run`
patching, and the choice-list cache is isolated per test via `SASE_HOME`.
"""

from __future__ import annotations

import configparser
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from sase_github.workspace.onboarding import (
    create_gh_repo,
    describe_gh_repo_creation,
    list_gh_local_repo_candidates,
    parse_gh_repo_ref,
    probe_gh_repo,
    read_gh_account,
)
from sase_github.workspace_plugin import GitHubWorkspacePlugin


def _completed(
    *,
    args: list[str] | None = None,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=args or ["gh"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def _view_payload(**overrides: object) -> str:
    payload = {
        "id": "R_kgDOSsepkQ",
        "name": "bob",
        "owner": {"login": "bobs-org"},
        "visibility": "PRIVATE",
        "isEmpty": False,
        "isArchived": False,
        "viewerPermission": "ADMIN",
        "defaultBranchRef": {"name": "main"},
        "url": "https://github.com/bobs-org/bob",
        "description": "Bugyi's Obsidian",
    }
    payload.update(overrides)
    return json.dumps(payload)


@pytest.fixture()
def _hosts() -> object:
    with (
        patch("sase_github.config.get_default_github_host", return_value="github.com"),
        patch(
            "sase_github.config.get_github_hosts",
            return_value=["github.com"],
        ),
    ):
        yield


class TestParseGhRepoRef:
    @pytest.mark.usefixtures("_hosts")
    @pytest.mark.parametrize(
        ("text", "owner", "repo"),
        [
            ("sase-org/sase", "sase-org", "sase"),
            ("gh:sase-org/sase", "sase-org", "sase"),
            ("  sase-org/sase  ", "sase-org", "sase"),
            ("https://github.com/sase-org/sase", "sase-org", "sase"),
            ("https://github.com/sase-org/sase.git", "sase-org", "sase"),
            ("https://github.com/sase-org/sase/", "sase-org", "sase"),
            ("git@github.com:sase-org/sase.git", "sase-org", "sase"),
            ("git@github.com:sase-org/sase", "sase-org", "sase"),
            ("ssh://git@github.com/sase-org/sase.git", "sase-org", "sase"),
            ("ssh://git@github.com:22/sase-org/sase.git", "sase-org", "sase"),
        ],
    )
    def test_accepted_spellings(self, text: str, owner: str, repo: str) -> None:
        parsed = parse_gh_repo_ref(text)

        assert parsed is not None
        assert parsed.workflow_type == "gh"
        assert parsed.ref == f"{owner}/{repo}"
        assert parsed.display == f"{owner}/{repo}"
        assert parsed.identity_key == f"github.com/{owner}/{repo}"
        assert parsed.host == "github.com"
        assert parsed.owner == owner
        assert parsed.name == repo
        assert parsed.project_key_base == f"gh_{owner}__{repo}"
        assert parsed.project_name_base == repo
        assert parsed.checkout_dir.endswith(f"github/{owner}/{repo}/")
        assert parsed.web_url == f"https://github.com/{owner}/{repo}"
        assert parsed.normalized_from == ""

    @pytest.mark.usefixtures("_hosts")
    def test_enterprise_host_url(self) -> None:
        with patch(
            "sase_github.config.get_github_hosts",
            return_value=["github.com", "ghe.example.test"],
        ):
            parsed = parse_gh_repo_ref("https://ghe.example.test/acme/widget")

        assert parsed is not None
        assert parsed.host == "ghe.example.test"
        assert parsed.identity_key == "ghe.example.test/acme/widget"
        assert "ghe.example.test" in parsed.checkout_dir
        assert parsed.web_url == "https://ghe.example.test/acme/widget"

    @pytest.mark.usefixtures("_hosts")
    @pytest.mark.parametrize(
        "text",
        [
            "",
            "   ",
            "gh:",
            "just-a-name",
            "git:just-a-name",
            "a/b/c",
            "/",
            "owner/",
            "/repo",
            "https://evil.test/owner/repo",
            "git@evil.test:owner/repo.git",
            "ssh://git@evil.test/owner/repo.git",
        ],
    )
    def test_rejected_spellings(self, text: str) -> None:
        assert parse_gh_repo_ref(text) is None

    @pytest.mark.usefixtures("_hosts")
    def test_normalization_sets_normalized_from(self) -> None:
        parsed = parse_gh_repo_ref("sase-org/sase forge")

        assert parsed is not None
        assert parsed.ref == "sase-org/sase-forge"
        assert parsed.normalized_from == "sase-org/sase forge"

    @pytest.mark.usefixtures("_hosts")
    def test_identity_casefolds_but_ref_preserves_case(self) -> None:
        parsed = parse_gh_repo_ref("Sase-Org/Sase")

        assert parsed is not None
        assert parsed.ref == "Sase-Org/Sase"
        assert parsed.identity_key == "github.com/sase-org/sase"

    @pytest.mark.usefixtures("_hosts")
    def test_parse_is_pure(self) -> None:
        def _raise(*args: object, **kwargs: object) -> object:
            raise AssertionError("parse must not touch subprocess or fs")

        with (
            patch("subprocess.run", side_effect=_raise),
            patch.object(Path, "exists", side_effect=_raise),
        ):
            assert parse_gh_repo_ref("sase-org/sase") is not None

    def test_does_not_claim_other_workflow_types(self) -> None:
        assert GitHubWorkspacePlugin().ws_parse_repo_ref("git", "sase-org/sase") is None


def _write_checkout(home: Path, *parts: str, origin: str | None = None) -> Path:
    checkout = home / "projects" / "github" / Path(*parts)
    git_dir = checkout / ".git"
    git_dir.mkdir(parents=True, exist_ok=True)
    if origin is not None:
        config = configparser.ConfigParser(interpolation=None)
        config['remote "origin"'] = {"url": origin}
        with open(git_dir / "config", "w", encoding="utf-8") as f:
            config.write(f)
    return checkout


class TestListLocalRepoCandidates:
    def test_does_not_claim_other_workflow_types(self) -> None:
        assert GitHubWorkspacePlugin().ws_list_local_repo_candidates("git") is None

    def test_discovers_matching_checkouts(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        _write_checkout(
            home,
            "sase-org",
            "sase",
            origin="git@github.com:sase-org/sase.git",
        )
        _write_checkout(
            home,
            "ghe.example.test",
            "acme",
            "widget",
            origin="https://ghe.example.test/acme/widget.git",
        )

        with (
            patch.object(Path, "home", return_value=home),
            patch(
                "sase_github.config.get_default_github_host",
                return_value="github.com",
            ),
        ):
            result = list_gh_local_repo_candidates()

        assert [c.parsed.ref for c in result.entries] == [
            "acme/widget",
            "sase-org/sase",
        ]
        by_ref = {c.parsed.ref: c for c in result.entries}
        assert by_ref["sase-org/sase"].path.endswith("github/sase-org/sase")
        assert by_ref["sase-org/sase"].origin_url == (
            "git@github.com:sase-org/sase.git"
        )
        assert by_ref["sase-org/sase"].parsed.identity_key == (
            "github.com/sase-org/sase"
        )
        assert by_ref["acme/widget"].parsed.host == "ghe.example.test"
        assert all(c.numbered_copy is False for c in result.entries)

    def test_skips_mismatched_and_non_github_origins(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        _write_checkout(
            home, "sase-org", "sase", origin="git@github.com:other/sase.git"
        )
        _write_checkout(
            home, "plain", "dir", origin="https://gitlab.test/plain/dir.git"
        )
        _write_checkout(home, "no", "origin")
        _write_checkout(home, "local", "only", origin="/srv/git/only.git")

        with (
            patch.object(Path, "home", return_value=home),
            patch(
                "sase_github.config.get_default_github_host",
                return_value="github.com",
            ),
        ):
            result = list_gh_local_repo_candidates()

        assert result.entries == ()

    def test_marks_numbered_copies(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        _write_checkout(
            home, "sase-org", "sase_2", origin="git@github.com:sase-org/sase_2.git"
        )

        with (
            patch.object(Path, "home", return_value=home),
            patch(
                "sase_github.config.get_default_github_host",
                return_value="github.com",
            ),
        ):
            result = list_gh_local_repo_candidates()

        assert len(result.entries) == 1
        assert result.entries[0].parsed.ref == "sase-org/sase_2"
        assert result.entries[0].numbered_copy is True

    def test_spawns_no_subprocess(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        _write_checkout(
            home, "sase-org", "sase", origin="git@github.com:sase-org/sase.git"
        )

        def _raise(*args: object, **kwargs: object) -> object:
            raise AssertionError("local discovery must not spawn subprocesses")

        with (
            patch.object(Path, "home", return_value=home),
            patch(
                "sase_github.config.get_default_github_host",
                return_value="github.com",
            ),
            patch("subprocess.run", side_effect=_raise),
        ):
            result = list_gh_local_repo_candidates()

        assert len(result.entries) == 1


class TestReadGhAccount:
    def test_reads_signed_in_user(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "gh"
        config_dir.mkdir()
        (config_dir / "hosts.yml").write_text(
            "github.com:\n"
            "    user: bbugyi200\n"
            "    oauth_token: REDACTED\n"
            "ghe.example.test:\n"
            "    user: enterprise-u\n",
            encoding="utf-8",
        )

        with (
            patch.dict(os.environ, {"GH_CONFIG_DIR": str(config_dir)}),
            patch(
                "sase_github.config.get_default_github_host",
                return_value="github.com",
            ),
        ):
            assert read_gh_account() == "bbugyi200"
            assert read_gh_account("ghe.example.test") == "enterprise-u"
            assert read_gh_account("missing.test") == ""

    def test_missing_or_invalid_config_yields_empty(self, tmp_path: Path) -> None:
        with (
            patch.dict(os.environ, {"GH_CONFIG_DIR": str(tmp_path / "absent")}),
            patch(
                "sase_github.config.get_default_github_host",
                return_value="github.com",
            ),
        ):
            assert read_gh_account() == ""

        config_dir = tmp_path / "gh"
        config_dir.mkdir()
        (config_dir / "hosts.yml").write_text("[[not yaml\n", encoding="utf-8")
        with (
            patch.dict(os.environ, {"GH_CONFIG_DIR": str(config_dir)}),
            patch(
                "sase_github.config.get_default_github_host",
                return_value="github.com",
            ),
        ):
            assert read_gh_account() == ""

    def test_spawns_no_subprocess(self, tmp_path: Path) -> None:
        def _raise(*args: object, **kwargs: object) -> object:
            raise AssertionError("account read must not spawn subprocesses")

        with (
            patch.dict(os.environ, {"GH_CONFIG_DIR": str(tmp_path / "absent")}),
            patch("subprocess.run", side_effect=_raise),
        ):
            assert read_gh_account() == ""


def _probe_run(payload: str, sase_yml_returncode: int = 1) -> object:
    def _run(argv: list[str], **kwargs: object) -> object:
        if argv[:3] == ["gh", "repo", "view"]:
            assert "--json" in argv
            assert kwargs["stdin"] is subprocess.DEVNULL
            assert kwargs["env"]["GH_HOST"] == "github.com"
            assert kwargs["env"]["GH_PROMPT_DISABLED"] == "1"
            return _completed(stdout=payload)
        assert argv[:2] == ["gh", "api"]
        if sase_yml_returncode == 0:
            return _completed(
                stdout="is_sase_managed: true\n", returncode=sase_yml_returncode
            )
        return _completed(
            stdout="", stderr="HTTP 404: Not Found", returncode=sase_yml_returncode
        )

    return _run


class TestProbeGhRepo:
    def test_does_not_claim_other_workflow_types(self) -> None:
        assert GitHubWorkspacePlugin().ws_probe_repo("git", "sase-org/sase") is None
        assert probe_gh_repo("just-a-name") is None

    @pytest.mark.usefixtures("_hosts")
    def test_found_repo(self) -> None:
        with patch(
            "sase_github.workspace.onboarding.subprocess.run",
            side_effect=_probe_run(_view_payload()),
        ) as mock_run:
            probe = probe_gh_repo("bobs-org/bob")

        assert probe is not None
        assert probe.status == "found"
        assert probe.visibility == "private"
        assert probe.description == "Bugyi's Obsidian"
        assert probe.default_branch == "main"
        assert probe.is_empty is False
        assert probe.is_archived is False
        assert probe.can_push is True
        assert probe.sase_managed is False
        assert probe.repo_id == "R_kgDOSsepkQ"
        assert probe.web_url == "https://github.com/bobs-org/bob"
        assert probe.error_kind is None
        assert mock_run.call_count == 2

    @pytest.mark.usefixtures("_hosts")
    def test_managed_and_read_only_permissions(self) -> None:
        payload = _view_payload(viewerPermission="READ")
        with patch(
            "sase_github.workspace.onboarding.subprocess.run",
            side_effect=_probe_run(payload, sase_yml_returncode=0),
        ):
            probe = probe_gh_repo("bobs-org/bob")

        assert probe is not None
        assert probe.can_push is False
        assert probe.sase_managed is True

    @pytest.mark.usefixtures("_hosts")
    @pytest.mark.parametrize(
        ("stderr", "status", "error_kind", "remedy"),
        [
            (
                "GraphQL: Could not resolve to a Repository with the name 'o/r'.",
                "not_found",
                "not_found",
                (),
            ),
            (
                "To get started with GitHub CLI, run: gh auth login",
                "unavailable",
                "auth",
                ("gh", "auth", "login", "-h", "github.com"),
            ),
            (
                "dial tcp: lookup api.github.com: no such host",
                "unavailable",
                "network",
                (),
            ),
            ("GraphQL failed", "unavailable", "unknown", ()),
        ],
    )
    def test_error_kind_table(
        self,
        stderr: str,
        status: str,
        error_kind: str,
        remedy: tuple[str, ...],
    ) -> None:
        with patch(
            "sase_github.workspace.onboarding.subprocess.run",
            return_value=_completed(returncode=1, stderr=stderr),
        ):
            probe = probe_gh_repo("o/r")

        assert probe is not None
        assert probe.status == status
        assert probe.error_kind == error_kind
        assert probe.remedy_argv == remedy

    @pytest.mark.usefixtures("_hosts")
    @pytest.mark.parametrize(
        ("side_effect", "error_kind"),
        [
            (FileNotFoundError(), "tool_missing"),
            (OSError("nope"), "tool_missing"),
            (subprocess.TimeoutExpired(["gh"], timeout=10), "network"),
        ],
    )
    def test_spawn_failures(self, side_effect: BaseException, error_kind: str) -> None:
        with patch(
            "sase_github.workspace.onboarding.subprocess.run",
            side_effect=side_effect,
        ):
            probe = probe_gh_repo("o/r")

        assert probe is not None
        assert probe.status == "unavailable"
        assert probe.error_kind == error_kind

    @pytest.mark.usefixtures("_hosts")
    def test_probe_never_writes(self) -> None:
        def _no_write(*args: object, **kwargs: object) -> object:
            raise AssertionError("probe must never write files")

        with (
            patch(
                "sase_github.workspace.onboarding.subprocess.run",
                side_effect=_probe_run(_view_payload()),
            ),
            patch("pathlib.Path.write_text", side_effect=_no_write),
            patch("pathlib.Path.mkdir", side_effect=_no_write),
            patch("os.replace", side_effect=_no_write),
        ):
            probe = probe_gh_repo("bobs-org/bob")

        assert probe is not None
        assert probe.status == "found"


def _describe_run(
    *,
    gitignore: str = "Python\nRust\n",
    licenses: str = "mit\tMIT\tMIT License\n",
    teams: str = "[]",
    plan: str = "Team",
    memberships: str = "sase-org\n",
) -> object:
    def _run(argv: list[str], **kwargs: object) -> object:
        command = " ".join(argv)
        if command.startswith("gh repo gitignore list"):
            return _completed(stdout=gitignore)
        if command.startswith("gh repo license list"):
            return _completed(stdout=licenses)
        if "/teams" in command:
            return _completed(stdout=teams)
        if command.startswith("gh api orgs/"):
            return _completed(stdout=plan)
        if "memberships" in command:
            return _completed(stdout=memberships)
        raise AssertionError(f"unexpected gh call: {command}")

    return _run


def _account(account: str) -> object:
    return patch(
        "sase_github.workspace.onboarding.read_gh_account", return_value=account
    )


class TestDescribeGhRepoCreation:
    def test_does_not_claim_other_workflow_types(self) -> None:
        assert GitHubWorkspacePlugin().ws_describe_repo_creation("git", "acme") is None
        assert describe_gh_repo_creation("") is None
        assert describe_gh_repo_creation("a/b") is None

    @pytest.mark.usefixtures("_hosts")
    def test_personal_namespace_form(self, tmp_path: Path) -> None:
        with (
            patch.dict(os.environ, {"SASE_HOME": str(tmp_path / "home")}),
            _account("bbugyi200"),
            patch(
                "sase_github.workspace.onboarding.subprocess.run",
                side_effect=_describe_run(),
            ),
        ):
            form = describe_gh_repo_creation("bbugyi200")

        assert form is not None
        assert form.workflow_type == "gh"
        assert form.namespace == "bbugyi200"
        assert form.can_create is True
        by_name = {field.name: field for field in form.fields}
        assert [field.name for field in form.fields] == [
            "description",
            "visibility",
            "add_readme",
            "gitignore",
            "license",
            "template",
            "include_all_branches",
            "homepage",
            "team",
            "issues",
            "wiki",
        ]
        assert by_name["visibility"].default == "private"
        assert by_name["visibility"].required is True
        assert [c.value for c in by_name["visibility"].choices] == [
            "public",
            "private",
            "internal",
        ]
        assert by_name["visibility"].choices[2].disabled_reason != ""
        assert by_name["add_readme"].default == "true"
        assert by_name["template"].group == "advanced"
        assert by_name["template"].placeholder == "OWNER/REPO"
        assert by_name["template"].excludes == (
            "add_readme",
            "gitignore",
            "license",
            "team",
        )
        assert by_name["include_all_branches"].requires == ("template",)
        assert by_name["team"].disabled_reason != ""
        assert by_name["issues"].default == "true"
        assert by_name["wiki"].default == "true"
        assert [c.value for c in by_name["gitignore"].choices] == ["Python", "Rust"]
        assert [c.value for c in by_name["license"].choices] == ["mit"]

    @pytest.mark.usefixtures("_hosts")
    def test_org_namespace_team_enabled_and_member_can_create(
        self, tmp_path: Path
    ) -> None:
        teams = json.dumps([{"slug": "core", "name": "Core team"}])
        with (
            patch.dict(os.environ, {"SASE_HOME": str(tmp_path / "home")}),
            _account("bbugyi200"),
            patch(
                "sase_github.workspace.onboarding.subprocess.run",
                side_effect=_describe_run(teams=teams, plan="Enterprise Cloud"),
            ),
        ):
            form = describe_gh_repo_creation("sase-org")

        assert form is not None
        by_name = {field.name: field for field in form.fields}
        assert by_name["team"].disabled_reason == ""
        assert [c.value for c in by_name["team"].choices] == ["core"]
        assert by_name["visibility"].choices[2].disabled_reason == ""
        assert form.can_create is True

    @pytest.mark.usefixtures("_hosts")
    def test_unknown_rights_report_none(self, tmp_path: Path) -> None:
        with (
            patch.dict(os.environ, {"SASE_HOME": str(tmp_path / "home")}),
            _account("bbugyi200"),
            patch(
                "sase_github.workspace.onboarding.subprocess.run",
                side_effect=_describe_run(memberships="other-org\n"),
            ),
        ):
            form = describe_gh_repo_creation("sase-org")

        assert form is not None
        assert form.can_create is None
        assert "unverified" in form.message

    @pytest.mark.usefixtures("_hosts")
    def test_choice_lists_cached_for_second_call(self, tmp_path: Path) -> None:
        calls: list[str] = []

        def _counting_run(argv: list[str], **kwargs: object) -> object:
            calls.append(" ".join(argv))
            return _describe_run()(argv, **kwargs)

        with (
            patch.dict(os.environ, {"SASE_HOME": str(tmp_path / "home")}),
            _account("bbugyi200"),
            patch(
                "sase_github.workspace.onboarding.subprocess.run",
                side_effect=_counting_run,
            ),
        ):
            first = describe_gh_repo_creation("sase-org")
            second = describe_gh_repo_creation("sase-org")

        assert first is not None and second is not None
        assert first == second
        gitignore_calls = [c for c in calls if "gitignore list" in c]
        assert len(gitignore_calls) == 1


class TestCreateGhRepo:
    def test_does_not_claim_other_workflow_types(self) -> None:
        assert (
            GitHubWorkspacePlugin().ws_create_repo(
                "git", "sase", {"visibility": "private"}
            )
            is None
        )
        assert create_gh_repo("just-a-name", {"visibility": "private"}) is None

    @pytest.mark.usefixtures("_hosts")
    def test_missing_visibility_fails_before_any_subprocess(self) -> None:
        def _raise(*args: object, **kwargs: object) -> object:
            raise AssertionError("must reject conflicts before any subprocess")

        with patch(
            "sase_github.workspace.onboarding.subprocess.run", side_effect=_raise
        ):
            result = create_gh_repo("sase-org/sase", {})

        assert result is not None
        assert result.status == "failed"
        assert "visibility" in result.message
        assert "public" in result.message

    @pytest.mark.usefixtures("_hosts")
    def test_argv_exact(self) -> None:
        seen: list[list[str]] = []

        def _run(argv: list[str], **kwargs: object) -> object:
            seen.append(argv)
            if argv[:3] == ["gh", "repo", "create"]:
                assert kwargs["stdin"] is subprocess.DEVNULL
                assert kwargs["env"]["GH_HOST"] == "github.com"
                assert kwargs["env"]["GH_PROMPT_DISABLED"] == "1"
                return _completed(stdout="")
            assert argv[:3] == ["gh", "repo", "view"]
            return _completed(
                stdout=json.dumps(
                    {
                        "id": "R_new",
                        "visibility": "private",
                        "url": "https://github.com/sase-org/forge",
                    }
                )
            )

        options = {
            "visibility": "private",
            "description": "Forge",
            "homepage": "https://example.test",
            "add_readme": "true",
            "gitignore": "Python",
            "license": "mit",
            "template": "sase-org/tpl",
            "include_all_branches": "true",
            "team": "core",
            "issues": "false",
            "wiki": "false",
        }
        with patch("sase_github.workspace.onboarding.subprocess.run", side_effect=_run):
            result = create_gh_repo("sase-org/forge", options)

        assert result is not None
        assert result.status == "created"
        assert seen[0] == [
            "gh",
            "repo",
            "create",
            "sase-org/forge",
            "--private",
            "-d",
            "Forge",
            "-h",
            "https://example.test",
            "--add-readme",
            "-g",
            "Python",
            "-l",
            "mit",
            "-p",
            "sase-org/tpl",
            "--include-all-branches",
            "-t",
            "core",
            "--disable-issues",
            "--disable-wiki",
        ]
        assert result.repo_id == "R_new"
        assert result.mismatches == ()

    @pytest.mark.usefixtures("_hosts")
    def test_reports_visibility_mismatch(self) -> None:
        def _run(argv: list[str], **kwargs: object) -> object:
            if argv[:3] == ["gh", "repo", "create"]:
                return _completed(stdout="")
            return _completed(
                stdout=json.dumps({"id": "R_new", "visibility": "public", "url": ""})
            )

        with patch("sase_github.workspace.onboarding.subprocess.run", side_effect=_run):
            result = create_gh_repo("sase-org/forge", {"visibility": "private"})

        assert result is not None
        assert result.status == "created"
        assert result.mismatches == ("visibility: requested private, got public",)

    @pytest.mark.usefixtures("_hosts")
    def test_exists_reprobes_and_reports_exists(self) -> None:
        def _run(argv: list[str], **kwargs: object) -> object:
            if argv[:3] == ["gh", "repo", "create"]:
                return _completed(
                    returncode=1,
                    stderr="GraphQL: Name already taken (createRepository)",
                )
            if argv[:3] == ["gh", "repo", "view"]:
                return _completed(stdout=_view_payload())
            return _completed(stdout="", stderr="HTTP 404: Not Found", returncode=1)

        with patch("sase_github.workspace.onboarding.subprocess.run", side_effect=_run):
            result = create_gh_repo("bobs-org/bob", {"visibility": "private"})

        assert result is not None
        assert result.status == "exists"
        assert result.repo_id == "R_kgDOSsepkQ"
        assert result.visibility == "private"

    @pytest.mark.usefixtures("_hosts")
    def test_create_failure_without_exists_evidence(self) -> None:
        with patch(
            "sase_github.workspace.onboarding.subprocess.run",
            return_value=_completed(returncode=1, stderr="validation failed"),
        ):
            result = create_gh_repo("sase-org/forge", {"visibility": "private"})

        assert result is not None
        assert result.status == "failed"
        assert "validation failed" in result.message


class TestEnrichedCompletion:
    @pytest.mark.usefixtures("_hosts")
    def test_repo_list_fields_host_and_truncation(self) -> None:
        from sase_github.workspace.completion import _REPO_LIST_JSON_FIELDS

        payload = [
            {
                "name": "sase",
                "description": "Structured agents",
                "visibility": "PRIVATE",
                "isArchived": False,
                "isFork": False,
                "pushedAt": "2026-07-07T17:00:00Z",
                "id": "R_1",
                "viewerPermission": "ADMIN",
                "isEmpty": False,
            }
        ]
        with (
            patch(
                "sase_github.workspace.completion._repo_completion_limit",
                return_value=1,
            ),
            patch(
                "sase_github.workspace.completion.subprocess.run",
                return_value=_completed(stdout=json.dumps(payload)),
            ) as mock_run,
        ):
            result = GitHubWorkspacePlugin().ws_list_repo_candidates("gh", "alice")

        assert result is not None
        assert result.status == "ok"
        assert "--json" in mock_run.call_args[0][0]
        assert _REPO_LIST_JSON_FIELDS in mock_run.call_args[0][0]
        assert result.host == "github.com"
        assert result.truncated is True
        assert result.entries[0].repo_id == "R_1"
        assert result.entries[0].viewer_permission == "ADMIN"
        assert result.entries[0].is_empty is False

    @pytest.mark.usefixtures("_hosts")
    def test_auth_error_carries_remedy(self) -> None:
        with patch(
            "sase_github.workspace.completion.subprocess.run",
            return_value=_completed(returncode=1, stderr="gh auth login required"),
        ):
            result = GitHubWorkspacePlugin().ws_list_repo_candidates("gh", "alice")

        assert result is not None
        assert result.error_kind == "auth"
        assert result.host == "github.com"
        assert result.remedy_argv == ("gh", "auth", "login", "-h", "github.com")

    def test_namespaces_carry_account(self) -> None:
        records = [SimpleNamespace(project_name="gh_sase-org__sase")]

        with (
            patch(
                "sase_github.workspace.completion.enabled_project_records",
                return_value=records,
            ),
            patch("sase_github.config.get_github_orgs", return_value=[]),
            _account("bbugyi200"),
        ):
            result = GitHubWorkspacePlugin().ws_list_ref_namespaces("gh")

        assert result is not None
        assert result.account == "bbugyi200"
        assert [(entry.name, entry.kind_label) for entry in result.entries] == [
            ("bbugyi200", "you"),
            ("sase-org", "org"),
        ]

    def test_namespaces_without_account_unchanged(self) -> None:
        records = [SimpleNamespace(project_name="gh_sase-org__sase")]

        with (
            patch(
                "sase_github.workspace.completion.enabled_project_records",
                return_value=records,
            ),
            patch("sase_github.config.get_github_orgs", return_value=[]),
            _account(""),
        ):
            result = GitHubWorkspacePlugin().ws_list_ref_namespaces("gh")

        assert result is not None
        assert result.account == ""
        assert [(entry.name, entry.kind_label) for entry in result.entries] == [
            ("sase-org", "org")
        ]
