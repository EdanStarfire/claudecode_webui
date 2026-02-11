---
name: git-state-validator
description: Check git repository status, detect conflicts, and validate branch state before operations. Use when verifying working directory cleanliness, checking for conflicts, or validating branch state.
allowed-tools:
  - Bash(scripts/validate.sh:*)
---

# Git State Validator

## Instructions

### Invocation

Run the validation script:

```bash
bash scripts/validate.sh
```

Optional flags:

```bash
bash scripts/validate.sh --fetch     # Fetch remote before checking sync status
bash scripts/validate.sh --health    # Include repository integrity check (slow)
```

### Reading the Output

The script emits structured `KEY=VALUE` pairs on stdout:

| Field | Values | Meaning |
|---|---|---|
| `GIT_STATE_IS_REPO` | `true`/`false` | Whether current directory is a git repo |
| `GIT_STATE_BRANCH` | branch name or short SHA | Current branch (SHA if detached) |
| `GIT_STATE_DETACHED` | `true`/`false` | Whether HEAD is detached |
| `GIT_STATE_CLEAN` | `true`/`false` | No staged or unstaged changes, no conflicts |
| `GIT_STATE_STAGED` | count | Files staged for commit |
| `GIT_STATE_UNSTAGED` | count | Files modified but not staged |
| `GIT_STATE_UNTRACKED` | count | New files not tracked by git |
| `GIT_STATE_CONFLICTS` | count | Files with merge conflicts |
| `GIT_STATE_CONFLICTED_FILES` | file list | Names of conflicted files (only if conflicts > 0) |
| `GIT_STATE_AHEAD` | count | Commits ahead of upstream |
| `GIT_STATE_BEHIND` | count | Commits behind upstream |
| `GIT_STATE_FILES` | porcelain output | Full `git status --porcelain` (only if non-empty) |
| `GIT_STATE_FSCK` | `ok`/`errors` | Repository integrity (only with `--health`) |

### Interpreting Exit Codes

#### Exit 0 — Clean

Working directory is clean, no conflicts. Safe to proceed with any git operation (branch, merge, commit, pull).

#### Exit 1 — Dirty working directory

Uncommitted changes exist but no conflicts. Check `GIT_STATE_FILES` for details.

Depending on the caller's context:
- **Before branch switch:** suggest stashing or committing first
- **Before commit:** this is expected — the caller wants to commit these changes
- **Before merge/pull:** suggest committing or stashing first

#### Exit 2 — Merge conflicts

Active merge conflicts. Check `GIT_STATE_CONFLICTS` for count and `GIT_STATE_CONFLICTED_FILES` for the list. **Do not proceed** with branch operations, commits, or merges. Alert the user that conflicts must be resolved first.

#### Exit 3 — Not a git repository

Current directory is not inside a git working tree. Alert the user.
