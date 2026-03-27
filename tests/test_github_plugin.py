"""Tests for the GitHub pluggy plugin.

Verifies that :class:`GitHubPlugin` works correctly when routed through
:class:`VCSPluginManager`.
"""

from unittest.mock import MagicMock, patch

import pluggy
import pytest
from sase.vcs_provider._base import VCSProvider
from sase.vcs_provider._command_runner import CommandRunner
from sase.vcs_provider._hookspec import VCSHookSpec
from sase.vcs_provider._plugin_manager import VCSPluginManager
from sase_github.plugin import GitHubPlugin

_MOCK_TARGET = "sase.vcs_provider._command_runner.subprocess.run"


@pytest.fixture
def github_provider() -> VCSPluginManager:
    """Create a VCSPluginManager backed by GitHubPlugin."""
    pm = pluggy.PluginManager("sase_vcs")
    pm.add_hookspecs(VCSHookSpec)
    pm.register(GitHubPlugin())
    return VCSPluginManager(pm)


# === Tests for isinstance / type checks ===


def test_github_plugin_provider_is_vcs_provider(
    github_provider: VCSPluginManager,
) -> None:
    """The plugin-backed provider is a VCSProvider."""
    assert isinstance(github_provider, VCSProvider)


def test_github_plugin_is_command_runner() -> None:
    """GitHubPlugin inherits from CommandRunner."""
    plugin = GitHubPlugin()
    assert isinstance(plugin, CommandRunner)


# === Tests for core git operations via plugin ===


@patch(_MOCK_TARGET)
def test_plugin_checkout_success(
    mock_run: MagicMock, github_provider: VCSPluginManager
) -> None:
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    success, error = github_provider.checkout("main", "/workspace")

    assert success is True
    assert error is None
    assert mock_run.call_args[0][0] == ["git", "checkout", "main"]


@patch(_MOCK_TARGET)
def test_plugin_diff_success(
    mock_run: MagicMock, github_provider: VCSPluginManager
) -> None:
    mock_run.return_value = MagicMock(returncode=0, stdout="diff output", stderr="")
    success, text = github_provider.diff("/workspace")

    assert success is True
    assert text == "diff output"


@patch(_MOCK_TARGET)
def test_plugin_add_remove(
    mock_run: MagicMock, github_provider: VCSPluginManager
) -> None:
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    success, error = github_provider.add_remove("/workspace")

    assert success is True
    assert error is None
    assert mock_run.call_args[0][0] == ["git", "add", "-A"]


@patch(_MOCK_TARGET)
def test_plugin_commit(mock_run: MagicMock, github_provider: VCSPluginManager) -> None:
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    success, error = github_provider.commit("feature", "/tmp/msg.txt", "/workspace")

    assert success is True
    assert error is None


# === Tests for abandon_change ===


@patch(_MOCK_TARGET)
def test_plugin_abandon_change_success(
    mock_run: MagicMock, github_provider: VCSPluginManager
) -> None:
    """abandon_change closes the PR and deletes the remote branch."""
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    success, error = github_provider.abandon_change(
        "https://github.com/user/repo/pull/42", "feature-branch", "/workspace"
    )

    assert success is True
    assert error is None
    assert mock_run.call_args[0][0] == [
        "gh",
        "pr",
        "close",
        "https://github.com/user/repo/pull/42",
        "--delete-branch",
    ]


@patch(_MOCK_TARGET)
def test_plugin_abandon_change_already_closed(
    mock_run: MagicMock, github_provider: VCSPluginManager
) -> None:
    """abandon_change succeeds when PR is already closed."""
    mock_run.return_value = MagicMock(
        returncode=1, stdout="", stderr="already closed"
    )
    success, error = github_provider.abandon_change(
        "https://github.com/user/repo/pull/42", "feature-branch", "/workspace"
    )

    assert success is True
    assert error is None


@patch(_MOCK_TARGET)
def test_plugin_abandon_change_not_found(
    mock_run: MagicMock, github_provider: VCSPluginManager
) -> None:
    """abandon_change succeeds when PR is not found."""
    mock_run.return_value = MagicMock(
        returncode=1, stdout="", stderr="not found"
    )
    success, error = github_provider.abandon_change(
        "https://github.com/user/repo/pull/42", "feature-branch", "/workspace"
    )

    assert success is True
    assert error is None


@patch(_MOCK_TARGET)
def test_plugin_abandon_change_failure(
    mock_run: MagicMock, github_provider: VCSPluginManager
) -> None:
    """abandon_change returns error on unexpected failure."""
    mock_run.return_value = MagicMock(
        returncode=1, stdout="", stderr="network error"
    )
    success, error = github_provider.abandon_change(
        "https://github.com/user/repo/pull/42", "feature-branch", "/workspace"
    )

    assert success is False
    assert error is not None
    assert "gh pr close failed" in error


# === Tests for GitHub-specific operations ===


@patch(_MOCK_TARGET)
def test_plugin_get_change_url_with_pr(
    mock_run: MagicMock, github_provider: VCSPluginManager
) -> None:
    mock_run.return_value = MagicMock(
        returncode=0, stdout="https://github.com/user/repo/pull/42\n", stderr=""
    )
    success, url = github_provider.get_change_url("/workspace")

    assert success is True
    assert url == "https://github.com/user/repo/pull/42"


@patch(_MOCK_TARGET)
def test_plugin_get_change_url_no_pr(
    mock_run: MagicMock, github_provider: VCSPluginManager
) -> None:
    mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="no PR")
    success, url = github_provider.get_change_url("/workspace")

    assert success is True
    assert url is None


@patch(_MOCK_TARGET)
def test_plugin_get_cl_number_with_pr(
    mock_run: MagicMock, github_provider: VCSPluginManager
) -> None:
    mock_run.return_value = MagicMock(returncode=0, stdout="42\n", stderr="")
    success, number = github_provider.get_cl_number("/workspace")

    assert success is True
    assert number == "42"


@patch(_MOCK_TARGET)
def test_plugin_get_cl_number_no_pr(
    mock_run: MagicMock, github_provider: VCSPluginManager
) -> None:
    mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="no PR")
    success, number = github_provider.get_cl_number("/workspace")

    assert success is True
    assert number is None


@patch(_MOCK_TARGET)
def test_plugin_mail_creates_pr(
    mock_run: MagicMock, github_provider: VCSPluginManager
) -> None:
    """mail pushes and creates a PR when none exists."""
    # First call: git push (success)
    # Second call: gh pr view (fail = no existing PR)
    # Third call: gh pr create (success)
    mock_run.side_effect = [
        MagicMock(returncode=0, stdout="", stderr=""),
        MagicMock(returncode=1, stdout="", stderr="no PR"),
        MagicMock(returncode=0, stdout="", stderr=""),
    ]
    success, error = github_provider.mail("feature-branch", "/workspace")

    assert success is True
    assert error is None
    assert mock_run.call_count == 3
    assert mock_run.call_args_list[0][0][0] == [
        "git",
        "push",
        "-u",
        "origin",
        "feature-branch",
    ]
    assert mock_run.call_args_list[2][0][0] == ["gh", "pr", "create", "--fill"]


@patch(_MOCK_TARGET)
def test_plugin_mail_existing_pr(
    mock_run: MagicMock, github_provider: VCSPluginManager
) -> None:
    """mail pushes but skips PR creation when PR already exists."""
    mock_run.side_effect = [
        MagicMock(returncode=0, stdout="", stderr=""),  # git push
        MagicMock(returncode=0, stdout="42\n", stderr=""),  # gh pr view (PR exists)
    ]
    success, error = github_provider.mail("feature-branch", "/workspace")

    assert success is True
    assert error is None
    assert mock_run.call_count == 2


# === Tests for prepare_description_for_reword ===


def test_plugin_prepare_description_passthrough(
    github_provider: VCSPluginManager,
) -> None:
    """Git plugins pass description through unchanged."""
    result = github_provider.prepare_description_for_reword("hello\nworld")
    assert result == "hello\nworld"


# === Direct plugin method tests (GitHub-specific) ===


@patch(_MOCK_TARGET)
def test_direct_get_change_url_with_pr(mock_run: MagicMock) -> None:
    """Test GitHubPlugin.vcs_get_change_url when PR exists."""
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout="https://github.com/user/repo/pull/42\n",
        stderr="",
    )

    plugin = GitHubPlugin()
    success, url = plugin.vcs_get_change_url("/workspace")

    assert success is True
    assert url == "https://github.com/user/repo/pull/42"


@patch(_MOCK_TARGET)
def test_direct_get_change_url_no_pr(mock_run: MagicMock) -> None:
    """Test GitHubPlugin.vcs_get_change_url when no PR exists."""
    mock_run.return_value = MagicMock(
        returncode=1, stdout="", stderr="no pull requests found"
    )

    plugin = GitHubPlugin()
    success, url = plugin.vcs_get_change_url("/workspace")

    assert success is True
    assert url is None


@patch(_MOCK_TARGET)
def test_direct_get_cl_number_with_pr(mock_run: MagicMock) -> None:
    """Test GitHubPlugin.vcs_get_cl_number when PR exists."""
    mock_run.return_value = MagicMock(returncode=0, stdout="42\n", stderr="")

    plugin = GitHubPlugin()
    success, number = plugin.vcs_get_cl_number("/workspace")

    assert success is True
    assert number == "42"


@patch(_MOCK_TARGET)
def test_direct_get_cl_number_no_pr(mock_run: MagicMock) -> None:
    """Test GitHubPlugin.vcs_get_cl_number when no PR exists."""
    mock_run.return_value = MagicMock(
        returncode=1, stdout="", stderr="no pull requests found"
    )

    plugin = GitHubPlugin()
    success, number = plugin.vcs_get_cl_number("/workspace")

    assert success is True
    assert number is None


@patch(_MOCK_TARGET)
def test_direct_mail_push_and_create_pr(mock_run: MagicMock) -> None:
    """Test GitHubPlugin.vcs_mail pushes and creates PR when none exists."""
    mock_run.side_effect = [
        MagicMock(returncode=0, stdout="", stderr=""),  # git push
        MagicMock(returncode=1, stdout="", stderr="no PR"),  # gh pr view (no PR)
        MagicMock(returncode=0, stdout="", stderr=""),  # gh pr create
    ]

    plugin = GitHubPlugin()
    success, error = plugin.vcs_mail("feature-branch", "/workspace")

    assert success is True
    assert error is None
    assert mock_run.call_count == 3
    assert mock_run.call_args_list[0][0][0] == [
        "git",
        "push",
        "-u",
        "origin",
        "feature-branch",
    ]
    assert mock_run.call_args_list[2][0][0] == ["gh", "pr", "create", "--fill"]


@patch(_MOCK_TARGET)
def test_direct_mail_push_existing_pr(mock_run: MagicMock) -> None:
    """Test GitHubPlugin.vcs_mail just pushes when PR already exists."""
    mock_run.side_effect = [
        MagicMock(returncode=0, stdout="", stderr=""),  # git push
        MagicMock(returncode=0, stdout="42\n", stderr=""),  # gh pr view (PR exists)
    ]

    plugin = GitHubPlugin()
    success, error = plugin.vcs_mail("feature-branch", "/workspace")

    assert success is True
    assert error is None
    assert mock_run.call_count == 2


# === Tests for commit dispatch hooks ===


@patch(_MOCK_TARGET)
def test_vcs_create_commit_success(
    mock_run: MagicMock, github_provider: VCSPluginManager
) -> None:
    """All git commands (add, validate, merge, commit, push) succeed."""

    def _side_effect(*args, **kwargs):
        cmd = args[0]
        if cmd == ["git", "diff", "--cached", "--quiet"]:
            return MagicMock(returncode=1, stdout="", stderr="")
        if cmd[:2] == ["git", "rev-parse"]:
            return MagicMock(returncode=0, stdout="abc1234\n", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    mock_run.side_effect = _side_effect
    ok, result = github_provider.create_commit(
        {"message": "fix: bug", "files": ["a.py"]}, "/workspace"
    )

    assert ok is True
    assert result == "abc1234"


@patch(_MOCK_TARGET)
def test_vcs_create_commit_add_fails(
    mock_run: MagicMock, github_provider: VCSPluginManager
) -> None:
    """Returns error when git add fails."""
    mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="pathspec error")
    ok, err = github_provider.create_commit(
        {"message": "test", "files": ["missing.py"]}, "/ws"
    )

    assert ok is False
    assert isinstance(err, str)


@patch(_MOCK_TARGET)
def test_vcs_create_commit_specific_files(
    mock_run: MagicMock, github_provider: VCSPluginManager
) -> None:
    """Specific files list is passed to git add."""
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    github_provider.create_commit(
        {"message": "fix: bug", "files": ["a.py", "b.py"]}, "/ws"
    )

    add_call = mock_run.call_args_list[0]
    cmd = add_call[0][0]
    assert cmd == ["git", "add", "--", "a.py", "b.py"]


@patch("sase.workflows.commit_utils.workspace.clean_workspace")
@patch("sase.workflows.commit_utils.workspace.save_diff")
def test_vcs_create_proposal_saves_diff_and_cleans(
    mock_save_diff: MagicMock,
    mock_clean: MagicMock,
    github_provider: VCSPluginManager,
) -> None:
    """create_proposal saves a diff and cleans the workspace."""
    mock_save_diff.return_value = "/ws/some.diff"
    ok, result = github_provider.create_proposal({"message": "propose: change"}, "/ws")

    assert ok is True
    assert result == "/ws/some.diff"
    mock_save_diff.assert_called_once()
    mock_clean.assert_called_once_with("/ws")


@patch(_MOCK_TARGET)
def test_vcs_create_pull_request_success(
    mock_run: MagicMock, github_provider: VCSPluginManager
) -> None:
    """Full PR flow: checkout -b, add, validate, commit, push, gh pr create."""

    def _side_effect(*args, **kwargs):
        cmd = args[0]
        if cmd == ["git", "diff", "--cached", "--quiet"]:
            return MagicMock(returncode=1, stdout="", stderr="")
        if cmd[:3] == ["gh", "pr", "create"]:
            return MagicMock(
                returncode=0,
                stdout="https://github.com/user/repo/pull/99\n",
                stderr="",
            )
        return MagicMock(returncode=0, stdout="", stderr="")

    mock_run.side_effect = _side_effect
    ok, result = github_provider.create_pull_request(
        {"name": "feat-x", "message": "add feature", "files": []}, "/ws"
    )

    assert ok is True
    assert result == "https://github.com/user/repo/pull/99"
    # Verify branch creation
    assert mock_run.call_args_list[0][0][0] == ["git", "checkout", "-b", "feat-x"]
    # Verify gh pr create uses message for title/body
    pr_cmd = mock_run.call_args_list[-1][0][0]
    assert pr_cmd[:3] == ["gh", "pr", "create"]


@patch(_MOCK_TARGET)
def test_vcs_create_pull_request_pr_create_fails(
    mock_run: MagicMock, github_provider: VCSPluginManager
) -> None:
    """Returns error when gh pr create fails."""
    mock_run.side_effect = [
        MagicMock(returncode=0, stdout="", stderr=""),  # checkout -b
        MagicMock(returncode=0, stdout="", stderr=""),  # add
        MagicMock(returncode=0, stdout="", stderr=""),  # commit
        MagicMock(returncode=0, stdout="", stderr=""),  # push -u
        MagicMock(returncode=1, stdout="", stderr="error creating PR"),  # gh pr create
    ]
    ok, err = github_provider.create_pull_request(
        {"name": "feat-x", "message": "test", "files": []}, "/ws"
    )

    assert ok is False
    assert isinstance(err, str)


# === Direct plugin method tests (commit dispatch) ===


# === Direct plugin method tests (abandon_change) ===


@patch(_MOCK_TARGET)
def test_direct_abandon_change_success(mock_run: MagicMock) -> None:
    """Test GitHubPlugin.vcs_abandon_change closes PR successfully."""
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

    plugin = GitHubPlugin()
    success, error = plugin.vcs_abandon_change(
        "https://github.com/user/repo/pull/42", "feature-branch", "/workspace"
    )

    assert success is True
    assert error is None
    assert mock_run.call_args[0][0] == [
        "gh",
        "pr",
        "close",
        "https://github.com/user/repo/pull/42",
        "--delete-branch",
    ]


@patch(_MOCK_TARGET)
def test_direct_abandon_change_already_closed(mock_run: MagicMock) -> None:
    """Test GitHubPlugin.vcs_abandon_change when PR is already closed."""
    mock_run.return_value = MagicMock(
        returncode=1, stdout="", stderr="already closed"
    )

    plugin = GitHubPlugin()
    success, error = plugin.vcs_abandon_change(
        "https://github.com/user/repo/pull/42", "feature-branch", "/workspace"
    )

    assert success is True
    assert error is None


@patch(_MOCK_TARGET)
def test_direct_abandon_change_failure(mock_run: MagicMock) -> None:
    """Test GitHubPlugin.vcs_abandon_change on unexpected error."""
    mock_run.return_value = MagicMock(
        returncode=1, stdout="", stderr="permission denied"
    )

    plugin = GitHubPlugin()
    success, error = plugin.vcs_abandon_change(
        "https://github.com/user/repo/pull/42", "feature-branch", "/workspace"
    )

    assert success is False
    assert error is not None
    assert "gh pr close failed" in error


@patch(_MOCK_TARGET)
def test_direct_mail_push_fails(mock_run: MagicMock) -> None:
    """Test GitHubPlugin.vcs_mail when push fails."""
    mock_run.return_value = MagicMock(
        returncode=1, stdout="", stderr="permission denied"
    )

    plugin = GitHubPlugin()
    success, error = plugin.vcs_mail("feature-branch", "/workspace")

    assert success is False
    assert error is not None
    assert "git push failed" in error
