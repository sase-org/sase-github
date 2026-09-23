"""Shared `gh` plumbing for the GitHub workspace provider."""

from __future__ import annotations

from collections.abc import Mapping

from sase.workspace_provider.utils import non_interactive_git_env

DEFAULT_GH_TIMEOUT_SECONDS = 10


def non_interactive_gh_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    env = non_interactive_git_env(base)
    env["GH_PROMPT_DISABLED"] = "1"
    return env


def looks_like_auth_error(text: str) -> bool:
    # HTTP 403 is deliberately absent: gh reports it for valid tokens
    # that lack permission (e.g. fine-grained PATs), where `gh auth
    # login` is the wrong remedy.
    markers = (
        "auth login",
        "authentication required",
        "not logged in",
        "requires authentication",
        "bad credentials",
        "http 401",
        "status code 401",
    )
    return any(marker in text for marker in markers)


def looks_like_not_found_error(text: str) -> bool:
    markers = (
        "could not resolve to a user",
        "could not resolve to an organization",
        "could not resolve to a repository",
        "not found",
        "http 404",
        "status code 404",
    )
    return any(marker in text for marker in markers)


def looks_like_already_exists_error(text: str) -> bool:
    markers = (
        "already exists",
        "already taken",
        "name already exists",
        "name is already taken",
    )
    return any(marker in text for marker in markers)


def looks_like_network_error(text: str) -> bool:
    markers = (
        "could not resolve host",
        "failed to connect",
        "connection refused",
        "connection reset",
        "i/o timeout",
        "network",
        "no such host",
        "temporary failure",
        "tls handshake timeout",
        "timeout",
    )
    return any(marker in text for marker in markers)
