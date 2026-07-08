"""Tests for GitHub configuration helpers."""

from unittest.mock import patch

from sase_github.config import (
    GitHubRemote,
    get_default_github_host,
    get_github_hosts,
    get_sdd_repo_name_override,
    normalize_github_host,
    parse_github_remote_url,
)


def test_get_github_hosts_defaults_to_github_com() -> None:
    with patch("sase_github.config.load_merged_config", return_value={}):
        assert get_github_hosts() == ["github.com"]
        assert get_default_github_host() == "github.com"


def test_get_github_hosts_normalizes_and_adds_github_com() -> None:
    with patch(
        "sase_github.config.load_merged_config",
        return_value={
            "github_hosts": [
                " https://GITHUB.ENTERPRISE.TEST/ ",
                "https://github.enterprise.test/org/repo",
                "github.com",
            ],
        },
    ):
        assert get_github_hosts() == ["github.enterprise.test", "github.com"]


def test_get_default_github_host_uses_first_configured_host() -> None:
    with patch(
        "sase_github.config.load_merged_config",
        return_value={"github_hosts": ["github.enterprise.test", "github.com"]},
    ):
        assert get_default_github_host() == "github.enterprise.test"


def test_normalize_github_host_accepts_urls_and_remotes() -> None:
    assert normalize_github_host("https://GITHUB.ENTERPRISE.TEST/org/repo") == (
        "github.enterprise.test"
    )
    assert normalize_github_host("git@github.enterprise.test:org/repo.git") == (
        "github.enterprise.test"
    )
    assert normalize_github_host("ssh://git@github.enterprise.test/org/repo.git") == (
        "github.enterprise.test"
    )
    assert normalize_github_host("") is None


def test_parse_github_remote_url_accepts_common_forms() -> None:
    assert parse_github_remote_url("https://github.com/Owner/repo.git") == (
        GitHubRemote("github.com", "Owner", "repo")
    )
    assert parse_github_remote_url("git@github.enterprise.test:org/repo.git") == (
        GitHubRemote("github.enterprise.test", "org", "repo")
    )
    assert parse_github_remote_url(
        "ssh://git@github.enterprise.test:2222/org/repo.git"
    ) == GitHubRemote("github.enterprise.test:2222", "org", "repo")


def test_parse_github_remote_url_rejects_non_remote_paths() -> None:
    assert parse_github_remote_url("/home/user/repo.git") is None
    assert parse_github_remote_url("https://github.com/org") is None
    assert parse_github_remote_url("") is None


def test_get_sdd_repo_name_override_reads_nested_config() -> None:
    with patch(
        "sase_github.config.load_merged_config",
        return_value={"sdd": {"repo": {"name": "sase-org/custom-sdd"}}},
    ):
        assert get_sdd_repo_name_override() == "sase-org/custom-sdd"


def test_get_sdd_repo_name_override_ignores_blank_values() -> None:
    with patch(
        "sase_github.config.load_merged_config",
        return_value={"sdd": {"repo": {"name": "  "}}},
    ):
        assert get_sdd_repo_name_override() is None
