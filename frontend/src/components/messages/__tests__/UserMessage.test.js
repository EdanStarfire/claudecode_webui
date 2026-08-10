import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { screen } from '@testing-library/vue'
import { renderWithStores } from '@/test-utils/render'
import { useSessionStore } from '@/stores/session'
import UserMessage from '@/components/messages/UserMessage.vue'

const apiMock = vi.hoisted(() => ({
  get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn()
}))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn().mockReturnValue(null) }))

const MarkdownViewStub = {
  props: ['content', 'streaming', 'caret', 'selfAgentId'],
  template: '<div class="markdown-stub" />',
  methods: { addEventListener() {}, removeEventListener() {} },
}

beforeEach(() => {
  setActivePinia(createPinia())
})

async function seedSessions(wrapper, sessions) {
  const sessionStore = useSessionStore()
  for (const s of sessions) sessionStore.sessions.set(s.session_id, s)
  await wrapper.vm.$nextTick()
}

describe('UserMessage', () => {
  it('renders the comm sender name as a link when from_minion_id resolves to a live session', async () => {
    const message = {
      type: 'user',
      content: 'status update',
      timestamp: 1700000000,
      metadata: {
        comm: { from_name: 'reporter', from_display_name: 'Reporter', from_minion_id: 'sender-1', comm_type: 'report' },
      },
    }

    const { wrapper } = renderWithStores(UserMessage, { props: { message }, stubs: { MarkdownView: MarkdownViewStub } })
    await seedSessions(wrapper, [
      { session_id: 'sender-1', project_id: 'proj-1', slug: 'reporter', name: 'Reporter' },
    ])

    const link = screen.getByText('Reporter')
    expect(link.tagName).toBe('A')
    expect(link.getAttribute('href')).toBe('#/session/sender-1')
  })

  it('renders plain text when from_minion_id is missing (legacy comm)', () => {
    const message = {
      type: 'user',
      content: 'status update',
      timestamp: 1700000000,
      metadata: {
        comm: { from_name: 'reporter', from_display_name: 'Reporter', comm_type: 'report' },
      },
    }

    renderWithStores(UserMessage, { props: { message }, stubs: { MarkdownView: MarkdownViewStub } })

    const text = screen.getByText('Reporter')
    expect(text.tagName).not.toBe('A')
  })

  it('renders plain text when the sender session no longer exists (deleted agent)', async () => {
    const message = {
      type: 'user',
      content: 'status update',
      timestamp: 1700000000,
      metadata: {
        comm: { from_name: 'reporter', from_display_name: 'Reporter', from_minion_id: 'ghost-session', comm_type: 'report' },
      },
    }

    const { wrapper } = renderWithStores(UserMessage, { props: { message }, stubs: { MarkdownView: MarkdownViewStub } })
    await seedSessions(wrapper, [
      { session_id: 'sender-1', project_id: 'proj-1', slug: 'reporter', name: 'Reporter' },
    ])

    const text = screen.getByText('Reporter')
    expect(text.tagName).not.toBe('A')
  })

  it('renders "user" as plain text for a non-comm message', () => {
    const message = {
      type: 'user',
      content: 'hello',
      timestamp: 1700000000,
    }

    renderWithStores(UserMessage, { props: { message }, stubs: { MarkdownView: MarkdownViewStub } })

    const text = screen.getByText('user')
    expect(text.tagName).not.toBe('A')
  })
})
