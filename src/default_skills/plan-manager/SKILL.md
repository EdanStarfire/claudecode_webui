---
name: plan-manager
description: File-based plan storage for the Planner/Builder workflow
disable-model-invocation: true
argument-hint: <operation> <issue_number> [stage]
allowed-tools:
  - Bash(mkdir:*)
  - Bash(rm:*)
  - Bash(cat:*)
  - Bash(ls:*)
  - Read
  - Write
  - Skill(github-issue-reader)
  - mcp__resources__register_resource
---

## Plan Manager

File-based plan storage at `~/.cc_webui/plans/`. This is the default plan manager used by the Planner/Builder workflow. Projects can override this by providing a `custom-plan-manager` skill in `.claude/skills/`.

### Operations

The first argument (`$1`) is the operation. The second argument (`$2`) is the issue number. The optional third argument (`$3`) is the stage name.

**Supported operations:**
- `fetch-issue` - Retrieve issue details from the issue tracker
- `write-plan` - Write plan content to file and register as resource
- `read-plan` - Read plan content from file
- `verify-plan` - Check that plan file exists and return its path
- `delete-plan` - Delete plan file

### Path Convention

Plan files are stored at:
- Without stage: `$HOME/.cc_webui/plans/issue-{N}.md`
- With stage: `$HOME/.cc_webui/plans/issue-{N}-{stage}.md`

Stage names are sanitized to `[a-zA-Z0-9-]` only.

---

### Operation: fetch-issue

**Usage:** `plan-manager fetch-issue <issue_number>`

Delegates to `github-issue-reader` skill to fetch issue details. This thin wrapper exists so that custom plan managers can replace the issue fetching mechanism (e.g., for Jira, Azure DevOps).

**Steps:**
1. Invoke the `github-issue-reader` skill with issue number $2
2. Return the issue details to the caller

---

### Operation: write-plan

**Usage:** `plan-manager write-plan <issue_number> [stage]`

Writes the plan content to a file and registers it as a resource for the Resource Gallery.

**Steps:**

1. **Compute file path:**
   - If `$3` (stage) is provided: `$HOME/.cc_webui/plans/issue-$2-$3.md`
   - If no stage: `$HOME/.cc_webui/plans/issue-$2.md`

2. **Ensure directory exists:**
   ```bash
   mkdir -p "$HOME/.cc_webui/plans"
   ```

3. **Write plan content:**
   Use the Write tool to write the plan content to the computed file path.
   The plan content should be the finalized implementation plan that was approved by the user.

4. **Register as resource:**
   Invoke `mcp__resources__register_resource` with:
   - **file_path**: The absolute path to the plan file
   - **title**: `Plan: Issue #$2` (or `Plan: Issue #$2 - $3` if stage provided)
   - **description**: `Implementation plan for issue #$2`

5. **Report path:**
   Output the file path so the caller knows where the plan was stored.

---

### Operation: read-plan

**Usage:** `plan-manager read-plan <issue_number> [stage]`

Reads and returns the plan content from file.

**Steps:**

1. **Compute file path:**
   - If `$3` (stage) is provided: `$HOME/.cc_webui/plans/issue-$2-$3.md`
   - If no stage: `$HOME/.cc_webui/plans/issue-$2.md`

2. **Read plan file:**
   Use the Read tool to read the file at the computed path.

3. **Handle missing file:**
   If the file does not exist, report an error:
   ```
   Error: Plan file not found at [path]. Has the Planner written the plan yet?
   ```

4. **Return content:**
   Return the full plan content to the caller.

---

### Operation: verify-plan

**Usage:** `plan-manager verify-plan <issue_number> [stage]`

Checks that the plan file exists and returns its absolute path.

**Steps:**

1. **Compute file path:**
   - If `$3` (stage) is provided: `$HOME/.cc_webui/plans/issue-$2-$3.md`
   - If no stage: `$HOME/.cc_webui/plans/issue-$2.md`

2. **Check existence:**
   ```bash
   ls "$HOME/.cc_webui/plans/issue-$2.md" 2>/dev/null || ls "$HOME/.cc_webui/plans/issue-$2-$3.md" 2>/dev/null
   ```

3. **Report result:**
   - If file exists: `Plan verified at [path]`
   - If file missing: `Warning: Plan file not found at [path]. Planner may not have written the plan yet.`

4. **Return path:**
   Always return the expected path regardless of existence, so callers can use it.

---

### Operation: delete-plan

**Usage:** `plan-manager delete-plan <issue_number> [stage]`

Deletes the plan file during cleanup (typically called by `/approve_issue`).

**Steps:**

1. **Compute file path:**
   - If `$3` (stage) is provided: `$HOME/.cc_webui/plans/issue-$2-$3.md`
   - If no stage: `$HOME/.cc_webui/plans/issue-$2.md`

2. **Delete file:**
   ```bash
   rm -f "$HOME/.cc_webui/plans/issue-$2.md"
   ```
   Or with stage:
   ```bash
   rm -f "$HOME/.cc_webui/plans/issue-$2-$3.md"
   ```

3. **Report result:**
   - If file existed and was deleted: `Plan file deleted: [path]`
   - If file was already missing: `Plan file already removed: [path]`

---

### Important Notes

- All paths use `$HOME` (not `~`) to avoid shell expansion issues
- Stage names are sanitized — only `[a-zA-Z0-9-]` characters allowed
- The `write-plan` operation registers the plan as a resource for the Resource Gallery
- The `fetch-issue` operation delegates to `github-issue-reader` by default
- This skill is deployed automatically via the SkillManager symlink system
- Projects can override by placing a `custom-plan-manager` in `.claude/skills/`
