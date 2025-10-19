# Plan 2: Layer-Based Architecture

## Summary
Reorganize the monolithic 5016-line `app.js` into a clean three-layer architecture (Presentation, Business Logic, and Data/Communication), separating concerns by technical responsibility rather than domain. This approach creates clear horizontal boundaries where each layer communicates through well-defined interfaces, making the system easier to test, maintain, and scale.

## Current Problems

### Architectural Issues
1. **Massive Single Class**: ClaudeWebUI has ~120 methods handling everything from WebSocket management to DOM manipulation
2. **Mixed Concerns**: Business logic (session state), presentation logic (DOM rendering), and data access (API calls) are intertwined
3. **No Clear Boundaries**: Methods freely access DOM, call APIs, and manage state without separation
4. **Testing Difficulty**: Cannot test business logic without DOM; cannot test UI rendering without WebSockets
5. **Code Navigation**: Finding functionality requires scanning through 5000+ lines of mixed responsibilities

### Specific Pain Points
- Session selection logic (`selectSession()` lines 2381-2519) mixes state management, API calls, DOM updates, and WebSocket connection
- Message processing (`processMessage()`, `handleIncomingMessage()`) combines parsing, state updates, and rendering
- Permission handling spreads across WebSocket handlers, UI updates, and API calls
- Drag-and-drop code is embedded directly in main class despite being pure presentation logic
- Auto-scroll functionality mixed with message rendering

## Proposed Architecture

### Layer Structure

```
static/
├── layers/
│   ├── presentation/                    # Layer 1: UI Components & Rendering
│   │   ├── components/
│   │   │   ├── session-list.js         # Session sidebar rendering
│   │   │   ├── message-display.js      # Message/tool call rendering
│   │   │   ├── chat-controls.js        # Input controls, send button
│   │   │   ├── modals.js               # Modal dialogs management
│   │   │   ├── sidebar.js              # Sidebar resize/collapse
│   │   │   └── drag-drop.js            # Drag-drop interactions
│   │   │
│   │   └── renderers/
│   │       ├── session-renderer.js     # Renders session UI elements
│   │       ├── message-renderer.js     # Renders messages/compaction
│   │       ├── permission-renderer.js  # Renders permission dialogs
│   │       └── status-renderer.js      # Status indicators/dots
│   │
│   ├── business/                        # Layer 2: Business Logic & State
│   │   ├── state/
│   │   │   ├── session-state.js        # Session lifecycle state machine
│   │   │   ├── message-state.js        # Message queue/history state
│   │   │   ├── permission-state.js     # Permission mode state
│   │   │   └── ui-state.js             # UI preferences (auto-scroll, etc.)
│   │   │
│   │   ├── services/
│   │   │   ├── session-service.js      # Session orchestration logic
│   │   │   ├── message-service.js      # Message processing workflows
│   │   │   ├── permission-service.js   # Permission decision workflows
│   │   │   └── navigation-service.js   # URL/routing logic
│   │   │
│   │   └── workflows/
│   │       ├── session-workflows.js    # Complex session operations
│   │       ├── message-workflows.js    # Message send/receive flows
│   │       └── permission-workflows.js # Permission request/response
│   │
│   └── data/                            # Layer 3: Data & Communication
│       ├── websocket/
│       │   ├── ui-websocket.js         # Global UI WebSocket
│       │   ├── session-websocket.js    # Session-specific WebSocket
│       │   └── websocket-manager.js    # Connection lifecycle management
│       │
│       ├── api/
│       │   ├── session-api.js          # Session CRUD operations
│       │   ├── message-api.js          # Message fetch operations
│       │   ├── project-api.js          # Project operations (extends existing)
│       │   └── permission-api.js       # Permission mode updates
│       │
│       └── transforms/
│           ├── message-transform.js    # Message format conversions
│           ├── session-transform.js    # Session data normalization
│           └── websocket-transform.js  # WebSocket event parsing
│
├── core/                                # Existing infrastructure (unchanged)
│   ├── logger.js
│   ├── constants.js
│   ├── api-client.js
│   └── project-manager.js
│
├── tools/                               # Existing tool system (unchanged)
│   ├── tool-call-manager.js
│   ├── tool-handler-registry.js
│   └── handlers/
│
├── orchestrator.js                      # NEW: Layer coordinator
└── app.js                               # NEW: Slim initialization only
```

### Layer Responsibilities

#### Layer 1: Presentation (UI Components & Rendering)
**Responsibility**: DOM manipulation, event handling, visual updates

**What it does**:
- Renders UI components (session lists, messages, modals)
- Handles user interactions (clicks, drags, keyboard)
- Updates visual state (expand/collapse, animations, styles)
- Manages layout (sidebar resize, scroll position)

**What it doesn't do**:
- Make API calls or WebSocket connections
- Store application state
- Implement business rules

**Example Classes**:
```javascript
class MessageDisplay {
    renderMessage(messageData, toolHandler) { /* DOM only */ }
    scrollToBottom() { /* DOM only */ }
    highlightMessage(messageId) { /* DOM only */ }
}
```

#### Layer 2: Business Logic (Services & State)
**Responsibility**: Application workflows, state management, business rules

**What it does**:
- Manages session lifecycle (created → starting → active → terminated)
- Coordinates multi-step workflows (select session → load info → connect WS)
- Enforces business rules (permission mode transitions, validation)
- Maintains application state (current session, processing status)

**What it doesn't do**:
- Touch DOM directly
- Make raw fetch/WebSocket calls
- Format data for display

**Example Classes**:
```javascript
class SessionService {
    constructor(sessionApi, sessionState) { /* dependencies */ }

    async selectSession(sessionId) {
        // 1. Validate can select
        // 2. Save previous session state
        // 3. Update current session via API layer
        // 4. Load session info via API layer
        // 5. Update internal state
        // 6. Return session data for presentation
    }
}
```

#### Layer 3: Data/Communication (API, WebSocket, Transforms)
**Responsibility**: External communication, data fetching, format conversion

**What it does**:
- Makes HTTP requests to backend
- Manages WebSocket connections
- Transforms data between formats (API ↔ internal ↔ display)
- Handles network errors/retries

**What it doesn't do**:
- Implement business logic
- Update DOM
- Make state decisions

**Example Classes**:
```javascript
class SessionWebSocket {
    constructor(baseUrl, onMessage, onStateChange) { /* callbacks */ }

    connect(sessionId) { /* WebSocket setup */ }
    disconnect() { /* cleanup */ }
    sendMessage(content) { /* send via WS */ }

    // Fires callbacks for business layer to handle
}
```

## Layer Communication Contracts

### Communication Flow (Top to Bottom)

```
┌─────────────────────────────────────────────────────────────┐
│                  Presentation Layer                          │
│  (Components emit events, request data via services)         │
└──────────────────┬──────────────────────────────────────────┘
                   │ Events (user actions)
                   │ Data requests (display data)
                   ▼
┌─────────────────────────────────────────────────────────────┐
│                  Business Logic Layer                        │
│  (Services process requests, update state, emit events)      │
└──────────────────┬──────────────────────────────────────────┘
                   │ API calls (fetch/update)
                   │ WebSocket events (connect/send)
                   ▼
┌─────────────────────────────────────────────────────────────┐
│               Data/Communication Layer                       │
│  (Execute network calls, transform data, fire callbacks)     │
└─────────────────────────────────────────────────────────────┘
```

### Interface Contracts

#### Presentation → Business
**Pattern**: Event-driven with callbacks

```javascript
// Presentation calls business services
sessionService.selectSession(sessionId)
    .then(sessionData => {
        // Presentation updates UI with returned data
        sessionRenderer.renderActiveSession(sessionData);
    });

// Presentation subscribes to business events
sessionService.on('session:changed', (sessionData) => {
    sessionRenderer.updateSessionDisplay(sessionData);
});
```

#### Business → Data
**Pattern**: Promise-based with dependency injection

```javascript
class SessionService {
    constructor(sessionApi, sessionWebSocket, sessionState) {
        this.api = sessionApi;
        this.ws = sessionWebSocket;
        this.state = sessionState;

        // Subscribe to data layer events
        this.ws.on('message', (msg) => this.handleMessage(msg));
        this.ws.on('state_change', (state) => this.handleStateChange(state));
    }

    async loadSessionInfo(sessionId) {
        // Call data layer, process result
        const data = await this.api.getSessionInfo(sessionId);
        this.state.updateSession(sessionId, data);
        return data;
    }
}
```

#### Data → Business
**Pattern**: Callback-based for async events

```javascript
class SessionWebSocket {
    constructor(callbacks = {}) {
        this.onMessage = callbacks.onMessage || (() => {});
        this.onStateChange = callbacks.onStateChange || (() => {});
        this.onError = callbacks.onError || (() => {});
    }

    handleWebSocketMessage(event) {
        const data = JSON.parse(event.data);

        // Fire appropriate callback
        if (data.type === 'message') {
            this.onMessage(data.data);
        } else if (data.type === 'state_change') {
            this.onStateChange(data.data);
        }
    }
}
```

### Orchestrator Pattern

The `orchestrator.js` coordinates layers and maintains references:

```javascript
class ClaudeWebUIOrchestrator {
    constructor() {
        // Layer 3: Data
        this.sessionApi = new SessionAPI(apiClient);
        this.messageApi = new MessageAPI(apiClient);
        this.sessionWS = new SessionWebSocket({
            onMessage: (msg) => this.messageService.handleIncoming(msg),
            onStateChange: (state) => this.sessionService.handleStateChange(state)
        });

        // Layer 2: Business
        this.sessionState = new SessionState();
        this.sessionService = new SessionService(this.sessionApi, this.sessionWS, this.sessionState);
        this.messageService = new MessageService(this.messageApi, this.messageState);

        // Layer 1: Presentation
        this.sessionRenderer = new SessionRenderer();
        this.messageDisplay = new MessageDisplay();
        this.chatControls = new ChatControls({
            onSendMessage: (msg) => this.messageService.sendMessage(msg),
            onInterrupt: () => this.sessionService.interrupt()
        });

        this.init();
    }
}
```

## Migration Strategy

### Phase 1: Extract Data Layer (Week 1)
**Goal**: Isolate all API and WebSocket communication

1. Create `data/websocket/session-websocket.js`
   - Move WebSocket connection logic from `connectSessionWebSocket()`, `disconnectSessionWebSocket()`
   - Extract message handlers from `handleWebSocketMessage()`
   - Keep callbacks to business layer

2. Create `data/api/session-api.js`
   - Extract all `/api/sessions/*` fetch calls
   - Wrap in clean methods: `getSessionInfo()`, `startSession()`, `deleteSession()`

3. Create `data/transforms/message-transform.js`
   - Extract message formatting logic from `processMessage()`
   - Separate WebSocket format → internal format → display format

**Testing**: Can test data layer in isolation with mock servers

### Phase 2: Extract Business Layer (Week 2)
**Goal**: Centralize state and workflows

1. Create `business/state/session-state.js`
   - Move session Map, orderedSessions
   - Extract `currentSessionId`, `isProcessing` state
   - Provide getters/setters with validation

2. Create `business/services/session-service.js`
   - Move `selectSession()`, `createSession()`, `restartSession()` logic
   - Remove all DOM references
   - Return data objects for presentation to render

3. Create `business/workflows/session-workflows.js`
   - Extract complex flows like "select session → check state → start if needed → connect WS"
   - Keep all branching logic here

**Testing**: Can test business logic with mock data layer

### Phase 3: Extract Presentation Layer (Week 3)
**Goal**: Pure rendering and event handling

1. Create `presentation/components/session-list.js`
   - Move `renderSessions()`, `createSessionElement()`, `createProjectElement()`
   - Keep only DOM creation/manipulation

2. Create `presentation/components/message-display.js`
   - Move `addMessageToUI()`, `renderMessages()`, `addCompactionToUI()`
   - Extract scroll logic to separate class

3. Create `presentation/components/drag-drop.js`
   - Move entire drag-drop subsystem (`addDragDropListeners()`, etc.)
   - Self-contained presentation logic

**Testing**: Can test rendering with mock data objects

### Phase 4: Wire Together with Orchestrator (Week 4)
**Goal**: Replace monolithic app.js with orchestrator

1. Create `orchestrator.js`
   - Instantiate all layers
   - Wire dependencies
   - Setup event routing

2. Update `app.js` to minimal bootstrap
   - Just create orchestrator
   - Remove all logic

3. Migration verification
   - Run full integration tests
   - Check all features still work

### Gradual Migration (Recommended)

Instead of big-bang, migrate one feature area at a time:

**Step 1**: Migrate session selection
- Extract session WebSocket → `data/websocket/session-websocket.js`
- Extract session service → `business/services/session-service.js`
- Extract session renderer → `presentation/renderers/session-renderer.js`
- Keep old code as fallback until verified

**Step 2**: Migrate message display
- Repeat pattern for messages
- Can coexist with old session code

**Step 3**: Continue feature-by-feature until complete

## Example Refactoring

### Example 1: Session Selection Flow

**Before (app.js lines 2381-2520, simplified):**
```javascript
class ClaudeWebUI {
    async selectSession(sessionId) {
        // Show loading screen immediately
        document.getElementById('loading-screen').classList.remove('d-none');

        // Clean disconnect from previous session
        if (this.currentSessionId && this.currentSessionId !== sessionId) {
            this.disconnectSessionWebSocket();
            await new Promise(resolve => setTimeout(resolve, 100));
        }

        // Update URL
        this.updateURLWithSession(sessionId);

        // Update UI
        document.querySelectorAll('.session-item').forEach(el => {
            el.classList.remove('active');
        });
        const sessionElement = document.querySelector(`[data-session-id="${sessionId}"]`);
        if (sessionElement) {
            sessionElement.classList.add('active');
        }

        // Show chat container
        document.getElementById('chat-container').classList.remove('d-none');
        document.getElementById('no-session-message').classList.add('d-none');

        // Load session info first
        const session = await this.loadSessionInfo();

        // Check if session needs to be started
        if (session.state === 'error') {
            // Handle error state...
            return;
        }

        if (session.state === 'created') {
            // Wait for session to become active...
            let attempts = 0;
            while (attempts < maxAttempts) {
                // Polling logic...
            }
        }

        // Connect WebSocket
        this.connectSessionWebSocket();

        // Load messages
        await this.loadMessages();

        // Restore cached input
        const messageInput = document.getElementById('message-input');
        if (this.sessionInputCache.has(sessionId)) {
            messageInput.value = this.sessionInputCache.get(sessionId);
        }

        // Update session info button
        this.updateSessionInfoButton();

        // Hide loading screen
        document.getElementById('loading-screen').classList.add('d-none');
    }
}
```

**After (business/services/session-service.js):**
```javascript
class SessionService {
    constructor(sessionApi, sessionWebSocket, sessionState, navigationService) {
        this.api = sessionApi;
        this.ws = sessionWebSocket;
        this.state = sessionState;
        this.nav = navigationService;

        // Event emitter for presentation layer
        this.eventTarget = new EventTarget();
    }

    async selectSession(sessionId, cachedInputValue = null) {
        // Pure business logic - no DOM, no raw WebSocket

        // 1. Validate can switch
        if (this.state.currentSessionId === sessionId && this.ws.isConnected()) {
            Logger.debug('SESSION', 'Already connected', sessionId);
            return this.state.getCurrentSession();
        }

        // 2. Save previous session's state
        if (this.state.currentSessionId && this.state.currentSessionId !== sessionId) {
            if (cachedInputValue) {
                this.state.saveInputCache(this.state.currentSessionId, cachedInputValue);
            }
            this.ws.disconnect();
            await this._wait(100); // Clean disconnect
        }

        // 3. Update current session
        this.state.setCurrentSession(sessionId);
        this.nav.updateURL(sessionId);

        // 4. Load session info
        const sessionInfo = await this.api.getSessionInfo(sessionId);
        this.state.updateSession(sessionId, sessionInfo.session);

        // 5. Handle session state and connection
        const connectionResult = await this._handleSessionConnection(sessionId, sessionInfo.session);

        // 6. Return data for presentation layer
        return {
            sessionId,
            sessionInfo: sessionInfo.session,
            connectionStatus: connectionResult,
            cachedInput: this.state.getInputCache(sessionId)
        };
    }

    async _handleSessionConnection(sessionId, session) {
        if (session.state === 'error') {
            return { connected: false, reason: 'error_state' };
        }

        if (session.state === 'active' || session.state === 'running') {
            this.ws.connect(sessionId);
            return { connected: true, reason: 'already_active' };
        }

        if (session.state === 'starting') {
            const active = await this._waitForActive(sessionId, 15);
            if (active) {
                this.ws.connect(sessionId);
                return { connected: true, reason: 'became_active' };
            }
            return { connected: false, reason: 'timeout' };
        }

        // Need to start session
        await this.api.startSession(sessionId);
        const active = await this._waitForActive(sessionId, 15);
        if (active) {
            this.ws.connect(sessionId);
            return { connected: true, reason: 'started' };
        }
        return { connected: false, reason: 'start_timeout' };
    }

    async _waitForActive(sessionId, maxAttempts) {
        for (let i = 0; i < maxAttempts; i++) {
            await this._wait(1000);
            const sessionInfo = await this.api.getSessionInfo(sessionId);
            this.state.updateSession(sessionId, sessionInfo.session);

            if (sessionInfo.session.state === 'error') {
                return false;
            }
            if (sessionInfo.session.state === 'active' || sessionInfo.session.state === 'running') {
                return true;
            }
        }
        return false;
    }

    _wait(ms) {
        return new Promise(resolve => setTimeout(resolve, ms));
    }
}
```

**After (presentation/components/session-list.js):**
```javascript
class SessionList {
    constructor(sessionRenderer, sessionService) {
        this.renderer = sessionRenderer;
        this.service = sessionService;
        this.container = document.getElementById('sessions-list-container');

        // Subscribe to session service events
        this.service.on('session:selected', (data) => this.handleSessionSelected(data));
    }

    handleSessionSelected(data) {
        // Pure presentation logic
        this.updateActiveState(data.sessionId);
        this.showChatContainer();
        this.restoreCachedInput(data.cachedInput);
    }

    updateActiveState(sessionId) {
        // Remove active from all
        document.querySelectorAll('.session-item').forEach(item => {
            item.classList.remove('active');
        });

        // Add active to selected
        const sessionElement = document.querySelector(`[data-session-id="${sessionId}"]`);
        if (sessionElement) {
            sessionElement.classList.add('active');
        }
    }

    showChatContainer() {
        document.getElementById('no-session-selected').classList.add('d-none');
        document.getElementById('chat-container').classList.remove('d-none');
    }

    restoreCachedInput(cachedInput) {
        const messageInput = document.getElementById('message-input');
        if (messageInput && cachedInput) {
            messageInput.value = cachedInput;
        }
    }

    async onSessionClick(sessionId) {
        // Get current input value for caching
        const messageInput = document.getElementById('message-input');
        const currentInput = messageInput ? messageInput.value : null;

        // Show loading (presentation concern)
        this.showLoading(true);

        try {
            // Request selection via service (business layer)
            await this.service.selectSession(sessionId, currentInput);
            // Service will emit events that trigger handleSessionSelected
        } catch (error) {
            this.showError(`Failed to select session: ${error.message}`);
        } finally {
            this.showLoading(false);
        }
    }

    showLoading(show) {
        const overlay = document.getElementById('loading-overlay');
        overlay.classList.toggle('d-none', !show);
    }

    showError(message) {
        alert(`Error: ${message}`);
    }
}
```

### Example 2: Message Rendering

**Before: Mixed concerns in `addMessageToUI()` (lines 3406-3478)**

```javascript
class ClaudeWebUI {
    addMessageToUI(message, scroll = true) {
        // Business logic + Presentation mixed
        const messagesArea = document.getElementById('messages-area');
        const messageDiv = document.createElement('div');
        messageDiv.className = `message message-${message.role}`;
        messageDiv.dataset.messageId = message.message_id || Date.now();

        // Complex conditional rendering logic
        const content = message.content || '';
        const formattedContent = this._formatMessageContent(message, content);

        // More DOM manipulation...
        messageDiv.innerHTML = `
            <div class="message-header">${this._getMessageHeader(message)}</div>
            <div class="message-content">${formattedContent}</div>
        `;

        // Metadata rendering
        if (this._shouldShowMetadata(message)) {
            const metadata = this._getDisplayMetadata(message);
            // Create metadata UI...
        }

        messagesArea.appendChild(messageDiv);

        // Auto-scroll logic mixed in
        if (scroll) {
            this.smartScrollToBottom();
        }
    }
}
```

**After: Separated layers**

```javascript
// Layer 2: Business/services/message-service.js
class MessageService {
    constructor(messageApi, messageState, toolCallManager) {
        this.api = messageApi;
        this.state = messageState;
        this.toolManager = toolCallManager;
        this.eventTarget = new EventTarget();
    }

    handleIncomingMessage(rawMessage) {
        // Business logic: process, validate, update state
        const processedMessage = this._processMessage(rawMessage);
        this.state.addMessage(processedMessage);

        // Emit for presentation layer
        this.emit('message:received', {
            message: processedMessage,
            shouldScroll: true
        });

        // Handle tool-related logic
        if (this._hasToolUses(processedMessage)) {
            this._handleToolUses(processedMessage);
        }
    }

    _processMessage(rawMessage) {
        // Pure business logic
        return {
            ...rawMessage,
            timestamp: rawMessage.timestamp || new Date().toISOString(),
            shouldShowMetadata: this._shouldShowMetadata(rawMessage),
            displayMetadata: this._getDisplayMetadata(rawMessage)
        };
    }
}

// Layer 1: Presentation/components/message-display.js
class MessageDisplay {
    constructor(messageRenderer, autoScrollManager) {
        this.renderer = messageRenderer;
        this.scrollManager = autoScrollManager;
        this.container = document.getElementById('messages-area');
    }

    addMessage(messageData, shouldScroll = true) {
        // Pure presentation: create DOM, append, scroll
        const messageElement = this.renderer.createMessageElement(messageData);
        this.container.appendChild(messageElement);

        if (shouldScroll) {
            this.scrollManager.smartScroll();
        }
    }
}

// Layer 1: Presentation/renderers/message-renderer.js
class MessageRenderer {
    createMessageElement(messageData) {
        // Pure rendering logic
        const div = document.createElement('div');
        div.className = `message message-${messageData.role}`;
        div.dataset.messageId = messageData.message_id || Date.now();

        div.innerHTML = `
            <div class="message-header">${this._renderHeader(messageData)}</div>
            <div class="message-content">${this._renderContent(messageData)}</div>
            ${messageData.shouldShowMetadata ? this._renderMetadata(messageData) : ''}
        `;

        return div;
    }
}
```

## Testing Approach

### Layer 3: Data Layer Testing

```javascript
// Test WebSocket without business logic
describe('SessionWebSocket', () => {
    it('should connect and emit message events', async () => {
        const messages = [];
        const ws = new SessionWebSocket({
            onMessage: (msg) => messages.push(msg)
        });

        ws.connect('test-session-id');

        // Mock WebSocket message
        ws.ws.dispatchEvent(new MessageEvent('message', {
            data: JSON.stringify({ type: 'message', data: { content: 'test' } })
        }));

        expect(messages).toHaveLength(1);
        expect(messages[0].content).toBe('test');
    });
});
```

### Layer 2: Business Logic Testing

```javascript
// Test session selection logic without DOM or real API
describe('SessionService', () => {
    it('should handle session state transitions', async () => {
        const mockApi = {
            getSessionInfo: jest.fn().mockResolvedValue({
                session: { state: 'active', session_id: 'test' }
            }),
            startSession: jest.fn()
        };

        const mockWS = {
            connect: jest.fn(),
            disconnect: jest.fn(),
            isConnected: () => false
        };

        const service = new SessionService(mockApi, mockWS, new SessionState(), new NavigationService());

        const result = await service.selectSession('test-session');

        expect(result.sessionId).toBe('test-session');
        expect(result.connectionStatus.connected).toBe(true);
        expect(mockWS.connect).toHaveBeenCalledWith('test-session');
    });
});
```

### Layer 1: Presentation Testing

```javascript
// Test rendering without business logic or real data
describe('MessageRenderer', () => {
    it('should create message element with correct structure', () => {
        const renderer = new MessageRenderer();

        const messageData = {
            role: 'assistant',
            content: 'Hello world',
            timestamp: '2025-10-19T12:00:00Z',
            shouldShowMetadata: false
        };

        const element = renderer.createMessageElement(messageData);

        expect(element.className).toContain('message-assistant');
        expect(element.querySelector('.message-content').textContent).toBe('Hello world');
    });
});
```

### Integration Testing

```javascript
// Test full stack with orchestrator
describe('Session Selection Integration', () => {
    let app;

    beforeEach(() => {
        document.body.innerHTML = `
            <div id="messages-area"></div>
            <div id="sessions-list-container"></div>
        `;
        app = new ClaudeWebUIOrchestrator();
    });

    it('should select session and update UI', async () => {
        await app.sessionList.onSessionClick('test-session');

        // Check business layer state
        expect(app.sessionState.currentSessionId).toBe('test-session');

        // Check presentation layer updated
        const activeElement = document.querySelector('.session-item.active');
        expect(activeElement.dataset.sessionId).toBe('test-session');
    });
});
```

## Benefits and Trade-offs

### Benefits

1. **Clear Separation of Concerns**
   - DOM code cannot accidentally call APIs
   - Business logic testable without browser
   - Data layer swappable (mock for tests, real for production)

2. **Improved Testability**
   - Each layer tested in isolation
   - Fast unit tests (no DOM or network for business logic)
   - Integration tests verify layer contracts

3. **Better Navigation**
   - Know exactly where to look: UI issue → presentation/, logic bug → business/, API problem → data/
   - Smaller files (200-400 lines each vs 5000+ monolith)

4. **Easier Onboarding**
   - New developers understand layer responsibilities quickly
   - Can work on one layer without understanding others deeply

5. **Scalability**
   - Add new features by extending layers independently
   - Swap implementations (e.g., replace WebSocket with SSE)

6. **Parallel Development**
   - Frontend dev works on presentation
   - Backend dev works on data layer
   - Both use business layer contracts

### Trade-offs

1. **More Files**
   - 1 file (5016 lines) becomes ~30 files (150-300 lines each)
   - More navigation between files during development
   - **Mitigation**: Good IDE navigation, clear naming conventions

2. **Indirection**
   - Feature flow spans multiple files
   - Following execution path requires jumping layers
   - **Mitigation**: Clear naming, comprehensive documentation, good logging

3. **Boilerplate**
   - Event emitters, dependency injection setup
   - Interface contracts and type definitions
   - **Mitigation**: Reusable base classes, helper utilities

4. **Migration Effort**
   - Significant upfront work to refactor
   - Risk of breaking existing functionality
   - **Mitigation**: Gradual migration, extensive testing, feature flags

5. **Over-Engineering Risk**
   - Could be overkill for simple features
   - Layer boundaries might feel restrictive
   - **Mitigation**: Pragmatic enforcement, allow exceptions for trivial features

### Comparison with Plan 1 (Domain-Based)

| Aspect | Plan 2 (Layer-Based) | Plan 1 (Domain-Based) |
|--------|----------------------|----------------------|
| **Organization** | By technical role (UI/Business/Data) | By feature domain (Session/Message/Tool) |
| **Testing** | Easy to mock layers | Easy to test features end-to-end |
| **Navigation** | Know layer, find all features | Know feature, find all layers |
| **Team Structure** | Aligns with frontend/backend split | Aligns with feature team structure |
| **New Features** | Touch multiple layers | Mostly in one domain |
| **Refactoring** | Change layer implementation | Change domain implementation |

**Recommendation**: Use Layer-Based (Plan 2) if:
- Team has distinct frontend/backend roles
- Testing isolation is critical
- Need to swap infrastructure (WebSocket → SSE, etc.)
- Codebase will grow significantly

Use Domain-Based (Plan 1) if:
- Feature teams own full stack
- Prefer colocation of related code
- Want faster feature iteration

## Implementation Checklist

### Week 1: Data Layer
- [ ] Create `data/websocket/session-websocket.js`
- [ ] Create `data/websocket/ui-websocket.js`
- [ ] Create `data/api/session-api.js`
- [ ] Create `data/api/message-api.js`
- [ ] Create `data/transforms/message-transform.js`
- [ ] Write unit tests for data layer
- [ ] Verify with integration tests

### Week 2: Business Layer
- [ ] Create `business/state/session-state.js`
- [ ] Create `business/state/message-state.js`
- [ ] Create `business/services/session-service.js`
- [ ] Create `business/services/message-service.js`
- [ ] Create `business/workflows/session-workflows.js`
- [ ] Write unit tests for business layer
- [ ] Verify state management works correctly

### Week 3: Presentation Layer
- [ ] Create `presentation/components/session-list.js`
- [ ] Create `presentation/components/message-display.js`
- [ ] Create `presentation/components/chat-controls.js`
- [ ] Create `presentation/renderers/message-renderer.js`
- [ ] Create `presentation/components/drag-drop.js`
- [ ] Write unit tests for presentation
- [ ] Verify UI updates correctly

### Week 4: Integration
- [ ] Create `orchestrator.js`
- [ ] Wire all layers together
- [ ] Update `index.html` script loading
- [ ] Slim down `app.js` to bootstrap only
- [ ] Run full integration test suite
- [ ] Performance testing
- [ ] Deploy to staging

---

**Total Estimated Effort**: 4-6 weeks for full migration, or 8-12 weeks for gradual migration

**Risk Level**: Medium-High (significant architectural change)

**Recommended Approach**: Gradual migration feature-by-feature with thorough testing at each step
