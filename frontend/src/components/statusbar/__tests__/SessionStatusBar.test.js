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
// opening ONE bounded window around the target turn (stage 3 PR 3C-1: historyBackfill.jumpAround;
// only a slow link asks first, naming the window size); without a usable index (loading, old
// server) it keeps the stage-1 lock until the history is complete.
describe('SessionStatusBar turn navigation (issue #2030)', () => {
  beforeEach(() => apiMock.get.mockReset())
  afterEach(() => vi.restoreAllMocks())

  async function mountBar({ prevTurn, nextIndex = 5, startOffset = null } = {}) {
    const virtualNav = {
      findPrevTurn: vi.fn(() => (typeof prevTurn === 'function' ? prevTurn() : prevTurn)),
      findNextTurn: vi.fn(() => (typeof nextIndex === 'function' ? nextIndex() : (nextIndex == null ? null : { kind: 'loaded', index: nextIndex }))),
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
  const target = (over = {}) => ({ kind: 'unloaded', messageId: 'turn-9', cursor: 'b:1000', ordinal: 9, sizeHint: 1000, ...over })

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
    vi.spyOn(historyBackfill, 'getMode').mockReturnValue('manual') // slow link (derived, not read from the status)
    let step = 0
    const { virtualNav, messageStore } = await mountBar({
      prevTurn: () => (step === 0 ? { kind: 'pending', reason: 'idle' } : { kind: 'loaded', index: 2 }),
      startOffset: 5000,
    })
    await nextTick()
    vi.spyOn(messageStore, 'ensureHistoryIndex').mockImplementation(async () => { step = 1; return null })
    expect(prev().disabled).toBe(false)
    prev().click()
    await flush()
    expect(messageStore.ensureHistoryIndex).toHaveBeenCalledWith(SID)
    expect(virtualNav.scrollToItemIndex).toHaveBeenCalledWith(2, expect.anything())
    historyBackfill.statusBySession.value = new Map()
  })

  describe('jump to a turn that is not loaded', () => {
    const jumpAround = (impl) => vi.spyOn(historyBackfill, 'jumpAround').mockImplementation(impl || (() => ({ promise: Promise.resolve({ status: 'island' }), cancel() {} })))

    it('a fast link proceeds without asking: one around window by the turn cursor, then scrolls to the turn', async () => {
      const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
      const spy = jumpAround()
      const { virtualNav } = await mountBar({ prevTurn: target({ cursor: 'b:4000000', sizeHint: 4_000_000 }), startOffset: 5_000_000 })
      prev().click()
      await flush()
      expect(confirm).not.toHaveBeenCalled()
      expect(spy).toHaveBeenCalledWith(SID, { cursor: 'b:4000000' }, { label: 'turn 9', ordinal: 9 })
      expect(virtualNav.scrollToTurnMessage).toHaveBeenCalledWith('turn-9')
      // the sticky-to-bottom pin is dropped for the jump and re-derived once it ended
      expect(virtualNav.beginTurnJump).toHaveBeenCalledTimes(1)
      expect(virtualNav.endTurnJump).toHaveBeenCalledTimes(1)
      expect(virtualNav.beginTurnJump.mock.invocationCallOrder[0]).toBeLessThan(spy.mock.invocationCallOrder[0])
    })

    it('a far jump on a fast link is the same single window: no confirm, no size question', async () => {
      const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
      const spy = jumpAround()
      await mountBar({ prevTurn: target({ ordinal: 12, sizeHint: 0, cursor: 'b:0' }), startOffset: 90 * MB })
      prev().click()
      await flush()
      expect(confirm).not.toHaveBeenCalled()
      expect(spy).toHaveBeenCalledTimes(1)
    })

    it('Next walks the index too: an unloaded next turn (below an island) opens a window around it', async () => {
      const spy = jumpAround()
      const { virtualNav } = await mountBar({ prevTurn: null, nextIndex: () => target({ messageId: 'turn-30', ordinal: 30, cursor: 'b:300' }), startOffset: 5000 })
      next().click()
      await flush()
      expect(spy).toHaveBeenCalledWith(SID, { cursor: 'b:300' }, { label: 'turn 30', ordinal: 30 })
      expect(virtualNav.scrollToTurnMessage).toHaveBeenCalledWith('turn-30')
    })

    it('a slow link asks first naming the window size; nothing starts while the confirm is open, Cancel never starts it, OK does', async () => {
      vi.spyOn(historyBackfill, 'getMode').mockReturnValue('manual')
      const spy = jumpAround()
      const { virtualNav } = await mountBar({ prevTurn: target({ sizeHint: 0, cursor: 'b:0' }), startOffset: 30 * MB })
        await nextTick()
      const atConfirm = []
      const confirm = vi.spyOn(window, 'confirm').mockImplementation(() => {
        atConfirm.push({ jump: spy.mock.calls.length, begin: virtualNav.beginTurnJump.mock.calls.length, jumpStatus: historyBackfill.statusBySession.value.get(SID)?.jump ?? null })
        return atConfirm.length > 1 // first Cancel, then OK
      })
      prev().click()
      await flush()
      expect(confirm.mock.calls[0][0]).toBe('Jump to turn 9? Loads about 0.5 MB of history')
      expect(atConfirm[0]).toEqual({ jump: 0, begin: 0, jumpStatus: null })
      expect(spy).not.toHaveBeenCalled() // Cancel: nothing started
      expect(prev().disabled).toBe(false) // not left stuck in the jumping state
      prev().click()
      await flush()
      expect(atConfirm[1]).toEqual({ jump: 0, begin: 0, jumpStatus: null })
      expect(spy).toHaveBeenCalledTimes(1) // only after OK
      expect(virtualNav.beginTurnJump).toHaveBeenCalledTimes(1)
      historyBackfill.statusBySession.value = new Map()
    })

    it('a cancelled / superseded jump (or a session switch) leaves the list alone and re-enables the buttons', async () => {
      const err = Object.assign(new Error('Jump cancelled'), { reason: 'aborted' })
      jumpAround(() => ({ promise: Promise.reject(err), cancel() {} }))
      const { virtualNav } = await mountBar({ prevTurn: target({ sizeHint: 4_000_000 }), startOffset: 5_000_000 })
      prev().click()
      await flush()
      expect(virtualNav.scrollToTurnMessage).not.toHaveBeenCalled()
      expect(virtualNav.endTurnJump).toHaveBeenCalledTimes(1) // sticky re-derived from where the list really is
      expect(prev().disabled).toBe(false)
    })

    it('shows a spinner on the Prev button while loading and allows one jump at a time', async () => {
      let resolveJump
      jumpAround(() => ({ promise: new Promise(r => { resolveJump = r }), cancel() {} }))
      await mountBar({ prevTurn: target({ sizeHint: 1_000_000 }), startOffset: 5_000_000 })
      prev().click()
      await flush()
      expect(prev().disabled).toBe(true)
      expect(document.querySelector('[data-testid=turn-jump-progress]')).toBeTruthy()
      resolveJump({ status: 'island' })
      await flush()
      expect(document.querySelector('[data-testid=turn-jump-progress]')).toBeNull()
    })
  })
})
