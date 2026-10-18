import { describe, it, expect, beforeEach, vi } from 'vitest'
import { nextTick } from 'vue'
import { screen } from '@testing-library/vue'
import { renderWithStores } from '@/test-utils/render'
import ArchiveAttachmentsTab from '@/components/session/ArchiveAttachmentsTab.vue'

const apiGetMock = vi.hoisted(() => vi.fn())
vi.mock('@/utils/api', () => ({
  apiGet: apiGetMock,
  getAuthToken: vi.fn(() => null),
}))

beforeEach(() => {
  apiGetMock.mockReset()
})

async function loadWith(pinia, attachments) {
  const { useArchiveTabsStore } = await import('@/stores/archiveTabs')
  const store = useArchiveTabsStore(pinia)
  apiGetMock.mockImplementation((url) => {
    if (url.endsWith('/attachments')) return Promise.resolve(attachments)
    if (url.endsWith('/schedules')) return Promise.resolve({ schedules: [], history: [] })
    return Promise.resolve([])
  })
  await store.loadAllTabs('sess-1', 'proj-1', 'arch-1')
  await nextTick()
  return store
}

describe('ArchiveAttachmentsTab', () => {
  it('shows the empty state when no attachments were recorded', async () => {
    const { pinia } = renderWithStores(ArchiveAttachmentsTab, {
      props: { sessionId: 'sess-1', archiveId: 'arch-1' }
    })
    await loadWith(pinia, [])

    expect(screen.getByText('No attachments were recorded for this session.')).toBeTruthy()
  })

  it('renders a row per attachment with a download link to the archive endpoint', async () => {
    const { pinia } = renderWithStores(ArchiveAttachmentsTab, {
      props: { sessionId: 'sess-1', archiveId: 'arch-1' }
    })
    await loadWith(pinia, [
      { name: 'error_traceback.txt', size_bytes: 4300 },
      { name: 'build_output.zip', size_bytes: 1_800_000 },
    ])

    expect(screen.getByText('error_traceback.txt')).toBeTruthy()
    expect(screen.getByText('build_output.zip')).toBeTruthy()

    const link = screen.getByText('error_traceback.txt').closest('.att-row').querySelector('a.dl-btn')
    expect(link.getAttribute('href')).toBe(
      '/api/projects/proj-1/archives/sess-1/arch-1/attachments/error_traceback.txt'
    )
  })
})
