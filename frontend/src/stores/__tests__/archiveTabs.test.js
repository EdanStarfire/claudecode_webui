import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

const apiGetMock = vi.hoisted(() => vi.fn())
vi.mock('@/utils/api', () => ({
  apiGet: apiGetMock,
  getAuthToken: vi.fn(() => null),
}))

beforeEach(() => {
  setActivePinia(createPinia())
  apiGetMock.mockReset()
})

describe('archiveTabs store (issue #1263)', () => {
  describe('loadAllTabs', () => {
    it('fires all 5 listing calls in parallel and populates per-session Maps', async () => {
      const { useArchiveTabsStore } = await import('@/stores/archiveTabs')
      const store = useArchiveTabsStore()

      apiGetMock.mockImplementation((url) => {
        if (url.endsWith('/proxy-logs')) return Promise.resolve([{ name: 'access.log', size_bytes: 10, line_count: 2 }])
        if (url.endsWith('/queue')) return Promise.resolve([{ type: 'enqueue', queue_id: 'q1', content: 'hi', created_at: 1 }])
        if (url.endsWith('/attachments')) return Promise.resolve([{ name: 'notes.txt', size_bytes: 5 }])
        if (url.endsWith('/history')) return Promise.resolve([{ name: 'a.md', size_bytes: 3, mtime: 1 }])
        if (url.endsWith('/schedules')) return Promise.resolve({ schedules: [{ schedule_id: 's1' }], history: [] })
        throw new Error(`unexpected url ${url}`)
      })

      await store.loadAllTabs('sess-1', 'proj-1', 'arch-1')

      expect(store.proxyLogsForSession('sess-1')).toHaveLength(1)
      expect(store.queueForSession('sess-1')).toHaveLength(1)
      expect(store.attachmentsForSession('sess-1')).toHaveLength(1)
      expect(store.historyForSession('sess-1')).toHaveLength(1)
      expect(store.schedulesForSession('sess-1').schedules).toHaveLength(1)
      expect(apiGetMock).toHaveBeenCalledTimes(5)
    })

    it('is a no-op when sessionId/projectId/archiveId are missing', async () => {
      const { useArchiveTabsStore } = await import('@/stores/archiveTabs')
      const store = useArchiveTabsStore()

      await store.loadAllTabs(null, 'proj-1', 'arch-1')
      expect(apiGetMock).not.toHaveBeenCalled()
    })

    it('defaults a failed listing call to an empty result instead of throwing', async () => {
      const { useArchiveTabsStore } = await import('@/stores/archiveTabs')
      const store = useArchiveTabsStore()

      apiGetMock.mockImplementation((url) => {
        if (url.endsWith('/proxy-logs')) return Promise.reject(new Error('network error'))
        if (url.endsWith('/schedules')) return Promise.resolve({ schedules: [], history: [] })
        return Promise.resolve([])
      })

      await expect(store.loadAllTabs('sess-1', 'proj-1', 'arch-1')).resolves.toBeUndefined()
      expect(store.proxyLogsForSession('sess-1')).toEqual([])
    })

    it('sets isLoadingTabs true during the fetch and false after', async () => {
      const { useArchiveTabsStore } = await import('@/stores/archiveTabs')
      const store = useArchiveTabsStore()

      let resolveProxy
      apiGetMock.mockImplementation((url) => {
        if (url.endsWith('/proxy-logs')) return new Promise((resolve) => { resolveProxy = resolve })
        if (url.endsWith('/schedules')) return Promise.resolve({ schedules: [], history: [] })
        return Promise.resolve([])
      })

      const promise = store.loadAllTabs('sess-1', 'proj-1', 'arch-1')
      expect(store.isLoadingTabs('sess-1')).toBe(true)
      resolveProxy([])
      await promise
      expect(store.isLoadingTabs('sess-1')).toBe(false)
    })
  })

  describe('reconstructedQueueForSession', () => {
    it('collapses the raw enqueue+status event log into one entry per queue_id, final status wins', async () => {
      const { useArchiveTabsStore } = await import('@/stores/archiveTabs')
      const store = useArchiveTabsStore()

      apiGetMock.mockImplementation((url) => {
        if (url.endsWith('/queue')) {
          return Promise.resolve([
            { type: 'enqueue', queue_id: 'q1', content: 'a', created_at: 100 },
            { type: 'status', queue_id: 'q1', status: 'sent' },
            { type: 'enqueue', queue_id: 'q2', content: 'b', created_at: 200 },
          ])
        }
        if (url.endsWith('/schedules')) return Promise.resolve({ schedules: [], history: [] })
        return Promise.resolve([])
      })
      await store.loadAllTabs('sess-1', 'proj-1', 'arch-1')

      // 3 raw records collapse to 2 reconstructed items — this is what the
      // ArchiveTabBar count badge must agree with (not the raw record count).
      expect(store.queueForSession('sess-1')).toHaveLength(3)
      const items = store.reconstructedQueueForSession('sess-1')
      expect(items).toHaveLength(2)
      expect(items[0]).toMatchObject({ queue_id: 'q1', status: 'sent' })
      expect(items[1]).toMatchObject({ queue_id: 'q2', status: 'pending' })
    })
  })

  describe('loadProxyLogContent', () => {
    it('returns null when no archive context has been established for the session', async () => {
      const { useArchiveTabsStore } = await import('@/stores/archiveTabs')
      const store = useArchiveTabsStore()

      const result = await store.loadProxyLogContent('sess-unknown', 'arch-1', 'access.log')
      expect(result).toBeNull()
      expect(apiGetMock).not.toHaveBeenCalled()
    })

    it('builds the correct URL and forwards offset/limit/from_end params', async () => {
      const { useArchiveTabsStore } = await import('@/stores/archiveTabs')
      const store = useArchiveTabsStore()

      apiGetMock.mockImplementation((url) => {
        if (url.endsWith('/schedules')) return Promise.resolve({ schedules: [], history: [] })
        return Promise.resolve([])
      })
      await store.loadAllTabs('sess-1', 'proj-1', 'arch-1')
      apiGetMock.mockReset()
      apiGetMock.mockResolvedValue({ lines: ['a', 'b'], total_lines: 10, start_line: 0, end_line: 2 })

      const result = await store.loadProxyLogContent('sess-1', 'arch-1', 'access.log', { offset: 0, limit: 2 })

      expect(apiGetMock).toHaveBeenCalledWith(
        '/api/projects/proj-1/archives/sess-1/arch-1/proxy-logs/access.log',
        { params: { offset: 0, limit: 2, from_end: false } }
      )
      expect(result).toEqual({ lines: ['a', 'b'], total_lines: 10, start_line: 0, end_line: 2 })
    })
  })

  describe('loadHistoryContent', () => {
    it('fetches and caches markdown content, returning the cached value on a second call', async () => {
      const { useArchiveTabsStore } = await import('@/stores/archiveTabs')
      const store = useArchiveTabsStore()

      apiGetMock.mockImplementation((url) => {
        if (url.endsWith('/schedules')) return Promise.resolve({ schedules: [], history: [] })
        return Promise.resolve([])
      })
      await store.loadAllTabs('sess-1', 'proj-1', 'arch-1')
      apiGetMock.mockReset()
      apiGetMock.mockResolvedValue({ content: '# Summary' })

      const content = await store.loadHistoryContent('sess-1', 'arch-1', 'disposal.md')
      expect(content).toBe('# Summary')
      expect(store.historyContent('sess-1', 'arch-1', 'disposal.md')).toEqual({
        content: '# Summary', loading: false, error: null
      })

      await store.loadHistoryContent('sess-1', 'arch-1', 'disposal.md')
      expect(apiGetMock).toHaveBeenCalledTimes(1)
    })

    it('does not reuse cached content across two different archives of the same session', async () => {
      const { useArchiveTabsStore } = await import('@/stores/archiveTabs')
      const store = useArchiveTabsStore()

      apiGetMock.mockImplementation((url) => {
        if (url.endsWith('/schedules')) return Promise.resolve({ schedules: [], history: [] })
        return Promise.resolve([])
      })
      await store.loadAllTabs('sess-1', 'proj-1', 'arch-A')
      apiGetMock.mockReset()
      apiGetMock.mockResolvedValue({ content: '# From archive A' })
      await store.loadHistoryContent('sess-1', 'arch-A', 'disposal.md')

      await store.loadAllTabs('sess-1', 'proj-1', 'arch-B')
      apiGetMock.mockReset()
      apiGetMock.mockResolvedValue({ content: '# From archive B' })
      const contentB = await store.loadHistoryContent('sess-1', 'arch-B', 'disposal.md')

      expect(contentB).toBe('# From archive B')
      expect(store.historyContent('sess-1', 'arch-A', 'disposal.md').content).toBe('# From archive A')
      expect(store.historyContent('sess-1', 'arch-B', 'disposal.md').content).toBe('# From archive B')
    })
  })

  describe('getAttachmentDownloadUrl', () => {
    it('returns null without archive context, and a URL once context is set', async () => {
      const { useArchiveTabsStore } = await import('@/stores/archiveTabs')
      const store = useArchiveTabsStore()

      expect(store.getAttachmentDownloadUrl('sess-1', 'arch-1', 'notes.txt')).toBeNull()

      apiGetMock.mockImplementation((url) => {
        if (url.endsWith('/schedules')) return Promise.resolve({ schedules: [], history: [] })
        return Promise.resolve([])
      })
      await store.loadAllTabs('sess-1', 'proj-1', 'arch-1')

      expect(store.getAttachmentDownloadUrl('sess-1', 'arch-1', 'notes.txt')).toBe(
        '/api/projects/proj-1/archives/sess-1/arch-1/attachments/notes.txt'
      )
    })
  })

  describe('clearArchiveTabs', () => {
    it('removes all per-session state including on-demand content caches', async () => {
      const { useArchiveTabsStore } = await import('@/stores/archiveTabs')
      const store = useArchiveTabsStore()

      apiGetMock.mockImplementation((url) => {
        if (url.endsWith('/proxy-logs')) return Promise.resolve([{ name: 'access.log', size_bytes: 1, line_count: 1 }])
        if (url.endsWith('/schedules')) return Promise.resolve({ schedules: [], history: [] })
        return Promise.resolve([])
      })
      await store.loadAllTabs('sess-1', 'proj-1', 'arch-1')

      apiGetMock.mockResolvedValue({ content: 'hello' })
      await store.loadHistoryContent('sess-1', 'arch-1', 'a.md')

      store.clearArchiveTabs('sess-1')

      expect(store.proxyLogsForSession('sess-1')).toEqual([])
      expect(store.historyContent('sess-1', 'arch-1', 'a.md')).toBeNull()
      expect(store.getAttachmentDownloadUrl('sess-1', 'arch-1', 'notes.txt')).toBeNull()
    })
  })
})
