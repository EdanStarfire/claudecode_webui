import { describe, it, expect, beforeEach, vi } from 'vitest'
import { nextTick } from 'vue'
import { setActivePinia, createPinia } from 'pinia'
import { screen } from '@testing-library/vue'
import userEvent from '@testing-library/user-event'
import { renderWithStores } from '@/test-utils/render'
import PermissionQueue from '@/components/messages/PermissionQueue.vue'

const apiMock = vi.hoisted(() => ({
  get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn()
}))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn() }))

const SESSION_ID = 'sess-1'

beforeEach(() => {
  setActivePinia(createPinia())
  Object.values(apiMock).forEach(fn => fn.mockReset())
  apiMock.post.mockResolvedValue({})
})

async function seedTwoOpenPermissions(pinia) {
  const { useMessageStore } = await import('@/stores/message')
  const { useSessionStore } = await import('@/stores/session')
  const messageStore = useMessageStore(pinia)
  const sessionStore = useSessionStore(pinia)
  sessionStore.currentSessionId = SESSION_ID

  messageStore.handleToolCall(SESSION_ID, {
    tool_use_id: 'use-main',
    name: 'Write',
    input: {},
    status: 'awaiting_permission',
    request_id: 'req-main',
  })
  messageStore.applyTaskLifecycleFrame(SESSION_ID, 'task_started', {
    task_id: 'task-1', tool_use_id: 'launch-1', description: 'Fix the failing test',
  }, 100)
  messageStore.handleToolCall(SESSION_ID, {
    tool_use_id: 'child-1',
    name: 'Bash',
    input: {},
    status: 'awaiting_permission',
    request_id: 'req-sub',
    parent_tool_use_id: 'launch-1',
  })

  return { messageStore, sessionStore }
}

async function seedAskUserQuestionAlongsideNormal(pinia) {
  const { useMessageStore } = await import('@/stores/message')
  const { useSessionStore } = await import('@/stores/session')
  const messageStore = useMessageStore(pinia)
  const sessionStore = useSessionStore(pinia)
  sessionStore.currentSessionId = SESSION_ID

  messageStore.handleToolCall(SESSION_ID, {
    tool_use_id: 'use-main',
    name: 'Write',
    input: {},
    status: 'awaiting_permission',
    request_id: 'req-main',
  })
  messageStore.handleToolCall(SESSION_ID, {
    tool_use_id: 'use-question',
    name: 'AskUserQuestion',
    input: { questions: [{ question: 'Which approach?', options: ['A', 'B'] }] },
    status: 'awaiting_permission',
    request_id: 'req-question',
  })

  return { messageStore, sessionStore }
}

describe('PermissionQueue', () => {
  it('renders one card per open permission', async () => {
    const { pinia } = renderWithStores(PermissionQueue, {
      props: { sessionId: SESSION_ID, bottomOffset: 0 }
    })
    await seedTwoOpenPermissions(pinia)
    await nextTick()

    expect(screen.getAllByText(/wants to use/i)).toHaveLength(2)
    expect(screen.getByText('Main session')).toBeTruthy()
    expect(screen.getByText('Fix the failing test')).toBeTruthy()
  })

  it('approve calls handlePermissionResponse and sendPermissionResponse with allow decision', async () => {
    const user = userEvent.setup()
    const { pinia } = renderWithStores(PermissionQueue, {
      props: { sessionId: SESSION_ID, bottomOffset: 0 }
    })
    const { messageStore } = await seedTwoOpenPermissions(pinia)
    await nextTick()

    const approveButtons = screen.getAllByRole('button', { name: /^approve$/i })
    await user.click(approveButtons[0])

    expect(apiMock.post).toHaveBeenCalledWith(
      expect.stringContaining('/permission/req-main'),
      expect.objectContaining({ decision: 'allow' })
    )
    // Same store function PermissionPrompt.vue uses — resolved tool call flips to executing.
    const tc = messageStore.toolCallsBySession.get(SESSION_ID).find(t => t.id === 'use-main')
    expect(tc.status).toBe('executing')
  })

  it('deny calls sendPermissionResponse with deny decision', async () => {
    const user = userEvent.setup()
    const { pinia } = renderWithStores(PermissionQueue, {
      props: { sessionId: SESSION_ID, bottomOffset: 0 }
    })
    await seedTwoOpenPermissions(pinia)
    await nextTick()

    const denyButtons = screen.getAllByRole('button', { name: /^deny$/i })
    await user.click(denyButtons[0])

    expect(apiMock.post).toHaveBeenCalledWith(
      expect.stringContaining('/permission/'),
      expect.objectContaining({ decision: 'deny' })
    )
  })

  it('resolving one permission leaves the other card and updates the count', async () => {
    const user = userEvent.setup()
    const { pinia } = renderWithStores(PermissionQueue, {
      props: { sessionId: SESSION_ID, bottomOffset: 0 }
    })
    await seedTwoOpenPermissions(pinia)
    await nextTick()

    expect(screen.getByText('2')).toBeTruthy()

    const approveButtons = screen.getAllByRole('button', { name: /^approve$/i })
    await user.click(approveButtons[0])
    await nextTick()

    expect(screen.getByText('1')).toBeTruthy()
    expect(screen.getByText('Fix the failing test')).toBeTruthy()
  })

  it('minimize collapses the card list but keeps the badge count; only the explicit button toggles it', async () => {
    const user = userEvent.setup()
    const { pinia } = renderWithStores(PermissionQueue, {
      props: { sessionId: SESSION_ID, bottomOffset: 0 }
    })
    const { messageStore } = await seedTwoOpenPermissions(pinia)
    await nextTick()

    expect(screen.getAllByText(/wants to use/i)).toHaveLength(2)

    await user.click(screen.getByRole('button', { name: /minimize permission queue/i }))
    await nextTick()

    expect(screen.queryAllByText(/wants to use/i)).toHaveLength(0)
    expect(screen.getByText('2')).toBeTruthy()

    // A new permission arriving while minimized must not force it back open.
    messageStore.handleToolCall(SESSION_ID, {
      tool_use_id: 'use-third',
      name: 'Read',
      input: {},
      status: 'awaiting_permission',
      request_id: 'req-third',
    })
    await nextTick()

    expect(screen.queryAllByText(/wants to use/i)).toHaveLength(0)
    expect(screen.getByText('3')).toBeTruthy()

    await user.click(screen.getByText('3'))
    await nextTick()

    expect(screen.getAllByText(/wants to use/i)).toHaveLength(3)
  })

  it('renders an AskUserQuestion card with the context directive and no Approve/Deny buttons', async () => {
    const { pinia } = renderWithStores(PermissionQueue, {
      props: { sessionId: SESSION_ID, bottomOffset: 0 }
    })
    await seedAskUserQuestionAlongsideNormal(pinia)
    await nextTick()

    expect(screen.getByText(/go to context to review questions/i)).toBeTruthy()
    // Only the non-AskUserQuestion card gets Approve/Deny.
    expect(screen.getAllByRole('button', { name: /^approve$/i })).toHaveLength(1)
    expect(screen.getAllByRole('button', { name: /^deny$/i })).toHaveLength(1)
    expect(screen.getAllByText(/view in context/i)).toHaveLength(2)
  })

  it('clicking View in context on an AskUserQuestion card scrolls to its tool anchor', async () => {
    const user = userEvent.setup()
    const { pinia } = renderWithStores(PermissionQueue, {
      props: { sessionId: SESSION_ID, bottomOffset: 0 }
    })
    await seedAskUserQuestionAlongsideNormal(pinia)
    await nextTick()

    const anchor = document.createElement('div')
    anchor.id = 'tool-anchor-use-question'
    anchor.scrollIntoView = vi.fn()
    document.body.appendChild(anchor)

    const viewLinks = screen.getAllByText(/view in context/i)
    await user.click(viewLinks[viewLinks.length - 1])

    expect(anchor.scrollIntoView).toHaveBeenCalled()
    document.body.removeChild(anchor)
  })
})
