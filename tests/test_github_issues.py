"""Tests for the GitHub issue-tracker hooks."""

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pluggy
import pytest
from sase.vcs_provider import IssueWire, VCSOperationError
from sase.vcs_provider._base import VCSProvider
from sase.vcs_provider._hookspec import VCSHookSpec
from sase.vcs_provider._plugin_manager import VCSPluginManager

from sase_github.errors import (
    GitHubAuthenticationError,
    GitHubIssueError,
    GitHubRateLimitError,
)
from sase_github.plugin import GitHubPlugin

_MOCK_TARGET = "sase.vcs_provider._command_runner.subprocess.run"
_JSON_FIELDS = (
    "number,title,state,body,labels,assignees,author,createdAt,updatedAt,url,comments"
)


@pytest.fixture
def github_provider() -> VCSPluginManager:
    pm = pluggy.PluginManager("sase_vcs")
    pm.add_hookspecs(VCSHookSpec)
    pm.register(GitHubPlugin())
    return VCSPluginManager(pm)


def _json_issue(
    number: int = 42,
    *,
    state: str = "OPEN",
    title: str = "Broken widget",
    body: str | None = "Steps to reproduce",
    labels: tuple[str, ...] = ("bug", "p1"),
    assignees: tuple[str, ...] = ("octocat",),
    author: str | None = "hubot",
    comments: int = 2,
) -> dict[str, Any]:
    return {
        "number": number,
        "title": title,
        "state": state,
        "body": body,
        "labels": [{"name": label, "color": "ff0000"} for label in labels],
        "assignees": [{"login": login} for login in assignees],
        "author": {"login": author} if author is not None else None,
        "createdAt": "2026-07-14T10:00:00Z",
        "updatedAt": "2026-07-15T11:00:00Z",
        "url": f"https://github.example/owner/repo/issues/{number}",
        "comments": [{"id": index} for index in range(comments)],
    }


def _completed(*, returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    return MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)


def _command(mock_run: MagicMock, index: int = 0) -> list[str]:
    return mock_run.call_args_list[index].args[0]


def test_github_provider_advertises_issue_capability(
    github_provider: VCSPluginManager,
) -> None:
    assert isinstance(github_provider, VCSProvider)
    assert github_provider.supports_issues() is True


@patch(_MOCK_TARGET)
def test_list_issues_uses_json_and_normalizes_records(
    mock_run: MagicMock, github_provider: VCSPluginManager
) -> None:
    first = _json_issue()
    second = _json_issue(
        7,
        state="CLOSED",
        title="No body",
        body=None,
        labels=(),
        assignees=(),
        author=None,
        comments=0,
    )
    mock_run.return_value = _completed(stdout=json.dumps([first, second]))

    issues = github_provider.list_issues("/workspace", state="all", limit=25)

    assert issues == [
        IssueWire(
            number=42,
            title="Broken widget",
            state="open",
            body="Steps to reproduce",
            labels=("bug", "p1"),
            assignees=("octocat",),
            author="hubot",
            created_at="2026-07-14T10:00:00Z",
            updated_at="2026-07-15T11:00:00Z",
            url="https://github.example/owner/repo/issues/42",
            comment_count=2,
        ),
        IssueWire(
            number=7,
            title="No body",
            state="closed",
            created_at="2026-07-14T10:00:00Z",
            updated_at="2026-07-15T11:00:00Z",
            url="https://github.example/owner/repo/issues/7",
        ),
    ]
    assert _command(mock_run) == [
        "gh",
        "issue",
        "list",
        "--state",
        "all",
        "--limit",
        "25",
        "--json",
        _JSON_FIELDS,
    ]
    assert mock_run.call_args.kwargs["cwd"] == "/workspace"


@patch(_MOCK_TARGET)
def test_list_issues_non_positive_limit_requests_all_available_results(
    mock_run: MagicMock, github_provider: VCSPluginManager
) -> None:
    mock_run.return_value = _completed(stdout="[]")

    assert github_provider.list_issues("/workspace", limit=0) == []

    command = _command(mock_run)
    assert command[command.index("--limit") + 1] == "1000000"


@patch(_MOCK_TARGET)
def test_get_issue_uses_view_json(
    mock_run: MagicMock, github_provider: VCSPluginManager
) -> None:
    mock_run.return_value = _completed(stdout=json.dumps(_json_issue(17)))

    issue = github_provider.get_issue(17, "/workspace")

    assert issue.number == 17
    assert _command(mock_run) == [
        "gh",
        "issue",
        "view",
        "17",
        "--json",
        _JSON_FIELDS,
    ]


@patch(_MOCK_TARGET)
def test_create_issue_passes_labels_and_refreshes_created_url(
    mock_run: MagicMock, github_provider: VCSPluginManager
) -> None:
    issue_url = "https://github.example/owner/repo/issues/43"
    mock_run.side_effect = [
        _completed(stdout=f"{issue_url}\n"),
        _completed(stdout=json.dumps(_json_issue(43, labels=("bug", "p2")))),
    ]

    issue = github_provider.create_issue(
        "New issue", "Detailed body", ["bug", "p2", "bug"], "/workspace"
    )

    assert issue.number == 43
    assert _command(mock_run, 0) == [
        "gh",
        "issue",
        "create",
        "--title",
        "New issue",
        "--body",
        "Detailed body",
        "--label",
        "bug",
        "--label",
        "p2",
    ]
    assert _command(mock_run, 1) == [
        "gh",
        "issue",
        "view",
        issue_url,
        "--json",
        _JSON_FIELDS,
    ]


@patch(_MOCK_TARGET)
def test_update_issue_edits_labels_closes_and_refreshes(
    mock_run: MagicMock, github_provider: VCSPluginManager
) -> None:
    current = _json_issue(labels=("bug", "p1", "needs-info"))
    updated = _json_issue(
        state="CLOSED",
        title="Clearer title",
        body="Updated body",
        labels=("bug", "p2"),
    )
    mock_run.side_effect = [
        _completed(stdout=json.dumps(current)),
        _completed(),
        _completed(),
        _completed(stdout=json.dumps(updated)),
    ]

    issue = github_provider.update_issue(
        42,
        "/workspace",
        title="Clearer title",
        body="Updated body",
        state="closed",
        labels=["bug", "p2"],
    )

    assert issue.state == "closed"
    assert issue.labels == ("bug", "p2")
    assert _command(mock_run, 0)[:4] == ["gh", "issue", "view", "42"]
    assert _command(mock_run, 1) == [
        "gh",
        "issue",
        "edit",
        "42",
        "--title",
        "Clearer title",
        "--body",
        "Updated body",
        "--remove-label",
        "p1",
        "--remove-label",
        "needs-info",
        "--add-label",
        "p2",
    ]
    assert _command(mock_run, 2) == ["gh", "issue", "close", "42"]
    assert _command(mock_run, 3)[:4] == ["gh", "issue", "view", "42"]


@patch(_MOCK_TARGET)
def test_update_issue_reopens_without_unnecessary_edit(
    mock_run: MagicMock, github_provider: VCSPluginManager
) -> None:
    current = _json_issue(state="CLOSED")
    reopened = _json_issue(state="OPEN")
    mock_run.side_effect = [
        _completed(stdout=json.dumps(current)),
        _completed(),
        _completed(stdout=json.dumps(reopened)),
    ]

    issue = github_provider.update_issue(42, "/workspace", state="open")

    assert issue.state == "open"
    assert _command(mock_run, 1) == ["gh", "issue", "reopen", "42"]
    assert all("edit" not in call.args[0] for call in mock_run.call_args_list)


@patch(_MOCK_TARGET)
def test_update_issue_noop_returns_current_record_without_mutation(
    mock_run: MagicMock, github_provider: VCSPluginManager
) -> None:
    current = _json_issue()
    mock_run.return_value = _completed(stdout=json.dumps(current))

    issue = github_provider.update_issue(
        42,
        "/workspace",
        state="open",
        labels=["bug", "p1", "bug"],
    )

    assert issue.number == 42
    assert mock_run.call_count == 1


@patch(_MOCK_TARGET)
def test_get_issue_url_requests_only_url(
    mock_run: MagicMock, github_provider: VCSPluginManager
) -> None:
    mock_run.return_value = _completed(
        stdout="https://github.example/owner/repo/issues/9\n"
    )

    url = github_provider.get_issue_url(9, "/workspace")

    assert url == "https://github.example/owner/repo/issues/9"
    assert _command(mock_run) == [
        "gh",
        "issue",
        "view",
        "9",
        "--json",
        "url",
        "--jq",
        ".url",
    ]


@pytest.mark.parametrize(
    ("returncode", "stderr"),
    [
        (4, "authentication required"),
        (1, "To get started with GitHub CLI, please run: gh auth login"),
        (1, "HTTP 401: Bad credentials"),
    ],
)
@patch(_MOCK_TARGET)
def test_auth_failures_map_to_typed_actionable_error(
    mock_run: MagicMock,
    returncode: int,
    stderr: str,
    github_provider: VCSPluginManager,
) -> None:
    mock_run.return_value = _completed(returncode=returncode, stderr=stderr)

    with pytest.raises(GitHubAuthenticationError) as exc_info:
        github_provider.list_issues("/workspace")

    assert isinstance(exc_info.value, VCSOperationError)
    assert exc_info.value.operation == "gh issue list"
    assert "gh auth login" in exc_info.value.message


@pytest.mark.parametrize(
    "stderr",
    [
        "GraphQL: API rate limit exceeded for user",
        "HTTP 429: Too Many Requests",
        "You have exceeded a secondary rate limit",
    ],
)
@patch(_MOCK_TARGET)
def test_rate_limit_failures_map_to_typed_retryable_error(
    mock_run: MagicMock,
    stderr: str,
    github_provider: VCSPluginManager,
) -> None:
    mock_run.return_value = _completed(returncode=1, stderr=stderr)

    with pytest.raises(GitHubRateLimitError) as exc_info:
        github_provider.get_issue(42, "/workspace")

    assert isinstance(exc_info.value, VCSOperationError)
    assert exc_info.value.operation == "gh issue view"
    assert "try again later" in exc_info.value.message


@patch(_MOCK_TARGET)
def test_other_command_failures_preserve_gh_error(
    mock_run: MagicMock, github_provider: VCSPluginManager
) -> None:
    mock_run.return_value = _completed(
        returncode=1,
        stderr="GraphQL: Could not resolve to an Issue with the number of 404",
    )

    with pytest.raises(GitHubIssueError, match="Could not resolve") as exc_info:
        github_provider.get_issue(404, "/workspace")

    assert type(exc_info.value) is GitHubIssueError


@pytest.mark.parametrize(
    "stdout",
    [
        "not json",
        "{}",
        json.dumps([{"number": 1, "title": "Missing state"}]),
    ],
)
@patch(_MOCK_TARGET)
def test_malformed_list_json_raises_typed_error(
    mock_run: MagicMock, stdout: str, github_provider: VCSPluginManager
) -> None:
    mock_run.return_value = _completed(stdout=stdout)

    with pytest.raises(GitHubIssueError, match="gh issue list"):
        github_provider.list_issues("/workspace")


@patch(_MOCK_TARGET)
def test_create_issue_requires_created_url(
    mock_run: MagicMock, github_provider: VCSPluginManager
) -> None:
    mock_run.return_value = _completed(stdout="\n")

    with pytest.raises(GitHubIssueError, match="no created issue URL"):
        github_provider.create_issue("Title", "Body", [], "/workspace")
