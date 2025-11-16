---
description: Create a checkpoint by reviewing changes and committing with proper message
allowed-tools: [Read, Edit, Bash, Skill]
---

## Checkpoints

Checkpoints involve capturing all recent changes from a previous git commit. Follow these steps:

1. **Review Changes**
   **Invoke the `git-state-validator` skill** to review all changes:
   - Changed files since last commit
   - New untracked files
   - Overall repository status

2. **Generate Summary**
   Generate a summary of all changes. Provide this summary to the user and request approval to continue.

3. **Update Development Plan**
   Once approved (or requested changes are incorporated into the summary), update the DEVELOPMENT_PLAN.md file:
   - Add new tasks or subtasks where necessary, or
   - Mark tasks/subtasks as complete/in progress as appropriate

4. **Confirm Changes**
   Once PRD is updated, ask for approval to commit all changes to git. The user may ask for updates to the PRD or manually do some themselves at this time.

5. **Commit Changes**
   Once approved:
   - Use `git add` to add all changed/newly added files
   - **Invoke the `git-commit-composer` skill** to create well-formatted commit message following project conventions
   - Use `git push` to finalize the checkpoint