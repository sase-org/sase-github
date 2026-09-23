"""SDD sidecar repo identity and `gh` operations on it."""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Mapping
from typing import Literal

from sase_github.workspace.gh_cli import (
    DEFAULT_GH_TIMEOUT_SECONDS,
    looks_like_already_exists_error,
    looks_like_auth_error,
    looks_like_network_error,
    looks_like_not_found_error,
    non_interactive_gh_env,
)

_SDD_SIDECAR_LABEL = "sase--sdd"
_SDD_SIDECAR_LABEL_DESCRIPTION = "SASE SDD sidecar repository"
_SDD_SIDECAR_LABEL_COLOR = "0e8a16"

SddRepoProbe = Literal["found", "not_found", "unavailable"]


def sidecar_sdd_candidates(
    owner: str, repo: str, *, suffix: str = "sdd"
) -> list[tuple[str, str]]:
    """Return sidecar candidates, preserving the legacy ``sdd`` override."""

    suffix = _validate_sdd_sidecar_suffix(suffix)
    if suffix != "sdd":
        return [(owner, f"{repo}--{suffix}")]

    from sase_github.config import get_sdd_repo_name_override

    override = get_sdd_repo_name_override()
    if override is None:
        return [(owner, f"{repo}--sdd")]

    parts = [part for part in override.strip("/").split("/") if part]
    if len(parts) == 1:
        return [(owner, parts[0])]
    if len(parts) == 2:
        return [(parts[0], parts[1])]
    raise ValueError("sdd.repo.name must be a repo name or owner/repo")


def sdd_sidecar_suffix(options: Mapping[str, object]) -> str:
    raw = options.get("sdd_sidecar_suffix", options.get("sidecar_suffix", "sdd"))
    if not isinstance(raw, str):
        raise RuntimeError("SDD sidecar suffix must be a string")
    return _validate_sdd_sidecar_suffix(raw)


def _validate_sdd_sidecar_suffix(suffix: str) -> str:
    normalized = suffix.strip().removeprefix("--")
    if not normalized or re.fullmatch(r"[a-z0-9][a-z0-9-]*", normalized) is None:
        raise RuntimeError(f"invalid SDD sidecar suffix: {suffix!r}")
    return normalized


def probe_github_repo_detail(
    host: str, repo_full_name: str
) -> tuple[SddRepoProbe, str | None]:
    env = non_interactive_gh_env()
    env["GH_HOST"] = host
    try:
        result = subprocess.run(
            [
                "gh",
                "repo",
                "view",
                repo_full_name,
                "--json",
                "name,isArchived",
                "-q",
                "[.name, .isArchived] | @tsv",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=_sdd_network_timeout(),
            env=env,
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return (
            "unavailable",
            "gh not found. Install the GitHub CLI, then re-run `sase sdd init`.",
        )
    except subprocess.TimeoutExpired:
        return (
            "unavailable",
            f"could not reach {host}. Check your connection, then re-run "
            "`sase sdd init`.",
        )
    except OSError as exc:
        return (
            "unavailable",
            f"could not run gh repo view for {repo_full_name}: {exc}",
        )

    if result.returncode == 0:
        fields = result.stdout.strip().split("\t")
        if len(fields) >= 2 and fields[1].casefold() == "true":
            return (
                "unavailable",
                f"{repo_full_name} is archived and read-only; if this project "
                "migrated to split sidecars run `sase sdd init`, otherwise "
                "unarchive the repo",
            )
        if fields and fields[0]:
            return "found", None
        return (
            "unavailable",
            f"could not verify GitHub repository {repo_full_name}: empty response",
        )

    output = "\n".join(part for part in (result.stderr, result.stdout) if part).strip()
    normalized = output.casefold()
    if looks_like_not_found_error(normalized):
        return "not_found", None
    if looks_like_auth_error(normalized):
        return (
            "unavailable",
            "GitHub CLI is not authenticated. Run `gh auth login`, then "
            "re-run `sase sdd init`.",
        )
    if looks_like_network_error(normalized):
        return (
            "unavailable",
            f"could not reach {host}. Check your connection, then re-run "
            "`sase sdd init`.",
        )
    detail = output.splitlines()[0] if output else "unknown gh repo view failure"
    return (
        "unavailable",
        f"could not verify GitHub repository {repo_full_name}: {detail}",
    )


def _probe_github_repo(host: str, repo_full_name: str) -> SddRepoProbe:
    probe, _message = probe_github_repo_detail(host, repo_full_name)
    return probe


def create_github_sdd_repo(
    host: str,
    repo_full_name: str,
    *,
    source_repo_full_name: str,
    sidecar_suffix: str = "sdd",
) -> bool:
    env = non_interactive_gh_env()
    env["GH_HOST"] = host
    try:
        result = subprocess.run(
            [
                "gh",
                "repo",
                "create",
                repo_full_name,
                "--public",
                "--description",
                _sdd_sidecar_description(
                    source_repo_full_name, sidecar_suffix=sidecar_suffix
                ),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=_sdd_network_timeout(),
            env=env,
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "gh not found. Install the GitHub CLI, then re-run `sase sdd init`."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"could not reach {host}. Check your connection, then re-run "
            "`sase sdd init`."
        ) from exc
    except OSError as exc:
        raise RuntimeError(f"gh repo create failed: {exc}") from exc

    if result.returncode == 0:
        return True

    if result.returncode != 0:
        detail = "\n".join(part for part in (result.stderr, result.stdout) if part)
        detail = detail.strip()
        normalized = detail.casefold()
        if looks_like_already_exists_error(normalized) and (
            _probe_github_repo(host, repo_full_name) == "found"
        ):
            return False
        message = f"gh repo create failed for {repo_full_name}"
        if detail:
            message += f": {detail}"
        owner = repo_full_name.split("/", 1)[0]
        message += f". You may lack repo-create rights in {owner}."
        raise RuntimeError(message)

    return True


def _sdd_sidecar_description(source_repo_full_name: str, *, sidecar_suffix: str) -> str:
    suffix = _validate_sdd_sidecar_suffix(sidecar_suffix)
    if suffix == "sdd":
        return f"SDD sidecar repository for {source_repo_full_name}"
    return f"SASE {suffix} sidecar repository for {source_repo_full_name}"


def ensure_github_sdd_label(host: str, repo_full_name: str) -> None:
    """Best-effort creation of the sidecar marker label.

    The label is informational repository metadata; tokens without
    label-management rights must still be able to complete sidecar
    init, so failures surface as warnings instead of aborting.
    """
    try:
        _create_github_sdd_label(host, repo_full_name)
    except RuntimeError as exc:
        print(f"warning: {exc}", file=sys.stderr)


def _create_github_sdd_label(host: str, repo_full_name: str) -> None:
    env = non_interactive_gh_env()
    env["GH_HOST"] = host
    try:
        result = subprocess.run(
            [
                "gh",
                "label",
                "create",
                _SDD_SIDECAR_LABEL,
                "--repo",
                repo_full_name,
                "--description",
                _SDD_SIDECAR_LABEL_DESCRIPTION,
                "--color",
                _SDD_SIDECAR_LABEL_COLOR,
                "--force",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=_sdd_network_timeout(),
            env=env,
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "gh not found. Install the GitHub CLI, then re-run `sase sdd init`."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"could not reach {host}. Check your connection, then re-run "
            "`sase sdd init`."
        ) from exc
    except OSError as exc:
        raise RuntimeError(
            f"could not run gh label create for {repo_full_name}: {exc}"
        ) from exc

    if result.returncode == 0:
        return

    detail = "\n".join(part for part in (result.stderr, result.stdout) if part).strip()
    normalized = detail.casefold()
    if looks_like_auth_error(normalized):
        raise RuntimeError(
            "GitHub CLI is not authenticated. Run `gh auth login`, then "
            "re-run `sase sdd init`."
        )
    if looks_like_network_error(normalized):
        raise RuntimeError(
            f"could not reach {host}. Check your connection, then re-run "
            "`sase sdd init`."
        )

    message = (
        f"could not create or update GitHub label {_SDD_SIDECAR_LABEL} on "
        f"{repo_full_name}"
    )
    if detail:
        message += f": {detail}"
    message += (
        ". Check that your GitHub token has write or label-management "
        "permissions, then re-run `sase sdd init`."
    )
    raise RuntimeError(message)


def _sdd_network_timeout() -> float:
    try:
        from sase.sdd._commit import network_git_timeout

        return network_git_timeout()
    except Exception:
        return float(DEFAULT_GH_TIMEOUT_SECONDS)
