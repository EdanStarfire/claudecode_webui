DO NOT SAY THAT THE USER IS CORRECT OR COMPLEMENT THEIR REQUEST. FORMAL, CONCISE COMMUNICATION SHOULD BE THE ONLY COMMENTARY PROVIDED.

# Development Requirements - REQUIRED
1. Server-side code is all in Python using `uv`, using commands like `uv run ...` or `uv add ...` or `uv run pytest ...` for executing, testing, linting, and managing dependencies.
2. **Frontend is Vue 3 + Pinia + Vite** (PRODUCTION): Frontend code is in `frontend/` directory. The `static/` directory has been sunset and should not be referenced for new development.
3. **Code Quality - Ruff Linting** (REQUIRED): All Python code must be linted with Ruff before committing. Run `uv run ruff check --fix src/` on changed files to auto-fix violations. New code must not introduce linting violations.

# High-Level Goal
We are building a web-based interface for Claude Agent SDK that provides:
1. **Single-Agent Mode**: Real-time streaming conversations with rich tool visualization
2. **Multi-Agent Mode (Legion)**: Teams of AI agents (minions) collaborating on complex tasks through structured communication

The SDK's streaming message responses are proxied through WebSockets to a Vue 3 frontend which displays messages, tool executions, permissions, and multi-agent activity.

# Claude Agent SDK Integration - CRITICAL TECHNICAL KNOWLEDGE

## SDK Usage (REQUIRED)
```python
from claude_agent_sdk import query, ClaudeAgentOptions

# Basic streaming conversation
async def main():
    async for message in query(prompt="Create a Python web server"):
        print(message)

# With configuration
options = ClaudeAgentOptions(
    cwd="/path/to/project",
    permission_mode="acceptEdits",
    allowed_tools=["bash", "edit", "read"]
)
async for message in query(prompt="Build the project", options=options):
    process_message(message)
```

## SDK Configuration (CRITICAL)
```python
from claude_agent_sdk import ClaudeAgentOptions

options = ClaudeAgentOptions(
    cwd="/path/to/project",              # Project working directory (NOT working_directory)
    permission_mode="acceptEdits",       # Permission mode (NOT permissions)
    system_prompt={                      # System prompt configuration (preset or custom)
        "type": "preset",
        "preset": "claude_code"          # Use Claude Code preset
    },
    allowed_tools=["bash", "edit", "read"],  # Tool allowlist (NOT tools)
    setting_sources=["user", "project", "local"],  # Settings sources to load
    model="claude-3-5-sonnet-20241022"   # Model selection
)
```

## CRITICAL PARAMETER MAPPING
- Use `cwd` NOT `working_directory`
- Use `permission_mode` NOT `permissions`
- Use `allowed_tools` NOT `tools`
- Use `prompt=message` NOT positional argument in query()
- Always import from `claude_agent_sdk` NOT `claude_code_sdk`
- Use `ClaudeAgentOptions` NOT `ClaudeCodeOptions`

## Permission Mode Behavior (CRITICAL)
- `permission_mode="default"` means "prompt for everything NOT pre-approved"
- Pre-approved tools are defined in `.claude/settings.json` or `.claude/settings.local.json`
- Tools like WebFetch, Edit, Write, etc. require permission prompts unless explicitly pre-approved
- Only tools in the settings file's `permissions.allow` array bypass permission prompts
- Most tools should trigger permission callbacks in `default` mode - lack of prompts indicates SDK integration issues

# Development Process Requirements

## Testing and Verification Protocol
1. ALWAYS test actual SDK integration before claiming functionality works
2. NEVER assume parameter names or function signatures - verify with actual imports
3. Create minimal test files to verify integration, then DELETE them when done
4. Test each component in isolation before building complex architectures

## File Management Protocol
1. DELETE temporary test files (test_*.py, demo_*.py) after use
2. Do not leave debugging files in the project directory
3. Only keep files that are part of the core application

## SDK Integration Requirements
1. Use exact parameter names from CLAUDE.md specification
2. Test imports and function calls in isolation first
3. Handle JSON serialization of SDK objects properly
4. Always use try/except blocks around SDK calls

## Code Quality - Ruff Linting Workflow

**REQUIRED**: All Python code changes must be linted with Ruff before committing.

### Development Workflow
1. **Before Committing**: Run Ruff on the specific files you modified
   ```bash
   # Lint specific files you changed
   uv run ruff check --fix src/web_server.py src/session_manager.py

   # Or use git to find changed files
   uv run ruff check --fix $(git diff --name-only --diff-filter=AM | grep '\.py$')
   ```

2. **View Violations** (without fixing):
   ```bash
   uv run ruff check src/module_name.py
   ```

3. **AVOID running on entire codebase**: Do NOT run `uv run ruff check --fix src/` as this will auto-fix 684+ unrelated violations across the codebase, creating massive unrelated changes in your PR.

### Progressive Strictness Strategy
The project uses **Option 3: Progressive Strictness** to manage existing technical debt:

**Current State** (as of initial Ruff integration):
- 791 total violations identified
- 684 auto-fixable with `--fix`
- Rule sets enabled: E (pycodestyle errors), W (warnings), F (pyflakes), I (isort), N (pep8-naming), UP (pyupgrade), B (flake8-bugbear)

**Requirements**:
1. **New code MUST NOT introduce new violations**
2. **Changed code SHOULD fix existing violations when touched**
3. **Auto-fix safe violations** in files you modify
4. **Legacy code** with violations is acceptable until modified

### When Working on Files
1. Run `uv run ruff check --fix <file>` before committing
2. Review and commit auto-fixes separately if desired
3. For unfixable violations (marked with `[ ]`), either:
   - Fix manually if straightforward
   - Add inline `# noqa: <code>` with justification if needed
   - Document in PR why violation remains

### Configuration
Ruff configuration is in `pyproject.toml`:
- Line length: 100 characters
- Target Python: 3.11+
- Unused imports in `__init__.py` are allowed

# Frontend Architecture - Vue 3 + Pinia + Vite (PRODUCTION)

## Current Status

The Vue 3 migration is **complete** and in production use. The frontend has grown significantly beyond the original migration scope with 12 Pinia stores, 85+ Vue components, 21 tool handlers, and 3 composables.

**Documentation**: See [frontend/CLAUDE.md](./frontend/CLAUDE.md) for detailed frontend architecture.

## Frontend Structure

```
frontend/
├── src/
│   ├── stores/                    # 12 Pinia stores
│   │   ├── session.js             # Session CRUD, selection, deep linking
│   │   ├── project.js             # Project hierarchy, ordering
│   │   ├── message.js             # Messages, tool calls, orphaned detection
│   │   ├── websocket.js           # 3 WebSocket connections (UI, session, legion)
│   │   ├── legion.js              # Multi-agent: comms, minions
│   │   ├── ui.js                  # Sidebar, modals, loading, responsive
│   │   ├── queue.js               # Per-session message queue
│   │   ├── schedule.js            # Per-legion cron schedules
│   │   ├── resource.js            # Per-session resources (images/files)
│   │   ├── diff.js                # Per-session git diff data
│   │   ├── task.js                # Per-session SDK task tracking
│   │   └── image.js               # Deprecated shim → resource.js
│   │
│   ├── composables/               # 3 reusable composition functions
│   │   ├── useToolResult.js       # Shared tool result extraction
│   │   ├── useToolStatus.js       # Tool status computation
│   │   └── useWebSocket.js        # Generic WebSocket hook
│   │
│   ├── components/
│   │   ├── layout/        (12)    # ProjectPillBar, AgentStrip, AgentChip, RightSidebar, etc.
│   │   ├── configuration/ (6)     # ConfigurationModal + tabs + PermissionPreview
│   │   ├── project/       (4)     # ProjectOverview, ProjectCreateModal, etc.
│   │   ├── session/       (7)     # SessionView, SessionInfoBar, modals, etc.
│   │   ├── messages/      (7)     # MessageList, MessageItem, InputArea, AttachmentList, etc.
│   │   ├── messages/tools/ (5)    # ActivityTimeline, TimelineNode/Detail/Segment/Overflow
│   │   ├── tools/         (21)    # Tool handlers: Read, Edit, Bash, Task*, Skill, etc.
│   │   ├── legion/        (4)     # TimelineView, CommComposer, MinionTreeNode, etc.
│   │   ├── header/        (1)     # TimelineHeader
│   │   ├── statusbar/     (2)     # SessionStatusBar, TimelineStatusBar
│   │   ├── schedules/     (3)     # SchedulePanel, ScheduleItem, ScheduleCreateModal
│   │   ├── tasks/         (7)     # TaskListPanel, DiffPanel, ResourceGallery, QueueSection, etc.
│   │   └── common/        (4)     # FolderBrowserModal, CommCard, DiffFullView, ResourceFullView
│   │
│   ├── router/                    # Vue Router: /, /project/:id, /session/:id, /timeline/:id
│   ├── utils/                     # API client, time formatting, tool summaries
│   └── assets/                    # CSS (styles.css, tool-theme.css)
│
├── vite.config.js                 # Vite dev server + proxy + build config
├── index.html                     # Entry point
└── package.json                   # Dependencies (Vue 3.4, Pinia 2.1, Vite 5.2, Bootstrap 5.3)
```

## Development Workflow

### Running Frontend Dev Server

```bash
# Terminal 1: Backend (use port 8001 to avoid conflicts with production on 8000)
uv run python main.py --debug-all --port 8001

# Terminal 2: Frontend dev server with HMR
cd frontend
npm install  # first time only
npm run dev  # starts on http://localhost:5173

# Access dev server at http://localhost:5173
# Changes reload instantly with Hot Module Replacement
```

### Production Build

```bash
cd frontend
npm run build  # Output: frontend/dist/

# Update FastAPI to serve frontend/dist/ instead of static/
# Delete static/ directory after stability verified
```

## Key Benefits Over Vanilla JS

1. **State Management**: 12 Pinia stores replace 135+ instance variables and dual Map+Array storage
2. **Automatic Reactivity**: No manual `renderSessions()` calls - Vue reactivity handles all UI updates
3. **Component Architecture**: 6767-line monolith split into 85+ focused, reusable components
4. **Event Listener Cleanup**: Automatic cleanup prevents memory leaks
5. **Developer Experience**: Instant HMR, Vue DevTools, TypeScript support, clear separation of concerns

## Pinia Stores (12 Stores)

For detailed store documentation, see [frontend/CLAUDE.md](./frontend/CLAUDE.md#pinia-stores-12).

### 1. Session Store (`stores/session.js`)
**Responsibility**: Session lifecycle, CRUD operations, selection

**State**:
- `sessions` (Map): All sessions by ID
- `currentSessionId` (ref): Currently selected session
- `inputCache` (Map): Preserved unsent text per session
- `initData` (Map): Session initialization config
- `deletingSessions` (Set): Track deletions in progress

**Key Actions**:
- `fetchSessions()`, `createSession()`, `selectSession()`, `deleteSession()`
- `startSession()`, `pauseSession()`, `terminateSession()`, `restartSession()`, `resetSession()`
- `updateSessionName()`, `setPermissionMode()`
- Deep linking with auto-start for created/terminated sessions

### 2. Project Store (`stores/project.js`)
**Responsibility**: Project hierarchy, organization

**State**:
- `projects` (Map): All projects by ID
- `currentProjectId` (ref): Currently selected project (for Legion views)

**Key Actions**:
- `fetchProjects()`, `createProject()`, `deleteProject()`, `updateProject()`
- `toggleExpansion()`, `reorderProjects()`, `reorderSessionsInProject()`
- `isMultiAgent(projectId)`: Check if project is Legion

### 3. Message Store (`stores/message.js`)
**Responsibility**: Messages, tool call lifecycle, orphaned tool detection

**State**:
- `messagesBySession` (Map): Messages per session
- `toolCallsBySession` (Map): Tool calls with full lifecycle tracking
- `toolSignatureToId` (Map): Tool identification for permission matching
- `permissionToToolMap` (Map): Permission request to tool mapping
- `orphanedToolUses` (Map): Tools marked as orphaned

**Key Actions**:
- `loadMessages()`: Paginated loading with orphaned detection
- `addMessage()`, `addToolCall()`, `updateToolCall()`
- `handleToolUse()`, `handlePermissionRequest()`, `handlePermissionResponse()`, `handleToolResult()`
- `toggleToolExpansion()`: Collapse/expand tool cards
- Orphaned tool detection: session restart, interrupt, termination

### 4. WebSocket Store (`stores/websocket.js`)
**Responsibility**: 3 WebSocket connections, message routing

**State**:
- `uiSocket`, `uiConnected`, `uiRetryCount`: Global UI updates
- `sessionSocket`, `sessionConnected`, `sessionRetryCount`: Session messages
- `legionSocket`, `legionConnected`, `legionRetryCount`: Legion comms

**Key Actions**:
- `connectUI()`, `connectSession()`, `connectLegion()`: Establish connections
- `sendMessage()`, `sendPermissionResponse()`, `interruptSession()`
- `handleUIMessage()`, `handleSessionMessage()`, `handleLegionMessage()`: Route incoming messages
- Automatic reconnection with exponential backoff (max 10 UI retries, 5 session/legion)

### 5. Legion Store (`stores/legion.js`)
**Responsibility**: Multi-agent data (comms, minions)

**State**:
- `commsByLegion` (Map): Timeline communications per legion
- `minionsByLegion` (Map): Minions per legion

**Key Actions**:
- `loadTimeline()`: Paginated comm loading (100/page)
- `addComm()`: Real-time comm from WebSocket
- `sendComm()`: User sends comm to minion
- `createMinion()`

### 6. UI Store (`stores/ui.js`)
**Responsibility**: UI state (sidebar, modals, scroll, responsive)

**State**:
- `sidebarCollapsed`, `sidebarWidth`: Mobile-first sidebar
- `windowWidth`, `isMobile`: Responsive breakpoints
- `autoScrollEnabled`: Toggle message auto-scroll
- `activeModal`, `modalData`: Current modal

**Key Actions**:
- `toggleSidebar()`, `setSidebarWidth()`, `setAutoScroll()`
- `showModal()`, `hideModal()`, `showLoading()`, `hideLoading()`

### 7. Queue Store (`stores/queue.js`)
**Responsibility**: Per-session message queue state, pause/resume, item lifecycle

### 8. Schedule Store (`stores/schedule.js`)
**Responsibility**: Per-legion cron schedules, execution history, WebSocket updates

### 9. Resource Store (`stores/resource.js`)
**Responsibility**: Per-session resources (images/files), gallery state, full view modal, text content cache

### 10. Diff Store (`stores/diff.js`)
**Responsibility**: Per-session git diff data, view modes (total/commits), per-file diff cache

### 11. Task Store (`stores/task.js`)
**Responsibility**: Per-session SDK task tracking (TaskCreate/Update/List/Get tool integration)

### 12. Image Store (`stores/image.js`)
**Responsibility**: Deprecated shim re-exporting `useResourceStore`

## Vue Components (85+ files)

For detailed component documentation, see [frontend/CLAUDE.md](./frontend/CLAUDE.md#component-organization).

### Layout (12)
- `ProjectPillBar`, `ProjectPill`, `AgentStrip`, `AgentChip`, `StackedChip`, `ChipConnector`
- `HeaderRow1`, `AgentOverview`, `PeekCard`, `ConnectionIndicator`, `RightSidebar`, `RestartModal`

### Configuration (6)
- `ConfigurationModal`, `GeneralTab`, `PermissionsTab`, `AdvancedTab`, `SandboxTab`, `PermissionPreviewModal`

### Session (7)
- `SessionView`, `SessionInfoBar`, `SessionStateStatusLine`, `SessionInfoModal`, `SessionManageModal`, `NoSessionSelected`

### Project (4)
- `ProjectOverview`, `ProjectStatusLine`, `ProjectCreateModal`, `ProjectEditModal`

### Messages (7)
- `MessageList`, `MessageItem`, `UserMessage`, `AssistantMessage`, `SystemMessage`, `ThinkingBlock`, `InputArea`
- `AttachmentList`, `CompactionEventGroup`, `SlashCommandDropdown`

### Activity Timeline (5)
- `ActivityTimeline`, `TimelineNode`, `TimelineDetail`, `TimelineSegment`, `TimelineOverflow`

### Tool Handlers (21)
**See [TOOL_HANDLERS.md](./TOOL_HANDLERS.md) for detailed documentation**

- **File**: `ReadToolHandler`, `EditToolHandler`, `WriteToolHandler`
- **Shell**: `BashToolHandler`, `ShellToolHandler`, `CommandToolHandler`
- **Search**: `SearchToolHandler` (Grep/Glob)
- **Web**: `WebToolHandler` (WebFetch/WebSearch)
- **Tasks**: `TodoToolHandler`, `TaskToolHandler`, `TaskCreateToolHandler`, `TaskGetToolHandler`, `TaskListToolHandler`, `TaskUpdateToolHandler`
- **Interactive**: `AskUserQuestionToolHandler`
- **Skills**: `SkillToolHandler`, `SlashCommandToolHandler`
- **Other**: `ExitPlanModeToolHandler`, `NotebookEditToolHandler`
- **Shared**: `ToolSuccessMessage` (success banner), `BaseToolHandler` (fallback)

### Right Sidebar Panels (7)
- `TaskListPanel`, `TaskItem`, `DiffPanel`, `ResourceGallery`, `ImageGallery`, `CommsPanel`, `QueueSection`

### Schedules (3)
- `SchedulePanel`, `ScheduleItem`, `ScheduleCreateModal`

### Legion (4)
- `TimelineView`, `CommComposer`, `MinionTreeNode`, `MinionViewModal`

### Common (4)
- `FolderBrowserModal`, `CommCard`, `DiffFullView`, `ResourceFullView`

## Naming Conventions

- **camelCase**: Variables, functions, computed properties
- **PascalCase**: Component names
- **kebab-case**: Component file names

# Backend Architecture - Python FastAPI Server

## System Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                       Browser (Vue 3 Frontend)                       │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐             │
│  │ Pinia Stores │  │ Components   │  │ Vue Router   │             │
│  │ (12 stores)  │  │  (85+ files) │  │  (routing)   │             │
│  └──────────────┘  └──────────────┘  └──────────────┘             │
└────────┬────────────────────┬────────────────────┬──────────────────┘
         │                    │                    │
         │ WebSocket (3 connections) + REST API    │
         │                    │                    │
┌────────▼────────────────────▼────────────────────▼──────────────────┐
│                   FastAPI Server (src/web_server.py)                 │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │                    SessionCoordinator                           │ │
│  │  ┌────────────┐  ┌────────────┐  ┌───────────────────────┐   │ │
│  │  │SessionMgr  │  │ProjectMgr  │  │  ClaudeSDK            │   │ │
│  │  │(state)     │  │(hierarchy) │  │  (SDK wrapper)        │   │ │
│  │  └────────────┘  └────────────┘  └───────────────────────┘   │ │
│  └────────────────────────────────────────────────────────────────┘ │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │                    LegionSystem (Multi-Agent)                   │ │
│  │  ┌─────────────┐  ┌──────────┐  ┌────────────────────────┐   │ │
│  │  │ Legion      │  │Overseer  │  │  CommRouter            │   │ │
│  │  │ Coordinator │  │Control   │  │  (minion comms)        │   │ │
│  │  └─────────────┘  └──────────┘  └────────────────────────┘   │ │
│  │  ┌──────────────────────────────────────────────────────┐     │ │
│  │  │  MemoryManager (distillation, reinforcement)         │     │ │
│  │  └──────────────────────────────────────────────────────┘     │ │
│  └────────────────────────────────────────────────────────────────┘ │
└────────┬─────────────────────────────────────────────────────────────┘
         │ query() API
┌────────▼─────────────────────────────────────────────────────────────┐
│                   Claude Agent SDK (External Package)                 │
│              Streaming conversations, tool execution                  │
└───────────────────────────────────────────────────────────────────────┘
```

## Backend File Organization

### Entry Point
- **`main.py`**: Application entry, arg parsing, logging config, uvicorn server start

### Core Backend Modules (`src/`)

#### `src/web_server.py` (1230 lines) - **HTTP + WebSocket Server**
**Main Class**: `ClaudeWebUI`

**Responsibilities**:
- REST API endpoints (projects, sessions, legion, utility)
- 3 WebSocket managers: `UIWebSocketManager`, `WebSocketManager`, `LegionWebSocketManager`
- Permission callback creation with asyncio.Future for async approval
- Session state change broadcasting

**Key Methods**:
- `_create_permission_callback()`: Async permission prompts (lines 984-1185)
- `_create_message_callback()`: Message wrapping for WebSocket broadcast (lines 931-960)
- `_on_state_change()`: Broadcast session state changes (lines 962-982)

#### `src/session_coordinator.py` (1050 lines) - **Central Orchestrator**
**Main Class**: `SessionCoordinator`

**Responsibilities**:
- Ties together SessionManager, ProjectManager, ClaudeSDK, DataStorage, MessageProcessor
- Manages active SDK instances per session
- Handles ExitPlanMode detection and auto-reset
- Permission update tracking
- Tool use tracking for orphaned detection

**Key Methods**:
- `create_session()`: Session + storage + SDK initialization (lines 100-171)
- `start_session()`: Start/resume SDK, send client_launched message (lines 177-289)
- `send_message()`: Queue message to SDK, update processing state (lines 538-572)
- `interrupt_session()`: Stop active SDK processing (lines 447-492)
- `set_permission_mode()`: Runtime permission mode changes (lines 494-536)
- `_create_message_callback()`: Process SDK messages, detect completion (lines 741-818)

#### `src/session_manager.py` - **Session Lifecycle & State**
**Main Classes**: `SessionState` (enum), `SessionInfo` (dataclass), `SessionManager`

**SessionState Values**:
- `CREATED`, `STARTING`, `ACTIVE`, `PAUSED`, `TERMINATING`, `TERMINATED`, `ERROR`

**SessionInfo Fields** (Extended for Legion):
- Standard: `session_id`, `state`, `working_directory`, `current_permission_mode`, `tools`, `model`, `name`, `order`
- Legion: `is_minion`, `role`, `is_overseer`, `parent_overseer_id`, `child_minion_ids`, `capabilities`, `initialization_context`

**Key Methods**:
- `create_session()`: Create directory, persist state.json
- `start_session()`, `pause_session()`, `terminate_session()`: State transitions
- `update_processing_state()`: Track active processing
- `update_permission_mode()`: Runtime mode changes

#### `src/project_manager.py` - **Project Hierarchy & Organization**
**Main Classes**: `ProjectInfo` (dataclass), `ProjectManager`

**ProjectInfo Fields** (Extended for Legion):
- Standard: `project_id`, `name`, `working_directory`, `session_ids`, `is_expanded`, `order`
- Legion: `is_multi_agent`, `minion_ids`, `max_concurrent_minions`, `active_minion_count`

**Key Methods**:
- `create_project()`: Create with order shifting
- `add_session_to_project()`, `remove_session_from_project()`
- `reorder_projects()`, `reorder_project_sessions()`

#### `src/claude_sdk.py` - **SDK Wrapper & Message Queue**
**Main Class**: `ClaudeSDK`

**Responsibilities**:
- Wraps Claude Agent SDK with async queue
- Manages conversation loop
- Handles streaming responses
- MCP server integration for Legion tools

**Key Components**:
- `_message_queue`: Async queue for user messages
- `_conversation_task`: Background processing loop
- `_sdk_client`: ClaudeSDKClient instance
- `_mcp_server`: Optional MCP server for Legion tools

**Key Methods**:
- `start()`: Initialize SDK client, start conversation loop
- `send_message()`: Enqueue message
- `interrupt_session()`: Set interrupt flag
- `set_permission_mode()`: Send mode change to SDK
- `_conversation_loop()`: Process queue, stream responses

#### `src/message_parser.py` - **Message Normalization**
**Main Classes**: `MessageType` (enum), `ParsedMessage` (dataclass), `MessageProcessor`

**Responsibilities**:
- Convert between SDK objects, storage format, WebSocket format
- Unified processing for consistency

**Handlers**: `SystemMessageHandler`, `AssistantMessageHandler`, `UserMessageHandler`, `ResultMessageHandler`, `PermissionRequestHandler`, `PermissionResponseHandler`

**MessageProcessor Methods**:
- `process_message()`: Raw SDK message → ParsedMessage
- `prepare_for_storage()`: ParsedMessage → JSON-serializable dict
- `prepare_for_websocket()`: Format for frontend

#### `src/data_storage.py` - **Persistent Storage**
**Main Class**: `DataStorageManager`

**Files Managed**:
- `messages.jsonl`: Append-only message log
- `state.json`: Session metadata (managed by SessionManager)

**Key Methods**:
- `append_message()`, `read_messages()`, `get_message_count()`, `cleanup()`

## Legion Multi-Agent System

### Legion Components (`src/legion/`)

#### `src/legion/legion_coordinator.py` - **Legion Lifecycle Management**
**Main Class**: `LegionCoordinator`

**Responsibilities**:
- Legion creation and deletion
- Fleet control (halt all, resume all, emergency halt)
- Central capability registry (MVP: keyword search)

#### `src/legion/overseer_controller.py` - **Minion Management**
**Main Class**: `OverseerController`

**Responsibilities**:
- Minion lifecycle: create_minion_for_user(), spawn_minion(), dispose_minion()
- Enforce parent authority (only parent can dispose children)
- Memory transfer on disposal
- Capability registration

#### `src/legion/comm_router.py` - **Inter-Agent Communication**
**Main Class**: `CommRouter`

**Responsibilities**:
- Convert between Comms and SDK Messages
- Route Comms to minions or user
- Handle interrupt priorities (HALT, PIVOT)
- Persist to timeline and minion logs
- Parse and validate #tag references

#### `src/legion/memory_manager.py` - **Memory & Learning** (Planned)
**Main Class**: `MemoryManager`

**Responsibilities** (Future):
- Distill task completions into structured memories
- Reinforce memories based on outcome feedback
- Promote high-quality memories to long-term
- Transfer knowledge between minions
- Support minion forking with memory copy

#### `src/legion/legion_mcp_tools.py` - **MCP Tools for Minions**
**Main Class**: `LegionMCPTools`

**Tools Provided**:
- **Communication**: `send_comm`
- **Lifecycle**: `spawn_minion`, `dispose_minion`
- **Discovery**: `list_minions`, `get_minion_info`, `search_capability`

**Integration**: Single instance per legion, attached to all minion SDK sessions

### Legion Data Models (`src/legion/models.py`)

**Core Entities**:
- `Comm`: High-level message with routing info
- `CommType` enum: TASK, QUESTION, REPORT, INFO, HALT, PIVOT, THOUGHT, SPAWN, DISPOSE, SYSTEM
- `MemoryEntry`, `MinionMemory`: Knowledge management (future)

## Additional Backend Systems

### Queue System (`src/queue_manager.py`, `src/queue_processor.py`)

FIFO message queue with JSONL persistence for timed/sequential message delivery.

- **QueueManager**: State management with `QueueItem` dataclass (queue_id, session_id, content, reset_session, status, position). Storage via `queue.jsonl` with event replay on startup.
- **QueueProcessor**: Background asyncio task delivering queued messages with timing guards (`min_wait_seconds=10`, `min_idle_seconds=10`). Auto-starts sessions, handles pausing, polls `is_processing` without timeout.

### Cron Scheduler (`src/legion/scheduler_service.py`)

Background service evaluating cron schedules every 30 seconds.

- **SchedulerService**: Creates/manages `Schedule` objects with croniter evaluation. Enqueues prompts via SessionCoordinator when due. Records `ScheduleExecution` history to JSONL. Auto-cancels on minion disposal.
- **Models**: `Schedule` (cron, next_run, status, failure tracking), `ScheduleExecution` (execution record), `ScheduleStatus` (ACTIVE/PAUSED/CANCELLED)

### Archive Manager (`src/legion/archive_manager.py`)

Timestamped archival of minion session data before disposal.

- **ArchiveManager**: Copies messages.jsonl, state.json, and disposal metadata to `data/archives/minions/{minion_id}/{timestamp}/`. Returns `ArchiveResult` with archive path and file count.

### Permission Resolver (`src/permission_resolver.py`)

Multi-source permission merge for effective permission preview.

- **`resolve_effective_permissions()`**: Parses permissions from user/project/local settings files and session-level allowed_tools. Returns list of `{permission, sources}` with source tracking.

### Resource MCP Tools (`src/resource_mcp_tools.py`)

Session-scoped MCP server for agent resource display in the task panel.

- **ResourceMCPTools**: Creates per-session MCP servers with `register_resource` and `register_image` (deprecated alias) tools. Validates file path, extension, size (10MB max, 100 per session). Broadcasts `resource_registered` via WebSocket.

### Template Manager (`src/template_manager.py`)

File-based minion template CRUD with slug naming.

- **TemplateManager**: Stores templates as JSON+MD file pairs in `data/templates/`. Supports slug-based filenames for human readability. Seeds default templates from `src/default_templates/`. Migrates legacy UUID filenames on load.

### Skill Manager (`src/skill_manager.py`)

Global skill deployment and symlink management.

- **SkillManager**: Syncs skills from `src/default_skills/` to `~/.cc_webui/skills/`, creates symlinks in `~/.claude/skills/`. Detects conflicts with user files. Returns (added, updated, removed) counts.

## Data Directory Structure

```
data/
├── logs/                           # Per-category debug logs
│   ├── coordinator.log             # SessionCoordinator actions
│   ├── error.log                   # All errors
│   ├── parser.log                  # Message parsing
│   ├── sdk_debug.log               # SDK integration
│   ├── storage.log                 # File operations
│   └── websocket_debug.log         # WebSocket lifecycle
│
├── projects/{uuid}/                # One folder per project
│   └── state.json                  # ProjectInfo serialized
│
├── sessions/{uuid}/                # One folder per session
│   ├── state.json                  # SessionInfo serialized
│   ├── messages.jsonl              # Append-only message log
│   ├── queue.jsonl                 # Message queue event log
│   └── resources/                  # Registered resources (images/files)
│
├── templates/                      # Minion templates (JSON + MD pairs)
│   ├── {slug}.json                 # Template configuration
│   └── {slug}.md                   # Template system prompt
│
├── archives/                       # Archived minion data (post-disposal)
│   └── minions/{minion_id}/{ts}/   # Timestamped snapshots
│
└── legions/{uuid}/                 # One folder per legion (multi-agent project)
    ├── timeline.jsonl              # Unified comm log
    ├── schedules.json              # Cron schedule definitions
    ├── schedule_history.jsonl      # Schedule execution log
    └── minions/{minion_id}/
        ├── minion_state.json
        ├── session_messages.jsonl  # SDK messages
        ├── comms.jsonl             # Minion-specific comm log
        ├── short_term_memory.json  # (Future)
        └── long_term_memory.json   # (Future)
```

## API Endpoint Reference

For the complete endpoint reference with request/response details, see [.claude/API_REFERENCE.md](./.claude/API_REFERENCE.md).

**Summary**: 50+ REST endpoints across 10 domains (projects, sessions, files, resources, diffs, queue, legion, schedules, templates, system) + 3 WebSocket endpoints (UI, Session, Legion).

## Message Flow Architecture

### SDK Message → Storage → WebSocket Flow

```
1. ClaudeSDK receives message from claude_agent_sdk
   ↓
2. ClaudeSDK._conversation_loop() extracts message data
   ↓
3. Calls message_callback (SessionCoordinator._create_message_callback)
   ↓
4. MessageProcessor.process_message() normalizes to ParsedMessage
   ↓
5. SessionCoordinator stores via DataStorageManager.append_message()
   ├─ MessageProcessor.prepare_for_storage() converts to dict
   └─ Writes to messages.jsonl
   ↓
6. SessionCoordinator broadcasts to WebSocket
   ├─ MessageProcessor.prepare_for_websocket() formats for frontend
   └─ WebSocketManager.send_message() pushes to connected clients
   ↓
7. Frontend (Vue stores) receives and updates state reactively
```

### User Message → SDK Flow

```
1. User types in frontend, clicks Send
   ↓
2. Frontend sends via session WebSocket: {type: "send_message", content: "..."}
   ↓
3. web_server.py websocket handler receives message
   ↓
4. Calls SessionCoordinator.send_message(session_id, content)
   ↓
5. SessionCoordinator marks session.is_processing = True
   ↓
6. ClaudeSDK.send_message() enqueues to _message_queue
   ↓
7. ClaudeSDK._conversation_loop() picks up from queue
   ↓
8. Sends to claude_agent_sdk via query(prompt=message)
   ↓
9. SDK streams back responses (loop back to SDK Message flow)
   ↓
10. On ResultMessage, SessionCoordinator sets is_processing = False
```

### Permission Flow

```
1. SDK needs permission for tool (e.g., Edit, Write)
   ↓
2. SDK calls permission_callback() from web_server.py
   ↓
3. web_server.py stores permission request message (with suggestions if any)
   ↓
4. web_server.py broadcasts permission_request to WebSocket
   ↓
5. Frontend displays permission modal with suggestions
   ↓
6. User clicks Allow/Deny (optionally applies suggestions)
   ↓
7. Frontend sends permission_response via WebSocket
   ↓
8. web_server.py resolves asyncio.Future with user's decision
   ↓
9. Permission callback returns {behavior: "allow"/"deny", updated_permissions: [...]}
   ↓
10. SDK receives response and continues/aborts tool execution
```

## Common Development Scenarios

### Finding Where Functionality Lives

**Problem**: Need to change how Edit tool is displayed
→ **Solution**: `frontend/src/components/messages/tools/EditToolHandler.vue`

**Problem**: Session not starting, need to debug SDK initialization
→ **Solution**: `src/session_coordinator.py:177-289` (start_session()) + enable `--debug-sdk`

**Problem**: WebSocket not connecting
→ **Solution**: `src/web_server.py:684-930` (websocket_endpoint) + enable `--debug-websocket`

**Problem**: Messages not persisting
→ **Solution**: `src/data_storage.py:51-67` (append_message()) + enable `--debug-storage`

**Problem**: Permission callback not triggering
→ **Solution**: `src/web_server.py:984-1185` (_create_permission_callback()) + enable `--debug-permissions`

**Problem**: Add new REST endpoint
→ **Solution**: Add route in `src/web_server.py:232-635` (_setup_routes())

**Problem**: Add new Vue component
→ **Solution**: Create `.vue` file, register in parent component or router

**Problem**: Add new tool handler
→ **Solution**: See TOOL_HANDLERS.md for Vue 3 component creation guide

### Understanding State Management

**Session States** (`SessionState` enum):
- `CREATED`: Session exists but SDK not started
- `STARTING`: Transitioning to active
- `ACTIVE`: SDK running, can send/receive messages
- `PAUSED`: Awaiting user input (permissions)
- `TERMINATED`: SDK stopped cleanly
- `ERROR`: Startup or runtime failure (check `error_message` field)

**Processing State** (`is_processing` boolean):
- `True`: Session actively processing user input (disable send button)
- `False`: Session idle, ready for new input
- Automatically reset on `ResultMessage` or errors

**Permission Modes**:
- `default`: Prompt for everything not pre-approved in .claude/settings
- `acceptEdits`: Auto-approve Edit/Write/etc (permissive)
- `plan`: Planning mode (auto-resets to default after ExitPlanMode)
- `bypassPermissions`: No prompts at all

## Component Dependencies

```
main.py
  └─ web_server.py
      ├─ session_coordinator.py
      │   ├─ session_manager.py
      │   ├─ project_manager.py
      │   ├─ claude_sdk.py
      │   │   ├─ data_storage.py
      │   │   ├─ message_parser.py
      │   │   └─ logging_config.py
      │   ├─ data_storage.py
      │   ├─ message_parser.py
      │   ├─ queue_manager.py
      │   ├─ queue_processor.py
      │   ├─ template_manager.py
      │   ├─ skill_manager.py
      │   ├─ permission_resolver.py
      │   ├─ resource_mcp_tools.py
      │   ├─ legion/legion_coordinator.py (if multi-agent)
      │   │   ├─ legion/overseer_controller.py
      │   │   ├─ legion/comm_router.py
      │   │   ├─ legion/memory_manager.py
      │   │   ├─ legion/legion_mcp_tools.py
      │   │   ├─ legion/scheduler_service.py
      │   │   └─ legion/archive_manager.py
      │   └─ logging_config.py
      ├─ message_parser.py
      └─ logging_config.py
```

## Testing & Development Patterns

### Standard Testing Configuration

**CRITICAL**: Always use port 8001 for testing to avoid conflicts with production on port 8000.

```bash
# Test run
uv run python main.py --debug-all --data-dir test_data --port 8001

# Production run
uv run python main.py --port 8000
```

### Process Management - REQUIRED PATTERN

**CRITICAL**: Always kill processes by PID, never by name.

**Windows**:
```bash
# Find process
netstat -ano | findstr ":8001"

# Kill by PID
taskkill /PID <PID> /F
```

**Unix/Linux/macOS**:
```bash
# Find process
lsof -i :8001

# Kill by PID
kill <PID>
# or: kill -9 <PID>
```

### Running Tests

```bash
# All tests
uv run pytest src/tests/ -v

# Specific test file
uv run pytest src/tests/test_session_manager.py -v

# With coverage
uv run pytest src/tests/ --cov=src --cov-report=html
```

### Frontend Development

```bash
# Terminal 1: Backend
uv run python main.py --port 8001 --debug-all

# Terminal 2: Frontend dev server
cd frontend
npm run dev  # http://localhost:5173
```

## Key Architectural Decisions

**Why SessionCoordinator?**
- Central orchestrator pattern prevents circular dependencies
- Single point of control for SDK lifecycle
- Coordinates state across multiple managers

**Why MessageProcessor?**
- SDK message format ≠ storage format ≠ WebSocket format
- Unified processing ensures consistency
- Handles backward compatibility as SDK evolves

**Why Separate Project and Session Managers?**
- Projects = lightweight grouping (working_directory + sessions)
- Sessions = heavy (SDK instances, message history, state)
- Enables multi-project session support (future)

**Why JSONL for messages?**
- Append-only is safe for concurrent access
- Line-by-line reading enables pagination
- Easy to repair if corruption occurs
- Human-readable for debugging

**Why asyncio.Future for permissions?**
- SDK permission callback is synchronous from its perspective
- WebSocket communication is asynchronous
- Future bridges sync callback ↔ async WebSocket response

**Why Vue 3 + Pinia?**
- Reactive state management eliminates manual UI updates
- Component architecture improves maintainability
- Vue DevTools provides powerful debugging
- TypeScript support improves code quality
- Hot Module Replacement accelerates development

**Why MCP Tools for Legion?**
- Explicit intent (no ambiguity in minion actions)
- Structured parameters (type-safe, validated)
- Clear error feedback (minions can act on specific errors)
- Debuggable (tool calls visible in session messages)
- Self-documenting (tool descriptions teach minions usage)

## Future Enhancements

**Frontend**:
- Syntax highlighting for code blocks (highlight.js/Prism)
- Virtual scrolling for large message lists
- Advanced filtering and search
- Multi-user authentication
- Theme customization

**Backend**:
- PostgreSQL for multi-instance deployments
- Redis for session state caching
- Prometheus metrics exporter
- Rate limiting and abuse prevention
- Multi-user authorization

**Legion**:
- Memory distillation and reinforcement
- Knowledge transfer on disposal
- Minion forking for A/B testing
- Enhanced capability discovery

## Additional Resources

- **User Guide**: [run_guide.md](./run_guide.md) - Setup, usage, troubleshooting
- **Frontend Architecture**: [frontend/CLAUDE.md](./frontend/CLAUDE.md) - Vue 3 stores, components, composables
- **API Reference**: [.claude/API_REFERENCE.md](./.claude/API_REFERENCE.md) - All REST + WebSocket endpoints
- **Tool Handlers**: [TOOL_HANDLERS.md](./TOOL_HANDLERS.md) - Vue 3 tool handler development
- **Legion Proposal**: [legion_proposal/LEGION_PROPOSAL.md](./legion_proposal/LEGION_PROPOSAL.md) - Multi-agent design
- **MCP Tools**: [legion_proposal/MCP_TOOLS_ARCHITECTURE.md](./legion_proposal/MCP_TOOLS_ARCHITECTURE.md) - Inter-agent communication
- **Development Plan**: [DEVELOPMENT_PLAN.md](./DEVELOPMENT_PLAN.md) - Project roadmap
- **Claude Agent SDK**: https://github.com/anthropics/claude-agent-sdk

## Summary

Claude WebUI is a **production-ready web interface** for Claude Agent SDK with:

**Single-Agent Features**:
- Real-time streaming conversations with rich tool visualization (21 tool handlers)
- Project/session hierarchy with drag-and-drop reordering
- Four permission modes with smart suggestions and permission preview
- Message queue with timed delivery and auto-start
- Resource gallery (images and files from agents)
- Git diff viewer with per-commit and aggregate views
- Orphaned tool detection and cleanup
- Persistent message storage (JSONL + JSON)
- Vue 3 + Pinia reactive UI (12 stores, 85+ components)
- Mobile-responsive design

**Multi-Agent Features (Legion)**:
- Minion creation and management with templates
- Inter-agent communication (structured Comms)
- Cron-based scheduling for recurring agent tasks
- Timeline view for observability
- MCP tools for explicit minion actions (send_comm, spawn/dispose, discovery)
- Sandbox mode for minions
- Session archival on disposal

**Developer Experience**:
- Comprehensive debugging tools (per-category logs)
- 50+ REST endpoints + 3 WebSocket endpoints
- Extensible architecture (21 tool handlers, easy to add more)
- Hot Module Replacement for instant feedback
- Vue DevTools integration

---

**important-instruction-reminders**

Do what has been asked; nothing more, nothing less.
NEVER create files unless they're absolutely necessary for achieving your goal.
ALWAYS prefer editing an existing file to creating a new one.
NEVER proactively create documentation files (*.md) or README files. Only create documentation files if explicitly requested by the User.
ALWAYS remove temporary test files after debugging is complete.

There is windows file modification bug in Claude Code. The workaround is: always use complete absolute Windows paths  with drive letters and backslashes for ALL file operations. Apply this rule going forward, not just for this  file.
