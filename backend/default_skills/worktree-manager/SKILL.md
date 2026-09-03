---
name: worktree-manager
description: Manage git worktrees for isolated issue work. Create, list, and remove worktrees safely.
allowed-tools:
  - Bash(~/.claude/skills/worktree-manager/scripts/create.sh :*)
  - Bash(~/.claude/skills/worktree-manager/scripts/list.sh)
  - Bash(~/.claude/skills/worktree-manager/scripts/remove.sh :*)
  - Bash(git status:*)
---

# Worktree Manager

## Instructions

### Create a worktree

```bash
~/.claude/skills/worktree-manager/scripts/create.sh <issue_number>
~/.claude/skills/worktree-manager/scripts/create.sh <issue_number> --prefix fix       # Branch prefix (default: feat)
~/.claude/skills/worktree-manager/scripts/create.sh <issue_number> --branch main      # Override default branch detection
```

The script creates `worktrees/issue-<N>/` with branch `<prefix>/issue-<N>` based on the latest `origin/<default-branch>`.

| Field | Meaning |
|---|---|
| `CREATE_STATUS` | `success` or `error` |
| `WORKTREE_PATH` | Path to the created worktree |
| `BRANCH_NAME` | Branch created for the worktree |
| `DEFAULT_BRANCH` | Base branch used |
| `BASE_COMMIT` | Commit the worktree is based on |
| `ERROR_CODE` | `already_exists`, `no_default_branch`, `fetch_failed`, `create_failed` |

**Choosing the prefix:** Analyze the issue to determine the work type:
- `feat` — new features (default)
- `fix` — bug fixes
- `chore` — maintenance, tooling
- `docs` — documentation
- `refactor` — code refactoring
- `test` — test additions/fixes

#### Error handling

**Exit 1 — already exists:** Ask user if they want to remove and recreate, or reuse the existing worktree.

**Exit 2 — no default branch:** Re-run with `--branch main` or `--branch master`.

**Exit 3 — fetch failed:** Network issue. Suggest retry.

**Exit 4 — create failed:** Usually means the branch name already exists. Suggest removing the stale branch first (`git branch -D <branch>`) or using a different prefix.

### List worktrees

```bash
~/.claude/skills/worktree-manager/scripts/list.sh
```

Outputs one block per worktree (separated by `---`), plus a summary count at the end.

| Field | Meaning |
|---|---|
| `WORKTREE_PATH` | Absolute path |
| `WORKTREE_BRANCH` | Branch name |
| `WORKTREE_CLEAN` | `true` / `false` / `unknown` |
| `WORKTREE_MODIFIED` | Number of changed files |
| `WORKTREE_FILES` | `git status --short` output (only if dirty) |
| `WORKTREE_LAST_COMMIT` | Latest commit oneline |
| `WORKTREE_AHEAD` | Commits ahead of upstream |
| `WORKTREE_COUNT` | Total issue worktrees (in final block) |

Format this into a readable summary for the user.

### Remove a worktree

```bash
~/.claude/skills/worktree-manager/scripts/remove.sh <issue_number>
~/.claude/skills/worktree-manager/scripts/remove.sh <issue_number> --force    # Discard uncommitted changes
```

| Field | Meaning |
|---|---|
| `REMOVE_STATUS` | `success` or `error` |
| `BRANCH_NAME` | Branch that was in the worktree |
| `ERROR_CODE` | `not_found`, `uncommitted_changes`, `remove_failed` |
| `DETAILS` | Changed files (if uncommitted) or error message |

#### Error handling

**Exit 1 — not found:** Worktree doesn't exist. Inform user, no action needed.

**Exit 3 — uncommitted changes:** Show the user the `DETAILS` (modified files) and ask:
1. **Force remove** — re-run with `--force` to discard changes
2. **Commit first** — cd into the worktree, commit, then retry removal
3. **Abort** — leave worktree in place

**Exit 4 — removal failed:** Worktree may be locked (process using it). Suggest checking for running processes, or re-run with `--force`.
