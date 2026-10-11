"""GitHub onboarding hooks for the New Project wizard.

Implements the five additive workspace-provider onboarding hookspecs for
``workflow_type="gh"``: ``ws_parse_repo_ref``, ``ws_list_local_repo_candidates``,
``ws_probe_repo``, ``ws_describe_repo_creation`` and ``ws_create_repo``.

Every ``gh`` invocation here runs with :func:`non_interactive_gh_env`, an
explicit ``GH_HOST``, ``stdin=DEVNULL`` and a bounded timeout. Tests fake
``gh`` through the existing ``subprocess.run`` patching style; live
repositories are never created by tests.
"""

from __future__ import annotations

import configparser
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from sase.workspace_provider import (
    LocalRepoCandidate,
    LocalRepoCandidates,
    ParsedRepoRef,
    RepoCreateResult,
    RepoCreationChoice,
    RepoCreationField,
    RepoCreationForm,
    RepoProbe,
)
from sase_github.workspace.gh_cli import (
    DEFAULT_GH_TIMEOUT_SECONDS,
    looks_like_already_exists_error,
    looks_like_auth_error,
    looks_like_network_error,
    looks_like_not_found_error,
    non_interactive_gh_env,
)

#: Timeout for the `gh repo view` probe call, in seconds.
PROBE_TIMEOUT_SECONDS = 10

#: Timeout for the raw `sase/sase.yml` content read, in seconds.
SASE_YML_TIMEOUT_SECONDS = 10

#: Timeout for repository creation, in seconds.
CREATE_TIMEOUT_SECONDS = 30

#: Timeout for creation-form choice-list queries, in seconds.
CHOICE_LIST_TIMEOUT_SECONDS = 10

#: TTL for cached creation-form choice lists, in seconds (24 h).
CHOICE_LIST_CACHE_TTL_SECONDS = 24 * 3600

#: Cap on local candidates reported in one call.
_LOCAL_CANDIDATE_CAP = 500

#: `viewerPermission` values that grant push access.
_PUSH_PERMISSIONS = frozenset({"ADMIN", "MAINTAIN", "WRITE"})

#: Characters GitHub allows in repository names; anything else becomes `-`.
_REPO_NAME_INVALID_RE = re.compile(r"[^A-Za-z0-9._-]+")

#: A trailing `_<digits>` marks a disambiguated numbered copy.
_NUMBERED_COPY_RE = re.compile(r"_\d+$")

#: scp-like remote spelling: `[user@]host:path`.
_SCP_LIKE_RE = re.compile(r"^(?:[^@/]+@)?(?P<host>[^:/]+):(?P<path>.+)$")

#: Matches `is_sase_managed: true` in a raw `sase/sase.yml` read.
_SASE_MANAGED_RE = re.compile(
    r"(?im)^\s*is_sase_managed\s*:\s*(?:true|yes|1)\s*(?:#.*)?$"
)

_PROBE_JSON_FIELDS = (
    "id,name,owner,visibility,isEmpty,isArchived,"
    "viewerPermission,defaultBranchRef,url,description"
)

_VERIFY_JSON_FIELDS = "id,visibility,url"

_TRUE_STRINGS = frozenset({"true", "yes", "on", "1"})
_FALSE_STRINGS = frozenset({"false", "no", "off", "0"})


def _strip_port(host: str) -> str:
    if host.startswith("["):
        end = host.find("]")
        return host[: end + 1] if end != -1 else host
    if host.count(":") == 1:
        return host.split(":")[0]
    return host


def _normalize_name(part: str) -> str:
    return _REPO_NAME_INVALID_RE.sub("-", part)


def _split_owner_repo(path: str) -> tuple[str, str] | None:
    parts = [part for part in path.strip("/").split("/") if part]
    if len(parts) != 2:
        return None
    owner, repo = parts
    repo = repo.removesuffix(".git")
    if not owner or not repo:
        return None
    return owner, repo


def _build_parsed(
    host: str,
    owner: str,
    repo: str,
    *,
    normalized_from: str = "",
) -> ParsedRepoRef:
    from sase_github.workspace.projects import github_workspace_dir

    ref = f"{owner}/{repo}"
    return ParsedRepoRef(
        workflow_type="gh",
        ref=ref,
        identity_key=f"{host}/{owner}/{repo}".casefold(),
        display=ref,
        host=host,
        owner=owner,
        name=repo,
        project_key_base=f"gh_{owner}__{repo}",
        project_name_base=repo,
        checkout_dir=github_workspace_dir(owner, repo, host),
        web_url=f"https://{host}/{owner}/{repo}",
        normalized_from=normalized_from,
    )


def parse_gh_repo_ref(text: str) -> ParsedRepoRef | None:
    """Parse user text into a canonical GitHub reference.

    Pure: no filesystem, network, or subprocess effects. Accepts
    ``owner/repo``, ``gh:owner/repo``, ``https://<host>/owner/repo[.git]``
    (for configured GitHub hosts only) and scp/ssh remote spellings
    (``git@<host>:owner/repo.git``,
    ``ssh://git@<host>[:port]/owner/repo.git``).
    """
    from sase_github.config import get_default_github_host, get_github_hosts

    original = text
    value = text.strip()
    if value.startswith("gh:"):
        value = value[3:].strip()
    if not value:
        return None

    hosts = {host.casefold() for host in get_github_hosts()}

    host: str | None = None
    coords: tuple[str, str] | None = None
    if "://" in value:
        from urllib.parse import urlparse

        parsed_url = urlparse(value)
        candidate_host = _strip_port(
            parsed_url.netloc.rsplit("@", 1)[-1].strip().lower()
        )
        if not candidate_host or candidate_host.casefold() not in hosts:
            return None
        coords = _split_owner_repo(parsed_url.path)
        if coords is None:
            return None
        host = candidate_host
    elif _SCP_LIKE_RE.match(value) and ":" in value:
        match = _SCP_LIKE_RE.match(value)
        assert match is not None
        candidate_host = match.group("host").strip().lower()
        if not candidate_host or candidate_host.casefold() not in hosts:
            return None
        coords = _split_owner_repo(match.group("path"))
        if coords is None:
            return None
        host = candidate_host
    else:
        coords = _split_owner_repo(value)
        if coords is None:
            return None
        host = get_default_github_host()

    raw_owner, raw_repo = coords
    owner = _normalize_name(raw_owner)
    repo = _normalize_name(raw_repo)
    if not owner or not repo:
        return None
    normalized_from = original if (owner, repo) != (raw_owner, raw_repo) else ""
    return _build_parsed(host, owner, repo, normalized_from=normalized_from)


def _origin_from_config(config_path: Path) -> str:
    parser = configparser.ConfigParser(interpolation=None)
    try:
        with open(config_path, encoding="utf-8") as f:
            parser.read_file(f)
        return parser.get('remote "origin"', "url", fallback="").strip()
    except (OSError, configparser.Error, UnicodeDecodeError):
        return ""


def _iter_candidate_dirs(root: Path) -> list[tuple[Path, str, str, str]]:
    """Return ``(dir, host, owner, repo)`` triples under the github layout root.

    Two levels (``<owner>/<repo>``) resolve against the default host; three
    levels (``<host>/<owner>/<repo>``) carry an explicit host. Depth and entry
    counts are bounded; ``OSError`` on any level yields no entries there.
    """
    from sase_github.config import get_default_github_host

    default_host = get_default_github_host()
    found: list[tuple[Path, str, str, str]] = []
    try:
        level_one = sorted(
            (entry for entry in root.iterdir() if entry.is_dir()),
            key=lambda entry: entry.name.casefold(),
        )
    except OSError:
        return found
    for owner_or_host in level_one:
        try:
            level_two = sorted(
                (entry for entry in owner_or_host.iterdir() if entry.is_dir()),
                key=lambda entry: entry.name.casefold(),
            )
        except OSError:
            continue
        for second in level_two:
            found.append((second, default_host, owner_or_host.name, second.name))
            try:
                level_three = sorted(
                    (entry for entry in second.iterdir() if entry.is_dir()),
                    key=lambda entry: entry.name.casefold(),
                )
            except OSError:
                continue
            for third in level_three:
                found.append((third, owner_or_host.name, second.name, third.name))
    return found


def list_gh_local_repo_candidates() -> LocalRepoCandidates:
    """Discover local GitHub checkouts without subprocesses or network.

    Walks ``~/projects/github/<owner>/<repo>/`` (and host-prefixed
    ``<host>/<owner>/<repo>/``) to a bounded depth, reads each ``origin``
    from ``.git/config`` directly, and keeps entries whose origin identity
    matches the directory. Siblings named ``*_<digits>`` are marked
    ``numbered_copy``.
    """
    from sase_github.config import parse_github_remote_url

    root = Path.home() / "projects" / "github"
    candidates: dict[tuple[str, str], LocalRepoCandidate] = {}
    for directory, host, owner, repo in _iter_candidate_dirs(root):
        if len(candidates) >= _LOCAL_CANDIDATE_CAP:
            break
        origin = _origin_from_config(directory / ".git" / "config")
        if not origin:
            continue
        parsed_origin = parse_github_remote_url(origin)
        if parsed_origin is None:
            continue
        expected_identity = f"{host}/{owner}/{repo}".casefold()
        actual_identity = (
            f"{parsed_origin.host}/{parsed_origin.owner}/{parsed_origin.repo}"
        ).casefold()
        if actual_identity != expected_identity:
            continue
        parsed = _build_parsed(
            parsed_origin.host, parsed_origin.owner, parsed_origin.repo
        )
        parsed = ParsedRepoRef(
            workflow_type=parsed.workflow_type,
            ref=parsed.ref,
            identity_key=parsed.identity_key,
            display=parsed.display,
            host=parsed.host,
            owner=parsed.owner,
            name=parsed.name,
            project_key_base=parsed.project_key_base,
            project_name_base=parsed.project_name_base,
            checkout_dir=str(directory) + "/",
            web_url=parsed.web_url,
            normalized_from=parsed.normalized_from,
        )
        candidate = LocalRepoCandidate(
            parsed=parsed,
            path=str(directory),
            origin_url=origin,
            numbered_copy=_NUMBERED_COPY_RE.search(directory.name) is not None,
        )
        candidates[(candidate.path.casefold(), parsed.identity_key)] = candidate
    return LocalRepoCandidates(
        entries=tuple(
            sorted(
                candidates.values(),
                key=lambda candidate: (
                    candidate.parsed.owner.casefold(),
                    candidate.parsed.name.casefold(),
                    candidate.path.casefold(),
                ),
            )
        )
    )


def read_gh_account(host: str | None = None) -> str:
    """Return the signed-in login from gh's local ``hosts.yml``.

    No subprocess and no network. Returns ``""`` when nothing is known.
    ``GH_CONFIG_DIR`` overrides the config location (also used by tests).
    """
    from sase_github.config import get_default_github_host

    target = host or get_default_github_host()
    config_dir = os.environ.get("GH_CONFIG_DIR", "").strip()
    base = Path(config_dir) if config_dir else Path.home() / ".config" / "gh"
    try:
        raw = (base / "hosts.yml").read_text(encoding="utf-8")
    except OSError:
        return ""
    try:
        import yaml  # type: ignore[import-untyped]

        data = yaml.safe_load(raw)
    except Exception:
        return ""
    if not isinstance(data, dict):
        return ""
    for key, entry in data.items():
        if str(key).casefold() != target.casefold():
            continue
        if not isinstance(entry, dict):
            return ""
        user = entry.get("user", "")
        return user if isinstance(user, str) and user.strip() else ""
    return ""


def _gh_env(host: str) -> dict[str, str]:
    env = non_interactive_gh_env()
    env["GH_HOST"] = host
    return env


def _auth_remedy(host: str) -> tuple[str, ...]:
    return ("gh", "auth", "login", "-h", host)


def _probe_not_found(parsed: ParsedRepoRef, message: str) -> RepoProbe:
    return RepoProbe(
        status="not_found",
        parsed=parsed,
        error_kind="not_found",
        message=message,
        web_url=parsed.web_url,
    )


def _probe_unavailable(
    parsed: ParsedRepoRef,
    error_kind: Any,
    message: str,
    *,
    remedy_argv: tuple[str, ...] = (),
) -> RepoProbe:
    return RepoProbe(
        status="unavailable",
        parsed=parsed,
        error_kind=error_kind,
        message=message,
        remedy_argv=remedy_argv,
        web_url=parsed.web_url,
    )


def _read_sase_managed(owner: str, repo: str, host: str) -> bool | None:
    """Read the remote default branch's ``sase/sase.yml`` marker.

    Returns ``True`` when it marks the repo managed, ``False`` on 404, and
    ``None`` when the read fails any other way.
    """
    try:
        result = subprocess.run(
            [
                "gh",
                "api",
                "-H",
                "Accept: application/vnd.github.raw",
                f"repos/{owner}/{repo}/contents/sase/sase.yml",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=SASE_YML_TIMEOUT_SECONDS,
            env=_gh_env(host),
            stdin=subprocess.DEVNULL,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode == 0:
        return _SASE_MANAGED_RE.search(result.stdout) is not None
    output = "\n".join(
        part for part in (result.stderr, result.stdout) if part
    ).casefold()
    if looks_like_not_found_error(output):
        return False
    return None


def _string_or_empty(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    return value if isinstance(value, str) else ""


def probe_gh_repo(ref: str) -> RepoProbe | None:
    """Read-only, bounded probe of one GitHub repository.

    Never clones or writes. ``can_push`` is true for ``ADMIN``, ``MAINTAIN``
    and ``WRITE`` viewer permissions.
    """
    parsed = parse_gh_repo_ref(ref)
    if parsed is None:
        return None
    owner, _, name = parsed.ref.partition("/")
    if not owner or not name:
        return None
    host = parsed.host
    try:
        result = subprocess.run(
            ["gh", "repo", "view", f"{owner}/{name}", "--json", _PROBE_JSON_FIELDS],
            capture_output=True,
            text=True,
            check=False,
            timeout=PROBE_TIMEOUT_SECONDS,
            env=_gh_env(host),
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return _probe_unavailable(parsed, "tool_missing", "install the gh CLI")
    except subprocess.TimeoutExpired:
        return _probe_unavailable(parsed, "network", "probe timed out — network error")
    except OSError:
        return _probe_unavailable(parsed, "tool_missing", "install the gh CLI")

    if result.returncode != 0:
        output = "\n".join(
            part for part in (result.stderr, result.stdout) if part
        ).strip()
        normalized = output.casefold()
        first_line = output.splitlines()[0] if output else "probe failed"
        if looks_like_auth_error(normalized):
            return _probe_unavailable(
                parsed,
                "auth",
                "run 'gh auth login' to probe GitHub repositories",
                remedy_argv=_auth_remedy(host),
            )
        if looks_like_not_found_error(normalized):
            return _probe_not_found(parsed, first_line)
        if looks_like_network_error(normalized):
            return _probe_unavailable(parsed, "network", first_line)
        return _probe_unavailable(parsed, "unknown", first_line)

    try:
        data = json.loads(result.stdout or "null")
    except json.JSONDecodeError:
        return _probe_unavailable(
            parsed, "unknown", "probe failed - unexpected gh output"
        )
    if not isinstance(data, dict):
        return _probe_unavailable(
            parsed, "unknown", "probe failed - unexpected gh output"
        )

    default_branch_ref = data.get("defaultBranchRef")
    default_branch = (
        default_branch_ref.get("name", "")
        if isinstance(default_branch_ref, dict)
        else ""
    )
    if not isinstance(default_branch, str):
        default_branch = ""
    permission = _string_or_empty(data, "viewerPermission").upper()
    raw_empty = data.get("isEmpty")
    raw_archived = data.get("isArchived")
    return RepoProbe(
        status="found",
        parsed=parsed,
        visibility=_string_or_empty(data, "visibility").lower(),
        description=_string_or_empty(data, "description"),
        default_branch=default_branch,
        is_empty=bool(raw_empty) if isinstance(raw_empty, bool) else None,
        is_archived=bool(raw_archived) if isinstance(raw_archived, bool) else None,
        can_push=permission in _PUSH_PERMISSIONS if permission else None,
        sase_managed=_read_sase_managed(owner, name, host),
        repo_id=_string_or_empty(data, "id"),
        web_url=_string_or_empty(data, "url") or parsed.web_url,
    )


def _creation_cache_path(key: str) -> Path:
    from sase.core.paths import sase_subdir

    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", key).strip("._") or "default"
    return sase_subdir("vcs_repo_cache") / f"gh_creation_{safe}.json"


def _read_cached_values(key: str) -> list[str] | None:
    try:
        payload = json.loads(_creation_cache_path(key).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    fetched_at = payload.get("fetched_at", 0)
    values = payload.get("values", [])
    if (
        not isinstance(fetched_at, (int, float))
        or not isinstance(values, list)
        or time.time() - fetched_at > CHOICE_LIST_CACHE_TTL_SECONDS
    ):
        return None
    return [str(value) for value in values]


def _write_cached_values(key: str, values: list[str]) -> None:
    path = _creation_cache_path(key)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_text(
            json.dumps({"fetched_at": time.time(), "values": values}),
            encoding="utf-8",
        )
        os.replace(tmp, path)
    except OSError:
        pass


def _run_gh(
    argv: list[str], host: str, timeout: int
) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            argv,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
            env=_gh_env(host),
            stdin=subprocess.DEVNULL,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None


def _cached_choice_lines(
    key: str, argv: list[str], host: str, *, full_json: str = ""
) -> tuple[list[str], bool]:
    """Return cached-or-fresh choice lines and whether `gh` ran authed-ok.

    The second element is False when the fetch failed with an auth-classified
    error, so callers can offer the login remedy.
    """
    cached = _read_cached_values(key)
    if cached is not None:
        return cached, True
    result = _run_gh(argv, host, CHOICE_LIST_TIMEOUT_SECONDS)
    if result is None or result.returncode != 0:
        output = ""
        if result is not None:
            output = "\n".join(
                part for part in (result.stderr, result.stdout) if part
            ).casefold()
        return [], not looks_like_auth_error(output)
    if full_json:
        try:
            data = json.loads(result.stdout or "null")
        except json.JSONDecodeError:
            return [], True
        lines = _teams_from_json(data) if full_json == "teams" else []
    else:
        lines = [
            line.strip() for line in (result.stdout or "").splitlines() if line.strip()
        ]
    _write_cached_values(key, lines)
    return lines, True


def _teams_from_json(data: Any) -> list[str]:
    if not isinstance(data, list):
        return []
    lines: list[str] = []
    for team in data:
        if not isinstance(team, dict):
            continue
        slug = team.get("slug", "")
        name = team.get("name", "")
        if isinstance(slug, str) and slug:
            lines.append(f"{slug}\t{name}" if isinstance(name, str) and name else slug)
    return lines


def _license_choices(lines: list[str]) -> tuple[RepoCreationChoice, ...]:
    choices: list[RepoCreationChoice] = []
    for line in lines:
        fields = line.split("\t")
        value = fields[0].strip() if fields else ""
        if not value:
            continue
        label = fields[1].strip() if len(fields) > 1 and fields[1].strip() else value
        description = fields[2].strip() if len(fields) > 2 else ""
        choices.append(
            RepoCreationChoice(value=value, label=label, description=description)
        )
    return tuple(choices)


def _search_choices(lines: list[str]) -> tuple[RepoCreationChoice, ...]:
    choices: list[RepoCreationChoice] = []
    for line in lines:
        value = line.split("\t")[0].strip()
        if value:
            choices.append(RepoCreationChoice(value=value, label=value))
    return tuple(choices)


def _org_plan_supports_internal(owner: str, host: str) -> bool | None:
    """Best-effort check for enterprise-backed `internal` visibility.

    True when the org plan names enterprise, False when it names anything
    else, None when the plan could not be read.
    """
    cached = _read_cached_values(f"orgplan\t{host}\t{owner.casefold()}")
    if cached is not None:
        return cached == ["enterprise"]
    result = _run_gh(
        ["gh", "api", f"orgs/{owner}", "--jq", ".plan.name // empty"],
        host,
        CHOICE_LIST_TIMEOUT_SECONDS,
    )
    if result is None or result.returncode != 0:
        return None
    enterprise = "enterprise" in (result.stdout or "").casefold()
    _write_cached_values(
        f"orgplan\t{host}\t{owner.casefold()}",
        ["enterprise"] if enterprise else ["other"],
    )
    return enterprise


def _member_orgs(host: str) -> list[str] | None:
    """Best-effort list of org logins holding an active user membership."""
    cached = _read_cached_values(f"memberships\t{host}")
    if cached is not None:
        return cached
    result = _run_gh(
        ["gh", "api", "user/memberships/orgs", "--jq", ".[].organization.login"],
        host,
        CHOICE_LIST_TIMEOUT_SECONDS,
    )
    if result is None or result.returncode != 0:
        return None
    orgs = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
    _write_cached_values(f"memberships\t{host}", orgs)
    return orgs


def describe_gh_repo_creation(namespace: str) -> RepoCreationForm | None:
    """Describe GitHub's repository creation form for one owner.

    Read-only. Choice lists are cached for 24 h under the vcs cache dir.
    Returns None for empty namespaces.
    """
    from sase_github.config import get_default_github_host

    owner = namespace.strip().strip("/")
    if not owner or "/" in owner:
        return None
    host = get_default_github_host()
    account = read_gh_account(host)
    is_personal = bool(account) and owner.casefold() == account.casefold()

    auth_ok = True
    gitignore_lines, ok = _cached_choice_lines(
        "gitignore", ["gh", "repo", "gitignore", "list"], host
    )
    auth_ok = auth_ok and ok
    license_lines, ok = _cached_choice_lines(
        "license", ["gh", "repo", "license", "list"], host
    )
    auth_ok = auth_ok and ok

    if is_personal:
        team_choices: tuple[RepoCreationChoice, ...] = ()
        team_disabled = "not available for personal accounts"
    else:
        team_lines, ok = _cached_choice_lines(
            f"teams\t{host}\t{owner.casefold()}",
            ["gh", "api", f"orgs/{owner}/teams"],
            host,
            full_json="teams",
        )
        auth_ok = auth_ok and ok
        team_choices = _search_choices(team_lines)
        team_disabled = ""

    internal_disabled = ""
    if is_personal:
        internal_disabled = "enterprise organizations only"
    else:
        enterprise = _org_plan_supports_internal(owner, host)
        if enterprise is not True:
            internal_disabled = (
                "requires an enterprise organization"
                if enterprise is False
                else "could not verify the organization plan"
            )

    can_create: bool | None
    if is_personal:
        can_create = True
    else:
        orgs = _member_orgs(host)
        can_create = (
            True
            if orgs is not None and owner.casefold() in {org.casefold() for org in orgs}
            else None
        )

    message = ""
    if can_create is not True:
        message = f"repository creation rights for {owner} are unverified"
    remedy: tuple[str, ...] = () if auth_ok else _auth_remedy(host)

    return RepoCreationForm(
        workflow_type="gh",
        namespace=owner,
        fields=(
            RepoCreationField(
                name="description",
                label="Description",
                kind="line",
                group="basics",
                description="Shown on the repository page.",
            ),
            RepoCreationField(
                name="visibility",
                label="Visibility",
                kind="choice",
                group="basics",
                default="private",
                choices=(
                    RepoCreationChoice(value="public", label="Public"),
                    RepoCreationChoice(value="private", label="Private"),
                    RepoCreationChoice(
                        value="internal",
                        label="Internal",
                        disabled_reason=internal_disabled,
                    ),
                ),
                required=True,
                description="Public repositories are visible to everyone.",
            ),
            RepoCreationField(
                name="add_readme",
                label="Initialize with a README",
                kind="bool",
                group="basics",
                default="true",
            ),
            RepoCreationField(
                name="gitignore",
                label=".gitignore template",
                kind="search",
                group="basics",
                choices=_search_choices(gitignore_lines),
                description="Language or platform template.",
            ),
            RepoCreationField(
                name="license",
                label="License",
                kind="search",
                group="basics",
                choices=_license_choices(license_lines),
                description="Open-source license keyword.",
            ),
            RepoCreationField(
                name="template",
                label="Template repository",
                kind="line",
                group="advanced",
                placeholder="OWNER/REPO",
                description="Make the new repository from a template.",
                excludes=("add_readme", "gitignore", "license", "team"),
            ),
            RepoCreationField(
                name="include_all_branches",
                label="Include all template branches",
                kind="bool",
                group="advanced",
                default="false",
                requires=("template",),
            ),
            RepoCreationField(
                name="homepage",
                label="Homepage URL",
                kind="line",
                group="advanced",
            ),
            RepoCreationField(
                name="team",
                label="Team access",
                kind="search",
                group="advanced",
                choices=team_choices,
                description="Grant an organization team access.",
                disabled_reason=team_disabled,
            ),
            RepoCreationField(
                name="issues",
                label="Enable issues",
                kind="bool",
                group="advanced",
                default="true",
                description="Off passes --disable-issues.",
            ),
            RepoCreationField(
                name="wiki",
                label="Enable wiki",
                kind="bool",
                group="advanced",
                default="true",
                description="Off passes --disable-wiki.",
            ),
        ),
        can_create=can_create,
        message=message,
        remedy_argv=remedy,
    )


def _option_bool(options: dict[str, str], name: str, *, default: bool) -> bool:
    raw = options.get(name, "").strip().casefold()
    if not raw:
        return default
    if raw in _TRUE_STRINGS:
        return True
    if raw in _FALSE_STRINGS:
        return False
    return default


def create_gh_repo(ref: str, options: dict[str, str]) -> RepoCreateResult | None:
    """Create only the remote GitHub repository.

    Never clones, registers, or writes project records. Always passes an
    explicit visibility and never falls back to public. On an "already
    exists" error, re-probes and reports `exists`. Verifies after creating
    and reports requested settings the repo does not reflect.
    """
    parsed = parse_gh_repo_ref(ref)
    if parsed is None:
        return None
    owner, _, name = parsed.ref.partition("/")
    if not owner or not name:
        return None
    host = parsed.host

    visibility = (options.get("visibility") or "").strip().casefold()
    if visibility not in ("public", "private", "internal"):
        return RepoCreateResult(
            status="failed",
            parsed=parsed,
            message=(
                "a visibility option is required "
                "(public, private, or internal); refusing to fall back to public"
            ),
        )

    argv = ["gh", "repo", "create", f"{owner}/{name}", f"--{visibility}"]
    description = (options.get("description") or "").strip()
    if description:
        argv += ["-d", description]
    homepage = (options.get("homepage") or "").strip()
    if homepage:
        argv += ["-h", homepage]
    if _option_bool(options, "add_readme", default=True):
        argv.append("--add-readme")
    gitignore = (options.get("gitignore") or "").strip()
    if gitignore:
        argv += ["-g", gitignore]
    license_opt = (options.get("license") or "").strip()
    if license_opt:
        argv += ["-l", license_opt]
    template = (options.get("template") or "").strip()
    if template:
        argv += ["-p", template]
        if _option_bool(options, "include_all_branches", default=False):
            argv.append("--include-all-branches")
    team = (options.get("team") or "").strip()
    if team:
        argv += ["-t", team]
    if not _option_bool(options, "issues", default=True):
        argv.append("--disable-issues")
    if not _option_bool(options, "wiki", default=True):
        argv.append("--disable-wiki")

    result = _run_gh(argv, host, CREATE_TIMEOUT_SECONDS)
    if result is None:
        return RepoCreateResult(
            status="failed",
            parsed=parsed,
            message="could not run gh (missing, or the call timed out)",
        )
    if result.returncode != 0:
        output = "\n".join(
            part for part in (result.stderr, result.stdout) if part
        ).strip()
        normalized = output.casefold()
        message = output.splitlines()[0] if output else "gh repo create failed"
        if looks_like_already_exists_error(normalized):
            probe = probe_gh_repo(ref)
            if probe is not None and probe.status == "found":
                return RepoCreateResult(
                    status="exists",
                    parsed=parsed,
                    repo_id=probe.repo_id,
                    web_url=probe.web_url,
                    visibility=probe.visibility,
                    message=f"{owner}/{name} already exists",
                )
            return RepoCreateResult(status="failed", parsed=parsed, message=message)
        return RepoCreateResult(status="failed", parsed=parsed, message=message)

    verify = _run_gh(
        ["gh", "repo", "view", f"{owner}/{name}", "--json", _VERIFY_JSON_FIELDS],
        host,
        PROBE_TIMEOUT_SECONDS,
    )
    repo_id = ""
    web_url = parsed.web_url
    mismatches: list[str] = []
    message = f"created {owner}/{name} ({visibility})"
    if verify is None or verify.returncode != 0:
        message += "; verification query failed — confirm settings on the repo page"
    else:
        try:
            data = json.loads(verify.stdout or "null")
        except json.JSONDecodeError:
            data = None
        if not isinstance(data, dict):
            message += "; verification query failed — confirm settings on the repo page"
        else:
            repo_id = _string_or_empty(data, "id")
            web_url = _string_or_empty(data, "url") or web_url
            actual_visibility = _string_or_empty(data, "visibility").casefold()
            if actual_visibility and actual_visibility != visibility:
                mismatches.append(
                    f"visibility: requested {visibility}, got {actual_visibility}"
                )
    return RepoCreateResult(
        status="created",
        parsed=parsed,
        repo_id=repo_id,
        web_url=web_url,
        visibility=visibility,
        message=message,
        mismatches=tuple(mismatches),
    )
