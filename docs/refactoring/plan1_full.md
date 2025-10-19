# Plan 1: Functional Domain Separation

## Summary
This plan refactors the monolithic `ClaudeWebUI` class (5016 lines, ~120 methods) by separating it into focused, domain-specific modules organized by functional responsibility. Each module handles a distinct area of the application: WebSocket communication, session lifecycle, message rendering, UI interactions, and navigation.

## Current Problems

### Maintainability Issues
- **Single massive class**: 5016 lines with ~120 methods makes navigation and understanding difficult
- **Mixed responsibilities**: WebSocket management, DOM manipulation, state tracking, API calls, and business logic all intertwined
- **Unclear dependencies**: Hard to understand what depends on what without reading entire file
- **Testing challenges**: Cannot test individual concerns in isolation (e.g., message rendering without WebSocket logic)

### Code Organization Issues
- **Long methods**: Methods like `selectSession()` (140+ lines) handle too many concerns
- **Scattered state**: Session state, WebSocket state, UI state all mixed in one class
- **Duplicate patterns**: Similar WebSocket connection logic for UI and session sockets
- **Hard to extend**: Adding new message types or UI features requires editing the monolithic class

### Performance and Debugging Issues
- **Everything loads at once**: All code is parsed even if features aren't used
- **Hard to debug**: Breakpoints and logs are buried in massive file
- **No clear boundaries**: Changes to message rendering can accidentally affect WebSocket logic

## Proposed Architecture

### Directory Structure
```
static/
├── app/                           # Application modules (new)
│   ├── core/                      # Core application infrastructure
│   │   ├── application.js         # Main orchestrator (replaces ClaudeWebUI)
│   │   ├── state-manager.js       # Centralized state management
│   │   └── router.js              # URL routing and navigation
│   │
│   ├── websocket/                 # WebSocket management
│   │   ├── websocket-manager.js   # Base WebSocket connection logic
│   │   ├── ui-websocket.js        # Global UI WebSocket (project/session state)
│   │   └── session-websocket.js   # Session-specific message streaming
│   │
│   ├── session/                   # Session lifecycle management
│   │   ├── session-manager.js     # Session CRUD and state transitions
│   │   ├── session-selector.js    # Session selection and switching logic
│   │   └── permission-manager.js  # Permission mode handling and cycling
│   │
│   ├── messages/                  # Message processing and rendering
│   │   ├── message-renderer.js    # Main message rendering coordinator
│   │   ├── message-processor.js   # Message filtering and processing
│   │   ├── compaction-handler.js  # Compaction message pairing
│   │   └── thinking-renderer.js   # Thinking block rendering
│   │
│   ├── ui/                        # UI controllers
│   │   ├── sidebar-controller.js  # Sidebar state, resize, collapse
│   │   ├── modal-controller.js    # Modal lifecycle management
│   │   ├── input-controller.js    # Message input, send, interrupt
│   │   ├── auto-scroll.js         # Auto-scroll functionality
│   │   └── drag-drop.js           # Drag and drop for sessions/projects
│   │
│   └── project/                   # Project-specific UI
│       ├── project-renderer.js    # Project DOM element creation
│       └── session-renderer.js    # Session DOM element creation
│
├── core/                          # Shared utilities (existing)
│   ├── logger.js
│   ├── constants.js
│   ├── api-client.js
│   └── project-manager.js
│
├── tools/                         # Tool system (existing)
│   ├── tool-call-manager.js
│   ├── tool-handler-registry.js
│   └── handlers/
│
├── app.js                         # Application entry point (simplified)
└── index.html
```

### Core Modules

#### 1. **application.js** - Main Application Orchestrator
```javascript
// Minimal orchestrator that wires up modules
class ClaudeWebUI {
    constructor() {
        // State management
        this.state = new StateManager();

        // Infrastructure
        this.router = new Router(this.state);
        this.apiClient = new APIClient();

        // WebSocket managers
        this.uiWebSocket = new UIWebSocket(this.state);
        this.sessionWebSocket = new SessionWebSocket(this.state);

        // Session management
        this.sessionManager = new SessionManager(this.state, this.apiClient);
        this.sessionSelector = new SessionSelector(this.state, this.sessionManager);
        this.permissionManager = new PermissionManager(this.state, this.apiClient);

        // Message handling
        this.messageRenderer = new MessageRenderer(this.state);
        this.messageProcessor = new MessageProcessor(this.state);

        // UI controllers
        this.sidebarController = new SidebarController(this.state);
        this.modalController = new ModalController(this.state);
        this.inputController = new InputController(this.state, this.sessionWebSocket);
        this.autoScroll = new AutoScroll(this.state);

        // Project/Session rendering
        this.projectRenderer = new ProjectRenderer(this.state);
        this.sessionRenderer = new SessionRenderer(this.state);
    }

    async init() {
        await this.state.initialize();
        this.router.init();
        this.uiWebSocket.connect();
        await this.sessionManager.loadSessions();
        this.router.restoreFromURL();
    }
}
```

#### 2. **state-manager.js** - Centralized State
```javascript
// Single source of truth for application state
class StateManager extends EventTarget {
    constructor() {
        super();
        this.sessions = new Map();
        this.orderedSessions = [];
        this.currentSessionId = null;
        this.currentPermissionMode = 'default';
        this.isProcessing = false;
        this.sessionInputCache = new Map();
        this.sessionInitData = new Map();
        // ... other state
    }

    // Emit events when state changes
    setCurrentSession(sessionId) {
        const oldSessionId = this.currentSessionId;
        this.currentSessionId = sessionId;
        this.dispatchEvent(new CustomEvent('session-changed', {
            detail: { oldSessionId, newSessionId: sessionId }
        }));
    }

    updateSession(sessionId, sessionData) {
        this.sessions.set(sessionId, sessionData);
        this.dispatchEvent(new CustomEvent('session-updated', {
            detail: { sessionId, sessionData }
        }));
    }
}
```

#### 3. **ui-websocket.js** & **session-websocket.js**
```javascript
// Base class extracted from common WebSocket patterns
class WebSocketManager {
    constructor(url, maxRetries = 5) {
        this.url = url;
        this.socket = null;
        this.retryCount = 0;
        this.maxRetries = maxRetries;
        this.intentionalDisconnect = false;
    }

    connect() { /* shared connection logic */ }
    disconnect() { /* shared disconnect logic */ }
    scheduleReconnect() { /* shared reconnect logic */ }
    send(message) { /* shared send logic */ }
}

// Specialized for UI state updates
class UIWebSocket extends WebSocketManager {
    constructor(state) {
        super('/ws/ui', 10);
        this.state = state;
    }

    handleMessage(data) {
        switch (data.type) {
            case 'sessions_list':
                this.state.updateSessionsList(data.data.sessions);
                break;
            case 'state_change':
                this.state.handleStateChange(data.data);
                break;
            // ...
        }
    }
}

// Specialized for session messages
class SessionWebSocket extends WebSocketManager {
    constructor(state) {
        super(null, 5); // URL set dynamically
        this.state = state;
    }

    connectToSession(sessionId) {
        this.url = `/ws/session/${sessionId}`;
        this.connect();
    }

    handleMessage(data) {
        // Delegate to message processor
        this.dispatchEvent(new CustomEvent('message', { detail: data }));
    }
}
```

#### 4. **session-selector.js** - Session Selection Logic
```javascript
// Handles complex session selection flow
class SessionSelector {
    constructor(state, sessionManager) {
        this.state = state;
        this.sessionManager = sessionManager;
    }

    async selectSession(sessionId) {
        // Show loading
        this.showLoadingScreen();

        // Disconnect from old session
        await this.disconnectCurrentSession();

        // Update state
        this.state.setCurrentSession(sessionId);

        // Update URL
        this.updateURL(sessionId);

        // Check session state and start if needed
        const session = await this.sessionManager.getSession(sessionId);
        if (session.state === 'created') {
            await this.waitForSessionActive(sessionId);
        }

        // Connect WebSocket
        this.dispatchEvent(new CustomEvent('connect-requested', {
            detail: { sessionId }
        }));

        // Load messages
        this.dispatchEvent(new CustomEvent('load-messages-requested', {
            detail: { sessionId }
        }));

        // Hide loading
        this.hideLoadingScreen();
    }
}
```

#### 5. **message-renderer.js** - Message Display
```javascript
class MessageRenderer {
    constructor(state) {
        this.state = state;
        this.toolCallManager = new ToolCallManager();
        this.toolHandlerRegistry = new ToolHandlerRegistry();
        this.compactionHandler = new CompactionHandler();
        this.thinkingRenderer = new ThinkingRenderer();
    }

    renderMessage(message, scroll = true) {
        // Delegate to specialized renderers
        if (this.isCompaction(message)) {
            return this.compactionHandler.render(message, scroll);
        }

        if (this.hasThinkingBlocks(message)) {
            return this.thinkingRenderer.render(message, scroll);
        }

        if (this.hasToolUses(message)) {
            return this.renderToolMessage(message, scroll);
        }

        return this.renderStandardMessage(message, scroll);
    }

    renderStandardMessage(message, scroll) {
        const element = this.createMessageElement(message);
        this.appendToDOM(element);
        if (scroll) this.scrollToBottom();
        return element;
    }
}
```

#### 6. **input-controller.js** - User Input Handling
```javascript
class InputController {
    constructor(state, sessionWebSocket) {
        this.state = state;
        this.sessionWebSocket = sessionWebSocket;
        this.setupEventListeners();
    }

    setupEventListeners() {
        const messageInput = document.getElementById('message-input');
        const sendBtn = document.getElementById('send-btn');

        sendBtn.addEventListener('click', () => this.handleSend());
        messageInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                this.handleSend();
            }
        });
    }

    async handleSend() {
        const messageInput = document.getElementById('message-input');
        const message = messageInput.value.trim();

        if (!message || !this.state.currentSessionId) return;

        // Send via WebSocket
        this.sessionWebSocket.send({
            type: 'send_message',
            content: message
        });

        // Update UI
        messageInput.value = '';
        this.state.setProcessing(true);
    }

    async handleInterrupt() {
        this.sessionWebSocket.send({ type: 'interrupt_session' });
        this.state.setProcessing(false);
    }
}
```

#### 7. **modal-controller.js** - Modal Management
```javascript
class ModalController {
    constructor(state) {
        this.state = state;
        this.modals = new Map(); // Store modal instances
        this.originalContent = new Map(); // Cache original content
        this.setupEventListeners();
    }

    show(modalId, options = {}) {
        const modal = this.getModal(modalId);
        if (options.onShow) options.onShow(modal);
        modal.show();
    }

    hide(modalId) {
        const modal = this.getModal(modalId);
        modal.hide();
        this.restoreOriginalContent(modalId);
    }

    setupEventListeners() {
        // Manage session modal
        document.getElementById('manage-session-btn')
            ?.addEventListener('click', () => this.showManageSession());

        // Create session modal
        document.getElementById('create-session-btn')
            ?.addEventListener('click', () => this.showCreateSession());

        // ... other modal triggers
    }
}
```

## Module Communication Pattern

### Event-Driven Architecture
Modules communicate through events emitted by `StateManager`:

```javascript
// State change triggers events
state.setCurrentSession('session-123');
// -> Emits 'session-changed' event

// Modules listen to relevant events
sessionRenderer.listenTo(state, 'session-changed', (e) => {
    sessionRenderer.highlightActiveSession(e.detail.newSessionId);
});

messageRenderer.listenTo(state, 'session-changed', (e) => {
    messageRenderer.clearMessages();
});

router.listenTo(state, 'session-changed', (e) => {
    router.updateURL(e.detail.newSessionId);
});
```

### Dependency Injection
Constructor injection makes dependencies explicit:

```javascript
// Clear dependency graph
class SessionSelector {
    constructor(state, sessionManager) {
        this.state = state;           // Needs state access
        this.sessionManager = sessionManager; // Needs session API
    }
}

// Easy to test with mocks
const mockState = new MockStateManager();
const mockSessionManager = new MockSessionManager();
const selector = new SessionSelector(mockState, mockSessionManager);
```

### Custom Events for Cross-Module Communication
```javascript
// Input controller doesn't know about message renderer
inputController.dispatchEvent(new CustomEvent('message-sent', {
    detail: { message: 'Hello', sessionId: 'abc' }
}));

// Message renderer listens for message events
messageRenderer.addEventListener('message-sent', (e) => {
    messageRenderer.addUserMessage(e.detail.message);
});
```

## Migration Strategy

### Phase 1: Extract Infrastructure (Week 1)
1. Create `app/core/state-manager.js` with shared state
2. Update `app.js` to instantiate `StateManager`
3. Gradually move state access to `StateManager` (can coexist with old properties)
4. Test that existing functionality still works

### Phase 2: Extract WebSocket Managers (Week 1-2)
1. Create `app/websocket/websocket-manager.js` base class
2. Extract UI WebSocket to `app/websocket/ui-websocket.js`
3. Extract Session WebSocket to `app/websocket/session-websocket.js`
4. Update `ClaudeWebUI` to use new classes
5. Remove old WebSocket code once verified

### Phase 3: Extract Session Management (Week 2)
1. Create `app/session/session-manager.js` for CRUD operations
2. Create `app/session/session-selector.js` for selection logic
3. Create `app/session/permission-manager.js` for permission mode
4. Migrate methods one at a time, testing after each

### Phase 4: Extract Message Rendering (Week 2-3)
1. Create `app/messages/message-renderer.js`
2. Extract tool rendering logic
3. Extract thinking block rendering
4. Extract compaction handling
5. Update message flow to use new renderer

### Phase 5: Extract UI Controllers (Week 3)
1. Create `app/ui/input-controller.js`
2. Create `app/ui/modal-controller.js`
3. Create `app/ui/sidebar-controller.js`
4. Create `app/ui/auto-scroll.js`
5. Create `app/ui/drag-drop.js`

### Phase 6: Extract Project/Session Rendering (Week 3-4)
1. Create `app/project/project-renderer.js`
2. Create `app/project/session-renderer.js`
3. Migrate DOM creation methods

### Phase 7: Cleanup (Week 4)
1. Remove emptied methods from `ClaudeWebUI`
2. Simplify `app.js` to just wire modules together
3. Update documentation
4. Add JSDoc comments to all public methods

### Gradual Migration Pattern
```javascript
// Before (in ClaudeWebUI)
async selectSession(sessionId) {
    // 140 lines of code...
}

// During migration (delegates to new class)
async selectSession(sessionId) {
    return this.sessionSelector.selectSession(sessionId);
}

// After migration (remove from ClaudeWebUI entirely)
// Call directly: app.sessionSelector.selectSession(sessionId)
```

## Testing Approach

### Unit Testing Individual Modules
```javascript
// test/websocket-manager.test.js
describe('WebSocketManager', () => {
    let manager;
    let mockSocket;

    beforeEach(() => {
        manager = new WebSocketManager('/test', { maxRetries: 3 });
        mockSocket = new MockWebSocket();
        global.WebSocket = jest.fn(() => mockSocket);
    });

    it('should connect and emit connected event', async () => {
        const connectedSpy = jest.fn();
        manager.addEventListener('connected', connectedSpy);

        manager.connect();
        mockSocket.triggerOpen();

        expect(connectedSpy).toHaveBeenCalled();
    });

    it('should reconnect with exponential backoff', async () => {
        manager.connect();

        // First disconnect
        mockSocket.triggerClose();
        expect(manager.retryCount).toBe(1);

        // Second disconnect
        await jest.advanceTimersByTime(2000);
        mockSocket.triggerClose();
        expect(manager.retryCount).toBe(2);
    });
});
```

### Integration Testing Module Communication
```javascript
// test/session-selection.integration.test.js
describe('Session Selection Flow', () => {
    let app;

    beforeEach(() => {
        app = new ClaudeWebUI();
        app.init();
    });

    it('should coordinate session selection across modules', async () => {
        const sessionId = 'test-session-123';

        // Mock API responses
        mockAPI.getSession.mockResolvedValue({
            session_id: sessionId,
            state: 'active'
        });

        // Select session
        await app.sessionSelector.selectSession(sessionId);

        // Verify state updated
        expect(app.state.currentSessionId).toBe(sessionId);

        // Verify WebSocket connected
        expect(app.sessionWebSocket.url).toBe(`/ws/session/${sessionId}`);

        // Verify messages loaded
        expect(app.messageRenderer.messages.length).toBeGreaterThan(0);

        // Verify UI updated
        const activeElement = document.querySelector('.session-item.active');
        expect(activeElement.dataset.sessionId).toBe(sessionId);
    });
});
```

### Mocking for Isolated Tests
```javascript
// test/mocks/mock-state-manager.js
class MockStateManager extends EventTarget {
    constructor() {
        super();
        this.currentSessionId = null;
        this.sessions = new Map();
    }

    setCurrentSession(sessionId) {
        this.currentSessionId = sessionId;
        this.dispatchEvent(new CustomEvent('session-changed', {
            detail: { oldSessionId: null, newSessionId: sessionId }
        }));
    }
}

// Usage in tests
const mockState = new MockStateManager();
const selector = new SessionSelector(mockState, mockSessionManager);
```

## Benefits and Trade-offs

### Benefits

**Maintainability**
- **Focused modules**: Each module has single, clear responsibility
- **Easier navigation**: Finding functionality is straightforward (session logic in session/, WebSocket in websocket/)
- **Smaller files**: 200-400 line files are easier to understand than 5000 lines
- **Clear interfaces**: Public methods define what each module does

**Testability**
- **Unit tests**: Can test WebSocketManager without DOM or API calls
- **Mocking**: Easy to mock dependencies with constructor injection
- **Integration tests**: Can test module interactions in isolation
- **Coverage**: Easier to achieve high coverage with focused modules

**Performance**
- **Code splitting**: Can lazy-load modules not needed initially
- **Smaller parse time**: Browser parses smaller files faster
- **Better caching**: Changes to one module don't invalidate all code

**Developer Experience**
- **Parallel work**: Multiple developers can work on different modules
- **Clear ownership**: Each module has clear purpose and boundaries
- **Easier onboarding**: New developers can understand one module at a time
- **Better tooling**: IDEs provide better autocomplete for smaller files

**Extensibility**
- **Add features**: New message types just need new formatters
- **Plugin architecture**: Can add new WebSocket types by extending base class
- **Swap implementations**: Can replace MessageRenderer without touching other code

### Trade-offs

**Initial Complexity**
- **More files**: 15+ files instead of 1 (mitigated by clear directory structure)
- **Event wiring**: Need to wire up events in application.js (but this makes dependencies explicit)
- **Learning curve**: Developers need to understand module boundaries (but improves over time)

**Migration Effort**
- **Time investment**: 4 weeks of gradual migration
- **Testing overhead**: Need to verify each extraction doesn't break functionality
- **Potential bugs**: Risk of introducing bugs during refactoring (mitigated by gradual approach)

**Runtime Overhead**
- **Event dispatching**: Slight overhead from CustomEvents (negligible in practice)
- **More objects**: More class instances in memory (minimal impact)
- **Multiple files**: More HTTP requests (solved with bundling if needed)

### Mitigation Strategies

**For Complexity:**
- Clear naming conventions (`*-manager.js`, `*-controller.js`, `*-renderer.js`)
- Comprehensive documentation in CLAUDE.md
- JSDoc comments on all public methods

**For Migration Risk:**
- Gradual migration (one module per week)
- Keep old code alongside new until verified
- Comprehensive testing after each extraction
- Use feature flags if needed for risky changes

**For Runtime Overhead:**
- Use event delegation where appropriate
- Implement lazy loading for rarely-used modules
- Bundle for production if multiple files become issue

## Conclusion

Plan 1: Functional Domain Separation provides a clean, maintainable architecture by organizing code based on what it does (WebSocket management, session handling, message rendering, etc.). The gradual migration strategy allows for safe refactoring over 4 weeks while maintaining a working application throughout. The event-driven communication pattern and dependency injection make module interactions explicit and testable.

This refactoring will significantly improve code maintainability, enable better testing, support parallel development, and make the codebase more approachable for new contributors. While there is upfront migration effort, the long-term benefits of focused, testable modules far outweigh the costs.
