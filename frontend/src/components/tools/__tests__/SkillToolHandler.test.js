import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { screen } from '@testing-library/vue'
import { renderWithStores } from '@/test-utils/render'
import { makeMessage } from '@/test-utils/factories'
import SkillToolHandler from '@/components/tools/SkillToolHandler.vue'

const apiMock = vi.hoisted(() => ({
  get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn()
}))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn() }))

const SESSION_ID = 'sess-1'

beforeEach(() => {
  setActivePinia(createPinia())
})

function baseToolCall() {
  return {
    id: 'use-skill-1',
    name: 'Skill',
    input: { command: 'my-skill' },
    status: 'completed',
    result: { error: false, content: 'ok' }
  }
}

describe('SkillToolHandler', () => {
  it('surfaces "Base directory for this skill:" content in the Skill Content section', async () => {
    const { pinia, wrapper } = renderWithStores(SkillToolHandler, {
      props: { toolCall: baseToolCall() }
    })

    const { useSessionStore } = await import('@/stores/session')
    const { useMessageStore } = await import('@/stores/message')
    useSessionStore(pinia).currentSessionId = SESSION_ID
    const messageStore = useMessageStore(pinia)

    messageStore.messagesBySession.set(SESSION_ID, [
      makeMessage({
        type: 'user',
        content: '<command-message>my-skill skill is running</command-message>'
      }),
      makeMessage({
        type: 'user',
        metadata: { has_tool_results: true, tool_results: [{ tool_use_id: 'use-skill-1' }] },
        content: 'Tool results: 1 results'
      }),
      makeMessage({
        type: 'user',
        content: 'Base directory for this skill: /some/path\nSkill instructions here.'
      })
    ])
    messageStore.messagesBySession = new Map(messageStore.messagesBySession)

    await wrapper.vm.$nextTick()
    await new Promise(r => setTimeout(r, 10))

    expect(screen.getByText('Skill Content')).toBeTruthy()
    await wrapper.find('.skill-content-header').trigger('click')

    expect(screen.getByText(/Skill instructions here\./)).toBeTruthy()
  })

  it('surfaces a re-invocation notice as skill content instead of dropping it (#1724)', async () => {
    const { pinia, wrapper } = renderWithStores(SkillToolHandler, {
      props: { toolCall: baseToolCall() }
    })

    const { useSessionStore } = await import('@/stores/session')
    const { useMessageStore } = await import('@/stores/message')
    useSessionStore(pinia).currentSessionId = SESSION_ID
    const messageStore = useMessageStore(pinia)

    messageStore.messagesBySession.set(SESSION_ID, [
      makeMessage({
        type: 'user',
        metadata: { has_tool_results: true, tool_results: [{ tool_use_id: 'use-skill-1' }] },
        content: 'Tool results: 1 results'
      }),
      makeMessage({
        type: 'user',
        content: '(Re-invocation of /my-skill — the skill instructions were previously loaded; the arguments or dynamic output below are new.)\nnew argument value'
      })
    ])
    messageStore.messagesBySession = new Map(messageStore.messagesBySession)

    await wrapper.vm.$nextTick()
    await new Promise(r => setTimeout(r, 10))

    expect(screen.getByText('Skill Content')).toBeTruthy()
    await wrapper.find('.skill-content-header').trigger('click')

    expect(screen.getByText(/Re-invocation of \/my-skill/)).toBeTruthy()
    expect(screen.getByText(/new argument value/)).toBeTruthy()
  })

  it('shows no Skill Content section when no follow-up messages exist', async () => {
    const { pinia } = renderWithStores(SkillToolHandler, {
      props: { toolCall: baseToolCall() }
    })

    const { useSessionStore } = await import('@/stores/session')
    useSessionStore(pinia).currentSessionId = SESSION_ID

    expect(screen.queryByText('Skill Content')).toBeNull()
  })
})
