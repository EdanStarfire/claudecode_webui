# Frontend Architecture — Vue 3 + Pinia + Vite

Agent-oriented guide to the Claude WebUI frontend. For backend architecture, see the root [CLAUDE.md](../CLAUDE.md). For tool handler development, see [TOOL_HANDLERS.md](../TOOL_HANDLERS.md).

## Quick Reference

| Metric | Value |
|--------|-------|
| Framework | Vue 3.4 + Composition API |
| State | Pinia 2.1 (23 stores) |
| Build | Vite 7.1 |
| Router | Vue Router 4 (hash history) |
| CSS | Bootstrap 5.3 + scoped component styles |
| Components | Organized by folder — see [Component Organization](#component-organization) |
| Composables | 17 |
| Utils | 14 |

## Directory Structure

```
frontend/
├── src/
│   ├── main.js                    # App entry: createApp, Pinia, Router
│   ├── App.vue                    # Root component
│   ├── router/index.js            # 20 routes (5 main + 15 settings/* routes)
│   ├── stores/                    # 23 Pinia stores
│   ├── composables/               # 17 reusable composition functions
│   ├── utils/                     # 14 utility modules
│   ├── components/
│   │   ├── analytics/              # Analytics dashboard: filters, time-series chart, summary cards
│   │   ├── audit/                  # Audit log view: stream/turns tabs, event rows
│   │   ├── common/                 # Shared building blocks: banners, buttons, full-screen viewers
│   │   ├── configuration/          # Session/app configuration tabs (MCP, features, providers, secrets)
│   │   ├── configuration/fields/   # Reusable field widgets consumed by FieldRenderer
│   │   ├── configuration/providers/ # LiteLLM provider config editor and status cards
│   │   ├── layout/                 # App chrome: project/agent navigation strip, header, right sidebar shell, modals
│   │   ├── legion/                 # Multi-agent minion tree and detail views
│   │   ├── messages/                # Message list and per-type message rendering
│   │   ├── messages/tools/          # Activity timeline: per-message tool-call nodes and detail
│   │   ├── project/                # Project overview and create/edit dialogs
│   │   ├── session/                 # Chat interface container and session dialogs
│   │   ├── settings/               # Settings editor shell (sidebar, toolbar, source markers)
│   │   ├── settings/sections/       # Per-area settings sections (General, Model, MCP, Isolation, etc.)
│   │   ├── statusbar/               # Session status and rate limit indicators
│   │   ├── tasks/                   # Right sidebar panels: tasks, diff, edit history, resources, queue
│   │   └── tools/                   # Per-tool-type rendering handlers — see TOOL_HANDLERS.md
│   └── assets/
│       ├── styles.css             # Global styles
│       └── tool-theme.css         # Tool handler CSS variables
├── vite.config.js                 # Dev server + proxy + build config
├── index.html                     # Entry point
└── package.json                   # Dependencies
```

## Routing

Hash-based routing (`createWebHashHistory`):

| Path | Component | Purpose |
|------|-----------|---------|
| `/` | `NoSessionSelected` | Landing page |
| `/project/:projectId` | `ProjectOverview` | Project management |
| `/session/:sessionId` | `SessionView` | Chat interface |
| `/session/:sessionId/archive/:archiveId` | `SessionView` | Archived session (read-only) |
| `/archive/agent/:agentId/:archiveId` | `SessionView` | Deleted agent archive (read-only) |

## Pinia Stores (23)

### Core Stores (6)

#### `session.js` — Session lifecycle & CRUD
- **State**: `sessions` (Map), `currentSessionId`, `inputCache` (Map), `initData` (Map), `deletingSessions` (Set)
- **Key actions**: `fetchSessions()`, `createSession()`, `selectSession()` (with auto-start), `deleteSession()`, `startSession()`, `terminateSession()`, `restartSession()`, `resetSession()`, `patchSession()`, `setPermissionMode()`
- **Note**: Abort mechanism prevents race conditions on rapid selection changes

#### `project.js` — Project hierarchy
- **State**: `projects` (Map), `currentProjectId`
- **Key actions**: `fetchProjects()`, `createProject()`, `deleteProject()`, `toggleExpansion()`, `reorderProjects()`, `reorderSessionsInProject()`
- **Helpers**: `formatPath()`, `getStatusBarSegments()`

#### `message.js` — Messages & tool lifecycle
- **State**: `messagesBySession` (Map), `toolCallsBySession` (Map), `toolSignatureToId` (Map), `permissionToToolMap` (Map), `activeToolUses` (Map), `orphanedToolUses` (Map), `backendToolStates` (Map)
- **Key actions**: `loadMessages()`, `addMessage()`, `handleToolCall()` (unified handler), `handlePermissionRequest()`, `handlePermissionResponse()`, `toggleToolExpansion()`, `syncMessages()`
- **Features**: Orphaned tool detection on restart/interrupt/termination, backend display metadata cache, deduplication on reconnect

#### `polling.js` — HTTP long-polling
- **State**: `uiConnected`, `uiRetryCount`, `sessionConnected`, `sessionRetryCount`, `sessionCursors` (Map)
- **Key actions**: `startUIPolling()`, `startSessionPolling(sessionId)`, `sendMessage()`, `sendPermissionResponse()`, `sendPermissionResponseWithInput()`, `interruptSession()`
- **Features**: Cursor-based incremental polling (`/api/poll/ui`, `/api/poll/session/{id}`), exponential backoff (up to 30s), page-visibility pause/resume

#### `legion.js` — Multi-agent data
- **State**: `commsByLegion` (Map), `minionsByLegion` (Map), `currentLegionId`
- **Key actions**: `loadTimeline()` (paginated), `addComm()`, `sendComm()`, `loadMinions()`, `createMinion()`, `haltAll()`, `resumeAll()`

#### `ui.js` — UI state & persistence
- **State**: `rightSidebarCollapsed`, `rightSidebarWidth`, `rightSidebarActiveTab`, `browsingProjectId`, `expandedStacks` (Set), `autoScrollEnabled`, `isRedBackground`, `activeModal`, `restartInProgress`
- **Persistence**: localStorage with `webui-sidebar-` prefix
- **Key actions**: `toggleRightSidebar()`, `setBrowsingProject()`, `toggleStack()`, `showModal()`, `hideModal()`, `showRestartModal()`

### Queue, Schedule & Resource Stores (3)

#### `queue.js` — Message queue per session
- **State**: `queuesBySession` (Map), `pausedBySession` (Map)
- **Key actions**: `fetchQueue()`, `enqueueMessage()`, `cancelItem()`, `requeueItem()`, `clearQueue()`, `pauseQueue()`, `handleQueueUpdate()`

#### `schedule.js` — Cron schedules per legion
- **State**: `schedulesByLegion` (Map), `scheduleCountByMinion` (Map), `selectedScheduleId`, `executionHistory`
- **Key actions**: `loadSchedules()`, `createSchedule()`, `updateSchedule()`, `pauseSchedule()`, `resumeSchedule()`, `deleteSchedule()`, `loadHistory()`, `handleScheduleEvent()`

#### `resource.js` — Resource gallery per session
- **State**: `resourcesBySession` (Map), `fullViewOpen`, `currentResourceIndex`, `textContentCache` (Map)
- **Key actions**: `loadResources()`, `addResource()`, `removeResource()`, `openFullView()`, `fetchTextContent()`
- **Helpers**: `isImageResource()`, `isTextResource()`, `getResourceIcon()`, `getResourceUrl()`

### Diff, Task & MCP Stores (4)

#### `diff.js` — Git diff per session
- **State**: `diffBySession` (Map), `currentMode` ('total'|'commits'), `fullViewOpen`, `fileDiffCache` (Map)
- **Key actions**: `loadDiff()`, `refreshDiff()`, `loadFileDiff()`, `openFullView()`, `setMode()`

#### `task.js` — SDK task tracking per session
- **State**: `tasksBySession` (Map), `sessionsWithTasks` (Set)
- **Key actions**: `createTask()`, `updateTask()`, `clearTasks()`, `reconstructFromMessages()`, `handleTaskToolResult()`
- **Helpers**: `tasksForSession()`, `activeTask()`, `taskStats()`

#### `mcp.js` — Active MCP server state per session
- Runtime state of active MCP servers, synced from backend

#### `mcpConfig.js` — MCP server configuration CRUD
- Persistent MCP server definitions (STDIO/SSE/HTTP, OAuth 2.1, enable/disable)

### Settings & Configuration Stores (4)

#### `settings.js` — Per-area settings draft management
- Per-area in-memory drafts, dirty area tracking, pending navigation state, and sidebar UI state for the settings editor

#### `profile.js` — Configuration profile management
- Manages configuration profiles (the base layer in the 3-tier Profile → Template → Session inheritance chain)

#### `providerCatalog.js` — Provider model catalog
- Provider model catalog entries and provider status including pending changes and restart tracking

#### `secrets.js` — Host-level secrets vault
- Secrets CRUD with backend status, OAuth2 token health tracking, and refresh event handlers

### Analytics, Audit & Usage Stores (3)

#### `analytics.js` — Analytics view state
- Filters, time range presets, time series data, and bucket-size calculations for the analytics dashboard

#### `audit.js` — Audit log events
- Audit event storage with filtering by time, session, project, and event type; loads stream and turns data

#### `usage.js` — API usage tracking
- API usage aggregates per session and current session usage statistics

### Miscellaneous Stores (3)

#### `editHistory.js` — Edit history per session
- Edit history tracking with per-field expanded states, terminal tool deduplication, and debounced loads

#### `proxy.js` — Credential vault and proxy state
- Credential vault, per-session proxy status, and proxy access logs (HTTP and DNS query tracking)

#### `template.js` — Session configuration templates
- Reusable session configuration templates with CRUD operations and template listing

## Composables (17)

### Tool Composables (2)

#### `useToolResult.js` — Tool result extraction
- **Input**: `toolCallRef` (reactive)
- **Returns**: `hasResult`, `isError`, `resultContent`, `formattedInput`
- **Used by**: Most tool handler components

#### `useToolStatus.js` — Tool status computation
- **Input**: `toolRef` (reactive)
- **Returns**: `effectiveStatus`, `isOrphaned`, `orphanedInfo`, `statusColor`, `hasError`
- **Helpers**: `getEffectiveStatusForTool()`, `getColorForStatus()` (non-reactive versions)
- **Used by**: TimelineNode, ToolCallCard, tool handlers

### UI Composables (5)

#### `useAgentColor.js` — Per-agent color assignment
- Stable deterministic color per agent/session ID for visual differentiation

#### `useLongPress.js` — Long-press gesture handler
- Touch/mouse long-press detection for mobile context menus

#### `useMarkdown.js` — Markdown rendering
- Renders markdown via `marked` + sanitizes with `DOMPurify`

#### `useMermaid.js` — Mermaid diagram rendering
- Detects and renders Mermaid code blocks in agent messages

#### `useSessionState.js` — Session state colors
- Exports `STATE_COLOR_MAP` for session display states and shared colors for session status visualization

### Notification & Media Composables (2)

#### `useNotifications.js` — Sound/browser notifications
- Plays audio cues (permission, completion, error) and fires browser notifications

#### `useTTSReadAloud.js` — Text-to-speech / read-aloud
- Web Speech API integration with voice selection and queue management

### Resource Composable (1)

#### `useResourceImages.js` — Resource image helpers
- Resolves resource URLs, handles image load errors, provides thumbnail logic

### Settings / Config Composables (7)

#### `fieldResetSentinel.js` — Field reset sentinel
- Exports `FIELD_RESET` sentinel object for marking fields to be reset to inherited/default values in drafts

#### `useEditSectionFieldStates.js` — Per-field source states for edit sections
- Computes per-field source states (S|T|P|EMPTY) for template/profile/session edit sections to drive SourceMarker badges

#### `useEditSectionReset.js` — Reset field to inherited value
- Stages "reset field to inherited/default" operations in drafts for template/profile edit sections

#### `useFieldState.js` — Single-field resolution metadata
- Per-field resolution metadata for the 3-tier config chain (session > template > profile > defaults)

#### `useFieldStates.js` — Section-level source-marker computation
- Computes source-marker state for every field in a settings section across session/template/profile layers

#### `useProfileSelector.js` — Profile selector dropdown logic
- Shared logic for profile selector dropdowns with area-scoped profile listing and updating

#### `useScheduleSectionSave.js` — Schedule section save
- Async save function that performs dual-PATCH for ephemeral schedule sections and bound agent session

## Utils (14)

#### `api.js` — HTTP client
- **Functions**: `apiGet()`, `apiPost()`, `apiPut()`, `apiDelete()`, `apiPatch()`
- **System**: `getGitStatus()`, `restartServer()`
- **Error handling**: Custom `APIError` class with status and data
- **Usage**: `import { api } from '@/utils/api'` → `api.get('/api/sessions')`

#### `time.js` — Timestamp formatting
- **Functions**: `parseTimestamp()`, `formatTimestamp()`, `formatFullTimestamp()`, `getRelativeTime()`
- Handles Unix seconds, milliseconds, and ISO strings

#### `toolSummary.js` — Tool description generation
- **Functions**: `generateToolSummary(toolCall, status)`, `generateShortToolSummary(toolCall)`
- **Helpers**: `getBasename()`, `extractBashCommand()`, `truncateBashCommand()`, `getExitCode()`, `countDiffLines()`
- Custom formatting for 22+ tools

#### `fileTypes.js` — File type detection
- Extension-to-MIME mapping, icon selection, text/binary classification

#### `templateVariables.js` — Template variable substitution
- Resolves `{{variable}}` placeholders in session prompts and names

#### `agentSort.js` — Agent sorting helpers
- Agent comparison functions for alphabetical and creation-order sorting with numeric-aware locale comparison

#### `analytics.js` — Analytics utility helpers
- CSS variable reading, bucket-size selection, and time range preset handling for the analytics view

#### `auditColors.js` — Audit event type colors
- Audit event type definitions and helper functions for mapping event types to Bootstrap CSS classes

#### `configFields.js` — Configuration field definitions
- Mirrors backend `CONFIG_FIELDS` with field definitions and profile areas; single source of truth for frontend field resolution

#### `diffRender.js` — Diff rendering
- Builds parsed diff lines from old/new strings using the diff library; returns added/removed counts and structured line objects

#### `hierarchyUtils.js` — Tree traversal utilities
- Depth-first traversal utilities for walking and flattening hierarchical node structures

#### `profileAreas.js` — Profile area key constants
- Profile area key constants (model, permissions, system_prompt, mcp, isolation, features); mirrors backend `PROFILE_AREAS`

#### `sourceCascade.js` — Config cascade resolution
- Resolves the source of a field value in the S→T→P→EMPTY cascade (Session > Template > Profile > Empty)

#### `toolConstants.js` — Tool-related constants
- Constants for common tools, denied tools, and tool categorization

## Component Organization

New components go into the existing matching folder below; add a new folder row here only when introducing a genuinely new functional area, not for every new file.

| Folder | Purpose |
|---|---|
| `analytics/` | Analytics dashboard: filters, time-series chart, summary cards, session table |
| `audit/` | Audit log view: stream/turns tabs, per-event-type row rendering |
| `common/` | Shared building blocks: banners, buttons, folder browser, full-screen diff/resource/mermaid viewers |
| `configuration/` | Session/app configuration tabs (MCP, features, providers, notifications, secrets, pricing) |
| `configuration/fields/` | Reusable field widgets (toggle, text, range, tag input, etc.) consumed by `FieldRenderer` |
| `configuration/providers/` | LiteLLM provider params editor and provider status/pending-restart cards |
| `layout/` | App chrome: project/agent navigation strip (`ProjectPillBar` → `AgentStrip` → `AgentChip`/`StackedChip`, with `PeekCard` hover preview), header row, right sidebar shell, restart/deleted-agent modals |
| `legion/` | Multi-agent minion tree navigation and minion detail modal |
| `messages/` | Message list and per-type message rendering (user/assistant/system/thinking), input area, attachments, timeline banners |
| `messages/tools/` | Activity timeline: per-message tool-call nodes, expansion detail, inline permission prompt, hook execution list |
| `project/` | Project overview view and create/edit dialogs |
| `session/` | Chat interface container, session info/manage dialogs, per-session MCP server detail |
| `settings/` | Settings editor shell: layout, sidebar navigation, toolbar, dirty-guard modal, field source markers |
| `settings/sections/` | Per-area settings sections rendered within `SettingsLayout` (General, Model, System Prompt, Tools/Permissions, Isolation, Features, MCP, Schedule, App-wide and Library sections) |
| `statusbar/` | Session state/processing indicator and API rate limit badge |
| `tasks/` | Right sidebar panels: SDK task list, git diff summary, edit history, proxy access log, resource gallery, message queue |
| `tools/` | Per-tool-type rendering handlers (Read/Edit/Bash/Agent/etc.) — see [TOOL_HANDLERS.md](../TOOL_HANDLERS.md) |

## Naming Conventions

- **camelCase**: Variables, functions, computed properties, store actions
- **PascalCase**: Component names, component file names
- **kebab-case**: CSS classes
- **UPPER_SNAKE**: Constants

## Development Workflow

```bash
# Terminal 1: Backend
uv run python main.py --host 0.0.0.0 --port 8001 --debug-all

# Terminal 2: Frontend dev server with HMR
cd frontend && npm run dev   # http://localhost:5173
```

Production build: `cd frontend && npm run build` → output in `frontend/dist/`
