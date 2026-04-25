# Frontend Testing Guide

## Overview

The frontend uses [Vitest](https://vitest.dev/) with [Vue Test Utils](https://test-utils.vuejs.org/) and
[@testing-library/vue](https://testing-library.com/docs/vue-testing-library/intro/) for unit and component testing.

## Running Tests

```bash
cd frontend
npm test            # single run (CI)
npm run test:watch  # watch mode (development)
npm run test:coverage  # with coverage report
```

All 40+ tests should pass in under 10 seconds.

## Test Structure

```
frontend/src/
├── stores/__tests__/          # 24 Pinia store tests (6 files)
│   ├── session.test.js        # Session CRUD, currentInput getter/setter
│   ├── project.test.js        # Project fetch, reorder, status segments
│   ├── message.test.js        # Messages, tool lifecycle, permissions
│   ├── polling.test.js        # HTTP polling actions (send, interrupt)
│   ├── legion.test.js         # Multi-agent: sendComm, createMinion, haltAll
│   └── ui.test.js             # Sidebar toggle, modals, responsive state
│
└── components/*/__tests__/    # 16 component tests (11 files)
    ├── common/
    │   ├── AttachmentChip.test.js
    │   └── AuthPrompt.test.js
    ├── layout/
    │   ├── AgentChip.test.js
    │   ├── ConnectionIndicator.test.js
    │   └── ProjectPill.test.js
    ├── messages/
    │   ├── InputArea.test.js
    │   ├── MessageItem.test.js
    │   └── MessageList.test.js
    ├── messages/tools/
    │   ├── ActivityTimeline.test.js
    │   └── PermissionPrompt.test.js
    └── tools/
        └── BashToolHandler.test.js
```

## Test Utilities

### `src/test-utils/factories.js`

Factory functions that produce plain objects matching store shapes:

```js
import { makeSession, makeProject, makeMessage, makeToolCall, makeMinion, makeComm } from '@/test-utils/factories'

const session = makeSession({ session_id: 'sess-1', name: 'My Session' })
const message = makeMessage({ content: 'Hello' })
```

### `src/test-utils/render.js`

`renderWithStores(component, options)` — mounts a Vue component with a fresh Pinia and
in-memory router. Returns the pinia instance for store access.

```js
import { renderWithStores } from '@/test-utils/render'

const { pinia } = renderWithStores(MyComponent, {
  props: { foo: 'bar' },
  stubs: { ChildComponent: true }
})

// Access stores bound to the component's pinia
const { useMyStore } = await import('@/stores/myStore')
const store = useMyStore(pinia)
store.someValue = 'test'
```

## Writing Store Tests

Store tests use dynamic imports to bind the store to the active Pinia:

```js
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

const apiMock = vi.hoisted(() => ({
  get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn()
}))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn() }))

beforeEach(() => {
  setActivePinia(createPinia())
  Object.values(apiMock).forEach(fn => fn.mockReset())
})

it('does something', async () => {
  const { useMyStore } = await import('@/stores/myStore')
  const store = useMyStore()
  // ...
})
```

## Writing Component Tests

Component tests use `renderWithStores` which handles Pinia + router setup and DOM cleanup:

```js
import { describe, it, expect, vi } from 'vitest'
import { nextTick } from 'vue'
import { screen, fireEvent } from '@testing-library/vue'
import { renderWithStores } from '@/test-utils/render'
import MyComponent from '@/components/MyComponent.vue'

vi.mock('@/utils/api', () => ({
  api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn() },
  getAuthToken: vi.fn()
}))

it('renders expected content', async () => {
  const { pinia } = renderWithStores(MyComponent, {
    props: { title: 'Hello' },
    stubs: { ComplexChild: true }
  })

  // Set store state and wait for re-render
  const { useSessionStore } = await import('@/stores/session')
  const store = useSessionStore(pinia)
  store.currentSessionId = 'sess-1'
  await nextTick()

  expect(screen.getByText('Hello')).toBeTruthy()
})
```

## Mocking Patterns

### API mock (required in almost every test file)

```js
const apiMock = vi.hoisted(() => ({
  get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn()
}))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn() }))
```

### Composable mock

```js
vi.mock('@/composables/useTTSReadAloud', () => ({
  useTTSReadAloud: () => ({
    isEnabled: { value: false },
    isPlaying: { value: false },
    speak: vi.fn(),
    stop: vi.fn()
  })
}))
```

### Component stub (inline template)

```js
stubs: {
  TimelineNode: { template: '<div data-testid="node" />', props: ['tool'] }
}
```

## Configuration

- **`vitest.config.js`**: Standalone Vitest config (uses `vitest/config`, not `vite/config`)
- **`vitest.setup.js`**: Global beforeEach/afterEach (clear storage, suppress console noise)
- **Environment**: jsdom
- **Globals**: enabled (no need to import `describe`/`it`/`expect`)
- **Alias**: `@` → `src/`
