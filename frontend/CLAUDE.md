# Frontend Architecture — Vue 3 + Pinia + Vite

Agent-oriented guide to the Claude WebUI frontend. For backend architecture, see the root [CLAUDE.md](../CLAUDE.md). For tool handler development, see [TOOL_HANDLERS.md](../TOOL_HANDLERS.md).

## Quick Reference

| Metric | Value |
|--------|-------|
| Framework | Vue 3.4 + Composition API |
| State | Pinia 2.1 (12 stores) |
| Build | Vite 5.2 |
| Router | Vue Router 4 (hash history) |
| CSS | Bootstrap 5.3 + scoped component styles |
| Components | 85+ `.vue` files |
| Composables | 3 |
| Utils | 3 |

## Directory Structure

```
frontend/
├── src/
│   ├── main.js                    # App entry: createApp, Pinia, Router
│   ├── App.vue                    # Root component
│   ├── router/index.js            # 4 routes (home, project, session, timeline)
│   ├── stores/                    # 12 Pinia stores
│   ├── composables/               # 3 reusable composition functions
│   ├── utils/                     # 3 utility modules
│   ├── components/
│   │   ├── layout/        (12)    # Navigation: ProjectPillBar, AgentStrip, AgentChip, etc.
│   │   ├── configuration/ (6)     # ConfigurationModal + tabs + PermissionPreview
│   │   ├── project/       (4)     # ProjectOverview, ProjectCreateModal, etc.
│   │   ├── session/       (7)     # SessionView, SessionInfoBar, modals, etc.
│   │   ├── messages/      (7)     # MessageList, MessageItem, InputArea, etc.
│   │   ├── messages/tools/ (5)    # ActivityTimeline, TimelineNode/Detail/Segment/Overflow
│   │   ├── tools/         (21)    # Tool handlers: Read, Edit, Bash, Task*, Skill, etc.
│   │   ├── legion/        (4)     # TimelineView, CommComposer, MinionTreeNode, etc.
│   │   ├── header/        (1)     # TimelineHeader
│   │   ├── statusbar/     (2)     # SessionStatusBar, TimelineStatusBar
│   │   ├── schedules/     (3)     # SchedulePanel, ScheduleItem, ScheduleCreateModal
│   │   ├── tasks/         (7)     # TaskListPanel, TaskItem, DiffPanel, ResourceGallery, etc.
│   │   └── common/        (4)     # FolderBrowserModal, CommCard, DiffFullView, ResourceFullView
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
| `/timeline/:legionId` | `TimelineView` | Multi-agent timeline |

## Pinia Stores (12)

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

#### `websocket.js` — 3 WebSocket connections
- **Connections**: UI (global state), Session (message streaming), Legion (comms)
- **Key actions**: `connectUI()`, `connectSession()`, `connectLegion()`, `sendMessage()`, `sendPermissionResponse()`, `sendPermissionResponseWithInput()`, `interruptSession()`
- **Features**: Heartbeat monitoring (10s timeout), exponential backoff reconnection, generation tracking to prevent stale connections

#### `legion.js` — Multi-agent data
- **State**: `commsByLegion` (Map), `minionsByLegion` (Map), `currentLegionId`
- **Key actions**: `loadTimeline()` (paginated), `addComm()`, `sendComm()`, `loadMinions()`, `createMinion()`, `haltAll()`, `resumeAll()`

#### `ui.js` — UI state & persistence
- **State**: `rightSidebarCollapsed`, `rightSidebarWidth`, `rightSidebarActiveTab`, `browsingProjectId`, `expandedStacks` (Set), `autoScrollEnabled`, `isRedBackground`, `activeModal`, `restartInProgress`
- **Persistence**: localStorage with `webui-sidebar-` prefix
- **Key actions**: `toggleRightSidebar()`, `setBrowsingProject()`, `toggleStack()`, `showModal()`, `hideModal()`, `showRestartModal()`

### Specialized Stores (6)

#### `queue.js` — Message queue per session
- **State**: `queuesBySession` (Map), `pausedBySession` (Map)
- **Key actions**: `fetchQueue()`, `enqueueMessage()`, `cancelItem()`, `requeueItem()`, `clearQueue()`, `pauseQueue()`, `handleQueueUpdate()`

#### `schedule.js` — Cron schedules per legion
- **State**: `schedulesByLegion` (Map), `scheduleCountByMinion` (Map), `selectedScheduleId`, `executionHistory`
- **Key actions**: `loadSchedules()`, `createSchedule()`, `updateSchedule()`, `pauseSchedule()`, `resumeSchedule()`, `cancelSchedule()`, `deleteSchedule()`, `loadHistory()`, `handleScheduleEvent()`

#### `resource.js` — Resource gallery per session
- **State**: `resourcesBySession` (Map), `fullViewOpen`, `currentResourceIndex`, `textContentCache` (Map)
- **Key actions**: `loadResources()`, `addResource()`, `removeResource()`, `openFullView()`, `fetchTextContent()`
- **Helpers**: `isImageResource()`, `isTextResource()`, `getResourceIcon()`, `getResourceUrl()`
- **Note**: Backward-compatible aliases for legacy `useImageStore`

#### `diff.js` — Git diff per session
- **State**: `diffBySession` (Map), `currentMode` ('total'|'commits'), `fullViewOpen`, `fileDiffCache` (Map)
- **Key actions**: `loadDiff()`, `refreshDiff()`, `loadFileDiff()`, `openFullView()`, `setMode()`

#### `task.js` — SDK task tracking per session
- **State**: `tasksBySession` (Map), `sessionsWithTasks` (Set)
- **Key actions**: `createTask()`, `updateTask()`, `clearTasks()`, `reconstructFromMessages()`, `handleTaskToolResult()`
- **Helpers**: `tasksForSession()`, `activeTask()`, `taskStats()`

#### `image.js` — Deprecated shim
- Re-exports `useResourceStore` for backward compatibility. All functionality lives in `resource.js`.

## Composables (3)

#### `useToolResult.js` — Tool result extraction
- **Input**: `toolCallRef` (reactive)
- **Returns**: `hasResult`, `isError`, `resultContent`, `formattedInput`
- **Used by**: Most tool handler components

#### `useToolStatus.js` — Tool status computation
- **Input**: `toolRef` (reactive)
- **Returns**: `effectiveStatus`, `isOrphaned`, `orphanedInfo`, `statusColor`, `hasError`
- **Helpers**: `getEffectiveStatusForTool()`, `getColorForStatus()` (non-reactive versions)
- **Used by**: TimelineNode, ToolCallCard, tool handlers

#### `useWebSocket.js` — Generic WebSocket hook
- **Input**: URL + options (maxRetries, autoReconnect, callbacks)
- **Returns**: `socket`, `connected`, `retryCount`, `connect()`, `disconnect()`, `send()`
- **Note**: Main WebSocket connections use the WebSocket store; this composable is for custom one-off connections

## Utils (3)

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
- Custom formatting for 20+ tools

## Component Organization

### Layout (`layout/`) — 12 components
Navigation architecture follows a horizontal strip pattern:

```
ProjectPillBar → AgentStrip → AgentChip / StackedChip
                                    ↕
                              PeekCard (hover preview)
```

| Component | Purpose |
|-----------|---------|
| `ProjectPillBar` | Horizontal bar of project pills |
| `ProjectPill` | Individual project tab |
| `AgentStrip` | Horizontal strip of agent/session chips within a project |
| `AgentChip` | Individual session chip |
| `StackedChip` | Collapsed group of child sessions |
| `ChipConnector` | Visual connector between chips |
| `HeaderRow1` | Top-level header row |
| `AgentOverview` | Agent summary panel |
| `PeekCard` | Hover preview card for sessions |
| `ConnectionIndicator` | WebSocket connection status |
| `RightSidebar` | Tabbed right panel (Diff, Tasks, Resources, Comms, Queue, Schedules) |
| `RestartModal` | Server restart confirmation |

### Configuration (`configuration/`) — 6 components

| Component | Purpose |
|-----------|---------|
| `ConfigurationModal` | Main configuration dialog with tabs |
| `GeneralTab` | General settings (name, model, tools) |
| `PermissionsTab` | Permission mode and allowed tools |
| `AdvancedTab` | Advanced session options |
| `SandboxTab` | Sandbox mode configuration |
| `PermissionPreviewModal` | Preview effective permissions from settings files |

### Messages (`messages/`) — 7 components

| Component | Purpose |
|-----------|---------|
| `MessageList` | Auto-scrolling message container |
| `MessageItem` | Router to message type components |
| `UserMessage` | User message display |
| `AssistantMessage` | Assistant response with tool timeline |
| `SystemMessage` | System/status messages |
| `ThinkingBlock` | Claude thinking block display |
| `InputArea` | Message textarea with send/interrupt buttons |
| `AttachmentList` | File attachment display |
| `CompactionEventGroup` | Context compaction indicator |
| `SlashCommandDropdown` | Slash command autocomplete |

### Activity Timeline (`messages/tools/`) — 5 components

Horizontal timeline showing tool calls within an assistant message:

| Component | Purpose |
|-----------|---------|
| `ActivityTimeline` | Container: renders nodes + segments, manages expansion |
| `TimelineNode` | Circular dot per tool with status color and pulse animations |
| `TimelineDetail` | Expanded detail panel with tool handler + permission UI |
| `TimelineSegment` | Gradient connecting line between nodes |
| `TimelineOverflow` | "+N" pill for collapsed earlier tools |

### Tool Handlers (`tools/`) — 21 components

See [TOOL_HANDLERS.md](../TOOL_HANDLERS.md) for detailed documentation.

**File operations**: `ReadToolHandler`, `EditToolHandler`, `WriteToolHandler`
**Shell**: `BashToolHandler`, `ShellToolHandler`, `CommandToolHandler`
**Search**: `SearchToolHandler` (Grep/Glob)
**Web**: `WebToolHandler` (WebFetch/WebSearch)
**Task management**: `TodoToolHandler`, `TaskToolHandler`, `TaskCreateToolHandler`, `TaskGetToolHandler`, `TaskListToolHandler`, `TaskUpdateToolHandler`
**Interactive**: `AskUserQuestionToolHandler`
**Skills**: `SkillToolHandler`, `SlashCommandToolHandler`
**Other**: `ExitPlanModeToolHandler`, `NotebookEditToolHandler`
**Shared**: `ToolSuccessMessage` (success banner), `BaseToolHandler` (fallback)

### Right Sidebar Panels (`tasks/`) — 7 components

| Component | Purpose |
|-----------|---------|
| `TaskListPanel` | SDK task list with status tracking |
| `TaskItem` | Individual task with status icon |
| `DiffPanel` | Git diff summary with file list |
| `ResourceGallery` | Resource thumbnails and file icons |
| `ImageGallery` | Legacy image gallery (deprecated) |
| `CommsPanel` | Legion communications panel |
| `QueueSection` | Message queue display |

### Schedules (`schedules/`) — 3 components

| Component | Purpose |
|-----------|---------|
| `SchedulePanel` | Schedule list for a legion |
| `ScheduleItem` | Individual schedule with status |
| `ScheduleCreateModal` | Create/edit cron schedule |

### Session (`session/`) — 7 components

| Component | Purpose |
|-----------|---------|
| `SessionView` | Main chat interface container |
| `SessionInfoBar` | Session metadata display |
| `SessionStateStatusLine` | Session state indicator |
| `SessionInfoModal` | Session details dialog |
| `SessionManageModal` | Restart/reset/delete actions |
| `NoSessionSelected` | Landing page placeholder |

### Project (`project/`) — 4 components

| Component | Purpose |
|-----------|---------|
| `ProjectOverview` | Project details view |
| `ProjectStatusLine` | Project state summary |
| `ProjectCreateModal` | New project dialog |
| `ProjectEditModal` | Edit/delete project dialog |

### Legion (`legion/`) — 4 components

| Component | Purpose |
|-----------|---------|
| `TimelineView` | Unified comm timeline display |
| `CommComposer` | Send comm to minion |
| `MinionTreeNode` | Hierarchical minion tree |
| `MinionViewModal` | Minion details dialog |

### Common (`common/`) — 4 components

| Component | Purpose |
|-----------|---------|
| `FolderBrowserModal` | Directory selection dialog |
| `CommCard` | Formatted communication card |
| `DiffFullView` | Full-screen diff viewer |
| `ResourceFullView` | Full-screen resource viewer |

## Naming Conventions

- **camelCase**: Variables, functions, computed properties, store actions
- **PascalCase**: Component names, component file names
- **kebab-case**: CSS classes
- **UPPER_SNAKE**: Constants

## Development Workflow

```bash
# Terminal 1: Backend
uv run python main.py --port 8001 --debug-all

# Terminal 2: Frontend dev server with HMR
cd frontend && npm run dev   # http://localhost:5173
```

Production build: `cd frontend && npm run build` → output in `frontend/dist/`
