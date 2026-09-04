DO NOT SAY THAT THE USER IS CORRECT OR COMPLEMENT THEIR REQUEST. FORMAL, CONCISE COMMUNICATION SHOULD BE THE ONLY COMMENTARY PROVIDED.

# Development Requirements - REQUIRED
1. Server-side code is all in Python using `uv`, using commands like `uv run ...` or `uv add ...` or `uv run pytest ...` for executing, testing, linting, and managing dependencies.
2. **Frontend is Vue 3 + Pinia + Vite** (PRODUCTION): Frontend code is in `frontend/` directory. The `static/` directory has been sunset and should not be referenced for new development.
3. **Code Quality - Ruff Linting** (REQUIRED): All Python code must be linted with Ruff before committing. Run `uv run ruff check --fix src/` on changed files to auto-fix violations. New code must not introduce linting violations.

# High-Level Goal
We are building a web-based interface for Claude Agent SDK that provides:
1. **Single-Agent Mode**: Real-time streaming conversations with rich tool visualization
2. **Multi-Agent Mode (Legion)**: Teams of AI agents (minions) collaborating on complex tasks through structured communication

The SDK's streaming message responses are delivered via HTTP long-polling to a Vue 3 frontend which displays messages, tool executions, permissions, and multi-agent activity.

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
   uv run ruff check --fix src/web_server.py backend/session_manager.py

   # Or use git to find changed files
   uv run ruff check --fix $(git diff --name-only --diff-filter=AM | grep '\.py$')
   ```

2. **View Violations** (without fixing):
   ```bash
   uv run ruff check src/module_name.py
   ```

### Zero Violation Policy

The codebase maintains **zero ruff violations**. `uv run ruff check src/` must pass with no errors.

**Requirements**:
1. **All code must pass `uv run ruff check src/` with zero violations**
2. **No `# noqa` comments without PR-level justification**
3. **Run `uv run ruff check --fix <file>` before committing** to auto-fix safe violations
4. **CI enforcement**: Any violation fails the check

**Rule sets enabled**: E (pycodestyle errors), W (warnings), F (pyflakes), I (isort), N (pep8-naming), UP (pyupgrade), B (flake8-bugbear)

### Configuration
Ruff configuration is in `pyproject.toml`:
- Line length: 100 characters
- Target Python: 3.11+
- Unused imports in `__init__.py` are allowed

# Memory and Context Persistence - IMPORTANT

## Do NOT Use Built-in Auto-Memory for Session/Legion Workflows

Claude Code's built-in memory (`/memory` command, auto-memory in `~/.claude/`) is
**working-directory-specific**, not session-specific. This is unsuitable when:

- **Multiple minions share the same working directory** — memory cross-contaminates
- **Sessions persist across context resets** — working-directory memory doesn't travel with the session
- **Session archival/recovery** — built-in memory is not captured in session archives

### Disabling Auto-Memory

Sessions have a `disable_auto_memory` config flag (default: `False`). When enabled, the SDK
subprocess receives `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`, preventing Claude Code from reading
or writing to its working-directory memory store.

Enable this for multi-agent (Legion) workflows or any session where isolated context is required.

### Recommended Alternatives

1. **Session History Distillation** (#691): Distilled history markdown for context continuity
2. **Task/Agent Messages**: Structured messages within the session
3. **Custom Session State**: Context in session `state.json` (persists with archives)
4. **Archive-Based Recovery**: Restore context from archived session data

### When Built-in Memory IS Acceptable

Built-in memory is fine for single-agent, single-session use where no archival,
multi-agent isolation, or cross-reset persistence is needed.

# Environment Configuration - Background Call Suppression

The Claude Agent SDK and Claude Code CLI honor a number of environment variables that suppress
ambient/background API calls. Claude WebUI applies these at session-launch time based on the
`background_calls` section of `~/.config/cc_webui/config.json`.

## Defaults (fleet-mode)

All flags default to ON (suppression enabled) except `dont_inherit_env` (off — breaks Docker/proxy flows).

| Config Field                  | Env Var                                  | Default | Effect                                       |
|-------------------------------|------------------------------------------|---------|----------------------------------------------|
| `disable_auto_memory`         | `CLAUDE_CODE_DISABLE_AUTO_MEMORY`        | true    | Suppress working-directory auto-memory       |
| `disable_claudeai_mcp_servers`| `ENABLE_CLAUDEAI_MCP_SERVERS=false`      | true    | Disable built-in Claude AI MCP polling       |
| `disable_background_tasks`    | `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS`   | true    | Suppress CLI background ambient operations   |
| `disable_nonessential_traffic`| `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` | true  | Block ambient telemetry/metrics              |
| `disable_cron`                | `CLAUDE_CODE_DISABLE_CRON`               | true    | Disable CLI's bundled cron (≠ our scheduler) |
| `disable_feedback_survey`     | `CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY`    | true    | Suppress survey prompts                      |
| `disable_telemetry`           | `CLAUDE_CODE_ENABLE_TELEMETRY=0`         | true    | Explicitly opt out of telemetry              |
| `subprocess_env_scrub`        | `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB`       | false   | Strip credentials from spawned subprocesses (off by default — enabling this causes CLI to force permission_mode=default unless allowedTools declared explicitly) |
| `skip_version_check`          | `CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK`    | true    | Skip CLI version check (latency)             |
| `dont_inherit_env`            | `CLAUDE_CODE_DONT_INHERIT_ENV`           | false   | Block env inheritance (breaks Docker/proxy)  |

## Per-Session Override

Session config and templates retain their existing override fields:
- `auto_memory_mode = "claude"` → re-enables auto-memory for that session
- `enable_claudeai_mcp_servers = True` → re-enables Claude AI MCP for that session
- `env_scrub_enabled = True` → forces scrubbing (additive with global default)

For other suppression flags, per-session `extra_env` has the highest priority
and can set or unset any env var.

## Verifying Configuration

The injected env dict is logged at debug level by `claude_sdk.py` when `--debug-sdk` is enabled.
Inspect `data/logs/sdk_debug.log` for the `ClaudeAgentOptions: ...` line to see the final env
dict for each session.

## Auditing Future SDK Versions

When upgrading `claude-agent-sdk`, check for new env vars by:
1. Grepping the installed package for `os.environ`, `os.getenv`, `CLAUDE_*`, `ANTHROPIC_*`.
2. Checking the bundled CLI binary for new `CLAUDE_CODE_DISABLE_*` flags.
3. Updating `_BACKGROUND_CALL_ENV_MAP` in `backend/claude_sdk.py` and `BackgroundCallsConfig` in `backend/config_manager.py`.

# Frontend Architecture - Vue 3 + Pinia + Vite (PRODUCTION)

## Current Status

The Vue 3 migration is **complete** and in production use. The frontend has grown significantly beyond the original migration scope with 13 Pinia stores, 99+ Vue components, 22 tool handlers, and 9 composables.

**Documentation**: See [frontend/CLAUDE.md](./frontend/CLAUDE.md) for detailed frontend architecture.

## Frontend Structure

```
frontend/
├── src/
│   ├── stores/                    # 13 Pinia stores
│   │   ├── session.js             # Session CRUD, selection, deep linking
│   │   ├── project.js             # Project hierarchy, ordering
│   │   ├── message.js             # Messages, tool calls, orphaned detection
│   │   ├── polling.js             # HTTP long-polling (UI + session event streams)
│   │   ├── legion.js              # Multi-agent: comms, minions
│   │   ├── ui.js                  # Sidebar, modals, loading, responsive
│   │   ├── queue.js               # Per-session message queue
│   │   ├── schedule.js            # Per-legion cron schedules
│   │   ├── resource.js            # Per-session resources (images/files)
│   │   ├── diff.js                # Per-session git diff data
│   │   ├── task.js                # Per-session SDK task tracking
│   │   ├── mcp.js                 # MCP server state (active servers)
│   │   └── mcpConfig.js           # MCP server configuration CRUD
│   │
│   ├── composables/               # 9 reusable composition functions
│   │   ├── useToolResult.js       # Shared tool result extraction
│   │   ├── useToolStatus.js       # Tool status computation
│   │   ├── useAgentColor.js       # Per-agent color assignment
│   │   ├── useLongPress.js        # Long-press gesture handler
│   │   ├── useMarkdown.js         # Markdown rendering with DOMPurify
│   │   ├── useMermaid.js          # Mermaid diagram rendering
│   │   ├── useNotifications.js    # Sound/browser notifications
│   │   ├── useResourceImages.js   # Resource image helpers
│   │   └── useTTSReadAloud.js     # Text-to-speech / read-aloud
│   │
│   ├── components/
│   │   ├── layout/        (13)    # ProjectPillBar, AgentStrip, AgentChip, RightSidebar, DeletedAgentsModal, etc.
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
│   │   ├── tasks/         (6)     # TaskListPanel, DiffPanel, ResourceGallery, QueueSection, etc.
│   │   └── common/        (6)     # FolderBrowserModal, CommCard, DiffFullView, ResourceFullView, AttachmentChip, AuthPrompt
│   │
│   ├── router/                    # Vue Router: /, /project/:id, /session/:id, /session/:id/archive/:id
│   ├── utils/                     # API client, time formatting, tool summaries, file types, template vars
│   └── assets/                    # CSS (styles.css, tool-theme.css)
│
├── vite.config.js                 # Vite dev server + proxy + build config
├── index.html                     # Entry point
└── package.json                   # Dependencies (Vue 3.4, Pinia 2.1, Vite 7.1, Bootstrap 5.3)
```

## Development Workflow

### Running Frontend Dev Server

```bash
# Terminal 1: Backend (use port 8001 to avoid conflicts with production on 8000)
uv run python main.py --host 0.0.0.0 --debug-all --port 8001

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

1. **State Management**: 13 Pinia stores replace 135+ instance variables and dual Map+Array storage
2. **Automatic Reactivity**: No manual `renderSessions()` calls - Vue reactivity handles all UI updates
3. **Component Architecture**: 6767-line monolith split into 99+ focused, reusable components
4. **Event Listener Cleanup**: Automatic cleanup prevents memory leaks
5. **Developer Experience**: Instant HMR, Vue DevTools, TypeScript support, clear separation of concerns

## Pinia Stores (14 Stores)

For detailed store documentation, see [frontend/CLAUDE.md](./frontend/CLAUDE.md#pinia-stores-14).

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

### 4. Polling Store (`stores/polling.js`)
**Responsibility**: HTTP long-polling event streams (UI + per-session), outbound message dispatch

**State**:
- `uiConnected`, `uiRetryCount`: Global UI poll status
- `sessionConnected`, `sessionRetryCount`: Session poll status
- `sessionCursors` (Map): Cursor position per session for incremental polling

**Key Actions**:
- `startUIPolling()`: Long-poll loop against `/api/poll/ui`
- `startSessionPolling(sessionId)`: Long-poll loop against `/api/poll/session/{id}`
- `sendMessage()`: POST to `/api/sessions/{id}/messages`
- `sendPermissionResponse()`, `interruptSession()`: REST calls
- Exponential backoff on error (up to 30 seconds), page-visibility pause/resume

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
**Responsibility**: Per-legion cron schedules, execution history, real-time updates

### 9. Resource Store (`stores/resource.js`)
**Responsibility**: Per-session resources (images/files), gallery state, full view modal, text content cache

### 10. Diff Store (`stores/diff.js`)
**Responsibility**: Per-session git diff data, view modes (total/commits), per-file diff cache

### 11. Task Store (`stores/task.js`)
**Responsibility**: Per-session SDK task tracking (TaskCreate/Update/List/Get tool integration)

### 12. MCP Store (`stores/mcp.js`)
**Responsibility**: Active MCP server state per session

### 13. MCP Config Store (`stores/mcpConfig.js`)
**Responsibility**: MCP server configuration CRUD (STDIO/SSE/HTTP, OAuth 2.1)

## Vue Components (99+ files)

For detailed component documentation, see [frontend/CLAUDE.md](./frontend/CLAUDE.md#component-organization).

### Layout (13)
- `ProjectPillBar`, `ProjectPill`, `AgentStrip`, `AgentChip`, `StackedChip`, `ChipConnector`
- `HeaderRow1`, `AgentOverview`, `PeekCard`, `ConnectionIndicator`, `RightSidebar`, `RestartModal`, `DeletedAgentsModal`

### Configuration (12)
- `ConfigurationModal`, `GlobalConfigModal`, `QuickSettingsPanel`, `AdvancedSettingsPanel`
- `FeaturesTab`, `McpConfigTab`, `McpServerPanel`, `McpServerPicker`, `McpServerRow`
- `NotificationsTab`, `ReadAloudTab`, `PermissionPreviewModal`

### Session (7)
- `SessionView`, `SessionInfoBar`, `SessionStateStatusLine`, `SessionInfoModal`, `SessionManageModal`, `McpServerDetail`, `NoSessionSelected`

### Project (4)
- `ProjectOverview`, `ProjectStatusLine`, `ProjectCreateModal`, `ProjectEditModal`

### Messages (12)
- `MessageList`, `MessageItem`, `UserMessage`, `AssistantMessage`, `SystemMessage`, `ThinkingBlock`, `InputArea`
- `AttachmentList`, `CompactionEventGroup`, `SlashCommandDropdown`, `SubagentTimeline`, `TruncationBanner`

### Activity Timeline (6)
- `ActivityTimeline`, `PermissionPrompt`, `TimelineNode`, `TimelineDetail`, `TimelineSegment`, `TimelineOverflow`

### Tool Handlers (22)
**See [TOOL_HANDLERS.md](./TOOL_HANDLERS.md) for detailed documentation**

- **File**: `ReadToolHandler`, `EditToolHandler`, `WriteToolHandler`
- **Shell**: `BashToolHandler`, `ShellToolHandler`, `CommandToolHandler`
- **Search**: `SearchToolHandler` (Grep/Glob)
- **Web**: `WebToolHandler` (WebFetch/WebSearch)
- **Tasks**: `TodoToolHandler`, `TaskCreateToolHandler`, `TaskGetToolHandler`, `TaskListToolHandler`, `TaskUpdateToolHandler`
- **Interactive**: `AskUserQuestionToolHandler`
- **Skills**: `SkillToolHandler`, `SlashCommandToolHandler`
- **Agent/Comms**: `AgentToolHandler`, `SendCommToolHandler`
- **Other**: `ExitPlanModeToolHandler`, `NotebookEditToolHandler`
- **Shared**: `ToolSuccessMessage` (success banner), `BaseToolHandler` (fallback)

### Right Sidebar Panels (6)
- `TaskListPanel`, `TaskItem`, `DiffPanel`, `ResourceGallery`, `ImageGallery`, `QueueSection`

### Schedules (3)
- `SchedulePanel`, `ScheduleItem`, `ScheduleCreateModal`

### Legion (2)
- `MinionTreeNode`, `MinionViewModal`

### Common (6)
- `FolderBrowserModal`, `CommCard`, `DiffFullView`, `ResourceFullView`, `AttachmentChip`, `AuthPrompt`

## Naming Conventions

- **camelCase**: Variables, functions, computed properties
- **PascalCase**: Component names
- **kebab-case**: Component file names

# Backend Architecture - Two Processes (Frontend API + Backend)

**Issue #498**: what used to be one process is now two. The **Frontend API**
(`src/`, `main.py`) serves the browser, authenticates it, and relays every
domain request — it holds zero session-execution code and zero domain state.
The **Backend** (`backend/`, `backend/main.py`) is the actual control plane:
SessionCoordinator, ClaudeSDK, Legion, and everything else that used to live
directly in the old unified `src/`. `shared/` holds the handful of
zero-SDK-dependency utilities both tiers import (`event_queue.py`,
`logging_config.py`, `exception_handlers.py`). A static test
(`src/tests/test_import_boundary.py`) enforces that `src/` can never import
from `backend/` — this is a structural guarantee, not a convention.

Single-user self-hosted use (the default) auto-starts Backend as a child
process of Frontend on an OS-assigned free port with a freshly generated
credential (`src/backend_supervisor.py`) — no manual configuration required.
`--remote-backend-url`/`--remote-backend-token` point Frontend at a
manually-run or genuinely remote Backend instead.

## System Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                       Browser (Vue 3 Frontend)                       │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐             │
│  │ Pinia Stores │  │ Components   │  │ Vue Router   │             │
│  │ (13 stores)  │  │  (99+ files) │  │  (routing)   │             │
│  └──────────────┘  └──────────────┘  └──────────────┘             │
└────────┬────────────────────┬────────────────────┬──────────────────┘
         │                    │                    │
         │ HTTP long-polling + REST API (browser token auth)         │
         │                    │                    │
┌────────▼────────────────────▼────────────────────▼──────────────────┐
│              Frontend API (src/web_server.py, main.py)               │
│  Static SPA serving · browser AuthMiddleware · zero domain state     │
│  ┌──────────────────┐ ┌──────────────────┐ ┌───────────────────┐   │
│  │ Generic relay     │ │ poll_relay.py     │ │ backend_supervisor │   │
│  │ (src/routers/     │ │ (fans Backend's   │ │ .py (auto-start,   │   │
│  │  relay.py)         │ │  poll streams out │ │  readiness gate,   │   │
│  │                    │ │  to local queues) │ │  crash/restart)    │   │
│  └──────────────────┘ └──────────────────┘ └───────────────────┘   │
└────────┬─────────────────────────────────────────────────────────────┘
         │ backend-scoped bearer token (never the browser's own token)
┌────────▼─────────────────────────────────────────────────────────────┐
│               Backend (backend/web_server.py, backend/main.py)       │
│  Own AuthMiddleware · own /health + /ready · single-tenant control   │
│  plane — "the actual meat of the platform"                           │
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

## Frontend API File Organization (`src/`, `shared/`)

### Entry Point
- **`main.py`**: Arg parsing, logging config, resolves Backend connection
  (manual `--remote-backend-url`/`--remote-backend-token`, or auto-start via
  `BackendSupervisor`), uvicorn server start

### `src/web_server.py` - **Frontend Shell**
**Main Class**: `ClaudeWebUI`

**Responsibilities**:
- Static SPA serving, browser-facing `AuthMiddleware` (issue #728)
- Owns `backend_client` (httpx relay wrapper), `poll_relay` (background
  long-poll fan-out), optionally `backend_supervisor` (auto-start lifecycle)
- Dynamic OAuth callback path mirroring for shared MCP servers
  (`resync_oauth_callback_paths()` — Backend usually isn't independently
  publicly reachable, so custom callback paths relay through Frontend)
- **Zero session-execution code.** No SessionCoordinator, no domain state.

### `src/routers/` - **Frontend-Side Routes Only**
- **`core.py`**: `/`, `/health`, `/ready`, `/api/auth/check`
- **`poll.py`**: `/api/poll/ui`, `/api/poll/session/{id}` — reads LOCAL
  EventQueues populated by `poll_relay.py`, not direct coordinator callbacks
- **`config.py`**: `/api/config` — merged read (local `networking`/
  `backend_connection` + relayed Backend sections) / split write
- **`system.py`**: `POST /api/system/restart` only — pulls/switches Frontend's
  own repo and re-execs Frontend itself; in embedded mode also stops the
  current Backend child first so re-exec's normal startup auto-starts a fresh
  one from the same, already-pulled code (remote mode leaves Backend alone
  entirely). Backend's other `/api/system/*` routes (git-status, git-branches,
  git-commits, docker-status) stay relayed unchanged — in embedded mode
  they're the same repo either way. A blind relay of `restart` here (the
  behavior inherited by simply relocating the old unified router into
  `backend/routers/` in Phase 1) restarted Backend only, silently leaving
  Frontend running stale code forever — found in manual post-merge testing.
- **`relay.py`**: generic catch-all reverse-proxy for everything else under
  `/api/*`, plus the default `/oauth/callback` path. Registered last so the
  routers above take priority.

### `src/backend_client.py`, `src/backend_supervisor.py`, `src/poll_relay.py`, `src/frontend_config.py`
- **`backend_client.py`**: httpx wrapper — generic relay, JSON convenience
  methods, `/health`+`/ready` checks. Injects the backend-scoped token, never
  the browser's own.
- **`backend_supervisor.py`**: owns the auto-started local Backend subprocess
  — OS-assigned port, fresh token, readiness gating, crash/restart with
  backoff (capped, then marks degraded), clean SIGTERM-then-SIGKILL shutdown.
- **`poll_relay.py`**: one background long-poll task per stream (UI, each
  active session) continuously polling Backend's own poll endpoints and
  fanning results into local `EventQueue`s.
- **`frontend_config.py`**: Frontend's own `networking`/`backend_connection`
  config sections — reads/writes only its own keys in the shared
  `~/.config/cc_webui/config.json`, leaving Backend's sections untouched.

### `shared/` - **Used By Both Tiers**
- **`event_queue.py`**: `EventQueue` — cursor-based async event buffer
- **`logging_config.py`**: category-based debug logging setup
- **`exception_handlers.py`**: `handle_exceptions` decorator for route handlers

## Backend File Organization (`backend/`)

### Entry Point
- **`backend/main.py`**: Arg parsing (own token, own debug flags), logging
  config, `configure_webui_base_url()` (Docker/LiteLLM sidecar callback
  advertising — issue #498 Phase 4), uvicorn server start. Independently
  runnable (`python -m backend.main`) for manual/remote deployment.

### `backend/web_server.py` - **Backend Control-Plane Application**
**Main Class**: `BackendApp`

**Responsibilities**:
- Owns `SessionCoordinator` and every session-execution/control-plane manager
- Own `AuthMiddleware` validating the backend-scoped bearer token
- Own `/health` (liveness) + `/ready` (readiness — true once
  `SessionCoordinator` and its managers finish constructing)
- Own `/api/poll/ui`, `/api/poll/session/{id}` (`backend/routers/poll.py`) —
  the real EventQueues SessionCoordinator writes to
- Own `/api/config` (`backend/routers/config.py`) — every AppConfig section
  except `networking`/`backend_connection`
- `/api/internal/oauth-callback-paths` — exposes the dynamic custom-OAuth-
  callback-path registry so Frontend can mirror it
- Permission callback creation with asyncio.Future for async approval
- Session state change broadcasting

#### `backend/session_coordinator.py` (~5700 lines) - **Central Orchestrator**
**Main Class**: `SessionCoordinator`

**Responsibilities**:
- Ties together SessionManager, ProjectManager, ClaudeSDK, DataStorage, MessageProcessor
- Manages active SDK instances per session
- Handles ExitPlanMode detection and auto-reset
- Permission update tracking
- Tool use tracking for orphaned detection

**Key Methods** (line numbers drift often — use `grep -n "async def "` for current locations):
- `create_session()`: Session + storage + SDK initialization
- `start_session()`: Start/resume SDK, send client_launched message
- `send_message()`: Queue message to SDK, update processing state
- `interrupt_session()`: Stop active SDK processing
- `set_permission_mode()`: Runtime permission mode changes
- `create_ephemeral_session()`: Scheduled/ephemeral session creation (bundles
  a `SessionConfig` — a #498-era bug had this passing flat kwargs instead,
  fixed with regression tests, see git history)
- `_create_message_callback()`: Process SDK messages, detect completion

#### `backend/session_manager.py` - **Session Lifecycle & State**
**Main Classes**: `SessionState` (enum), `SessionInfo` (dataclass), `SessionManager`

**SessionState Values**:
- `CREATED`, `STARTING`, `ACTIVE`, `PAUSED`, `TERMINATING`, `TERMINATED`, `ERROR`

**SessionInfo Fields** (Extended for Legion):
- Standard: `session_id`, `state`, `working_directory`, `current_permission_mode`, `tools`, `model`, `name`, `order`
- Legion: `role`, `is_overseer`, `parent_overseer_id`, `child_minion_ids`, `capabilities`, `initialization_context`

**Key Methods**:
- `create_session()`: Create directory, persist state.json
- `start_session()`, `pause_session()`, `terminate_session()`: State transitions
- `update_processing_state()`: Track active processing
- `update_permission_mode()`: Runtime mode changes

#### `backend/project_manager.py` - **Project Hierarchy & Organization**
**Main Classes**: `ProjectInfo` (dataclass), `ProjectManager`

**ProjectInfo Fields** (Extended for Legion):
- Standard: `project_id`, `name`, `working_directory`, `session_ids`, `is_expanded`, `order`
- Legion: `is_multi_agent`, `minion_ids`, `max_concurrent_minions`, `active_minion_count`

**Key Methods**:
- `create_project()`: Create with order shifting
- `add_session_to_project()`, `remove_session_from_project()`
- `reorder_projects()`, `reorder_project_sessions()`

#### `backend/claude_sdk.py` - **SDK Wrapper & Message Queue**
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

#### `backend/message_parser.py` - **Message Normalization**
**Main Classes**: `MessageType` (enum), `ParsedMessage` (dataclass), `MessageProcessor`

**Responsibilities**:
- Convert between SDK objects, storage format, and event-queue format
- Unified processing for consistency

**Handlers**: `SystemMessageHandler`, `AssistantMessageHandler`, `UserMessageHandler`, `ResultMessageHandler`, `PermissionRequestHandler`, `PermissionResponseHandler`

**MessageProcessor Methods**:
- `process_message()`: Raw SDK message → ParsedMessage
- `prepare_for_storage()`: ParsedMessage → JSON-serializable dict
- `prepare_for_websocket()`: Format for the poll response

#### `backend/data_storage.py` - **Persistent Storage**
**Main Class**: `DataStorageManager`

**Files Managed**:
- `messages.jsonl`: Append-only message log
- `state.json`: Session metadata (managed by SessionManager)

**Key Methods**:
- `append_message()`, `read_messages()`, `get_message_count()`, `cleanup()`

#### Additional Backend Modules (`backend/`)

- **`application_service.py`**: Top-level service facade used by `web_server.py`
- **`config_manager.py`**: `~/.config/cc_webui/config.json` read/write —
  every `AppConfig` section except `networking`/`backend_connection`
  (Frontend-owned, see `src/frontend_config.py`). `save_config()` merges
  into the existing file rather than overwriting it, so it doesn't clobber
  Frontend-owned keys it doesn't know about.
- **`docker_utils.py`**: Docker image/mount configuration helpers for per-session isolation
- **`file_upload.py`**: Multipart file upload handling and session attachment storage
- **`history_distiller.py`**: Session history summarization for archival context continuity
- **`legion_system.py`**: Top-level Legion system wiring (coordinator + overseer + comm router)
- **`mcp_config_manager.py`**: Per-session MCP server configuration persistence (STDIO/SSE/HTTP, OAuth)
- **`mock_sdk.py`**: `MockClaudeSDK` — fixture replay for testing without live SDK calls
- **`oauth_manager.py`**: OAuth 2.1 token acquisition and storage
- **`oauth_refresh_manager.py`**: Background OAuth token refresh loop
- **`permission_service.py`**: Permission evaluation and decision routing
- **`session_config.py`**: Session configuration dataclass combining all per-session options;
  also home of `validate_and_normalize_working_directory()` (relocated here from the
  old unified `web_server.py`, since its only real callers are backend-side)
- **`task_utils.py`**: SDK task reconstruction helpers used by `task.js` store sync
- **`template_variables.py`**: Template variable substitution (e.g., `{{session_id}}`)
- **`timestamp_utils.py`**: Consistent timestamp parsing/formatting across storage and API

## Legion Multi-Agent System

### Legion Components (`backend/legion/`)

#### `backend/legion/legion_coordinator.py` - **Legion Lifecycle Management**
**Main Class**: `LegionCoordinator`

**Responsibilities**:
- Legion creation and deletion
- Fleet control (halt all, resume all, emergency halt)
- Central capability registry (MVP: keyword search)

#### `backend/legion/overseer_controller.py` - **Minion Management**
**Main Class**: `OverseerController`

**Responsibilities**:
- Minion lifecycle: create_minion_for_user(), spawn_minion(), dispose_minion()
- Enforce parent authority (only parent can dispose children)
- Memory transfer on disposal
- Capability registration

#### `backend/legion/comm_router.py` - **Inter-Agent Communication**
**Main Class**: `CommRouter`

**Responsibilities**:
- Convert between Comms and SDK Messages
- Route Comms to minions or user
- Handle interrupt priorities (HALT, PIVOT)
- Persist to timeline and minion logs
- Parse and validate #tag references

#### `backend/legion/memory_manager.py` - **Memory & Learning** (Planned)
**Main Class**: `MemoryManager`

**Responsibilities** (Future):
- Distill task completions into structured memories
- Reinforce memories based on outcome feedback
- Promote high-quality memories to long-term
- Transfer knowledge between minions
- Support minion forking with memory copy

#### `backend/legion/legion_mcp_tools.py` - **MCP Tools for Minions**
**Main Class**: `LegionMCPTools`

**Tools Provided**:
- **Communication**: `send_comm`
- **Lifecycle**: `spawn_minion`, `dispose_minion`
- **Discovery**: `list_minions`, `get_minion_info`, `search_capability`

**Integration**: Single instance per legion, attached to all minion SDK sessions

### Legion Data Models (`backend/models/legion_models.py`)

**Core Entities**:
- `Comm`: High-level message with routing info
- `CommType` enum: TASK, QUESTION, REPORT, INFO, HALT, PIVOT, THOUGHT, SPAWN, DISPOSE, SYSTEM
- `MemoryEntry`, `MinionMemory`: Knowledge management (future)

## Additional Backend Systems

### Queue System (`backend/queue_manager.py`, `backend/queue_processor.py`)

FIFO message queue with JSONL persistence for timed/sequential message delivery.

- **QueueManager**: State management with `QueueItem` dataclass (queue_id, session_id, content, reset_session, status, position). Storage via `queue.jsonl` with event replay on startup.
- **QueueProcessor**: Background asyncio task delivering queued messages with timing guards (`min_wait_seconds=10`, `min_idle_seconds=10`). Auto-starts sessions, handles pausing, polls `is_processing` without timeout.

### Cron Scheduler (`backend/legion/scheduler_service.py`)

Background service evaluating cron schedules every 30 seconds.

- **SchedulerService**: Creates/manages `Schedule` objects with croniter evaluation. Enqueues prompts via SessionCoordinator when due. Records `ScheduleExecution` history to JSONL. Auto-cancels on minion disposal.
- **Models**: `Schedule` (cron, next_run, status, failure tracking), `ScheduleExecution` (execution record), `ScheduleStatus` (ACTIVE/PAUSED/CANCELLED)

### Archive Manager (`backend/legion/archive_manager.py`)

Timestamped archival of minion session data before disposal.

- **ArchiveManager**: Copies messages.jsonl, state.json, and disposal metadata to `data/archives/minions/{minion_id}/{timestamp}/`. Returns `ArchiveResult` with archive path and file count.

### Permission Resolver (`backend/permission_resolver.py`)

Multi-source permission merge for effective permission preview.

- **`resolve_effective_permissions()`**: Parses permissions from user/project/local settings files and session-level allowed_tools. Returns list of `{permission, sources}` with source tracking.

### Resource MCP Tools (`backend/mcp/resource_mcp_tools.py`)

Session-scoped MCP server for agent resource display in the task panel.

- **ResourceMCPTools**: Creates per-session MCP servers with `register_resource` and `register_image` (deprecated alias) tools. Validates file path, extension, size (10MB max, 100 per session). Broadcasts `resource_registered` via WebSocket.

### Template Manager (`backend/template_manager.py`)

File-based minion template CRUD with slug naming.

- **TemplateManager**: Stores templates as JSON+MD file pairs in `data/templates/`. Supports slug-based filenames for human readability. Seeds default templates from `backend/default_templates/`. Migrates legacy UUID filenames on load.

### Skill Manager (`backend/skill_manager.py`)

Global skill deployment and symlink management.

- **SkillManager**: Syncs skills from `backend/default_skills/` to `~/.cc_webui/skills/`, creates symlinks in `~/.claude/skills/`. Detects conflicts with user files. Returns (added, updated, removed) counts.

## Data Directory Structure

Both tiers share one `data/` tree (same `--data-dir` value passed through to
an auto-started Backend by `backend_supervisor.py`). Frontend itself stores
no domain data — only its own logs.

```
data/
├── logs/                           # Frontend API's own logs
│   ├── coordinator.log             # (legacy name — pre-#498 unified process)
│   ├── error.log                   # All errors
│   ├── parser.log                  # Message parsing
│   ├── sdk_debug.log               # SDK integration
│   ├── storage.log                 # File operations
│   ├── polling.log                 # Poll transport signal logging
│   └── backend/                    # Backend subprocess's own logs (issue #498)
│       └── backend.log             # Backend's stdout/stderr, piped by backend_supervisor.py
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
        ├── short_term_memory.json  # (Future)
        └── long_term_memory.json   # (Future)
```

## API Endpoint Reference

For the complete endpoint reference with request/response details, see [.claude/API_REFERENCE.md](./.claude/API_REFERENCE.md).

**Summary**: 50+ REST endpoints across 10 domains (projects, sessions, files, resources, diffs, queue, legion, schedules, templates, system), owned by `backend/routers/` and reached through Frontend's generic relay (`src/routers/relay.py`) — plus `core.py`/`poll.py`/`config.py`/`system.py`'s handful of Frontend-side routes. 2 HTTP long-polling endpoints exist on **both** tiers post-#498 (`/api/poll/ui`, `/api/poll/session/{id}`) — Backend's are the real ones SessionCoordinator writes to; Frontend's read from local EventQueues kept in sync by `poll_relay.py`. The browser only ever talks to Frontend's copy.

## Message Flow Architecture

### SDK Message → Storage → Poll-Relay → Long-Poll Event Flow

```
1. ClaudeSDK (backend/) receives message from claude_agent_sdk
   ↓
2. ClaudeSDK._conversation_loop() extracts message data
   ↓
3. Calls message_callback (SessionCoordinator._create_message_callback, backend/web_server.py)
   ↓
4. MessageProcessor.process_message() normalizes to ParsedMessage
   ↓
5. SessionCoordinator stores via DataStorageManager.append_message()
   ├─ MessageProcessor.prepare_for_storage() converts to dict
   └─ Writes to messages.jsonl
   ↓
6. SessionCoordinator pushes event to session EventQueue (Backend's own, real one)
   └─ EventQueue.put() wakes any waiting poll request — served by
      backend/routers/poll.py
   ↓
7. Frontend's poll_relay.py background task (long-polling Backend's poll
   endpoint) receives the event, re-appends it to Frontend's LOCAL EventQueue
   ↓
8. Frontend's own poll.py (src/routers/poll.py) serves it from that local
   queue to the browser's long-poll request — same {events, next_cursor}
   shape as before the #498 split
   ↓
9. Frontend poll loop (browser) receives it, updates Pinia stores reactively
```

### User Message → SDK Flow

```
1. User types in frontend, clicks Send
   ↓
2. Frontend POST /api/sessions/{id}/messages {message: "..."}
   ↓
3. src/routers/relay.py's generic catch-all forwards method/path/query/body/
   headers (browser token stripped, backend-scoped token injected) to Backend
   ↓
4. backend/routers/sessions.py REST handler receives the relayed request
   ↓
5. Calls SessionCoordinator.send_message(session_id, content)
   ↓
6. SessionCoordinator marks session.is_processing = True
   ↓
7. ClaudeSDK.send_message() enqueues to _message_queue
   ↓
8. ClaudeSDK._conversation_loop() picks up from queue
   ↓
9. Sends to claude_agent_sdk via query(prompt=message)
   ↓
10. SDK streams back responses (loop back to SDK Message flow above)
   ↓
11. On ResultMessage, SessionCoordinator sets is_processing = False
```

### Permission Flow

```
1. SDK needs permission for tool (e.g., Edit, Write) — inside Backend
   ↓
2. SDK calls permission_callback() from backend/web_server.py
   ↓
3. backend/web_server.py stores permission request message (with suggestions if any)
   ↓
4. backend/web_server.py pushes permission_request event to session EventQueue
   ↓
5. Frontend's poll_relay.py picks it up (same relay chain as any other event) and
   fans it into Frontend's local queue; browser's poll loop displays the
   permission modal with suggestions
   ↓
6. User clicks Allow/Deny (optionally applies suggestions)
   ↓
7. Frontend POST /api/sessions/{id}/permission/{request_id} — relayed via
   src/routers/relay.py (this route lives in backend/routers/session_runtime.py
   now, relocated from the old unified core.py since it acts on backend-owned
   state: PermissionService.pending_permissions)
   ↓
8. backend/web_server.py resolves asyncio.Future with user's decision
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
→ **Solution**: `backend/session_coordinator.py`'s `start_session()` + enable `--debug-sdk` on Backend

**Problem**: Long-poll events not arriving
→ **Solution**: Check `backend/routers/poll.py` (the real EventQueues) and
`src/poll_relay.py` (the fan-out mechanism) and `src/routers/poll.py` (what the
browser actually talks to); enable `--debug-polling`

**Problem**: Messages not persisting
→ **Solution**: `backend/data_storage.py`'s `append_message()` + enable `--debug-storage` on Backend

**Problem**: Permission callback not triggering
→ **Solution**: `backend/web_server.py`'s `_create_permission_callback()` + enable `--debug-permissions` on Backend

**Problem**: Add new REST endpoint
→ **Solution**: Add route in the appropriate `backend/routers/*.py` file, register it
in `backend/routers/__init__.py`'s `register_all()`. Frontend's generic relay
(`src/routers/relay.py`) picks it up automatically — no Frontend-side change needed
unless the route needs special-casing (like `/api/config` or the poll endpoints).

**Problem**: Frontend<->Backend relay not working
→ **Solution**: Check `src/backend_client.py` (the relay client), confirm Backend
is actually reachable (`curl <backend_url>/health`), check
`src/backend_supervisor.py`'s logs (`data/logs/backend/backend.log`) if auto-started

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
main.py (Frontend API)
  └─ src/web_server.py (ClaudeWebUI)
      ├─ src/backend_client.py       — relay to Backend
      ├─ src/backend_supervisor.py   — auto-start Backend (if no --remote-backend-url)
      ├─ src/poll_relay.py           — fan Backend's poll streams into local queues
      ├─ src/frontend_config.py      — networking/backend_connection config sections
      └─ shared/ (logging_config.py, exception_handlers.py, event_queue.py)

backend/main.py (Backend)
  └─ backend/web_server.py (BackendApp)
      ├─ backend/session_coordinator.py
      │   ├─ backend/session_manager.py
      │   ├─ backend/project_manager.py
      │   ├─ backend/claude_sdk.py
      │   │   ├─ backend/data_storage.py
      │   │   ├─ backend/message_parser.py
      │   │   └─ shared/logging_config.py
      │   ├─ backend/data_storage.py
      │   ├─ backend/message_parser.py
      │   ├─ backend/queue_manager.py
      │   ├─ backend/queue_processor.py
      │   ├─ backend/template_manager.py
      │   ├─ backend/skill_manager.py
      │   ├─ backend/permission_resolver.py
      │   ├─ backend/mcp/resource_mcp_tools.py
      │   ├─ backend/legion/legion_coordinator.py (if multi-agent)
      │   │   ├─ backend/legion/overseer_controller.py
      │   │   ├─ backend/legion/comm_router.py
      │   │   ├─ backend/legion/memory_manager.py
      │   │   ├─ backend/legion/legion_mcp_tools.py
      │   │   ├─ backend/legion/scheduler_service.py
      │   │   └─ backend/legion/archive_manager.py
      │   └─ shared/logging_config.py
      ├─ backend/message_parser.py
      └─ shared/logging_config.py
```

## Testing & Development Patterns

### Standard Testing Configuration

**CRITICAL**: Always use port 8001 (Frontend API) for testing to avoid
conflicts with production on port 8000. Backend's port is allocated
dynamically — do not hardcode a second fixed port (see
`.claude/skills/custom-environment-setup/SKILL.md` for the full explanation
of why, tied to issue #1825).

```bash
# Test run — auto-starts Backend, no second port to manage
uv run python main.py --host 0.0.0.0 --debug-all --data-dir test_data --port 8001

# Production run
uv run python main.py --port 8000
```

### Process Management - REQUIRED PATTERN

**CRITICAL**: Always kill processes by PID, never by name. Killing the
Frontend API gracefully (SIGTERM, not `-9`) cascades to its auto-started
Backend child automatically (`src/backend_supervisor.py`'s shutdown
handling) — you should not need to separately find and kill Backend's PID
in the normal case. If you do need Backend's PID directly (e.g. after a
force-kill of Frontend), find it by process name since its port isn't
predictable: `ps aux | grep "backend.main" | grep -v grep`.

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
# or: kill -9 <PID>  — if you must, then also check for an orphaned Backend
# process (ps aux | grep backend.main) since -9 skips the graceful cascade
```

### Running Tests

```bash
# Both suites — the split is real, they cover different processes
uv run pytest backend/tests/ src/tests/ -v

# Specific test file
uv run pytest backend/tests/test_session_manager.py -v
uv run pytest src/tests/test_backend_supervisor.py -v

# With coverage
uv run pytest backend/tests/ src/tests/ --cov=backend --cov=src --cov-report=html

# The mandatory live two-process E2E test (issue #498's hard acceptance gate —
# not marked slow, always runs): src/tests/test_live_two_process_e2e.py
```

### Frontend Development

```bash
# Terminal 1: Frontend API (auto-starts Backend as a child process)
uv run python main.py --host 0.0.0.0 --port 8001 --debug-all

# Terminal 2: Frontend dev server
cd frontend
VITE_BACKEND_PORT=8001 npm run dev  # http://localhost:5173 — proxies to the Frontend API port, not Backend's
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
- HTTP long-polling response is asynchronous
- Future bridges sync callback ↔ async REST response

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

**Why split Frontend API and Backend into two processes? (issue #498)**
- A prior attempt at this split (#498/#499, reverted) used an in-process
  LOCAL/REMOTE mode flag on one shared codebase — nothing physically prevented
  a code path from quietly reaching session-execution internals in "frontend
  mode," and gaps (config mutation, watchdog visibility, Legion topology) kept
  surfacing because partial correctness was shippable
- Import-enforced structural separation (`src/tests/test_import_boundary.py`)
  makes "zero in-process execution in the Frontend API" provable by
  construction instead of relying on code-review vigilance
- Single-user self-hosted use isn't a special case — it's the same relay
  mechanism with Backend auto-started locally instead of pointed at manually,
  so there's exactly one code path to get right, not two

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
- **API Reference**: [.claude/API_REFERENCE.md](./.claude/API_REFERENCE.md) - All REST + long-polling endpoints
- **Tool Handlers**: [TOOL_HANDLERS.md](./TOOL_HANDLERS.md) - Vue 3 tool handler development
- **Legion Proposal**: [legion_proposal/LEGION_PROPOSAL.md](./legion_proposal/LEGION_PROPOSAL.md) - Multi-agent design
- **MCP Tools**: [legion_proposal/MCP_TOOLS_ARCHITECTURE.md](./legion_proposal/MCP_TOOLS_ARCHITECTURE.md) - Inter-agent communication
- **Development Plan**: [DEVELOPMENT_PLAN.md](./DEVELOPMENT_PLAN.md) - Project roadmap
- **Claude Agent SDK**: https://github.com/anthropics/claude-agent-sdk

## Summary

Claude WebUI is a **production-ready web interface** for Claude Agent SDK with:

**Single-Agent Features**:
- Real-time streaming conversations with rich tool visualization (22 tool handlers)
- Project/session hierarchy with drag-and-drop reordering
- Four permission modes with smart suggestions and permission preview
- Message queue with timed delivery and auto-start
- Resource gallery (images and files from agents)
- Git diff viewer with per-commit and aggregate views
- Orphaned tool detection and cleanup
- Persistent message storage (JSONL + JSON)
- Vue 3 + Pinia reactive UI (13 stores, 99+ components)
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
- 50+ REST endpoints + HTTP long-polling event streams
- Extensible architecture (22 tool handlers, easy to add more)
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
