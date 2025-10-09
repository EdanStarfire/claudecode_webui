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

# important-instruction-reminders
Do what has been asked; nothing more, nothing less.
NEVER create files unless they're absolutely necessary for achieving your goal.
ALWAYS prefer editing an existing file to creating a new one.
NEVER proactively create documentation files (*.md) or README files. Only create documentation files if explicitly requested by the User.
ALWAYS remove temporary test files after debugging is complete.
