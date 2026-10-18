import { describe, it, expect, beforeEach, vi } from 'vitest'
import { nextTick } from 'vue'
import { screen } from '@testing-library/vue'
import userEvent from '@testing-library/user-event'
import { renderWithStores } from '@/test-utils/render'
import ArchiveTabBar from '@/components/session/ArchiveTabBar.vue'

const apiGetMock = vi.hoisted(() => vi.fn())
vi.mock('@/utils/api', () => ({
  apiGet: apiGetMock,
  getAuthToken: vi.fn(() => null),
}))

beforeEach(() => {
  apiGetMock.mockReset()
})

describe('ArchiveTabBar', () => {
  it('renders all 6 tabs with Messages active by default', () => {
    renderWithStores(ArchiveTabBar, { props: { sessionId: 'sess-1' } })

    for (const label of ['Messages', 'Proxy Logs', 'Queue', 'Attachments', 'History', 'Schedules']) {
      expect(screen.getByText(label, { exact: false })).toBeTruthy()
    }
    expect(screen.getByText('Messages').className).toContain('active')
  })

  it('shows count badges once listing data is loaded, and switches tabs on click', async () => {
    const { pinia } = renderWithStores(ArchiveTabBar, { props: { sessionId: 'sess-1' } })
    const { useArchiveTabsStore } = await import('@/stores/archiveTabs')
    const store = useArchiveTabsStore(pinia)

    apiGetMock.mockImplementation((url) => {
      if (url.endsWith('/proxy-logs')) return Promise.resolve([{ name: 'access.log', size_bytes: 1, line_count: 1 }])
      if (url.endsWith('/schedules')) return Promise.resolve({ schedules: [{ schedule_id: 's1' }], history: [] })
      return Promise.resolve([])
    })
    await store.loadAllTabs('sess-1', 'proj-1', 'arch-1')
    await nextTick()

    expect(screen.getAllByText('1', { exact: true })).toHaveLength(2)

    const user = userEvent.setup()
    await user.click(screen.getByText('Schedules', { exact: false }))
    expect(store.activeTabForSession('sess-1')).toBe('schedules')
  })

  it('keeps tab selection session-scoped — selecting a tab for one session does not affect another', async () => {
    const { useArchiveTabsStore } = await import('@/stores/archiveTabs')
    const { pinia } = renderWithStores(ArchiveTabBar, { props: { sessionId: 'sess-1' } })
    const store = useArchiveTabsStore(pinia)

    store.setActiveTab('sess-1', 'queue')

    expect(store.activeTabForSession('sess-1')).toBe('queue')
    expect(store.activeTabForSession('sess-2')).toBe('messages')
  })
})
