# Architecture

sase-github is structured as two pluggy-based plugins that integrate with sase core via Python entry points.

## Plugin System

### Entry Points

Registered in `pyproject.toml`:

| Entry Point | Plugin | Purpose |
|---|---|---|
| `sase_vcs:github` | `GitHubPlugin` | VCS operations (push, PR creation, PR info) |
| `sase_workspace:github` | `GitHubWorkspacePlugin` | Workspace orchestration (ref resolution, submission, mail prep) |
| `sase_xprompts:sase_github` | — | Makes `#gh`, `#new_pr_desc`, `#prdd` xprompts discoverable |
| `sase_config:sase_github` | — | Contributes `default_config.yml` to the sase config chain |

### GitHubPlugin (`plugin.py`)

Extends `GitCommon` from sase core. Handles low-level VCS operations by wrapping `git` and `gh` CLI commands.

**Hook implementations:**

| Hook | Behavior |
|---|---|
| `vcs_classify_repo()` | Claims repos with `github.com` in their origin URL |
| `vcs_get_change_url()` | Returns PR URL via `gh pr view --json url` |
| `vcs_get_cl_number()` | Returns PR number via `gh pr view --json number` |
| `vcs_mail()` | Pushes branch (`git push -u origin`) and creates PR if needed (`gh pr create --fill`) |
| `vcs_create_pull_request()` | Creates a PR with an AI-generated title and body |

### GitHubWorkspacePlugin (`workspace_plugin.py`)

Handles higher-level workflow orchestration. Implements workspace hooks for GitHub-hosted projects.

**Hook implementations:**

| Hook | Behavior |
|---|---|
| `ws_get_workflow_metadata()` | Returns metadata for the `gh` workflow type (ref pattern `#gh`, vcs family `git`) |
| `ws_detect_workflow_type()` | Returns `"gh"` for repos with a remote origin URL (non-local) |
| `ws_get_change_label()` | Returns `"PR"` for GitHub projects |
| `ws_resolve_ref()` | Resolves `#gh` references (see [Reference Resolution](#reference-resolution)) |
| `ws_extract_change_identifier()` | Extracts PR number from GitHub PR URLs |
| `ws_generate_submitted_check_script()` | Generates a bash script that checks if a PR is merged via `gh pr view` |
| `ws_supports_reviewer_comments()` | Returns `False` for GitHub URLs (critique comments not supported) |
| `ws_get_workspace_directory()` | Ensures git clone exists via `ensure_git_clone()` |
| `ws_prepare_mail()` | Displays branch/description and prompts user before pushing |
| `ws_format_commit_description()` | Prepends `[project]` prefix to commit messages |
| `ws_submit()` | Submits a ChangeSpec by merging its PR via `gh pr merge --merge --delete-branch` |

## Reference Resolution

The `resolve_gh_ref()` function supports three dispatch modes for `#gh` references:

### Mode 1: Repo Path (`user/project`)

When the ref contains `/`, it's treated as a GitHub repo path:
- Derives workspace from `~/projects/github/<user>/<project>/`
- Clones the repo if it doesn't exist (SSH for orgs in `github_orgs`, HTTPS otherwise)
- Sets `WORKSPACE_DIR` in the project file
- Checks out the default branch

### Mode 2: Project Shorthand (`myproject`)

When the ref matches an existing project directory:
- Looks up `~/.sase/projects/<name>/<name>.gp`
- Reads `WORKSPACE_DIR` from the project file
- Checks out the default branch

### Mode 3: ChangeSpec Name

When the ref matches an existing ChangeSpec:
- Searches all changespecs for a matching name
- Reads `WORKSPACE_DIR` from the changespec's project file
- Checks out `origin/<name>` (the ChangeSpec's branch)

## Submission Flow

When submitting a GitHub ChangeSpec (`ws_submit`):

1. Kill and persist all running processes on the ChangeSpec
2. Verify no active child ChangeSpecs exist
3. Claim a workspace and checkout the ChangeSpec branch
4. Check for an existing PR on the branch
5. Merge via `gh pr merge --merge --delete-branch`
6. Finalize submission (update ChangeSpec status)
7. Release the workspace

## Config Helper

`config.py` provides `get_github_orgs()` which reads the `github_orgs` list from the merged sase config. This determines whether repos are cloned via SSH (for orgs the user has push access to) or HTTPS.
