import { describe, it, expect, beforeEach, vi } from 'vitest'
import { nextTick } from 'vue'
import { screen } from '@testing-library/vue'
import userEvent from '@testing-library/user-event'
import { renderWithStores } from '@/test-utils/render'
import ArchiveProxyLogsTab from '@/components/session/ArchiveProxyLogsTab.vue'

const apiGetMock = vi.hoisted(() => vi.fn())
vi.mock('@/utils/api', () => ({
  apiGet: apiGetMock,
  getAuthToken: vi.fn(() => null),
}))

beforeEach(() => {
  apiGetMock.mockReset()
})

async function loadWith(pinia, proxyLogs) {
  const { useArchiveTabsStore } = await import('@/stores/archiveTabs')
  const store = useArchiveTabsStore(pinia)
  apiGetMock.mockImplementation((url) => {
    if (url.endsWith('/proxy-logs')) return Promise.resolve(proxyLogs)
    if (url.endsWith('/schedules')) return Promise.resolve({ schedules: [], history: [] })
    return Promise.resolve([])
  })
  await store.loadAllTabs('sess-1', 'proj-1', 'arch-1')
  await nextTick()
  return store
}

describe('ArchiveProxyLogsTab', () => {
  it('shows the empty state when proxy was not enabled for the session', async () => {
    const { pinia } = renderWithStores(ArchiveProxyLogsTab, {
      props: { sessionId: 'sess-1', archiveId: 'arch-1' }
    })
    await loadWith(pinia, [])

    expect(screen.getByText('Proxy was not enabled for this session — no logs were captured.')).toBeTruthy()
  })

  it('renders a grayed-out row for a log file absent from the listing, without hiding it', async () => {
    const { pinia } = renderWithStores(ArchiveProxyLogsTab, {
      props: { sessionId: 'sess-1', archiveId: 'arch-1' }
    })
    await loadWith(pinia, [{ name: 'access.log', size_bytes: 100, line_count: 5 }])

    expect(screen.getByText('access.log')).toBeTruthy()
    expect(screen.getByText('dropped.log')).toBeTruthy()
    // dns.log/socks5.log/dropped.log are all uncaptured here — one "not captured" row each.
    expect(screen.getAllByText('not captured for this session')).toHaveLength(3)
  })

  it('a small log (<= head+tail chunk) loads as a single block with no gap divider', async () => {
    const { pinia } = renderWithStores(ArchiveProxyLogsTab, {
      props: { sessionId: 'sess-1', archiveId: 'arch-1' }
    })
    await loadWith(pinia, [{ name: 'socks5.log', size_bytes: 50, line_count: 2 }])

    apiGetMock.mockResolvedValueOnce({ lines: ['line-0', 'line-1'], total_lines: 2, start_line: 0, end_line: 2 })

    const user = userEvent.setup()
    await user.click(screen.getByText('socks5.log'))
    await nextTick()
    await nextTick()

    expect(screen.getByText('line-0')).toBeTruthy()
    expect(screen.getByText('line-1')).toBeTruthy()
    expect(screen.queryByText(/lines hidden/)).toBeNull()
  })

  it('a large log loads head+tail chunks with a "Load more" gap, which fills in and disappears', async () => {
    const { pinia } = renderWithStores(ArchiveProxyLogsTab, {
      props: { sessionId: 'sess-1', archiveId: 'arch-1' }
    })
    await loadWith(pinia, [{ name: 'access.log', size_bytes: 1000, line_count: 110 }])

    apiGetMock.mockImplementation((url, opts) => {
      const fromEnd = opts?.params?.from_end
      if (fromEnd) {
        return Promise.resolve({ lines: ['tail-0'], total_lines: 110, start_line: 60, end_line: 61 })
      }
      const offset = opts?.params?.offset ?? 0
      if (offset === 0) {
        return Promise.resolve({ lines: ['head-0'], total_lines: 110, start_line: 0, end_line: 1 })
      }
      // "Load more" continues from the previous head end_line up to the tail boundary.
      return Promise.resolve({ lines: ['mid-0'], total_lines: 110, start_line: offset, end_line: 60 })
    })

    const user = userEvent.setup()
    await user.click(screen.getByText('access.log'))
    await nextTick()
    await nextTick()

    expect(screen.getByText('head-0')).toBeTruthy()
    expect(screen.getByText('tail-0')).toBeTruthy()
    expect(screen.getByText('59 lines hidden')).toBeTruthy()

    await user.click(screen.getByText('Load more ⇕'))
    await nextTick()

    expect(screen.getByText('mid-0')).toBeTruthy()
    // head (end_line=60) has now caught up to tail (start_line=60) — gap divider is gone.
    expect(screen.queryByText(/lines hidden/)).toBeNull()
  })

  it('collapses an expanded row back without refetching on a second click', async () => {
    const { pinia } = renderWithStores(ArchiveProxyLogsTab, {
      props: { sessionId: 'sess-1', archiveId: 'arch-1' }
    })
    await loadWith(pinia, [{ name: 'socks5.log', size_bytes: 50, line_count: 2 }])

    apiGetMock.mockResolvedValueOnce({ lines: ['line-0'], total_lines: 1, start_line: 0, end_line: 1 })

    const user = userEvent.setup()
    await user.click(screen.getByText('socks5.log'))
    await nextTick()
    await nextTick()
    expect(screen.getByText('line-0')).toBeTruthy()

    await user.click(screen.getByText('socks5.log'))
    await nextTick()
    expect(screen.queryByText('line-0')).toBeNull()

    const callCountAfterCollapse = apiGetMock.mock.calls.length
    await user.click(screen.getByText('socks5.log'))
    await nextTick()
    expect(screen.getByText('line-0')).toBeTruthy()
    expect(apiGetMock.mock.calls.length).toBe(callCountAfterCollapse)
  })

  it('a zero-progress "Load more" response (no forward movement) still closes the gap instead of leaving a dead button', async () => {
    const { pinia } = renderWithStores(ArchiveProxyLogsTab, {
      props: { sessionId: 'sess-1', archiveId: 'arch-1' }
    })
    await loadWith(pinia, [{ name: 'access.log', size_bytes: 1000, line_count: 110 }])

    apiGetMock.mockImplementation((url, opts) => {
      if (opts?.params?.from_end) {
        return Promise.resolve({ lines: ['tail-0'], total_lines: 110, start_line: 60, end_line: 61 })
      }
      return Promise.resolve({ lines: ['head-0'], total_lines: 110, start_line: 0, end_line: 1 })
    })

    const user = userEvent.setup()
    await user.click(screen.getByText('access.log'))
    await nextTick()
    await nextTick()
    expect(screen.getByText('59 lines hidden')).toBeTruthy()

    // Backend returns a chunk that makes no forward progress (end_line === requested offset).
    apiGetMock.mockImplementationOnce(() =>
      Promise.resolve({ lines: [], total_lines: 110, start_line: 1, end_line: 1 })
    )
    await user.click(screen.getByText('Load more ⇕'))
    await nextTick()

    // The gap divider must still disappear rather than staying clickable forever.
    expect(screen.queryByText(/lines hidden/)).toBeNull()
    expect(screen.getByText('tail-0')).toBeTruthy()
  })

  it('ignores a rapid second "Load more" click while the first request is still in flight', async () => {
    const { pinia } = renderWithStores(ArchiveProxyLogsTab, {
      props: { sessionId: 'sess-1', archiveId: 'arch-1' }
    })
    await loadWith(pinia, [{ name: 'access.log', size_bytes: 1000, line_count: 110 }])

    apiGetMock.mockImplementation((url, opts) => {
      if (opts?.params?.from_end) {
        return Promise.resolve({ lines: ['tail-0'], total_lines: 110, start_line: 100, end_line: 101 })
      }
      return Promise.resolve({ lines: ['head-0'], total_lines: 110, start_line: 0, end_line: 1 })
    })

    const user = userEvent.setup()
    await user.click(screen.getByText('access.log'))
    await nextTick()
    await nextTick()

    let resolveLoadMore
    apiGetMock.mockImplementationOnce(() => new Promise((resolve) => { resolveLoadMore = resolve }))
    const callsBeforeLoadMore = apiGetMock.mock.calls.length

    const loadMoreBtn = screen.getByText('Load more ⇕')
    await user.click(loadMoreBtn)
    await nextTick()
    // Second click while the first is still pending — must not fire a second request.
    await user.click(screen.getByText('Loading…'))
    await nextTick()

    expect(apiGetMock.mock.calls.length).toBe(callsBeforeLoadMore + 1)

    resolveLoadMore({ lines: ['mid-0'], total_lines: 110, start_line: 1, end_line: 100 })
    await nextTick()
    await nextTick()
  })
})
