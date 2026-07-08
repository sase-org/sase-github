"""GitHub configuration helpers."""

from dataclasses import dataclass
import re
from collections.abc import Iterable
from urllib.parse import urlparse

from sase.config import load_merged_config

DEFAULT_GITHUB_HOST = "github.com"


@dataclass(frozen=True)
class GitHubRemote:
    """Parsed GitHub remote origin coordinates."""

    host: str
    owner: str
    repo: str


def normalize_github_host(value: object) -> str | None:
    """Normalize a configured GitHub host or pasted GitHub URL."""
    if value is None:
        return None

    raw = str(value).strip().lower().rstrip("/")
    if not raw:
        return None

    # Accept pasted scp-style remotes such as git@github.example.com:org/repo.git.
    scp_match = re.match(r"^(?:[^@/]+@)?([^:/]+):[^/]+/.+", raw)
    if "://" not in raw and scp_match:
        return scp_match.group(1)

    if "://" in raw:
        parsed = urlparse(raw)
        host = parsed.netloc.rsplit("@", 1)[-1]
    else:
        host = raw.split("/", 1)[0].rsplit("@", 1)[-1]

    return host or None


def parse_github_remote_url(value: object) -> GitHubRemote | None:
    """Parse a git remote URL into GitHub host, owner, and repo coordinates."""
    if value is None:
        return None

    raw = str(value).strip()
    if not raw:
        return None

    host: str
    path: str
    if "://" not in raw:
        scp_match = re.match(r"^(?:[^@/]+@)?(?P<host>[^:/]+):(?P<path>.+)$", raw)
        if not scp_match:
            return None
        host = scp_match.group("host")
        path = scp_match.group("path")
    else:
        parsed = urlparse(raw)
        host = parsed.netloc.rsplit("@", 1)[-1]
        path = parsed.path.lstrip("/")

    host = host.strip().lower().rstrip("/")
    if not host or not path:
        return None

    parts = [part for part in path.strip("/").split("/") if part]
    if len(parts) != 2:
        return None

    owner = parts[0].strip()
    repo = parts[1].removesuffix(".git").strip()
    if not owner or not repo:
        return None

    return GitHubRemote(host=host, owner=owner, repo=repo)


def _config_list(value: object) -> list[object]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, Iterable) and not isinstance(value, str):
        return list(value)
    if value:
        return [value]
    return []


def _dedupe_hosts(hosts: Iterable[str]) -> list[str]:
    deduped: list[str] = []
    for host in hosts:
        if host not in deduped:
            deduped.append(host)
    return deduped


def get_github_orgs() -> list[str]:
    """Read ``github_orgs`` from the merged sase config.

    Returns:
        A list of GitHub org/user names the user has push access to.
    """
    config = load_merged_config()
    value = config.get("github_orgs")
    if isinstance(value, list):
        return [str(v) for v in value if v]
    if value:
        return [str(value)]
    return []


def get_github_hosts() -> list[str]:
    """Read configured GitHub hosts, always including ``github.com``."""
    config = load_merged_config()
    configured_hosts = [
        host
        for item in _config_list(config.get("github_hosts"))
        if (host := normalize_github_host(item)) is not None
    ]
    return _dedupe_hosts([*configured_hosts, DEFAULT_GITHUB_HOST])


def get_default_github_host() -> str:
    """Return the host used for bare ``#gh(owner/repo)`` clone refs."""
    config = load_merged_config()
    for item in _config_list(config.get("github_hosts")):
        host = normalize_github_host(item)
        if host:
            return host
    return DEFAULT_GITHUB_HOST


def get_sdd_repo_name_override() -> str | None:
    """Return the optional ``sdd.repo.name`` companion-repo override."""
    config = load_merged_config()
    raw_sdd = config.get("sdd", {}) if isinstance(config, dict) else {}
    sdd = raw_sdd if isinstance(raw_sdd, dict) else {}
    raw_repo = sdd.get("repo", {})
    repo = raw_repo if isinstance(raw_repo, dict) else {}
    raw_name = repo.get("name")
    if not isinstance(raw_name, str):
        return None
    name = raw_name.strip()
    return name or None
