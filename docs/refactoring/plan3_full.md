# Plan 3: Composition-Based Architecture

## Summary
Refactor the monolithic `ClaudeWebUI` class (5016 lines, ~120+ methods) using composition with behavior mixins. Instead of splitting into separate manager classes, compose the main orchestrator from reusable behavioral modules that encapsulate concerns like WebSocket management, message handling, UI interactions, and drag-drop. This approach maintains a single entry point while achieving modularity through "has-a" relationships.

## Current Problems

### Code Organization Issues
- **Massive class file**: 5016 lines with 120+ methods makes navigation difficult
- **Mixed concerns**: WebSocket logic, DOM manipulation, state management, and business logic intertwined
- **Low cohesion**: Related methods scattered throughout the file (e.g., session methods at lines 376-712, drag-drop at 4486-4847)
- **Hard to test**: Monolithic class requires full initialization for testing individual features
- **Tight coupling**: Direct property access (`this.currentSessionId`, `this.sessions`, etc.) makes refactoring risky

### Specific Problem Areas
1. **WebSocket management** (lines 2087-2380): Two separate WebSocket connections with similar retry/error logic duplicated
2. **Modal management** (lines 3731-4310): 15+ modal-related methods with repetitive show/hide/confirm patterns
3. **Drag-drop** (lines 4486-4847): Session and project drag-drop with duplicated indicator/state logic
4. **Message processing** (lines 1038-1478): Complex message routing with tool calls, thinking blocks, and compaction
5. **Session lifecycle** (lines 376-712, 2381-2520): State management mixed with API calls and UI updates

## Proposed Architecture

### Composition Pattern Overview
Use **behavior mixins** to compose `ClaudeWebUI` from reusable modules. Each mixin provides a focused set of related methods that operate on shared state. The main class acts as a lightweight orchestrator that composes behaviors together.

```
┌─────────────────────────────────────────────────────────────┐
│                    ClaudeWebUI (Orchestrator)                │
│  • Minimal initialization logic                              │
│  • Composes behaviors via Object.assign()                    │
│  • Provides shared state properties                          │
│  • Delegates to composed behaviors                           │
└────────┬────────────────────────────────────────────────────┘
         │
         │ Composed from (mixins applied in order):
         │
         ├─► WebSocketBehavior (handles UI + session WebSockets)
         ├─► MessageProcessingBehavior (message routing, display)
         ├─► SessionLifecycleBehavior (create, start, end sessions)
         ├─► ModalManagementBehavior (show/hide/confirm modals)
         ├─► DragDropBehavior (session + project reordering)
         ├─► UIRenderingBehavior (render sessions, projects, messages)
         ├─► AutoScrollBehavior (scroll tracking, auto-scroll)
         ├─► PermissionModeBehavior (mode cycling, UI updates)
         └─► ToolCallRenderingBehavior (tool call display, interactions)
```

### Directory Structure

```
static/
├── app.js                           # Main orchestrator (reduced to ~500 lines)
│
├── behaviors/                       # Composable behavior modules
│   ├── websocket-behavior.js        # WebSocket connection management
│   ├── message-processing-behavior.js  # Message routing and processing
│   ├── session-lifecycle-behavior.js   # Session CRUD and state transitions
│   ├── modal-management-behavior.js    # Modal show/hide/confirm patterns
│   ├── drag-drop-behavior.js          # Drag-drop for sessions and projects
│   ├── ui-rendering-behavior.js       # DOM rendering for sessions/projects
│   ├── auto-scroll-behavior.js        # Auto-scroll detection and control
│   ├── permission-mode-behavior.js    # Permission mode cycling
│   └── tool-call-rendering-behavior.js # Tool call display and interactions
│
├── core/                            # Core infrastructure (unchanged)
│   ├── logger.js
│   ├── constants.js
│   ├── api-client.js
│   └── project-manager.js
│
├── tools/                           # Tool system (unchanged)
│   ├── tool-call-manager.js
│   ├── tool-handler-registry.js
│   └── handlers/
│       └── ... (all existing handlers)
│
├── index.html                       # Updated script load order
└── styles.css                       # Unchanged
```

## Composition Pattern Details

### Mixin Structure
Each behavior mixin is a plain object with methods that will be mixed into `ClaudeWebUI.prototype`:

```javascript
// behaviors/websocket-behavior.js
export const WebSocketBehavior = {
    // UI WebSocket methods
    connectUIWebSocket() {
        // Access shared state via 'this'
        if (this.uiWebsocket && this.uiWebsocket.readyState === WebSocket.OPEN) {
            return;
        }
        // ... implementation using this.uiWebsocket, this.uiConnectionRetryCount, etc.
    },

    handleUIWebSocketMessage(data) {
        // ... implementation
    },

    // Session WebSocket methods
    connectSessionWebSocket() {
        // ... implementation
    },

    handleWebSocketMessage(data) {
        // ... implementation
    },

    disconnectSessionWebSocket() {
        // ... implementation
    },

    scheduleReconnect() {
        // ... implementation
    }
};
```

### Main Orchestrator Pattern
The `ClaudeWebUI` class becomes a lightweight container that:
1. Initializes shared state in constructor
2. Composes behaviors using `Object.assign()`
3. Provides entry points for initialization

```javascript
// app.js
import { WebSocketBehavior } from './behaviors/websocket-behavior.js';
import { MessageProcessingBehavior } from './behaviors/message-processing-behavior.js';
import { SessionLifecycleBehavior } from './behaviors/session-lifecycle-behavior.js';
// ... other imports

class ClaudeWebUI {
    constructor() {
        // Initialize shared state
        this.currentSessionId = null;
        this.sessions = new Map();
        this.orderedSessions = [];

        this.projectManager = new ProjectManager(this);
        this.currentProjectId = null;

        this.sessionWebsocket = null;
        this.uiWebsocket = null;
        this.sessionConnectionRetryCount = 0;
        this.uiConnectionRetryCount = 0;

        this.autoScrollEnabled = true;
        this.isUserScrolling = false;

        this.isProcessing = false;
        this.currentPermissionMode = 'default';

        this.toolCallManager = new ToolCallManager();
        this.toolHandlerRegistry = new ToolHandlerRegistry();
        this.defaultToolHandler = new DefaultToolHandler();

        // ... other state initialization

        this.init();
    }

    async init() {
        this.initializeToolHandlers();
        this.setupEventListeners();
        await this.loadSessions();
        this.connectUIWebSocket();

        const sessionId = this.getSessionIdFromURL();
        if (sessionId) {
            await this.selectSession(sessionId);
        } else {
            this.showNoSessionSelected();
        }
    }

    // Minimal orchestrator methods
    initializeToolHandlers() { /* ... */ }
    setupEventListeners() { /* delegates to behaviors */ }
    getSessionIdFromURL() { /* ... */ }
}

// Compose behaviors into prototype
Object.assign(ClaudeWebUI.prototype,
    WebSocketBehavior,
    MessageProcessingBehavior,
    SessionLifecycleBehavior,
    ModalManagementBehavior,
    DragDropBehavior,
    UIRenderingBehavior,
    AutoScrollBehavior,
    PermissionModeBehavior,
    ToolCallRenderingBehavior
);

// Initialize app
document.addEventListener('DOMContentLoaded', () => {
    window.app = new ClaudeWebUI();
});
```

### Behavior Dependencies
Some behaviors depend on methods from other behaviors. Dependencies are managed through:
1. **Composition order**: Apply mixins in dependency order
2. **Shared state**: All behaviors access `this` properties
3. **Method delegation**: Behaviors can call each other's methods via `this`

Example dependency chain:
- `MessageProcessingBehavior.processMessage()` calls `ToolCallRenderingBehavior.renderToolCall()`
- `SessionLifecycleBehavior.selectSession()` calls `WebSocketBehavior.connectSessionWebSocket()`
- `UIRenderingBehavior.renderSessions()` calls `DragDropBehavior.addDragDropListeners()`

## Migration Strategy

### Phase 1: Extract WebSocket Behavior (Week 1)
**Goal**: Prove the pattern works with a clear, isolated concern

1. Create `behaviors/websocket-behavior.js`
2. Move WebSocket methods (lines 2087-2380):
   - `connectUIWebSocket()`
   - `handleUIWebSocketMessage()`
   - `connectSessionWebSocket()`
   - `handleWebSocketMessage()`
   - `disconnectSessionWebSocket()`
   - `scheduleReconnect()`
3. Update `app.js` to import and compose behavior
4. Test WebSocket reconnection, message handling, cleanup

**Validation**: All WebSocket tests pass, no functionality changes

### Phase 2: Extract Modal Management (Week 1-2)
**Goal**: Extract repetitive patterns

1. Create `behaviors/modal-management-behavior.js`
2. Move modal methods (lines 3731-4310):
   - Session modals: `showCreateSessionModal()`, `hideCreateSessionModal()`, `handleCreateSession()`
   - Project modals: `showCreateProjectModal()`, `hideCreateProjectModal()`, `handleCreateProject()`
   - Edit/delete modals: `showEditSessionModal()`, `confirmSessionRename()`, etc.
   - Folder browser: `showFolderBrowser()`, `browseFolderPath()`, etc.
3. Test all modal workflows

### Phase 3: Extract Drag-Drop Behavior (Week 2)
**Goal**: Isolate complex interaction logic

1. Create `behaviors/drag-drop-behavior.js`
2. Move drag-drop methods (lines 4486-4847):
   - Session drag-drop: `addDragDropListeners()`, `handleSessionDrop()`, `calculateNewOrder()`
   - Project drag-drop: `addProjectDragListeners()`, `reorderProjects()`
   - Visual feedback: `showDropIndicator()`, `hideDropIndicator()`, etc.
3. Test drag-drop for sessions and projects

### Phase 4: Extract Message Processing (Week 3)
**Goal**: Separate message routing logic

1. Create `behaviors/message-processing-behavior.js`
2. Move message handling (lines 1038-1478):
   - `processMessage()`, `handleIncomingMessage()`
   - `shouldDisplayMessage()`, `_hasToolUses()`, `_hasToolResults()`
   - `handleCompactionMessage()`, `handleToolRelatedMessage()`, `handleThinkingBlockMessage()`
   - `extractPermissionMode()`
3. Test message display, filtering, tool call routing

### Phase 5: Extract Remaining Behaviors (Week 3-4)
Extract in this order:
1. `session-lifecycle-behavior.js` - Session CRUD and state management
2. `ui-rendering-behavior.js` - DOM rendering for sessions/projects
3. `tool-call-rendering-behavior.js` - Tool call display and interactions
4. `permission-mode-behavior.js` - Permission mode cycling
5. `auto-scroll-behavior.js` - Auto-scroll functionality

### Phase 6: Final Cleanup (Week 4)
1. Update `index.html` script load order
2. Document behavior dependencies
3. Update tests to use behaviors
4. Remove obsolete code from `app.js`

## Example Refactoring

### Example 1: WebSocket Behavior

**Before** (lines 2087-2134 in app.js):
```javascript
class ClaudeWebUI {
    constructor() {
        this.uiWebsocket = null;
        this.uiConnectionRetryCount = 0;
        this.maxUIRetries = 10;
        // ...
    }

    connectUIWebSocket() {
        if (this.uiWebsocket && this.uiWebsocket.readyState === WebSocket.OPEN) {
            Logger.debug('WS_UI', 'UI WebSocket already connected');
            return;
        }

        Logger.info('WS_UI', 'Connecting to UI WebSocket');
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws/ui`;

        this.uiWebsocket = new WebSocket(wsUrl);

        this.uiWebsocket.onopen = () => {
            Logger.info('WS_UI', 'UI WebSocket connected successfully');
            this.uiConnectionRetryCount = 0;
        };

        this.uiWebsocket.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                this.handleUIWebSocketMessage(data);
            } catch (error) {
                Logger.error('WS_UI', 'Error parsing UI WebSocket message', error);
            }
        };

        this.uiWebsocket.onclose = (event) => {
            Logger.info('WS_UI', 'UI WebSocket disconnected', {code: event.code, reason: event.reason});
            this.uiWebsocket = null;

            if (this.uiConnectionRetryCount < this.maxUIRetries) {
                this.uiConnectionRetryCount++;
                const delay = Math.min(1000 * Math.pow(2, this.uiConnectionRetryCount), 30000);
                setTimeout(() => this.connectUIWebSocket(), delay);
            }
        };

        this.uiWebsocket.onerror = (error) => {
            Logger.error('WS_UI', 'UI WebSocket error', error);
        };
    }
}
```

**After** (behaviors/websocket-behavior.js):
```javascript
// behaviors/websocket-behavior.js
export const WebSocketBehavior = {
    connectUIWebSocket() {
        if (this.uiWebsocket && this.uiWebsocket.readyState === WebSocket.OPEN) {
            Logger.debug('WS_UI', 'UI WebSocket already connected');
            return;
        }

        Logger.info('WS_UI', 'Connecting to UI WebSocket');
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws/ui`;

        this.uiWebsocket = new WebSocket(wsUrl);

        this.uiWebsocket.onopen = () => {
            Logger.info('WS_UI', 'UI WebSocket connected successfully');
            this.uiConnectionRetryCount = 0;
        };

        this.uiWebsocket.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                this.handleUIWebSocketMessage(data);
            } catch (error) {
                Logger.error('WS_UI', 'Error parsing UI WebSocket message', error);
            }
        };

        this.uiWebsocket.onclose = (event) => {
            Logger.info('WS_UI', 'UI WebSocket disconnected', {code: event.code, reason: event.reason});
            this.uiWebsocket = null;

            if (this.uiConnectionRetryCount < this.maxUIRetries) {
                this.uiConnectionRetryCount++;
                const delay = Math.min(1000 * Math.pow(2, this.uiConnectionRetryCount), 30000);
                setTimeout(() => this.connectUIWebSocket(), delay);
            }
        };

        this.uiWebsocket.onerror = (error) => {
            Logger.error('WS_UI', 'UI WebSocket error', error);
        };
    },

    handleUIWebSocketMessage(data) {
        Logger.debug('WS_UI', 'UI WebSocket message received', data.type);

        switch (data.type) {
            case 'sessions_list':
                this.updateSessionsList(data.data.sessions);
                break;
            case 'state_change':
                this.handleStateChange(data.data);
                break;
            case 'project_updated':
                this.handleProjectUpdated(data.data);
                break;
            case 'project_deleted':
                this.handleProjectDeleted(data.data);
                break;
            case 'ping':
                if (this.uiWebsocket && this.uiWebsocket.readyState === WebSocket.OPEN) {
                    this.uiWebsocket.send(JSON.stringify({type: 'pong', timestamp: new Date().toISOString()}));
                }
                break;
            default:
                Logger.warn('WS_UI', 'Unknown UI WebSocket message type', data.type);
        }
    },

    connectSessionWebSocket() {
        // Similar pattern for session WebSocket
        // ... implementation
    },

    disconnectSessionWebSocket() {
        if (this.sessionWebsocket) {
            this.intentionalSessionDisconnect = true;
            this.sessionWebsocket.close();
            this.sessionWebsocket = null;
        }
    },

    scheduleReconnect() {
        if (this.intentionalSessionDisconnect) return;

        if (this.sessionConnectionRetryCount < this.maxSessionRetries) {
            this.sessionConnectionRetryCount++;
            const delay = Math.min(1000 * Math.pow(2, this.sessionConnectionRetryCount), 30000);
            setTimeout(() => {
                if (this.currentSessionId && !this.intentionalSessionDisconnect) {
                    this.connectSessionWebSocket();
                }
            }, delay);
        }
    }
};
```

**Updated app.js**:
```javascript
import { WebSocketBehavior } from './behaviors/websocket-behavior.js';

class ClaudeWebUI {
    constructor() {
        // State initialization only
        this.uiWebsocket = null;
        this.sessionWebsocket = null;
        this.uiConnectionRetryCount = 0;
        this.sessionConnectionRetryCount = 0;
        this.maxUIRetries = 10;
        this.maxSessionRetries = 5;
        this.intentionalSessionDisconnect = false;
        // ...
    }
}

// Compose behavior
Object.assign(ClaudeWebUI.prototype, WebSocketBehavior);
```

### Example 2: Modal Management Behavior

**Before** (lines 3732-3758 in app.js):
```javascript
class ClaudeWebUI {
    showCreateSessionModal() {
        document.getElementById('working-directory').value = '.';
        const modalElement = document.getElementById('create-session-modal');
        const modal = new bootstrap.Modal(modalElement);
        modal.show();
    }

    hideCreateSessionModal() {
        const modalElement = document.getElementById('create-session-modal');
        const modal = bootstrap.Modal.getInstance(modalElement);
        if (modal) {
            modal.hide();
        }

        const form = document.getElementById('create-session-form');
        form.reset();

        const submitBtn = modalElement.querySelector('button[type="submit"]');
        const dismissButtons = modalElement.querySelectorAll('[data-bs-dismiss="modal"]');
        if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.innerHTML = 'Create Session';
        }
        dismissButtons.forEach(btn => btn.disabled = false);
    }

    handleCreateSession(event) {
        event.preventDefault();
        // ... 30+ lines of form handling, API calls, UI updates
    }
}
```

**After** (behaviors/modal-management-behavior.js):
```javascript
// behaviors/modal-management-behavior.js
export const ModalManagementBehavior = {
    // Generic modal helper
    _showModal(elementId, setupFn = null) {
        const modalElement = document.getElementById(elementId);
        if (setupFn) setupFn(modalElement);
        const modal = new bootstrap.Modal(modalElement);
        modal.show();
    },

    _hideModal(elementId, cleanupFn = null) {
        const modalElement = document.getElementById(elementId);
        const modal = bootstrap.Modal.getInstance(modalElement);
        if (modal) modal.hide();
        if (cleanupFn) cleanupFn(modalElement);
    },

    _disableModalButtons(modalElement, disabled = true) {
        const submitBtn = modalElement.querySelector('button[type="submit"]');
        const dismissButtons = modalElement.querySelectorAll('[data-bs-dismiss="modal"]');

        if (submitBtn) submitBtn.disabled = disabled;
        dismissButtons.forEach(btn => btn.disabled = disabled);
    },

    // Session modals
    showCreateSessionModal() {
        this._showModal('create-session-modal', (modal) => {
            document.getElementById('working-directory').value = '.';
        });
    },

    hideCreateSessionModal() {
        this._hideModal('create-session-modal', (modal) => {
            const form = document.getElementById('create-session-form');
            form.reset();

            const submitBtn = modal.querySelector('button[type="submit"]');
            if (submitBtn) {
                submitBtn.disabled = false;
                submitBtn.innerHTML = 'Create Session';
            }
            this._disableModalButtons(modal, false);
        });
    },

    async handleCreateSession(event) {
        event.preventDefault();
        const formData = new FormData(event.target);
        const data = Object.fromEntries(formData.entries());

        const modalElement = document.getElementById('create-session-modal');
        this._disableModalButtons(modalElement, true);

        try {
            await this.createSession(data);
            this.hideCreateSessionModal();
        } catch (error) {
            Logger.error('SESSION', 'Failed to create session', error);
            this._disableModalButtons(modalElement, false);
        }
    },

    // Project modals
    showCreateProjectModal() {
        this._showModal('create-project-modal');
    },

    hideCreateProjectModal() {
        this._hideModal('create-project-modal', (modal) => {
            const form = document.getElementById('create-project-form');
            if (form) form.reset();
        });
    },

    async handleCreateProject(event) {
        event.preventDefault();
        const formData = new FormData(event.target);
        const data = Object.fromEntries(formData.entries());

        try {
            await this.projectManager.createProject(data.name, data.working_directory);
            this.hideCreateProjectModal();
        } catch (error) {
            Logger.error('PROJECT', 'Failed to create project', error);
        }
    },

    // Folder browser modal
    showFolderBrowser(targetInputId, initialPath = null) {
        this.folderBrowserTargetInput = targetInputId;
        this.browseFolderPath(initialPath);
        this._showModal('folder-browser-modal');
    },

    hideFolderBrowser() {
        this._hideModal('folder-browser-modal', () => {
            this.folderBrowserTargetInput = null;
        });
    },

    // ... other modal methods (edit, delete confirmations, etc.)
};
```

### Example 3: Drag-Drop Behavior

**Before** (lines 4486-4600 in app.js):
```javascript
class ClaudeWebUI {
    constructor() {
        this.dragState = null;
        this.projectDragState = null;
    }

    addDragDropListeners(sessionElement, sessionId, projectId) {
        if (!this.dragState) {
            this.dragState = {
                draggedElement: null,
                draggedSessionId: null,
                draggedProjectId: null,
                dropIndicator: null
            };
        }

        sessionElement.addEventListener('dragstart', (e) => {
            // 20+ lines of dragstart logic
        });

        sessionElement.addEventListener('dragover', (e) => {
            // 15+ lines of dragover logic
        });

        // ... more event listeners
    }

    showDropIndicator(targetElement, event) {
        // 20+ lines of indicator logic
    }

    async handleSessionDrop(targetElement, targetSessionId, projectId) {
        // 30+ lines of drop handling
    }
}
```

**After** (behaviors/drag-drop-behavior.js):
```javascript
// behaviors/drag-drop-behavior.js
export const DragDropBehavior = {
    // Initialize drag state
    _initializeDragState() {
        if (!this.dragState) {
            this.dragState = {
                draggedElement: null,
                draggedSessionId: null,
                draggedProjectId: null,
                dropIndicator: null
            };
        }
    },

    _initializeProjectDragState() {
        if (!this.projectDragState) {
            this.projectDragState = {
                draggedElement: null,
                draggedProjectId: null,
                dropIndicator: null,
                insertBefore: false,
                isReordering: false
            };
        }
    },

    // Session drag-drop
    addDragDropListeners(sessionElement, sessionId, projectId) {
        this._initializeDragState();

        sessionElement.addEventListener('dragstart', (e) => {
            this._handleSessionDragStart(e, sessionElement, sessionId, projectId);
        });

        sessionElement.addEventListener('dragover', (e) => {
            this._handleSessionDragOver(e, sessionElement, sessionId, projectId);
        });

        sessionElement.addEventListener('drop', (e) => {
            this._handleSessionDrop(e, sessionElement, sessionId, projectId);
        });

        sessionElement.addEventListener('dragend', () => {
            this._cleanupDragState();
        });
    },

    _handleSessionDragStart(e, sessionElement, sessionId, projectId) {
        this.dragState.draggedElement = sessionElement;
        this.dragState.draggedSessionId = sessionId;
        this.dragState.draggedProjectId = projectId;
        sessionElement.classList.add('dragging');
        e.dataTransfer.effectAllowed = 'move';
    },

    _handleSessionDragOver(e, sessionElement, sessionId, projectId) {
        const draggedProjectId = this.dragState.draggedProjectId;
        if (draggedProjectId !== projectId) return; // Cross-project not allowed

        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';

        if (this.dragState.draggedElement !== sessionElement) {
            this.showDropIndicator(sessionElement, e);
        }
    },

    async _handleSessionDrop(e, sessionElement, sessionId, projectId) {
        e.preventDefault();
        e.stopPropagation();

        if (!this.dragState.draggedSessionId) return;
        if (this.dragState.draggedSessionId === sessionId) return;
        if (this.dragState.draggedProjectId !== projectId) return;

        const project = this.projectManager.projects.get(projectId);
        if (!project) return;

        const newOrder = this.calculateNewOrder(
            this.dragState.draggedSessionId,
            sessionId,
            project.session_ids
        );

        await this.reorderProjectSessions(projectId, newOrder);
        this._cleanupDragState();
    },

    showDropIndicator(targetElement, event) {
        this.hideDropIndicator();

        const rect = targetElement.getBoundingClientRect();
        const midY = rect.top + rect.height / 2;
        const insertBefore = event.clientY < midY;

        const indicator = document.createElement('div');
        indicator.className = 'drop-indicator';

        if (insertBefore) {
            targetElement.parentNode.insertBefore(indicator, targetElement);
        } else {
            targetElement.parentNode.insertBefore(indicator, targetElement.nextSibling);
        }

        this.dragState.dropIndicator = indicator;
    },

    hideDropIndicator() {
        if (this.dragState?.dropIndicator) {
            this.dragState.dropIndicator.remove();
            this.dragState.dropIndicator = null;
        }
    },

    _cleanupDragState() {
        this.hideDropIndicator();
        if (this.dragState?.draggedElement) {
            this.dragState.draggedElement.classList.remove('dragging');
        }
        this.dragState = null;
    },

    // Project drag-drop (similar pattern)
    addProjectDragListeners(projectElement, projectId) {
        this._initializeProjectDragState();
        // ... similar event listener setup
    },

    // Utility methods
    calculateNewOrder(draggedId, targetId, currentOrder) {
        const draggedIndex = currentOrder.indexOf(draggedId);
        const targetIndex = currentOrder.indexOf(targetId);

        if (draggedIndex === -1 || targetIndex === -1) return currentOrder;

        const newOrder = [...currentOrder];
        newOrder.splice(draggedIndex, 1);

        const insertIndex = targetIndex > draggedIndex ? targetIndex : targetIndex;
        newOrder.splice(insertIndex, 0, draggedId);

        return newOrder;
    },

    async reorderProjectSessions(projectId, sessionIds) {
        try {
            const response = await this.apiRequest(`/api/projects/${projectId}/sessions/reorder`, {
                method: 'PUT',
                body: JSON.stringify({ session_ids: sessionIds })
            });

            if (response.success) {
                const project = this.projectManager.projects.get(projectId);
                if (project) {
                    project.session_ids = sessionIds;
                    await this.renderSessions();
                }
            }
        } catch (error) {
            Logger.error('DRAG_DROP', 'Failed to reorder sessions', error);
        }
    }
};
```

## Testing Approach

### Unit Testing Behaviors
Each behavior mixin can be tested independently by composing it into a minimal test harness:

```javascript
// tests/behaviors/websocket-behavior.test.js
import { WebSocketBehavior } from '../../behaviors/websocket-behavior.js';

describe('WebSocketBehavior', () => {
    let testInstance;

    beforeEach(() => {
        // Create minimal test instance with required state
        testInstance = {
            uiWebsocket: null,
            uiConnectionRetryCount: 0,
            maxUIRetries: 3,
            handleUIWebSocketMessage: jest.fn(),
            updateSessionsList: jest.fn(),
            handleStateChange: jest.fn()
        };

        // Compose behavior into test instance
        Object.assign(testInstance, WebSocketBehavior);
    });

    test('connectUIWebSocket creates WebSocket connection', () => {
        testInstance.connectUIWebSocket();
        expect(testInstance.uiWebsocket).toBeInstanceOf(WebSocket);
    });

    test('connectUIWebSocket skips if already connected', () => {
        testInstance.uiWebsocket = { readyState: WebSocket.OPEN };
        const spy = jest.spyOn(global, 'WebSocket');

        testInstance.connectUIWebSocket();

        expect(spy).not.toHaveBeenCalled();
    });

    test('handleUIWebSocketMessage routes messages correctly', () => {
        testInstance.handleUIWebSocketMessage({
            type: 'state_change',
            data: { session_id: '123' }
        });

        expect(testInstance.handleStateChange).toHaveBeenCalledWith({ session_id: '123' });
    });
});
```

### Integration Testing
Test composition in full `ClaudeWebUI` context:

```javascript
// tests/integration/app.test.js
describe('ClaudeWebUI Integration', () => {
    let app;

    beforeEach(() => {
        app = new ClaudeWebUI();
    });

    test('behaviors are composed correctly', () => {
        expect(typeof app.connectUIWebSocket).toBe('function');
        expect(typeof app.showCreateSessionModal).toBe('function');
        expect(typeof app.addDragDropListeners).toBe('function');
    });

    test('behaviors can call each other', async () => {
        // Mock WebSocket
        global.WebSocket = jest.fn();

        await app.selectSession('test-123');

        // Verify SessionLifecycleBehavior called WebSocketBehavior
        expect(global.WebSocket).toHaveBeenCalled();
    });
});
```

### Behavior Isolation Testing
Test that behaviors don't have unexpected side effects:

```javascript
describe('Behavior Isolation', () => {
    test('WebSocketBehavior does not pollute global scope', () => {
        const before = Object.keys(window);
        Object.assign({}, WebSocketBehavior);
        const after = Object.keys(window);

        expect(after).toEqual(before);
    });

    test('behaviors only access documented state', () => {
        const testInstance = { /* minimal state */ };
        const spy = new Proxy(testInstance, {
            get(target, prop) {
                if (!(prop in target)) {
                    throw new Error(`Unexpected access to ${prop}`);
                }
                return target[prop];
            }
        });

        Object.assign(spy, WebSocketBehavior);
        // Should not throw
        spy.connectUIWebSocket();
    });
});
```

## Benefits and Trade-offs

### Benefits

1. **Modularity without fragmentation**
   - Code is organized into focused modules
   - No need to pass dependencies between classes
   - Single import for consumers (`new ClaudeWebUI()`)

2. **Easy to test**
   - Behaviors can be tested in isolation
   - Test harnesses only need relevant state
   - No need to mock entire class hierarchy

3. **Flexible composition**
   - Add/remove behaviors easily
   - Compose custom variants for testing
   - Behaviors can be reused in other contexts

4. **Gradual migration**
   - Extract one behavior at a time
   - No breaking changes to external API
   - Can mix old and new code during transition

5. **Low coupling**
   - Behaviors don't depend on each other directly
   - Dependencies are through shared state
   - Easy to refactor individual behaviors

6. **Familiar pattern**
   - Similar to React hooks or Vue composables
   - No complex class hierarchies to understand
   - Plain JavaScript objects

7. **Performance**
   - No runtime overhead (methods mixed at startup)
   - No proxy or getter/setter indirection
   - Same performance as monolithic class

### Trade-offs

1. **Shared state coupling**
   - **Issue**: All behaviors access same `this` properties
   - **Mitigation**: Document required state for each behavior, use TypeScript/JSDoc
   - **Example**: Add JSDoc comments listing required properties:
     ```javascript
     /**
      * @requires this.uiWebsocket
      * @requires this.uiConnectionRetryCount
      * @requires this.maxUIRetries
      */
     export const WebSocketBehavior = { /* ... */ };
     ```

2. **Naming conflicts**
   - **Issue**: Two behaviors might define same method name
   - **Mitigation**: Use consistent naming conventions, namespace methods
   - **Example**: Prefix internal methods with `_behavior_`:
     ```javascript
     export const WebSocketBehavior = {
         connectUIWebSocket() { /* public */ },
         _ws_createConnection() { /* internal */ }
     };
     ```

3. **Initialization order matters**
   - **Issue**: Behaviors composed later override earlier ones
   - **Mitigation**: Document composition order, use linting to enforce
   - **Example**: Add composition order comment in app.js:
     ```javascript
     // IMPORTANT: Composition order matters!
     // Later behaviors override earlier ones with same method names
     Object.assign(ClaudeWebUI.prototype,
         WebSocketBehavior,      // 1. Foundation
         MessageProcessingBehavior, // 2. Depends on WebSocket
         // ...
     );
     ```

4. **Harder to trace method calls**
   - **Issue**: IDE "Go to Definition" might not work across behaviors
   - **Mitigation**: Use consistent file naming, add inline comments
   - **Example**: Add method source comments:
     ```javascript
     // Method defined in behaviors/websocket-behavior.js
     connectUIWebSocket() { /* ... */ }
     ```

5. **No compile-time type safety**
   - **Issue**: Can't verify required state exists at compile time
   - **Mitigation**: Use TypeScript or comprehensive JSDoc
   - **Example**: TypeScript interface for shared state:
     ```typescript
     interface ClaudeWebUIState {
         uiWebsocket: WebSocket | null;
         sessionWebsocket: WebSocket | null;
         currentSessionId: string | null;
         // ...
     }
     ```

6. **Behavior dependencies not explicit**
   - **Issue**: Can't see which behaviors depend on others
   - **Mitigation**: Document dependencies in behavior file headers
   - **Example**:
     ```javascript
     /**
      * MessageProcessingBehavior
      *
      * Dependencies:
      * - WebSocketBehavior (calls this.connectSessionWebSocket)
      * - ToolCallRenderingBehavior (calls this.renderToolCall)
      */
     export const MessageProcessingBehavior = { /* ... */ };
     ```

### Comparison with Alternatives

| Aspect | Composition (Plan 3) | Manager Classes (Plan 1) | Layer Architecture (Plan 2) |
|--------|---------------------|-------------------------|--------------------------|
| **Lines per file** | ~300-500 | ~400-800 | ~200-400 |
| **File count** | 9 behaviors + 1 main | 15 managers + 1 main | 30 modules + 1 main |
| **Dependency management** | Shared state | Constructor injection | Layer contracts |
| **Testing complexity** | Low (mock state) | Medium (mock dependencies) | Medium (mock layers) |
| **Migration effort** | Low (gradual) | High (all-or-nothing) | Medium (per layer) |
| **Runtime overhead** | None | Instance creation | Layer boundaries |
| **Type safety** | Medium (JSDoc) | High (interfaces) | Medium (contracts) |
| **Learning curve** | Low (familiar) | Medium (architecture) | Medium (layer patterns) |

## Conclusion

Plan 3 (Composition-Based Architecture) provides a pragmatic middle ground between the monolithic `app.js` and full architectural refactoring. It achieves modularity through composable behaviors while maintaining simplicity and avoiding the complexity of manager classes or layer architectures.

**Best suited for**:
- Teams that want quick wins without major architectural changes
- Codebases that need incremental refactoring
- Projects where shared state is acceptable
- Developers familiar with mixin/trait patterns

**Key success factors**:
1. Document required state for each behavior
2. Maintain consistent composition order
3. Use JSDoc or TypeScript for type hints
4. Migrate one behavior at a time
5. Write tests for each behavior in isolation
