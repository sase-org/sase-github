# sase-github - Agent Instructions

## Overview
GitHub VCS plugin for sase. Provides GitHubPlugin (PR creation, gh CLI integration)
and GitHub-related xprompts (#gh, #new_pr_desc).

## Build & Run
```bash
just install    # Install in editable mode with dev deps
just lint       # ruff + mypy
just fmt        # Auto-format
just test       # pytest
just check      # lint + test
```

## Architecture
- `src/sase_github/plugin.py` — GitHubPlugin class (extends `sase.vcs_provider.plugins._git_common.GitCommon`)
- `src/sase_github/xprompts/` — GitHub workflow YAML files discovered via `sase_xprompts` entry point
- Depends on `sase>=0.1.0` for base classes, hookspec, and script modules

## Code Conventions
- Absolute imports: `from sase_github.plugin import GitHubPlugin`
- Target Python 3.12+
- Follow ruff rules matching sase core
