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
vi.mock('@/components/tools/AskUserQuestionToolHandler.vue', () => ({
  default: { template: '<div />', props: ['toolCall', 'disabled'] }
}))

const makePendingTool = (overrides = {}) => ({
  id: 'use-1',
  name: 'Edit',
  input: { path: '/tmp/f' },
  status: 'permission_required',
  backendStatus: 'awaiting_permission',
  permissionRequestId: 'req-1',
  suggestions: [],
  isExpanded: true,
  ...overrides
})

beforeEach(() => {
  setActivePinia(createPinia())
  Object.values(apiMock).forEach(fn => fn.mockReset())
  apiMock.post.mockResolvedValue({})
})

describe('PermissionPrompt', () => {
  it('renders Approve and Deny buttons for a permission_required tool', async () => {
    const { pinia } = renderWithStores(PermissionPrompt, {
      props: { toolCall: makePendingTool() }
    })

    const { useSessionStore } = await import('@/stores/session')
    const sessionStore = useSessionStore(pinia)
    sessionStore.currentSessionId = 'sess-1'

    await new Promise(r => setTimeout(r, 0))

    expect(screen.getByRole('button', { name: /approve/i })).toBeTruthy()
    expect(screen.getByRole('button', { name: /deny/i })).toBeTruthy()
  })

  it('clicking Approve calls sendPermissionResponse with allow decision', async () => {
    const user = userEvent.setup()
    const { pinia } = renderWithStores(PermissionPrompt, {
      props: { toolCall: makePendingTool() }
    })

    const { useSessionStore } = await import('@/stores/session')
    const sessionStore = useSessionStore(pinia)
    sessionStore.currentSessionId = 'sess-1'

    await new Promise(r => setTimeout(r, 0))

    const approveBtn = screen.getByRole('button', { name: /^approve$/i })
    await user.click(approveBtn)

    expect(apiMock.post).toHaveBeenCalledWith(
      expect.stringContaining('/permission/req-1'),
      expect.objectContaining({ decision: 'allow' })
    )
  })
})
