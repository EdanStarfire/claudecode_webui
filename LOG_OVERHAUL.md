# Comprehensive Logging System Refactor Plan

**Created**: 2025-01-15
**Status**: Planning Complete - Ready for Implementation
**Estimated Impact**: ~500+ log statements across 10+ files

## Overview
Implement a configurable, multi-tier logging system with proper log levels, file-based outputs, and CLI toggles for debugging capabilities.

---

## Architecture Changes

### 1. CLI Argument System
Add new CLI flags to control logging behavior:
- `--debug-websocket` - Enable WebSocket lifecycle debugging
- `--debug-sdk` - Enable SDK integration debugging
- `--debug-permissions` - Enable permission callback debugging
- `--debug-storage` - Enable data storage debugging
- `--debug-parser` - Enable message parser debugging
- `--debug-error-handler` - Enable error handler debugging
- `--debug-all` - Enable all debug logging

### 2. Log File Structure
Create new log directory structure under `data/logs/`:
```
data/logs/
├── error.log              # All errors from all sources
├── websocket_debug.log    # WS_LIFECYCLE + ping/pong (when enabled)
├── sdk_debug.log          # SDK operations + permissions (when enabled)
├── coordinator.log        # Session lifecycle actions (always on)
├── storage.log            # Database operations (when enabled)
└── parser.log             # Message parsing (when enabled)
```

### 3. Standardized Log Format
**Python Format:**
```
2025-01-15T14:32:45.123 INFO - [WS_LIFECYCLE] WebSocket connected for session abc123
2025-01-15T14:32:45.124 ERROR - [SDK] Fatal error in query loop: <details>
```

**JavaScript Format:**
```
2025-01-15T14:32:45.123 INFO - [TOOL_MANAGER] Handling tool use: Read
2025-01-15T14:32:45.124 DEBUG - [SESSION] Switching to session abc123
```

---

## User Requirements Summary

### Python Console Behavior
- **Default**: Show only coordinator actions + all errors
- **With Debug Flags**: Show category-specific debug logs to console + file
- **Always**: Duplicate all ERROR/WARNING to both console and `error.log`

### Log File Routing

| Category | Default File | When Enabled | CLI Flag |
|----------|-------------|--------------|----------|
| WS_LIFECYCLE | None | `websocket_debug.log` | `--debug-websocket` |
| Ping/Pong | None | `websocket_debug.log` | `--debug-websocket` |
| SDK Integration | None | `sdk_debug.log` | `--debug-sdk` |
| Permissions | None | `sdk_debug.log` | `--debug-permissions` or `--debug-sdk` |
| Session Coordinator | `coordinator.log` | `coordinator.log` | Always on |
| Data Storage | None | `storage.log` | `--debug-storage` |
| Message Parser | None | `parser.log` | `--debug-parser` |
| Error Handler | `error.log` | `error.log` | Always on |
| All Errors | `error.log` | `error.log` | Always on |

### WebUI Console Behavior
All JavaScript logs should use proper log levels:
- `console.debug()` - Verbose debugging (filtered by browser settings)
- `console.info()` - Important events (connections, actions)
- `console.warn()` - Warnings (retries, fallbacks)
- `console.error()` - Errors (failures, exceptions)

**Key User Actions**: Always log to console at INFO level:
- Session creation/deletion
- Message send/receive
- Interrupt/Stop events
- Permission decisions

---

## Implementation Details

### Python Logging Refactor

#### A. Update `src/logging_config.py`

**Current State**: Single logger configuration with file and console handlers

**Changes Required**:
1. Create named loggers for each subsystem:
   ```python
   LOGGERS = {
       'websocket_debug': 'data/logs/websocket_debug.log',
       'sdk_debug': 'data/logs/sdk_debug.log',
       'coordinator': 'data/logs/coordinator.log',
       'storage': 'data/logs/storage.log',
       'parser': 'data/logs/parser.log',
       'error_handler': 'data/logs/error.log',
   }
   ```

2. Implement custom formatter with standardized format:
   ```python
   class StandardizedFormatter(logging.Formatter):
       def format(self, record):
           # Format: YYYY-MM-DDTHH:mm:ss.mmm LEVEL - [CATEGORY] Message
           timestamp = self.formatTime(record, '%Y-%m-%dT%H:%M:%S')
           ms = f"{record.msecs:03.0f}"
           category = getattr(record, 'category', 'GENERAL')
           return f"{timestamp}.{ms} {record.levelname} - [{category}] {record.getMessage()}"
   ```

3. Add file handlers for each log category:
   - Create `data/logs/` directory if not exists
   - Each logger gets dedicated file handler
   - All loggers also send ERROR+ to `error.log` via shared error handler

4. Add CLI argument parsing:
   ```python
   def configure_logging(
       debug_websocket=False,
       debug_sdk=False,
       debug_permissions=False,
       debug_storage=False,
       debug_parser=False,
       debug_error_handler=False,
       debug_all=False
   ):
       # Enable loggers based on flags
       # Default: Only coordinator and error logs active
   ```

5. Console handler rules:
   - `coordinator` logger: Always to console (INFO+)
   - `error_handler` logger: Always to console (ERROR+)
   - Debug loggers: Only to console when flag enabled
   - Shared error handler: Always sends ERROR+ to console

---

#### B. Update `src/web_server.py`

**Category 1 & 2: WS_LIFECYCLE + Ping/Pong**

**Lines Affected**: 467-703

**Current Pattern**:
```python
logger.info(f"[WS_LIFECYCLE] WebSocket connection attempt for session {session_id}")
logger.debug(f"[WS_LIFECYCLE] Message loop iteration {message_loop_iteration}")
logger.debug(f"[WS_LIFECYCLE] Ping sent successfully at {ping_sent_time}")
```

**New Pattern**:
```python
# Create module-level logger
from logging_config import get_logger
ws_logger = get_logger('websocket_debug', category='WS_LIFECYCLE')

# Usage
ws_logger.debug(f"WebSocket connection attempt for session {session_id}")
ws_logger.debug(f"Message loop iteration {message_loop_iteration}")
ws_logger.debug(f"Ping sent successfully at {ping_sent_time}")

# Errors still go to main logger AND error.log
logger.error(f"WebSocket error for session {session_id}: {error}")
```

**Changes**:
- Replace ~40 `logger.info/debug(f"[WS_LIFECYCLE]...")` calls with `ws_logger.debug()`
- Remove manual timestamp tracking (formatter handles this)
- Keep ERROR/WARNING logs using main logger (goes to console + error.log)
- Ping/pong logs become `ws_logger.debug()` calls

**Specific Lines**:
- 467-508: Connection establishment → `ws_logger.debug()`
- 509-530: Initial message sending → `ws_logger.debug()`
- 535-681: Message loop → `ws_logger.debug()`
- 659-672: Ping/pong → `ws_logger.debug()`
- 685-703: Cleanup → `ws_logger.info()` for summary, `ws_logger.debug()` for details

---

#### C. Update `src/claude_sdk.py`

**Category 3: SDK Logs + Category 6: Permission Logs**

**Lines Affected**: Throughout file (120+ logger calls)

**Current Pattern**:
```python
logger.info(f"DEBUG: ClaudeSDK initialized with permission_callback: {permission_callback is not None}")
logger.info(f"[SDK_LIFECYCLE] Message processing loop cleanup")
logger.error(f"[ERROR_TRACKING] FATAL ERROR - Full exception traceback:")
logger.info(f"PERMISSION CALLBACK TRIGGERED: tool={tool_name}")
```

**New Pattern**:
```python
# Create module-level loggers
from logging_config import get_logger
sdk_logger = get_logger('sdk_debug', category='SDK')
perm_logger = get_logger('sdk_debug', category='PERMISSIONS')

# Usage
sdk_logger.debug(f"ClaudeSDK initialized with permission_callback: {permission_callback is not None}")
sdk_logger.info(f"Message processing loop cleanup")
sdk_logger.error(f"FATAL ERROR - Full exception traceback:")
perm_logger.info(f"Permission callback triggered: tool={tool_name}")

# Errors still duplicate to error.log via logging config
```

**Changes**:
1. **Debug logs** (lines 153-157, 656-676):
   - Change to `sdk_logger.debug()`
   - Remove "DEBUG:" prefix (redundant with log level)

2. **SDK Lifecycle** (lines 417-623):
   - `[SDK_LIFECYCLE]` → `sdk_logger.info()` for major events
   - `[SDK_LIFECYCLE]` → `sdk_logger.debug()` for detailed tracking
   - Keep ERROR logs for fatal errors

3. **Error Tracking** (lines 581-612):
   - `[ERROR_TRACKING]` → `sdk_logger.error()` for errors
   - Full tracebacks → `sdk_logger.debug()` for details

4. **Permission Callbacks** (lines 824-870):
   - Move to `perm_logger.info()` for callback triggers
   - Move to `perm_logger.debug()` for detailed decision logic
   - Enabled when `--debug-permissions` OR `--debug-sdk` flag set

5. **SDK Error Detection** (lines 69-93):
   - Keep as `sdk_logger.error()` for detection
   - Details → `sdk_logger.debug()`

**Specific Sections**:
- 153-157: Init debugging → `sdk_logger.debug()`
- 202, 242-286: Session startup → `sdk_logger.info()` for key events
- 304-321: Message queueing → `sdk_logger.debug()`
- 337-363: Interrupt handling → `sdk_logger.info()` for action, `sdk_logger.debug()` for details
- 379-407: Permission mode → `sdk_logger.info()`
- 417-623: Message loop → `sdk_logger.info()` for lifecycle, `sdk_logger.debug()` for iterations
- 656-870: Permission system → `perm_logger.info()` for callbacks

---

#### D. Update `src/session_coordinator.py`

**Category 12: Session Coordinator**

**Lines Affected**: 94 logger calls throughout file

**Current Pattern**:
```python
logger.info(f"Session {session_id} is already running, skipping start")
# (Many debug-level state transitions and queue operations)
```

**New Pattern**:
```python
# Create module-level logger
from logging_config import get_logger
coord_logger = get_logger('coordinator', category='COORDINATOR')

# Usage - Focus on ACTIONS only
coord_logger.info(f"Session {session_id} created")
coord_logger.info(f"Session {session_id} started")
coord_logger.info(f"Session {session_id} stopped")
coord_logger.info(f"Session {session_id} deleted")

# Errors
logger.error(f"Failed to start session {session_id}: {error}")
```

**Changes**:
1. **Keep** (as `coord_logger.info()`):
   - Session create/start/stop/delete actions
   - Message forwarding (high-level only)
   - State changes (major transitions only)

2. **Remove**:
   - Verbose state transition logs (SDK handles this)
   - Queue operation details
   - Debug-level session state tracking
   - Redundant "already running" messages

3. **Refactor Goals**:
   - Reduce from ~94 calls to ~30 high-value action logs
   - Always output to `coordinator.log` (no flag needed)
   - Keep errors in main logger → console + `error.log`

---

#### E. Update `src/data_storage.py`

**Category 13: Data Storage**

**Lines Affected**: 13 logger calls

**Current Pattern**:
```python
logger.info(f"Saving message to database: {message_type}")
logger.debug(f"Database query: {query}")
```

**New Pattern**:
```python
from logging_config import get_logger
storage_logger = get_logger('storage', category='STORAGE')

# Only active when --debug-storage flag set
storage_logger.debug(f"Saving message to database: {message_type}")
storage_logger.debug(f"Database query: {query}")

# Errors always active
logger.error(f"Database error: {error}")
```

**Changes**:
- Move all 13 logger calls to `storage_logger.debug()`
- Only output to `storage.log` when `--debug-storage` enabled
- Keep errors in main logger

---

#### F. Update `src/message_parser.py`

**Category 14: Message Parser**

**Lines Affected**: 22 logger calls

**Current Pattern**:
```python
logger.debug(f"Parsing SDK message type: {message_type}")
logger.info(f"Converted SDK message to WebSocket format")
```

**New Pattern**:
```python
from logging_config import get_logger
parser_logger = get_logger('parser', category='PARSER')

# Only active when --debug-parser flag set
parser_logger.debug(f"Parsing SDK message type: {message_type}")
parser_logger.debug(f"Converted SDK message to WebSocket format")

# Errors always active
logger.error(f"Failed to parse message: {error}")
```

**Changes**:
- Move all 22 logger calls to `parser_logger.debug()`
- Only output to `parser.log` when `--debug-parser` enabled
- Keep errors in main logger

---

#### G. Update `src/error_handler.py`

**Category 15: Error Handler**

**Lines Affected**: 5 logger calls

**Current Pattern**:
```python
logger.error(f"Error detected: {error_details}")
```

**New Pattern**:
```python
from logging_config import get_logger
error_logger = get_logger('error_handler', category='ERROR_HANDLER')

# Only active when --debug-error-handler flag set
error_logger.debug(f"Error handler processing: {error_details}")

# All errors always go to error.log + console
logger.error(f"Error detected: {error_details}")
```

**Changes**:
- Move debug-level processing to `error_logger.debug()`
- Only output to `error.log` when `--debug-error-handler` enabled
- Keep actual errors in main logger (always active)

---

#### H. Update Main Entry Point (Server Startup)

**File**: `src/web_server.py` or main entry script

**Add CLI Argument Parsing**:
```python
import argparse

parser = argparse.ArgumentParser(description='Claude WebUI Server')
parser.add_argument('--debug-websocket', action='store_true', help='Enable WebSocket debug logging')
parser.add_argument('--debug-sdk', action='store_true', help='Enable SDK debug logging')
parser.add_argument('--debug-permissions', action='store_true', help='Enable permissions debug logging')
parser.add_argument('--debug-storage', action='store_true', help='Enable storage debug logging')
parser.add_argument('--debug-parser', action='store_true', help='Enable parser debug logging')
parser.add_argument('--debug-error-handler', action='store_true', help='Enable error handler debug logging')
parser.add_argument('--debug-all', action='store_true', help='Enable all debug logging')

args = parser.parse_args()

# Configure logging based on flags
from logging_config import configure_logging
configure_logging(
    debug_websocket=args.debug_websocket or args.debug_all,
    debug_sdk=args.debug_sdk or args.debug_all,
    debug_permissions=args.debug_permissions or args.debug_all,
    debug_storage=args.debug_storage or args.debug_all,
    debug_parser=args.debug_parser or args.debug_all,
    debug_error_handler=args.debug_error_handler or args.debug_all
)
```

---

### JavaScript Logging Refactor

#### I. Add Standardized Logging Utility to `static/app.js`

**Add at top of file (after initial declarations)**:

```javascript
/**
 * Standardized logging utility for consistent log formatting
 * Uses browser console's native log levels for filtering
 */
const Logger = {
    /**
     * Format a log message with timestamp and category
     * @param {string} category - Log category (e.g., 'TOOL_MANAGER', 'SESSION', 'WS')
     * @param {string} message - The log message
     * @param {*} data - Optional structured data to append
     * @returns {string} Formatted log message
     */
    formatMessage(category, message, data = null) {
        const timestamp = new Date().toISOString();
        const baseMsg = `${timestamp} - [${category}] ${message}`;

        if (data !== null && data !== undefined) {
            // For objects/arrays, pretty print on new line
            if (typeof data === 'object') {
                return `${baseMsg}\n${JSON.stringify(data, null, 2)}`;
            }
            return `${baseMsg} ${data}`;
        }

        return baseMsg;
    },

    /**
     * Debug level - verbose debugging, filtered by browser settings
     */
    debug(category, message, data = null) {
        console.debug(this.formatMessage(category, message, data));
    },

    /**
     * Info level - important events (connections, actions, completions)
     */
    info(category, message, data = null) {
        console.info(this.formatMessage(category, message, data));
    },

    /**
     * Warning level - warnings (retries, fallbacks, deprecated paths)
     */
    warn(category, message, data = null) {
        console.warn(this.formatMessage(category, message, data));
    },

    /**
     * Error level - errors (failures, exceptions)
     */
    error(category, message, data = null) {
        console.error(this.formatMessage(category, message, data));
    }
};
```

---

#### J. Update JavaScript Logging Throughout `static/app.js`

**Category 4: Tool Call Manager** (lines 15-145)

| Current | New | Reasoning |
|---------|-----|-----------|
| `console.error('createToolSignature called with undefined toolName')` | `Logger.error('TOOL_MANAGER', 'createToolSignature called with undefined toolName', {toolName, inputParams})` | Error condition |
| `console.warn('createToolSignature called with invalid inputParams')` | `Logger.warn('TOOL_MANAGER', 'createToolSignature called with invalid inputParams', {toolName, inputParams})` | Warning condition |
| `console.log('ToolCallManager: Handling tool use', toolUseBlock)` | `Logger.debug('TOOL_MANAGER', 'Handling tool use', toolUseBlock)` | Debug detail |
| `console.log('ToolCallManager: Handling permission request', permissionRequest)` | `Logger.debug('TOOL_MANAGER', 'Handling permission request', permissionRequest)` | Debug detail |
| `console.debug('ToolCallManager: Creating historical tool call for unknown tool')` | `Logger.debug('TOOL_MANAGER', 'Creating historical tool call for unknown tool', permissionRequest)` | Already debug |
| `console.log('ToolCallManager: Handling permission response', permissionResponse)` | `Logger.debug('TOOL_MANAGER', 'Handling permission response', permissionResponse)` | Debug detail |
| `console.log('ToolCallManager: Handling tool result', toolResultBlock)` | `Logger.debug('TOOL_MANAGER', 'Handling tool result', toolResultBlock)` | Debug detail |
| `console.log('ToolCallManager: Handling assistant explanation')` | `Logger.debug('TOOL_MANAGER', 'Handling assistant explanation', {assistantMessage, relatedToolIds})` | Debug detail |

---

**Category 5: Session Management** (lines 2060-4407)

| Current | New | Reasoning |
|---------|-----|-----------|
| `console.error('Failed to load sessions:', error)` | `Logger.error('SESSION', 'Failed to load sessions', error)` | Error |
| `console.error('Failed to create session:', error)` | `Logger.error('SESSION', 'Failed to create session', error)` | Error |
| `console.log('Exiting session ${this.currentSessionId}')` | `Logger.info('SESSION', 'Exiting session', {sessionId: this.currentSessionId})` | Important action |
| `console.log('Skipping loadSessionInfo for session - deletion in progress')` | `Logger.debug('SESSION', 'Skipping loadSessionInfo - deletion in progress', {sessionId})` | Debug detail |
| `console.log('Session not found (404) - likely deleted')` | `Logger.info('SESSION', 'Session not found (404) - likely deleted', {sessionId})` | Important event |
| `console.log('Already connected to session ${sessionId}')` | `Logger.debug('SESSION', 'Already connected to session', {sessionId})` | Debug detail |
| `console.log('Switching from session ${this.currentSessionId} to ${sessionId}')` | `Logger.info('SESSION', 'Switching sessions', {from: this.currentSessionId, to: sessionId})` | Important action |
| `console.log('Session is in error state, skipping WebSocket connection')` | `Logger.warn('SESSION', 'Session in error state, skipping WebSocket', {sessionId})` | Warning |
| `console.log('Session is now active, connecting WebSocket')` | `Logger.info('SESSION', 'Session active, connecting WebSocket', {sessionId})` | Important event |
| `console.log('Waiting for session to become active... (attempt ${attempts}/${maxAttempts})')` | `Logger.debug('SESSION', 'Waiting for session to become active', {sessionId, attempts, maxAttempts})` | Debug detail |
| `console.warn('Session did not become active after ${maxAttempts} attempts')` | `Logger.warn('SESSION', 'Session did not become active', {sessionId, maxAttempts, totalTime})` | Warning |
| `console.error('Failed to save session name:', error)` | `Logger.error('SESSION', 'Failed to save session name', error)` | Error |
| `console.log('Handling external deletion of session ${sessionId}')` | `Logger.info('SESSION', 'Handling external deletion', {sessionId})` | Important event |
| `console.error('Session creation failed:', error)` | `Logger.error('SESSION', 'Session creation failed', error)` | Error |
| `console.log('Session ${sessionIdToDelete} deleted successfully')` | `Logger.info('SESSION', 'Session deleted successfully', {sessionId: sessionIdToDelete})` | Important action |
| `console.error('Failed to delete session:', error)` | `Logger.error('SESSION', 'Failed to delete session', error)` | Error |
| `console.warn('Session ${sessionId} not found in orderedSessions, adding it')` | `Logger.warn('SESSION', 'Session not found in orderedSessions, adding it', {sessionId})` | Warning |
| `console.error('Failed to reorder sessions:', error)` | `Logger.error('SESSION', 'Failed to reorder sessions', error)` | Error |
| `console.log('Sessions reordered successfully')` | `Logger.info('SESSION', 'Sessions reordered successfully')` | Important action |

---

**Category 6 & 7: Permission Mode & Message Processing** (lines 2384-2859)

| Current | New | Reasoning |
|---------|-----|-----------|
| `console.log('Processing message from ${source}:', message)` | `Logger.debug('MESSAGE', 'Processing message', {source, type: message.type})` | Debug detail |
| `console.log('Adding ${source} message to UI:', message.type)` | `Logger.debug('MESSAGE', 'Adding message to UI', {source, type: message.type})` | Debug detail |
| `console.log('Permission mode changed from ${this.currentPermissionMode} to ${permissionMode}')` | `Logger.info('PERMISSION', 'Permission mode changed', {from: this.currentPermissionMode, to: permissionMode})` | Important change |
| `console.error('Error extracting permission mode:', error)` | `Logger.error('PERMISSION', 'Error extracting permission mode', error)` | Error |
| `console.log('Updating UI - Current permission mode: ${mode}')` | `Logger.debug('PERMISSION', 'Updating UI permission mode', {mode})` | Debug detail |
| `console.log('ExitPlanMode completed successfully - updating permission mode to default')` | `Logger.info('PERMISSION', 'ExitPlanMode completed, updating to default mode')` | Important event |
| `console.error('Error handling tool-related message:', error, message)` | `Logger.error('MESSAGE', 'Error handling tool-related message', {error, message})` | Error |
| `console.log('Processing thinking blocks:', thinkingBlocks)` | `Logger.debug('MESSAGE', 'Processing thinking blocks', thinkingBlocks)` | Debug detail |
| `console.error('Error handling thinking block message:', error, message)` | `Logger.error('MESSAGE', 'Error handling thinking block', {error, message})` | Error |
| `console.log('Rendering tool call:', toolCall)` | `Logger.debug('UI', 'Rendering tool call', toolCall)` | Debug detail |
| `console.log('Updating tool call:', toolCall)` | `Logger.debug('UI', 'Updating tool call', toolCall)` | Debug detail |
| `console.log('Permission decision:', requestId, decision)` | `Logger.info('PERMISSION', 'Permission decision', {requestId, decision})` | Important action |
| `console.error('Cannot send permission response: WebSocket not connected')` | `Logger.error('PERMISSION', 'Cannot send permission response - WebSocket not connected')` | Error |
| `console.log('Rendering thinking block:', thinkingBlock)` | `Logger.debug('UI', 'Rendering thinking block', thinkingBlock)` | Debug detail |

---

**Category 8: Interrupt/Stop** (lines 2169-2213, 3299-3306)

| Current | New | Reasoning |
|---------|-----|-----------|
| `console.error('Failed to send message:', error)` | `Logger.error('MESSAGE', 'Failed to send message', error)` | Error |
| `console.log('DEBUG: sendInterrupt() called but conditions not met')` | `Logger.debug('INTERRUPT', 'sendInterrupt() called but conditions not met', {hasSession, isProcessing, hasWebSocket})` | Debug detail |
| `console.log('DEBUG: Sending interrupt request for session:', this.currentSessionId)` | `Logger.info('INTERRUPT', 'Sending interrupt request', {sessionId: this.currentSessionId})` | **Important user action** |
| `console.log('DEBUG: WebSocket state check:', {hasWS, readyState, OPEN})` | `Logger.debug('INTERRUPT', 'WebSocket state check', {hasWS, readyState, OPEN})` | Debug detail |
| `console.log('DEBUG: Sending interrupt message via WebSocket:', interruptMessage)` | `Logger.debug('INTERRUPT', 'Sending interrupt via WebSocket', interruptMessage)` | Debug detail |
| `console.log('DEBUG: Interrupt message sent successfully')` | `Logger.info('INTERRUPT', 'Interrupt sent successfully')` | **Important confirmation** |
| `console.warn('DEBUG: WebSocket not connected, cannot send interrupt')` | `Logger.warn('INTERRUPT', 'WebSocket not connected, cannot send interrupt', {state})` | Warning |
| `console.log('DEBUG: WebSocket connection details:', {hasWS, readyState})` | `Logger.debug('INTERRUPT', 'WebSocket connection details', {hasWS, readyState})` | Debug detail |
| `console.error('DEBUG: Failed to send interrupt:', error)` | `Logger.error('INTERRUPT', 'Failed to send interrupt', error)` | Error |
| `console.log('Interrupt response received:', data)` | `Logger.debug('INTERRUPT', 'Interrupt response received', data)` | Debug detail |
| `console.log('Interrupt successful:', data.message)` | `Logger.info('INTERRUPT', 'Interrupt successful', {message: data.message})` | **Important confirmation** |
| `console.warn('Interrupt failed:', data.message)` | `Logger.warn('INTERRUPT', 'Interrupt failed', {message: data.message})` | Warning |

---

**Category 9: WebSocket Connection** (lines 3016-3267)

| Current | New | Reasoning |
|---------|-----|-----------|
| `console.error('Cannot set permission mode: no active session')` | `Logger.error('PERMISSION', 'Cannot set permission mode - no active session')` | Error |
| `console.log('Setting permission mode to: ${mode}')` | `Logger.info('PERMISSION', 'Setting permission mode', {mode})` | Important action |
| `console.log('Permission mode successfully set to: ${response.mode}')` | `Logger.info('PERMISSION', 'Permission mode set successfully', {mode: response.mode})` | Important confirmation |
| `console.error('Failed to set permission mode on backend')` | `Logger.error('PERMISSION', 'Failed to set permission mode on backend')` | Error |
| `console.error('Error setting permission mode:', error)` | `Logger.error('PERMISSION', 'Error setting permission mode', error)` | Error |
| `console.log('Loading all messages with pagination...')` | `Logger.debug('MESSAGE', 'Loading all messages with pagination')` | Debug detail |
| `console.log('Loading messages page: offset=${offset}, limit=${pageSize}')` | `Logger.debug('MESSAGE', 'Loading messages page', {offset, limit: pageSize})` | Debug detail |
| `console.log('Loaded ${response.messages.length} messages, total so far: ${allMessages.length}')` | `Logger.debug('MESSAGE', 'Messages loaded', {loaded: response.messages.length, total: allMessages.length, hasMore})` | Debug detail |
| `console.log('Finished loading all ${allMessages.length} messages')` | `Logger.info('MESSAGE', 'Finished loading all messages', {count: allMessages.length})` | Important completion |
| `console.error('Failed to load messages:', error)` | `Logger.error('MESSAGE', 'Failed to load messages', error)` | Error |
| `console.log('UI WebSocket already connected')` | `Logger.debug('WS_UI', 'WebSocket already connected')` | Debug detail |
| `console.log('Connecting to UI WebSocket...')` | `Logger.info('WS_UI', 'Connecting to UI WebSocket')` | Important action |
| `console.log('UI WebSocket connected successfully')` | `Logger.info('WS_UI', 'WebSocket connected successfully')` | Important confirmation |
| `console.error('Error parsing UI WebSocket message:', error)` | `Logger.error('WS_UI', 'Error parsing WebSocket message', error)` | Error |
| `console.log('UI WebSocket disconnected', event.code, event.reason)` | `Logger.info('WS_UI', 'WebSocket disconnected', {code: event.code, reason: event.reason})` | Important event |
| `console.log('Reconnecting UI WebSocket in ${delay}ms (attempt ${this.uiConnectionRetryCount}/${this.maxUIRetries})')` | `Logger.debug('WS_UI', 'Reconnecting WebSocket', {delay, attempt: this.uiConnectionRetryCount, max: this.maxUIRetries})` | Debug detail |
| `console.log('Max UI WebSocket reconnection attempts reached')` | `Logger.warn('WS_UI', 'Max reconnection attempts reached', {attempts: this.maxUIRetries})` | Warning |
| `console.error('UI WebSocket error:', error)` | `Logger.error('WS_UI', 'WebSocket error', error)` | Error |
| `console.log('UI WebSocket message received:', data.type)` | `Logger.debug('WS_UI', 'WebSocket message received', {type: data.type})` | Debug detail |
| `console.debug('UI WebSocket pong received')` | `Logger.debug('WS_UI', 'Pong received')` | Debug detail |
| `console.log('Unknown UI WebSocket message type:', data.type)` | `Logger.warn('WS_UI', 'Unknown WebSocket message type', {type: data.type})` | Warning |
| `console.log('Updating sessions list with ${sessions.length} sessions')` | `Logger.debug('SESSION', 'Updating sessions list', {count: sessions.length})` | Debug detail |
| `console.log('Refreshing sessions via API fallback')` | `Logger.debug('SESSION', 'Refreshing sessions via API fallback')` | Debug detail |
| `console.log('Closing existing session WebSocket connection before creating new one')` | `Logger.debug('WS_SESSION', 'Closing existing WebSocket before creating new one')` | Debug detail |
| `console.log('Connecting session WebSocket for session: ${this.currentSessionId}')` | `Logger.info('WS_SESSION', 'Connecting session WebSocket', {sessionId: this.currentSessionId})` | Important action |
| `console.log('WebSocket connected')` | `Logger.info('WS_SESSION', 'WebSocket connected')` | Important confirmation |
| `console.error('Failed to parse WebSocket message:', error)` | `Logger.error('WS_SESSION', 'Failed to parse WebSocket message', error)` | Error |
| `console.log('WebSocket disconnected', event.code, event.reason)` | `Logger.info('WS_SESSION', 'WebSocket disconnected', {code: event.code, reason: event.reason})` | Important event |
| `console.log('WebSocket closed intentionally, not retrying')` | `Logger.debug('WS_SESSION', 'WebSocket closed intentionally, not retrying')` | Debug detail |
| `console.log('WebSocket closed with error code ${event.code}, not retrying')` | `Logger.warn('WS_SESSION', 'WebSocket closed with error, not retrying', {code: event.code})` | Warning |
| `console.error('WebSocket error:', error)` | `Logger.error('WS_SESSION', 'WebSocket error', error)` | Error |
| `console.error('Failed to create WebSocket:', error)` | `Logger.error('WS_SESSION', 'Failed to create WebSocket', error)` | Error |
| `console.log('Reconnect cancelled due to intentional disconnect')` | `Logger.debug('WS_SESSION', 'Reconnect cancelled - intentional disconnect')` | Debug detail |
| `console.log('Scheduling WebSocket reconnect in ${delay}ms (attempt ${this.sessionConnectionRetryCount})')` | `Logger.debug('WS_SESSION', 'Scheduling WebSocket reconnect', {delay, attempt: this.sessionConnectionRetryCount})` | Debug detail |
| `console.log('Max reconnection attempts reached')` | `Logger.warn('WS_SESSION', 'Max reconnection attempts reached')` | Warning |
| `console.log('WebSocket message received:', data)` | `Logger.debug('WS_SESSION', 'WebSocket message received', data)` | Debug detail |
| `console.log('WebSocket connection confirmed for session:', data.session_id)` | `Logger.info('WS_SESSION', 'Connection confirmed', {sessionId: data.session_id})` | Important confirmation |
| `console.log('Unknown WebSocket message type:', data.type)` | `Logger.warn('WS_SESSION', 'Unknown message type', {type: data.type})` | Warning |

---

**Category 10 & 11: API Errors & UI State** (various lines)

| Current | New | Reasoning |
|---------|-----|-----------|
| `console.error('API request failed:', error)` | `Logger.error('API', 'Request failed', error)` | Error |
| `console.log('Cannot cycle permission mode - session not active')` | `Logger.debug('UI', 'Cannot cycle permission mode - session not active')` | Debug detail |
| `console.log('Cycling permission mode from ${currentMode} to ${nextMode}')` | `Logger.info('PERMISSION', 'Cycling permission mode', {from: currentMode, to: nextMode})` | Important action |
| `console.log('Successfully changed permission mode to ${nextMode}')` | `Logger.info('PERMISSION', 'Permission mode changed successfully', {mode: nextMode})` | Important confirmation |
| `console.error('Failed to cycle permission mode:', error)` | `Logger.error('PERMISSION', 'Failed to cycle permission mode', error)` | Error |
| `console.log('Processing ${messages.length} historical messages...')` | `Logger.debug('MESSAGE', 'Processing historical messages', {count: messages.length})` | Debug detail |
| `console.log('Single-pass processing complete: Found ${toolUseCount} tool uses')` | `Logger.debug('MESSAGE', 'Historical processing complete', {toolUseCount, messageCount: messages.length})` | Debug detail |
| `console.log('Session state changed:', stateData)` | `Logger.debug('SESSION', 'Session state changed', stateData)` | Debug detail |
| `console.log('Ignoring state change for session - deletion in progress')` | `Logger.debug('SESSION', 'Ignoring state change - deletion in progress', {sessionId})` | Debug detail |
| `console.log('Input controls enabled')` | `Logger.debug('UI', 'Input controls enabled')` | Debug detail |
| `console.log('Input controls disabled due to error state')` | `Logger.debug('UI', 'Input controls disabled - error state')` | Debug detail |
| `console.log('Displaying error message in top bar:', sessionData.session.error_message)` | `Logger.info('UI', 'Displaying error in top bar', {error: sessionData.session.error_message})` | Important event |

---

## Implementation Order

### Phase 1: Python Infrastructure ✅
**Goal**: Set up logging system foundation without breaking existing code

1. **Create `data/logs/` directory structure**
   - Ensure `.gitignore` excludes `data/logs/*.log`

2. **Refactor `src/logging_config.py`**
   - Add `StandardizedFormatter` class
   - Add `get_logger(name, category)` function
   - Add `configure_logging()` function with CLI flag parameters
   - Create all file handlers (websocket_debug, sdk_debug, coordinator, storage, parser, error)
   - Set up shared error handler (sends ERROR+ from all loggers to `error.log`)

3. **Add CLI argument parsing to server entry point**
   - Parse all `--debug-*` flags
   - Call `configure_logging()` with parsed flags
   - Test: Run server with various flag combinations

4. **Test logging infrastructure**
   - Create simple test script to emit logs from each logger
   - Verify file outputs go to correct locations
   - Verify error duplication to `error.log` works
   - Verify console output respects flag settings

**Acceptance Criteria**:
- ✅ All log files created when flags enabled
- ✅ Logs respect enable/disable flags
- ✅ All errors duplicate to `error.log`
- ✅ Log format matches spec: `YYYY-MM-DDTHH:mm:ss.mmm LEVEL - [CATEGORY] Message`
- ✅ Existing app still runs without errors

---

### Phase 2: Python Logger Migration ✅
**Goal**: Migrate all Python logging to new system

**Files to Update** (in order):

1. **`src/error_handler.py`** (5 calls - simplest)
   - Import `get_logger`
   - Create `error_logger = get_logger('error_handler', category='ERROR_HANDLER')`
   - Replace logger calls with `error_logger`
   - Test error handling still works

2. **`src/data_storage.py`** (13 calls)
   - Import `get_logger`
   - Create `storage_logger = get_logger('storage', category='STORAGE')`
   - Replace logger calls with `storage_logger.debug()`
   - Keep errors as `logger.error()`
   - Test: Run with/without `--debug-storage` flag

3. **`src/message_parser.py`** (22 calls)
   - Import `get_logger`
   - Create `parser_logger = get_logger('parser', category='PARSER')`
   - Replace logger calls with `parser_logger.debug()`
   - Keep errors as `logger.error()`
   - Test: Run with/without `--debug-parser` flag

4. **`src/session_coordinator.py`** (94 calls → reduce to ~30)
   - Import `get_logger`
   - Create `coord_logger = get_logger('coordinator', category='COORDINATOR')`
   - Identify action logs (create/start/stop/delete) → `coord_logger.info()`
   - Remove verbose debug logs
   - Keep errors as `logger.error()`
   - Test: Verify `coordinator.log` shows high-value events only

5. **`src/web_server.py`** (40+ WS_LIFECYCLE calls)
   - Import `get_logger`
   - Create `ws_logger = get_logger('websocket_debug', category='WS_LIFECYCLE')`
   - Replace `[WS_LIFECYCLE]` logs with `ws_logger.debug()`/`ws_logger.info()`
   - Replace ping/pong logs with `ws_logger.debug()`
   - Keep errors as `logger.error()`
   - Test: Run with/without `--debug-websocket` flag
   - Verify ping/pong timing still works

6. **`src/claude_sdk.py`** (120+ calls - most complex)
   - Import `get_logger`
   - Create `sdk_logger = get_logger('sdk_debug', category='SDK')`
   - Create `perm_logger = get_logger('sdk_debug', category='PERMISSIONS')`
   - Replace SDK lifecycle logs with `sdk_logger`
   - Replace permission logs with `perm_logger`
   - Keep errors as `logger.error()` (duplicates to error.log)
   - Test: Run with various flag combinations (`--debug-sdk`, `--debug-permissions`)
   - Verify SDK session lifecycle still works

**Acceptance Criteria**:
- ✅ All Python files migrated to new logging system
- ✅ Log files populate correctly when flags enabled
- ✅ Console output clean by default (only coordinator + errors)
- ✅ All errors still duplicate to console + `error.log`
- ✅ Existing functionality unchanged

---

### Phase 3: JavaScript Logger Migration ✅
**Goal**: Add Logger utility and migrate all console.* calls

1. **Add `Logger` utility to `static/app.js`**
   - Add Logger object at top of file (after initial declarations)
   - Test: Manually call each method to verify formatting

2. **Systematically replace all console.* calls**
   - Work through file top to bottom
   - Use table mappings from section J above
   - Focus on one category at a time:
     - Tool Call Manager (lines 15-145)
     - Session Management (lines 2060-4407)
     - Permission Mode & Message Processing (lines 2384-2859)
     - Interrupt/Stop (lines 2169-2213, 3299-3306)
     - WebSocket Connection (lines 3016-3267)
     - API Errors & UI State (various)

3. **Test each category after migration**
   - Open browser DevTools console
   - Filter by log level (Info, Debug, etc.)
   - Verify log format matches spec
   - Verify structured data appears correctly

**Acceptance Criteria**:
- ✅ All console.log() calls replaced with Logger.*()
- ✅ Log levels used appropriately (debug/info/warn/error)
- ✅ Log format matches spec: `YYYY-MM-DDTHH:mm:ss.mmm - [CATEGORY] Message`
- ✅ Structured data formatted correctly
- ✅ Browser console filtering works (can hide debug logs)

---

### Phase 4: Testing & Validation ✅
**Goal**: Comprehensive testing of entire logging system

1. **Test CLI flag combinations**
   ```bash
   # Test each flag individually
   uv run python -m src.web_server --debug-websocket
   uv run python -m src.web_server --debug-sdk
   uv run python -m src.web_server --debug-permissions
   uv run python -m src.web_server --debug-storage
   uv run python -m src.web_server --debug-parser
   uv run python -m src.web_server --debug-error-handler

   # Test combined flags
   uv run python -m src.web_server --debug-sdk --debug-permissions

   # Test --debug-all
   uv run python -m src.web_server --debug-all

   # Test default (no flags)
   uv run python -m src.web_server
   ```

2. **Verify log file outputs**
   - Check each file in `data/logs/` directory
   - Verify format consistency
   - Verify content matches expectations
   - Verify `error.log` gets errors from all sources

3. **Verify console output**
   - Default run: Should only show coordinator actions + errors
   - With debug flags: Should show additional debug output
   - Verify no duplicate logs

4. **Test error propagation**
   - Trigger errors in various subsystems
   - Verify errors appear in console
   - Verify errors appear in `error.log`
   - Verify errors appear in category-specific log if debug enabled

5. **Test WebUI console**
   - Open browser DevTools
   - Test log level filtering (show only Info, only Errors, etc.)
   - Verify log formatting
   - Verify structured data displays correctly
   - Test key user actions appear at Info level

6. **End-to-end workflow tests**
   - Create session → verify coordinator.log
   - Send message → verify SDK logs (if enabled)
   - Trigger permission → verify permission logs (if enabled)
   - Delete session → verify coordinator.log
   - Trigger WebSocket disconnect → verify websocket_debug.log (if enabled)

**Acceptance Criteria**:
- ✅ All CLI flags work as expected
- ✅ Log files created and populated correctly
- ✅ Console output clean and useful
- ✅ Error propagation works across all subsystems
- ✅ WebUI console filtering works
- ✅ No regressions in functionality
- ✅ Performance acceptable (logging overhead minimal)

---

## Breaking Changes
**None** - This refactor is fully backwards compatible:
- Default behavior: Clean console (only actions + errors)
- Debug flags: Opt-in for verbose logging
- Log files: New feature, doesn't affect existing behavior
- WebUI: Console logs still work, just better formatted

## Benefits
1. **Cleaner production output** - Only relevant logs by default
2. **Better debugging** - Targeted log categories with CLI flags
3. **Persistent debugging** - File-based logs survive restarts
4. **Standardized format** - Easy parsing and searching
5. **Performance** - Reduced logging overhead in production
6. **Browser console clarity** - Proper log levels enable filtering
7. **Troubleshooting** - Error logs centralized in `error.log`
8. **Developer experience** - Clear categories and consistent format

## Estimated Effort
- **Phase 1**: 2-3 hours (infrastructure setup + testing)
- **Phase 2**: 4-6 hours (Python migration + testing)
- **Phase 3**: 3-4 hours (JavaScript migration + testing)
- **Phase 4**: 2-3 hours (comprehensive testing)
- **Total**: 11-16 hours

## Risks & Mitigations
| Risk | Mitigation |
|------|------------|
| Breaking existing logs | Thorough testing at each phase |
| Performance degradation | Benchmark before/after, optimize if needed |
| Missing log categories | Comprehensive grep search done upfront |
| File I/O errors | Add proper error handling in logging_config |
| Log file growth | Add log rotation in future phase |

## Future Enhancements (Out of Scope)
- Log rotation (daily/size-based)
- Log archival (compress old logs)
- Log shipping (send to external service)
- Real-time log viewer in WebUI
- Log search/filter UI
- Performance metrics in logs

---

## Status Tracking

### Completed
- ✅ Requirements gathering
- ✅ Log category analysis (15 categories identified)
- ✅ Architecture design
- ✅ Implementation plan creation
- ✅ Documentation written

### In Progress
- ⏳ Awaiting user approval to begin implementation

### Pending
- ⬜ Phase 1: Python Infrastructure
- ⬜ Phase 2: Python Logger Migration
- ⬜ Phase 3: JavaScript Logger Migration
- ⬜ Phase 4: Testing & Validation

---

## Notes
- All log categories preserved from original analysis
- User requirements fully captured
- Standardized format matches user spec
- CLI flags provide granular control
- Error propagation ensures no logs lost
- WebUI console improvements enhance developer experience

**Ready for implementation upon user approval.**
