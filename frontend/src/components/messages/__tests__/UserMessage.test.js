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

  it('renders a Slack monitor envelope as a collapsed CommCard without a jump link (3b-i)', () => {
    const message = {
      type: 'user',
      content: '📡 Slack monitor update — 2 message(s) ...',
      timestamp: 1700000000,
      metadata: {
        comm: {
          from_name: 'slack-monitor', from_display_name: 'Slack monitor', comm_type: 'info',
          summary: 'Slack monitor: 2 messages', content: 'Slack monitor: 2 messages',
        },
        relay_inbound: { kind: 'monitor', count: 2 },
      },
    }

    const { wrapper } = renderWithStores(UserMessage, { props: { message }, stubs: { MarkdownView: MarkdownViewStub } })

    const name = screen.getByText('Slack monitor')
    expect(name.tagName).not.toBe('A')
    expect(wrapper.text()).toContain('Slack monitor: 2 messages')
    expect(wrapper.text()).not.toContain('📡')  // collapsed: the fenced body is not shown until expanded
  })

  it('expands a Slack monitor envelope into a verbatim code block (3b-i)', async () => {
    const body = '📡 update\n[UNTRUSTED EXTERNAL CONTENT id=ab]\n[\n {"text": "```js\\nx```"}\n]\n[END UNTRUSTED EXTERNAL CONTENT id=ab]'
    const message = {
      type: 'user', content: body, timestamp: 1700000000,
      metadata: {
        comm: { from_display_name: 'Slack monitor', comm_type: 'info', summary: 's', content: 's' },
        relay_inbound: { kind: 'monitor' },
      },
    }
    const seen = []
    const Stub = { props: ['content'], setup(p) { seen.push(p) }, template: '<div class="markdown-stub" />' }
    const { wrapper } = renderWithStores(UserMessage, { props: { message }, stubs: { MarkdownView: Stub, CommCard: {
      props: ['bodyContent'], template: '<div class="card-stub">{{ bodyContent }}</div>' } } })
    const shown = wrapper.find('.card-stub').text()
    expect(shown.startsWith('````text')).toBe(true)
    expect(shown).toContain('[UNTRUSTED EXTERNAL CONTENT id=ab]')
    // the fence is longer than any backtick run inside the content, so it cannot be closed early
    expect(shown.split('\n')[0]).toBe('````text')
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

  it('issue #1843 follow-up: falls back to message.content for comms persisted before this feature (metadata.comm has no content key at all)', async () => {
    const message = {
      id: 'msg-legacy-1',
      type: 'user',
      content: '**📋 Task from Minion #Overseer:** Old comm\n\nThis is old delivered text.\n\n---\nAlways send messages to Minion #Overseer using the `send_comm` tool.',
      timestamp: 1700000000,
      metadata: {
        // No summary/content/trailing_instruction keys - simulates data stored before #1843.
        comm: { from_name: 'overseer', from_display_name: 'Overseer', comm_type: 'task' },
      },
    }

    const { wrapper } = renderWithStores(UserMessage, { props: { message }, stubs: { MarkdownView: MarkdownViewStub } })

    // Card must NOT be permanently disabled - the legacy record's full text is recoverable
    // from message.content even though metadata.comm.content was never populated for it.
    const card = wrapper.find('.comm-card')
    expect(card.classes()).not.toContain('no-body')
    expect(wrapper.find('.comm-summary-row').classes()).not.toContain('is-empty-state')

    await wrapper.find('.comm-card-header').trigger('click')
    expect(wrapper.find('.comm-card-body').exists()).toBe(true)
  })

  it('issue #1843 follow-up: a new comm with genuinely empty content (summary-only, by design) still disables expand', async () => {
    const message = {
      id: 'msg-new-1',
      type: 'user',
      content: '**📋 Task from Minion #Overseer:** Just the summary\n\n\n\n---\nAlways send messages to Minion #Overseer using the `send_comm` tool.',
      timestamp: 1700000000,
      metadata: {
        comm: {
          from_name: 'overseer', from_display_name: 'Overseer', comm_type: 'task',
          summary: 'Just the summary', content: '', trailing_instruction: 'Always send messages to Minion #Overseer using the `send_comm` tool.',
        },
      },
    }

    const { wrapper } = renderWithStores(UserMessage, { props: { message }, stubs: { MarkdownView: MarkdownViewStub } })

    const card = wrapper.find('.comm-card')
    expect(card.classes()).toContain('no-body')

    await wrapper.find('.comm-card-header').trigger('click')
    expect(wrapper.find('.comm-card-body').exists()).toBe(false)
  })
})
