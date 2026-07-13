# sase-github — GitHub VCS Plugin for sase

[![PyPI](https://img.shields.io/pypi/v/sase-github?logo=pypi&logoColor=white)](https://pypi.org/project/sase-github/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![mypy](https://img.shields.io/badge/type_checker-mypy-blue.svg)](https://mypy-lang.org/)
[![pytest](https://img.shields.io/badge/tests-pytest-blue.svg)](https://docs.pytest.org/)

## Overview

**sase-github** is a plugin for [sase](https://github.com/sase-org/sase) that adds GitHub-specific VCS and workspace
support. It provides the `GitHubPlugin` VCS provider and `GitHubWorkspacePlugin` workspace provider for repositories
hosted on `github.com` or configured GitHub Enterprise hosts, integrating with the `gh` CLI for pull request creation,
management, and submission, along with GitHub-specific xprompt workflows.

## Installation

For a managed SASE install, install `sase-github` into the same `uv tool` environment as `sase` so its entry points are
discovered by the `sase` command.

### Recommended: SASE Admin Center Updates tab

If SASE is already installed with `uv tool install sase`, open `sase ace`, press `#` for the SASE Admin Center, then go
to the **Updates** tab (`5`, or `[` / `]`). Highlight `sase-github` in the plugin list (`j` / `k`, or `/` to filter),
press `i` to install, and confirm the preview modal. The preview shows the exact `uv` command and resolved package set;
the install runs as a tracked background task and is discovered on the next `sase` run.

See the core SASE docs for the
[Updates tab](https://github.com/sase-org/sase/blob/master/docs/configuration.md#updates-tab) and
[`sase plugin` commands](https://github.com/sase-org/sase/blob/master/docs/plugins.md).

### Alternative: install SASE and the plugin together

```bash
uv tool install sase --with sase-github
```

Repeat `--with` for additional plugins, for example `--with sase-github --with sase-telegram`. Add `--force` to replace
an existing tool install.

### Equivalent CLI for an existing install

```bash
sase plugin install github
```

`pip install sase-github` is only an escape hatch for non-managed or library-style environments. It is not the normal
path for a `uv tool`-managed SASE command.

Requires `sase>=0.11.0` as a dependency. For GitHub Enterprise Server or self-hosted GitHub, follow the
[GitHub Enterprise setup walkthrough](docs/configuration.md#github-enterprise-setup).

## What's Included

### VCS Provider

- **GitHubPlugin** — GitHub VCS provider that extends `GitCommon` with `gh` CLI integration for PR workflows (push,
  create PR, retrieve PR URL/number)

### Workspace Provider

- **GitHubWorkspacePlugin** — Workspace provider that handles GitHub-specific workflow orchestration: reference
  resolution (repo paths, project names, changespec names), PR submission via `gh pr merge`, branch management, and
  commit description formatting. It also owns GitHub SDD policy: every GitHub project requires a labeled sidecar
  repository, materialized before `#gh` work starts.
- **Repo completion for `#gh:<owner>/` refs** — Supplies repository candidates to SASE prompt completion by calling
  `gh repo list <owner>`. Authenticated `gh` sessions include private repositories the user can access, and configured
  GitHub Enterprise hosts are respected through `GH_HOST`.
- **Owner completion for `#gh:` refs** — Supplies local namespace candidates from active canonical GitHub project
  records (`gh_<owner>__<repo>`) plus configured `github_orgs`, so accepting an owner can chain into repo completion
  without a network call.

### Configuration

- **`get_github_hosts()` / `get_default_github_host()`** — Read `github_hosts` from sase config to recognize GitHub
  Enterprise hosts and choose the default host for `#gh(owner/repo)` clone refs
- **`get_github_orgs()`** — Reads `github_orgs` from sase config to determine SSH vs HTTPS clone URLs for
  organizations/users with push access, and to seed local `#gh:` owner completion

### XPrompts

| XPrompt        | Description                                                                        |
| -------------- | ---------------------------------------------------------------------------------- |
| `#gh`          | GitHub workflow orchestration — resolves refs, claims workspaces, manages branches |
| `#new_pr_desc` | AI-generated PR descriptions from commit diffs                                     |
| `#prdd`        | Injects PR diff and description as context (auto-appended on feature branches)     |

## How It Works

sase-github uses Python [entry points](https://packaging.python.org/en/latest/specifications/entry-points/) to register
itself with sase core:

- **`sase_vcs`** — Registers `GitHubPlugin` as the `github` VCS provider
- **`sase_workspace`** — Registers `GitHubWorkspacePlugin` as the `github` workspace provider
- **`sase_xprompts`** — Makes GitHub xprompts discoverable via plugin discovery

When sase detects a repository whose remote origin host is in the configured GitHub host set, it automatically loads
`GitHubPlugin` and `GitHubWorkspacePlugin` to handle VCS operations and provider-owned SDD storage. The first `#gh` or
For managed projects, `sase sdd init` finds or creates the public `<owner>/<repo>--plans` and `<owner>/<repo>--research`
sidecars and returns their provider metadata to SASE core. The same hooks retain `<owner>/<repo>--sdd` discovery for
legacy stores. Authentication, permission, network, repository creation, labeling, clone, or initial-push failures stop
setup; there is no GitHub-local SDD fallback.

The explicit `sase sdd init` flow first uses a read-only `gh repo view` preflight for each sidecar. Existing
sidecars connect without a creation prompt. Each missing sidecar requires a fresh, default-no interactive `y`/`yes`
response naming the full `--plans` or `--research` repository. Non-interactive stdin, EOF, interruption, and bare
`sase init --yes` cannot authorize creation.

## Requirements

- Python 3.12+
- [sase](https://github.com/sase-org/sase) >= 0.11.0
- [gh](https://cli.github.com/) CLI (for GitHub API operations, mandatory sidecar repository setup, and `#gh:<owner>/`
  repository completion). Run `gh auth login`; the account must be able to create the sidecar when it is missing and
  manage its labels. For GitHub Enterprise, run `gh auth login --hostname <host>` for each configured Enterprise host.

See [Configuration](docs/configuration.md) for `github_hosts`, `github_orgs`, workspace layout, and the ordered GitHub
Enterprise setup flow.

## Development

```bash
just install    # Install in editable mode with dev deps
just fmt        # Auto-format code
just lint       # Run ruff + mypy
just test       # Run tests
just check      # All checks (lint + test)
just build      # Build distribution packages
just clean      # Remove build artifacts
```

Set `SASE_CORE_PATH=/path/to/sase` when you need development installs to use an editable SASE checkout before installing
`sase-github`.

## Project Structure

```
src/sase_github/
├── __init__.py              # Package exports
├── plugin.py                # GitHubPlugin VCS implementation
├── workspace_plugin.py      # GitHubWorkspacePlugin workspace implementation
├── config.py                # GitHub config helpers (host and org/user lists)
├── scripts/
│   ├── gh_setup.py                 # Setup step for #gh workflow
│   └── new_pr_desc_get_context.py  # Context retrieval for PR description generation
└── xprompts/
    ├── gh.yml              # GitHub workflow orchestration
    ├── new_pr_desc.yml     # PR description generation
    └── prdd.yml            # PR description detail injection
```

## License

MIT
