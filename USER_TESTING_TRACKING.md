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
[ ] feat: Implement cancel "working" state using client.interrupt  - example: https://github.com/anthropics/claude-code-sdk-python/blob/main/examples/streaming_mode.py
[ ] qol: figure out how to represent blank messages from the messages.jsonl
[ ] bug: The websocket appears to loop connect/disconnect in a certain scenario - unsure what it is, but it does occur.
[ ] feat: capture permission prompt functionality in a generic sense
