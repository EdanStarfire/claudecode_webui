# Vue 3 + Pinia Migration Plan

## Overview

This document outlines the complete migration from the 6767-line vanilla JavaScript `static/app.js` to a modern Vue 3 + Pinia architecture.

## Problem Statement

The original vanilla JS codebase suffered from:
1. **State Management Hell** (Priority 5): Dual Map+Array storage requiring manual synchronization
2. **Bug-prone UI Updates** (Priority 4): Manual `renderSessions()` calls easy to forget
3. **Event Listener Leaks** (Priority 3): 91 event listeners with no cleanup
4. **Monolithic Complexity**: Single 6767-line file, 135 instance variables, impossible to maintain

## Solution

Migrate to Vue 3 + Pinia + Vite:
- **Vue 3 Composition API**: Component-based architecture with automatic reactivity
- **Pinia**: Centralized state management with single source of truth
- **Vite**: Modern build tool with instant Hot Module Replacement (HMR)
- **Vue Router**: Client-side routing for session/timeline/spy views

## Timeline

**Total: 3-4 weeks (22 days)**

- **Phase 1** (Days 1-5): Setup + Infrastructure + Stores
- **Phase 2** (Days 6-10): Layout + Project/Session UI + Modals
- **Phase 3** (Days 11-15): Messages + Tool Handlers
- **Phase 4** (Days 16-18): Legion Features
- **Phase 5** (Days 19-21): Testing + Polish
- **Phase 6** (Day 22): Cutover

## Architecture

### Directory Structure

```
frontend/
├── src/
│   ├── main.js                 # App entry point
│   ├── App.vue                 # Root component
│   ├── router/                 # Vue Router
│   │   └── index.js
│   ├── stores/                 # Pinia stores (state management)
│   │   ├── session.js          # Session state + CRUD
│   │   ├── project.js          # Project state + CRUD
│   │   ├── message.js          # Messages + tool calls
│   │   ├── websocket.js        # WebSocket connections
│   │   └── ui.js               # UI state (sidebar, modals, etc.)
│   ├── composables/            # Reusable logic
│   │   └── useWebSocket.js
│   ├── components/             # Vue components
│   │   ├── layout/             # AppHeader, Sidebar, etc.
│   │   ├── project/            # Project components
│   │   ├── session/            # Session components
│   │   ├── messages/           # Message display
│   │   ├── tools/              # Tool call system
│   │   └── legion/             # Legion features
│   ├── utils/                  # Utilities
│   │   └── api.js              # API client
│   └── assets/                 # Styles, images
│       └── styles.css
├── index.html                  # HTML entry point
├── vite.config.js              # Vite configuration
├── package.json                # Dependencies
└── .env                        # Environment variables
```

### State Management Architecture

#### Before (Vanilla JS - State Management Hell)

```javascript
class ClaudeWebUI {
  constructor() {
    this.sessions = new Map();           // ❌ Duplication
    this.orderedSessions = [];           // ❌ Same data, different structure
    this.currentSessionId = null;        // ❌ Manual tracking
    // ... 132 more instance variables
  }

  updateSessionData(sessionId, sessionInfo) {
    this.sessions.set(sessionId, sessionInfo);     // ❌ Update Map
    const index = this.orderedSessions.findIndex(...);
    this.orderedSessions[index] = sessionInfo;    // ❌ Update Array
    this.renderSessions();                         // ❌ EASY TO FORGET!
  }
}
```

#### After (Pinia - Single Source of Truth)

```javascript
// stores/session.js
export const useSessionStore = defineStore('session', () => {
  // Single source of truth - no duplication!
  const sessions = ref(new Map())
  const currentSessionId = ref(null)

  // Automatically sorted - no manual array maintenance
  const orderedSessions = computed(() =>
    Array.from(sessions.value.values()).sort((a, b) => a.order - b.order)
  )

  // Update session - UI updates automatically!
  function updateSession(sessionId, updates) {
    const session = sessions.value.get(sessionId)
    Object.assign(session, updates)
    sessions.value = new Map(sessions.value) // Trigger reactivity
    // ✅ NO MANUAL renderSessions() NEEDED!
  }
})
```

### Component Architecture

#### Key Benefits

1. **Automatic Reactivity**: UI updates automatically when state changes
2. **Automatic Cleanup**: Event listeners removed when components unmount
3. **Reusability**: Components can be reused across views
4. **Testability**: Components can be tested in isolation

#### Component Example

```vue
<!-- SessionItem.vue -->
<template>
  <div
    class="list-group-item"
    :class="{ active: isSelected }"
    @click="selectSession"
  >
    <StatusIndicator :state="session.state" />
    <span>{{ session.name }}</span>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useSessionStore } from '@/stores/session'

const props = defineProps({ session: Object })
const sessionStore = useSessionStore()

const isSelected = computed(() =>
  sessionStore.currentSessionId === props.session.session_id
)

function selectSession() {
  sessionStore.selectSession(props.session.session_id)
}

// ✅ Event listener automatically removed on unmount!
// ✅ UI automatically updates when session state changes!
</script>
```

## Migration Strategy

### Big Bang Cutover (Non-Destructive)

1. Build entire Vue app in `frontend/` directory
2. Keep `static/` unchanged during development
3. Test Vue app thoroughly on port 5173 (Vite dev server)
4. When ready, build production bundle: `npm run build`
5. Update FastAPI to serve `frontend/dist/` instead of `static/`
6. Monitor for issues
7. After 1 week of stability, delete `static/` directory

### Rollback Plan

If critical issues found:
1. Stop serving `frontend/dist/`
2. Revert FastAPI to serve `static/index.html`
3. Restart backend
4. Debug Vue app in parallel
5. Re-cutover when fixed

## Development Workflow

### Running the Dev Server

```bash
# Terminal 1: Start backend (port 8001)
cd claudecode_webui
uv run python main.py --debug-all --port 8001

# Terminal 2: Start frontend dev server (port 5173)
cd frontend
npm run dev
```

- Frontend runs on `http://localhost:5173`
- API requests proxied to `http://0.0.0.0:8001`
- WebSocket connections proxied to `ws://0.0.0.0:8001`
- Hot Module Replacement (HMR) provides instant updates

### Building for Production

```bash
cd frontend
npm run build

# Output: frontend/dist/
# Serve this with FastAPI
```

### Environment Configuration

Create `.env` file in `frontend/` directory:

```bash
# Backend configuration
VITE_BACKEND_HOST=0.0.0.0
VITE_BACKEND_PORT=8001
```

## Phase-by-Phase Details

### Phase 1: Setup + Infrastructure (✅ COMPLETE)

**Completed:**
- ✅ Initialized Vite + Vue 3 project
- ✅ Installed all dependencies
- ✅ Configured Vite with dynamic backend proxy
- ✅ Created 5 Pinia stores (session, project, message, websocket, ui)
- ✅ Set up Vue Router with all routes
- ✅ Created useWebSocket composable
- ✅ Created API utility (replaced broken APIClient)
- ✅ Created basic layout components (AppHeader, Sidebar, etc.)
- ✅ Created placeholder route components (NoSessionSelected, SessionView, etc.)

**Status:** Foundation complete. App can connect to backend and load data.

### Phase 2: Core UI (Next)

**Remaining Tasks:**
- Build full ProjectItem component with expansion/collapse
- Build full SessionItem component with status indicators
- Implement all modals:
  - ProjectCreateModal with folder browser
  - ProjectEditModal with delete
  - SessionCreateModal with form validation
  - SessionEditModal
  - SessionManageModal (restart/reset/delete)
- Implement drag-and-drop for reordering (use native or library)
- Add session/project actions (edit, delete, reorder)

### Phase 3: Messages + Tools

**Remaining Tasks:**
- Build complete message rendering (UserMessage, AssistantMessage, SystemMessage)
- Implement markdown rendering with code highlighting
- Build ToolCallCard with expand/collapse
- Port all tool handlers to Vue components:
  - EditToolHandler (diff view)
  - ReadToolHandler (file preview)
  - WriteToolHandler
  - TodoToolHandler (checklist)
  - BashToolHandler (command output)
  - SearchToolHandler (Grep/Glob results)
  - WebToolHandler
  - McpToolHandler
- Build PermissionModal with suggestions
- Implement auto-scroll with user scroll detection
- Add message filtering logic

### Phase 4: Legion Features

**Remaining Tasks:**
- Implement TimelineView (Legion communication timeline)
- Implement SpyView (individual minion inspection)
- Build CommComposer component
- Implement tag autocomplete for mentions
- Connect Legion WebSocket

### Phase 5: Testing + Polish

**Testing Checklist:**
- [ ] Test all WebSocket reconnection scenarios
- [ ] Test all modal workflows
- [ ] Test drag-and-drop edge cases
- [ ] Test permission flow end-to-end
- [ ] Test session creation/deletion
- [ ] Test project creation/deletion
- [ ] Test message display (all types)
- [ ] Test tool call lifecycle
- [ ] Test Legion views
- [ ] Performance testing
- [ ] Cross-browser testing (Chrome, Firefox, Edge)
- [ ] Mobile responsiveness
- [ ] Error handling for all API calls

**Polish Tasks Completed:**

**Status Bar Enhancements:**
- ✅ Add mode switcher button for sessions to status bar
- ✅ Add session info button for sessions to status bar
- ✅ Add manage session button and modal for sessions to status bar

**Timeline/Legion Features:**
- ✅ Add autoscroll control button to timeline view
- ✅ Update default names for minions and for initial sessions (changed to "main")
- ✅ Remove "respond using send_comms" message for non-user-sourced Comms
- ✅ Fix minion-state-indicator's background to reflect currently selected minion state
- ✅ Standardize Legion Header Bars
- ✅ Fix add minion button on Legion header - instantiate AddMinion modal (CreateMinionModal complete, backend pending)
- ✅ Minion name validation - enforce single-word names (no spaces) for #nametag matching

**Session Management:**
- ✅ Expose session ID in Session Info Modal
- ✅ Work with confirmation in the DangerZone inside edit project/session modals
- ✅ Fix ExitPlanMode by forcing a setMode suggestion to acceptEdits

**Session State & Permission Management:**
- ✅ PAUSED state for pending permissions - Sessions enter PAUSED state while waiting for permission responses, with yellow blinking indicator across all UI components (SessionItem, ProjectStatusLine, SpySelector)
- ✅ Orphaned permission cleanup - Automatically clean up orphaned permission requests on server restart, session termination, and session restart by storing synthetic denial messages
- ✅ Permission mode sync - Keep permission mode synchronized between session state and init data for consistent UI display
- ✅ Interrupt during PAUSED - Resolve pending permissions with denial when session is interrupted
- ✅ Startup cleanup - Reset PAUSED sessions to TERMINATED on server startup to handle orphaned permission requests from crashes

**Deeplink & Auto-Start:**
- ✅ Deeplink auto-start - Sessions accessed via deeplink now auto-fetch if not in store and auto-start if in created/terminated states
- ✅ Wait for active state - WebSocket connection now waits up to 60 seconds for session to become 'active' (not just 'starting') with progress logs every 5 seconds

**Permission Request Restoration:**
- ✅ Page refresh restoration - Permission prompts are now restored on page refresh by processing permission_request messages during initial message load

**bypassPermissions Mode Support:**
- ✅ Creation - Allow bypassPermissions selection in SessionCreateModal
- ✅ Editing - Conditionally show bypassPermissions in SessionEditModal based on initial_permission_mode
- ✅ Status bar - Display bypassPermissions icon but cycle to 'default' when clicked (don't include in normal rotation)

**Input Area & UX:**
- ✅ Disable during startup - Input area (textarea and send button) disabled while session is in 'starting' state with appropriate placeholder text
- ✅ Naming clarity - Changed 'paused-processing' to 'pending-prompt' for clearer user understanding
- ✅ Color visibility - Changed pending-prompt from pale yellow to warning yellow (#ffc107) with darker border (#e0a800) for better visibility

**Comm Routing:**
- ✅ Conditional instructions - Only show "respond using send_comm()" instruction for user messages, not minion-to-minion comms

**MCP Tool Description:**
- ✅ Proactive usage hint - Added "Proactively use when needing to respond to or ask questions of another #minion" to send_comm tool description

**Orphaned Tool Handling:**
- ✅ Frontend tracks open tool uses via tool_use_id (direct linkage, no heuristics needed)
- ✅ Detects session restarts (client_launched), interrupts, and terminations
- ✅ Automatically marks orphaned tools as cancelled with appropriate messaging
- ✅ Works in both load-time and real-time scenarios
- ✅ Hides permission prompts for orphaned tools
- ✅ Shows clear "Tool Execution Cancelled" or "Permission Request Cancelled" banners
- ✅ No backend synthetic messages needed - keeps logs clean

**Remaining Polish Tasks:**

**UI/UX Improvements:**
- [ ] Re-theme the entire app
- [ ] Re-layout the message card area
- [ ] Review all icons and ensure consistency among views, messages, and dropdowns
- [ ] Review mobile vs Desktop view for all re-themed items and modals

### Phase 6: Cutover

**Steps:**
1. Run `npm run build` in `frontend/`
2. Update `src/web_server.py` to serve `frontend/dist/`
3. Test production build
4. Deploy
5. Monitor logs for issues
6. After stability, delete `static/` directory

## Key Technical Decisions

### Naming Conventions

- **camelCase**: All variables, functions, computed properties
- **PascalCase**: Component names (Vue convention)
- **kebab-case**: Component file names (Vue convention)

### API Client

Replaced broken `core/api-client.js` with clean `utils/api.js`:

```javascript
import { api } from '@/utils/api'

// Usage
const sessions = await api.get('/api/sessions')
await api.post('/api/sessions', { name: 'Test' })
await api.put(`/api/sessions/${id}`, { name: 'Updated' })
await api.delete(`/api/sessions/${id}`)
```

### WebSocket Management

Centralized in `stores/websocket.js`:

```javascript
import { useWebSocketStore } from '@/stores/websocket'

const wsStore = useWebSocketStore()

// Connect to UI WebSocket (global state updates)
wsStore.connectUI()

// Connect to Session WebSocket (message streaming)
wsStore.connectSession(sessionId)

// Send message
wsStore.sendMessage('Hello Claude')

// Send permission response
wsStore.sendPermissionResponse(requestId, 'allow', appliedUpdates)

// Interrupt session
wsStore.interruptSession()
```

### State Updates

**Critical Pattern**: All state updates trigger Vue reactivity automatically!

```javascript
// ❌ OLD WAY (Vanilla JS)
this.sessions.set(sessionId, sessionData)
this.orderedSessions[index] = sessionData
this.renderSessions() // MUST REMEMBER TO CALL THIS!

// ✅ NEW WAY (Vue + Pinia)
sessionStore.updateSession(sessionId, updates)
// UI updates automatically! No render call needed!
```

## Common Gotchas

### 1. Forgetting to Trigger Reactivity on Map Updates

```javascript
// ❌ WRONG - Vue won't detect this change
sessions.value.set(sessionId, session)

// ✅ CORRECT - Reassign to trigger reactivity
sessions.value.set(sessionId, session)
sessions.value = new Map(sessions.value)
```

### 2. Using `this` Instead of Stores

```javascript
// ❌ WRONG - No `this` in Composition API
this.sessions.get(sessionId)

// ✅ CORRECT - Use stores
const sessionStore = useSessionStore()
sessionStore.getSession(sessionId)
```

### 3. Not Using Computed for Derived Data

```javascript
// ❌ WRONG - Won't update automatically
const orderedSessions = sessions.value.sort(...)

// ✅ CORRECT - Use computed
const orderedSessions = computed(() =>
  Array.from(sessions.value.values()).sort(...)
)
```

### 4. Circular Store Imports

```javascript
// ❌ WRONG - Circular dependency
import { useMessageStore } from './message'
const messageStore = useMessageStore()

// ✅ CORRECT - Dynamic import
const messageStore = await import('./message')
messageStore.useMessageStore().addMessage(...)
```

## Success Metrics

### Code Metrics

| Metric | Before | After | Improvement |
|--------|--------|-------|-------------|
| Main file LOC | 6767 (app.js) | ~200 (App.vue) | **97% reduction** |
| Total JS LOC | ~8500 | ~4000 | **53% reduction** |
| Largest component | 6767 lines | ~300 lines | **96% reduction** |
| State locations | 135 instance vars | 5 Pinia stores | **Clear separation** |
| Event listeners | 91 manual | Auto-managed | **0 leaks** |

### Developer Experience

| Task | Before | After |
|------|--------|-------|
| Add new session field | Update 5+ locations | Update store + template (2 places) |
| Debug state | console.log() everywhere | Vue DevTools (visual inspector) |
| Test component | Can't test in isolation | Vitest + Vue Test Utils |
| Find UI update | Search for renderSessions() | Automatic (reactivity) |

## Resources

- **Vue 3 Docs**: https://vuejs.org/guide/introduction.html
- **Pinia Docs**: https://pinia.vuejs.org/
- **Vite Docs**: https://vitejs.dev/guide/
- **Vue Router Docs**: https://router.vuejs.org/

## Support

If you encounter issues during migration:

1. Check Vue DevTools for state inspection
2. Check browser console for errors
3. Check Vite terminal output for build errors
4. Check backend logs for API errors
5. Refer to this migration plan for patterns

---

**Last Updated**: Phase 1 Complete
**Next Phase**: Phase 2 - Core UI Components
