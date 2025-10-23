# Claude Code WebUI - Frontend

Modern Vue 3 + Pinia frontend for Claude Code WebUI.

## Tech Stack

- **Vue 3** - Progressive JavaScript framework with Composition API
- **Pinia** - Official state management for Vue
- **Vite** - Fast build tool with Hot Module Replacement (HMR)
- **Vue Router** - Client-side routing
- **Bootstrap 5** - CSS framework for styling
- **@vueuse/core** - Vue composition utilities
- **marked** + **DOMPurify** - Markdown rendering with XSS protection
- **diff** + **diff2html** - Code diff visualization

## Prerequisites

- Node.js 18+ (for running Vite)
- npm or yarn
- Backend server running on port 8001 (or configured port)

## Quick Start

```bash
# Install dependencies
npm install

# Start development server
npm run dev

# Build for production
npm run build

# Preview production build
npm run preview
```

## Development Workflow

### 1. Start Backend Server

```bash
cd ..  # Go to project root
uv run python main.py --debug-all --port 8001
```

### 2. Start Frontend Dev Server

```bash
npm run dev
```

This starts Vite dev server on `http://localhost:5173` with:
- **Hot Module Replacement (HMR)**: Instant updates on file changes
- **API Proxy**: Proxies `/api/*` requests to backend on port 8001
- **WebSocket Proxy**: Proxies `/ws/*` connections to backend

### 3. Development

Edit files in `src/` and see changes instantly in browser!

## Project Structure

```
src/
├── main.js                 # App entry point
├── App.vue                 # Root component
├── router/                 # Vue Router configuration
│   └── index.js
├── stores/                 # Pinia stores (state management)
│   ├── session.js          # Session state + operations
│   ├── project.js          # Project state + operations
│   ├── message.js          # Messages + tool calls
│   ├── websocket.js        # WebSocket connections
│   └── ui.js               # UI state (sidebar, modals, etc.)
├── composables/            # Reusable composition functions
│   └── useWebSocket.js
├── components/             # Vue components
│   ├── layout/             # Layout components
│   ├── project/            # Project management
│   ├── session/            # Session management
│   ├── messages/           # Message display
│   ├── tools/              # Tool call system
│   └── legion/             # Legion multi-agent features
├── utils/                  # Utility functions
│   └── api.js              # API client
└── assets/                 # Static assets
    └── styles.css
```

## State Management with Pinia

All application state is managed through Pinia stores:

### Session Store

```javascript
import { useSessionStore } from '@/stores/session'

const sessionStore = useSessionStore()

// Access state
const currentSession = sessionStore.currentSession
const orderedSessions = sessionStore.orderedSessions

// Perform actions
await sessionStore.fetchSessions()
await sessionStore.createSession(projectId, formData)
await sessionStore.selectSession(sessionId)
```

### Project Store

```javascript
import { useProjectStore } from '@/stores/project'

const projectStore = useProjectStore()

// Access state
const projects = projectStore.orderedProjects

// Perform actions
await projectStore.fetchProjects()
await projectStore.createProject(name, workingDirectory)
await projectStore.deleteProject(projectId)
```

### WebSocket Store

```javascript
import { useWebSocketStore } from '@/stores/websocket'

const wsStore = useWebSocketStore()

// Connect to WebSockets
wsStore.connectUI()
wsStore.connectSession(sessionId)

// Send messages
wsStore.sendMessage('Hello Claude')
wsStore.sendPermissionResponse(requestId, 'allow')
```

### Message Store

```javascript
import { useMessageStore } from '@/stores/message'

const messageStore = useMessageStore()

// Access messages
const messages = messageStore.currentMessages
const toolCalls = messageStore.currentToolCalls

// Load messages
await messageStore.loadMessages(sessionId)
```

### UI Store

```javascript
import { useUIStore } from '@/stores/ui'

const uiStore = useUIStore()

// Control UI state
uiStore.toggleSidebar()
uiStore.showModal('create-project')
uiStore.setAutoScroll(true)
```

## Component Development

### Creating a New Component

```vue
<template>
  <div class="my-component">
    <h2>{{ title }}</h2>
    <button @click="handleClick">Click me</button>
  </div>
</template>

<script setup>
import { ref } from 'vue'

// Props
const props = defineProps({
  title: String
})

// State
const count = ref(0)

// Methods
function handleClick() {
  count.value++
}
</script>

<style scoped>
.my-component {
  padding: 1rem;
}
</style>
```

### Using Stores in Components

```vue
<script setup>
import { computed } from 'vue'
import { useSessionStore } from '@/stores/session'
import { storeToRefs } from 'pinia'

const sessionStore = useSessionStore()

// Use storeToRefs to keep reactivity
const { currentSession, orderedSessions } = storeToRefs(sessionStore)

// Or use computed for derived data
const sessionCount = computed(() => sessionStore.orderedSessions.length)

// Call actions directly
async function loadData() {
  await sessionStore.fetchSessions()
}
</script>
```

## API Client Usage

```javascript
import { api } from '@/utils/api'

// GET request
const data = await api.get('/api/sessions')

// POST request
const session = await api.post('/api/sessions', {
  project_id: projectId,
  name: 'My Session'
})

// PUT request
await api.put(`/api/sessions/${sessionId}`, {
  name: 'Updated Name'
})

// DELETE request
await api.delete(`/api/sessions/${sessionId}`)

// Error handling
try {
  await api.get('/api/sessions')
} catch (error) {
  console.error('API error:', error.message)
  // error.status contains HTTP status code
  // error.data contains error response data
}
```

## Environment Variables

Create a `.env` file in the `frontend/` directory:

```bash
# Backend server configuration
VITE_BACKEND_HOST=0.0.0.0
VITE_BACKEND_PORT=8001
```

**Note**: Environment variables must be prefixed with `VITE_` to be accessible in the app.

Access in code:

```javascript
const backendHost = import.meta.env.VITE_BACKEND_HOST
const backendPort = import.meta.env.VITE_BACKEND_PORT
```

## Building for Production

```bash
# Build optimized production bundle
npm run build
```

Output is in `dist/` directory:
- `dist/index.html` - Entry point
- `dist/assets/` - Bundled JS/CSS

### Serve Production Build

Update FastAPI to serve the `dist/` directory:

```python
# src/web_server.py
from fastapi.staticfiles import StaticFiles

app.mount("/static", StaticFiles(directory="frontend/dist"), name="static")

@app.get("/")
async def serve_index():
    return FileResponse("frontend/dist/index.html")
```

## Debugging

### Vue DevTools

Install Vue DevTools browser extension:
- **Chrome**: https://chrome.google.com/webstore (search "Vue DevTools")
- **Firefox**: https://addons.mozilla.org/firefox (search "Vue DevTools")

Features:
- Inspect component tree
- View Pinia store state
- Track events and route changes
- Performance profiling

### Console Logging

All stores include console logging:

```javascript
// Session store logs
console.log(`Created session ${sessionId}`)
console.log(`Selected session ${sessionId}`)

// WebSocket store logs
console.log('UI WebSocket connected')
console.log('Session WebSocket closed')
```

Filter logs by category in browser DevTools.

### Common Issues

#### 1. Backend Connection Failed

**Symptom**: "Connection Indicator" shows "Disconnected"

**Solution**:
- Verify backend is running on port 8001
- Check `.env` file has correct `VITE_BACKEND_PORT`
- Check Vite proxy configuration in `vite.config.js`

#### 2. WebSocket Connection Failed

**Symptom**: Messages not streaming, UI not updating

**Solution**:
- Check backend WebSocket endpoints are accessible
- Verify WebSocket proxy in `vite.config.js`
- Check browser console for WebSocket errors

#### 3. Hot Module Replacement Not Working

**Symptom**: Changes don't appear until full page refresh

**Solution**:
- Restart Vite dev server: `npm run dev`
- Check for syntax errors in Vue files
- Clear browser cache

#### 4. Pinia Store Not Reactive

**Symptom**: UI doesn't update when store changes

**Solution**:
- Use `storeToRefs()` when destructuring store refs
- Trigger reactivity on Map/Set updates: `map.value = new Map(map.value)`
- Use computed properties for derived data

## Code Style

### Naming Conventions

- **camelCase**: Variables, functions, computed properties
  ```javascript
  const sessionId = 'abc123'
  function fetchSessions() {}
  const currentSession = computed(() => ...)
  ```

- **PascalCase**: Component names
  ```vue
  <!-- SessionList.vue -->
  <script setup>
  import SessionItem from './SessionItem.vue'
  </script>
  ```

- **kebab-case**: Component file names, CSS classes
  ```
  components/session-list.vue
  class="session-item"
  ```

### Component Structure

Always use this order in `.vue` files:

```vue
<template>
  <!-- HTML template -->
</template>

<script setup>
// Imports
import { ref, computed } from 'vue'
import { useSessionStore } from '@/stores/session'

// Props
const props = defineProps({ ... })

// Emits
const emit = defineEmits(['update'])

// Composables/Stores
const sessionStore = useSessionStore()

// State
const count = ref(0)

// Computed
const doubled = computed(() => count.value * 2)

// Methods
function increment() {
  count.value++
}

// Lifecycle
onMounted(() => { ... })
</script>

<style scoped>
/* Component-specific styles */
</style>
```

## Performance Tips

### 1. Use `v-once` for Static Content

```vue
<div v-once>
  <h1>This content never changes</h1>
</div>
```

### 2. Use `v-memo` for Expensive Lists

```vue
<div v-for="item in list" :key="item.id" v-memo="[item.id, item.name]">
  <!-- Only re-renders if id or name changes -->
</div>
```

### 3. Lazy Load Components

```javascript
// router/index.js
const TimelineView = () => import('../components/legion/TimelineView.vue')
```

### 4. Optimize Computed Properties

```javascript
// ❌ BAD - Recomputes on every access
const filteredItems = () => items.value.filter(...)

// ✅ GOOD - Cached until dependencies change
const filteredItems = computed(() => items.value.filter(...))
```

## Testing (Future)

Placeholder for testing setup:

```bash
# Run unit tests (not yet implemented)
npm run test

# Run tests in watch mode
npm run test:watch

# Coverage report
npm run test:coverage
```

## Resources

- **Vue 3 Guide**: https://vuejs.org/guide/
- **Pinia Docs**: https://pinia.vuejs.org/
- **Vite Guide**: https://vitejs.dev/guide/
- **Vue Router**: https://router.vuejs.org/
- **Vue DevTools**: https://devtools.vuejs.org/

## Migration Status

- ✅ Phase 1: Setup + Infrastructure (COMPLETE)
- ⏳ Phase 2: Core UI Components (IN PROGRESS)
- ⏳ Phase 3: Messages + Tool Handlers
- ⏳ Phase 4: Legion Features
- ⏳ Phase 5: Testing + Polish
- ⏳ Phase 6: Cutover

See [MIGRATION_PLAN.md](./MIGRATION_PLAN.md) for full migration details.
