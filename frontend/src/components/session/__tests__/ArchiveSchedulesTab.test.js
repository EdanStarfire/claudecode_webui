import { describe, it, expect, beforeEach, vi } from 'vitest'
import { nextTick } from 'vue'
import { screen } from '@testing-library/vue'
import { renderWithStores } from '@/test-utils/render'
import ArchiveSchedulesTab from '@/components/session/ArchiveSchedulesTab.vue'

const apiGetMock = vi.hoisted(() => vi.fn())
vi.mock('@/utils/api', () => ({
  apiGet: apiGetMock,
  getAuthToken: vi.fn(() => null),
}))

beforeEach(() => {
  apiGetMock.mockReset()
})

async function loadWith(pinia, schedulesPayload) {
  const { useArchiveTabsStore } = await import('@/stores/archiveTabs')
  const store = useArchiveTabsStore(pinia)
  apiGetMock.mockImplementation((url) => {
    if (url.endsWith('/schedules')) return Promise.resolve(schedulesPayload)
    return Promise.resolve([])
  })
  await store.loadAllTabs('sess-1', 'proj-1', 'arch-1')
  await nextTick()
  return store
}

describe('ArchiveSchedulesTab', () => {
  it('shows the empty state when no schedules were associated with the session', async () => {
    const { pinia } = renderWithStores(ArchiveSchedulesTab, {
      props: { sessionId: 'sess-1', archiveId: 'arch-1' }
    })
    await loadWith(pinia, { schedules: [], history: [] })

    expect(screen.getByText('No schedules were associated with this session.')).toBeTruthy()
  })

  it('renders schedule config cards and execution history rows using the real backend field names', async () => {
    const { pinia } = renderWithStores(ArchiveSchedulesTab, {
      props: { sessionId: 'sess-1', archiveId: 'arch-1' }
    })
    // Field names mirror backend/models/schedule_models.py's Schedule.to_dict()/
    // ScheduleExecution.to_dict() exactly (cron_expression, actual_time,
    // error_message, status enum values) — NOT invented shapes.
    await loadWith(pinia, {
      schedules: [{ schedule_id: 's1', cron_expression: '0 2 * * *', name: 'Nightly audit', status: 'active' }],
      history: [{
        execution_id: 'e1', schedule_id: 's1', actual_time: 1760234403,
        status: 'delivered', error_message: null,
      }],
    })

    expect(screen.getByText('0 2 * * *')).toBeTruthy()
    // "Nightly audit" appears in both the config card and (via schedule_id lookup) the history row.
    expect(screen.getAllByText('Nightly audit')).toHaveLength(2)
    expect(screen.getByText('delivered')).toBeTruthy()
  })

  it('shows the error message for a failed execution and falls back to schedule_id when unmatched', async () => {
    const { pinia } = renderWithStores(ArchiveSchedulesTab, {
      props: { sessionId: 'sess-1', archiveId: 'arch-1' }
    })
    await loadWith(pinia, {
      schedules: [{ schedule_id: 's1', cron_expression: '*/30 * * * *', name: 'Poll comments', status: 'active' }],
      history: [{
        execution_id: 'e2', schedule_id: 's-deleted', actual_time: 1760234000,
        status: 'error', error_message: 'GitHub API rate limited',
      }],
    })

    expect(screen.getByText('s-deleted')).toBeTruthy()
    expect(screen.getByText('GitHub API rate limited')).toBeTruthy()
    expect(screen.getByText('error')).toBeTruthy()
  })

  it('shows only the config section when there is no execution history yet', async () => {
    const { pinia } = renderWithStores(ArchiveSchedulesTab, {
      props: { sessionId: 'sess-1', archiveId: 'arch-1' }
    })
    await loadWith(pinia, {
      schedules: [{ schedule_id: 's1', cron_expression: '*/30 * * * *', name: 'Poll comments', status: 'active' }],
      history: [],
    })

    expect(screen.getByText('Schedule Config')).toBeTruthy()
    expect(screen.queryByText('Execution History')).toBeNull()
  })
})
