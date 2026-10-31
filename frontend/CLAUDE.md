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
| Utils | 16 |

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
- **State**: `messagesBySession` (Map), `toolCallsBySession` (Map), `permissionToToolMap` (Map)
- **Key actions**: `loadMessages()`, `applyRecord(sessionId, record, source)` (issue #2110 stage 4b-B: the one entry point for every message/tool_call record, `source` ∈ `'live'`/`'load'`/`'archive'`), `handlePermissionResponse()`, `toggleToolExpansion()`, `setArchiveMessages()`
- **Features**: Orphaned-tool status (`_isOrphaned`/`_orphanedInfo`) sourced directly from the backend's own `interrupted` tool_call record — no browser-side restart/interrupt sweep, deduplication on reconnect
- **Tail-first history (issue #2030)**: sessions and archives open on the newest turn-snapped window (`loadMessages(id, null, 0, {window: 'tail'})` → `loadHistoryTail`); older pages (`fetchOlderPage` / `applyFetchedOlderPage` → `applyOlderPage`, merge in `utils/historyMerge.js`) are prepended under the loaded suffix by the backfill scheduler (`composables/useHistoryBackfill.js`). `isHistoryComplete(id)` gates the banner/backfill scheduler (no longer search or the leg-signal filter, which is scoped per loaded interval: while the history is partial, `composables/useMessageSearch.js` searches the WHOLE history on the server — `searchServer` in `search/messageSearch.js`, `GET .../search`, newest-first pages, a per-tab `client_id` on every request, "N+"/"Load more results", a search hit that is not loaded opens through the same `loadAround(cursor)` window; 404/503/network failure/repeated 409 fall back to `findMatches` with the "loaded only" indicator; with the history complete the search stays local); the server history index (`ensureHistoryIndex` → `GET .../history_index`, `historyIndexBySession`, dropped whenever the loaded suffix is replaced) feeds turn navigation, so Prev works on a tail-only load (a target that is not loaded opens ONE bounded `around=` window through `historyBackfill.jumpAround` → `loadAround`, see the interval model below; only a slow link confirms, naming the ~0.5 MB window — `utils/turnNavigation.js`), and compaction groups are keyed by their first record's `message_id` (`groupKey`; PreCompact hooks via `hooksForCompaction(id, groupKey)`); `historyProgress(id)` drives the list banner. **Interval model (issue #2030 stage 3 PR 3C-1)**: the loaded history is the live SUFFIX (the ordinary per-session message/tool state, byte-for-byte the hot path) plus at most ONE pinned ISLAND — a window fetched by `loadAround(sessionId, {cursor|messageId|toolUseId})` (`GET .../messages?around=|around_id=`; one bounded fetch, ~0.5 MB) for turn navigation / search hits, held in its own record target (`islandBySession`, `getIsland`). A new jump replaces the island (its exclusively-owned messages/tools go; tools the suffix already holds stay the suffix's, `_shareIslandTools`); the island grows by `before=` pages (scrolling near the top of the topmost interval) and `after=` pages ("Load newer" on the gap row, bounded by `until=<suffix start>`), and merges into the suffix as if it were an older backfill page (`mergeOlderPage`) when the two touch (`utils/historyIntervals.js`: offset compare, `has_more_after`, id overlap for opaque cursors). `loadedMessages/loadedToolCalls/findLoadedToolCall` are the union getters (they return the suffix arrays by identity when there is no island); `utils/loadedRows.js` builds the rows (the pipeline runs per interval with a `gap` row between, `HistoryGapRow.vue`; leg signals are scoped to each interval's own time span). A resync keeps the island while `history_id` is unchanged; a changed `history_id` drops it. A leg whose launch row is not loaded (while the history is partial) keeps a lane in `MessageList.vue`'s `laneOffsets`, clamped to the top of the first loaded interval whose time span overlaps the leg's `[started_at, ended_at|now]` (`launchedEarlier` on the lane offset; none when the leg lies wholly outside the loaded spans), and `SubagentGlobalGutter.vue` draws it as a dashed "launched earlier" chip; clicking it runs `historyBackfill.jumpAround(id, {toolUseId})` (`around_id=<tool_use_id>`, slow-link confirm naming the window size, failures and `parent_unresolved` in the history banner) and the lane becomes the ordinary one once the launch row is loaded (the launch card is scrolled into view; two or more stacked chips use a 46 px pitch so their 44x44 hit areas never overlap, at most five are drawn with a `+k` count, and clamped lanes / lanes spanning the gap row are drawn as a dashed line). The island is capped at 64 MiB of history-file bytes (`ISLAND_MAX_BYTES`, interim until the stage 4 eviction): paging stops, the edge indicators stay. The island's records never overwrite the suffix's latest-wins state (stop reason, launch time): they apply on merge only where nothing newer set one. History replay never touches the task store — the Tasks panel hydrates from `GET /api/sessions/{id}/tasks`

#### `polling.js` — HTTP long-polling
- **State** (Epic #1990 stage 5c: ONE shared `StreamConnection` for the whole multiplexed
  transport — states `idle | connecting | connected | resyncing | backoff | unreachable |
  archived`): `uiState`/`uiRetryCount`/`uiBackendStatus` and `sessionState`/`sessionRetryCount`/
  `sessionBackendStatus` (same underlying connection, exposed under both names for API-shape
  compatibility), `sessionHasEverConnected` (per-session now, not per-connection), a
  `subscriptions` Map (sessionId -> `{cursor, hasEverSeenResponse}`) LRU-bounded to
  `MAX_SUBSCRIBED_SESSIONS` (5) replacing the old passive `sessionCursors` cache — every
  subscribed session is now ACTIVELY polled, not just cached
- **Key actions**: `startUIPolling()`, `connectSession(sessionId)`, `markSessionArchived(sessionId)`, `sendMessage()`, `sendPermissionResponse()`, `sendPermissionResponseWithInput()`, `interruptSession()`
- **Features**: one multiplexed `POST /api/poll` request per cycle (UI stream plus the current
  subscription set), cursor-based incremental polling per stream, exponential backoff (up to
  30s), liveness derived only from poll responses/fetch outcomes (no wall-clock staleness
  timers). A background (recently-viewed, not current) subscribed session's events are applied
  live via `message.js`'s `applyRecord()` — switching back shows everything that happened while
  it was backgrounded, with no reload/resync.

#### `legion.js` — Multi-agent data
- **State**: `commsByLegion` (Map), `minionsByLegion` (Map), `currentLegionId`
- **Key actions**: `addComm()`, `sendComm()`, `loadMinions()`, `createMinion()`, `haltAll()`, `resumeAll()`

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
- **Key actions**: `createTask()`, `updateTask()`, `clearTasks()`, `handleTaskToolResult()` (live records only; buffered while a snapshot is in flight), `hydrate()` (server snapshot), `beginHydration()`
- **Helpers**: `tasksForSession()`, `activeTask()`, `taskStats()`

#### `backgroundTasks.js` — Running background tasks per session (issue #2145)
- **State**: `tasksBySession` (Map of latest snapshot `[{task_id, task_type, description}]`)
- **Key actions**: `setTasks()` (replace), `clear()`, `hydrate()` (GET `/background_tasks`); fed by live `background_tasks_changed` only (never history), cleared on client_launched/session_failed/terminated/error. Rendered by `BackgroundTasksSection` in the Tasks panel; bubbles are hidden in `MessageList`.

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

#### `useHistoryBackfill.js` — Older-history backfill scheduler (issue #2030)
- Paced page-by-page backfill behind the tail window: fixed page budget, gap `max(500 ms, 2× last page)`, paused while the connection is not `connected`, during live catch-up / activity, hidden tab, or a user REST call; slow-link mode (saveData / 2g / measured < 100 KiB/s) is on-demand only
- **Jumps (stage 3 PR 3C-1)**: `jumpAround(id, target, {label})` fetches ONE bounded `around=` window as the island (user-driven: no pacing pauses, works in slow-link mode, `{promise, cancel}`, rejects with `err.reason` `not-found|busy|reset|aborted|superseded|failed`); `requestIslandOlder/requestIslandNewer(id)` grow it one page on demand; `cancelJump`, `getMode`; `scheduleIndex` requests the history index after the window + snapshots (not on a slow link). The stage 2 `requestUntil` extend-suffix jump and its >2 MiB confirm are gone
- **Older-page view hook (issue #2320 follow-up)**: `setPageApplyHook(id, fn)` / `clearPageApplyHook(id, fn)` — `MessageList.vue` registers a function the scheduler calls right before an OLDER page is applied (suffix backfill page or island `before` page); its return value is called right after. The list uses it to keep the viewport on the same message (`utils/prependAnchor.js`); a throwing hook never fails a page
- **Exports**: `historyBackfill` (`start`, `stop`, `requestOlder`, `jumpAround`, `requestIslandOlder`, `requestIslandNewer`, `cancelJump`, `scheduleIndex`, `getMode`, `loadAll`, `retry`, `setScrollInteraction`, `setPageApplyHook`, `clearPageApplyHook`, reactive `statusBySession`), `createHistoryBackfill(deps)` for tests

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

## Utils (16)

#### `api.js` — HTTP client
- **Functions**: `apiGet()`, `apiPost()`, `apiPut()`, `apiDelete()`, `apiPatch()`
- **System**: `getGitStatus()`, `restartServer()`
- **Error handling**: Custom `APIError` class with status and data
- **Usage**: `import { api } from '@/utils/api'` → `api.get('/api/sessions')`

#### `displayableItems.js` — MessageList row pipeline (issue #2030)
- **Functions**: `buildDisplayableItems(ctx)` (pure 7-pass pipeline extracted from `MessageList.vue`; the caller builds `ctx` inside its computed so store reads stay reactive), `getItemKey(item, index)` (stable virtualizer row key)

#### `historyIntervals.js` / `loadedRows.js` — Interval model helpers (issue #2030 stage 3 PR 3C-1)
- `historyIntervals.js`: `intervalsTouch`, `gapBetween`, `windowBytes`, `islandIsFull`, `ISLAND_MAX_BYTES`, `formatHistorySize`; `loadedRows.js`: `buildLoadedRows(store, sessionId)` (plain pipeline without an island, per-interval pipeline + `gap` row with one); `turnNavigation.js`: `planTurnStep` (Prev/Next over the loaded intervals + the history index), `findTurnBefore/AtOrAfter`

#### `virtualScroll.js` / `prependAnchor.js` — Message-list scroll corrections (issues #2320)
- `virtualScroll.js`: `scrollWithSizerGuard` (the virtualizer's `scrollToFn`: a write past the reachable range waits for the spacer to grow), `shouldAdjustForItemSizeChange` (assigned to the virtualizer INSTANCE: re-measured rows wholly above the fold are compensated whatever the scroll direction); `prependAnchor.js`: `captureMessageAnchor` / `anchorCorrection` (keep the viewport on the same message across an older-page prepend)

#### `historyMerge.js` — Prepend-merge for tail-first history (issue #2030)
- **Functions**: `mergeOlderPage(current, older)`, `mergeToolCallFields(current, older)` (newer entry wins field-by-field; an older record only fills what is absent)

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
