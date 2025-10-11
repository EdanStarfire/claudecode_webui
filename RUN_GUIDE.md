# Claude WebUI - Complete Run & Development Guide

## Quick Navigation
- [Quick Start](#-quick-start---webui) - Get running in 60 seconds
- [Development Mode](#development-mode) - Debug flags and logging
- [API Reference](#api-endpoints) - REST and WebSocket documentation
- [Testing](#testing-guide) - Unit tests and integration tests
- [Troubleshooting](#troubleshooting) - Common issues and solutions
- [Architecture](#architecture-overview) - System design overview
- [Production](#production-deployment) - Deployment considerations

## Current Status
- ✅ **Phase 1**: Claude Agent SDK integration and message discovery
- ✅ **Phase 2**: Session management, data storage, and bidirectional communication
- ✅ **Phase 3**: Web interface with real-time messaging
- ✅ **Phase 4**: Project hierarchy and session organization
- ✅ **Phase 5**: Tool handler system and frontend modularization

## Prerequisites
```bash
# Python 3.13+ required
python --version  # or python3 --version

# uv package manager (recommended)
uv --version

# Alternative: pip + venv
pip --version
```

## 🚀 Quick Start - WebUI

### 1. Install Dependencies (First Time Only)
```bash
# Using uv (recommended)
uv sync

# OR using pip + venv
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r pyproject.toml
```

### 2. Start the Web Interface
```bash
# Standard mode (recommended)
uv run python main.py

# Custom host/port
uv run python main.py --host 0.0.0.0 --port 8080

# With all debug logging (verbose)
uv run python main.py --debug-all
```

**Server starts on**: http://127.0.0.1:8000 (or your specified host/port)

### 3. Access the Interface
1. Open browser to: **http://127.0.0.1:8000**
2. Click **"New Project"** button
3. Enter project name and select working directory
4. Click **"New Session"** within the project
5. Configure permission mode, tools, model (or use defaults)
6. Click **"Start Session"** - wait for "Claude Code Launched"
7. Type message, press Send - see real-time Claude responses!

### 4. Using the WebUI

#### Project Management
- **Create Project**: Groups sessions by working directory (one directory = one project)
- **Expand/Collapse**: Click project name to show/hide sessions
- **Drag to Reorder**: Drag projects or sessions to change display order
- **Delete Project**: Removes project and all its sessions (data files deleted)

#### Session Management
- **Create Session**: Configure permission mode, tools, model, custom name
- **Start Session**: Launches Claude Agent SDK (shows "Claude Code Launched")
- **Chat**: Type in input box, press Send or Enter (Shift+Enter for newlines)
- **Interrupt**: Click Stop button to halt current processing
- **Permission Mode**: Change at runtime (default, acceptEdits, plan, bypassPermissions)
- **Delete Session**: Removes session and conversation history

#### Message Display
- **Tool Cards**: Expandable/collapsible cards showing tool usage (Read, Edit, Write, etc.)
- **Diff Views**: Edit/MultiEdit show color-coded line changes (red=removed, green=added)
- **Todo Tracking**: TodoWrite shows checklist with ☐ pending, ◐ in-progress, ☑ completed
- **Permission Prompts**: Modal dialog when tools need approval (with suggested settings)
- **Auto-scroll**: New messages scroll into view automatically

## Development Mode

### Debug Flags (Specialized Logging)
Enable specific debug categories to diagnose issues:

```bash
# All debugging (VERY verbose)
uv run python main.py --debug-all

# WebSocket lifecycle debugging
uv run python main.py --debug-websocket

# SDK integration debugging
uv run python main.py --debug-sdk

# Permission callback debugging
uv run python main.py --debug-permissions

# Data storage debugging
uv run python main.py --debug-storage

# Message parser debugging
uv run python main.py --debug-parser

# Error handler debugging
uv run python main.py --debug-error-handler

# Combine multiple flags
uv run python main.py --debug-sdk --debug-permissions --debug-websocket
```

**Debug logs location**: `data/logs/{category}.log`
- `coordinator.log` - SessionCoordinator orchestration
- `sdk_debug.log` - SDK wrapper and message processing
- `websocket_debug.log` - WebSocket connection lifecycle
- `storage.log` - File operations and persistence
- `parser.log` - Message parsing and normalization
- `error.log` - All errors across system

### Live Log Monitoring
```bash
# Watch coordinator actions
tail -f data/logs/coordinator.log

# Watch SDK messages
tail -f data/logs/sdk_debug.log

# Watch WebSocket events
tail -f data/logs/websocket_debug.log

# Watch all errors
tail -f data/logs/error.log
```

### Frontend Debugging
```javascript
// In browser console (F12):

// Enable all frontend logging
Logger.setLevel('debug');

// Check tool handler registry
app.toolHandlerRegistry.listHandlers();

// Inspect active sessions
app.sessions;

// Check WebSocket connection status
app.sessionWebSocket;  // session-specific WebSocket
app.uiWebSocket;       // global UI WebSocket
```

## Testing Guide

### Unit Tests
```bash
# Run all tests
uv run pytest src/tests/ -v

# Run specific test file
uv run pytest src/tests/test_session_manager.py -v
uv run pytest src/tests/test_project_manager.py -v
uv run pytest src/tests/test_data_storage.py -v
uv run pytest src/tests/test_message_parser.py -v

# Run tests matching pattern
uv run pytest src/tests/ -k "session" -v

# Show detailed output (including print statements)
uv run pytest src/tests/ -v -s

# Stop on first failure
uv run pytest src/tests/ -x
```

### Integration Tests
```bash
# Full system test (requires Claude Agent SDK)
uv run python demo_session.py

# Quick component test
uv run python simple_test.py
```

### Manual API Testing
```bash
# Health check
curl http://127.0.0.1:8000/health

# List projects
curl http://127.0.0.1:8000/api/projects

# Create project
curl -X POST http://127.0.0.1:8000/api/projects \
  -H "Content-Type: application/json" \
  -d '{"name":"Test Project","working_directory":"/tmp"}'

# List sessions
curl http://127.0.0.1:8000/api/sessions

# Create session
curl -X POST http://127.0.0.1:8000/api/sessions \
  -H "Content-Type: application/json" \
  -d '{"project_id":"<uuid>","permission_mode":"acceptEdits"}'

# Start session
curl -X POST http://127.0.0.1:8000/api/sessions/<session-id>/start

# Send message
curl -X POST http://127.0.0.1:8000/api/sessions/<session-id>/messages \
  -H "Content-Type: application/json" \
  -d '{"message":"Hello Claude!"}'

# Get messages
curl http://127.0.0.1:8000/api/sessions/<session-id>/messages?limit=50
```

## Running with Claude Code SDK

### 1. Install Claude Code SDK
```bash
# Add Claude Code SDK to your project
uv add claude-code-sdk

# Or install globally
pip install claude-code-sdk
```

### 2. Run Session Demo
```bash
uv run python demo_session.py
```
This will:
- Create a real Claude Code session
- Attempt to start the SDK
- Send messages and store responses
- Demonstrate the full pipeline

## API Endpoints

The WebUI provides REST API endpoints for integration:

### Session Management
```bash
# List all sessions
curl http://127.0.0.1:8000/api/sessions

# Create new session
curl -X POST -H "Content-Type: application/json" \
  -d '{"working_directory":"/path/to/project","permissions":"acceptEdits","tools":["bash","edit","read"]}' \
  http://127.0.0.1:8000/api/sessions

# Start a session
curl -X POST http://127.0.0.1:8000/api/sessions/{session_id}/start

# Send message
curl -X POST -H "Content-Type: application/json" \
  -d '{"message":"Hello Claude!"}' \
  http://127.0.0.1:8000/api/sessions/{session_id}/messages

# Get messages
curl http://127.0.0.1:8000/api/sessions/{session_id}/messages
```

### WebSocket Connection
```javascript
// Real-time messaging via WebSocket
const ws = new WebSocket('ws://127.0.0.1:8000/ws/{session_id}');
ws.send(JSON.stringify({type: 'send_message', content: 'Hello!'}));
```

## Available Scripts

### Core Components Demo
```bash
# Session management demo (command line)
uv run python demo_session.py
```

### SDK Discovery Tool
```bash
# Discover Claude Code SDK message types (requires Claude Code SDK)
uv run python -c "
import sys
sys.path.insert(0, 'src')
from src.sdk_discovery_tool import main
import asyncio
asyncio.run(main())
"
```

### Message Parser Test
```bash
# Test message parsing
uv run python -c "
import sys
sys.path.insert(0, 'src')
from src.message_parser import MessageParser
parser = MessageParser()
result = parser.parse_message({'type': 'user', 'content': 'Hello!'})
print(f'Parsed: {result}')
"
```

## Data Directory Structure

After running any demo, check the created data structure:
```
data/
├── sessions/
│   └── {session-uuid}/
│       ├── messages.jsonl    # All messages in JSONL format
│       ├── state.json        # Session state
│       ├── history.json      # Command history
│       └── .integrity        # Data integrity hash
└── logs/                     # Application logs (if file logging enabled)
```

## Development Workflow

### 1. Run Tests During Development
```bash
# Watch for changes and run tests
uv run pytest src/tests/ --tb=short -v

# Run specific test files
uv run pytest src/tests/test_session_coordinator.py -v
```

### 2. Check Code Quality
```bash
# If you have linting tools configured
uv run ruff check src/
uv run mypy src/
```

### 3. Debug with Logging
```bash
# Enable debug logging
uv run python -c "
import sys
sys.path.insert(0, 'src')
from src.logging_config import setup_logging
setup_logging(log_level='DEBUG', enable_console=True)
# ... rest of your code
"
```

## WebUI Features

### ✅ Implemented Features
- **Session Dashboard**: Create, manage, and monitor Claude Code sessions
- **Real-time Chat**: WebSocket-based messaging with Claude Code
- **Session Management**: Start, pause, terminate sessions
- **Message History**: Persistent storage and retrieval
- **Connection Monitoring**: Live connection status and auto-reconnect
- **Responsive Design**: Works on desktop and mobile
- **REST API**: Full programmatic access to functionality

### 🎯 Production Deployment
1. **Security**: Add authentication, rate limiting, HTTPS
2. **Scaling**: Multiple workers, load balancing
3. **Monitoring**: Health checks, metrics, alerting
4. **Configuration**: Environment-based settings

## Troubleshooting

### Common Issues & Solutions

#### Startup Issues

**Problem**: `uv: command not found`
→ **Solution**: Install uv: `pip install uv` or use pip/venv directly

**Problem**: `ModuleNotFoundError: No module named 'fastapi'`
→ **Solution**: Install dependencies: `uv sync` or `pip install -r requirements.txt`

**Problem**: `Address already in use` error on port 8000
→ **Solution**: Use different port: `uv run python main.py --port 8080`

**Problem**: Server starts but browser shows "Cannot connect"
→ **Solution**: Check firewall, ensure binding to correct host (try `--host 0.0.0.0`)

#### Session & Connection Issues

**Problem**: Session stuck in "CREATED" state, won't start
→ **Solution**:
1. Enable debug: `--debug-sdk --debug-coordinator`
2. Check `data/logs/sdk_debug.log` for SDK errors
3. Verify Claude Agent SDK installed: `uv add claude-agent-sdk`
4. Check working directory exists and is accessible

**Problem**: "Claude Code Launched" never appears
→ **Solution**:
1. Check SDK installation: `python -c "import claude_agent_sdk; print(claude_agent_sdk.__version__)"`
2. Enable `--debug-sdk` and check for immediate CLI failures
3. Verify API key configured: `claude auth login`

**Problem**: WebSocket connection fails (console error)
→ **Solution**:
1. Enable `--debug-websocket`
2. Check browser console for specific error
3. Verify session is in ACTIVE state before connecting
4. Check `data/logs/websocket_debug.log` for rejection reasons

**Problem**: Messages not appearing in UI
→ **Solution**:
1. Open browser DevTools → Network tab → WS → check messages flowing
2. Enable frontend logging: `Logger.setLevel('debug')` in console
3. Check backend: `--debug-parser` to see message processing
4. Verify `data/sessions/{uuid}/messages.jsonl` is being written

#### Permission Issues

**Problem**: Permission prompt never appears
→ **Solution**:
1. Enable `--debug-permissions`
2. Check permission callback is triggered (logs show "PERMISSION CALLBACK TRIGGERED")
3. Verify WebSocket connected (permission sent via WS)
4. Check permission mode - `acceptEdits` bypasses prompts

**Problem**: Permission suggestions not showing
→ **Solution**:
1. Suggestions only available in SDK v0.1.0+
2. Check `context.suggestions` is not empty in logs
3. Verify permission modal rendering in browser console

#### Data & Persistence Issues

**Problem**: Session data not persisting
→ **Solution**:
1. Enable `--debug-storage`
2. Check `data/sessions/{uuid}/` directory exists
3. Verify write permissions to data directory
4. Check `messages.jsonl` file growing with new messages

**Problem**: Project/session deleted but files remain
→ **Solution**:
1. Windows file locking - wait a few seconds, retry
2. Close any processes with open file handles (editors, terminals)
3. Manual cleanup: delete `data/projects/{uuid}/` or `data/sessions/{uuid}/`

**Problem**: "Corruption detected" on session load (rare)
→ **Solution**:
1. Check `messages.jsonl` for malformed JSON (each line must be valid JSON)
2. Repair: Remove invalid lines or restore from backup
3. Note: Corruption detection currently disabled by default

#### Frontend Issues

**Problem**: Tool cards not rendering (generic display)
→ **Solution**:
1. Check browser console for handler errors
2. Verify handler registered: `app.toolHandlerRegistry.getHandler('ToolName')`
3. Check script load order in index.html (handlers before app.js)

**Problem**: Diff views not showing colors
→ **Solution**:
1. Hard refresh browser (Ctrl+Shift+R) to reload CSS
2. Check `styles.css` loaded in Network tab
3. Inspect element to verify `.diff-line-added` / `.diff-line-removed` classes applied

**Problem**: Todo checkboxes not updating
→ **Solution**:
1. Check ToolCallManager state: `app.toolCallManager._toolCalls`
2. Verify TodoWrite result message processed (check console logs)
3. Ensure todo status one of: `pending`, `in_progress`, `completed`

### Performance Issues

**Problem**: UI sluggish with many messages
→ **Solution**:
1. Collapse old tool cards (click headers)
2. Delete old sessions periodically
3. Reduce message preview limits (edit handler line limits)

**Problem**: High memory usage
→ **Solution**:
1. Limit concurrent sessions (terminate unused sessions)
2. Clear browser cache periodically
3. Check for memory leaks in custom tool handlers

### Debug Checklist

When something doesn't work:
1. ✅ Check browser console (F12) for JS errors
2. ✅ Check backend logs: `tail -f data/logs/error.log`
3. ✅ Enable relevant debug flag: `--debug-sdk`, `--debug-websocket`, etc.
4. ✅ Check session state: `curl http://127.0.0.1:8000/api/sessions/{id}`
5. ✅ Verify file permissions on `data/` directory
6. ✅ Test with clean session (create new project + session)
7. ✅ Check Claude Agent SDK version: `claude --version`

### Getting Help
1. Check logs in `data/logs/{category}.log`
2. Run unit tests: `uv run pytest src/tests/ -v`
3. Review session state files: `cat data/sessions/{uuid}/state.json`
4. Check GitHub issues: https://github.com/anthropics/claude-agent-sdk/issues
5. Enable `--debug-all` and capture full log output

## Architecture Overview

See [CLAUDE.md](./CLAUDE.md) for complete architecture documentation including:
- System architecture diagram (Frontend → FastAPI → SessionCoordinator → SDK)
- Backend file organization (every Python module documented)
- Data folder structure (projects, sessions, logs)
- API endpoint reference (REST + WebSocket)
- Message flow architecture (SDK → Storage → WebSocket flows)
- Component dependencies and responsibilities

### Quick Architecture Summary
```
Browser (static/app.js + tools/ + core/)
    ↓ WebSocket + REST
FastAPI Server (src/web_server.py)
    ↓ Orchestration
SessionCoordinator (src/session_coordinator.py)
    ↓ Manages
ClaudeSDK + SessionManager + ProjectManager + DataStorage
    ↓ Wraps
Claude Agent SDK (external package)
```

**Key Points**:
- **Frontend**: Modular JavaScript (core/, tools/, handlers/)
- **Backend**: Python FastAPI with async WebSockets
- **Data**: JSONL append-only logs + JSON state files
- **SDK**: Wrapped with message queue and callbacks
- **Persistence**: Projects contain sessions, sessions contain messages

## Production Deployment

### Security Considerations
⚠️ **DO NOT deploy without these security measures**:

1. **Authentication**: Add user authentication (OAuth, JWT, etc.)
2. **Authorization**: Restrict project access per user
3. **HTTPS**: Use reverse proxy (nginx, Caddy) with TLS
4. **Rate Limiting**: Prevent API abuse
5. **Input Validation**: Sanitize all user inputs
6. **CORS**: Configure proper CORS policies
7. **Environment Variables**: Never commit API keys

### Deployment Options

#### Option 1: Docker (Recommended)
```dockerfile
# Dockerfile
FROM python:3.13-slim
WORKDIR /app
COPY . .
RUN pip install uv && uv sync
EXPOSE 8000
CMD ["uv", "run", "python", "main.py", "--host", "0.0.0.0"]
```

```bash
docker build -t claude-webui .
docker run -p 8000:8000 -v ./data:/app/data claude-webui
```

#### Option 2: Systemd Service
```ini
# /etc/systemd/system/claude-webui.service
[Unit]
Description=Claude WebUI
After=network.target

[Service]
Type=simple
User=claude
WorkingDirectory=/opt/claude-webui
ExecStart=/usr/local/bin/uv run python main.py
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable claude-webui
sudo systemctl start claude-webui
```

#### Option 3: Reverse Proxy (nginx)
```nginx
server {
    listen 80;
    server_name claude.example.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

### Scaling Considerations
- **Multiple Workers**: Use gunicorn/uvicorn workers
- **Load Balancing**: nginx upstream for multiple instances
- **Session Affinity**: Sticky sessions for WebSocket connections
- **Database**: Consider PostgreSQL for high-volume deployments
- **Caching**: Redis for session state caching
- **Monitoring**: Prometheus + Grafana for metrics

### Environment Configuration
```bash
# .env file
CLAUDE_API_KEY=your_key_here
SERVER_HOST=0.0.0.0
SERVER_PORT=8000
DATA_DIR=/var/lib/claude-webui/data
LOG_LEVEL=INFO
```

## Additional Resources

- **Main Documentation**: [CLAUDE.md](./CLAUDE.md) - Complete backend architecture
- **Tool Handlers**: [TOOL_HANDLERS.md](./TOOL_HANDLERS.md) - Frontend tool display system
- **Development Plan**: [DEVELOPMENT_PLAN.md](./DEVELOPMENT_PLAN.md) - Project roadmap and history
- **SDK Documentation**: https://github.com/anthropics/claude-agent-sdk

## Summary

Claude WebUI is a **production-ready web interface** for Claude Agent SDK with:
✅ Real-time conversation streaming
✅ Project and session hierarchy
✅ Customizable tool display handlers
✅ Permission management with suggestions
✅ Persistent message storage
✅ Complete REST and WebSocket API
✅ Comprehensive debugging tools
✅ Modular and extensible architecture