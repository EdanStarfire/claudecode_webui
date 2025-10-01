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

# important-instruction-reminders
Do what has been asked; nothing more, nothing less.
NEVER create files unless they're absolutely necessary for achieving your goal.
ALWAYS prefer editing an existing file to creating a new one.
NEVER proactively create documentation files (*.md) or README files. Only create documentation files if explicitly requested by the User.
ALWAYS remove temporary test files after debugging is complete.
