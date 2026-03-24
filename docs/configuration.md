# Configuration

## `github_orgs`

The `github_orgs` setting controls how sase-github clones repositories. Add it to your sase config file
(`~/.config/sase/sase.yml`):

```yaml
github_orgs:
  - your-username
  - your-org
```

**Effect:** When cloning a repo whose owner is in this list, sase-github uses SSH (`git@github.com:user/project.git`).
For all other repos, it uses HTTPS (`https://github.com/user/project.git`).

This matters because SSH URLs require an SSH key configured with GitHub, while HTTPS URLs work for public repos without
authentication (but require a token for push access).

## Default Config

sase-github contributes a `default_config.yml` via the `sase_config` entry point. This is merged into the sase config
chain between sase core defaults and your user config.

Currently the default config defines:

- `xprompts.pr_diff` — an xprompt that expands to the diff of the current PR's changes

## Requirements

- **`gh` CLI** — Required for all PR operations. Install from https://cli.github.com/ and authenticate with
  `gh auth login`.
- **Git** — Standard git CLI for repository operations.

## Workspace Layout

GitHub workspaces are stored under `~/projects/github/<user>/<project>/`. When using numbered workspaces (for parallel
work), clones are created as siblings: `~/projects/github/<user>/<project>_<N>/`.

## Project Files

Project metadata is stored in `~/.sase/projects/<project>/<project>.gp`. The key field is `WORKSPACE_DIR`, which points
to the primary workspace directory. This is set automatically when you first use `#gh(user/project)`.
