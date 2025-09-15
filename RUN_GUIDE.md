# How to Run Claude Code WebUI

## Current Status
- ✅ **Phase 1**: Claude Code SDK integration and message discovery
- ✅ **Phase 2**: Session management, data storage, and bidirectional communication
- ✅ **Phase 3**: Web interface with real-time messaging (COMPLETE!)

## Prerequisites
```bash
# Ensure you have Python 3.13+ and uv installed
uv --version
```

## 🚀 Quick Start - WebUI

### 1. Start the Web Interface
```bash
# Start the Claude Code WebUI server
uv run python main.py
```

### 2. Access the Interface
- Open your browser to: **http://127.0.0.1:8000**
- Create new sessions via the web interface
- Chat with Claude Code in real-time

### 3. Using the WebUI
1. **Create Session**: Click "New Session" and configure settings
2. **Start Session**: Click "Start" to activate Claude Code SDK
3. **Chat**: Type messages and see real-time responses
4. **Manage**: Pause, terminate, or switch between sessions

## Quick Test (No Claude Code SDK Required)

### Component Tests
```bash
# Test all components
uv run pytest src/tests/ -v

# Test specific components
uv run pytest src/tests/test_session_manager.py -v
uv run pytest src/tests/test_data_storage.py -v
```

### Integration Test
```bash
uv run python simple_test.py
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

### Common Issues

1. **Unicode Errors**: Windows console encoding
   - Solution: Use basic ASCII characters in print statements
   - Or set `PYTHONIOENCODING=utf-8`

2. **Claude Code SDK Not Found**
   - Expected behavior if SDK not installed
   - Install with `uv add claude-code-sdk` or `pip install claude-code-sdk`

3. **Permission Errors**
   - Ensure write permissions to project directory
   - Check `data/` directory can be created

4. **Test Failures**
   - Run `uv sync` to ensure dependencies are installed
   - Check Python version (requires 3.13+)

### Getting Help
- Check logs in `data/logs/` (if file logging enabled)
- Run tests to verify component integrity
- Review error messages in session state files

## Architecture Overview

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│                 │    │                  │    │                 │
│  Session        │    │  Claude Code     │    │  Data Storage   │
│  Coordinator    │◄──►│  SDK Wrapper     │◄──►│  Manager        │
│                 │    │                  │    │                 │
└─────────────────┘    └──────────────────┘    └─────────────────┘
         │                       │                       │
         ▼                       ▼                       ▼
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│                 │    │                  │    │                 │
│  Session        │    │  Message Queue   │    │  JSONL Files    │
│  Manager        │    │  & Processing    │    │  State Files    │
│                 │    │                  │    │                 │
└─────────────────┘    └──────────────────┘    └─────────────────┘
```

## 🎉 Complete System Architecture

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│                 │    │                  │    │                 │
│   Web Browser   │◄──►│   FastAPI        │◄──►│   Session       │
│   (Frontend)    │    │   WebServer      │    │   Coordinator   │
│                 │    │                  │    │                 │
└─────────────────┘    └──────────────────┘    └─────────────────┘
         │                       │                       │
         │ WebSocket/REST         │ Real-time             │ Manages
         │                       │ Messaging             │
         ▼                       ▼                       ▼
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│                 │    │                  │    │                 │
│  Static Files   │    │  Claude Code     │    │  Data Storage   │
│  (HTML/CSS/JS)  │    │  SDK Wrapper     │    │  Manager        │
│                 │    │                  │    │                 │
└─────────────────┘    └──────────────────┘    └─────────────────┘
```

**All three phases complete!** The Claude Code WebUI is ready for use with full web interface, real-time messaging, session management, and persistent storage.