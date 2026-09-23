"""Origin inspection and GitHub remote handling."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from sase.workspace_provider.utils import non_interactive_git_env

if TYPE_CHECKING:
    from sase_github.config import GitHubRemote


def read_github_origin(workspace_dir: str) -> GitHubRemote | None:
    from sase_github.config import get_github_hosts, parse_github_remote_url

    origin = read_git_origin(Path(workspace_dir).expanduser())
    parsed = parse_github_remote_url(origin)
    if parsed is None:
        return None
    if parsed.host not in get_github_hosts():
        return None
    return parsed


def read_git_origin(cwd: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "config", "--get", "remote.origin.url"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
            env=non_interactive_git_env(),
            stdin=subprocess.DEVNULL,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def remote_matches_repo(
    remote_url: str,
    host: str,
    owner: str,
    repo: str,
) -> bool:
    from sase_github.config import parse_github_remote_url

    parsed = parse_github_remote_url(remote_url)
    if parsed is None:
        return False
    return (
        parsed.host == host
        and parsed.owner.casefold() == owner.casefold()
        and parsed.repo.casefold() == repo.casefold()
    )


def clone_gh_repo(
    user: str,
    project: str,
    target_dir: str,
    *,
    host: str | None = None,
) -> str:
    """Clone a GitHub repo to the target directory."""
    from sase_github.config import get_default_github_host

    github_host = host or get_default_github_host()
    target = Path(target_dir.rstrip("/"))
    if os.path.lexists(target):
        raise RuntimeError(f"git clone destination already exists: {target}")
    parent = os.path.dirname(str(target))
    os.makedirs(parent, exist_ok=True)

    attempts = (
        ("SSH", github_ssh_url(github_host, user, project)),
        ("HTTPS", _github_https_url(github_host, user, project)),
    )
    failures: list[str] = []
    first_error: BaseException | None = None
    for label, url in attempts:
        try:
            subprocess.run(
                ["git", "clone", url, str(target)],
                capture_output=True,
                text=True,
                check=True,
                env=non_interactive_git_env(),
                stdin=subprocess.DEVNULL,
            )
            return url
        except subprocess.CalledProcessError as e:
            if first_error is None:
                first_error = e
            failures.append(_format_clone_failure(label, url, e))
            _remove_failed_clone_target(target)
        except FileNotFoundError as e:
            _remove_failed_clone_target(target)
            raise RuntimeError("git clone failed: git command not found") from e

    detail = "\n".join(failures)
    error_msg = f"git clone failed for {user}/{project}"
    if detail:
        error_msg += f":\n{detail}"
    error_msg += "\nIf authentication failed, run 'gh auth login' and retry."
    raise RuntimeError(error_msg) from first_error


def _remove_failed_clone_target(target: Path) -> None:
    """Remove only the clone target created by a failed attempt."""

    if target.is_dir() and not target.is_symlink():
        shutil.rmtree(target, ignore_errors=True)
        return
    try:
        target.unlink(missing_ok=True)
    except OSError:
        pass


def github_ssh_url(host: str, user: str, project: str) -> str:
    if ":" in host:
        return f"ssh://git@{host}/{user}/{project}.git"
    return f"git@{host}:{user}/{project}.git"


def _github_https_url(host: str, user: str, project: str) -> str:
    return f"https://{host}/{user}/{project}.git"


def _format_clone_failure(
    label: str,
    url: str,
    exc: subprocess.CalledProcessError,
) -> str:
    output = "\n".join(part for part in (exc.stderr, exc.stdout) if part).strip()
    message = f"{label} clone from {url} failed (exit code {exc.returncode})"
    if output:
        message += f": {output}"
    return message
