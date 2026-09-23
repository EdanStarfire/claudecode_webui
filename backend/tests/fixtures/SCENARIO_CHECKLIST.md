# Recording Scenario Checklist (issue #1998)

Manual verification checklist for building a raw-layer fixture via a live recording
session. Run all 11 steps against a single recording session on a scratch repo, then
export — the exporter's coverage-inventory check (AC4) verifies all 9 required
markers below are present in the raw log and names any that are missing.

Recording is only available when the Backend was started with
`--enable-session-recording`.

## Steps

1. Create a **non-Docker** session (Docker Isolation off) and enable **Record Session**
   in the manage modal's Isolation section before starting it. Recording is not
   supported on Docker-isolated sessions and locks once the session leaves the
   `created` state, so both toggles must be set before first start.
2. Send a normal message and let the agent respond with a streamed answer.
   → covers **streaming deltas**
3. Send a message that causes the agent to use a tool requiring a permission
   prompt, and **Allow** it.
   → covers **tool call with permission prompt**
4. Trigger another tool call requiring a permission prompt, and **Deny** it.
   → covers **denied permission**
5. Send a message that causes the agent to call `AskUserQuestion`, and answer it.
   → covers **AskUserQuestion**
6. Send a message that spawns a subagent/background task and let it report progress
   before completing.
   → covers **subagent task with progress**
7. While a tool call is in flight, click **Interrupt**.
   → covers **interrupt mid-tool**
8. From the manage modal, **Restart** the session mid-scenario, then send one more
   message. Recording must keep appending to the same `raw_log.jsonl` across the
   restart.
   → covers **session restart**
9. Continue the conversation long enough to trigger context compaction.
   → covers **compaction**
10. On a multi-agent (Legion) session, send or receive an inter-minion comm.
    → covers **inter-minion comm**
11. Open the manage modal and click **Export Fixture**, giving it a name. Confirm the
    export succeeds with all 9 markers checked — if any are missing, re-run the
    corresponding step(s) above on the same session and export again; there's no
    need to restart the whole scenario.

## The 9 required markers

- streaming deltas
- tool call with permission prompt
- denied permission
- AskUserQuestion
- subagent task with progress
- interrupt mid-tool
- session restart
- compaction
- inter-minion comm
