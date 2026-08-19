import { describe, it, expect, vi } from 'vitest'
import { ref } from 'vue'
import { useVirtualNavigation } from '@/composables/useVirtualNavigation'

// Issue #1748 (stage: offset-model): a minimal fake virtualizer — just enough of
// @tanstack/virtual-core's public surface (scrollToIndex, getVirtualItems, getTotalSize) for the
// composable to drive — so these tests exercise the composable's own control flow in isolation,
// independent of the real library or any DOM/Vue-component rendering.
function makeFakeVirtualizer({ mountedIndexes = [] } = {}) {
  return {
    scrollToIndex: vi.fn(),
    getTotalSize: vi.fn(() => 0),
    getVirtualItems: vi.fn(() => mountedIndexes.map(index => ({ index }))),
  }
}

describe('useVirtualNavigation', () => {
  it('returns false without calling scrollToIndex when the virtualizer is unavailable', async () => {
    const virtualizerRef = ref(null)
    const { scrollToItemIndex } = useVirtualNavigation(virtualizerRef)

    const result = await scrollToItemIndex(3)

    expect(result).toBe(false)
  })

  it('returns false for a null/negative index without calling scrollToIndex', async () => {
    const fake = makeFakeVirtualizer({ mountedIndexes: [0, 1, 2] })
    const virtualizerRef = ref(fake)
    const { scrollToItemIndex } = useVirtualNavigation(virtualizerRef)

    expect(await scrollToItemIndex(null)).toBe(false)
    expect(await scrollToItemIndex(-1)).toBe(false)
    expect(fake.scrollToIndex).not.toHaveBeenCalled()
  })

  it('calls scrollToIndex and resolves true once the target index is in the rendered range', async () => {
    // Stage 1 (overscan = full item count): the target is always immediately in range, so this
    // is the common case — the two-phase "estimate then confirm" pattern only matters once
    // Stage 2 introduces real culling.
    const fake = makeFakeVirtualizer({ mountedIndexes: [3, 4, 5] })
    const virtualizerRef = ref(fake)
    const { scrollToItemIndex } = useVirtualNavigation(virtualizerRef)

    const result = await scrollToItemIndex(4, { align: 'center' })

    expect(fake.scrollToIndex).toHaveBeenCalledWith(4, { align: 'center', behavior: 'auto' })
    expect(result).toBe(true)
  })

  it('forces a fresh measurement pass (getTotalSize) before scrollToIndex to avoid a stale measurementsCache read', async () => {
    const fake = makeFakeVirtualizer({ mountedIndexes: [0] })
    const virtualizerRef = ref(fake)
    const { scrollToItemIndex } = useVirtualNavigation(virtualizerRef)

    await scrollToItemIndex(0)

    expect(fake.getTotalSize).toHaveBeenCalled()
  })

  it('resolves false when the target index never appears in the rendered range within the retry budget', async () => {
    const fake = makeFakeVirtualizer({ mountedIndexes: [] }) // never mounts
    const virtualizerRef = ref(fake)
    const { scrollToItemIndex } = useVirtualNavigation(virtualizerRef)

    const result = await scrollToItemIndex(10)

    expect(result).toBe(false)
    expect(fake.getVirtualItems).toHaveBeenCalled()
  })

  it('waitForIndexMounted returns false if the virtualizer disappears mid-poll (e.g. component unmounted)', async () => {
    const fake = makeFakeVirtualizer({ mountedIndexes: [] })
    const virtualizerRef = ref(fake)
    const { waitForIndexMounted } = useVirtualNavigation(virtualizerRef)

    const promise = waitForIndexMounted(0)
    virtualizerRef.value = null
    const result = await promise

    expect(result).toBe(false)
  })
})
