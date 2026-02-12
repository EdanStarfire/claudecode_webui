---
name: custom-plan-manager
description: GitHub-based plan storage for the Planner/Builder workflow (marketplace plugin)
disable-model-invocation: true
argument-hint: <operation> <issue_number> [stage]
allowed-tools:
  - Bash(gh issue:*)
  - Bash(ls:*)
  - Skill(github-issue-reader)
---

## Custom Plan Manager (GitHub)

GitHub-based plan storage using issue comments and labels. This is a marketplace plugin that overrides the default file-based `plan-manager` when placed in a project's `.claude/skills/` directory.

When this skill is present, all workflow skills (`plan_issue`, `approve_plan`, `approve_issue`) and templates will use GitHub for plan storage instead of local files.

### Operations

The first argument (`$1`) is the operation. The second argument (`$2`) is the issue number. The optional third argument (`$3`) is the stage name.

**Supported operations:**
- `fetch-issue` - Retrieve issue details from GitHub
- `write-plan` - Post plan as GitHub comment and add `ready-to-build` label
- `read-plan` - Read latest plan comment from GitHub issue
- `verify-plan` - Check for `ready-to-build` label on issue
- `delete-plan` - Remove `ready-to-build` label from issue

---

### Operation: fetch-issue

**Usage:** `custom-plan-manager fetch-issue <issue_number>`

Delegates to `github-issue-reader` skill to fetch issue details from GitHub.

**Steps:**
1. Invoke the `github-issue-reader` skill with issue number $2
2. Return the issue details to the caller

---

### Operation: write-plan

**Usage:** `custom-plan-manager write-plan <issue_number> [stage]`

Posts the plan as a GitHub issue comment and marks the issue as ready to build.

**Steps:**

1. **Post plan as comment:**
   ```bash
   gh issue comment $2 --body "<plan_content>"
   ```
   The plan content should be the finalized implementation plan approved by the user.
   If a stage is specified, prefix the comment with `## Stage: $3` for clarity.

2. **Add ready-to-build label:**
   ```bash
   gh issue edit $2 --add-label "ready-to-build"
   ```

3. **Report result:**
   Output confirmation that the plan was posted to the issue.

---

### Operation: read-plan

**Usage:** `custom-plan-manager read-plan <issue_number> [stage]`

Reads the latest plan comment from the GitHub issue.

**Steps:**

1. **Fetch latest comment:**
   ```bash
   gh issue view $2 --comments
   ```

2. **Extract plan:**
   Find the most recent comment containing `## Implementation Plan` (or `## Stage: $3` if stage provided).

3. **Handle missing plan:**
   If no plan comment found, report:
   ```
   Error: No plan comment found on issue #$2. Has the Planner posted the plan yet?
   ```

4. **Return content:**
   Return the full plan content to the caller.

---

### Operation: verify-plan

**Usage:** `custom-plan-manager verify-plan <issue_number> [stage]`

Checks for the `ready-to-build` label on the issue.

**Steps:**

1. **Check label:**
   ```bash
   gh issue view $2 --json labels --jq '.labels[].name' | grep -q "ready-to-build"
   ```

2. **Report result:**
   - If label present: `Plan verified: issue #$2 has ready-to-build label`
   - If label missing: `Warning: issue #$2 does not have ready-to-build label. Planner may not have marked the plan as approved.`

---

### Operation: delete-plan

**Usage:** `custom-plan-manager delete-plan <issue_number> [stage]`

Removes the `ready-to-build` label during cleanup. Does not delete the comment (preserves history).

**Steps:**

1. **Remove label:**
   ```bash
   gh issue edit $2 --remove-label "ready-to-build"
   ```

2. **Report result:**
   ```
   Removed ready-to-build label from issue #$2
   ```

---

### Important Notes

- This is a **marketplace plugin** — place in `.claude/skills/custom-plan-manager/` to activate
- When present, it overrides the default file-based `plan-manager`
- Preserves the original GitHub-based workflow for teams that prefer it
- Requires `gh` CLI to be authenticated (`gh auth status`)
- Stage parameter is informational for GitHub (used in comment headers, not file paths)
- Comment history is preserved even after `delete-plan` (only label is removed)
- To switch back to file-based storage, simply remove this skill directory
