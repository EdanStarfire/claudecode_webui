# DEVELOPMENT_PLAN.md

## Claude Code WebUI Development Plan

### Project Overview
Build a web-based interface that proxies Claude Code sessions through a headless backend, providing real-time activity streaming and command interaction through a responsive web interface.

**MVP Target**: Phases 1-3 (Single-session functionality)
**Tech Stack**: Python 3.13+ with uv, FastAPI + WebSockets, Vanilla HTML/CSS/JS
**Code Structure**: All source code in `src/` directory

---

## Phase 1: Claude Code SDK Integration & Discovery ✅ COMPLETED
**Goal**: Establish reliable Claude Code SDK integration and discover streaming message formats
**Status**: Completed 2025-09-14

### Tasks
1. **Project Foundation Setup**
   - Initialize `src/` directory structure
   - Set up `pyproject.toml` with uv for Claude Code SDK, FastAPI, websockets, pytest
   - Configure comprehensive logging at module level
   - Create basic project structure in `src/`

2. **Claude Code SDK Wrapper**
   - Build `src/claude_sdk.py` - integrate Claude Code SDK with async `query()` function
   - Implement streaming message handling through async iterators
   - Add SDK session lifecycle management (start, terminate, error handling)
   - Include robust error handling and logging for SDK exceptions

3. **SDK Message Discovery System**
   - Create `src/sdk_discovery_tool.py` - send various prompts to Claude Code SDK
   - Capture and catalog different streaming message formats
   - Test scenarios: tool use, permission requests, errors, normal responses
   - Build sample library of SDK message types

4. **Message Parser Foundation**
   - Develop `src/message_parser.py` with extensible type system
   - Handle known SDK message types based on discovery results
   - Gracefully process unknown types with detailed logging
   - Implement streaming message processing

5. **Testing Infrastructure**
   - Unit tests for SDK integration
   - Parser tests with mock SDK message data
   - Error handling and SDK exception tests
   - No LLM integration testing (non-deterministic)

**Deliverables**: Working Claude Code SDK integration, catalogued message types, robust parsing foundation

**Completion Summary**: All core Phase 1 components implemented:
- `src/claude_sdk.py` - Claude Code SDK wrapper with async query integration
- `src/message_parser.py` - Extensible message parsing system
- `src/sdk_discovery_tool.py` - SDK message format discovery utilities
- `src/logging_config.py` - Centralized logging configuration
- Complete test suite with 4 test modules covering all components
- Project configuration with pyproject.toml and development dependencies

---

## Phase 2: Session Management & Communication ✅ COMPLETED
**Goal**: Build reliable session lifecycle with bidirectional communication
**Status**: Completed 2025-09-14

### Tasks
1. **Session State Management**
   - Create `src/session_manager.py` with UUID-based session IDs
   - Implement session lifecycle (create, start, pause, terminate)
   - Add session state persistence to file system
   - Handle multiple session instances

2. **File-Based Data Storage**
   - Implement `data/` directory structure per spec
   - Create session storage in `data/sessions/{uuid}/`
   - Store activity logs as JSONL, state as JSON
   - Add data integrity and corruption detection

3. **Bidirectional Communication**
   - Enhance SDK wrapper for interactive conversations
   - Implement streaming message handling and response processing
   - Add message queuing and async coordination
   - Handle SDK errors and exceptions with state capture

4. **Message Streaming System**
   - Build real-time message processing pipeline
   - Implement message buffering and history storage
   - Add timestamp and context information
   - Expand SDK message type handling based on Phase 1 discovery

5. **Enhanced Error Handling**
   - Comprehensive SDK exception detection and reporting
   - Session recovery state capture
   - User-visible error notifications
   - Detailed logging for debugging

**Deliverables**: Reliable session management, persistent message storage, robust SDK communication pipeline

**Completion Summary**: All core Phase 2 components implemented:
- `src/session_manager.py` - UUID-based session lifecycle management with persistence
- `src/data_storage.py` - JSONL message storage with integrity checking and corruption detection
- `src/session_coordinator.py` - Unified orchestrator integrating all session components
- `src/error_handler.py` - Comprehensive error detection, classification, and reporting system
- Enhanced `src/claude_sdk.py` - Interactive conversations with message queuing and async coordination
- Complete test suite with 64 passing tests covering all Phase 2 functionality
- Full integration between session management, data storage, SDK interaction, and error handling

**Phase 2.1 - SDK Modernization (2025-09-15)**:
- **ClaudeSDKClient Integration**: Migrated from function-based `query()` to modern `ClaudeSDKClient` context manager pattern
- **Enhanced Permission System**: Updated permission callbacks to support new `PermissionResultAllow`/`PermissionResultDeny` return types with backward compatibility
- **Improved Message Processing**: Enhanced message conversion with better type handling, serialization, and error recovery
- **Robust Error Handling**: Comprehensive error handling throughout SDK integration with proper logging and state management
- **Type Safety**: Enhanced type annotations and imports with fallback handling for development environments

---

## Phase 3: Basic WebUI (MVP Complete)
**Goal**: Responsive web interface for single-session Claude Code interaction

### Tasks
1. **FastAPI Web Server Foundation**
   - Create `src/web_server.py` with FastAPI application
   - Implement WebSocket endpoints for real-time communication
   - Add static file serving for frontend assets
   - Configure CORS and basic security

2. **Frontend Structure**
   - Create `src/static/` directory for HTML/CSS/JS
   - Build responsive single-session interface
   - Ensure desktop-primary, mobile-usable design
   - Implement touch-compatible controls

3. **Real-Time Message Display**
   - WebSocket client for message streaming
   - Message visualization based on discovered SDK types
   - Scrollable history with timestamps
   - Horizontal scrolling support for diffs and wide content

4. **Command Interface**
   - Input field with command sending
   - Command history and basic auto-completion
   - Input readiness indicators and queuing UI
   - Keyboard shortcuts and accessibility

5. **Connection Management**
   - WebSocket connection status indicators
   - Automatic reconnection handling
   - Clear error messaging for failed connections
   - Session state visibility

6. **Error Visibility & Status**
   - Display SDK errors and exceptions to user
   - Connection status notifications
   - Message processing errors
   - Recovery action suggestions

**Deliverables**: Complete single-session web interface, real-time SDK message streaming, responsive design

---

## Development Workflow

### Iteration Strategy
- Complete each phase fully before proceeding
- Break phases into small, testable tasks
- Continuous integration of discovery findings
- Regular testing with actual Claude Code sessions

### Discovery-Driven Development
1. **Phase 1**: Capture and catalog SDK message formats
2. **Phase 2**: Expand message library as new types discovered
3. **Phase 3**: Implement UI components for all discovered message types
4. **Ongoing**: Graceful handling of unknown formats with logging

### Testing Approach
- Unit tests for all core functionality
- Mock data for parser and UI testing
- Manual integration testing with Claude Code SDK
- No automated LLM testing (non-deterministic)

### Error Handling Philosophy
- User visibility into all session states
- Comprehensive logging for debugging
- Graceful degradation for unknown formats
- Clear recovery paths for crashes and errors

---

## File Structure
```
src/
├── claude_sdk.py             # Claude Code SDK integration
├── session_manager.py        # Session lifecycle and state
├── message_parser.py         # SDK message parsing and types
├── sdk_discovery_tool.py     # SDK message discovery utilities
├── web_server.py             # FastAPI application
├── static/                   # Frontend assets
│   ├── index.html
│   ├── style.css
│   └── app.js
└── tests/                    # Test suite

data/                         # Runtime data (created by app)
├── sessions/
│   └── {session-id}/
│       ├── messages.jsonl
│       ├── state.json
│       └── history.json
└── logs/                     # Application logs
```

---

## Success Criteria

### Phase 1 Complete
- [x] Claude Code SDK integration working reliably
- [x] SDK message capture and parsing working
- [x] Message type library with 5+ discovered types
- [x] Comprehensive error logging implemented

### Phase 2 Complete
- [x] Session creation and management working
- [x] Bidirectional SDK communication functional
- [x] File-based persistence operational
- [x] SDK exception detection and recovery implemented

### Phase 3 Complete (MVP)
- [ ] Web interface loads and connects via WebSocket
- [ ] Real-time message display functional
- [ ] Command sending and history working
- [ ] Mobile-usable responsive design
- [ ] Error visibility and connection status clear

---

## Future Phases (Post-MVP)
- **Phase 4**: Multi-session and project management
- **Phase 5**: Configuration management and settings UI
- **Enhancement**: Advanced mobile optimizations
- **Enhancement**: Performance optimizations for large message logs