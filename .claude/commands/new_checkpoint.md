## Checkpoints

Checkpoints involve capturing all recent changes from a previous git commit. To do so, follow these instructions:
1. Use `git diff`, `git status`, and `git log` to evaluate all changed code since the last commit and new untracked files.
2. Generate a summary of all changes. Provide this summary to the user and request approval to continue.
3. Once approved (or requested changes are incorporated into the summary), update the DEVELOPMENT_PLAN.md file
  1. This can be by adding new tasks or subtasks where necessary, or
  2. By marking tasks/subtasks as complete/in progress as appropriate.
4. Once PRD is updated, ask for approval to add all changes to github. The user may ask for updates to the PRD or manually do some themselves at this time.
5. Once approved, use `git add` to add all changed/newly added files.
6. Use `git commit` with an appropriately summarized commit message.
7. Use `git push` to finalize the checkpoint.