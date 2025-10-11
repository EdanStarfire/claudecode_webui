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
[x] feat: Allow session re-ordering
[x] feat: New sessions should go to the top of the list
[x] bug: Session renaming doesn't show in session list immediately.
[x] qol: User messages bubbles should be blue border with light blue back with black text for consistency with the assistant messages
[x] feat: Support diff views properly for permissions prompts
[x] qol: Shrink session start / session interrupted cards to 1-liners (de-emphasized)
[x] qol: Remove data integrity check
[x] qol: Clicking on an expanded tool call header should collapse the tool call, not just the arrow at the far right.
[x] qol: Make tool use blocks horizontally scrollable
[x] bug: Fix vertical alignment in tool call params blocks
[x] feat: Handle TodoWrite tool content display
[x] feat: Handle Read tool content display
[x] feat: Handle Write tool content display
[x] feat: Handle Edit tool (diff) content display
[x] feat: Handle MultiEdit tool (diff) content display
[x] feat: Handle Task tool content display
[x] feat: Handle Glob tool content display
[x] feat: Handle Grep tool content display
[x] feat: Handle WebFetch tool content display
[x] feat: Handle WebSearch tool content display
[x] feat: Handle BashOutput tool content display
[x] feat: Handle KillShell tool content display
[x] feat: Handle Bash tool content display
[x] bug: autoscroll doesn't scroll when permissions are prompted for
[x] feat: Implement mode switching (plan, autoaccept writes, normal)
[x] bug: Fix approval / deny permissions to disable additional clicks once pressed once. and to show submission of the permission
[x] feat: Handle ExitPlanMode tool content display
[x] bug: delete session modal needs margins or padding
[x] bug: delete session should remove session from session list on left
[x] bug: assistant message content should be trimmed to remove leading/trailing newlines
[x] bug: existing projects not loading at startup
[x] bug: session creation button not causing new session modal to appear.
[x] bug: project clicks result in Unknown UI WebSocket message type project_updated
[x] bug: project collapse/expand is logged twice
[x] bug: re-rendering madness - removes all projects, then re-draws all projects with the new session included, then shows only appropriate new sessions in the list, and then shows lots of duplicate project+session (added it several times - maybe 4-6 times) in the sidebar and then removes them all again and re-renders the sidebar with two of the new project/session.
[x] bug: color of solid green indicator not matching the project status line color (currently solid green = blue)
[x] bug: sessions not selected (processing in the background) do not reflect their status changes until selected.
[x] qol: when collapsing a project, go to the "no session selected pane"
[x] bug: permission results don't seem to get caught until after reselecting the session, causing it to just hang in processing mode (processing bar visible, input box disabled, etc) - it also resulted in the tool not being actually ran.
[x] bug: Clean up diff view now that we're on bootstrap
[x] bug: New websocket disconnect loop occuring
[x] bug: Newlines should not be trimmed from assistant and user responses.
[x] bug: Double Tool Type in accordian header
[x] bug: Fix the formatting of the tool handling as it's missing it's borders, backgrounds, and such
[x] qol: Allow input box to expand upwards when user types multiple lines of text (max 6 lines of height)
[x] bug: Diff viewer line numbers don't scroll with the message window.
[x] bug: When clicking the create session button, it collapses the project header and deselects the current session
[x] bug: Scrolling can push the title bar off the top of the screen on mobile. Titlebar should always stay visible
[x] qol: When adding a session to a collapsed project, it should re-expand the project and select the new session once created

# Polish
[x] qol: Better diff viewer (currently just alternates different lines)
[x] qol: Loading screen for when switching sessions to hide the previous session's content while loading
[x] qol: Move sidebar collapse to hamburger button in title bar

# Cleanup
[x] qol: Redirect app.log to the data/session folder for debugging purposes
[x] qol: Refactor code files for smaller sizes and easier organization
[x] qol: Remove debug messages for WS_LIFECYCLE and Ping - only leave logs where they throw issues
[x] qol: Creating a session needs to add loading block and hide the modal and sidebar on mobile
[x] qol: Mobile drag and drop of projects needs to be re-enabled
[x] qol: Disable delete session inputs after confirming to prevent double-clicks
[x] bug: Deleting a session and then attempting to re-order throws an error
[x] feat: Need function to delete a project (and all associated session data) - similar to the delete session mechanism
[x] qol: Rename session and delete button should be done the same way as the project (show edit on hover over, modal allows editing session name, and a delete session option there).
[x] qol: Remove delete session button.
[x] bug: When moving sessions to the top of the list in a project, a line occurs above the top of the project too, which implies that it can move out of the project. It doesn't, but that line shouldn't be shown above the project header.
[x] qol: change the un-editable features (project folder, session_id) in the edit modals to text with hovertext and ellispis overflow.
[x] qol: New sessions should go to the top of the list in the project
[ ] qol: Mode change toast notification should instead just flash the mode change button as it updates a couple times (~2x over 2s)

# Advanced Functionality
[ ] feat: Handle SlashCommand tool content display
[ ] feat: Handle MCP tool content display
[ ] feat: ExitPlanMode supports alternate exit mode
[x] feat: ToolPermission prompts support suggestions for additional permissions
[ ] feat: ToolPermission Deny - include alternate input for user-defined messages to the LLM when denying
[ ] feat: ToolPermission Deny - support choosing to interrupt or not
[ ] feat: Handle NotebookEdit tool content display
[ ] feat: Handle local command parsing: 
    `{"type": "user", "content": "", "timestamp": 1759240122.8231502, "metadata": {"tool_uses": [], "tool_results": [], "has_tool_uses": false, "has_tool_results": false, "has_thinking": false, "has_permission_requests": false, "has_permission_responses": false, "role": null, "session_id": "76fe166f-9515-4acf-97fa-eaad97e54706", "raw_sdk_message": "UserMessage(content='<local-command-stdout>With your Claude Pro subscription, no need to monitor cost — your subscription includes Claude Code usage</local-command-stdout>', parent_tool_use_id=None)", "source": "sdk", "processed_at": 1759240122.8245418}, "session_id": "76fe166f-9515-4acf-97fa-eaad97e54706", "raw_sdk_message": "{\"__class__\": \"UserMessage\", \"__module__\": \"claude_agent_sdk.types\", \"content\": \"<local-command-stdout>With your Claude Pro subscription, no need to monitor cost \\u2014 your subscription includes Claude Code usage</local-command-stdout>\", \"parent_tool_use_id\": null}", "sdk_message_type": "UserMessage"}`
[ ] feat: Enable bypassPermissions mode switch with confirmation and warning
[ ] feat: Support toolpermissioncontext suggestions: `ToolPermissionContext(signal=None, suggestions=[{'type': 'setMode', 'mode': 'acceptEdits', 'destination': 'session'}])`
[ ] feat: Support markdown for assistant responses (and user input?)
[x] feat: Folder browser for folder selection
[ ] feat: File browser for file folder exploration and viewingS
[ ] feat: change exit session button to be something more like "restart session" which actually restarts the underlying SDK session
[ ] feat: Break up main session class in app.js to be not so monolithic in size

# Major Features
[x] feat: Project support
[ ] feat: Application Configuration Support
[x] qol: Reskinning the entire app to be more aesthetic (bootstrap)
[x] qol: Better mobile layout
