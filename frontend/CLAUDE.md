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
| Components | 151 `.vue` files |
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
│   │   ├── analytics/     (6)     # AnalyticsView, filters, time-series chart, session table, summary cards
│   │   ├── audit/         (8)     # AuditView, AuditStreamTab, AuditTurnsTab, row components
│   │   ├── common/        (9)     # AlertBanner, AttachmentChip, AuthPrompt, CopyButton, DiffFullView, etc.
│   │   ├── configuration/ (9)     # McpConfigTab, McpServerPanel, FeaturesTab, ProvidersTab, SecretsTab, etc.
│   │   ├── configuration/fields/ (14)  # FieldRenderer, widget components (Toggle, Text, Range, etc.)
│   │   ├── configuration/providers/ (3) # LiteLLMParamsEditor, ProviderPendingBanner, ProviderStatusCard
│   │   ├── layout/        (13)    # Navigation: ProjectPillBar, AgentStrip, AgentChip, DeletedAgentsModal, etc.
│   │   ├── legion/        (2)     # MinionTreeNode, MinionViewModal
│   │   ├── messages/      (13)    # MessageList, MessageItem, InputArea, SubagentTimeline, TruncationBanner, etc.
│   │   ├── messages/tools/ (5)    # ActivityTimeline, PermissionPrompt, TimelineNode/Detail/Segment
│   │   ├── project/       (3)     # ProjectOverview, ProjectCreateModal, ProjectEditModal
│   │   ├── session/       (7)     # SessionView, SessionCostBadge, McpServerDetail, modals, etc.
│   │   ├── settings/      (10)    # SettingsLayout, SettingsSidebar, SettingsToolbar, SourceMarker, etc.
│   │   ├── settings/sections/ (18) # Per-area settings sections (General, Model, MCP, Isolation, etc.)
│   │   ├── statusbar/     (2)     # SessionStatusBar, RateLimitBadge
│   │   ├── tasks/         (7)     # TaskListPanel, DiffPanel, EditHistoryPanel, ProxyPanel, ResourceGallery, etc.
│   │   └── tools/         (22)    # Tool handlers: Read, Edit, Bash, Agent, SendComm, Task*, Skill, etc.
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

### Analytics (`analytics/`) — 6 components

| Component | Purpose |
|-----------|---------|
| `AnalyticsView` | Top-level analytics page container |
| `AnalyticsFilters` | Time range and filter controls |
| `AnalyticsTimeSeriesChart` | Token/cost time-series chart |
| `AnalyticsSummaryCards` | Aggregate metric cards |
| `AnalyticsSessionTable` | Per-session usage breakdown table |
| `AnalyticsEmptyState` | Empty state when no analytics data |

### Audit (`audit/`) — 8 components

| Component | Purpose |
|-----------|---------|
| `AuditView` | Top-level audit log page container |
| `AuditFilterBar` | Filter controls (time, session, project, event type) |
| `AuditStreamTab` | Raw event stream tab |
| `AuditTurnsTab` | Conversation turns tab |
| `TurnCard` | Individual conversation turn card |
| `CommRow` | Communication event row |
| `EventRow` | Generic audit event row |
| `LifecycleRow` | Session lifecycle event row |

### Common (`common/`) — 9 components

| Component | Purpose |
|-----------|---------|
| `AlertBanner` | Dismissible alert/notification banner |
| `AttachmentChip` | File attachment chip/badge |
| `AuthPrompt` | Authentication token entry prompt |
| `CopyButton` | Click-to-copy button with feedback |
| `DiffFullView` | Full-screen diff viewer |
| `ExportPdfButton` | PDF export trigger button |
| `FolderBrowserModal` | Directory selection dialog |
| `MermaidFullView` | Full-screen Mermaid diagram viewer |
| `ResourceFullView` | Full-screen resource viewer |

### Configuration (`configuration/`) — 9 components

| Component | Purpose |
|-----------|---------|
| `FeaturesTab` | Feature flags and experimental options |
| `McpConfigTab` | MCP server list and management |
| `McpServerPanel` | Per-server configuration panel |
| `McpServerRow` | Individual server row in list |
| `NotificationsTab` | Sound/browser notification preferences |
| `PricingTab` | Model pricing reference |
| `ProvidersTab` | LiteLLM provider configuration |
| `ReadAloudTab` | TTS voice selection and settings |
| `SecretsTab` | Secrets vault management |

### Configuration Fields (`configuration/fields/`) — 14 components

Reusable field widgets consumed by `FieldRenderer` in settings sections:

| Component | Purpose |
|-----------|---------|
| `FieldRenderer` | Dispatches field type to appropriate widget |
| `FieldSection` | Grouped section container with heading |
| `ButtonGroupWidget` | Segmented button group for enum fields |
| `DirListWidget` | Editable directory path list |
| `MultiSelectField` | Multi-value checkbox/tag selector |
| `ProviderModelSelectWidget` | Provider + model cascaded selector |
| `ProviderSelectWidget` | Provider-only selector |
| `RangeSliderWidget` | Numeric range slider |
| `SandboxSubSectionWidget` | Sandbox isolation sub-section |
| `TagInputWidget` | Free-text tag input |
| `TagListField` | Read-only tag list display |
| `TextInputWidget` | Single-line text input |
| `TextareaWidget` | Multi-line textarea |
| `ToggleWidget` | Boolean toggle switch |

### Configuration Providers (`configuration/providers/`) — 3 components

| Component | Purpose |
|-----------|---------|
| `LiteLLMParamsEditor` | LiteLLM extra params key-value editor |
| `ProviderPendingBanner` | Pending restart warning banner |
| `ProviderStatusCard` | Provider health and status card |

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

### Legion (`legion/`) — 2 components

| Component | Purpose |
|-----------|---------|
| `MinionTreeNode` | Hierarchical minion tree node |
| `MinionViewModal` | Minion details dialog |

### Messages (`messages/`) — 13 components

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
| `DeferredToolBanner` | Banner for deferred/pending tool calls |
| `SlashCommandDropdown` | Slash command autocomplete |
| `SubagentTimeline` | Nested subagent activity display |
| `TruncationBanner` | Context truncation warning banner |

### Activity Timeline (`messages/tools/`) — 5 components

Horizontal timeline showing tool calls within an assistant message:

| Component | Purpose |
|-----------|---------|
| `ActivityTimeline` | Container: renders nodes + segments, manages expansion |
| `PermissionPrompt` | Inline permission request UI within timeline |
| `TimelineNode` | Circular dot per tool with status color and pulse animations |
| `TimelineDetail` | Expanded detail panel with tool handler |
| `TimelineSegment` | Gradient connecting line between nodes |

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

### Project (`project/`) — 3 components

| Component | Purpose |
|-----------|---------|
| `ProjectOverview` | Project details view |
| `ProjectCreateModal` | New project dialog |
| `ProjectEditModal` | Edit/delete project dialog |

### Session (`session/`) — 7 components

| Component | Purpose |
|-----------|---------|
| `SessionView` | Main chat interface container |
| `SessionCostBadge` | Session API cost display badge |
| `SessionStateStatusLine` | Session state indicator |
| `SessionInfoModal` | Session details dialog |
| `SessionManageModal` | Restart/reset/delete actions |
| `McpServerDetail` | Per-session MCP server detail view |
| `NoSessionSelected` | Landing page placeholder |

### Settings (`settings/`) — 10 components

Top-level settings editor shell (SettingsLayout routes to per-area sections in `settings/sections/`):

| Component | Purpose |
|-----------|---------|
| `SettingsLayout` | Settings page container with sidebar and toolbar |
| `SettingsBreadcrumb` | Breadcrumb navigation within settings |
| `SettingsSidebar` | Left sidebar with area navigation |
| `SettingsSidebarGroup` | Collapsible sidebar group |
| `SettingsSidebarItem` | Individual sidebar navigation item |
| `SettingsSidebarSearch` | Sidebar search filter |
| `SettingsToolbar` | Top toolbar with save/cancel actions |
| `SettingsToolbarChip` | Chip component in toolbar (profile/template selector) |
| `DirtyGuardModal` | Unsaved changes confirmation dialog |
| `SourceMarker` | Field source badge (S/T/P indicator) |

### Settings Sections (`settings/sections/`) — 18 components

Each component renders a specific configuration area within `SettingsLayout`:

| Component | Area |
|-----------|------|
| `GeneralSection` | Session general settings (name, cwd, model) |
| `ModelTuningSection` | Model parameters (temperature, thinking, drop_params) |
| `SystemPromptSection` | System prompt configuration |
| `ToolsPermissionsSection` | Allowed tools and permission mode |
| `IsolationSection` | Sandbox isolation settings |
| `FeaturesSection` | Session feature flags |
| `McpServersSection` | Per-session MCP server list |
| `ScheduleGeneralSection` | Schedule general settings |
| `ApplicationFeaturesSection` | App-wide feature flags |
| `ApplicationNotifsSection` | App-wide notification settings |
| `ApplicationPricingSection` | App pricing configuration |
| `ApplicationReadAloudSection` | App TTS settings |
| `LibraryMcpServersSection` | Global MCP server library |
| `LibraryProfilesSection` | Profile library management |
| `LibraryProvidersSection` | Provider library management |
| `LibrarySchedulesSection` | Schedule template library |
| `LibrarySecretsSection` | Secrets library management |
| `LibraryTemplatesSection` | Session template library |

### Status Bar (`statusbar/`) — 2 components

| Component | Purpose |
|-----------|---------|
| `SessionStatusBar` | Session state and processing indicator |
| `RateLimitBadge` | API rate limit indicator |

### Right Sidebar Panels (`tasks/`) — 7 components

| Component | Purpose |
|-----------|---------|
| `TaskListPanel` | SDK task list with status tracking |
| `TaskItem` | Individual task with status icon |
| `DiffPanel` | Git diff summary with file list |
| `EditHistoryPanel` | Per-session edit history panel |
| `ProxyPanel` | Proxy access log and credential vault panel |
| `ResourceGallery` | Resource thumbnails and file icons |
| `QueueSection` | Message queue display |

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
