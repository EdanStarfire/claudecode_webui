import { describe, it, expect, beforeEach, vi } from 'vitest'
import { nextTick } from 'vue'
import { screen } from '@testing-library/vue'
import { renderWithStores } from '@/test-utils/render'
import ArchiveQueueTab from '@/components/session/ArchiveQueueTab.vue'

const apiGetMock = vi.hoisted(() => vi.fn())
vi.mock('@/utils/api', () => ({
  apiGet: apiGetMock,
  getAuthToken: vi.fn(() => null),
}))

beforeEach(() => {
  apiGetMock.mockReset()
})

async function loadWith(pinia, queueRecords) {
  const { useArchiveTabsStore } = await import('@/stores/archiveTabs')
  const store = useArchiveTabsStore(pinia)
  apiGetMock.mockImplementation((url) => {
    if (url.endsWith('/queue')) return Promise.resolve(queueRecords)
    if (url.endsWith('/schedules')) return Promise.resolve({ schedules: [], history: [] })
    return Promise.resolve([])
  })
  await store.loadAllTabs('sess-1', 'proj-1', 'arch-1')
  await nextTick()
  return store
}

describe('ArchiveQueueTab', () => {
  it('shows the empty state when no queue entries were recorded', async () => {
    const { pinia } = renderWithStores(ArchiveQueueTab, {
      props: { sessionId: 'sess-1', archiveId: 'arch-1' }
    })
    await loadWith(pinia, [])

    expect(screen.getByText('No queued messages were recorded for this session.')).toBeTruthy()
  })

  it('reconstructs one row per queue_id from the enqueue+status event log, final status wins', async () => {
    const { pinia } = renderWithStores(ArchiveQueueTab, {
      props: { sessionId: 'sess-1', archiveId: 'arch-1' }
    })
    await loadWith(pinia, [
      { type: 'enqueue', queue_id: 'q1', content: 'Run the audit', created_at: 100 },
      { type: 'enqueue', queue_id: 'q2', content: 'Nightly check', created_at: 200 },
      { type: 'status', queue_id: 'q1', status: 'sent' },
      { type: 'status', queue_id: 'q2', status: 'cancelled' },
    ])

    expect(screen.getByText('Run the audit')).toBeTruthy()
    expect(screen.getByText('Nightly check')).toBeTruthy()
    expect(screen.getByText('delivered')).toBeTruthy()
    expect(screen.getByText('cancelled')).toBeTruthy()
  })

  it('shows a pending status pill for an item with no terminal status event yet', async () => {
    const { pinia } = renderWithStores(ArchiveQueueTab, {
      props: { sessionId: 'sess-1', archiveId: 'arch-1' }
    })
    await loadWith(pinia, [
      { type: 'enqueue', queue_id: 'q1', content: 'Still waiting', created_at: 100 },
    ])

    expect(screen.getByText('pending')).toBeTruthy()
  })
})
