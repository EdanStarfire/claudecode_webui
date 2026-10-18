import { describe, it, expect, beforeEach, vi } from 'vitest'
import { nextTick } from 'vue'
import { screen } from '@testing-library/vue'
import userEvent from '@testing-library/user-event'
import { renderWithStores } from '@/test-utils/render'
import ArchiveHistoryTab from '@/components/session/ArchiveHistoryTab.vue'

const apiGetMock = vi.hoisted(() => vi.fn())
vi.mock('@/utils/api', () => ({
  apiGet: apiGetMock,
  getAuthToken: vi.fn(() => null),
}))

const MarkdownViewStub = {
  props: ['content'],
  template: '<div class="markdown-stub">{{ content }}</div>',
}

beforeEach(() => {
  apiGetMock.mockReset()
})

async function loadWith(pinia, historyFiles) {
  const { useArchiveTabsStore } = await import('@/stores/archiveTabs')
  const store = useArchiveTabsStore(pinia)
  apiGetMock.mockImplementation((url) => {
    if (url.endsWith('/history')) return Promise.resolve(historyFiles)
    if (url.endsWith('/schedules')) return Promise.resolve({ schedules: [], history: [] })
    return Promise.resolve([])
  })
  await store.loadAllTabs('sess-1', 'proj-1', 'arch-1')
  await nextTick()
  return store
}

describe('ArchiveHistoryTab', () => {
  it('shows the empty state when no history was captured', async () => {
    const { pinia } = renderWithStores(ArchiveHistoryTab, {
      props: { sessionId: 'sess-1', archiveId: 'arch-1' },
      stubs: { MarkdownView: MarkdownViewStub },
    })
    await loadWith(pinia, [])

    expect(screen.getByText('No history was captured for this session.')).toBeTruthy()
  })

  it('auto-selects the first history file and renders its markdown content', async () => {
    const { pinia } = renderWithStores(ArchiveHistoryTab, {
      props: { sessionId: 'sess-1', archiveId: 'arch-1' },
      stubs: { MarkdownView: MarkdownViewStub },
    })

    apiGetMock.mockImplementation((url) => {
      if (url.endsWith('/history')) {
        return Promise.resolve([
          { name: '1760284981_disposal.md', size_bytes: 2100, mtime: 2 },
          { name: '1760260004_reset.md', size_bytes: 1400, mtime: 1 },
        ])
      }
      if (url.endsWith('/schedules')) return Promise.resolve({ schedules: [], history: [] })
      if (url.includes('/history/')) return Promise.resolve({ content: '# Disposal Summary' })
      return Promise.resolve([])
    })

    const { useArchiveTabsStore } = await import('@/stores/archiveTabs')
    const store = useArchiveTabsStore(pinia)
    await store.loadAllTabs('sess-1', 'proj-1', 'arch-1')
    await nextTick()
    await nextTick()

    expect(screen.getByText('1760284981_disposal.md')).toBeTruthy()
    expect(screen.getByText('# Disposal Summary')).toBeTruthy()
  })

  it('switches preview content when a different history row is clicked', async () => {
    const { pinia } = renderWithStores(ArchiveHistoryTab, {
      props: { sessionId: 'sess-1', archiveId: 'arch-1' },
      stubs: { MarkdownView: MarkdownViewStub },
    })

    apiGetMock.mockImplementation((url) => {
      if (url.endsWith('/history')) {
        return Promise.resolve([
          { name: 'disposal.md', size_bytes: 2100, mtime: 2 },
          { name: 'reset.md', size_bytes: 1400, mtime: 1 },
        ])
      }
      if (url.endsWith('/schedules')) return Promise.resolve({ schedules: [], history: [] })
      if (url.endsWith('/history/disposal.md')) return Promise.resolve({ content: '# Disposal' })
      if (url.endsWith('/history/reset.md')) return Promise.resolve({ content: '# Reset' })
      return Promise.resolve([])
    })

    const { useArchiveTabsStore } = await import('@/stores/archiveTabs')
    const store = useArchiveTabsStore(pinia)
    await store.loadAllTabs('sess-1', 'proj-1', 'arch-1')
    await nextTick()
    await nextTick()
    expect(screen.getByText('# Disposal')).toBeTruthy()

    const user = userEvent.setup()
    await user.click(screen.getByText('reset.md'))
    await nextTick()

    expect(screen.getByText('# Reset')).toBeTruthy()
  })
})
