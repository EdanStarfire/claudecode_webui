# API Reference

Complete REST and WebSocket API reference for Claude WebUI. For backend architecture details, see [CLAUDE.md](../CLAUDE.md).

## REST API

### Project Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/projects` | Create project (`name`, `working_directory`, `is_multi_agent`, `max_concurrent_minions`) |
| `GET` | `/api/projects` | List all projects with sessions |
| `GET` | `/api/projects/{id}` | Get project with sessions |
| `PUT` | `/api/projects/{id}` | Update name/expansion state |
| `DELETE` | `/api/projects/{id}` | Delete project and all sessions |
| `PUT` | `/api/projects/{id}/toggle-expansion` | Toggle sidebar expansion |
| `PUT` | `/api/projects/reorder` | Reorder projects (`project_ids: string[]`) |
| `PUT` | `/api/projects/{id}/sessions/reorder` | Reorder sessions within project (`session_ids: string[]`) |

### Session Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/sessions` | Create session (`project_id`, `permission_mode`, `tools`, `model`, `name`) |
| `GET` | `/api/sessions` | List all sessions |
| `GET` | `/api/sessions/{id}` | Get session info |
| `GET` | `/api/sessions/{id}/descendants` | Get all descendant sessions (minion tree) |
| `PATCH` | `/api/sessions/{id}` | Update session fields (model, tools, permissions, cli_path, etc.) |
| `POST` | `/api/sessions/{id}/start` | Start or resume session |
| `POST` | `/api/sessions/{id}/terminate` | Terminate session |
| `POST` | `/api/sessions/{id}/restart` | Restart session (keep message history) |
| `POST` | `/api/sessions/{id}/reset` | Clear messages and fresh start |
| `POST` | `/api/sessions/{id}/disconnect` | End SDK session, keep state |
| `DELETE` | `/api/sessions/{id}` | Delete session |
| `PUT` | `/api/sessions/{id}/name` | Update session name |
| `PUT` | `/api/sessions/{id}/permission-mode` | Set permission mode |
| `POST` | `/api/sessions/{id}/messages` | Send message to session |
| `GET` | `/api/sessions/{id}/messages` | Get messages (paginated: `limit`, `offset`) |

### File Upload Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/sessions/{id}/upload` | Upload file to session storage |
| `GET` | `/api/sessions/{id}/files` | List uploaded files |
| `DELETE` | `/api/sessions/{id}/files/{file_id}` | Delete uploaded file |

### Resource Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/sessions/{id}/resources` | Get resource gallery metadata |
| `GET` | `/api/sessions/{id}/resources/{resource_id}` | Get single resource |
| `GET` | `/api/sessions/{id}/resources/{resource_id}/download` | Download resource file |
| `DELETE` | `/api/sessions/{id}/resources/{resource_id}` | Soft-remove resource |

### Diff Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/sessions/{id}/diffs` | Get diff summary (changed files, commit list) |
| `GET` | `/api/sessions/{id}/diffs/{file_path}` | Get diff content for specific file |

### Queue Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/sessions/{id}/enqueue` | Add message to queue (`content`, `reset_session`, `metadata`) |
| `GET` | `/api/sessions/{id}/queue` | Get queue status and items |
| `POST` | `/api/sessions/{id}/queue/{queue_id}/cancel` | Cancel queued item |
| `POST` | `/api/sessions/{id}/queue/{queue_id}/requeue` | Re-queue item at front |
| `POST` | `/api/sessions/{id}/queue/clear` | Clear all pending items |
| `POST` | `/api/sessions/{id}/queue/pause` | Pause/resume queue (`paused: bool`) |
| `PATCH` | `/api/sessions/{id}/queue/config` | Update queue timing config |

### Legion Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/legions/{id}/timeline` | Get comm timeline (paginated: `limit`, `offset`) |
| `GET` | `/api/legions/{id}/hierarchy` | Get minion hierarchy tree |
| `POST` | `/api/legions/{id}/comms` | Send comm to minion |
| `POST` | `/api/legions/{id}/minions` | Create new minion |
| `POST` | `/api/legions/{id}/halt` | Emergency halt all minions |
| `POST` | `/api/legions/{id}/resume` | Resume all minions |

### Schedule Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/legions/{id}/schedules` | List schedules (optional: `minion_id`, `status`) |
| `POST` | `/api/legions/{id}/schedules` | Create schedule (`minion_id`, `name`, `cron_expression`, `prompt`, `reset_session`, `max_retries`, `timeout_seconds`) |
| `GET` | `/api/legions/{id}/schedules/{schedule_id}` | Get schedule details |
| `PATCH` | `/api/legions/{id}/schedules/{schedule_id}` | Update schedule fields |
| `POST` | `/api/legions/{id}/schedules/{schedule_id}/pause` | Pause schedule |
| `POST` | `/api/legions/{id}/schedules/{schedule_id}/resume` | Resume schedule |
| `POST` | `/api/legions/{id}/schedules/{schedule_id}/cancel` | Cancel schedule permanently |
| `DELETE` | `/api/legions/{id}/schedules/{schedule_id}` | Delete schedule |
| `GET` | `/api/legions/{id}/schedules/{schedule_id}/history` | Get execution history (`limit`, `offset`) |

### Template Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/templates` | List all minion templates |
| `GET` | `/api/templates/{id}` | Get template details |
| `POST` | `/api/templates` | Create template |
| `PATCH` | `/api/templates/{id}` | Update template fields |
| `DELETE` | `/api/templates/{id}` | Delete template |

### Permission Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/preview-permissions` | Preview effective permissions for a working directory (`working_directory`, `setting_sources`, `session_allowed_tools`) |

### System Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/git-status` | Get git status of the project |
| `POST` | `/api/restart-server` | Restart the backend server (rate-limited) |

### Debug Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/debug/client-buffer` | Submit a frontend debug ring buffer (`session_id`, `browser`, `submitted_at`, `reason`, `events`); logged to `client_debug.log` (issue #1931) |

### Utility Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Serve index.html (frontend) |
| `GET` | `/health` | Health check |
| `GET` | `/api/filesystem/browse` | Browse directories (`path` query param) |

---

## Event Streaming (HTTP Long-Polling)

Issue #498 replaced WebSockets with HTTP long-polling across both tiers; issue #2063 (epic
#1990 stage 2a) added a typed event registry server-side, with a temporary browser-compatibility
shim for one event type. This section describes the real, current wire shape.

### Poll Endpoints

| Method | Path | Query params | Description |
|---|---|---|---|
| `GET` | `/api/poll/ui` | `since` (cursor, default 0), `timeout` (seconds) | Global UI event stream — one per browser tab |
| `GET` | `/api/poll/session/{session_id}` | `since`, `timeout` | Per-session event stream |

Both exist on **both** tiers post-#498: Backend's are the real queues `SessionCoordinator`
writes to; the Frontend API's read from a local `EventQueue` kept in sync by
`src/poll_relay.py`'s background fan-out. The browser only ever talks to the Frontend API's copy.

### Poll Response Envelope

```json
{
  "events": [ /* array of event dicts — see Event Shape below */ ],
  "next_cursor": 1234,
  "reset": false,
  "evicted": false
}
```

- `next_cursor`: pass as `since` on the next poll.
- `reset`: the cursor space restarted beneath the caller (e.g. a Backend restart) — treat as
  "local view may be stale," not just "nothing new."
- `evicted`: `since` predated the buffer's oldest retained event (genuinely-lost history,
  distinct from a reset).
- The Frontend API's own response additionally carries `backend_status` (issue #1997).

Typed on both tiers via `shared/poll_protocol.py`'s `PollBatch`/`parse_poll_response()`
(`events: list[EventEnvelope]`).

### Event Shape

Every element of `events` is `{"type": <registered type>, ...<payload>}`. The payload's own
shape (top-level fields vs. nested under a `data` key) varies by `type` — see the registry table
below for exactly which keys a given `type` requires — and is **unchanged from pre-#2063
behavior**: this stage did not alter any wire bytes except `tool_call`'s (see that section
below).

`shared/event_emitter.py`'s `emit()` is the *only* function allowed to write to an `EventQueue`
(enforced by a static AST-scan test, `shared/tests/test_event_emitter_boundary.py`) and validates
every event against the registry before appending — in production a mismatch is logged, not
rejected, so a registry bug can never silently drop a user-visible event.

`shared/event_envelope.py`'s `EventEnvelope` is the typed, in-process representation `emit()`
and `parse_poll_response()` use (`type`, `queue`, `sequence`, `timestamp`, `data`, `backend_id`,
`scope`, plus a derived `event_id`) — **not yet the literal wire format**. Today's wire bytes are
still the ad-hoc shape above; `EventEnvelope.from_dict()` tolerantly folds whatever a raw wire
dict contains into `.data`. A future stage may move the wire format itself onto this typed
model; this one does not.

### Event Type Registry

Source of truth: `shared/event_registry.py`'s `TOP_LEVEL_EVENT_TYPES`. This table is for human
reference — a programmatic consumer should call `shared.event_registry.export_json()` instead
of hand-copying it.

| Type | Queue(s) | Required payload key(s) |
|---|---|---|
| `audit_event` | audit | `data` |
| `audit_event_flush` | audit | — |
| `notification` | ui | `data` |
| `schedule_monitor_error` | ui | `legion_id`, `schedule_id`, `error` |
| `schedule_updated` | ui | `schedule`, `deleted` |
| `schedule_execution` | ui | `execution`, `schedule_id` |
| `project_updated` | ui | `data` |
| `project_deleted` | ui | `data` |
| `session_deleted` | ui | `data` |
| `state_change` | ui | `data` |
| `server_restarting` | ui | `message` |
| `mcp_oauth_complete` | ui | `server_id` |
| `secret_oauth_complete` | ui | `flow_id`, `success` |
| `secret_refreshed` | ui | `secret_name` |
| `secret_refresh_failed` | ui, session | ui: `secret_name`, `error`; session: `data` |
| `mcp_oauth_refreshed` | ui | `server_id` |
| `rate_limits_update` | ui | `data` |
| `session_reset` | ui | `data` |
| `session_watchdog_alert` | ui | `session_id`, `watchdog`, `details` |
| `session_self_restart` | ui | `data` |
| `session_restart_error` | ui | `data` |
| `resource_registered` | session | `resource` |
| `link_registered` | session | `link` |
| `queue_update` | session | `action`, `item` |
| `usage_updated` | session | `session_id`, `usage` |
| `assistant_delta` | session | `session_id`, `data` |
| `message` | session | `session_id`, `data` |
| `context_update` | session | `input_tokens`, `context_window`, `context_pct` |
| `tool_call` | session | `session_id`, `data` |
| `resource_removed` | session | `resource_id` |
| `proxy_event` | session | `data` |

Stage 2b-B (#2065 AC7) wired up both previously-orphaned UI types: `server_restarting`
is a cross-tab imminent-restart notice (every connected tab's `UI_EVENT_HANDLERS` calls
`uiStore.showRestartModal({remote: true, message})`, not just the one that initiated the
restart — guarded on `uiStore.restartInProgress` so the initiating tab's own locally-driven
modal isn't reset out from under it), and `session_self_restart` is a lightweight success
acknowledgment paralleling `session_restart_error` (`frontend/src/stores/polling.js`). Same
stage also added session-stream handlers for `secret_refresh_failed`/`proxy_event` (both
routed to `useSecretsStore()`/`console.log` respectively) and removed the dead `sessions_list`
UI case (handled by the browser pre-2b-B but produced by nothing server-side).

#### `message`'s nested `data.type` (`MessageType` enum, `backend/message_parser.py`, + `tool_call`)

`system`, `assistant`, `user`, `result`, `tool_use`, `tool_result`, `tool_error`,
`permission_request`, `permission_response`, `thinking`, `session_start`, `session_end`,
`status_update`, `processing`, `error`, `warning`, `exception`, `unknown`, `tool_call`.

#### `system`-typed messages' `subtype` field

`client_launched`, `interrupt`, `mcp_server_degraded`, `session_failed`, `stderr`,
`task_notification`, `task_progress`, `task_started`, `task_updated`, `unknown`,
`local_command_response`, `agent_notification`, `api_retry`, `permission_mode_change`,
`replay_complete`.

### `tool_call` — Canonical Shape and Legacy Shim

Stage 2a-C (issue #2063 AC5) introduced one canonical shape plus a temporary compatibility shim,
both on the session stream, for every tool_call lifecycle transition:

- **Canonical** (bare): `{"type": "tool_call", "session_id": ..., "data": {...}, "timestamp": ...}`
- **Legacy shim** (removed in stage 2b): `{"type": "message", "session_id": ..., "data": {..., "type": "tool_call"}, "timestamp": ...}`

Both are emitted for every transition today — `shared/event_emitter.py`'s `emit_tool_call()` is
the single, clearly-marked function responsible, so stage 2b can delete it and collapse both
call sites down to the canonical shape alone in one place. The `data` dict's fields follow the
presence semantics `frontend/src/stores/message.js`'s `handleToolCall` relies on (e.g. an absent
`turn_id` means "do not touch the timestamp"): `tool_use_id`, `status`, `turn_id`, `request_id`,
`created_at`, and an optional `display` (`DisplayProjection`, issue #310) payload.
