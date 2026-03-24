# XPrompts

sase-github provides three xprompts for GitHub-specific workflows.

## `#gh` — GitHub Workflow

**Type:** Workflow (`gh.yml`)
**Tags:** `vcs`, `rollover`

Orchestrates the full lifecycle of working in a GitHub repository: resolves references, claims a workspace, checks out
the target branch, and captures diffs.

### Input

| Parameter | Type | Default | Description |
|---|---|---|---|
| `gh_ref` | word | (required) | Repo path (`user/project`), project name, or ChangeSpec name |
| `n` | int | `null` | Workspace number override (auto-assigned if null) |
| `release` | bool | `true` | Whether to release the workspace when done |
| `workflow_label` | word | `null` | Optional label for the workflow |

### Steps

1. **setup** — Resolves the `gh_ref` to a project file, workspace directory, and checkout target. Claims the workspace.
2. **prepare** — Stashes uncommitted changes (including untracked files) and fetches from origin.
3. **checkout** — Checks out the target branch/commit. Falls back to `master`/`main` for project refs. Pulls with
   rebase to sync with remote.
4. **inject** — Empty `prompt_part` placeholder. The user's prompt is injected here, so any text after `#gh(ref)` in
   the agent prompt becomes the task.
5. **release** (finally) — Releases the workspace if `should_release` is true. Runs even if earlier steps fail.
6. **diff** (finally) — Captures the diff of changes made during the session. If commits were made, shows the last
   commit's diff; otherwise shows uncommitted changes.

### Usage Examples

```
# Work on a GitHub repo by org/project path
#gh(sase-org/sase) Can you fix the failing tests?

# Work on a project by shorthand name
#gh(sase) Add a new CLI command for status checking

# Resume work on an existing ChangeSpec
#gh(fix-auth-bug) Continue implementing the OAuth flow

# Work without releasing the workspace
#gh(sase-org/sase, release=False) Set up the initial structure
```

## `#new_pr_desc` — PR Description Generator

**Type:** Workflow (`new_pr_desc.yml`)

Generates an AI-written PR title and body from the diff and commits on a branch, then applies it to an existing PR.

### Input

| Parameter | Type | Description |
|---|---|---|
| `name` | word | ChangeSpec name to generate the description for |

### Steps

1. **get_context** — Runs a Python script that retrieves the diff, commit history, workspace directory, default branch,
   and current branch name for the ChangeSpec.
2. **generate** — Launches a sub-agent with the diff and commits, instructing it to produce a title (conventional
   commits format, under 72 chars) and a body (markdown with `## Summary` and bullet points).
3. **apply** — Looks up the PR for the branch via `gh pr view` and updates it with `gh pr edit` using the generated
   title and body.

### Usage Example

```
#new_pr_desc(fix-auth-bug)
```

## `#prdd` — PR Description Detail

**Type:** Workflow (`prdd.yml`)
**Tags:** `append_to_commit_and_propose`

Injects the current PR's diff and description as context into the agent's prompt. Only activates when on a feature
branch (not `master` or `main`).

### Steps

1. **check_branch** — Checks if the current branch is `master` or `main`.
2. **content** (conditional) — If on a feature branch, expands into a `prompt_part` that includes:
   - `#pr_diff` — the diff of changes made by the current PR
   - The current PR description (fetched via `gh pr view --json body`)

This xprompt is typically used as a tag rather than invoked directly — it's automatically appended when the
`append_to_commit_and_propose` tag is active.
