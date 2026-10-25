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
| `GET` | `/api/sessions/{id}/background_tasks` | Latest snapshot of running background tasks `{session_id, tasks:[{task_id, task_type, description}]}`; empty unless the SDK process is alive (issue #2145) |
| `GET` | `/api/sessions/{id}/descendants` | Get all descendant sessions (minion tree) |
| `PATCH` | `/api/sessions/{id}` | Update session fields (model, tools, permissions, cli_path, etc.) |
| `POST` | `/api/sessions/{id}/start` | Start or resume session |
| `POST` | `/api/sessions/{id}/terminate` | Terminate session |
| `POST` | `/api/sessions/{id}/restart` | Restart session (keep message history); allowed from ACTIVE/ERROR/TERMINATED, else 409; on failure returns `{success:false, error}` (issue #2293) |
| `POST` | `/api/sessions/{id}/reset` | Clear messages and fresh start |
| `POST` | `/api/sessions/{id}/disconnect` | End SDK session, keep state |
| `DELETE` | `/api/sessions/{id}` | Delete session |
| `PUT` | `/api/sessions/{id}/name` | Update session name |
| `PUT` | `/api/sessions/{id}/permission-mode` | Set permission mode |
| `POST` | `/api/sessions/{id}/messages` | Send message to session |
| `GET` | `/api/sessions/{id}/messages` | Get messages (paginated: `limit`, `offset`) — epic #1990 stage 5a: a dedicated Frontend-side route (`src/routers/sessions.py`), not blind-relayed; substitutes the Frontend-local `event_cursor` for Backend's own via `PollRelay.wait_until_relayed()` |

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
| `GET` | `/api/sessions/{id}/edit-history` | Chronological Edit/Write/Bash tool calls (see Host paths below) |

#### Host paths in payloads (issue #2279)

`edit-history` entries carry `file_path` and `input.file_path` **relative to the session working
directory** (`old_string`/`new_string`/`content` are untouched). A path outside the working directory
stays absolute and the entry gets `file_path_outside_workdir: true`. Diff payloads are already
repo-relative. These fields intentionally remain absolute **display fields** (registry:
`backend/path_display.py::DISPLAY_PATH_FIELDS`): session and project `working_directory`,
`GET /api/filesystem/browse` paths, resource `file_path`, upload `stored_path`, and tool_use/message
payloads. Archived sessions keep their recorded `working_directory`.

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
shim for one event type. Epic #1990 stage 5c replaced the original two per-stream long-poll
endpoints (`GET /api/poll/ui`, `GET /api/poll/session/{id}`) with one multiplexed endpoint
carrying a subscription set. This section describes the real, current wire shape.

### Poll Endpoints

| Method | Path | Body / query params | Description |
|---|---|---|---|
| `POST` | `/api/poll` | `{"timeout": 30, "ui": {"since": N}, "sessions": {"<id>": {"since": N}, ...}}` | Multiplexed long-poll: UI stream plus a subscription set of session streams in one request |
| `GET` | `/api/poll/cursor` | — | One-shot: current UI queue cursor, for client bootstrap |
| `GET` | `/api/poll/session/{session_id}/cursor` | — | One-shot: current session queue cursor, for client bootstrap |

`POST /api/poll` exists on **both** tiers: Backend's (`backend/routers/poll.py`) is the real
one `SessionCoordinator` writes to; the Frontend API's (`src/routers/poll.py`) reads from local
`EventQueue`s kept in sync by `src/poll_relay.py`'s single consolidated background task. The
browser only ever talks to the Frontend API's copy. `ui`/`sessions` are each optional in the
request — a caller interested in only one stream omits the other. Auth is a normal
`Authorization: Bearer` header (not a query-string token, unlike the endpoints this replaced).

Fairness across the subscribed streams in one request — so one noisy session can't delay UI
events or another session's updates — is implemented once in `shared/poll_multiplex.py`,
shared verbatim by both tiers' routers: it races `wait_for_events()` across every subscribed
queue (`asyncio.wait(..., return_when=FIRST_COMPLETED)`), then assembles each stream's slice via
`events_since()`, truncated to a per-stream quota (`DEFAULT_EVENTS_PER_STREAM_QUOTA = 200`).

### Poll Response Envelope

```json
{
  "ui": {
    "events": [ /* array of event dicts — see Event Shape below */ ],
    "next_cursor": 1234,
    "reset": false,
    "evicted": false,
    "epoch": "a1b2c3d4e5f6a7b8"
  },
  "sessions": {
    "<session_id>": {
      "events": [ /* ... */ ],
      "next_cursor": 42,
      "reset": false,
      "evicted": false,
      "epoch": "a1b2c3d4e5f6a7b8"
    }
  },
  "backend_status": "ok"
}
```

`ui` is present iff the request included a `ui` block; `sessions` always present (possibly
`{}`), one entry per subscribed session_id that's still known (an unknown/deleted session_id
is silently omitted from the response rather than failing the whole request). `backend_status`
is Frontend-tier only (issue #1997).

Per-stream fields, identical shape on both tiers now (`shared/poll_protocol.py`'s
`MultiplexStreamResponse`):

- `next_cursor`: pass as this stream's `since` on the next poll. If this stream's slice was
  quota-truncated, `next_cursor` reflects only the truncated portion actually returned (the
  untruncated remainder stays buffered for the next poll cycle) — never the queue's full
  current cursor.
- `reset`: whether the TRUNCATED/actually-delivered `events` array for this stream contains a
  `stream_reset` marker (epic #1990 stage 5c correction: computed from the truncated slice
  only, via `_contains_stream_reset()`, on both tiers now — not a cursor-magnitude inference).
  A marker sitting beyond a truncation cutoff is simply not yet reflected in `reset`; it
  arrives, with an accurate `reset: true`, once the cursor catches up on a later poll. Backend
  never emits this marker on its own queues (nothing there ever restarts mid-process); the
  Frontend relay emits it into a LOCAL queue when it observes the polled Backend `epoch`
  differ from what it last saw for that stream (a Backend restart made explicit).
- `evicted`: computed from the UNTRUNCATED `events_since()` result for this stream — `since`
  predated the buffer's oldest retained event (genuinely-lost history, distinct from a reset),
  independent of truncation. On the Frontend API's response this also surfaces a Backend-
  reported eviction the relay itself observed (`PollRelay.consume_pending_evicted()`) even
  though nothing about the LOCAL queue's own retained window changed.
- `epoch`: which Backend-process generation produced this stream's data (`secrets.token_hex(8)`,
  regenerated once per Backend process at first import of `shared/event_envelope.py` —
  `PROCESS_EPOCH`). Not persisted — every Backend restart is a new epoch by design. The
  Frontend API's own response surfaces the last epoch its poll-relay observed for that stream
  (`PollRelay.last_epoch()`), not necessarily Backend's CURRENT epoch if the relay hasn't
  polled since a restart yet.

Typed on both tiers via `shared/poll_protocol.py`'s `PollBatch`/`parse_poll_response()`
(`events: list[EventEnvelope]`) — consumers (`backend/tools/scenario_driver/poll_consumer.py`,
`src/tests/simulation/fault_harness.py`) call this per-stream, after unwrapping that stream's
own block out of the multiplexed response.

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
  "event_id": "local:ui:20620d8a-05a9-4a04-a75b-63e0a637134a:42",
  "epoch": "a1b2c3d4e5f6a7b8"
}
```

- `sequence`: the queue cursor assigned at append — monotonic per queue instance, not
  globally unique across queues.
- `backend_id`: `"local"` (`shared.event_envelope.DEFAULT_BACKEND_ID`) for every
  Backend-originated event in today's single-backend-per-user deployment (issue #1818 will
  introduce other values). `"frontend-local"`
  (`shared.event_envelope.FRONTEND_LOCAL_BACKEND_ID`) for Frontend-tier-direct writes issued
  on a local queue rather than relayed from Backend: `src/routers/system.py`'s
  `server_restarting` (`ui_queue`), and epic #1990 stage 5a's `stream_reset`
  (`src/poll_relay.py`, either queue) — kept distinct so their independently-numbered
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

Both poll routes (`backend/routers/poll.py`, `src/routers/poll.py`) build each stream's entry
via `shared/poll_protocol.py`'s `MultiplexStreamResponse` (`events`/`next_cursor`/`reset`/
`evicted`/`epoch`) — constructed explicitly rather than as an ad-hoc dict — and nest it under
`"ui"`/the session_id in the overall response dict; the Frontend tier adds the top-level
`backend_status` field. `parse_poll_response()`/`PollBatch` remain the consumer-side typed
read (`events: list[EventEnvelope]`), applied per-stream after unwrapping — reading either a
legacy flat dict or a real envelope dict through `EventEnvelope.from_dict()` already produced
the same `.data` shape, so nothing there needed to change when the wire format caught up to it.

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
| `stream_reset` | ui, session | `old_epoch`, `new_epoch` |
| `proxy_event` | session | `data` |

Epic #1990 stage 5a added `stream_reset` (valid on both `ui` and `session`): emitted by
`src/poll_relay.py` into a LOCAL queue only — never by a Backend-side producer — when the
relay observes the polled Backend `epoch` differ from what it last saw for that stream (a
Backend restart made explicit, replacing the old cursor-magnitude inference). The browser's
per-event dispatch treats it as a mid-batch resync trigger (see `frontend/src/stores/
polling.js`): events before the marker in the same batch are legitimate pre-reset history and
need no unwinding; the marker itself (and anything after it in that same batch) triggers the
same full resync `reset: true` already does, rather than being treated as a normal incremental
update.

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
