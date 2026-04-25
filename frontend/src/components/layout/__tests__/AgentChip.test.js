import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { screen } from '@testing-library/vue'
import { renderWithStores } from '@/test-utils/render'
import AgentChip from '@/components/layout/AgentChip.vue'
import { makeSession } from '@/test-utils/factories'

const apiMock = vi.hoisted(() => ({
  get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn()
}))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn() }))
vi.mock('@/stores/schedule', () => ({
  useScheduleStore: () => ({ getScheduleCount: vi.fn().mockReturnValue(0) })
}))

beforeEach(() => {
  setActivePinia(createPinia())
})

describe('AgentChip', () => {
  it('renders session name and reflects active state', () => {
    const session = makeSession({ session_id: 'sess-1', name: 'My Agent', state: 'active' })

    renderWithStores(AgentChip, {
      props: {
        session,
        isActive: true,
        isParentOfActive: false,
        variant: 'default',
        isGhost: false
      }
    })

    expect(screen.getByText('My Agent')).toBeTruthy()
    // Role button with aria-label that includes the agent name
    const btn = screen.getByRole('button', { name: /my agent/i })
    expect(btn.classList.contains('active')).toBe(true)
  })
})
