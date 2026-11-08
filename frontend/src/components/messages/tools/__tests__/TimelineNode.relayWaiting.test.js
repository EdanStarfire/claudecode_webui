import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { screen } from '@testing-library/vue'
import { renderWithStores } from '@/test-utils/render'
import TimelineNode from '@/components/messages/tools/TimelineNode.vue'
import { useMessageStore } from '@/stores/message'

vi.mock('@/utils/api', () => ({
  api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn() },
  getAuthToken: vi.fn()
}))

const PROMPT = {
  kind: 'ask_send', tool: 'post', action: 'send', channel_id: 'C0ASK00001',
  channel_name: 'ask-ch', channel_label: '#ask-ch', policy: 'ask', text_preview: 'hello'
}

beforeEach(() => setActivePinia(createPinia()))

describe('TimelineNode relay approval label (issue #2353)', () => {
  it('shows "Waiting for your approval" on a relay card awaiting permission', () => {
    renderWithStores(TimelineNode, {
      props: {
        tool: {
          id: 'use-1', name: 'mcp__relay_slack__post', input: { channel: 'C0ASK00001', text: 'hello' },
          status: 'permission_required', relayPrompt: PROMPT
        }
      }
    })
    expect(screen.getByTestId('relay-waiting-label').textContent).toBe('Waiting for your approval')
  })

  it('does not show it for other tools awaiting permission, or once the relay call resolved', () => {
    renderWithStores(TimelineNode, {
      props: { tool: { id: 'u2', name: 'Edit', input: {}, status: 'permission_required' } }
    })
    expect(screen.queryByTestId('relay-waiting-label')).toBeNull()
    renderWithStores(TimelineNode, {
      props: {
        tool: {
          id: 'u3', name: 'mcp__relay_slack__post', input: {}, status: 'executing',
          relayPrompt: PROMPT
        }
      }
    })
    expect(screen.queryByTestId('relay-waiting-label')).toBeNull()
  })
})

describe('message store: relay prompt records (issue #2353)', () => {
  it('the hook-linked card turns awaiting_permission with its relay prompt', () => {
    const store = useMessageStore()
    store.applyRecord('s1', {
      type: 'tool_call', tool_use_id: 'use-1', name: 'mcp__relay_slack__post',
      input: { channel: 'C0ASK00001', text: 'hello' }, status: 'pending'
    }, 'live')
    store.applyRecord('s1', {
      type: 'tool_call', tool_use_id: 'use-1', name: 'mcp__relay_slack__post',
      input: { channel: 'C0ASK00001', text: 'hello' }, status: 'awaiting_permission',
      request_id: 'req-1', permission: { message: 'Allow?', suggestions: [], relay_prompt: PROMPT }
    }, 'live')
    const calls = store.toolCallsBySession.get('s1')
    expect(calls).toHaveLength(1)
    expect(calls[0].status).toBe('permission_required')
    expect(calls[0].relayPrompt.kind).toBe('ask_send')
  })

  it('a standalone synthetic card (no prior record) renders its prompt and resolves', () => {
    const store = useMessageStore()
    const id = 'relay-prompt-req-9'
    store.applyRecord('s1', {
      type: 'tool_call', tool_use_id: id, name: 'mcp__relay_slack__post',
      input: { channel: 'C0ASK00001', text: 'hello' }, status: 'awaiting_permission',
      request_id: 'req-9', permission: { message: 'Allow?', suggestions: [], relay_prompt: PROMPT }
    }, 'live')
    let calls = store.toolCallsBySession.get('s1')
    expect(calls).toHaveLength(1)
    expect(calls[0].id).toBe(id)
    expect(calls[0].status).toBe('permission_required')
    expect(calls[0].relayPrompt.channel_label).toBe('#ask-ch')

    store.applyRecord('s1', {
      type: 'tool_call', tool_use_id: id, name: 'mcp__relay_slack__post',
      input: { channel: 'C0ASK00001', text: 'hello' }, status: 'denied',
      request_id: 'req-9', permission_granted: false
    }, 'live')
    calls = store.toolCallsBySession.get('s1')
    expect(calls[0].status).not.toBe('permission_required')
    expect(calls[0].permissionDecision).toBe('deny')
  })
})
