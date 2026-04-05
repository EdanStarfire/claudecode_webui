# Frontend Architecture — Vue 3 + Pinia + Vite

Agent-oriented guide to the Claude WebUI frontend. For backend architecture, see the root [CLAUDE.md](../CLAUDE.md). For tool handler development, see [TOOL_HANDLERS.md](../TOOL_HANDLERS.md).

## Quick Reference

| Metric | Value |
|--------|-------|
| Framework | Vue 3.4 + Composition API |
| State | Pinia 2.1 (14 stores) |
| Build | Vite 7.1 |
| Router | Vue Router 4 (hash history) |
| CSS | Bootstrap 5.3 + scoped component styles |
| Components | 99+ `.vue` files |
| Composables | 9 |
| Utils | 5 |

## Directory Structure

```
frontend/
├── src/
│   ├── main.js                    # App entry: createApp, Pinia, Router
│   ├── App.vue                    # Root component
│   ├── router/index.js            # 5 routes (home, project, session, session/archive, archive/agent)
│   ├── stores/                    # 14 Pinia stores
│   ├── composables/               # 9 reusable composition functions
│   ├── utils/                     # 5 utility modules
│   ├── components/
│   │   ├── layout/        (13)    # Navigation: ProjectPillBar, AgentStrip, AgentChip, DeletedAgentsModal, etc.
│   │   ├── configuration/ (12)    # ConfigurationModal, McpConfigTab, McpServerPanel, FeaturesTab, ReadAloudTab, etc.
│   │   ├── project/       (4)     # ProjectOverview, ProjectCreateModal, etc.
│   │   ├── session/       (7)     # SessionView, SessionInfoBar, McpServerDetail, modals, etc.
│   │   ├── messages/      (12)    # MessageList, MessageItem, InputArea, SubagentTimeline, TruncationBanner, etc.
│   │   ├── messages/tools/ (6)    # ActivityTimeline, PermissionPrompt, TimelineNode/Detail/Segment/Overflow
│   │   ├── tools/         (22)    # Tool handlers: Read, Edit, Bash, Agent, SendComm, Task*, Skill, etc.
│   │   ├── legion/        (2)     # MinionTreeNode, MinionViewModal
│   │   ├── header/        (1)     # TimelineHeader
│   │   ├── statusbar/     (3)     # SessionStatusBar, TimelineStatusBar, RateLimitBadge
│   │   ├── schedules/     (3)     # SchedulePanel, ScheduleItem, ScheduleCreateModal
│   │   ├── tasks/         (6)     # TaskListPanel, TaskItem, DiffPanel, ResourceGallery, QueueSection, etc.
│   │   └── common/        (6)     # FolderBrowserModal, CommCard, DiffFullView, ResourceFullView, AttachmentChip, AuthPrompt
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

## Pinia Stores (14)

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

#### `websocket.js` — HTTP long-polling (named for legacy reasons)
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

### Specialized Stores (8)

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

#### `mcp.js` — Active MCP server state per session
- Runtime state of active MCP servers, synced from backend

#### `mcpConfig.js` — MCP server configuration CRUD
- Persistent MCP server definitions (STDIO/SSE/HTTP, OAuth 2.1, enable/disable)

#### `image.js` — Deprecated shim
- Re-exports `useResourceStore` for backward compatibility. All functionality lives in `resource.js`.

## Composables (9)

#### `useToolResult.js` — Tool result extraction
- **Input**: `toolCallRef` (reactive)
- **Returns**: `hasResult`, `isError`, `resultContent`, `formattedInput`
- **Used by**: Most tool handler components

#### `useToolStatus.js` — Tool status computation
- **Input**: `toolRef` (reactive)
- **Returns**: `effectiveStatus`, `isOrphaned`, `orphanedInfo`, `statusColor`, `hasError`
- **Helpers**: `getEffectiveStatusForTool()`, `getColorForStatus()` (non-reactive versions)
- **Used by**: TimelineNode, ToolCallCard, tool handlers

#### `useAgentColor.js` — Per-agent color assignment
- Stable deterministic color per agent/session ID for visual differentiation

#### `useLongPress.js` — Long-press gesture handler
- Touch/mouse long-press detection for mobile context menus

#### `useMarkdown.js` — Markdown rendering
- Renders markdown via `marked` + sanitizes with `DOMPurify`

#### `useMermaid.js` — Mermaid diagram rendering
- Detects and renders Mermaid code blocks in agent messages

#### `useNotifications.js` — Sound/browser notifications
- Plays audio cues (permission, completion, error) and fires browser notifications

#### `useResourceImages.js` — Resource image helpers
- Resolves resource URLs, handles image load errors, provides thumbnail logic

#### `useTTSReadAloud.js` — Text-to-speech / read-aloud
- Web Speech API integration with voice selection and queue management

## Utils (5)

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

## Component Organization

### Layout (`layout/`) — 13 components
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
| `ConnectionIndicator` | Poll connection status |
| `RightSidebar` | Tabbed right panel (Diff, Tasks, Resources, Comms, Queue, Schedules) |
| `RestartModal` | Server restart confirmation |
| `DeletedAgentsModal` | Browse and restore archived deleted agents |

### Configuration (`configuration/`) — 12 components

| Component | Purpose |
|-----------|---------|
| `ConfigurationModal` | Main configuration dialog with tabs |
| `GlobalConfigModal` | Global app configuration |
| `QuickSettingsPanel` | Quick-access settings overlay |
| `AdvancedSettingsPanel` | Advanced session options |
| `FeaturesTab` | Feature flags and experimental options |
| `McpConfigTab` | MCP server list and management |
| `McpServerPanel` | Per-server configuration panel |
| `McpServerPicker` | Server selection dropdown |
| `McpServerRow` | Individual server row in list |
| `NotificationsTab` | Sound/browser notification preferences |
| `ReadAloudTab` | TTS voice selection and settings |
| `PermissionPreviewModal` | Preview effective permissions from settings files |

### Messages (`messages/`) — 12 components

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
| `SubagentTimeline` | Nested subagent activity display |
| `TruncationBanner` | Context truncation warning banner |

### Activity Timeline (`messages/tools/`) — 6 components

Horizontal timeline showing tool calls within an assistant message:

| Component | Purpose |
|-----------|---------|
| `ActivityTimeline` | Container: renders nodes + segments, manages expansion |
| `PermissionPrompt` | Inline permission request UI within timeline |
| `TimelineNode` | Circular dot per tool with status color and pulse animations |
| `TimelineDetail` | Expanded detail panel with tool handler |
| `TimelineSegment` | Gradient connecting line between nodes |
| `TimelineOverflow` | "+N" pill for collapsed earlier tools |

### Tool Handlers (`tools/`) — 22 components

See [TOOL_HANDLERS.md](../TOOL_HANDLERS.md) for detailed documentation.

**File operations**: `ReadToolHandler`, `EditToolHandler`, `WriteToolHandler`
**Shell**: `BashToolHandler`, `ShellToolHandler`, `CommandToolHandler`
**Search**: `SearchToolHandler` (Grep/Glob)
**Web**: `WebToolHandler` (WebFetch/WebSearch)
**Task management**: `TodoToolHandler`, `TaskCreateToolHandler`, `TaskGetToolHandler`, `TaskListToolHandler`, `TaskUpdateToolHandler`
**Interactive**: `AskUserQuestionToolHandler`
**Skills**: `SkillToolHandler`, `SlashCommandToolHandler`
**Agent/Comms**: `AgentToolHandler`, `SendCommToolHandler`
**Other**: `ExitPlanModeToolHandler`, `NotebookEditToolHandler`
**Shared**: `ToolSuccessMessage` (success banner), `BaseToolHandler` (fallback)

### Right Sidebar Panels (`tasks/`) — 6 components

| Component | Purpose |
|-----------|---------|
| `TaskListPanel` | SDK task list with status tracking |
| `TaskItem` | Individual task with status icon |
| `DiffPanel` | Git diff summary with file list |
| `ResourceGallery` | Resource thumbnails and file icons |
| `ImageGallery` | Legacy image gallery (deprecated) |
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
| `McpServerDetail` | Per-session MCP server detail view |
| `NoSessionSelected` | Landing page placeholder |

### Project (`project/`) — 4 components

| Component | Purpose |
|-----------|---------|
| `ProjectOverview` | Project details view |
| `ProjectStatusLine` | Project state summary |
| `ProjectCreateModal` | New project dialog |
| `ProjectEditModal` | Edit/delete project dialog |

### Legion (`legion/`) — 2 components

| Component | Purpose |
|-----------|---------|
| `MinionTreeNode` | Hierarchical minion tree node |
| `MinionViewModal` | Minion details dialog |

### Status Bar (`statusbar/`) — 3 components

| Component | Purpose |
|-----------|---------|
| `SessionStatusBar` | Session state and processing indicator |
| `TimelineStatusBar` | Legion timeline status |
| `RateLimitBadge` | API rate limit indicator |

### Common (`common/`) — 6 components

| Component | Purpose |
|-----------|---------|
| `FolderBrowserModal` | Directory selection dialog |
| `CommCard` | Formatted communication card |
| `DiffFullView` | Full-screen diff viewer |
| `ResourceFullView` | Full-screen resource viewer |
| `AttachmentChip` | File attachment chip/badge |
| `AuthPrompt` | Authentication token entry prompt |

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
