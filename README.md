# Claude WebUI

A web-based interface for [Claude Agent SDK](https://github.com/anthropics/claude-agent-sdk) that lets you interact with Claude Code from your browser, phone, or any device on your network.

![Status: 90% Functioning](https://img.shields.io/badge/status-90%25%20functional-green)
![Python: 3.13+](https://img.shields.io/badge/python-3.13+-blue)
![License: CC BY-NC-SA 4.0](https://img.shields.io/badge/license-CC%20BY--NC--SA%204.0-blue)

## What is Claude WebUI?

Claude WebUI provides a feature-rich web interface for Claude Agent SDK, enabling:
- 🌐 **Access from your network** - Use Claude Code from your phone, tablet, or any browser on the network the server is running (or anywhere with VPN)
- 💬 **Real-time conversations** - Streaming responses with full tool execution visibility
- 📁 **Project organization** - Group sessions by working directory
- 🔧 **Visual tool displays** - See file edits, diffs, searches, and task lists in rich UI
- 🔒 **Permission management** - Approve tool usage with suggested permission updates
- 💾 **Persistent history** - All conversations and tool executions saved
- 🎨 **Modern UI** - Clean, responsive interface that works on mobile

## Quick Start

**1. Install dependencies** (first time only):
```bash
uv sync
# or: pip install -r requirements.txt
```

**2. Start the server**:
```bash
uv run python main.py
```

**3. Open your browser**:
```
http://localhost:8000
```

**4. Create a project and start chatting** - That's it! 🎉

## Key Features

### 🗂️ Project & Session Organization
- **Projects** group sessions by working directory
- **Sessions** maintain conversation history and context
- **Customization** re-order and rename projects and sessions
- **Resumable** persistent sessions across server and agent restarts / crashes

### 🛠️ Rich Tool Visualization
See what Claude Code is doing with custom displays for every tool:
- **File Operations**: Preview file contents, see diffs with syntax highlighting
- **Edits**: Color-coded line-by-line changes (red=removed, green=added)
- **Search Results**: File matches and grep results with context
- **Todo Lists**: Visual task tracking with checkboxes (☐ pending, ◐ in-progress, ☑ completed)
- **Shell Commands**: Command execution with output preview
- **Web Fetches**: URL content retrieval with visibility into fetch prompts and returned content

### 🔐 Permission Control
- **Four permission modes**: `default`, `acceptEdits`, `plan`, `bypassPermissions`
- **Runtime mode switching**: Change permissions without restarting
- **Visual approval dialogs**: Clear tool usage explanations

### 💬 Real-Time Communication
- **WebSocket streaming**: See responses as Claude chats/thinks/uses tools
- **Tool execution tracking**: Watch Read, Edit, Write, Bash commands in real-time
- **Connection monitoring**: Auto-reconnect on network issues
- **Multi-session support**: Run multiple conversations simultaneously

### 📊 Developer-Friendly
- **REST API**: Full programmatic access to all functionality
- **Debug logging**: Detailed logs for troubleshooting (`--debug-all`)
- **Extensible handlers**: Add custom tool displays easily
- **Clean architecture**: Well-documented, modular codebase

## Configuration

### Command-Line Options
```bash
# Custom port
uv run python main.py --port 8080

# Bind to all interfaces (access from other devices)
uv run python main.py --host 0.0.0.0

# Enable debug logging
uv run python main.py --debug-all
uv run python main.py --debug-sdk --debug-websocket
```

### Supported Permission Modes
- **`default`**: Prompt for tools not pre-approved (recommended)
- **`acceptEdits`**: Auto-approve file edits and writes (permissive)
- **`plan`**: Planning mode, auto-resets after ExitPlanMode
- **`bypassPermissions`**: No prompts (use with caution)

### Supported Tools
All Claude Agent SDK tools are supported:
- File operations: `Read`, `Write`, `Edit`, `MultiEdit`
- Search: `Grep`, `Glob`
- Shell: `Bash`, `BashOutput`, `KillShell`
- Web: `WebFetch`, `WebSearch`
- Coordination: `Task`, `TodoWrite`, `ExitPlanMode`
- Fallback Tools: New tools and other unconfigured tools show raw inputs / outputs and still support prompting for permissions and suggestions.

## Requirements

- **Python 3.13+** (required by Claude Agent SDK)
- **Claude Code** (install and login as necessary - can even log-in while sessions are active if auth issue occur)
- **Modern browser** (Chrome, Firefox, Safari, Edge)

## Documentation

- **[run_guide.md](./run_guide.md)** - Complete setup, usage, and troubleshooting guide
- **[CLAUDE.md](./CLAUDE.md)** - Architecture, backend internals, API reference
- **[TOOL_HANDLERS.md](./TOOL_HANDLERS.md)** - Tool display system, add custom handlers
- **[DEVELOPMENT_PLAN.md](./DEVELOPMENT_PLAN.md)** - Project roadmap and development history

## Architecture

```
Browser                 FastAPI Server           Claude Agent SDK
   │                          │                          │
   │ ←─── WebSocket ─────→    │                          │
   │ ←──── REST API ─────→    │                          │
   │                          │                          │
   │                    SessionCoordinator               │
   │                          │                          │
   │                    ┌─────┴─────┐                    │
   │                    │           │                    │
   │              SessionMgr   ProjectMgr                │
   │                    │           │                    │
   │                    └─────┬─────┘                    │
   │                          │                          │
   │                     ClaudeSDK  ←──── query() ──────►│
   │                          │                          │
   │                    DataStorage                      │
   │                   (JSONL + JSON)                    │
```

**Key Components:**
- **Frontend**: Modular JavaScript (core/, tools/, handlers/)
- **Backend**: Python FastAPI with async WebSockets
- **Storage**: JSONL append-only message logs, JSON state files
- **SDK Wrapper**: Message queue, callbacks, streaming processor

## Contributing

This is a personal project, but contributions are welcome! Areas for improvement:
- Additional tool handler visualizations
- Mobile UI optimizations
- Multi-user authentication

## License

This project is licensed under the **Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International License** (CC BY-NC-SA 4.0).

- ✅ Free for personal, educational, and research use
- ✅ Modifications allowed with attribution
- ❌ Commercial use prohibited without permission

See [LICENSE.md](./LICENSE.md) for full terms. For commercial licensing inquiries, please contact the project maintainer.

## Acknowledgments

Built on [Claude Agent SDK](https://github.com/anthropics/claude-agent-sdk) by Anthropic.
