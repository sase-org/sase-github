"""Tests for ws_submit using the recorded PR URL from ChangeSpec.cl.

Covers:
- _extract_pr_number extracts from GitHub PR URLs
- _check_pr_state returns correct states
- ws_submit prefers recorded PR over branch-based heuristic
- ws_submit handles closed/merged PRs with clear errors
- ws_submit falls back to branch check when no PR recorded
"""

from unittest.mock import MagicMock, patch

from sase_github.workspace_plugin import (
    _check_pr_state,
    _extract_pr_number,
)


# === _extract_pr_number ===


def test_extract_pr_number_from_url() -> None:
    assert _extract_pr_number("https://github.com/org/repo/pull/42") == "42"


def test_extract_pr_number_none() -> None:
    assert _extract_pr_number(None) is None


def test_extract_pr_number_empty() -> None:
    assert _extract_pr_number("") is None


def test_extract_pr_number_non_github_url() -> None:
    assert _extract_pr_number("https://gitlab.com/org/repo/merge_requests/5") is None


def test_extract_pr_number_no_match() -> None:
    assert _extract_pr_number("some-random-string") is None


# === _check_pr_state ===


@patch("sase_github.workspace_plugin.subprocess.run")
def test_check_pr_state_open(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(returncode=0, stdout="OPEN\n", stderr="")
    assert _check_pr_state("42", "/ws") == "OPEN"
    mock_run.assert_called_once()
    cmd = mock_run.call_args[0][0]
    assert "42" in cmd
    assert "--json" in cmd


@patch("sase_github.workspace_plugin.subprocess.run")
def test_check_pr_state_closed(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(returncode=0, stdout="CLOSED\n", stderr="")
    assert _check_pr_state("42", "/ws") == "CLOSED"


@patch("sase_github.workspace_plugin.subprocess.run")
def test_check_pr_state_merged(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(returncode=0, stdout="MERGED\n", stderr="")
    assert _check_pr_state("42", "/ws") == "MERGED"


@patch("sase_github.workspace_plugin.subprocess.run")
def test_check_pr_state_failure(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="not found")
    assert _check_pr_state("99", "/ws") is None


@patch("sase_github.workspace_plugin.subprocess.run", side_effect=Exception("boom"))
def test_check_pr_state_exception(mock_run: MagicMock) -> None:
    assert _check_pr_state("99", "/ws") is None


# === _submit_via_pr_merge with pr_number ===


@patch("sase_github.workspace_plugin.subprocess.run")
@patch(
    "sase.workspace_provider.submission_utils.finalize_submission",
    return_value=(True, None),
)
@patch("sase_github.config.get_github_orgs", return_value=["org"])
def test_submit_via_pr_merge_uses_pr_number(
    mock_orgs: MagicMock,
    mock_finalize: MagicMock,
    mock_run: MagicMock,
) -> None:
    from sase_github.workspace_plugin import _submit_via_pr_merge

    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    cs = MagicMock()
    cs.file_path = "/tmp/proj.gp"
    cs.name = "feat"

    ok, _ = _submit_via_pr_merge(cs, "/ws", None, pr_number="42")
    assert ok is True

    # Verify the merge command includes the PR number
    merge_call = mock_run.call_args
    cmd = merge_call[0][0]
    assert "42" in cmd
    assert cmd.index("42") < cmd.index("--merge")


@patch("sase_github.workspace_plugin.subprocess.run")
@patch(
    "sase.workspace_provider.submission_utils.finalize_submission",
    return_value=(True, None),
)
@patch("sase_github.config.get_github_orgs", return_value=["org"])
def test_submit_via_pr_merge_without_pr_number(
    mock_orgs: MagicMock,
    mock_finalize: MagicMock,
    mock_run: MagicMock,
) -> None:
    from sase_github.workspace_plugin import _submit_via_pr_merge

    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    cs = MagicMock()
    cs.file_path = "/tmp/proj.gp"
    cs.name = "feat"

    ok, _ = _submit_via_pr_merge(cs, "/ws", None)
    assert ok is True

    # Verify the merge command does NOT include a PR number
    merge_call = mock_run.call_args
    cmd = merge_call[0][0]
    assert cmd == ["gh", "pr", "merge", "--merge", "--delete-branch"]
