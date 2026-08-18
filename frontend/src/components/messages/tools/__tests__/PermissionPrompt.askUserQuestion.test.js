import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { screen } from '@testing-library/vue'
import userEvent from '@testing-library/user-event'
import { renderWithStores } from '@/test-utils/render'
import PermissionPrompt from '@/components/messages/tools/PermissionPrompt.vue'

const apiMock = vi.hoisted(() => ({
  get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn()
}))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn() }))
vi.mock('@/composables/useNotifications', () => ({ notify: vi.fn() }))

const QUESTIONS = [{ question: 'Which colors?', header: 'Test', options: [{ label: 'Red' }, { label: 'Blue' }] }]

const makeAskUserQuestionTool = (overrides = {}) => ({
  id: 'use-askq-1',
  name: 'AskUserQuestion',
  input: { questions: QUESTIONS },
  status: 'permission_required',
  backendStatus: 'awaiting_permission',
  permissionRequestId: 'req-1',
  suggestions: [],
  isExpanded: true,
  answers: null,
  ...overrides
})

beforeEach(() => {
  setActivePinia(createPinia())
  Object.values(apiMock).forEach(fn => fn.mockReset())
  apiMock.post.mockResolvedValue({})
})

describe('PermissionPrompt — AskUserQuestion submit (#1774 live-session gap)', () => {
  it('submitting answers persists .answers immediately, and it survives a subsequent live tool_call event with no updated_input', async () => {
    const user = userEvent.setup()
    const { pinia } = renderWithStores(PermissionPrompt, {
      props: { toolCall: makeAskUserQuestionTool() }
    })

    const { useSessionStore } = await import('@/stores/session')
    const { useMessageStore } = await import('@/stores/message')
    const sessionStore = useSessionStore(pinia)
    sessionStore.currentSessionId = 'sess-1'
    const messageStore = useMessageStore(pinia)
    // Populates permissionToToolMap as a side effect, matching how a real awaiting_permission
    // tool_call event would arrive.
    messageStore.handleToolCall('sess-1', {
      tool_use_id: 'use-askq-1',
      name: 'AskUserQuestion',
      input: { questions: QUESTIONS },
      status: 'awaiting_permission',
      request_id: 'req-1'
    })

    await new Promise(r => setTimeout(r, 0))

    await user.click(screen.getByText('Red'))

    const submitBtn = screen.getByRole('button', { name: /submit answers/i })
    await user.click(submitBtn)
    await new Promise(r => setTimeout(r, 0))

    // Optimistic client-side write happened synchronously on submit.
    let tc = messageStore.toolCallsBySession.get('sess-1')[0]
    expect(tc.answers).toEqual({ 'Which colors?': 'Red' })

    // The live backend broadcast for this transition never carries `updated_input`
    // (only the historical/reload replay path does) — simulate that real event shape.
    messageStore.handleToolCall('sess-1', {
      tool_use_id: 'use-askq-1',
      name: 'AskUserQuestion',
      input: { questions: QUESTIONS },
      status: 'running'
    })

    tc = messageStore.toolCallsBySession.get('sess-1')[0]
    expect(tc.answers).toEqual({ 'Which colors?': 'Red' })

    // Terminal completed event, also without updated_input.
    messageStore.handleToolCall('sess-1', {
      tool_use_id: 'use-askq-1',
      name: 'AskUserQuestion',
      input: { questions: QUESTIONS },
      status: 'completed',
      result: 'ok'
    })

    tc = messageStore.toolCallsBySession.get('sess-1')[0]
    expect(tc.status).toBe('completed')
    expect(tc.answers).toEqual({ 'Which colors?': 'Red' })
  })
})
