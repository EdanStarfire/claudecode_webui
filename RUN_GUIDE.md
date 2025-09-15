# How to Run Claude Code WebUI

## Current Status
- ✅ **Phase 1**: Claude Code SDK integration and message discovery
- ✅ **Phase 2**: Session management, data storage, and bidirectional communication
- ❌ **Phase 3**: Web interface (not yet implemented)

## Prerequisites
```bash
# Ensure you have Python 3.13+ and uv installed
uv --version
```

## Quick Test (No Claude Code SDK Required)

### 1. Run Component Tests
```bash
# Test all components
uv run pytest src/tests/ -v

# Test specific components
uv run pytest src/tests/test_session_manager.py -v
uv run pytest src/tests/test_data_storage.py -v
```

### 2. Run Simple Integration Test
```bash
uv run python simple_test.py
```
This will:
- Create a session
- Test data storage
- Show the `data/` directory structure
- Verify all components work

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

## Available Scripts

### Core Components
```bash
# Session management demo
uv run python -c "
import asyncio
import sys
sys.path.insert(0, 'src')
from src.session_coordinator import SessionCoordinator

async def demo():
    coordinator = SessionCoordinator()
    await coordinator.initialize()
    session_id = await coordinator.create_session(working_directory='.')
    print(f'Created session: {session_id}')
    await coordinator.cleanup()

asyncio.run(demo())
"
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

## Next Steps

### To Build the Web Interface (Phase 3)
1. **FastAPI Web Server**: Create `src/web_server.py`
2. **WebSocket Endpoints**: Real-time communication
3. **Static Frontend**: HTML/CSS/JS in `src/static/`
4. **Integration**: Connect frontend to session coordinator

### To Deploy
1. **Production Setup**: Configure logging, error handling
2. **Security**: Add authentication, rate limiting
3. **Scaling**: Multiple workers, load balancing

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

The system is ready for Phase 3 web interface development!