import { describe, it, expect, beforeEach, vi } from 'vitest'
import { screen } from '@testing-library/vue'
import { renderWithStores } from '@/test-utils/render'
import SendMessageToolHandler from '@/components/tools/SendMessageToolHandler.vue'

const apiMock = vi.hoisted(() => ({
  get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn()
}))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn() }))

// Issue #1746 (stage: subagents) follow-up (user feedback): SendMessage direction genuinely
// varies — main->agent, agent->main, and agent->agent all use this same tool — so the sender
// side must be resolved from the actual calling context (parent_tool_use_id), not hardcoded
// to "always self".
describe('SendMessageToolHandler — direction-aware sender/recipient gradient', () => {
  beforeEach(() => {
    apiMock.get.mockReset()
  })

  it('main -> agent (top-level call, no parent_tool_use_id): no sender label, main is the gradient start', () => {
    const { wrapper } = renderWithStores(SendMessageToolHandler, {
      props: {
        toolCall: {
          id: 'tu-1', name: 'SendMessage',
          input: { to: 'PoetOne', message: 'Continue with the next verse.' },
          timestamp: 100, status: 'completed',
        }
      }
    })

    expect(wrapper.find('.outbound-comm-sender').exists()).toBe(false)
    expect(screen.getByText(/→ PoetOne/)).toBeTruthy()
    const bubble = wrapper.find('.outbound-comm-bubble')
    expect(bubble.attributes('style')).toMatch(/linear-gradient/)
  })

  it('agent -> main (nested, parent_tool_use_id resolves to a task): shows the agent as sender', async () => {
    const { pinia, wrapper } = renderWithStores(SendMessageToolHandler, {
      props: {
        toolCall: {
          id: 'tu-2', name: 'SendMessage',
          input: { to: 'main', message: 'Here is my verse for you.' },
          timestamp: 100, status: 'completed',
          parent_tool_use_id: 'toolu_root',
        }
      }
    })

    const { useMessageStore } = await import('@/stores/message')
    const messageStore = useMessageStore(pinia)
    messageStore.applyTaskLifecycleFrame('sess-1', 'task_started', {
      task_id: 'task-poetone', tool_use_id: 'toolu_root', description: 'PoetOne agent',
    }, 50)
    await wrapper.vm.$nextTick()

    expect(screen.getByText('PoetOne agent')).toBeTruthy()
    expect(screen.getByText(/→ main/)).toBeTruthy()
  })

  it('agent -> agent (nested, neither side is main): shows both agent names, neither uses the assistant wash', async () => {
    const { pinia, wrapper } = renderWithStores(SendMessageToolHandler, {
      props: {
        toolCall: {
          id: 'tu-3', name: 'SendMessage',
          input: { to: 'PoetTwo', message: 'Want to collaborate?' },
          timestamp: 100, status: 'completed',
          parent_tool_use_id: 'toolu_root',
        }
      }
    })

    const { useMessageStore } = await import('@/stores/message')
    const messageStore = useMessageStore(pinia)
    messageStore.applyTaskLifecycleFrame('sess-1', 'task_started', {
      task_id: 'task-poetone', tool_use_id: 'toolu_root', description: 'PoetOne agent',
    }, 50)
    await wrapper.vm.$nextTick()

    expect(screen.getByText('PoetOne agent')).toBeTruthy()
    expect(screen.getByText(/→ PoetTwo/)).toBeTruthy()
  })

  it('shows a delivered/failed result badge when the tool call has a result', () => {
    const { wrapper: delivered } = renderWithStores(SendMessageToolHandler, {
      props: {
        toolCall: {
          id: 'tu-4', name: 'SendMessage', input: { to: 'PoetOne', message: 'hi' },
          timestamp: 100, status: 'completed', result: { error: false, content: 'ok' },
        }
      }
    })
    expect(delivered.text()).toContain('Delivered')

    const { wrapper: failed } = renderWithStores(SendMessageToolHandler, {
      props: {
        toolCall: {
          id: 'tu-5', name: 'SendMessage', input: { to: 'PoetOne', message: 'hi' },
          timestamp: 100, status: 'completed', result: { error: true, content: 'no such agent' },
        }
      }
    })
    expect(failed.text()).toContain('Failed')
  })
})
