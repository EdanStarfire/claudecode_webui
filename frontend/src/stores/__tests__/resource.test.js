import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

const apiGetMock = vi.hoisted(() => vi.fn())
const apiDeleteMock = vi.hoisted(() => vi.fn())
vi.mock('@/utils/api', () => ({
  apiGet: apiGetMock,
  apiDelete: apiDeleteMock,
  getAuthToken: vi.fn(() => null)
}))
vi.mock('@/stores/session', () => ({
  useSessionStore: vi.fn(() => ({ currentSessionId: 'sess-1' }))
}))

beforeEach(() => {
  setActivePinia(createPinia())
  apiGetMock.mockReset()
  apiDeleteMock.mockReset()
})

function makeResource(overrides = {}) {
  return {
    resource_id: 'r1',
    session_id: 'sess-1',
    original_name: 'report.md',
    title: 'report.md',
    format: 'md',
    mime_type: 'text/markdown',
    is_image: false,
    is_video: false,
    size_bytes: 100,
    timestamp: 100,
    ...overrides
  }
}

describe('resource store — versioning/grouping (issue #1680)', () => {
  describe('addResource', () => {
    it('adds a brand new filename as a standalone entry', async () => {
      const { useResourceStore } = await import('@/stores/resource')
      const store = useResourceStore()

      store.addResource('sess-1', makeResource())

      const resources = store.resourcesForSession('sess-1')
      expect(resources).toHaveLength(1)
      expect(resources[0].resource_id).toBe('r1')
      expect(resources[0].version_count).toBe(1)
    })

    it('merges a re-registered filename into the existing group as a new version', async () => {
      const { useResourceStore } = await import('@/stores/resource')
      const store = useResourceStore()

      store.addResource('sess-1', makeResource({ resource_id: 'r1', timestamp: 100 }))
      store.addResource('sess-1', makeResource({ resource_id: 'r2', timestamp: 200 }))

      const resources = store.resourcesForSession('sess-1')
      expect(resources).toHaveLength(1)
      expect(resources[0].resource_id).toBe('r2')
      expect(resources[0].version_count).toBe(2)
      expect(resources[0].versions.map(v => v.resource_id)).toEqual(['r2', 'r1'])
      expect(resources[0].versions.map(v => v.version_number)).toEqual([2, 1])
    })

    it('is case-insensitive when matching filenames for grouping', async () => {
      const { useResourceStore } = await import('@/stores/resource')
      const store = useResourceStore()

      store.addResource('sess-1', makeResource({ resource_id: 'r1', original_name: 'Report.MD' }))
      store.addResource('sess-1', makeResource({ resource_id: 'r2', original_name: 'report.md' }))

      const resources = store.resourcesForSession('sess-1')
      expect(resources).toHaveLength(1)
      expect(resources[0].version_count).toBe(2)
    })

    it('keeps distinct filenames as distinct top-level entries', async () => {
      const { useResourceStore } = await import('@/stores/resource')
      const store = useResourceStore()

      store.addResource('sess-1', makeResource({ resource_id: 'r1', original_name: 'a.txt' }))
      store.addResource('sess-1', makeResource({ resource_id: 'r2', original_name: 'b.txt' }))

      const resources = store.resourcesForSession('sess-1')
      expect(resources).toHaveLength(2)
    })

    it('does not group same base name with a different extension', async () => {
      const { useResourceStore } = await import('@/stores/resource')
      const store = useResourceStore()

      store.addResource('sess-1', makeResource({ resource_id: 'r1', original_name: 'report.md' }))
      store.addResource('sess-1', makeResource({ resource_id: 'r2', original_name: 'report.txt' }))

      const resources = store.resourcesForSession('sess-1')
      expect(resources).toHaveLength(2)
    })

    it('increments total pagination count only for new groups, not merged versions', async () => {
      const { useResourceStore } = await import('@/stores/resource')
      const store = useResourceStore()

      store.paginationBySession.set('sess-1', { total: 0, hasMore: false, loading: false, offset: 0 })

      store.addResource('sess-1', makeResource({ resource_id: 'r1', timestamp: 100 }))
      expect(store.paginationForSession('sess-1').total).toBe(1)

      store.addResource('sess-1', makeResource({ resource_id: 'r2', timestamp: 200 }))
      expect(store.paginationForSession('sess-1').total).toBe(1)

      store.addResource('sess-1', makeResource({ resource_id: 'r3', original_name: 'other.txt', timestamp: 300 }))
      expect(store.paginationForSession('sess-1').total).toBe(2)
    })

    it('does not inflate total when a new version merges into a group visible under an active filter', async () => {
      const { useResourceStore } = await import('@/stores/resource')
      const store = useResourceStore()

      store.paginationBySession.set('sess-1', { total: 0, hasMore: false, loading: false, offset: 0 })
      store.addResource('sess-1', makeResource({ resource_id: 'r1', timestamp: 100 }))
      expect(store.paginationForSession('sess-1').total).toBe(1)

      // Simulate an active search filter that still matches the existing group.
      store.currentFilter.search = 'report'

      store.addResource('sess-1', makeResource({ resource_id: 'r2', timestamp: 200 }))

      expect(store.paginationForSession('sess-1').total).toBe(1)
      const resources = store.resourcesForSession('sess-1')
      expect(resources).toHaveLength(1)
      expect(resources[0].resource_id).toBe('r2')
      expect(resources[0].version_count).toBe(2)
    })

    it('bumps total for an unmatched registration while a filter hides the target group (pre-existing approximation)', async () => {
      const { useResourceStore } = await import('@/stores/resource')
      const store = useResourceStore()

      store.paginationBySession.set('sess-1', { total: 0, hasMore: false, loading: false, offset: 0 })
      // Active filter with no locally-visible match at all: cannot determine group
      // membership from local state, so the pre-#1680 approximation still applies.
      store.currentFilter.search = 'zzz-no-match'

      store.addResource('sess-1', makeResource({ resource_id: 'r1', timestamp: 100 }))
      expect(store.paginationForSession('sess-1').total).toBe(1)
    })

    it('keeps the full-screen preview anchored to the viewed resource when a brand-new group is unshifted (issue #1691)', async () => {
      const { useResourceStore } = await import('@/stores/resource')
      const store = useResourceStore()

      store.addResource('sess-1', makeResource({ resource_id: 'r1', original_name: 'viewed.txt' }))
      store.openFullView('sess-1', 0)
      expect(store.currentFullViewResource.resource_id).toBe('r1')

      store.addResource('sess-1', makeResource({ resource_id: 'r2', original_name: 'unrelated.txt' }))

      const resources = store.resourcesForSession('sess-1')
      expect(resources).toHaveLength(2)
      expect(resources[0].resource_id).toBe('r2')
      expect(store.currentFullViewResource.resource_id).toBe('r1')
      expect(store.currentResourceIndex).toBe(1)
    })

    it('keeps the full-screen preview anchored when a version merges into a different, already-visible group (issue #1691)', async () => {
      const { useResourceStore } = await import('@/stores/resource')
      const store = useResourceStore()

      store.addResource('sess-1', makeResource({ resource_id: 'r1', original_name: 'viewed.txt', timestamp: 100 }))
      store.addResource('sess-1', makeResource({ resource_id: 'r2', original_name: 'other.txt', timestamp: 100 }))
      store.openFullViewById('r1', 'sess-1')
      expect(store.currentFullViewResource.resource_id).toBe('r1')

      // New version registered for the *other* group — merges in place and moves to front.
      store.addResource('sess-1', makeResource({ resource_id: 'r3', original_name: 'other.txt', timestamp: 200 }))

      const resources = store.resourcesForSession('sess-1')
      expect(resources[0].resource_id).toBe('r3')
      expect(store.currentFullViewResource.resource_id).toBe('r1')
      expect(store.currentResourceIndex).toBe(1)
    })

    it('does not touch currentResourceIndex when no full view is open (control, issue #1691)', async () => {
      const { useResourceStore } = await import('@/stores/resource')
      const store = useResourceStore()

      store.addResource('sess-1', makeResource({ resource_id: 'r1', original_name: 'a.txt' }))
      expect(store.fullViewOpen).toBe(false)

      store.addResource('sess-1', makeResource({ resource_id: 'r2', original_name: 'b.txt' }))

      expect(store.currentResourceIndex).toBe(0)
      const resources = store.resourcesForSession('sess-1')
      expect(resources).toHaveLength(2)
      expect(resources[0].resource_id).toBe('r2')
    })

    it('does not reindex a pinned (nested-version) preview, since it is addressed by identity not index (issue #1691)', async () => {
      const { useResourceStore } = await import('@/stores/resource')
      const store = useResourceStore()

      store.addResource('sess-1', makeResource({ resource_id: 'r1', original_name: 'viewed.txt', timestamp: 100 }))
      store.addResource('sess-1', makeResource({ resource_id: 'r2', original_name: 'viewed.txt', timestamp: 200 }))
      // Pin the superseded nested version r1.
      store.openFullViewById('r1', 'sess-1')
      expect(store.currentFullViewResource?.resource_id).toBe('r1')
      const indexBefore = store.currentResourceIndex

      store.addResource('sess-1', makeResource({ resource_id: 'r3', original_name: 'unrelated.txt' }))

      expect(store.currentResourceIndex).toBe(indexBefore)
      expect(store.currentFullViewResource.resource_id).toBe('r1')
    })
  })

  describe('handleResourceRemoved (delete/splice)', () => {
    it('removes a standalone (single-version) resource entirely', async () => {
      const { useResourceStore } = await import('@/stores/resource')
      const store = useResourceStore()

      store.addResource('sess-1', makeResource({ resource_id: 'r1' }))
      store.handleResourceRemoved('sess-1', 'r1')

      expect(store.resourcesForSession('sess-1')).toHaveLength(0)
    })

    it('deleting the latest version promotes the next-newest surviving version', async () => {
      const { useResourceStore } = await import('@/stores/resource')
      const store = useResourceStore()

      store.addResource('sess-1', makeResource({ resource_id: 'r1', timestamp: 100 }))
      store.addResource('sess-1', makeResource({ resource_id: 'r2', timestamp: 200 }))
      store.addResource('sess-1', makeResource({ resource_id: 'r3', timestamp: 300 }))

      store.handleResourceRemoved('sess-1', 'r3')

      const resources = store.resourcesForSession('sess-1')
      expect(resources).toHaveLength(1)
      expect(resources[0].resource_id).toBe('r2')
      expect(resources[0].version_count).toBe(2)
      expect(resources[0].versions.map(v => v.resource_id)).toEqual(['r2', 'r1'])
    })

    it('deleting the second-to-last version collapses the group (drops versions array)', async () => {
      const { useResourceStore } = await import('@/stores/resource')
      const store = useResourceStore()

      store.addResource('sess-1', makeResource({ resource_id: 'r1', timestamp: 100 }))
      store.addResource('sess-1', makeResource({ resource_id: 'r2', timestamp: 200 }))

      store.handleResourceRemoved('sess-1', 'r2')

      const resources = store.resourcesForSession('sess-1')
      expect(resources).toHaveLength(1)
      expect(resources[0].resource_id).toBe('r1')
      expect(resources[0].version_count).toBe(1)
      expect(resources[0].versions).toBeUndefined()
    })

    it('deleting an older (nested, non-representative) version leaves the representative intact', async () => {
      const { useResourceStore } = await import('@/stores/resource')
      const store = useResourceStore()

      store.addResource('sess-1', makeResource({ resource_id: 'r1', timestamp: 100 }))
      store.addResource('sess-1', makeResource({ resource_id: 'r2', timestamp: 200 }))
      store.addResource('sess-1', makeResource({ resource_id: 'r3', timestamp: 300 }))

      store.handleResourceRemoved('sess-1', 'r1')

      const resources = store.resourcesForSession('sess-1')
      expect(resources).toHaveLength(1)
      expect(resources[0].resource_id).toBe('r3')
      expect(resources[0].version_count).toBe(2)
      expect(resources[0].versions.map(v => v.resource_id)).toEqual(['r3', 'r2'])
    })

    it('decrements pagination total only when a whole group is removed', async () => {
      const { useResourceStore } = await import('@/stores/resource')
      const store = useResourceStore()

      store.paginationBySession.set('sess-1', { total: 0, hasMore: false, loading: false, offset: 0 })
      store.addResource('sess-1', makeResource({ resource_id: 'r1', timestamp: 100 }))
      store.addResource('sess-1', makeResource({ resource_id: 'r2', timestamp: 200 }))
      expect(store.paginationForSession('sess-1').total).toBe(1)

      // Remove nested older version — group survives, total unchanged.
      store.handleResourceRemoved('sess-1', 'r1')
      expect(store.paginationForSession('sess-1').total).toBe(1)

      // Remove the last remaining version — group disappears, total decrements.
      store.handleResourceRemoved('sess-1', 'r2')
      expect(store.paginationForSession('sess-1').total).toBe(0)
    })
  })

  describe('getResourceById', () => {
    it('finds a top-level (latest) resource', async () => {
      const { useResourceStore } = await import('@/stores/resource')
      const store = useResourceStore()

      store.addResource('sess-1', makeResource({ resource_id: 'r1' }))

      expect(store.getResourceById('sess-1', 'r1')?.resource_id).toBe('r1')
    })

    it('finds a superseded version nested inside a group', async () => {
      const { useResourceStore } = await import('@/stores/resource')
      const store = useResourceStore()

      store.addResource('sess-1', makeResource({ resource_id: 'r1', timestamp: 100 }))
      store.addResource('sess-1', makeResource({ resource_id: 'r2', timestamp: 200 }))

      const found = store.getResourceById('sess-1', 'r1')
      expect(found).not.toBeNull()
      expect(found.resource_id).toBe('r1')
    })

    it('returns null for an unknown resource id', async () => {
      const { useResourceStore } = await import('@/stores/resource')
      const store = useResourceStore()

      store.addResource('sess-1', makeResource({ resource_id: 'r1' }))
      expect(store.getResourceById('sess-1', 'unknown')).toBeNull()
    })
  })

  describe('openFullViewById', () => {
    it('opens the top-level group by index when the id is the latest version', async () => {
      const { useResourceStore } = await import('@/stores/resource')
      const store = useResourceStore()

      store.addResource('sess-1', makeResource({ resource_id: 'r1', timestamp: 100 }))
      store.addResource('sess-1', makeResource({ resource_id: 'r2', timestamp: 200 }))

      store.openFullViewById('r2', 'sess-1')

      expect(store.fullViewOpen).toBe(true)
      expect(store.currentFullViewResource?.resource_id).toBe('r2')
      expect(store.fullViewTotalResources).toBe(1)
    })

    it('pins a superseded nested version for direct display', async () => {
      const { useResourceStore } = await import('@/stores/resource')
      const store = useResourceStore()

      store.addResource('sess-1', makeResource({ resource_id: 'r1', timestamp: 100 }))
      store.addResource('sess-1', makeResource({ resource_id: 'r2', timestamp: 200 }))

      store.openFullViewById('r1', 'sess-1')

      expect(store.fullViewOpen).toBe(true)
      expect(store.currentFullViewResource?.resource_id).toBe('r1')
      // Pinned single-version display: no carousel navigation across the group.
      expect(store.fullViewTotalResources).toBe(1)
    })

    it('closeFullView clears the pinned resource', async () => {
      const { useResourceStore } = await import('@/stores/resource')
      const store = useResourceStore()

      store.addResource('sess-1', makeResource({ resource_id: 'r1', timestamp: 100 }))
      store.addResource('sess-1', makeResource({ resource_id: 'r2', timestamp: 200 }))

      store.openFullViewById('r1', 'sess-1')
      store.closeFullView()

      expect(store.fullViewOpen).toBe(false)
      expect(store.currentFullViewResource).toBeNull()
    })
  })

  describe('toggleResourceGroup / isResourceGroupExpanded', () => {
    it('toggles expand state per session+group key', async () => {
      const { useResourceStore } = await import('@/stores/resource')
      const store = useResourceStore()

      expect(store.isResourceGroupExpanded('sess-1', 'report.md')).toBe(false)

      store.toggleResourceGroup('sess-1', 'report.md')
      expect(store.isResourceGroupExpanded('sess-1', 'report.md')).toBe(true)

      store.toggleResourceGroup('sess-1', 'report.md')
      expect(store.isResourceGroupExpanded('sess-1', 'report.md')).toBe(false)
    })

    it('is scoped independently per session', async () => {
      const { useResourceStore } = await import('@/stores/resource')
      const store = useResourceStore()

      store.toggleResourceGroup('sess-1', 'report.md')
      expect(store.isResourceGroupExpanded('sess-1', 'report.md')).toBe(true)
      expect(store.isResourceGroupExpanded('sess-2', 'report.md')).toBe(false)
    })
  })

  describe('removeResource (API-backed)', () => {
    it('calls DELETE then optimistically splices local state', async () => {
      const { useResourceStore } = await import('@/stores/resource')
      const store = useResourceStore()

      store.addResource('sess-1', makeResource({ resource_id: 'r1' }))
      apiDeleteMock.mockResolvedValue({ success: true })

      await store.removeResource('sess-1', 'r1')

      expect(apiDeleteMock).toHaveBeenCalledWith('/api/sessions/sess-1/resources/r1')
      expect(store.resourcesForSession('sess-1')).toHaveLength(0)
    })
  })
})
