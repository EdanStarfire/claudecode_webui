DO NOT SAY THAT THE USER IS CORRECT OR COMPLEMENT THEIR REQUEST. FORMAL, CONCISE COMMUNICATION SHOULD BE THE ONLY COMMENTARY PROVIDED.

# Development Requirements - REQUIRED
1. Server-side code is all in python using `uv`, using commands like `uv run ...` or `uv add ...` or `uv run pytest ...` and others for executing, testing, linting, and managing dependencies.
2. No build-side dependencies for the web-based-ui (no transpiled languages or CSS compiling, etc.)

# High-Level Goal
We are building a tool that integrates with the Claude Code Python SDK to provide streaming conversations through a web-based interface. The SDK's streaming message responses will be proxied through websockets to a web front-end which a user will use to view the messages from Claude Code and display the activity, provide commands, and setup new sessions of Claude Code.

# Claude Code SDK Integration - CRITICAL TECHNICAL KNOWLEDGE

## SDK Usage (REQUIRED)
```python
from claude_code import query, ClaudeCodeOptions

# Basic streaming conversation
async def main():
    async for message in query("Create a Python web server"):
        print(message)

# With configuration
options = ClaudeCodeOptions(
    working_directory="/path/to/project",
    permissions="acceptEdits",
    tools=["bash", "edit", "read"]
)
async for message in query("Build the project", options=options):
    process_message(message)
```

## Session Management
- SDK handles session management internally
- Use `ClaudeCodeOptions` to configure per-session settings
- Sessions are maintained through the async iterator lifecycle
- Generate unique identifiers for WebUI session tracking

## SDK Configuration (CRITICAL)
```python
from claude_code import ClaudeCodeOptions

options = ClaudeCodeOptions(
    working_directory="/path/to/project",  # Project working directory
    permissions="acceptEdits",             # Permission mode
    system_prompt="Custom prompt",         # System prompt override
    tools=["bash", "edit", "read"],       # Tool allowlist
    model="claude-3-sonnet-20241022"       # Model selection
)
```

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

# important-instruction-reminders
Do what has been asked; nothing more, nothing less.
NEVER create files unless they're absolutely necessary for achieving your goal.
ALWAYS prefer editing an existing file to creating a new one.
NEVER proactively create documentation files (*.md) or README files. Only create documentation files if explicitly requested by the User.
