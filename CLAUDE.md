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

The frontend migration to Vue 3 is **substantially complete** and in production use:

- ✅ **Phase 1 Complete**: All Pinia stores, Vue Router, base components
- ✅ **Phase 2 Complete**: Project/Session components with full CRUD operations
- ✅ **Phase 3 Complete**: Message display, tool handlers (13+ custom handlers), tool lifecycle tracking
- ✅ **Phase 4 Complete**: Legion Timeline/Spy/Horde views, minion management, comm system
- ⏳ **Phase 5 In Progress**: Polish, orphaned tool detection, permission system refinements
- ⏳ **Phase 6 Pending**: Production build optimization, cutover from dev server

**Documentation**: See `frontend/README.md` for development guide and `frontend/MIGRATION_PLAN.md` for detailed migration status.

## Frontend Structure

```
frontend/
├── src/
│   ├── stores/                    # Pinia stores (state management)
│   │   ├── session.js             # Session CRUD, selection, deep linking
│   │   ├── project.js             # Project hierarchy, ordering
│   │   ├── message.js             # Messages, tool calls, orphaned detection
│   │   ├── websocket.js           # 3 WebSocket connections (UI, session, legion)
│   │   ├── legion.js              # Multi-agent: comms, minions, hordes
│   │   └── ui.js                  # Sidebar, modals, loading, responsive
│   │
│   ├── components/
│   │   ├── layout/                # AppHeader, Sidebar, ConnectionIndicator
│   │   ├── project/               # ProjectList, ProjectItem, ProjectCreateModal, etc.
│   │   ├── session/               # SessionView, SessionItem, SessionCreateModal, etc.
│   │   ├── messages/              # MessageList, MessageItem, UserMessage, AssistantMessage, etc.
│   │   ├── messages/tools/        # 13+ tool handlers (ReadToolHandler, EditToolHandler, etc.)
│   │   ├── legion/                # TimelineView, SpyView, HordeView, CommComposer, etc.
│   │   └── common/                # FolderBrowserModal, InputArea, etc.
│   │
│   ├── router/                    # Vue Router: /, /session/:id, /timeline/:id, /spy/:id, /horde/:id
│   ├── composables/               # Reusable composition functions
│   ├── utils/                     # API client, helpers
│   └── assets/                    # CSS, images
│
├── vite.config.js                 # Vite dev server + build config
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

1. **State Management**: 6 Pinia stores replace 135+ instance variables and dual Map+Array storage
2. **Automatic Reactivity**: No manual `renderSessions()` calls - Vue reactivity handles all UI updates
3. **Component Architecture**: 6767-line monolith split into 53 focused, reusable components
4. **Event Listener Cleanup**: Automatic cleanup prevents memory leaks
5. **Developer Experience**: Instant HMR, Vue DevTools, TypeScript support, clear separation of concerns

## Pinia Stores (State Management)

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
**Responsibility**: Multi-agent data (comms, minions, hordes)

**State**:
- `commsByLegion` (Map): Timeline communications per legion
- `minionsByLegion` (Map): Minions per legion
- `hordesByLegion` (Map): Hierarchical groups

**Key Actions**:
- `loadTimeline()`: Paginated comm loading (100/page)
- `addComm()`: Real-time comm from WebSocket
- `sendComm()`: User sends comm to minion
- `createMinion()`, `loadHordes()`

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

## Vue Components (53 files)

### Layout (3)
- `AppHeader.vue`: Top navigation
- `Sidebar.vue`: Project/Session/Legion hierarchy
- `ConnectionIndicator.vue`: WebSocket status

### Session Management (9)
- `SessionView.vue`: Main chat interface
- `SessionItem.vue`: Sidebar session entry
- `SessionCreateModal.vue`, `SessionEditModal.vue`, `SessionManageModal.vue`, `SessionInfoModal.vue`
- `SessionInfoBar.vue`, `SessionStatusBar.vue`, `NoSessionSelected.vue`

### Project Management (5)
- `ProjectList.vue`, `ProjectItem.vue`, `ProjectStatusLine.vue`
- `ProjectCreateModal.vue`, `ProjectEditModal.vue`

### Message Display (7)
- `MessageList.vue`: Auto-scrolling container
- `MessageItem.vue`: Router to specific message types
- `UserMessage.vue`, `AssistantMessage.vue`, `SystemMessage.vue`
- `ThinkingBlock.vue`, `ToolCallCard.vue`

### Tool Handlers (13)
**See TOOL_HANDLERS.md for detailed documentation**

- `BaseToolHandler.vue`: Fallback for unknown tools
- `ReadToolHandler.vue`, `EditToolHandler.vue`, `WriteToolHandler.vue`
- `BashToolHandler.vue`, `ShellToolHandler.vue`, `CommandToolHandler.vue`
- `SearchToolHandler.vue`, `WebToolHandler.vue`
- `TodoToolHandler.vue`, `TaskToolHandler.vue`, `ExitPlanModeToolHandler.vue`
- `NotebookEditToolHandler.vue`

### Legion Features (11)
- `TimelineView.vue`, `TimelineHeader.vue`, `TimelineStatusBar.vue`
- `SpyView.vue`, `SpySelector.vue`
- `HordeView.vue`, `HordeHeader.vue`, `HordeStatusBar.vue`, `HordeSelector.vue`
- `MinionTreeNode.vue`, `CommComposer.vue`, `CreateMinionModal.vue`

### Common (3)
- `FolderBrowserModal.vue`: Directory selection
- `InputArea.vue`: Message textarea
- Status bars: `SessionStatusBar.vue`, `TimelineStatusBar.vue`, `HordeStatusBar.vue`

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
│  │  (6 stores)  │  │  (53 files)  │  │  (routing)   │             │
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
- Legion: `is_minion`, `role`, `is_overseer`, `parent_overseer_id`, `child_minion_ids`, `horde_id`, `capabilities`, `initialization_context`

**Key Methods**:
- `create_session()`: Create directory, persist state.json
- `start_session()`, `pause_session()`, `terminate_session()`: State transitions
- `update_processing_state()`: Track active processing
- `update_permission_mode()`: Runtime mode changes

#### `src/project_manager.py` - **Project Hierarchy & Organization**
**Main Classes**: `ProjectInfo` (dataclass), `ProjectManager`

**ProjectInfo Fields** (Extended for Legion):
- Standard: `project_id`, `name`, `working_directory`, `session_ids`, `is_expanded`, `order`
- Legion: `is_multi_agent`, `horde_ids`, `minion_ids`, `max_concurrent_minions`, `active_minion_count`

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
- Horde tracking

#### `src/legion/overseer_controller.py` - **Minion Management**
**Main Class**: `OverseerController`

**Responsibilities**:
- Minion lifecycle: create_minion_for_user(), spawn_minion(), dispose_minion()
- Enforce parent authority (only parent can dispose children)
- Horde creation and updates
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
- `Horde`: Tree structure with root overseer + members
- `Comm`: High-level message with routing info
- `CommType` enum: TASK, QUESTION, REPORT, INFO, HALT, PIVOT, THOUGHT, SPAWN, DISPOSE, SYSTEM
- `MemoryEntry`, `MinionMemory`: Knowledge management (future)

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
│   └── messages.jsonl              # Append-only message log
│
└── legions/{uuid}/                 # One folder per legion (multi-agent project)
    ├── timeline.jsonl              # Unified comm log
    ├── hordes/{horde_id}/
    │   └── horde_state.json
    └── minions/{minion_id}/
        ├── minion_state.json
        ├── session_messages.jsonl  # SDK messages
        ├── comms.jsonl             # Minion-specific comm log
        ├── short_term_memory.json  # (Future)
        └── long_term_memory.json   # (Future)
```

## API Endpoint Reference

### Project Endpoints

```
POST   /api/projects                      # Create project (with is_multi_agent, max_concurrent_minions)
GET    /api/projects                      # List all projects with sessions
GET    /api/projects/{id}                 # Get specific project
PUT    /api/projects/{id}                 # Update name/expansion state
DELETE /api/projects/{id}                 # Delete project and all sessions
PUT    /api/projects/{id}/toggle-expansion
PUT    /api/projects/reorder              # Reorder projects
PUT    /api/projects/{id}/sessions/reorder
```

### Session Endpoints

```
POST   /api/sessions                      # Create session (with project_id, permission_mode, tools, model, name)
GET    /api/sessions                      # List all sessions
GET    /api/sessions/{id}                 # Get session info
POST   /api/sessions/{id}/start           # Start/resume session
POST   /api/sessions/{id}/pause           # Pause session
POST   /api/sessions/{id}/terminate       # Stop session
POST   /api/sessions/{id}/restart         # Restart session (keep history)
POST   /api/sessions/{id}/reset           # Clear messages, fresh start
DELETE /api/sessions/{id}                 # Delete session
POST   /api/sessions/{id}/messages        # Send message
GET    /api/sessions/{id}/messages?limit=50&offset=0  # Get messages (paginated)
POST   /api/sessions/{id}/permission-mode # Set permission mode
PUT    /api/sessions/{id}/name            # Update session name
POST   /api/sessions/{id}/disconnect      # End SDK session, keep state
```

### Legion Endpoints (Multi-Agent)

```
GET    /api/legions/{id}/timeline?limit=100&offset=0  # Get comm timeline (paginated)
POST   /api/legions/{id}/comms            # Send comm (user to minion)
POST   /api/legions/{id}/minions          # Create minion
GET    /api/legions/{id}/hordes           # Load hordes
```

### Utility Endpoints

```
GET    /                                  # Serve index.html
GET    /health                            # Health check
GET    /api/filesystem/browse?path=/foo  # Browse directories
```

### WebSocket Endpoints

**UI WebSocket** (`/ws/ui`): Global UI state updates
- Receives: `sessions_list`, `state_change`, `project_updated`, `project_deleted`
- Sends: `ping`

**Session WebSocket** (`/ws/session/{id}`): Session-specific message streaming
- Receives: `message`, `permission_request`, `permission_response`, `tool_result`, `state_change`, `connection_established`
- Sends: `send_message`, `interrupt_session`, `permission_response`

**Legion WebSocket** (`/ws/legion/{id}`): Multi-agent communications
- Receives: `comm`, `minion_created`, `ping`
- Sends: `ping`

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
      │   ├─ legion/legion_coordinator.py (if multi-agent)
      │   │   ├─ legion/overseer_controller.py
      │   │   ├─ legion/comm_router.py
      │   │   ├─ legion/memory_manager.py
      │   │   └─ legion/legion_mcp_tools.py
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
- **Tool Handlers**: [TOOL_HANDLERS.md](./TOOL_HANDLERS.md) - Vue 3 tool handler development
- **Frontend Docs**: [frontend/README.md](./frontend/README.md) - Vue 3 architecture details
- **Migration Plan**: [frontend/MIGRATION_PLAN.md](./frontend/MIGRATION_PLAN.md) - Vue 3 migration status
- **Legion Proposal**: [legion_proposal/LEGION_PROPOSAL.md](./legion_proposal/LEGION_PROPOSAL.md) - Multi-agent design
- **MCP Tools**: [legion_proposal/MCP_TOOLS_ARCHITECTURE.md](./legion_proposal/MCP_TOOLS_ARCHITECTURE.md) - Inter-agent communication
- **Development Plan**: [DEVELOPMENT_PLAN.md](./DEVELOPMENT_PLAN.md) - Project roadmap
- **Claude Agent SDK**: https://github.com/anthropics/claude-agent-sdk

## Summary

Claude WebUI is a **production-ready web interface** for Claude Agent SDK with:

**Single-Agent Features**:
- Real-time streaming conversations with rich tool visualization
- Project/session hierarchy with drag-and-drop reordering
- Four permission modes with smart suggestions
- Orphaned tool detection and cleanup
- Persistent message storage (JSONL + JSON)
- Vue 3 + Pinia reactive UI
- Mobile-responsive design

**Multi-Agent Features (Legion)**:
- Minion creation and management
- Inter-agent communication (structured Comms)
- Timeline, Spy, Horde views for observability
- MCP tools for explicit minion actions
- Hierarchical organization (hordes)
- Memory & learning system (future)

**Developer Experience**:
- Comprehensive debugging tools (per-category logs)
- Complete REST + WebSocket API
- Extensible architecture (13+ tool handlers, easy to add more)
- Hot Module Replacement for instant feedback
- Vue DevTools integration
- Well-documented codebase

---

**important-instruction-reminders**

Do what has been asked; nothing more, nothing less.
NEVER create files unless they're absolutely necessary for achieving your goal.
ALWAYS prefer editing an existing file to creating a new one.
NEVER proactively create documentation files (*.md) or README files. Only create documentation files if explicitly requested by the User.
ALWAYS remove temporary test files after debugging is complete.

There is windows file modification bug in Claude Code. The workaround is: always use complete absolute Windows paths  with drive letters and backslashes for ALL file operations. Apply this rule going forward, not just for this  file.
