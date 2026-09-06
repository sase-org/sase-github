"""Tests for the #new_pr_desc xprompt's get-context script."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from sase_github.scripts import new_pr_desc_get_context


def test_main_reports_context_for_found_patch(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The script resolves the Patch through the canonical ``sase.ace.patch`` API."""
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    diff_dir = tmp_path / "gh-diffs"
    diff_dir.mkdir()
    entry = SimpleNamespace(
        name="feature",
        file_path=str(workspace_dir / "feature.sase"),
        description="Add feature",
    )

    with (
        patch(
            "sase_github.scripts.new_pr_desc_get_context.find_all_patches",
            return_value=[entry],
        ),
        patch(
            "sase_github.scripts.new_pr_desc_get_context.parse_workspace_dir",
            return_value=str(workspace_dir),
        ),
        patch(
            "sase_github.scripts.new_pr_desc_get_context.get_default_branch",
            return_value="origin/main",
        ),
        patch(
            "sase_github.scripts.new_pr_desc_get_context.subprocess.run",
            return_value=SimpleNamespace(returncode=0, stdout=""),
        ),
        patch(
            "sase_github.scripts.new_pr_desc_get_context.get_sase_managed_tmpdir",
            return_value=str(diff_dir),
        ),
    ):
        new_pr_desc_get_context.main(name="feature")

    out = capsys.readouterr().out
    assert "error=\n" in out
    assert "description=Add feature\n" in out
    assert "default_branch=main\n" in out
    assert "branch_name=feature\n" in out


def test_main_reports_error_for_missing_patch(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with patch(
        "sase_github.scripts.new_pr_desc_get_context.find_all_patches",
        return_value=[],
    ):
        new_pr_desc_get_context.main(name="missing")

    out = capsys.readouterr().out
    assert "error=Patch 'missing' not found\n" in out
