import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { makeMessage } from '@/test-utils/factories'

const apiMock = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  delete: vi.fn(),
  patch: vi.fn()
}))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn() }))

beforeEach(() => {
  setActivePinia(createPinia())
  Object.values(apiMock).forEach(fn => fn.mockReset())
})

function makeNotification(overrides = {}) {
  const { metadata: metadataOverrides, ...rest } = overrides
  return makeMessage({
    type: 'system',
    id: 'msg-1',
    content: 'alpha needs your input',
    ...rest,
    metadata: {
      subtype: 'agent_notification',
      notification_type: 'agent_needs_input',
      message: 'alpha needs your input',
      title: null,
      label: 'alpha',
      ...metadataOverrides
    }
  })
}

describe('agentNotificationsForSession (#1676)', () => {
  it('returns notifications tagged with metadata.subtype === agent_notification', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()

    store.addMessage('sess-1', makeNotification())

    const results = store.agentNotificationsForSession('sess-1')
    expect(results.length).toBe(1)
    expect(results[0]).toMatchObject({
      id: 'msg-1',
      notificationType: 'agent_needs_input',
      label: 'alpha',
      message: 'alpha needs your input'
    })
  })

  it('ignores unrelated system messages', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()

    store.addMessage('sess-1', makeMessage({
      type: 'system',
      id: 'msg-2',
      metadata: { subtype: 'hook_started' }
    }))

    expect(store.agentNotificationsForSession('sess-1')).toEqual([])
  })

  it('supports multiple concurrent notifications', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()

    store.addMessage('sess-1', makeNotification({ id: 'msg-1', metadata: { label: 'alpha' } }))
    store.addMessage('sess-1', makeNotification({
      id: 'msg-2',
      content: 'beta finished',
      metadata: {
        notification_type: 'agent_completed',
        message: 'beta finished',
        label: 'beta'
      }
    }))

    const results = store.agentNotificationsForSession('sess-1')
    expect(results.length).toBe(2)
    expect(results.map(r => r.label)).toEqual(['alpha', 'beta'])
  })

  it('dismissAgentNotification removes a notification from the active list', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()

    store.addMessage('sess-1', makeNotification({ id: 'msg-1' }))
    store.addMessage('sess-1', makeNotification({
      id: 'msg-2',
      metadata: { label: 'beta', message: 'beta finished', notification_type: 'agent_completed' }
    }))

    store.dismissAgentNotification('sess-1', 'msg-1')

    const results = store.agentNotificationsForSession('sess-1')
    expect(results.length).toBe(1)
    expect(results[0].id).toBe('msg-2')
  })

  it('dismissal is scoped per session', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()

    store.addMessage('sess-1', makeNotification({ id: 'msg-1' }))
    store.addMessage('sess-2', makeNotification({ id: 'msg-1' }))

    store.dismissAgentNotification('sess-1', 'msg-1')

    expect(store.agentNotificationsForSession('sess-1')).toEqual([])
    expect(store.agentNotificationsForSession('sess-2').length).toBe(1)
  })

  it('returns an empty array for a session with no messages', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()

    expect(store.agentNotificationsForSession('unknown-session')).toEqual([])
  })

  it('derives id from metadata.uuid when no top-level message id is present (real backend shape)', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()

    const msg = makeNotification({ metadata: { uuid: 'hook-uuid-1' } })
    delete msg.id
    store.addMessage('sess-1', msg)

    const results = store.agentNotificationsForSession('sess-1')
    expect(results[0].id).toBe('hook-uuid-1')

    store.dismissAgentNotification('sess-1', 'hook-uuid-1')
    expect(store.agentNotificationsForSession('sess-1')).toEqual([])
  })

  it('collapses duplicate hook_started/hook_response phases of the same notification', async () => {
    const { useMessageStore } = await import('@/stores/message')
    const store = useMessageStore()

    const started = makeNotification({ metadata: { uuid: 'uuid-started' } })
    const responded = makeNotification({ metadata: { uuid: 'uuid-response' } })
    delete started.id
    delete responded.id
    store.addMessage('sess-1', started)
    store.addMessage('sess-1', responded)

    const results = store.agentNotificationsForSession('sess-1')
    expect(results.length).toBe(1)
  })
})
