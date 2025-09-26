[x] qol: When creating a new session, it should automatically "start" it as well.
[x] qol: If the session is not "connected" when an input is submitted, it should "connect" or "start" the session first so that it can submit the message properly to the claude code session.
[x] bug: fix tools defaults
[x] bug: Create New Session dialog fix permissionModes defaults and options
[x] bug: Create New Session dialog should remove example model preview text (it's very old) and just leave the text box blank for now
[x] bug: Create New Session dialog should default default to the working dir: `.`, not `/`
[x] bug: Create New Session dialog should not default to a list of tools, that box should remain blank by default and optional.
[x] bug: When selecting a previous session, Start doesn't seem to work at all (never successfully starts) CLIP 1
[x] bug: After terminating a session and then selecting another pre-existing session, the websocket starts into a connect/disconnect loop
[x] qol: Use the init and result messages to mark "working" and "ready for input"...
[x] bug: Autoscroll functionality isn't working.
[x] bug: Seems like the front-end doesn't get updated responses from a reconnected session.
[x] bug: The state of the session in the session list does not update based on the state of the session itself (terminated sessions that get restarted do not show as active until refreshing the screen)
[x] bug: The webUI is receiving duplicate messages for each generated one from Claude code
[x] bug: On terminate, the browser still tries to re-open the websocket afterwards, which fails in a loop
[x] feat: Remove start/pause/terminate session buttons from UI and replace with a single "exit session" that disconnects the session's websocket (same as deselecting the conversation) and stops displaying the messages for that conversation in the UIs conversation window.
[x] bug: Any sessions that are in state "active" should be resumed on application startup - otherwise, it's expecting a working client SDK session and there is none.
[x] bug: When reshowing all the session messages from the past, the LAST assistant message doesn't seem to show in the UI.
[x] bug: When reshowing all the session messages from the past, the init and result messages from the past should also be suppressed.
[x] feat: When resuming an SDK client (with the resume function, not just reconnecting via the websocket due to session switching), we should send a system message to the messages.jsonl and the webui that says that a new session is being started to resume conversation.
[x] bug: The websocket appears to loop connect/disconnect in a certain scenario - unsure what it is, but it does occur.
[x] qol: Clean up UI indicators (Connected at top right, just a colored dot with status as hover text)
[x] qol: Clean up UI indicators (Make session indicators in session list colored icons on the left of the session name with status as hover text)
[x] qol: Clean up UI indicators (Make session indicators in session header colored icons on the left of the session name with status as hover text)
[x] qol: Clean up UI indicators (Make session titles truncated to a single line (...) on the front-end only)
[x] bug: Cannot load a session when messages.jsonl are empty but exists (or something along that line)
[x] bug: The session-info section in the session list can be removed as we don't need that second line now.
[x] feat: Make sidebar for session info collapsable AND adjustable to like max of 30% width.
[x] QOL: Make +New Session button just a heavy + sign button, and the refresh button just the normal refesh icon button.
[x] bug: failed sessions are not propagated to the webUI
[x] bug: failed sessions try to restart when they shouldn't: "WARNING - src.session_manager - Cannot start session {session_id} in state SessionState.ERROR"
[x] qol: instead of capturing the SDK error mesage in the chat, can we place it at the top in the bar with the "Exit session" button? If the session is in an error state, we can avoid trying to initialize the streaming message and just get the last error message from the state + the error state itself and avoid trying to create the message WebSoc
[x] bug: When going from IS PROCESSING session to ERROR session, the processing bar is not hidden
[x] bug: When going from an ERROR session to an in processing session, the processing bar is shown, but the input box and button are still in error mode
[x] bug: When starting up, all sessions that are in an `is_processing = true` state need to be reset to `is_processing = false` since no SDKs could possibly be processing (have not been launched by the app yet), otherwise they NEVER clear the is_processing state.
[x] qol: make the state color circles be purple for "in processing" sessions so that you can see when something's working on something vs waiting for input
[x] feat: Sessions support naming, default naming is date-time stamp?
[x] feat: Handle displaying tool calls with enough detail to understand what it is doing
[x] feat: Handle displaying tool results with enough detail to understand what was done
[x] bug: permissions_callback not sent to resumed sessions like it should be - it is passed to new sessions though
[x] feat: implement permissions_callback handling
[x] feat: capture permission prompt functionality in a generic sense (web_server.py:582 function)
[x] feat: Interactive permission prompt mechanism
[x] feat: capture ThinkingBlock messages for surfacing later on
[x] feat: Implement cancel "working" state using client.interrupt  - example: https://github.com/anthropics/claude-code-sdk-python/blob/main/examples/streaming_mode.py
[x] feat: Enable deletion of a session from session header.
[x] bug: In messages.jsonl, some messages have blank content (result, system messages for example) from the SDK - these should be more descriptive and include appropriate metadata we need to ensure we understand what the message is doing.
[x] bug: listen on all available IPs, not just 127.0.0.1
[x] qol: Refactor to remove fallbacks and duplicate processing / separate flows for real-time and historical webUI interaction
[x] bug: Session start messages (init) not visible in WebUI - were being suppressed by message filtering
[x] bug: Client_launched messages not appearing in messages.jsonl - sdk_was_created logic was incorrect
[ ] qol: use the Claude provided tool use ID for tracking the permission request + approval/denial instead of an internally generated UUID
[ ] feat: Allow session re-ordering
[ ] feat: New sessions should go to the top of the list
[ ] bug: Layout on mobile is... yucky.
[ ] feat: Support diff views properly for permissions prompts
[x] qol: Shrink session start / session interrupted cards to 1-liners (de-emphasized)
[ ] qol: Redirect app.log to the data/session folder for debugging purposes
[ ] bug: autoscroll doesn't scroll when permissions are prompted for
[ ] feat: Implement Todolist in WebUI
[ ] feat: Implement Todolist clearing ability in WebUI
[ ] feat: Implement mode switching (plan, autoaccept writes, normal)
[x] qol: Remove data integrity check
[ ] qol: Refactor code files for smaller sizes and easier organization
-- Tool Handling:
[ ] feat: Handle TodoWrite tool content display
[ ] feat: Handle Read tool content display
[ ] feat: Handle Write tool content display
[ ] feat: Handle Edit tool (diff) content display
[ ] feat: Handle MultiEdit tool (diff) content display
[ ] feat: Handle Task tool content display
[ ] feat: Handle NotebookEdit tool content display
[ ] feat: Handle Glob tool content display
[ ] feat: Handle Grep tool content display
[ ] feat: Handle ExitPlanMode tool content display
[ ] feat: Handle WebFetch tool content display
[ ] feat: Handle WebSearch tool content display
[ ] feat: Handle SlashCommand tool content display
[ ] feat: Handle BashOutput tool content display
[ ] feat: Handle KillShell tool content display
[ ] feat: Handle Bash tool content display
[ ] feat: Handle MCP tool content display
