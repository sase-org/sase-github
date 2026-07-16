"""GitHub VCS plugin implementation.

Handles git repositories hosted on GitHub (or similar hosted services).
Inherits shared git operations from :class:`GitCommon` and adds
GitHub-specific pull-request and issue operations via the ``gh`` CLI.
"""

import json
import subprocess
from collections.abc import Sequence
from typing import Any, cast

from sase.vcs_provider import IssueListState, IssueState, IssueWire
from sase.vcs_provider._hookspec import hookimpl
from sase.vcs_provider._types import CommandOutput
from sase.vcs_provider.plugins._git_common import GitCommon
from sase_github.config import get_github_hosts, normalize_github_host
from sase_github.errors import (
    GitHubAuthenticationError,
    GitHubIssueError,
    GitHubRateLimitError,
)

_ISSUE_JSON_FIELDS = (
    "number,title,state,body,labels,assignees,author,createdAt,updatedAt,url,comments"
)
_UNBOUNDED_ISSUE_LIMIT = 1_000_000
_AUTH_ERROR_MARKERS = (
    "authentication failed",
    "bad credentials",
    "gh auth login",
    "http 401",
    "not logged into any github hosts",
)
_RATE_LIMIT_ERROR_MARKERS = (
    "rate limit",
    "http 429",
    "too many requests",
)


def _command_error(operation: str, out: CommandOutput) -> GitHubIssueError:
    """Map a failed ``gh`` command to a stable, user-facing error."""
    details = out.stderr.strip() or out.stdout.strip() or "command failed"
    normalized = details.casefold()
    if out.returncode == 4 or any(
        marker in normalized for marker in _AUTH_ERROR_MARKERS
    ):
        return GitHubAuthenticationError(
            operation,
            "GitHub authentication required; run `gh auth login` and try again",
        )
    if any(marker in normalized for marker in _RATE_LIMIT_ERROR_MARKERS):
        return GitHubRateLimitError(
            operation,
            "GitHub API rate limit exceeded; try again later",
        )
    return GitHubIssueError(operation, details)


def _string_field(payload: dict[str, Any], name: str) -> str:
    """Return a nullable JSON string field as a normalized string."""
    value = payload.get(name)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    return value


def _login(payload: object, field_name: str) -> str:
    """Extract a GitHub login from an actor-shaped JSON value."""
    if payload is None:
        return ""
    if not isinstance(payload, dict):
        raise ValueError(f"{field_name} must be an object")
    value = payload.get("login")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{field_name}.login must be a string")
    return value


def _named_items(payload: object, field_name: str, key: str) -> tuple[str, ...]:
    """Extract names or logins from a list of GitHub JSON objects."""
    if payload is None:
        return ()
    if not isinstance(payload, list):
        raise ValueError(f"{field_name} must be a list")
    result: list[str] = []
    for item in payload:
        if not isinstance(item, dict) or not isinstance(item.get(key), str):
            raise ValueError(f"{field_name} entries must contain a string {key}")
        result.append(item[key])
    return tuple(result)


def _comment_count(payload: object) -> int:
    """Normalize comment JSON emitted by current and older ``gh`` versions."""
    if payload is None:
        return 0
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict) and isinstance(payload.get("totalCount"), int):
        return cast(int, payload["totalCount"])
    if isinstance(payload, int):
        return payload
    raise ValueError("comments must be a list or count")


def _issue_from_json(payload: object) -> IssueWire:
    """Normalize one ``gh issue --json`` object into :class:`IssueWire`."""
    if not isinstance(payload, dict):
        raise ValueError("issue must be an object")

    number = payload.get("number")
    title = payload.get("title")
    raw_state = payload.get("state")
    if not isinstance(number, int) or isinstance(number, bool):
        raise ValueError("number must be an integer")
    if not isinstance(title, str):
        raise ValueError("title must be a string")
    if not isinstance(raw_state, str) or raw_state.casefold() not in {
        "open",
        "closed",
    }:
        raise ValueError("state must be open or closed")

    state = cast(IssueState, raw_state.casefold())
    return IssueWire(
        number=number,
        title=title,
        state=state,
        body=_string_field(payload, "body"),
        labels=_named_items(payload.get("labels"), "labels", "name"),
        assignees=_named_items(payload.get("assignees"), "assignees", "login"),
        author=_login(payload.get("author"), "author"),
        created_at=_string_field(payload, "createdAt"),
        updated_at=_string_field(payload, "updatedAt"),
        url=_string_field(payload, "url"),
        comment_count=_comment_count(payload.get("comments")),
    )


def _ordered_unique(values: Sequence[str]) -> tuple[str, ...]:
    """Deduplicate values without changing their command-line order."""
    return tuple(dict.fromkeys(values))


class GitHubPlugin(GitCommon):
    """Pluggy plugin for GitHub-hosted git repositories."""

    @hookimpl
    def vcs_classify_repo(self, git_dir: str) -> str | None:
        """Claim repos whose origin host is a configured GitHub host."""
        try:
            result = subprocess.run(
                ["git", "config", "--get", "remote.origin.url"],
                cwd=git_dir,
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return None

        if result.returncode != 0:
            return None

        url = result.stdout.strip()
        host = normalize_github_host(url)
        if host in get_github_hosts():
            return "github"
        return None

    @hookimpl
    def vcs_can_rename_branch(self, cwd: str) -> bool:
        """GitHub branches are immutable once pushed with a PR."""
        return False

    # --- Optional issue-tracker operations ---

    def _run_issue_command(self, cmd: list[str], cwd: str, operation: str) -> str:
        out = self._run(cmd, cwd)
        if not out.success:
            raise _command_error(operation, out)
        return out.stdout

    def _run_issue_json(self, cmd: list[str], cwd: str, operation: str) -> object:
        raw = self._run_issue_command(cmd, cwd, operation)
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise GitHubIssueError(
                operation,
                f"gh returned invalid JSON: {exc.msg}",
            ) from exc

    def _view_issue(self, issue_ref: int | str, cwd: str) -> IssueWire:
        operation = "gh issue view"
        payload = self._run_issue_json(
            [
                "gh",
                "issue",
                "view",
                str(issue_ref),
                "--json",
                _ISSUE_JSON_FIELDS,
            ],
            cwd,
            operation,
        )
        try:
            return _issue_from_json(payload)
        except ValueError as exc:
            raise GitHubIssueError(
                operation,
                f"gh returned invalid issue JSON: {exc}",
            ) from exc

    @hookimpl
    def vcs_list_issues(
        self,
        cwd: str,
        state: IssueListState,
        limit: int,
    ) -> list[IssueWire]:
        """List GitHub issues and normalize ``gh`` JSON output."""
        operation = "gh issue list"
        payload = self._run_issue_json(
            [
                "gh",
                "issue",
                "list",
                "--state",
                state,
                "--limit",
                str(limit if limit > 0 else _UNBOUNDED_ISSUE_LIMIT),
                "--json",
                _ISSUE_JSON_FIELDS,
            ],
            cwd,
            operation,
        )
        if not isinstance(payload, list):
            raise GitHubIssueError(operation, "gh returned a non-list JSON value")
        try:
            return [_issue_from_json(issue) for issue in payload]
        except ValueError as exc:
            raise GitHubIssueError(
                operation,
                f"gh returned invalid issue JSON: {exc}",
            ) from exc

    @hookimpl
    def vcs_get_issue(self, number: int, cwd: str) -> IssueWire:
        """Return one GitHub issue by repository-local number."""
        return self._view_issue(number, cwd)

    @hookimpl
    def vcs_create_issue(
        self,
        title: str,
        body: str,
        labels: Sequence[str],
        cwd: str,
    ) -> IssueWire:
        """Create an issue, then retrieve its complete JSON representation."""
        operation = "gh issue create"
        cmd = ["gh", "issue", "create", "--title", title, "--body", body]
        for label in _ordered_unique(labels):
            cmd.extend(("--label", label))
        issue_ref = self._run_issue_command(cmd, cwd, operation).strip()
        if not issue_ref:
            raise GitHubIssueError(operation, "gh returned no created issue URL")
        return self._view_issue(issue_ref, cwd)

    @hookimpl
    def vcs_update_issue(
        self,
        number: int,
        cwd: str,
        title: str | None,
        body: str | None,
        state: IssueState | None,
        labels: Sequence[str] | None,
    ) -> IssueWire:
        """Edit issue fields and toggle state, returning refreshed JSON."""
        current = self._view_issue(number, cwd)
        edit_cmd = ["gh", "issue", "edit", str(number)]
        if title is not None:
            edit_cmd.extend(("--title", title))
        if body is not None:
            edit_cmd.extend(("--body", body))
        if labels is not None:
            desired_labels = _ordered_unique(labels)
            desired_set = set(desired_labels)
            current_set = set(current.labels)
            for label in current.labels:
                if label not in desired_set:
                    edit_cmd.extend(("--remove-label", label))
            for label in desired_labels:
                if label not in current_set:
                    edit_cmd.extend(("--add-label", label))
        if len(edit_cmd) > 4:
            self._run_issue_command(edit_cmd, cwd, "gh issue edit")

        if state is not None and state != current.state:
            state_operation = f"gh issue {'close' if state == 'closed' else 'reopen'}"
            self._run_issue_command(
                ["gh", "issue", state_operation.rsplit(" ", 1)[-1], str(number)],
                cwd,
                state_operation,
            )

        if len(edit_cmd) == 4 and (state is None or state == current.state):
            return current
        return self._view_issue(number, cwd)

    @hookimpl
    def vcs_get_issue_url(self, number: int, cwd: str) -> str:
        """Return the issue URL while requesting no other issue fields."""
        operation = "gh issue view"
        url = self._run_issue_command(
            [
                "gh",
                "issue",
                "view",
                str(number),
                "--json",
                "url",
                "--jq",
                ".url",
            ],
            cwd,
            operation,
        ).strip()
        if not url:
            raise GitHubIssueError(operation, "gh returned no issue URL")
        return url

    @hookimpl
    def vcs_abandon_change(
        self, cl: str, revision: str, cwd: str
    ) -> tuple[bool, str | None]:
        """Close the GitHub PR and delete the remote branch."""
        out = self._run(["gh", "pr", "close", cl, "--delete-branch"], cwd)
        if not out.success:
            # PR may already be closed/merged — treat as success
            if "already" in out.stderr.lower() or "not found" in out.stderr.lower():
                return (True, None)
            return self._to_result(out, "gh pr close")
        return (True, None)

    @hookimpl
    def vcs_get_change_url(self, cwd: str) -> tuple[bool, str | None]:
        out = self._run(["gh", "pr", "view", "--json", "url", "-q", ".url"], cwd)
        if out.success:
            url = out.stdout.strip()
            return (True, url) if url else (True, None)
        return (True, None)

    @hookimpl
    def vcs_get_change_body(self, change_ref: str, cwd: str) -> tuple[bool, str | None]:
        out = self._run(
            ["gh", "pr", "view", change_ref, "--json", "body", "-q", ".body"], cwd
        )
        if out.success:
            return (True, out.stdout.strip())
        return (False, out.stderr.strip())

    @hookimpl
    def vcs_get_pr_number(self, cwd: str) -> tuple[bool, str | None]:
        out = self._run(["gh", "pr", "view", "--json", "number", "-q", ".number"], cwd)
        if out.success:
            number = out.stdout.strip()
            return (True, number) if number else (True, None)
        return (True, None)

    @hookimpl
    def vcs_get_cl_number(self, cwd: str) -> tuple[bool, str | None]:
        return self.vcs_get_pr_number(cwd)

    @hookimpl
    def vcs_mail(self, revision: str, cwd: str) -> tuple[bool, str | None]:
        out = self._run(["git", "push", "-u", "origin", revision], cwd)
        if not out.success:
            return self._to_result(out, "git push")
        pr_check = self._run(
            ["gh", "pr", "view", "--json", "number", "-q", ".number"], cwd
        )
        if not pr_check.success:
            pr_create = self._run(["gh", "pr", "create", "--fill"], cwd)
            if not pr_create.success:
                return self._to_result(pr_create, "gh pr create")
        return (True, None)

    # --- Commit dispatch ---
    # vcs_create_commit and vcs_create_proposal are inherited from GitCommon.

    @hookimpl
    def vcs_create_pull_request(
        self, payload: dict, cwd: str
    ) -> tuple[bool, str | None]:
        # Common git operations (checkout -b, add, commit, push)
        ok, err = super().vcs_create_pull_request(payload, cwd)
        if not ok:
            return (ok, err)
        # GitHub-specific: create PR
        message = payload.get("message", "")
        body = payload.get("_pr_body", message)
        prefix = payload.get("_pr_title_prefix", "")
        title = prefix + message.split("\n", 1)[0]
        pr_out = self._run(
            ["gh", "pr", "create", "--title", title, "--body", body], cwd
        )
        if not pr_out.success:
            return self._to_result(pr_out, "gh pr create")
        return (True, pr_out.stdout.strip())
