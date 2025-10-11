DO NOT SAY THAT THE USER IS CORRECT OR COMPLEMENT THEIR REQUEST. FORMAL, CONCISE COMMUNICATION SHOULD BE THE ONLY COMMENTARY PROVIDED.

# Development Requirements - REQUIRED
1. Server-side code is all in python using `uv`, using commands like `uv run ...` or `uv add ...` or `uv run pytest ...` and others for executing, testing, linting, and managing dependencies.
2. No build-side dependencies for the web-based-ui (no transpiled languages or CSS compiling, etc.)

# High-Level Goal
We are building a tool that integrates with the Claude Agent SDK (formerly Claude Code SDK) to provide streaming conversations through a web-based interface. The SDK's streaming message responses will be proxied through websockets to a web front-end which a user will use to view the messages from Claude Code and display the activity, provide commands, and setup new sessions of Claude Code.

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

## Session Management
- SDK handles session management internally
- Use `ClaudeAgentOptions` to configure per-session settings
- Sessions are maintained through the async iterator lifecycle
- Generate unique identifiers for WebUI session tracking

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
    model="claude-3-sonnet-20241022"     # Model selection
)
```

## CRITICAL PARAMETER MAPPING
- Use `cwd` NOT `working_directory`
- Use `permission_mode` NOT `permissions`
- Use `allowed_tools` NOT `tools`
- Use `prompt=message` NOT positional argument in query()
- Always import from `claude_agent_sdk` NOT `claude_code_sdk`
- Use `ClaudeAgentOptions` NOT `ClaudeCodeOptions`

## System Prompt Configuration (CRITICAL - NEW in v0.1.0)
- SDK no longer uses Claude Code system prompt by default
- Must explicitly specify preset to get Claude Code behavior:
  ```python
  system_prompt={
      "type": "preset",
      "preset": "claude_code"
  }
  ```
- For custom prompts, pass string directly: `system_prompt="Custom prompt"`

## Settings Sources Configuration (CRITICAL - NEW in v0.1.0)
- SDK no longer loads settings from filesystem by default
- Must explicitly specify sources to restore previous behavior:
  ```python
  setting_sources=["user", "project", "local"]
  ```
- Available sources:
  - `"user"`: Load from `~/.claude/settings.json`
  - `"project"`: Load from `.claude/settings.json`
  - `"local"`: Load from `.claude/settings.local.json`

## Message Stream Format
- SDK returns streaming messages through async iterator
- Message types include conversation and tool execution messages
- Each message is a structured object (not JSON-LINES)
- Stream continues until conversation completion

## Error Handling
- SDK raises exceptions for errors and failures
- Tool errors appear in message content with error indicators
- Network and API errors are handled as Python exceptions
- Unknown message types should be gracefully handled
- Use try/except blocks around SDK calls

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

# Frontend Architecture - JavaScript Code Organization

## Directory Structure
```
static/
├── core/                      # Core infrastructure modules
│   ├── logger.js             # Logging utility (Logger object)
│   ├── constants.js          # Application constants (STATUS_COLORS, WEBSOCKET_CONFIG, SIDEBAR_CONFIG)
│   ├── api-client.js         # API communication (APIClient class)
│   └── project-manager.js    # Project operations (ProjectManager class)
│
├── tools/                     # Tool call system
│   ├── tool-call-manager.js  # Tool state management (ToolCallManager class)
│   ├── tool-handler-registry.js  # Handler lookup (ToolHandlerRegistry class)
│   │
│   └── handlers/              # Tool-specific UI renderers
│       ├── base-handler.js    # DefaultToolHandler (fallback renderer)
│       ├── read-handler.js    # ReadToolHandler (file reading with preview)
│       ├── edit-handlers.js   # EditToolHandler, MultiEditToolHandler (diff views)
│       ├── write-handler.js   # WriteToolHandler (file creation preview)
│       ├── todo-handler.js    # TodoWriteToolHandler (task checklist)
│       ├── search-handlers.js # GrepToolHandler, GlobToolHandler (search results)
│       ├── web-handlers.js    # WebFetchToolHandler, WebSearchToolHandler
│       ├── bash-handlers.js   # BashToolHandler, BashOutputToolHandler, KillShellToolHandler
│       └── misc-handlers.js   # TaskToolHandler, ExitPlanModeToolHandler
│
├── app.js                     # Main application (ClaudeWebUI class)
├── index.html                 # HTML template with script load order
└── styles.css                 # Application styles
```

## Module Loading Order (CRITICAL)
Scripts must load in this exact order in `index.html`:

1. **Core Modules** (no dependencies)
   - `core/logger.js` - Used by all other modules
   - `core/constants.js` - Used by app.js
   - `core/api-client.js` - Used by app.js
   - `core/project-manager.js` - Used by app.js

2. **Tool System** (depends on Logger)
   - `tools/tool-call-manager.js` - Used by app.js
   - `tools/tool-handler-registry.js` - Used by app.js

3. **Tool Handlers** (depend on Logger, used by ToolHandlerRegistry)
   - `tools/handlers/base-handler.js` - Base class for handlers
   - All other handler files (order doesn't matter)

4. **Main Application** (depends on all above)
   - `app.js` - ClaudeWebUI orchestrator

## Key Classes and Their Locations

### Core Infrastructure
- **Logger** (`core/logger.js`)
  - Methods: `debug()`, `info()`, `warn()`, `error()`
  - Used everywhere for consistent logging

- **APIClient** (`core/api-client.js`)
  - Methods: `request()`, `get()`, `post()`, `put()`, `delete()`
  - Handles all backend communication

- **ProjectManager** (`core/project-manager.js`)
  - Methods: `loadProjects()`, `createProject()`, `updateProject()`, `deleteProject()`, `toggleExpansion()`, `reorderProjects()`
  - Manages project CRUD operations

### Tool System
- **ToolCallManager** (`tools/tool-call-manager.js`)
  - Tracks tool call lifecycle: pending → permission_required → executing → completed/error
  - Methods: `handleToolUse()`, `handlePermissionRequest()`, `handlePermissionResponse()`, `handleToolResult()`

- **ToolHandlerRegistry** (`tools/tool-handler-registry.js`)
  - Maps tool names to custom renderers
  - Methods: `registerHandler()`, `getHandler()`, `hasHandler()`

### Tool Handlers (all in `tools/handlers/`)
Each handler provides:
- `renderParameters(toolCall, escapeHtmlFn)` - Display tool inputs
- `renderResult(toolCall, escapeHtmlFn)` - Display tool outputs
- `getCollapsedSummary(toolCall)` - Generate collapsed view text

To add a new tool handler:
1. Create new file in `tools/handlers/`
2. Implement handler class with render methods
3. Add script tag to `index.html` (before `app.js`)
4. Register in `ClaudeWebUI.initializeToolHandlers()` in `app.js`

### Main Application
- **ClaudeWebUI** (`app.js`)
  - Main orchestrator class (3685 lines)
  - Manages: WebSockets, sessions, messages, UI state, drag-drop, modals, sidebar
  - Uses all core modules and tool system components

## Finding Functionality

**Tool rendering logic** → `tools/handlers/*.js`
**Tool call state** → `tools/tool-call-manager.js`
**API calls** → `core/api-client.js` or direct fetch in `app.js`
**Project operations** → `core/project-manager.js`
**WebSocket logic** → `app.js` (ClaudeWebUI methods: `connectUIWebSocket()`, `connectSessionWebSocket()`)
**Message processing** → `app.js` (ClaudeWebUI methods: `processMessage()`, `handleToolRelatedMessage()`)
**Session management** → `app.js` (ClaudeWebUI methods: `selectSession()`, `createSession()`, `loadSessions()`)
**UI rendering** → `app.js` (ClaudeWebUI methods: `renderSessions()`, `createProjectElement()`, `createSessionElement()`)
**Logging** → `core/logger.js` (use `Logger.debug()`, `Logger.info()`, etc.)

## Common Patterns

### Adding a new tool handler
```javascript
// In tools/handlers/my-tool-handler.js
class MyToolHandler {
    renderParameters(toolCall, escapeHtmlFn) {
        return `<div>...</div>`;
    }

    renderResult(toolCall, escapeHtmlFn) {
        return `<div>...</div>`;
    }

    getCollapsedSummary(toolCall) {
        return `${icon} ${toolCall.name} - ${status}`;
    }
}
```

Then register in `app.js`:
```javascript
this.toolHandlerRegistry.registerHandler('MyTool', new MyToolHandler());
```

### Using Logger
```javascript
Logger.debug('CATEGORY', 'Debug message', optionalDataObject);
Logger.info('CATEGORY', 'Info message');
Logger.warn('CATEGORY', 'Warning message', errorObject);
Logger.error('CATEGORY', 'Error message', errorObject);
```

### Making API calls
```javascript
// Using APIClient (preferred in new code)
this.apiClient.get('/api/sessions');
this.apiClient.post('/api/sessions', {data});

// Using apiRequest (existing pattern in ClaudeWebUI)
await this.apiRequest('/api/sessions');
```

# Backend Architecture - Python Server Organization

## System Architecture Overview
```
┌─────────────────────────────────────────────────────────────────────┐
│                         Web Browser (Frontend)                       │
│  static/app.js + tools/ + core/ modules (see Frontend Architecture) │
└────────────────┬────────────────────────────────────────────────────┘
                 │ WebSocket + REST API
┌────────────────▼────────────────────────────────────────────────────┐
│                    FastAPI Server (src/web_server.py)                │
│  • REST endpoints for CRUD operations                                │
│  • WebSocket managers (UI + Session-specific)                        │
│  • Permission callback coordination                                  │
└────────┬───────────────────────────────────────┬────────────────────┘
         │                                       │
┌────────▼────────────────────────┐   ┌─────────▼──────────────────────┐
│  SessionCoordinator             │   │  MessageProcessor              │
│  (src/session_coordinator.py)   │   │  (src/message_parser.py)       │
│  • Orchestrates all components  │   │  • Parses SDK messages         │
│  • Manages SDK lifecycle         │   │  • Formats for storage/WS      │
│  • Routes callbacks              │   │  • Handles all message types   │
└────┬──────────┬──────────┬──────┘   └────────────────────────────────┘
     │          │          │
┌────▼─────┐ ┌─▼─────────┐ ┌▼──────────────┐
│SessionMgr│ │ProjectMgr │ │ClaudeSDK      │
│(session_ │ │(project_  │ │(claude_sdk.py)│
│manager.py)│ │manager.py)│ │• SDK wrapper  │
│• State   │ │• Projects │ │• Queue msgs   │
│• Persist │ │• Hierarchy│ │• Stream proc  │
└──────────┘ └───────────┘ └───────────────┘
                              │
                    ┌─────────▼──────────────────┐
                    │  Claude Agent SDK          │
                    │  (claude_agent_sdk)        │
                    │  • External package        │
                    │  • Streams Claude responses│
                    └────────────────────────────┘
```

## Backend File Organization & Responsibilities

### Entry Point
- **[main.py](./main.py)**: Application entry point
  - Parses CLI arguments (--host, --port, --debug-* flags)
  - Configures logging system
  - Creates FastAPI app and starts uvicorn server
  - Debug flags: `--debug-websocket`, `--debug-sdk`, `--debug-permissions`, `--debug-storage`, `--debug-parser`, `--debug-error-handler`, `--debug-all`

### Core Backend Modules (src/)

#### [src/web_server.py](./src/web_server.py) (1230 lines) - **HTTP + WebSocket Server**
**Main Class**: `ClaudeWebUI`
- **REST API Endpoints** (see API Reference section below)
- **WebSocket Managers**:
  - `UIWebSocketManager`: Broadcasts project/session state changes to all connected clients
  - `WebSocketManager`: Handles session-specific message streaming
- **Key Methods**:
  - `_create_permission_callback()`: Creates async callback for tool permissions (lines 984-1185)
  - `_create_message_callback()`: Wraps messages for WebSocket broadcast (lines 931-960)
  - `_on_state_change()`: Broadcasts session state changes to UI (lines 962-982)
- **Permission Flow**: User decision via WebSocket → Future.set_result() → SDK receives response

#### [src/session_coordinator.py](./src/session_coordinator.py) (1050 lines) - **Central Orchestrator**
**Main Class**: `SessionCoordinator`
- **Purpose**: Ties together SessionManager, ProjectManager, ClaudeSDK, DataStorage, and MessageProcessor
- **Manages**:
  - Active SDK instances per session (`_active_sdks` dict)
  - Storage managers per session (`_storage_managers` dict)
  - Message and error callbacks
  - ExitPlanMode detection and auto-reset to default mode
  - Permission update tracking
- **Key Methods**:
  - `create_session()`: Creates session, initializes storage, creates SDK instance (lines 100-171)
  - `start_session()`: Starts/resumes SDK, sends client_launched message (lines 177-289)
  - `send_message()`: Queues message to SDK, updates processing state (lines 538-572)
  - `interrupt_session()`: Stops active SDK processing (lines 447-492)
  - `set_permission_mode()`: Changes runtime permission mode (lines 494-536)
  - `_create_message_callback()`: Processes SDK messages, detects completion, manages state (lines 741-818)
  - `_send_client_launched_message()`: System message for SDK startup (lines 881-914)
- **Message Flow**: SDK → callback → MessageProcessor → storage + WebSocket

#### [src/session_manager.py](./src/session_manager.py) - **Session Lifecycle & State**
**Main Classes**: `SessionState` (enum), `SessionInfo` (dataclass), `SessionManager`
- **SessionState Values**: `CREATED`, `STARTING`, `ACTIVE`, `PAUSED`, `TERMINATING`, `TERMINATED`, `ERROR`
- **SessionInfo Fields**:
  - `session_id`, `state`, `working_directory`, `current_permission_mode`
  - `system_prompt`, `tools`, `model`, `is_processing`, `name`, `order`
  - `claude_code_session_id`: SDK's internal session ID for resume
  - `error_message`: User-friendly error text when state=ERROR
- **Key Methods**:
  - `create_session()`: Creates session directory, persists state.json
  - `start_session()`, `pause_session()`, `terminate_session()`: State transitions
  - `update_processing_state()`: Tracks if session is actively processing user input
  - `update_permission_mode()`: Updates current permission mode
  - `_persist_session_state()`: Saves state.json to disk
- **Persistence**: Each session has `data/sessions/{uuid}/state.json`

#### [src/project_manager.py](./src/project_manager.py) - **Project Hierarchy & Organization**
**Main Classes**: `ProjectInfo` (dataclass), `ProjectManager`
- **ProjectInfo Fields**:
  - `project_id`, `name`, `working_directory` (IMMUTABLE after creation)
  - `session_ids`: Ordered list of child sessions
  - `is_expanded`: UI expansion state (persisted)
  - `order`: Display order among projects
- **Key Methods**:
  - `create_project()`: Creates project, shifts existing projects down in order
  - `add_session_to_project()`: Adds session ID to project's session_ids list
  - `remove_session_from_project()`: Removes session from project
  - `reorder_projects()`: Changes project display order
  - `reorder_project_sessions()`: Changes session order within a project
- **Persistence**: Each project has `data/projects/{uuid}/state.json`

#### [src/claude_sdk.py](./src/claude_sdk.py) - **SDK Wrapper & Message Queue**
**Main Class**: `ClaudeSDK`
- **Purpose**: Wraps Claude Agent SDK, manages conversation queue, handles streaming
- **Key Components**:
  - `_message_queue`: Async queue for user messages
  - `_conversation_task`: Background task processing queue
  - `_sdk_client`: ClaudeSDKClient instance (context manager)
  - `_sdk_options`: ClaudeAgentOptions configuration
- **Key Methods**:
  - `start()`: Initializes SDK client, starts conversation loop
  - `send_message()`: Enqueues message for processing
  - `interrupt_session()`: Sets interrupt flag, stops current processing
  - `set_permission_mode()`: Sends permission mode change to SDK
  - `_conversation_loop()`: Processes queue, streams SDK responses (main async loop)
- **Message Processing**: Extracts SDK objects, passes to MessageProcessor, stores and broadcasts

#### [src/message_parser.py](./src/message_parser.py) - **Message Normalization**
**Main Classes**: `MessageType` (enum), `ParsedMessage` (dataclass), `MessageHandler` (ABC), `MessageProcessor`
- **Purpose**: Converts between SDK objects, storage format, and WebSocket format
- **Handlers**: `SystemMessageHandler`, `AssistantMessageHandler`, `UserMessageHandler`, `ResultMessageHandler`, `PermissionRequestHandler`, `PermissionResponseHandler`
- **MessageProcessor Methods**:
  - `process_message()`: Takes raw SDK message, returns ParsedMessage
  - `prepare_for_storage()`: Converts ParsedMessage to JSON-serializable dict
  - `prepare_for_websocket()`: Formats for frontend consumption
- **Flow**: SDK object → parse() → ParsedMessage → prepare_for_storage/websocket() → dict

#### [src/data_storage.py](./src/data_storage.py) - **Persistent Storage**
**Main Class**: `DataStorageManager`
- **Files Managed**:
  - `messages.jsonl`: One JSON object per line (append-only log)
  - `history.json`: Command history (array of objects)
  - `state.json`: Session metadata (managed by SessionManager)
- **Key Methods**:
  - `append_message()`: Adds message to JSONL file
  - `read_messages()`: Paginated message retrieval (limit/offset)
  - `get_message_count()`: Total message count
  - `cleanup()`: Closes file handles, releases resources
- **Note**: Corruption detection disabled to prevent startup issues

#### [src/error_handler.py](./src/error_handler.py) - **Error Processing**
- Categorizes and formats error messages for user display
- Extracts meaningful errors from SDK failures

#### [src/logging_config.py](./src/logging_config.py) - **Structured Logging**
- Configures per-category loggers (SDK, PERMISSIONS, STORAGE, WS_LIFECYCLE, PARSER, COORDINATOR)
- Outputs to `data/logs/{category}.log` files
- `get_logger(name, category)`: Returns specialized logger instance

### Supporting Modules

#### [src/sdk_discovery_tool.py](./src/sdk_discovery_tool.py) - **SDK API Inspector**
- Introspects claude_agent_sdk module structure
- Generates reports of available classes, functions, and signatures
- Used for SDK version compatibility testing

## Data Folder Structure

```
data/
├── logs/                           # Structured debug logs (one per category)
│   ├── coordinator.log            # SessionCoordinator actions
│   ├── error.log                  # All errors
│   ├── parser.log                 # Message parsing details
│   ├── sdk_debug.log              # SDK integration debugging
│   ├── storage.log                # File operations
│   └── websocket_debug.log        # WebSocket lifecycle events
│
├── projects/{uuid}/                # One folder per project
│   └── state.json                 # ProjectInfo serialized
│       Fields: project_id, name, working_directory, session_ids[],
│               is_expanded, created_at, updated_at, order
│
├── sessions/{uuid}/                # One folder per session
│   ├── state.json                 # SessionInfo serialized
│   │   Fields: session_id, state, working_directory, current_permission_mode,
│   │           system_prompt, tools[], model, is_processing, name, order,
│   │           claude_code_session_id, error_message
│   ├── messages.jsonl             # Append-only message log (one JSON per line)
│   │   Each line: {type, content, timestamp, metadata:{...}, session_id}
│   └── history.json               # Command history array
│
└── sdk_discovery/                  # SDK introspection reports
    ├── sdk_discovery_summary_{timestamp}.json
    └── sdk_discovery_detailed_{timestamp}.json
```

**Key Points**:
- UUIDs in folder names are variable data (don't document specific UUIDs)
- `state.json` files are authoritative source of truth for entity state
- `messages.jsonl` is append-only (never modified, only appended)
- Projects contain reference to sessions (via `session_ids` list)
- Sessions reference their parent working_directory (from project)

## API Endpoint Reference

### Project Endpoints
- `POST /api/projects` - Create new project (body: {name, working_directory})
- `GET /api/projects` - List all projects with sessions
- `GET /api/projects/{id}` - Get specific project with its sessions
- `PUT /api/projects/{id}` - Update project name/expansion state (body: {name?, is_expanded?})
- `DELETE /api/projects/{id}` - Delete project and all its sessions
- `PUT /api/projects/{id}/toggle-expansion` - Toggle project expansion state
- `PUT /api/projects/reorder` - Reorder projects (body: {project_ids: [...]})
- `PUT /api/projects/{id}/sessions/reorder` - Reorder sessions in project (body: {session_ids: [...]})

### Session Endpoints
- `POST /api/sessions` - Create new session in project (body: {project_id, permission_mode, system_prompt?, tools[], model?, name?})
- `GET /api/sessions` - List all sessions with state
- `GET /api/sessions/{id}` - Get session info (includes session, sdk, storage metadata)
- `POST /api/sessions/{id}/start` - Start/resume session (creates SDK instance)
- `POST /api/sessions/{id}/pause` - Pause session
- `POST /api/sessions/{id}/terminate` - Stop session (cleanup SDK)
- `DELETE /api/sessions/{id}` - Delete session and all data
- `POST /api/sessions/{id}/messages` - Send message to session (body: {message})
- `GET /api/sessions/{id}/messages?limit=50&offset=0` - Get messages with pagination
- `POST /api/sessions/{id}/permission-mode` - Set permission mode (body: {mode})
- `PUT /api/sessions/{id}/name` - Update session name (body: {name})

### Utility Endpoints
- `GET /` - Serve index.html
- `GET /health` - Health check endpoint
- `GET /api/filesystem/browse?path=/foo/bar` - Browse directories for project creation

### WebSocket Endpoints
- `WS /ws/ui` - Global UI WebSocket (receives project/session state broadcasts)
  - Receives: `sessions_list`, `state_change`, `project_updated`, `project_deleted`
  - Sends: `ping` (keepalive)
- `WS /ws/session/{id}` - Session-specific WebSocket (receives messages for one session)
  - Receives: `message` (SDK messages), `connection_established`, `ping`
  - Sends: `send_message`, `interrupt_session`, `permission_response`

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
   └─ Writes to messages.jsonl (one line per message)
   ↓
6. SessionCoordinator broadcasts to WebSocket
   ├─ MessageProcessor.prepare_for_websocket() formats for frontend
   └─ WebSocketManager.send_message() pushes to connected clients
   ↓
7. Frontend (app.js) receives and renders
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
9. SDK streams back responses (loop back to SDK Message flow above)
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

## Common Debugging Scenarios

### Finding Where Functionality Lives

**Problem**: Need to change how Edit tool is displayed
→ **Solution**: [static/tools/handlers/edit-handlers.js](./static/tools/handlers/edit-handlers.js) (`EditToolHandler` class)

**Problem**: Session not starting, need to debug SDK initialization
→ **Solution**: [src/session_coordinator.py:177-289](./src/session_coordinator.py) (`start_session()` method) + enable `--debug-sdk` flag

**Problem**: WebSocket not connecting
→ **Solution**: [src/web_server.py:684-930](./src/web_server.py) (websocket_endpoint function) + enable `--debug-websocket` flag

**Problem**: Messages not persisting to disk
→ **Solution**: [src/data_storage.py:51-67](./src/data_storage.py) (`append_message()` method) + enable `--debug-storage` flag

**Problem**: Permission callback not triggering
→ **Solution**: [src/web_server.py:984-1185](./src/web_server.py) (`_create_permission_callback()`) + enable `--debug-permissions` flag

**Problem**: Need to add new REST endpoint
→ **Solution**: Add route in [src/web_server.py:232-635](./src/web_server.py) (`_setup_routes()` method)

**Problem**: Change session state machine behavior
→ **Solution**: [src/session_manager.py](./src/session_manager.py) (SessionState enum and transition methods)

**Problem**: Modify project-session relationship logic
→ **Solution**: [src/project_manager.py](./src/project_manager.py) (add_session_to_project, remove_session_from_project)

**Problem**: Change how SDK messages are parsed
→ **Solution**: [src/message_parser.py](./src/message_parser.py) (MessageHandler subclasses)

### Understanding State Management

**Session States** (`SessionState` enum):
- `CREATED`: Session exists but SDK not started
- `STARTING`: Transitioning to active (not commonly seen)
- `ACTIVE`: SDK running, can send/receive messages
- `PAUSED`: Not currently used
- `TERMINATED`: SDK stopped cleanly
- `ERROR`: SDK startup or runtime failure (check `error_message` field)

**Processing State** (`is_processing` boolean):
- `True`: Session is actively processing user input (disable send button)
- `False`: Session idle and ready for new input
- Set by `SessionManager.update_processing_state()`
- Automatically reset on `ResultMessage` or errors

**Permission Modes**:
- `default`: Prompt for everything not pre-approved in .claude/settings
- `acceptEdits`: Auto-approve Edit/Write/etc (most permissive)
- `plan`: Planning mode (auto-resets to default after ExitPlanMode)
- `bypassPermissions`: No prompts at all

### Component Dependencies (What Imports What)

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
      │   └─ logging_config.py
      ├─ message_parser.py
      └─ logging_config.py
```

## Key Architectural Decisions

**Why SessionCoordinator?**
- Central orchestrator pattern prevents circular dependencies
- Single point of control for SDK lifecycle
- Coordinates state across multiple managers

**Why MessageProcessor?**
- SDK message format differs from storage format differs from WebSocket format
- Unified processing ensures consistency
- Handles backward compatibility as SDK evolves

**Why Separate Project and Session Managers?**
- Projects are lightweight grouping mechanism (working_directory + sessions)
- Sessions are heavy (SDK instances, message history, state)
- Allows deleting projects without losing session data references
- Enables future multi-project session support

**Why JSONL for messages?**
- Append-only is safe for concurrent access
- Line-by-line reading enables pagination
- Easy to repair if corruption occurs
- Human-readable for debugging

**Why asyncio.Future for permissions?**
- SDK permission callback is synchronous from its perspective
- WebSocket communication is asynchronous
- Future bridges sync SDK callback ↔ async WebSocket response

# Testing & Development Patterns

## Running the Server
```bash
# Standard run
uv run python main.py

# With debugging
uv run python main.py --debug-all

# Specific debugging
uv run python main.py --debug-sdk --debug-permissions

# Custom host/port
uv run python main.py --host 127.0.0.1 --port 8080
```

## Testing Components in Isolation
```python
# Test SessionManager
from src.session_manager import SessionManager
sm = SessionManager()
await sm.initialize()
await sm.create_session("test-id", working_directory="/tmp")

# Test MessageProcessor
from src.message_parser import MessageProcessor, MessageParser
mp = MessageProcessor(MessageParser())
parsed = mp.process_message({"type": "system", "content": "test"}, source="test")
websocket_format = mp.prepare_for_websocket(parsed)
```

## Common Import Issues
- Always import from `claude_agent_sdk` not `claude_code_sdk`
- MessageProcessor requires SDK types: `from claude_agent_sdk import UserMessage, AssistantMessage, ...`
- Circular import? You probably need to import in SessionCoordinator not the other way around

# important-instruction-reminders
Do what has been asked; nothing more, nothing less.
NEVER create files unless they're absolutely necessary for achieving your goal.
ALWAYS prefer editing an existing file to creating a new one.
NEVER proactively create documentation files (*.md) or README files. Only create documentation files if explicitly requested by the User.
ALWAYS remove temporary test files after debugging is complete.
