# sase-github — GitHub VCS Plugin for sase

[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![mypy](https://img.shields.io/badge/type_checker-mypy-blue.svg)](https://mypy-lang.org/)
[![pytest](https://img.shields.io/badge/tests-pytest-blue.svg)](https://docs.pytest.org/)

## Overview

**sase-github** is a plugin for [sase](https://github.com/sase-org/sase) that adds GitHub-specific VCS and workspace
support. It provides the `GitHubPlugin` VCS provider and `GitHubWorkspacePlugin` workspace provider for GitHub-hosted
repositories, integrating with the `gh` CLI for pull request creation, management, and submission, along with
GitHub-specific xprompt workflows.

## Installation

```bash
pip install sase-github
```

Or with [uv](https://docs.astral.sh/uv/):

```bash
uv pip install sase-github
```

Requires `sase>=0.1.0` as a dependency (installed automatically).

## What's Included

### VCS Provider

- **GitHubPlugin** — GitHub VCS provider that extends `GitCommon` with `gh` CLI integration for PR workflows (push,
  create PR, retrieve PR URL/number)

### Workspace Provider

- **GitHubWorkspacePlugin** — Workspace provider that handles GitHub-specific workflow orchestration: reference
  resolution (repo paths, project names, changespec names), PR submission via `gh pr merge`, branch management, and
  commit description formatting

### Configuration

- **`get_github_orgs()`** — Reads `github_orgs` from sase config to determine SSH vs HTTPS clone URLs for
  organizations/users with push access

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

When sase detects a GitHub-hosted repository (via `gh` CLI), it automatically loads `GitHubPlugin` and
`GitHubWorkspacePlugin` to handle VCS operations like PR creation, branch management, commit workflows, and PR
submission.

## Requirements

- Python 3.12+
- [sase](https://github.com/sase-org/sase) >= 0.1.0
- [gh](https://cli.github.com/) CLI (for GitHub API operations)

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

## Project Structure

```
src/sase_github/
├── __init__.py              # Package exports
├── plugin.py                # GitHubPlugin VCS implementation
├── workspace_plugin.py      # GitHubWorkspacePlugin workspace implementation
├── config.py                # GitHub config helpers (org/user list)
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
