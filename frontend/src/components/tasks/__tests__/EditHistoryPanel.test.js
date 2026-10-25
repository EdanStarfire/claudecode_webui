import { describe, it, expect, beforeEach, vi } from 'vitest'
import { screen } from '@testing-library/vue'
import { renderWithStores } from '@/test-utils/render'
import EditHistoryPanel from '@/components/tasks/EditHistoryPanel.vue'
import { useSessionStore } from '@/stores/session'

const apiMock = vi.hoisted(() => ({
  get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn()
}))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn() }))

const flush = () => new Promise(r => setTimeout(r, 0))

const ENTRIES = [
  {
    tool_use_id: 'e1', tool_name: 'Edit', status: 'completed', timestamp: 1,
    file_path: 'src/app/main.py',
    input: { file_path: 'src/app/main.py', old_string: 'a', new_string: 'b' },
  },
  {
    tool_use_id: 'w1', tool_name: 'Write', status: 'completed', timestamp: 2, line_count: 3,
    file_path: '/etc/outside/conf.py', file_path_outside_workdir: true,
    input: { file_path: '/etc/outside/conf.py', content: 'x\ny\nz' },
  },
]

beforeEach(() => {
  Object.values(apiMock).forEach(fn => fn.mockReset())
  apiMock.get.mockResolvedValue({ entries: ENTRIES, tool_count: 2 })
})

async function mountPanel() {
  const { pinia } = renderWithStores(EditHistoryPanel)
  useSessionStore(pinia).currentSessionId = 's1'
  await flush()
  await flush()
}

describe('EditHistoryPanel (issue #2279 relative paths)', () => {
  it('labels a workdir-relative entry by basename with the relative path on hover', async () => {
    await mountPanel()
    const label = screen.getByText('main.py')
    expect(label.getAttribute('title')).toBe('src/app/main.py')
    expect(screen.queryAllByText('↗')).toHaveLength(1)
  })

  it('flags an outside-workdir entry and keeps its absolute path on hover', async () => {
    await mountPanel()
    const label = screen.getByText('conf.py')
    expect(label.getAttribute('title')).toBe(
      '/etc/outside/conf.py (outside session working directory)'
    )
    expect(screen.getByText('↗').getAttribute('title')).toMatch(/Outside the session/)
  })
})
