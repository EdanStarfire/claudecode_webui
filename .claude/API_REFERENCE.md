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

Every element of `events` is a literal `EventEnvelope.to_dict()` (`shared/event_envelope.py`)
— the wire bytes ARE the envelope, not an ad-hoc dict `EventEnvelope` merely parses:

```json
{
  "type": "state_change",
  "queue": "ui",
  "sequence": 42,
  "timestamp": "2026-10-03T22:03:07.793169+00:00",
  "data": {
    "session_id": "20620d8a-05a9-4a04-a75b-63e0a637134a",
    "session": { "state": "active", "is_processing": false },
    "timestamp": null
  },
  "backend_id": "local",
  "scope": "20620d8a-05a9-4a04-a75b-63e0a637134a",
  "event_id": "local:ui:20620d8a-05a9-4a04-a75b-63e0a637134a:42"
}
```

- `sequence`: the queue cursor assigned at append — monotonic per queue instance, not
  globally unique across queues.
- `backend_id`: `"local"` (`shared.event_envelope.DEFAULT_BACKEND_ID`) for every
  Backend-originated event in today's single-backend-per-user deployment (issue #1818 will
  introduce other values). `"frontend-local"`
  (`shared.event_envelope.FRONTEND_LOCAL_BACKEND_ID`) for the one Frontend-tier-direct write
  (`src/routers/system.py`'s `server_restarting`, issued on the Frontend's own local
  `ui_queue` rather than relayed from Backend) — kept distinct so its independently-numbered
  `sequence` can never collide with a relayed Backend event's `event_id` on the same queue.
- `scope`: the session or project id this event is about, when one applies — omitted
  entirely (not `null`) when there isn't one (see `EventEnvelope.to_dict()`).
- `event_id`: `f"{backend_id}:{queue}:{scope or '-'}:{sequence}"` — deterministic (not a
  random UUID, so replaying the same recorded fixture through the real pipeline twice
  produces byte-identical fixtures), unique per distinct `emit()` call, and stable across
  redelivery of the exact same already-emitted event.

The payload every producer passes to `emit()` (`type`/`queue`/`backend_id`/`scope`/`sequence`
are all supplied by `emit()` itself, never by the caller) becomes `data` — but some call sites
nest their own fields under their own `"data"` key rather than passing them flat; either
convention ends up delivered as the same flat `.data` shape, since
`shared/event_envelope.py`'s `fold_payload()` unwraps a nested `"data"` key's contents into
`.data` directly rather than leaving it as a nested field. The registry table below lists each
type's required keys as checked *before* that unwrapping — for a type requiring concrete field
names, those fields land in the delivered `.data` unchanged; for a type requiring `data` itself,
that key's own contents (not a field literally named `data`) are what ends up in the delivered
`.data`.

`shared/event_emitter.py`'s `emit()` is the *only* function allowed to write to an
`EventQueue` (enforced by a static AST-scan test, `shared/tests/test_event_emitter_boundary.py`)
and validates every event's payload against the registry before constructing the envelope —
in production a mismatch is logged, not rejected (`STRICT=False`), so a registry bug can
never silently drop a user-visible event; tests can opt into the strict/raising behavior via
`strict_mode()`.

Both poll routes (`backend/routers/poll.py`, `src/routers/poll.py`) return a typed response —
`shared/poll_protocol.py`'s `PollResponse` (`events`/`next_cursor`/`reset`/`evicted`) on
Backend, `FrontendPollResponse` (adds `backend_status`) on the Frontend tier — constructed
explicitly rather than built as an ad-hoc dict. `parse_poll_response()`/`PollBatch` remain the
consumer-side typed read (`events: list[EventEnvelope]`), unchanged since #2063 — reading
either a legacy flat dict or a real envelope dict through `EventEnvelope.from_dict()` already
produced the same `.data` shape, so nothing there needed to change when the wire format
caught up to it.

### Event Type Registry

Source of truth: `shared/event_registry.py`'s `TOP_LEVEL_EVENT_TYPES`. This table is for human
reference — a programmatic consumer should call `shared.event_registry.export_json()` instead
of hand-copying it. The "Required payload key(s)" column lists keys the registry checks on
the `payload` argument passed to `emit()`, *before* `fold_payload()` normalizes it into the
envelope's `data` — rows naming concrete fields (e.g. `message`, `legion_id`) describe fields
that land in `data` unchanged; rows naming `data` itself mean the producer nests its fields
under that key, which `fold_payload()` then unwraps, so the literal key `data` never survives
into the delivered envelope.

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

### `tool_call` Shape

One canonical shape for every tool_call lifecycle transition on the session stream:
`{"type": "tool_call", "queue": "session", "sequence": ..., "timestamp": ..., "data":
{"tool_use_id": ..., "status": ..., "session_id": ..., ...}, "backend_id": "local", "scope":
<session_id>, "event_id": ...}` — `data`'s fields follow the presence semantics
`frontend/src/stores/message.js`'s `handleToolCall` relies on (e.g. an absent `turn_id` means
"do not touch the timestamp"): `tool_use_id`, `status`, `turn_id`, `request_id`, `created_at`,
`session_id`, and an optional `display` (`DisplayProjection`, issue #310) payload. The legacy
message-wrapped duplicate shape and its `emit_tool_call()` shim (stage 2a-C) were removed in
stage 2b-C (#2075) — this is the only shape that has ever existed on the wire since.
