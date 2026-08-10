import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { screen } from '@testing-library/vue'
import { renderWithStores } from '@/test-utils/render'
import { useSessionStore } from '@/stores/session'
import SendCommToolHandler from '@/components/tools/SendCommToolHandler.vue'

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

describe('SendCommToolHandler', () => {
  it('renders the recipient name as a link when it resolves to a live session in the project', async () => {
    const toolCall = {
      id: 'use-1',
      session_id: 'sender-1',
      name: 'send_comm',
      input: { to_minion_name: 'database-optimizer', content: 'hello', comm_type: 'info' },
      status: 'completed',
    }

    const { wrapper } = renderWithStores(SendCommToolHandler, { props: { toolCall }, stubs: { MarkdownView: MarkdownViewStub } })
    await seedSessions(wrapper, [
      { session_id: 'sender-1', project_id: 'proj-1', slug: 'sender', name: 'Sender' },
      { session_id: 'recipient-1', project_id: 'proj-1', slug: 'database-optimizer', name: 'Database Optimizer' },
    ])

    const link = screen.getByText('database-optimizer')
    expect(link.tagName).toBe('A')
    expect(link.getAttribute('href')).toBe('#/session/recipient-1')
  })

  it('resolves by display-name fallback when slug does not match', async () => {
    const toolCall = {
      id: 'use-1',
      session_id: 'sender-1',
      name: 'send_comm',
      input: { to_minion_name: 'Database Optimizer', content: 'hello', comm_type: 'info' },
      status: 'completed',
    }

    const { wrapper } = renderWithStores(SendCommToolHandler, { props: { toolCall }, stubs: { MarkdownView: MarkdownViewStub } })
    await seedSessions(wrapper, [
      { session_id: 'sender-1', project_id: 'proj-1', slug: 'sender', name: 'Sender' },
      { session_id: 'recipient-1', project_id: 'proj-1', slug: 'db-opt', name: 'Database Optimizer' },
    ])

    const link = screen.getByText('Database Optimizer')
    expect(link.tagName).toBe('A')
    expect(link.getAttribute('href')).toBe('#/session/recipient-1')
  })

  it('renders plain text when the recipient does not resolve (deleted/unknown agent)', async () => {
    const toolCall = {
      id: 'use-1',
      session_id: 'sender-1',
      name: 'send_comm',
      input: { to_minion_name: 'ghost-agent', content: 'hello', comm_type: 'info' },
      status: 'completed',
    }

    const { wrapper } = renderWithStores(SendCommToolHandler, { props: { toolCall }, stubs: { MarkdownView: MarkdownViewStub } })
    await seedSessions(wrapper, [
      { session_id: 'sender-1', project_id: 'proj-1', slug: 'sender', name: 'Sender' },
    ])

    const recipient = wrapper.find('.outbound-comm-recipient')
    expect(recipient.text()).toContain('ghost-agent')
    expect(recipient.find('a').exists()).toBe(false)
  })

  it('does not link a recipient with a matching name outside the current project', async () => {
    const toolCall = {
      id: 'use-1',
      session_id: 'sender-1',
      name: 'send_comm',
      input: { to_minion_name: 'database-optimizer', content: 'hello', comm_type: 'info' },
      status: 'completed',
    }

    const { wrapper } = renderWithStores(SendCommToolHandler, { props: { toolCall }, stubs: { MarkdownView: MarkdownViewStub } })
    await seedSessions(wrapper, [
      { session_id: 'sender-1', project_id: 'proj-1', slug: 'sender', name: 'Sender' },
      { session_id: 'recipient-1', project_id: 'proj-2', slug: 'database-optimizer', name: 'Database Optimizer' },
    ])

    const recipient = wrapper.find('.outbound-comm-recipient')
    expect(recipient.text()).toContain('database-optimizer')
    expect(recipient.find('a').exists()).toBe(false)
  })
})
