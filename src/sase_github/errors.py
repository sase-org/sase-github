"""Typed failures raised by GitHub-specific provider operations."""

from sase.vcs_provider import VCSOperationError


class GitHubIssueError(VCSOperationError):
    """Base error for a failed GitHub issue operation."""


class GitHubAuthenticationError(GitHubIssueError):
    """Raised when ``gh`` is not authenticated for an issue operation."""


class GitHubRateLimitError(GitHubIssueError):
    """Raised when GitHub rejects an issue operation due to rate limiting."""


__all__ = [
    "GitHubAuthenticationError",
    "GitHubIssueError",
    "GitHubRateLimitError",
]
