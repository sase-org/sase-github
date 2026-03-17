"""Regression tests for the #pr workflow branch-creation step."""

from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run a command in a test repo and return the completed process."""
    return subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def _extract_create_branch_script() -> str:
    """Extract the bash body for the create_branch step from pr.yml."""
    workflow_path = (
        Path(__file__).resolve().parents[1] / "src" / "sase_github" / "xprompts" / "pr.yml"
    )
    text = workflow_path.read_text(encoding="utf-8")
    start_marker = "  - name: create_branch\n    bash: |\n"
    end_marker = "    output:\n      branch_name: word\n"

    start = text.find(start_marker)
    assert start != -1, "create_branch step not found in pr.yml"
    start += len(start_marker)

    end = text.find(end_marker, start)
    assert end != -1, "create_branch output block not found in pr.yml"

    block = text[start:end]
    return textwrap.dedent(block)


def _init_repo(path: Path) -> None:
    """Initialize a git repo with one commit."""
    _run(["git", "init"], cwd=path)
    _run(["git", "config", "user.name", "Test User"], cwd=path)
    _run(["git", "config", "user.email", "test@example.com"], cwd=path)
    (path / "README.md").write_text("hello\n", encoding="utf-8")
    _run(["git", "add", "README.md"], cwd=path)
    _run(["git", "commit", "-m", "chore: init"], cwd=path)


def test_create_branch_step_fails_when_branch_already_exists(tmp_path: Path) -> None:
    """create_branch must fail if requested branch already exists."""
    _init_repo(tmp_path)
    initial_branch = _run(["git", "branch", "--show-current"], cwd=tmp_path).stdout.strip()
    _run(["git", "checkout", "-b", "existing"], cwd=tmp_path)
    _run(["git", "checkout", initial_branch], cwd=tmp_path)

    script = _extract_create_branch_script().replace("{{ name }}", "existing")
    result = _run(["bash", "-c", script], cwd=tmp_path)

    assert result.returncode != 0
    current_branch = _run(["git", "branch", "--show-current"], cwd=tmp_path).stdout.strip()
    assert current_branch == initial_branch


def test_create_branch_step_fails_when_push_fails(tmp_path: Path) -> None:
    """create_branch must fail if push to origin fails."""
    _init_repo(tmp_path)

    script = _extract_create_branch_script().replace("{{ name }}", "new_branch")
    result = _run(["bash", "-c", script], cwd=tmp_path)

    # No 'origin' remote exists in this test repo, so push must fail and the
    # workflow step should fail instead of silently continuing.
    assert result.returncode != 0
