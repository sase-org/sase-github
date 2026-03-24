"""GitHub VCS plugin implementation.

Handles git repositories hosted on GitHub (or similar hosted services).
Inherits shared git operations from :class:`GitCommon` and adds
GitHub-specific methods (``mail`` with PR creation, ``get_cl_number``
and ``get_change_url`` via ``gh`` CLI).
"""

import subprocess

from sase.vcs_provider._hookspec import hookimpl
from sase.vcs_provider.plugins._git_common import GitCommon


class GitHubPlugin(GitCommon):
    """Pluggy plugin for GitHub-hosted git repositories."""

    @hookimpl
    def vcs_classify_repo(self, git_dir: str) -> str | None:
        """Claim repos with ``github.com`` in their origin URL."""
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
        if "github.com" in url:
            return "github"
        return None

    @hookimpl
    def vcs_get_change_url(self, cwd: str) -> tuple[bool, str | None]:
        out = self._run(["gh", "pr", "view", "--json", "url", "-q", ".url"], cwd)
        if out.success:
            url = out.stdout.strip()
            return (True, url) if url else (True, None)
        return (True, None)

    @hookimpl
    def vcs_get_cl_number(self, cwd: str) -> tuple[bool, str | None]:
        out = self._run(["gh", "pr", "view", "--json", "number", "-q", ".number"], cwd)
        if out.success:
            number = out.stdout.strip()
            return (True, number) if number else (True, None)
        return (True, None)

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
        title = message.split("\n", 1)[0]
        pr_out = self._run(
            ["gh", "pr", "create", "--title", title, "--body", message], cwd
        )
        if not pr_out.success:
            return self._to_result(pr_out, "gh pr create")
        return (True, pr_out.stdout.strip())
