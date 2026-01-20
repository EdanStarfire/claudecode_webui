---
name: github-pr-manager
description: Create, view, and merge GitHub pull requests with validation. Use when creating PRs from branches, checking PR status, or merging approved PRs with cleanup.
---

# GitHub PR Manager

## Instructions

### When to Invoke This Skill
- Creating a pull request after implementing changes
- Checking status of an existing PR (mergeable, checks, reviews)
- Merging an approved PR
- Validating PR state before operations

### Capabilities

1. **Create Pull Request**
   - Generate PR from current branch
   - Create descriptive title and body
   - Link to related issues
   - Add appropriate labels

2. **View PR Status**
   - Check if PR is open/closed/merged
   - Verify mergeable state
   - Review CI check status
   - Check review approvals

3. **Merge Pull Request**
   - Squash merge with single commit
   - Auto-delete remote branch
   - Validate before merging

### Standard Workflows

#### Creating a PR

1. **Verify Current Branch**
   ```bash
   git branch --show-current
   ```
   Ensure you're on a feature branch, not main/master

2. **Push Branch** (if not already pushed)
   ```bash
   git push -u origin HEAD
   ```

3. **Create PR**
   ```bash
   gh pr create --title "<type>: <description>" --body "$(cat <<'EOF'
   ## Summary
   Resolves #<issue_number>

   <Brief description of changes>

   ## Changes Made
   - <List key changes>

   ## Testing
   - <How to test these changes>

   🤖 Generated with [Claude Code](https://claude.com/claude-code)
   EOF
   )"
   ```

#### Checking PR Status

1. **Fetch PR Information**
   ```bash
   gh pr view <pr_number> --json state,mergeable,statusCheckRollup,reviewDecision
   ```

2. **Interpret Results**
   - `state`: "OPEN", "CLOSED", "MERGED"
   - `mergeable`: "MERGEABLE", "CONFLICTING", "UNKNOWN"
   - `reviewDecision`: "APPROVED", "CHANGES_REQUESTED", "REVIEW_REQUIRED"

#### Merging a PR

1. **Validate PR State**
   ```bash
   gh pr view <pr_number> --json headRefName,state,mergeable
   ```
   - Verify state is "OPEN"
   - Verify mergeable is "MERGEABLE"
   - Extract branch name for cleanup

2. **Perform Squash Merge**
   ```bash
   gh pr merge <pr_number> --squash --delete-branch
   ```
   This will:
   - Squash all commits into one
   - Merge to main/master
   - Delete remote branch
   - Delete local tracking branch (if it exists)

3. **Update Local Repository**

   **Detect environment:**
   ```bash
   # Check if in worktree (.git is file) or main repo (.git is directory)
   if [ -f .git ]; then
     echo "📍 Worktree detected - skipping checkout to main"
     echo "   (Main branch is checked out in root repository)"
     SKIP_CHECKOUT=true
   else
     echo "📍 Main repository detected"
     SKIP_CHECKOUT=false
   fi
   ```

   **Switch to main (if not in worktree):**
   ```bash
   if [ "$SKIP_CHECKOUT" = "false" ]; then
     git checkout main
   fi
   ```

   **Pull latest changes:**
   ```bash
   git pull origin main
   ```

   **Note**: In worktree environments:
   - Main is checked out in the root repository
   - Cannot checkout a branch that's active elsewhere
   - Worktree cleanup is handled by orchestrator or worktree-manager skill

4. **Clean Up Local Branch** (if still exists)
   ```bash
   # Check if branch still exists
   git branch --list <branch-name>

   # Delete only if exists
   if git branch --list <branch-name> | grep -q <branch-name>; then
     git branch -d <branch-name>
   fi
   ```
   **Note**: The `--delete-branch` flag usually handles this, but verify to ensure cleanup

### Error Handling

**Authentication Issues:**
- Run: `gh auth status`
- If not authenticated: `gh auth login`

**PR Not Mergeable:**
- Check for conflicts: Inform user to resolve merge conflicts
- Check for failing CI: Wait for checks to pass
- Check for required reviews: Request reviews from team

**Branch Deletion Fails:**
- Branch may be protected
- User may have the branch checked out
- Branch may not exist locally (already deleted or never checked out)
- Always check branch existence before deletion
- Ignore "branch not found" errors - treat as already cleaned up

**Worktree Environment:**
- In git worktrees, `.git` is a file (not directory) pointing to actual git dir
- Cannot checkout main because it's active in root repository
- Skill automatically detects worktrees and skips checkout step
- Branch deletion still works normally from worktrees
- Pull operation fetches latest changes without switching branches
- Worktree cleanup should be handled separately (use worktree-manager skill)

### PR Title and Body Standards

**Title Format:**
```
<type>: <brief description>
```

Types: `feat`, `fix`, `chore`, `docs`, `refactor`, `test`, `perf`

**Body Format:**
```markdown
## Summary
Resolves #<issue>

<1-2 sentence description>

## Changes Made
- <Bullet list of key changes>

## Testing
- <How changes were tested>
- <Manual test steps if needed>

## Screenshots (if UI changes)
<Optional screenshots>

🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

## Examples

### Example 1: Create PR from feature branch
```
User: "Create a PR for this feature"
Action:
1. Verify on feature branch
2. Push if needed
3. Create PR with structured body linking to issue
Output: PR URL and number
```

### Example 2: Check PR status before merging
```
User: "Is PR #123 ready to merge?"
Action:
1. Fetch PR status
2. Check mergeable state, CI status, reviews
Output: "PR #123 is ready to merge" or list blockers
```

### Example 3: Merge approved PR
```
User: "Merge PR #123"
Action:
1. Validate PR state and extract branch name
2. Squash merge with branch deletion
3. Switch to main and pull
4. Verify local branch cleanup (check existence first)
Output: "PR #123 merged successfully, branch cleaned up"
```
### Example 4: Merge PR from worktree
```
User: "Merge PR #789"
Context: Working in worktree at worktrees/issue-275/
Action:
1. Validate PR state (gh pr view 789 --json headRefName,state,mergeable)
2. Extract branch name from PR
3. Squash merge with branch deletion (gh pr merge 789 --squash --delete-branch)
4. Detect worktree environment (test -f .git returns true)
5. Skip checkout to main (with informative message)
6. Pull latest changes (git pull origin main)
7. Verify local branch cleanup

Output:
"📍 Worktree detected - skipping checkout to main
   (Main branch is checked out in root repository)
PR #789 merged successfully
Remote branch deleted
Pulled latest changes from main
Ready for worktree cleanup"

Note: The worktree itself should be cleaned up using worktree-manager skill after merge
```
