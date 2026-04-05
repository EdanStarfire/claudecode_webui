# Claude WebUI

_A web-based command center for Claude Agent SDK — single-agent conversations and multi-agent teams, from any device._

![Python: 3.13+](https://img.shields.io/badge/python-3.13+-blue)
![Vue: 3.4+](https://img.shields.io/badge/vue-3.4+-brightgreen)
![Last Commit](https://img.shields.io/github/last-commit/EdanStarfire/claudecode_webui)
![License: CC BY-NC-SA 4.0](https://img.shields.io/badge/license-CC%20BY--NC--SA%204.0-blue)

<!-- screenshot: hero-session-view.png -->
![Hero: Full session view](docs/screenshots/hero-session-view.png)

Claude WebUI is Claude Code, plus a persistent browser interface you can reach from your phone, a visual activity timeline for every tool call, and a full multi-agent orchestration layer for complex tasks. It wraps [Claude Agent SDK](https://github.com/anthropics/claude-agent-sdk) with a FastAPI backend and a Vue 3 frontend, so every feature of the CLI is available — alongside capabilities the CLI doesn't offer at all.

---

## Single-Agent Features

> **Emoji legend** — ✨ net new capability (not available in Claude Code CLI) · ⚡ meaningfully enhanced over CLI equivalent

### Access From Any Device

- ✨ Network-accessible from phone, tablet, or any browser on your LAN
- Mobile-responsive design

<!-- screenshot: mobile-responsive.png -->
![Mobile: Responsive layout](docs/screenshots/mobile-responsive.png)

### Tool Visualization

- ✨ Activity timeline with status nodes (running / success / error)
- ✨ 22 specialized tool handlers: file diffs, search results, bash output, task lists, web tools, notebooks, and more

<!-- screenshot: tool-activity-timeline.png -->
![Tool activity timeline with expanded EditToolHandler diff](docs/screenshots/tool-activity-timeline.png)

<!-- gif: tool-execution-flow.gif -->
![Demo: message sent → tools appear → results stream in](docs/screenshots/tool-execution-flow.gif)

### Project & Session Management

- ✨ Hierarchical organization — projects contain sessions
- Persistent state across restarts
- Session controls: start, terminate, restart, reset

<!-- screenshot: project-session-sidebar.png -->
![Project and session sidebar](docs/screenshots/project-session-sidebar.png)

### Permission System

- Four modes: `default`, `acceptEdits`, `plan`, `bypassPermissions`
- ⚡ Smart suggestions from SDK with one-click apply
- ✨ Permission preview from settings files before starting sessions
- Runtime mode switching

<!-- screenshot: permission-prompt.png -->
![Permission modal with smart suggestions](docs/screenshots/permission-prompt.png)

### Right Sidebar Panels

- ✨ Git diff viewer (total / per-commit modes, file-level detail)
- Task tracking panel (SDK TaskCreate / Update / List / Get)
- ✨ Resource gallery (images, files, filtering, search, full-screen view)
- Schedule panel (cron management)

<!-- screenshot: right-sidebar-diff.png -->
![DiffPanel showing file changes](docs/screenshots/right-sidebar-diff.png)

### Message Queue

- ✨ Timed delivery with configurable delays
- ✨ Auto-start sessions for queued messages
- ✨ Pause / resume / cancel / requeue controls

### Additional Features

- File attachments (drag-and-drop and paste upload)
- Slash command autocomplete
- ✨ Mermaid diagram rendering in agent responses

<!-- screenshot: mermaid-diagram.png -->
![Auto-rendered Mermaid diagram in agent response](docs/screenshots/mermaid-diagram.png)

- ✨ Read-aloud / TTS with voice selection
- ✨ Sound notifications for permissions, completion, and errors
- ✨ Context usage indicators
- ✨ Session archival with distilled history

<!-- gif: session-archival.gif -->
![Demo: session archive and in-app review flow](docs/screenshots/session-archival.gif)

---

## Multi-Agent Mode (Legion)

### Agent Teams

- ✨ Create specialized minions with roles and initialization context
- ✨ Fully templated session management for user or minion spawning
- ✨ Custom template CRUD with import/export

<!-- screenshot: legion-agent-hierarchy.png -->
![AgentStrip with StackedChips and MinionTreeNode hierarchy](docs/screenshots/legion-agent-hierarchy.png)

<!-- gif: legion-agent-spawning.gif -->
![Demo: agent creates child → appears in hierarchy → sends first comm](docs/screenshots/legion-agent-spawning.gif)

### Inter-Agent Communication

- ⚡ Structured comms: task, question, report, info, halt, pivot
- ⚡ Direct injection into minion's active conversation — no polling, no waiting
- ✨ Full hierarchy visibility (ancestors, descendants, siblings)
- ✨ Comm cards with markdown, attachments, type badges
- ✨ Direct file passing between agents

<!-- screenshot: legion-comms.png -->
![CommCards showing agent-to-agent communication](docs/screenshots/legion-comms.png)

### Observability & Control

- ✨ Full visibility into all sessions by default
- ✨ Fleet controls: emergency halt and resume all agents
- ✨ Session archival on disposal with distilled history
- ✨ View previous sessions in-app

### Scheduling

- ⚡ Cron-based scheduled prompts, assignable to a session
- ✨ Clear context before running scheduled prompt
- ✨ Ephemeral agent schedules (fire-and-forget)
- ✨ Execution history with success/failure tracking

---

## Configuration & Customization

- ✨ Per-session Docker isolation (image, mounts, home directory)
- ⚡ Per-session MCP server configuration (STDIO / SSE / HTTP, OAuth 2.1, enable/disable)
- Near-full Claude Code configuration management via templates
- 12 built-in skills auto-deployed to `~/.claude/skills/`
- Custom skill creation
- Self-update and server restart from UI

---

## Quick Start

```bash
git clone https://github.com/EdanStarfire/claudecode_webui.git
cd claudecode_webui
uv sync
uv run python main.py
# Open http://localhost:8000
```

Prerequisites: Python 3.13+, `uv`, Claude Code installed and authenticated.
See [Setup Guide](./run_guide.md) for Docker, network access, frontend dev, and advanced configuration.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                       Browser (Vue 3)                        │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │ Pinia Stores │  │  Components  │  │ Vue Router   │      │
│  │ (12 stores)  │  │ (85+ files)  │  │  (routing)   │      │
│  └──────────────┘  └──────────────┘  └──────────────┘      │
└────────────┬────────────────────────────────────────────────┘
             │ HTTP long-polling + REST API
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

**Key technologies**: Vue 3.4 · Pinia 2.1 · Vite 5.2 · Bootstrap 5.3 · FastAPI · uvicorn · JSONL/JSON storage · HTTP long-polling

See [CLAUDE.md](./CLAUDE.md) for deep architecture documentation.

---

## Documentation

- [Architecture Guide](./CLAUDE.md)
- [Frontend Architecture](./frontend/CLAUDE.md)
- [API Reference](./.claude/API_REFERENCE.md)
- [Tool Handler Guide](./TOOL_HANDLERS.md)

---

## Contributing & License

1. Fork the repository
2. Create a feature branch
3. Submit a pull request with a clear description

**License**: [Creative Commons Attribution-NonCommercial-ShareAlike 4.0](./LICENSE.md) (CC BY-NC-SA 4.0) — free for personal, educational, and research use; commercial use requires permission; share adaptations under the same license.

**Support**: [GitHub Issues](https://github.com/EdanStarfire/claudecode_webui/issues)
