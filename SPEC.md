# Claude Code WebUI Specification

## Overview
Build a web-based interface that proxies Claude Code sessions through a headless backend, with hierarchical organization of Projects and Sessions for better configuration management and session persistence.

## Hierarchical Data Model

### Project
A working directory with Claude Code configuration following official standards:
- **Working Directory**: Absolute path where Claude Code sessions will run
- **Claude Code Settings**: Stored in `{project-folder}/.claude/settings.json` following Claude Code's official format:
  - `permissions`: Tool permissions (allow, deny, ask patterns)
  - `env`: Environment variables for sessions
  - `model`: Default Claude model to use
  - `hooks`: Pre/post tool execution hooks
  - `outputStyle`: Output formatting preferences
  - `statusLine`: Custom status line configuration
- **Project Metadata**: Stored in centralized project registry (no files added to project folder):
  - Project name and description
  - Created/modified timestamps
  - WebUI-specific settings
- **Sessions Collection**: All Claude Code sessions within this project

### Session
Individual Claude Code CLI instance within a Project:
- **Session Identity**:
  - Globally unique session ID as valid UUID (required by Claude Code for native session resumption)
  - Human-readable name/description
  - Created timestamp and last active time
- **Session State**:
  - Current status (active, paused, completed, failed)
  - Activity history and command log
  - Claude Code process state for resumption
- **Parent Project**: References Project configuration and working directory

## Architecture Components

### Backend Server (Python + uv)

#### Project Manager
- **Add Project**: Add existing folder as project, with option to create new folder if it doesn't exist
- **Project State Management**: Three distinct options for project lifecycle:
  - **Disable**: Hide project from view (recoverable, show via UI flag for disabled projects)
  - **Remove**: Remove from registry while preserving all session data (re-adding loses session association)
  - **Delete Metadata**: Permanently remove project and delete all associated session data (unrecoverable)
- Only method for data cleanup - no automatic retention policies
- **Rename Project**: Update display name while preserving folder path and all project data
- **Project Validation**: Verify folder exists and is accessible
- Working directory management and permissions
- Project registry persistence and integrity with backwards-compatible schema evolution

#### Session Manager
- Session lifecycle management (create, start, pause, resume, terminate)
- Generate globally unique UUIDs as session IDs for Claude Code compatibility
- Claude Code process spawning in Project working directory (Claude Code auto-detects `.claude/settings.json`)
- Session state persistence and recovery with crash state capture
- Activity history storage and retrieval
- Support for Claude Code's native session resumption (limited to user-provided history; full context reconstruction via captured JSON-LINES stream)
- No artificial limits on concurrent sessions - user service degradation as natural limiting factor

#### Claude Code SDK Handler
- Integrate Claude Code Python SDK with async `query()` function for streaming conversations
- Manage Claude Code sessions through SDK's built-in session management
- Configure SDK using `ClaudeCodeOptions` for project-specific settings (working directory, permissions, tools)
- Handle SDK streaming messages and activity processing through async iterators
- Capture SDK errors and exceptions for user-directed recovery decisions
- No automatic restart - present error information to user for manual recovery action

#### WebSocket Handler
- Real-time bidirectional communication with frontend
- Session-specific WebSocket channels
- Activity streaming and command routing
- Connection management with automatic reconnection and status notifications
- Clear user messaging for failed reconnection attempts with refresh/restart suggestions

#### Activity Parser
- Parse Claude Code SDK streaming messages through async iterator
- Structure SDK message data for frontend consumption with graceful handling of unknown message types
- Maintain session activity history with full message capture for session resumption
- Handle different SDK message types with extensible type system
- Present each streaming message individually (success/failure treated as normal processing)
- Detect SDK errors and exceptions with user warnings and new session prompts
- Graceful error handling with user-facing error reporting (configurable via WebUI settings)

#### Configuration Manager
- Load existing `{project-folder}/.claude/settings.json` for WebUI display if present (file takes precedence on read)
- Create settings file only when user explicitly configures first setting through WebUI
- Write only user-configured settings to file (never add defaults or unconfigured options)
- WebUI changes overwrite file settings when saving (WebUI takes precedence on write)
- Handle configuration validation using Claude Code SDK's `ClaudeCodeOptions` validation
- Claude Code SDK automatically detects and uses settings from working directory
- Assume no settings file exists by default - create only when user configures settings
- WebUI-specific settings including error reporting and debugging options

### Frontend (Vanilla HTML/CSS/JS)
*All frontend components designed with responsive layouts supporting both desktop and mobile browsers - desktop primary with mobile-usable design*

#### Project Browser
- List all available Projects with status (active/disabled)
- **Add Project**: Browse and select existing folder, or specify path for new folder creation
- **Project State Management**: Confirmation dialog with three distinct options:
  - **Disable**: Hide from view (recoverable via UI flag to show disabled projects)
  - **Remove**: Remove from registry while preserving session data (re-adding loses association)
  - **Delete Metadata**: Permanently remove project and delete all session data (unrecoverable)
- **Rename Project**: Edit display name while preserving folder path
- **Project Status**: Show folder accessibility and validation status
- **Responsive Design**: Optimized layouts for desktop and mobile browsers

#### Session Manager
- Within each Project, list all Sessions (active and historical)
- Create new Sessions within selected Project
- Resume paused or interrupted Sessions
- Session status indicators and metadata display
- **Mobile-Optimized**: Touch-friendly interface for session management

#### Activity Stream
- Real-time display of current Session activities (updated once per JSON-LINE)
- Activity type visualization (types determined by actual Claude Code stream-json format)
- Scrollable history with search and filtering
- Activity timestamps and context information
- **Mobile-Responsive**: Optimized scrolling and text rendering for mobile devices with horizontal scrolling support for diffs

#### Command Interface
- Input field for sending commands to active Session with input state awareness
- Command queuing UI - allow typing but prevent submission until session ready for input
- Command history and auto-completion
- Session context awareness and input readiness indicators
- Keyboard shortcuts and accessibility
- **Cross-Platform Input**: Works with both desktop keyboards and mobile touch keyboards

#### Project Settings Panel
- Visual editor for `{project-folder}/.claude/settings.json` (load existing if present, file takes precedence)
- Configure permissions (allow, deny, ask patterns) only when explicitly set
- Set environment variables and model preferences only when explicitly configured
- Manage hooks and output style settings only when user specifies them
- Show existing settings from file (if any) and allow modification
- Create settings file only when user configures first setting (write only user-configured options)
- Preview and validate settings using Claude Code documentation-based parser before saving
- WebUI changes overwrite file settings when saving
- No default values suggested - all settings remain unconfigured until user action
- WebUI-specific settings panel for error reporting and debugging options
- **Responsive Interface**: Desktop primary design with mobile-usable form controls

#### Session History Browser
- Browse activity logs from any Session
- Search across Session activities
- Export Session data and logs (individual user only, no team sharing)
- Session comparison and analysis tools
- **Responsive Tables**: Mobile-optimized data display with horizontal scrolling and collapsible columns

### Data Persistence Strategy

#### File Structure
```
data/
├── projects.json                      # Project registry and metadata
└── sessions/
    ├── {globally_unique_session_id}/  # Session IDs unique across all projects
    │   ├── activity.jsonl            # Activity log
    │   ├── state.json               # Session state
    │   └── history.json             # Command history
    └── ...

# Project working directories remain clean with only Claude Code files:
{project-folder}/
├── .claude/
│   ├── settings.json               # Claude Code settings (official format)
│   └── settings.local.json         # Personal overrides (optional)
└── [project files...]
```

#### Data Models
- **Project Registry**: Centralized registry in `data/projects.json` containing:
  - Project working directory paths
  - WebUI-specific metadata (name, description, created date, last accessed)
  - Project-specific WebUI settings and preferences
  - Schema versioning with backwards-compatible evolution (additions only, no breaking changes)
- **Claude Settings**: Standard `.claude/settings.json` in project folder (only if settings configured; preserves existing configurations)
- **Session State**: Process status, activity history, resumption data with backwards-compatible format evolution
- **Activity Logs**: Timestamped activity entries in JSONL format with permanent retention

## Communication Protocol

### WebSocket Message Types
- `project:list` - Request all Projects (active and disabled)
- `project:add` - Add existing folder as Project (with optional folder creation)
- `project:remove` - Remove Project from registry (with confirmation options)
- `project:update` - Update Project metadata (name, settings, etc.)
- `session:list` - Request Sessions for Project
- `session:create` - Create new Session
- `session:resume` - Resume existing Session
- `session:command` - Send command to active Session (with input readiness validation)
- `session:crash` - Process crash notification with state and recovery options
- `activity:stream` - Real-time activity updates (once per JSON-LINE)
- `input:ready` - Session ready for input notification
- `connection:status` - WebSocket connection status notifications
- `data:corrupted` - Data corruption warning with new session prompts
- `error` - Error notifications and handling

### Activity Stream Format
- Message type identification from Claude Code SDK streaming responses
- Timestamp and session context with full message capture for session resumption
- Structured data for different SDK message types with extensible type handling
- Progress indicators and status updates based on SDK message format with graceful degradation for unknown types

## Development Phases

### Phase 1: Claude Code SDK Integration & Discovery
1. **Core Claude Code SDK Integration**
   - Establish basic Claude Code SDK integration using async `query()` function
   - Capture and analyze actual SDK streaming message formats
   - Test SDK configuration and session management
   - Document real message types and data structures from SDK with sample collection

2. **SDK Message Parser Development**
   - Build flexible parser that handles unknown/future SDK message types gracefully
   - Implement extensible type system with detailed error reporting for unknown formats
   - Create message data structures based on discovered format with version compatibility handling
   - Test with various Claude Code SDK scenarios, collecting format samples

### Phase 2: Basic Session Management
1. **Single Session Manager**
   - Implement SDK session handling with configurable `ClaudeCodeOptions`
   - Implement session state tracking and basic persistence
   - Handle session lifecycle (start, active, terminate)
   - Build robust error handling and recovery

2. **Session Communication**
   - Develop bidirectional communication using SDK async iterators
   - Test message handling and streaming response processing
   - Implement activity streaming and buffering with error detection
   - Handle SDK exceptions, errors, and session state changes

### Phase 3: Basic WebUI for Single Session
1. **Minimal Web Interface**
   - Simple HTML/CSS/JS interface for single session
   - Real-time activity display using WebSockets
   - Basic command input and sending
   - Mobile-responsive single-session interface

2. **Activity Visualization**
   - Display activity stream based on SDK message formats (streaming message updates)
   - Handle unknown message types gracefully with individual success/failure presentation
   - Implement basic scrolling and history display with horizontal scrolling for diffs
   - Test with various Claude Code SDK messages including error scenarios

### Phase 4: Multi-Session & Project Management
1. **Project Structure**
   - Extend to multiple sessions and project organization
   - Implement project registry and metadata management
   - Add project lifecycle management (add, remove, rename)
   - Build project browser interface

2. **Advanced Session Features**
   - Session resumption capabilities (explore limitations)
   - Multiple concurrent sessions
   - Session history and persistence
   - Cross-session navigation and management

### Phase 5: Configuration & Polish
1. **Settings Management**
   - Claude Code settings file integration with precedence rules (file on read, WebUI on write)
   - Project-level configuration interface with no defaults
   - Settings validation using documentation-based parser and preview
   - Handle configuration conflicts with clear precedence rules
   - WebUI-specific settings for error reporting and debugging

2. **Production Features**
   - Enhanced mobile interface with horizontal scrolling for diffs
   - Performance optimization with JSON-LINE-based updates
   - Comprehensive error handling including network failures and data corruption
   - Documentation and deployment guides

### Future-Proofing Principle
- **Unknown Format Handling**: All SDK message parsing must gracefully handle unknown types and attributes with detailed error reporting
- **Extensible Design**: Activity display and processing should adapt to new Claude Code SDK features with version compatibility considerations
- **Rapid Version Changes**: Handle Claude Code SDK version changes gracefully with comprehensive error logging to identify and address format changes quickly
- **Backwards Compatibility**: All data formats must evolve through additions only - no breaking changes to existing schemas

## Key Benefits

### Organizational Structure
- **Project-based Organization**: Group related work and maintain consistent environments
- **Session Continuity**: Resume interrupted work with full context and history
- **Configuration Inheritance**: Set up MCP servers and permissions once per Project
- **Scalable Management**: Handle multiple ongoing projects efficiently

### Technical Advantages
- **Headless Operation**: Run Claude Code remotely without GUI dependencies
- **Multi-device Access**: Access sessions from any device with web browser
- **Session Persistence**: Never lose work due to connection issues or restarts
- **Activity History**: Complete audit trail of all Claude Code interactions

### User Experience
- **Familiar Workflow**: Mirrors typical development project organization
- **Single-User Interface**: Dedicated personal workspace for Claude Code sessions
- **Multi-Device Interface**: Desktop primary with mobile-usable functionality on tablets and mobile devices
- **Touch-Compatible Interface**: Desktop-optimized with touch support for mobile users
- **Centralized Management**: Single interface for all Claude Code projects and sessions
- **Cross-Device Continuity**: Future feature for continuing sessions across devices (post-MVP)

## Technical Requirements

### Backend Dependencies
- Python 3.13+ with uv package manager
- Claude Code Python SDK (claude-code package)
- WebSocket library (websockets or similar)
- JSON handling and file I/O
- Async/await support for SDK integration

### Frontend Requirements
- No build dependencies (vanilla HTML/CSS/JavaScript)
- Modern browser with WebSocket support
- **Responsive Design**: Desktop-optimized layouts that work on mobile phones, tablets, and desktop
- Touch-compatible interface for mobile devices
- Accessible interface following web standards
- Cross-platform input support (keyboard, touch, voice)

### System Requirements
- Cross-platform compatibility (Windows, macOS, Linux)
- File system access for Project working directories
- Network access for WebSocket communication
- Sufficient system resources for multiple Claude Code processes
- Single-user setup - no multi-instance support required