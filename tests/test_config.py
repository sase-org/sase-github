"""Tests for GitHub configuration helpers."""

from unittest.mock import patch

from sase_github.config import (
    get_default_github_host,
    get_github_hosts,
    normalize_github_host,
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
