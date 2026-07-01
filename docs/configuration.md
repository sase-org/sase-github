# Configuration

## `github_hosts`

The `github_hosts` setting controls which GitHub hosts sase-github recognizes. Add it to your sase config file
(`~/.config/sase/sase.yml`) when you use GitHub Enterprise Server or another self-hosted GitHub instance:

```yaml
github_hosts:
  - github.mycompany.com
  - github.com
```

**Effect:** sase-github claims repositories whose remote origin host matches one of these hosts. `github.com` is always
included implicitly, so public GitHub keeps working even if you only configure an Enterprise host.

The first configured host is the default for bare `#gh(owner/repo)` refs. With the example above,
`#gh(my-org/my-repo)` clones from `github.mycompany.com`. If `github_hosts` is unset, the default host is `github.com`.

Host entries are normalized, so pasted values such as `https://github.mycompany.com/` are accepted.

## `github_orgs`

The `github_orgs` setting controls how sase-github clones repositories. Add it to your sase config file
(`~/.config/sase/sase.yml`):

```yaml
github_orgs:
  - your-username
  - your-org
```

**Effect:** When cloning a repo whose owner is in this list, sase-github uses SSH
(`git@<github-host>:user/project.git`). For all other repos, it uses HTTPS
(`https://<github-host>/user/project.git`).

This matters because SSH URLs require an SSH key configured with GitHub, while HTTPS URLs work for public repos without
authentication (but require a token for push access).

## Default Config

sase-github contributes a `default_config.yml` via the `sase_config` entry point. This is merged into the sase config
chain between sase core defaults and your user config.

Currently the default config defines:

- `xprompts.pr_diff` — an xprompt that expands to the diff of the current PR's changes

## Requirements

- **`gh` CLI** — Required for all PR operations. Install from https://cli.github.com/ and authenticate with
  `gh auth login`. For GitHub Enterprise, authenticate to the configured host with
  `gh auth login --hostname github.mycompany.com`.
- **Git** — Standard git CLI for repository operations.

## Workspace Layout

Primary GitHub workspaces are stored under `~/projects/github/<user>/<project>/` when first resolved from a
`#gh(user/project)` reference and the default host is `github.com`. For other default hosts, workspaces are namespaced
by host at `~/projects/github/<host>/<user>/<project>/` to avoid collisions between same-named repos on different
GitHub installations.

Numbered parallel-work checkouts follow SASE's shared `workspace.root` policy: by default they live under the platform
state-root namespace, while explicit `workspace.root: adjacent` keeps the legacy
`~/projects/github/<user>/<project>_<N>/` sibling layout for `github.com` projects.

## Project Files

Project metadata is stored in `~/.sase/projects/<project>/<project>.sase`; legacy `.gp` files remain readable as a
fallback. The key field is `WORKSPACE_DIR`, which points to the primary workspace directory and is set automatically
when you first use an `#gh:<user>/<project>` ref.

For new `owner/repo` refs, the project name is based on the full GitHub identity, normally `gh_<user>__<project>`, so
two owners can have repositories with the same basename. If that canonical name is already occupied by a different
project, `PROJECT_NAME`, or alias, sase-github adds a deterministic suffix such as `-2`.

sase-github also writes `PROJECT_NAME` to the repo basename when it is valid and useful. The first `owner/foo` repo can
get `PROJECT_NAME: foo`; a second `owner/foo` repo gets the next available display name such as `foo_1`. Existing
basename ProjectSpecs are reused when their `WORKSPACE_DIR` already matches the GitHub workspace, so no automatic
migration or rename is required. Existing auto-aliased GitHub projects are also left unchanged and keep resolving via
their `PROJECT_ALIASES` entry.
