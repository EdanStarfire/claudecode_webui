# Claude WebUI

A web-based interface for [Claude Agent SDK](https://github.com/anthropics/claude-agent-sdk) that provides both single-agent conversations and multi-agent collaboration through an accessible browser interface.

![Status: Feature Development](https://img.shields.io/badge/status-Feature%20Development-green)
![Python: 3.13+](https://img.shields.io/badge/python-3.13+-blue)
![Vue: 3.4+](https://img.shields.io/badge/vue-3.4+-brightgreen)
![License: CC BY-NC-SA 4.0](https://img.shields.io/badge/license-CC%20BY--NC--SA%204.0-blue)

## What is Claude WebUI?

Claude WebUI transforms Claude Agent SDK into a powerful web-based development environment with:

**Single-Agent Features:**
- 🌐 **Network access** - Use Claude Code from any device on your network (phone, tablet, browser)
- 💬 **Real-time streaming** - See responses, tool executions, and thinking blocks as they happen
- 📁 **Project organization** - Group sessions by working directory with drag-and-drop reordering
- 🔧 **Rich tool visualization** - File diffs, search results, task lists, bash output with syntax highlighting
- 🔒 **Granular permissions** - Approve tools with smart suggestions and runtime mode switching
- 💾 **Persistent sessions** - Resume conversations across restarts with full history preservation
- 🎨 **Modern Vue 3 UI** - Responsive, mobile-first interface with real-time updates

**Multi-Agent Features (Legion):**
- 🏛️ **Multi-agent orchestration** - Create teams of specialized AI agents (minions) working together
- 📡 **Inter-agent communication** - Minions communicate via structured messages in real-time
- 🌳 **Hierarchical organization** - Parent-children relationships for task decomposition
- 🤖 **Autonomous spawning** - Minions can dynamically create and dispose of child minions
- 👁️ **Complete observability** - Timeline view shows all agent activity
- 🔎 **Direct Minion Control** - Spy view inspects individual minions, interrupt them, treat them as interactive sessions
- 🎛️ **Fleet controls** - Emergency halt and resume all agents instantly

## Quick Start

### Prerequisites
- **Python 3.13+** installed
- **uv** package manager ([installation guide](https://docs.astral.sh/uv/getting-started/installation/))
- **Claude Code** installed and authenticated (you can even re-login in the background to refresh auth tokens)
- Modern browser (Chrome, Firefox, Safari, Edge)

### Installation

**1. Clone the repository**:
```bash
git clone https://github.com/EdanStarfire/claudecode_webui.git
cd claudecode_webui
```

**2. Install dependencies**:
```bash
uv sync
```

**3. Start the server**:
```bash
uv run python main.py
```

**4. Open your browser**:
```
http://localhost:8000
```

**5. Create your first project**:
- Click "New Project" in the sidebar
- Choose a working directory (use folder browser)
- Create a session and start chatting

### Optional: Frontend Development

If you want to modify the Vue 3 frontend:

```bash
# Terminal 1: Start backend (use test port to avoid conflicts)
uv run python main.py --port 8001 --debug-all

# Terminal 2: Start frontend dev server with hot reload
cd frontend
npm install
npm run dev

# Access dev server at http://localhost:5173
```

### Code Quality

The project uses **Ruff** for Python linting to maintain code quality:

```bash
# Lint specific files you changed
uv run ruff check --fix src/web_server.py src/session_manager.py

# Or use git to find changed files
uv run ruff check --fix $(git diff --name-only --diff-filter=AM | grep '\.py$')

# View violations without fixing
uv run ruff check src/module_name.py
```

**Important**: Only run Ruff on files you've modified, not the entire `src/` directory. Running `--fix` on the whole codebase will auto-fix unrelated violations.

**Progressive Strictness Strategy**: The codebase currently has existing linting violations that are being addressed incrementally. New code must not introduce violations, and violations should be fixed when modifying existing files.

See [CLAUDE.md](./CLAUDE.md#code-quality---ruff-linting-workflow) for detailed workflow and requirements.

## Core Features

### Single-Agent Mode (Standard Sessions)
Use your browser to manage local claude code instances from your phone.

#### 🗂️ Project & Session Management
- **Hierarchical organization** - Projects group sessions by working directory
- **Drag-and-drop reordering** - Customize project and session order
- **Persistent state** - Resume conversations after crashes or restarts
- **Session controls** - Start, terminate, restart, or reset sessions
- **Name customization** - Rename projects and sessions for clarity

#### 🛠️ Rich Tool Visualization
Custom UI for every Claude Agent SDK tool:
- **File operations** - Syntax-highlighted previews for Read, color-coded diffs for Edit/Write
- **Search results** - Formatted Grep/Glob output with file paths and context
- **Todo lists** - Visual task tracking (☐ pending, ◐ in-progress, ☑ completed)
- **Shell commands** - Bash execution with real-time output display
- **Web tools** - WebFetch/WebSearch with prompt and result visibility
- **Notebooks** - Jupyter notebook editing with cell-by-cell changes
- **Fallback handler** - Generic display for new/unconfigured tools

#### 🔐 Permission System
- **Supports CC modes**: `default` (prompt), `acceptEdits` (permissive), `plan` (auto-resets), `bypassPermissions` (no prompts)
- **Smart suggestions** - SDK-provided permission updates you can apply instantly
- **Runtime switching** - Supports both manual and SDK-driven mode changes
- **Session state indication** - Visual indicators when a session is awaiting permissions

#### 💬 Real-Time Updates
- **WebSocket streaming** - See messages, tool calls, and thinking blocks as they arrive
- **Connection resilience** - Automatic reconnection with exponential backoff
- **Multi-session support** - Run dozens of conversations simultaneously

### Multi-Agent Mode (Legion) - In Progress

Legion enables teams of AI agents (minions) to collaborate on complex tasks:

#### ✅ Implemented Features
- **Minion creation** - User can manually create specialized agents with roles and initialization context
- **Dynamic minion creation** - Minions are able to spawn out their own dynamic minions for focused purposes.
- **Minion equivalency** - All minions support full features of claude code, including subagents, interactive permission management, and complex tool usage.
- **Inter-agent communication** - Minions send structured Comms (TASK, QUESTION, REPORT, etc.) to each other
- **Timeline view** - Unified chronological display of all agent communications across the legion
- **Spy view** - Inspect individual minion sessions and message history
- **Minion hierarchy** - Parent-child relationships visualized in tree structure
- **MCP tools integration** - Minions have access to Legion tools: send_comm, spawn_minion, list_minions, etc.
- **Real-time updates** - WebSocket broadcasting for instant comm delivery
- **Capability tracking** - Minions register expertise for discoverability

#### 🚧 In Development
- **Autonomous spawning enhancements** - Minions can develop custom specialization and expertise context for their children (MCP handlers implemented, system prompt handling needed)

__Longer-term Goals__
- **Memory & learning** - Distillation, reinforcement, knowledge transfer (architecture designed, implementation pending)
- **Minion forking** - Duplicate agents with identical memory for A/B testing (planned)

### 📊 Developer Experience
- **Vue 3 + Pinia** - Modern reactive frontend with centralized state management
- **REST API** - Full programmatic access to projects, sessions, messages, and Legion features
- **Debug logging** - Per-category logs (SDK, WebSocket, storage, parser, coordinator) with `--debug-all`
- **Extensible architecture** - Add custom tool handlers, MCP tools, or frontend components
- **Comprehensive documentation** - Architecture guides, API references, and development workflows

## Configuration

### Command-Line Options
```bash
# Custom port (default: 8000)
uv run python main.py --port 8080

# Network access (bind to all interfaces)
uv run python main.py --host 0.0.0.0

# Custom data directory (default: ./data)
uv run python main.py --data-dir /path/to/data

# Debug logging (per-category or all)
uv run python main.py --debug-all
uv run python main.py --debug-sdk --debug-websocket --debug-permissions

# Combined example
uv run python main.py --host 0.0.0.0 --port 8080 --debug-all
```

### Permission Modes

Choose the right permission level for your workflow:

| Mode | Behavior | Best For |
|------|----------|----------|
| `default` | Prompt for tools not pre-approved in `.claude/settings.json` | Recommended - balanced control |
| `acceptEdits` | Auto-approve file operations (Read, Write, Edit) | Trusted codebases, rapid iteration |
| `plan` | Planning mode, auto-resets to `default` after `ExitPlanMode` | Task planning workflows |
| `bypassPermissions` | No prompts for any tools | High-trust environments only |

**Tip**: Pre-approve frequently-used tools in `.claude/settings.json` to avoid repetitive prompts in `default` mode.

### Network Access

Access Claude WebUI from any device on your network:

1. Start with `--host 0.0.0.0`
2. Find your machine's IP address (`ipconfig` on Windows, `ifconfig` on Mac/Linux)
3. Access from other devices at `http://<your-ip>:8000`

**Security Note**: This exposes the server to your local network. Use VPN or firewall rules for internet access.

## Documentation

### Technical Documentation
- **[CLAUDE.md](./CLAUDE.md)** - Complete architecture, backend internals, API reference
- **[frontend/README.md](./frontend/README.md)** - Vue 3 frontend architecture and development guide

## Architecture Overview

### High-Level System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                       Browser (Vue 3)                        │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │ Pinia Stores │  │  Components  │  │ Vue Router   │      │
│  │  (6 stores)  │  │ (53 files)   │  │  (routing)   │      │
│  └──────────────┘  └──────────────┘  └──────────────┘      │
└────────────┬────────────────────────────────────────────────┘
             │ WebSocket (3 connections) + REST API
┌────────────▼────────────────────────────────────────────────┐
│                   FastAPI Server (Python)                    │
│  ┌──────────────────────────────────────────────────────┐   │
│  │              SessionCoordinator                       │   │
│  │  ┌────────────┐  ┌────────────┐  ┌────────────────┐ │   │
│  │  │ SessionMgr │  │ ProjectMgr │  │  ClaudeSDK     │ │   │
│  │  └────────────┘  └────────────┘  └────────────────┘ │   │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │              LegionSystem (Multi-Agent)               │   │
│  │  ┌─────────────┐  ┌──────────┐  ┌────────────────┐  │   │
│  │  │ Legion      │  │ Overseer │  │  CommRouter    │  │   │
│  │  │ Coordinator │  │ Control  │  │  (minion comms)│  │   │
│  │  └─────────────┘  └──────────┘  └────────────────┘  │   │
│  └──────────────────────────────────────────────────────┘   │
└────────────┬────────────────────────────────────────────────┘
             │ query() API
┌────────────▼────────────────────────────────────────────────┐
│                   Claude Agent SDK                           │
│              (Anthropic's official package)                  │
└──────────────────────────────────────────────────────────────┘
```

### Storage Architecture

```
data/
├── projects/{uuid}/state.json      # Project metadata
├── sessions/{uuid}/                # Session data
│   ├── state.json                  # Session state
│   └── messages.jsonl              # Append-only message log
└── legions/{uuid}/                 # Multi-agent legions
    ├── timeline.jsonl              # Unified comm log
```

**Key Technologies:**
- **Frontend**: Vue 3.4 + Pinia 2.1 + Vite 5.2 + Bootstrap 5.3
- **Backend**: Python 3.13 + FastAPI + uvicorn
- **Storage**: JSONL (messages) + JSON (state)
- **Real-time**: WebSockets with auto-reconnection

## Use Cases

### Single-Agent Development
- **Code generation** - Build features, refactor codebases, write tests
- **Debugging** - Investigate issues with full tool visibility
- **Documentation** - Generate docs, READMEs, API references
- **Learning** - Explore unfamiliar codebases interactively

### Multi-Agent Collaboration (Legion)
- **Complex software projects** - Coordinate specialists (AuthExpert, DatabaseArchitect) on large-scale changes
- **Research tasks** - Deploy domain experts (MedicalResearcher, BiochemistrySpecialist) to synthesize findings
- **Creative projects** - Simulate multi-character interactions (D&D campaigns, scenario planning)
- **Parallel exploration** - Fork agents to test multiple approaches simultaneously

## Contributing

This is a personal project, but contributions are welcome!

**Areas for contribution:**
- Additional tool handler visualizations
- Legion memory & learning system implementation
- Mobile UI refinements
- Multi-user authentication
- Performance optimizations

**Development workflow:**
1. Fork the repository
2. Create feature branch
3. Make changes with tests
4. Submit pull request with clear description

## License

**Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International** (CC BY-NC-SA 4.0)

- ✅ Free for personal, educational, and research use
- ✅ Modifications allowed with attribution
- ✅ Share adaptations under same license
- ❌ Commercial use prohibited without permission

See [LICENSE.md](./LICENSE.md) for full terms. For commercial licensing, contact the project maintainer.

## Acknowledgments

Built on [Claude Agent SDK](https://github.com/anthropics/claude-agent-sdk) by Anthropic.

## Support

- **Issues**: Report bugs at [GitHub Issues](https://github.com/EdanStarfire/claudecode_webui/issues)
- **Discussions**: Share ideas in GitHub Discussions
