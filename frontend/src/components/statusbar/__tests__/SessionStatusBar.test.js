import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { nextTick } from 'vue'
import { renderWithStores } from '@/test-utils/render'
import { makeSession } from '@/test-utils/factories'
import { historyBackfill } from '@/composables/useHistoryBackfill'
import SessionStatusBar from '@/components/statusbar/SessionStatusBar.vue'

const apiMock = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn() }))
vi.mock('@/utils/api', () => ({ api: apiMock, getAuthToken: vi.fn().mockReturnValue(null) }))
vi.mock('@/composables/useNotifications', () => ({ notify: vi.fn() }))

const SID = 'sess-1'
const MB = 1024 * 1024
const flush = () => new Promise(resolve => setTimeout(resolve, 0))

// Issue #2030: turn navigation over the server history index. Prev works on a tail-only load by
// extending the loaded suffix (guarded by a confirm for big/slow jumps); without a usable index
// (loading, old server) it keeps the stage-1 lock until the history is complete.
describe('SessionStatusBar turn navigation (issue #2030)', () => {
  beforeEach(() => apiMock.get.mockReset())
  afterEach(() => vi.restoreAllMocks())

  async function mountBar({ prevTurn, nextIndex = 5, startOffset = null } = {}) {
    const virtualNav = {
      findPrevTurn: vi.fn(() => (typeof prevTurn === 'function' ? prevTurn() : prevTurn)),
      findNextTurnIndex: vi.fn(() => nextIndex),
      scrollToItemIndex: vi.fn().mockResolvedValue(true),
      scrollToTurnMessage: vi.fn().mockResolvedValue(true),
      beginTurnJump: vi.fn(),
      endTurnJump: vi.fn(),
    }
    const rendered = renderWithStores(SessionStatusBar, { props: { sessionId: SID, virtualNav } })
    const { useSessionStore } = await import('@/stores/session')
    const { useMessageStore } = await import('@/stores/message')
    useSessionStore(rendered.pinia).sessions.set(SID, makeSession({ session_id: SID }))
    const messageStore = useMessageStore(rendered.pinia)
    if (startOffset != null) {
      // a tail-only load whose loaded suffix starts at `startOffset`
      apiMock.get.mockImplementation(async () => ({
        messages: [{ type: 'user', message_id: 'm-tail', timestamp: 1, content: 'x', metadata: {} }],
        start_cursor: `b:${startOffset}`, has_more_before: true, history_id: 'h1', total_bytes: startOffset + 1000, snapped: true,
      }))
      await messageStore.loadMessages(SID, null, 0, { window: 'tail', signal: new AbortController().signal })
    }
    await nextTick()
    return { ...rendered, messageStore, virtualNav }
  }

  const prev = () => document.querySelector('[aria-label="Jump to previous turn"]')
  const next = () => document.querySelector('[aria-label="Jump to next turn"]')
  const target = (over = {}) => ({ kind: 'unloaded', messageId: 'turn-9', ordinal: 9, sizeHint: 1000, ...over })

  it('a loaded previous turn scrolls straight to it', async () => {
    const { virtualNav } = await mountBar({ prevTurn: { kind: 'loaded', index: 1 } })
    expect(prev().disabled).toBe(false)
    expect(next().disabled).toBe(false)
    prev().click()
    await flush()
    expect(virtualNav.scrollToItemIndex).toHaveBeenCalledWith(1, { align: 'start', behavior: 'auto' })
  })

  it('no earlier turn disables Prev; Next stays independent of the history state', async () => {
    await mountBar({ prevTurn: null, nextIndex: 7, startOffset: 5000 })
    expect(prev().disabled).toBe(true)
    expect(next().disabled).toBe(false) // history is NOT complete: Next only needs loaded rows
  })

  it('a tail-only load with a ready index enables Prev right away', async () => {
    await mountBar({ prevTurn: target(), startOffset: 5000 })
    expect(prev().disabled).toBe(false)
    expect(prev().title).toBe('Jump to previous user message or comm')
  })

  it('an unavailable index (old server / failure) restores the stage-1 behaviour: BOTH buttons locked until the history is complete', async () => {
    const { messageStore } = await mountBar({ prevTurn: { kind: 'loaded', index: 1 }, startOffset: 5000 })
    expect(prev().disabled).toBe(false) // loaded turn above, index not yet known: usable
    apiMock.get.mockImplementation(async (url) => {
      if (!String(url).includes('/history_index')) return {}
      const err = new Error('not found'); err.status = 404; throw err
    })
    await messageStore.ensureHistoryIndex(SID)
    await nextTick()
    expect(messageStore.getHistoryIndex(SID).status).toBe('unavailable')
    expect(prev().disabled).toBe(true)
    expect(next().disabled).toBe(true)
    expect(prev().title).toBe('Loading older history…')
    expect(next().title).toBe('Loading older history…')
  })

  it.each(['loading', 'unavailable'])('while the index is %s, Prev is locked with the stage-1 tooltip', async (reason) => {
    await mountBar({ prevTurn: { kind: 'pending', reason }, startOffset: 5000 })
    expect(prev().disabled).toBe(true)
    expect(prev().title).toBe('Loading older history…')
  })

  it('an idle index on a normal link is about to be fetched automatically: Prev stays locked until it lands', async () => {
    await mountBar({ prevTurn: { kind: 'pending', reason: 'idle' }, startOffset: 5000 })
    expect(prev().disabled).toBe(true)
    expect(prev().title).toBe('Loading older history…')
  })

  it('an idle index on a slow link keeps Prev enabled; pressing it fetches the index first', async () => {
    let step = 0
    const { virtualNav, messageStore } = await mountBar({
      prevTurn: () => (step === 0 ? { kind: 'pending', reason: 'idle' } : { kind: 'loaded', index: 2 }),
      startOffset: 5000,
    })
    historyBackfill.statusBySession.value = new Map([[SID, { mode: 'manual', paused: null, loading: false, error: null }]])
    await nextTick()
    vi.spyOn(messageStore, 'ensureHistoryIndex').mockImplementation(async () => { step = 1; return null })
    expect(prev().disabled).toBe(false)
    prev().click()
    await flush()
    expect(messageStore.ensureHistoryIndex).toHaveBeenCalledWith(SID)
    expect(virtualNav.scrollToItemIndex).toHaveBeenCalledWith(2, expect.anything())
    historyBackfill.statusBySession.value = new Map()
  })

  describe('jump to a turn older than the loaded suffix', () => {
    it('a small jump on a fast link proceeds without asking, then scrolls to the loaded turn', async () => {
      const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
      const requestUntil = vi.spyOn(historyBackfill, 'requestUntil').mockReturnValue({ promise: Promise.resolve(), cancel() {} })
      const { virtualNav } = await mountBar({ prevTurn: target({ sizeHint: 4_000_000 }), startOffset: 5_000_000 })
      prev().click()
      await flush()
      expect(confirm).not.toHaveBeenCalled()
      expect(requestUntil).toHaveBeenCalledWith(SID, 'turn-9', { ordinal: 9, fromOffset: 5_000_000, toOffset: 4_000_000 })
      expect(virtualNav.scrollToTurnMessage).toHaveBeenCalledWith('turn-9')
      // the sticky-to-bottom pin is dropped for the jump and re-derived once it ended
      expect(virtualNav.beginTurnJump).toHaveBeenCalledTimes(1)
      expect(virtualNav.endTurnJump).toHaveBeenCalledTimes(1)
      expect(virtualNav.beginTurnJump.mock.invocationCallOrder[0]).toBeLessThan(requestUntil.mock.invocationCallOrder[0])
    })

    it('a big jump asks first with the estimated size (default 5x wire ratio); declining loads nothing', async () => {
      const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
      const requestUntil = vi.spyOn(historyBackfill, 'requestUntil')
      const { virtualNav } = await mountBar({ prevTurn: target({ ordinal: 12, sizeHint: 0 }), startOffset: 30 * MB })
      prev().click()
      await flush()
      expect(confirm).toHaveBeenCalledWith('Jump to turn 12? Loads about 6.0 MB of older history')
      expect(requestUntil).not.toHaveBeenCalled()
      expect(virtualNav.scrollToTurnMessage).not.toHaveBeenCalled()
      expect(prev().disabled).toBe(false) // not left stuck in the jumping state
    })

    it('nothing is fetched or started while the confirm is open; OK starts the jump, Cancel never does', async () => {
      const beforeRequests = () => apiMock.get.mock.calls.filter(([url]) => String(url).includes('before='))
      const requestUntil = vi.spyOn(historyBackfill, 'requestUntil').mockReturnValue({ promise: Promise.resolve(), cancel() {} })
      const { virtualNav } = await mountBar({ prevTurn: target({ sizeHint: 0 }), startOffset: 30 * MB })
      const atConfirm = []
      vi.spyOn(window, 'confirm').mockImplementation(() => {
        // state at the moment the dialog is open: no jump, no request, no jump status/banner
        atConfirm.push({
          requestUntil: requestUntil.mock.calls.length, begin: virtualNav.beginTurnJump.mock.calls.length,
          beforeRequests: beforeRequests().length, jumpStatus: historyBackfill.statusBySession.value.get(SID)?.jump ?? null,
        })
        return atConfirm.length > 1 // first Cancel, then OK
      })
      prev().click()
      await flush()
      expect(atConfirm[0]).toEqual({ requestUntil: 0, begin: 0, beforeRequests: 0, jumpStatus: null })
      expect(requestUntil).not.toHaveBeenCalled() // Cancel: nothing started
      expect(virtualNav.beginTurnJump).not.toHaveBeenCalled()
      expect(beforeRequests()).toHaveLength(0)
      prev().click()
      await flush()
      expect(atConfirm[1]).toEqual({ requestUntil: 0, begin: 0, beforeRequests: 0, jumpStatus: null })
      expect(requestUntil).toHaveBeenCalledTimes(1) // only after OK
      expect(virtualNav.beginTurnJump).toHaveBeenCalledTimes(1)
    })

    it('uses the observed wire ratio once a page has been measured', async () => {
      const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
      const { messageStore } = await mountBar({ prevTurn: target({ sizeHint: 0 }), startOffset: 30 * MB })
      vi.spyOn(messageStore, 'getWireRatio').mockReturnValue(10)
      prev().click()
      await flush()
      expect(confirm).toHaveBeenCalledWith('Jump to turn 9? Loads about 3.0 MB of older history')
    })

    it('accepting a big jump requests the turn and scrolls once loaded', async () => {
      vi.spyOn(window, 'confirm').mockReturnValue(true)
      const requestUntil = vi.spyOn(historyBackfill, 'requestUntil').mockReturnValue({ promise: Promise.resolve(), cancel() {} })
      const { virtualNav } = await mountBar({ prevTurn: target({ sizeHint: 0 }), startOffset: 30 * MB })
      prev().click()
      await flush()
      expect(requestUntil).toHaveBeenCalledTimes(1)
      expect(virtualNav.scrollToTurnMessage).toHaveBeenCalledWith('turn-9')
    })

    it('a slow link always asks, even for a tiny jump', async () => {
      const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
      await mountBar({ prevTurn: target({ sizeHint: 4_990_000 }), startOffset: 5_000_000 })
      historyBackfill.statusBySession.value = new Map([[SID, { mode: 'manual', paused: null, loading: false, error: null }]])
      await nextTick()
      prev().click()
      await flush()
      expect(confirm).toHaveBeenCalledTimes(1)
      expect(confirm.mock.calls[0][0]).toMatch(/^Jump to turn 9\? Loads about \d+ KB of older history$/)
      historyBackfill.statusBySession.value = new Map()
    })

    it('a cancelled jump (or a session switch) leaves the list alone and re-enables the buttons', async () => {
      const err = Object.assign(new Error('Jump cancelled'), { reason: 'cancelled' })
      vi.spyOn(historyBackfill, 'requestUntil').mockReturnValue({ promise: Promise.reject(err), cancel() {} })
      const { virtualNav } = await mountBar({ prevTurn: target({ sizeHint: 4_000_000 }), startOffset: 5_000_000 })
      prev().click()
      await flush()
      expect(virtualNav.scrollToTurnMessage).not.toHaveBeenCalled()
      expect(virtualNav.endTurnJump).toHaveBeenCalledTimes(1) // sticky re-derived from where the list really is
      expect(prev().disabled).toBe(false)
    })

    it('shows the jump progress on the Prev button while loading', async () => {
      let resolveJump
      vi.spyOn(historyBackfill, 'requestUntil').mockReturnValue({ promise: new Promise(r => { resolveJump = r }), cancel() {} })
      await mountBar({ prevTurn: target({ sizeHint: 1_000_000 }), startOffset: 5_000_000 })
      prev().click()
      await flush()
      expect(prev().disabled).toBe(true) // one jump at a time
      historyBackfill.statusBySession.value = new Map([[SID, { mode: 'auto', jump: { messageId: 'turn-9', ordinal: 9, fromOffset: 5_000_000, toOffset: 1_000_000 } }]])
      await nextTick()
      expect(document.querySelector('[data-testid=turn-jump-progress]').textContent).toBe('0%')
      resolveJump()
      await flush()
      historyBackfill.statusBySession.value = new Map()
    })
  })
})
