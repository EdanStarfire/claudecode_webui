import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { screen } from '@testing-library/vue'
import { renderWithStores } from '@/test-utils/render'
import BashToolHandler from '@/components/tools/BashToolHandler.vue'

const apiMock = vi.hoisted(() => ({
  get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn()
}))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn() }))

beforeEach(() => {
  setActivePinia(createPinia())
})

describe('BashToolHandler', () => {
  it('renders command text and output content', () => {
    const toolCall = {
      id: 'use-1',
      name: 'Bash',
      input: { command: 'ls -la' },
      status: 'completed',
      result: { error: false, content: 'file.txt\ndir/' },
      isExpanded: true
    }

    renderWithStores(BashToolHandler, { props: { toolCall } })

    expect(screen.getByText('ls -la')).toBeTruthy()
    expect(screen.getByText(/file\.txt/)).toBeTruthy()
  })
})
