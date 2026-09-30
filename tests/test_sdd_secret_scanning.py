"""Tests for the `sdd_secret_scanning` provider option (phase sase-1d5.2)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from sase_github.workspace.sdd_repo import (
    ensure_github_secret_scanning,
    sdd_secret_scanning,
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
        args=args or ["cmd"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


EXPECTED_PAYLOAD = {
    "security_and_analysis": {
        "secret_scanning": {"status": "enabled"},
        "secret_scanning_push_protection": {"status": "enabled"},
    }
}


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        ({"sdd_secret_scanning": True}, True),
        ({}, False),
        ({"sdd_secret_scanning": False}, False),
        ({"sdd_secret_scanning": None}, False),
        ({"sdd_secret_scanning": "true"}, False),
        ({"sdd_secret_scanning": 1}, False),
        ({"sdd_secret_scanning": "yes"}, False),
    ],
)
def test_sdd_secret_scanning_requires_literal_true(
    options: dict[str, object], expected: bool
) -> None:
    assert sdd_secret_scanning(options) is expected


def test_secret_scanning_patch_runs_for_public_repo_with_option(
    tmp_path: Path,
) -> None:
    primary = tmp_path / "widget"
    primary.mkdir()
    calls: list[list[str]] = []
    inputs: list[object] = []

    def run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        if cmd == ["git", "config", "--get", "remote.origin.url"]:
            return _completed(stdout="https://github.com/acme/widget.git\n")
        if cmd[:3] == ["gh", "repo", "view"]:
            return _completed(returncode=1, stderr="repository not found")
        if cmd[:3] == ["gh", "repo", "create"]:
            return _completed(stdout="created\n")
        if cmd[:4] == ["gh", "api", "-X", "PATCH"]:
            inputs.append(kwargs.get("input"))
            return _completed(stdout="{}\n")
        if cmd[:3] == ["gh", "label", "create"]:
            return _completed()
        raise AssertionError(f"unexpected command: {cmd}")

    with patch("subprocess.run", side_effect=run):
        record = GitHubWorkspacePlugin().ws_create_sdd_remote(
            str(primary),
            str(primary),
            {
                "create": True,
                "sdd_creation_authorized": True,
                "sdd_secret_scanning": True,
            },
        )

    assert record is not None
    assert record["created"] is True
    api_calls = [cmd for cmd in calls if cmd[:2] == ["gh", "api"]]
    assert api_calls == [
        ["gh", "api", "-X", "PATCH", "repos/acme/widget--sdd", "--input", "-"]
    ]
    assert json.loads(str(inputs[0])) == EXPECTED_PAYLOAD


def test_secret_scanning_patch_failure_warns_without_failing_creation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    primary = tmp_path / "widget"
    primary.mkdir()

    def run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if cmd == ["git", "config", "--get", "remote.origin.url"]:
            return _completed(stdout="https://github.com/acme/widget.git\n")
        if cmd[:3] == ["gh", "repo", "view"]:
            return _completed(returncode=1, stderr="repository not found")
        if cmd[:3] == ["gh", "repo", "create"]:
            return _completed(stdout="created\n")
        if cmd[:4] == ["gh", "api", "-X", "PATCH"]:
            return _completed(returncode=1, stderr="HTTP 403: Forbidden")
        if cmd[:3] == ["gh", "label", "create"]:
            return _completed()
        raise AssertionError(f"unexpected command: {cmd}")

    with patch("subprocess.run", side_effect=run):
        record = GitHubWorkspacePlugin().ws_create_sdd_remote(
            str(primary),
            str(primary),
            {
                "create": True,
                "sdd_creation_authorized": True,
                "sdd_secret_scanning": True,
            },
        )

    assert record is not None
    assert record["created"] is True
    assert record["repo"] == "acme/widget--sdd"
    err = capsys.readouterr().err
    assert "could not enable secret scanning on acme/widget--sdd" in err
    assert "gh api -X PATCH repos/acme/widget--sdd" in err


def test_no_patch_without_option(tmp_path: Path) -> None:
    primary = tmp_path / "widget"
    primary.mkdir()
    calls: list[list[str]] = []

    def run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        if cmd == ["git", "config", "--get", "remote.origin.url"]:
            return _completed(stdout="https://github.com/acme/widget.git\n")
        if cmd[:3] == ["gh", "repo", "view"]:
            return _completed(returncode=1, stderr="repository not found")
        if cmd[:3] == ["gh", "repo", "create"]:
            return _completed(stdout="created\n")
        if cmd[:3] == ["gh", "label", "create"]:
            return _completed()
        raise AssertionError(f"unexpected command: {cmd}")

    with patch("subprocess.run", side_effect=run):
        record = GitHubWorkspacePlugin().ws_create_sdd_remote(
            str(primary),
            str(primary),
            {"create": True, "sdd_creation_authorized": True},
        )

    assert record is not None
    assert record["created"] is True
    assert not [cmd for cmd in calls if cmd[:2] == ["gh", "api"]]


def test_no_patch_for_private_repo_with_option(tmp_path: Path) -> None:
    primary = tmp_path / "widget"
    primary.mkdir()
    calls: list[list[str]] = []

    def run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        if cmd == ["git", "config", "--get", "remote.origin.url"]:
            return _completed(stdout="https://github.com/acme/widget.git\n")
        if cmd[:3] == ["gh", "repo", "view"]:
            return _completed(returncode=1, stderr="repository not found")
        if cmd[:3] == ["gh", "repo", "create"]:
            assert "--private" in cmd
            return _completed(stdout="created\n")
        if cmd[:3] == ["gh", "label", "create"]:
            return _completed()
        raise AssertionError(f"unexpected command: {cmd}")

    with patch("subprocess.run", side_effect=run):
        record = GitHubWorkspacePlugin().ws_create_sdd_remote(
            str(primary),
            str(primary),
            {
                "create": True,
                "sdd_creation_authorized": True,
                "sdd_visibility": "private",
                "sdd_secret_scanning": True,
            },
        )

    assert record is not None
    assert record["created"] is True
    assert not [cmd for cmd in calls if cmd[:2] == ["gh", "api"]]


def test_no_patch_for_adopted_repo_with_option(tmp_path: Path) -> None:
    primary = tmp_path / "widget"
    primary.mkdir()
    calls: list[list[str]] = []

    def run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        if cmd == ["git", "config", "--get", "remote.origin.url"]:
            return _completed(stdout="https://github.com/acme/widget.git\n")
        if cmd[:3] == ["gh", "repo", "view"]:
            return _completed(stdout="widget--sdd\n")
        if cmd[:3] == ["gh", "label", "create"]:
            return _completed()
        raise AssertionError(f"unexpected command: {cmd}")

    with patch("subprocess.run", side_effect=run):
        record = GitHubWorkspacePlugin().ws_create_sdd_remote(
            str(primary),
            str(primary),
            {"sdd_secret_scanning": True},
        )

    assert record is not None
    assert record["created"] is False
    assert not [cmd for cmd in calls if cmd[:2] == ["gh", "api"]]


def test_ensure_secret_scanning_transport_failure_warns(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with patch("subprocess.run", side_effect=OSError("boom")):
        assert ensure_github_secret_scanning("github.com", "acme/widget--sdd") is False
    err = capsys.readouterr().err
    assert "could not enable secret scanning on acme/widget--sdd" in err
    assert "gh api -X PATCH repos/acme/widget--sdd" in err
