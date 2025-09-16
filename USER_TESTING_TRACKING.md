[x] qol: When creating a new session, it should automatically "start" it as well.
[ ] qol: If the session is not "connected" when an input is submitted, it should "connect" or "start" the session first so that it can submit the message properly to the claude code session.
[ ] qol: figure out how to represent blank messages from the messages.jsonl
[x] bug: fix tools defaults
[x] bug: Create New Session dialog fix permissionModes defaults and options
[x] bug: Create New Session dialog should remove example model preview text (it's very old) and just leave the text box blank for now
[x] bug: Create New Session dialog should default default to the working dir: `.`, not `/`
[x] bug: Create New Session dialog should not default to a list of tools, that box should remain blank by default and optional.
[ ] bug: When selecting a previous session, Start doesn't seem to work at all (never successfully starts) CLIP 1
[ ] bug: After terminating a session, the websocket starts into a connect/disconnect loop
[ ] qol: Use the init and result messages to mark "working" and "ready for input"...
[ ] feat: Implement cancel "working" state using client.interrupt  - example: https://github.com/anthropics/claude-code-sdk-python/blob/main/examples/streaming_mode.py
[ ] bug: verify autoscroll functionality - I don't think it's working


