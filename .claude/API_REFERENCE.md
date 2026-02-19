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
| `POST` | `/api/sessions/{id}/pause` | Pause session |
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

### Image Endpoints (Legacy)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/sessions/{id}/images` | Get images gallery |
| `GET` | `/api/sessions/{id}/images/{image_id}` | Get single image |

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

### Utility Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Serve index.html (frontend) |
| `GET` | `/health` | Health check |
| `GET` | `/api/filesystem/browse` | Browse directories (`path` query param) |

---

## WebSocket Endpoints

### UI WebSocket — `/ws/ui`

Global UI state updates. One connection per browser tab.

**Server → Client Messages:**

| Type | Payload | When |
|------|---------|------|
| `sessions_list` | `{sessions: [...]}` | Session list changes |
| `state_change` | `{session_id, state, ...}` | Session state transitions |
| `project_updated` | `{project_id, ...}` | Project metadata changes |
| `project_deleted` | `{project_id}` | Project deleted |

**Client → Server Messages:**

| Type | Payload | Purpose |
|------|---------|---------|
| `ping` | `{}` | Keep-alive heartbeat |

### Session WebSocket — `/ws/session/{session_id}`

Session-specific message streaming. One connection per active session view.

**Server → Client Messages:**

| Type | Payload | When |
|------|---------|------|
| `message` | `{type, content, ...}` | New message (assistant, system) |
| `tool_call` | `{id, name, input, status, ...}` | Tool call lifecycle updates |
| `permission_request` | `{request_id, tool_name, ...}` | Tool needs user approval |
| `permission_response` | `{request_id, decision, ...}` | Permission decision echoed back |
| `tool_result` | `{tool_use_id, content, ...}` | Tool execution result |
| `state_change` | `{session_id, state, ...}` | Session state change |
| `connection_established` | `{session_id}` | WebSocket connected |
| `queue_update` | `{session_id, item, ...}` | Queue item status change |
| `resource_registered` | `{session_id, resource, ...}` | New resource registered |

**Client → Server Messages:**

| Type | Payload | Purpose |
|------|---------|---------|
| `send_message` | `{content, attachments?}` | Send user message |
| `interrupt_session` | `{}` | Interrupt processing |
| `permission_response` | `{request_id, decision, apply_suggestions?, clarification?, selected_suggestions?}` | Respond to permission request |
| `permission_response_with_input` | `{request_id, decision, updated_input}` | Respond with modified input (AskUserQuestion) |
| `ping` | `{}` | Keep-alive heartbeat |

### Legion WebSocket — `/ws/legion/{legion_id}`

Multi-agent communications. One connection per active timeline/spy view.

**Server → Client Messages:**

| Type | Payload | When |
|------|---------|------|
| `comm` | `{comm_id, from, to, content, ...}` | New inter-agent communication |
| `minion_created` | `{minion_id, ...}` | New minion spawned |
| `schedule_updated` | `{schedule_id, event, ...}` | Schedule state change |
| `ping` | `{}` | Server heartbeat |

**Client → Server Messages:**

| Type | Payload | Purpose |
|------|---------|---------|
| `ping` | `{}` | Keep-alive heartbeat |
