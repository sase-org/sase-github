"""Tests for the GitHub pull-request listing hook."""

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pluggy
import pytest
from sase.vcs_provider import PullRequestWire
from sase.vcs_provider._hookspec import VCSHookSpec
from sase.vcs_provider._plugin_manager import VCSPluginManager

from sase_github.errors import GitHubIssueError
from sase_github.plugin import GitHubPlugin

_MOCK_TARGET = "sase.vcs_provider._command_runner.subprocess.run"
_JSON_FIELDS = (
    "id,number,title,state,body,isDraft,author,headRefName,baseRefName,createdAt,"
    "updatedAt,closedAt,mergedAt,url"
)


@pytest.fixture
def github_provider() -> VCSPluginManager:
    pm = pluggy.PluginManager("sase_vcs")
    pm.add_hookspecs(VCSHookSpec)
    pm.register(GitHubPlugin())
    return VCSPluginManager(pm)


def _json_pr(
    number: int = 42,
    *,
    state: str = "OPEN",
    title: str = "Add feature",
    body: str | None = "Description",
    is_draft: bool = False,
    author: str | None = "hubot",
    head_ref: str = "feature-branch",
    base_ref: str = "main",
    closed_at: str | None = None,
    merged_at: str | None = None,
    node_id: str | None = None,
) -> dict[str, Any]:
    return {
        "id": node_id if node_id is not None else f"PR_kwDOpr{number}",
        "number": number,
        "title": title,
        "state": state,
        "body": body,
        "isDraft": is_draft,
        "author": {"login": author} if author is not None else None,
        "headRefName": head_ref,
        "baseRefName": base_ref,
        "createdAt": "2026-07-14T10:00:00Z",
        "updatedAt": "2026-07-15T11:00:00Z",
        "closedAt": closed_at,
        "mergedAt": merged_at,
        "url": f"https://github.example/owner/repo/pull/{number}",
    }


def _completed(*, returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    return MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)


def _command(mock_run: MagicMock, index: int = 0) -> list[str]:
    return mock_run.call_args_list[index].args[0]


def test_github_provider_advertises_pull_request_capability(
    github_provider: VCSPluginManager,
) -> None:
    assert github_provider.supports_pull_requests() is True


@patch(_MOCK_TARGET)
def test_list_pull_requests_open_requests_state_open_and_normalizes_records(
    mock_run: MagicMock, github_provider: VCSPluginManager
) -> None:
    mock_run.return_value = _completed(stdout=json.dumps([_json_pr()]))

    pull_requests = github_provider.list_pull_requests(
        "/workspace", state="open", limit=25
    )

    assert pull_requests == [
        PullRequestWire(
            number=42,
            title="Add feature",
            state="open",
            provider_id="PR_kwDOpr42",
            url="https://github.example/owner/repo/pull/42",
            body="Description",
            is_draft=False,
            author="hubot",
            head_ref="feature-branch",
            base_ref="main",
            created_at="2026-07-14T10:00:00Z",
            updated_at="2026-07-15T11:00:00Z",
            closed_at="",
            merged_at="",
        )
    ]
    assert _command(mock_run) == [
        "gh",
        "pr",
        "list",
        "--state",
        "open",
        "--limit",
        "25",
        "--json",
        _JSON_FIELDS,
    ]
    assert mock_run.call_args.kwargs["cwd"] == "/workspace"


@patch(_MOCK_TARGET)
def test_list_pull_requests_all_requests_state_all_directly(
    mock_run: MagicMock, github_provider: VCSPluginManager
) -> None:
    mock_run.return_value = _completed(stdout="[]")

    assert github_provider.list_pull_requests("/workspace", state="all", limit=10) == []

    command = _command(mock_run)
    assert command[command.index("--state") + 1] == "all"
    assert command[command.index("--limit") + 1] == "10"


@patch(_MOCK_TARGET)
def test_list_pull_requests_closed_folds_in_merged_and_excludes_open(
    mock_run: MagicMock, github_provider: VCSPluginManager
) -> None:
    open_pr = _json_pr(1, state="OPEN")
    closed_pr = _json_pr(2, state="CLOSED", closed_at="2026-07-20T00:00:00Z")
    merged_pr = _json_pr(
        3,
        state="MERGED",
        closed_at="2026-07-21T00:00:00Z",
        merged_at="2026-07-21T00:00:00Z",
    )
    mock_run.return_value = _completed(
        stdout=json.dumps([open_pr, closed_pr, merged_pr])
    )

    pull_requests = github_provider.list_pull_requests("/workspace", state="closed")

    assert [pr.number for pr in pull_requests] == [2, 3]
    assert all(pr.state == "closed" for pr in pull_requests)
    merged = next(pr for pr in pull_requests if pr.number == 3)
    assert merged.merged_at == "2026-07-21T00:00:00Z"
    closed_unmerged = next(pr for pr in pull_requests if pr.number == 2)
    assert closed_unmerged.merged_at == ""

    command = _command(mock_run)
    assert command[command.index("--state") + 1] == "all"
    assert command[command.index("--limit") + 1] == "1000000"


@patch(_MOCK_TARGET)
def test_list_pull_requests_non_positive_limit_requests_all_available_results(
    mock_run: MagicMock, github_provider: VCSPluginManager
) -> None:
    mock_run.return_value = _completed(stdout="[]")

    assert github_provider.list_pull_requests("/workspace", limit=0) == []

    command = _command(mock_run)
    assert command[command.index("--limit") + 1] == "1000000"


@patch(_MOCK_TARGET)
def test_list_pull_requests_normalizes_draft_flag(
    mock_run: MagicMock, github_provider: VCSPluginManager
) -> None:
    mock_run.return_value = _completed(stdout=json.dumps([_json_pr(is_draft=True)]))

    pull_requests = github_provider.list_pull_requests("/workspace")

    assert pull_requests[0].is_draft is True


@pytest.mark.parametrize(
    "stdout",
    [
        "not json",
        "{}",
        json.dumps([{"number": 1, "title": "Missing state"}]),
    ],
)
@patch(_MOCK_TARGET)
def test_malformed_pull_request_json_raises_typed_error(
    mock_run: MagicMock, stdout: str, github_provider: VCSPluginManager
) -> None:
    mock_run.return_value = _completed(stdout=stdout)

    with pytest.raises(GitHubIssueError, match="gh pr list"):
        github_provider.list_pull_requests("/workspace")
