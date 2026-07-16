"""sase-github: GitHub VCS plugin for sase."""

from sase_github.errors import (
    GitHubAuthenticationError,
    GitHubIssueError,
    GitHubRateLimitError,
)
from sase_github.plugin import GitHubPlugin
from sase_github.workspace_plugin import GitHubWorkspacePlugin

__all__ = [
    "GitHubAuthenticationError",
    "GitHubIssueError",
    "GitHubPlugin",
    "GitHubRateLimitError",
    "GitHubWorkspacePlugin",
]
