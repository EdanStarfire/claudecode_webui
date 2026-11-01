import { defineStore } from 'pinia'
import { ref, computed, readonly, watch, markRaw, toRaw, shallowRef, shallowReactive } from 'vue'
import { api } from '../utils/api'
import { useSessionStore } from './session'
import { useTaskStore } from './task'
import { useBackgroundTasksStore } from './backgroundTasks'
import { correlateHooks } from '../utils/hookCorrelation'
import { mergeOlderPage, mergeToolCallFields } from '../utils/historyMerge'
import { ISLAND_PAGE_BYTES, intervalsTouch, windowBytes } from '../utils/historyIntervals'
import { getHistoryBudgetBytes, trimTargetBytes } from '../utils/historyBudget'
import { cursorOffset } from '../utils/turnNavigation'
import { getAgentColor, getAssistantRowColor, slugifyAgentName } from '../composables/useAgentColor'
import { pushDebugEvent, flushDebugBuffer } from '../composables/useDebugBuffer'

// Backend's 7-value tool status vocabulary -> the 5-value frontend-display vocabulary.
// Module scope so every site that needs to translate a backend status (ToolCallRecord
// updates, and handlePermissionResponse's own allow/deny-driven status) shares the exact
// same table instead of maintaining a second, independently-drifting mapping.
const TOOL_STATUS_MAP = {
  'pending': 'pending',
  'awaiting_permission': 'permission_required',
  'running': 'executing',
  'completed': 'completed',
  'failed': 'error',
  'denied': 'completed',  // Denied shows as completed with special styling
  'interrupted': 'completed'  // Interrupted shows as completed with orphaned styling
}

/**
 * Message Store - Manages messages and tool calls per session
 */
export const useMessageStore = defineStore('message', () => {
  // ========== STATE ==========

  // Issue #2030 stage 4 PR 4b: the loaded history is NOT deeply reactive. A deep `ref` wrapped
  // every message and tool entry in a Proxy (Vue Dep/Link bookkeeping was ~29 % of the heap and
  // every O(n) pass paid a proxy `get` per field). Instead: the per-session Maps are shallowRefs
  // (every mutation path replaces the Map to notify — `x.value = new Map(x.value)` or
  // `_installPageAndNotify`), messages are `markRaw` records that are NEVER mutated after
  // insertion (a changed message is replaced as a whole), and tool entries are `shallowReactive`
  // so a top-level field write (status, result, isExpanded, ...) still re-renders exactly the
  // readers of that field. Nested values (input/result/appliedUpdates/suggestions/_orphanedInfo)
  // are only ever REPLACED, never mutated in place. New writers must live in this store.

  // Messages per session (sessionId -> Message[])
  const messagesBySession = shallowRef(new Map())

  // Tool calls per session (sessionId -> ToolCall[])
  const toolCallsBySession = shallowRef(new Map())

  const permissionToToolMap = ref(new Map())

  // Issue #1000: Event cursors returned by REST /messages endpoint.
  // Used by websocket.connectSession() to start polling from the correct position.
  const loadedEventCursors = new Map()

  // Issue #2140: single shared per-session in-progress history-load tracker — the one
  // source of truth for "is there a current in-flight paginated history load for this
  // session, and here's its controller." Both session.js's retryHydration() and
  // polling.js's resync trigger read/write this same map via claimHydrationLoad()
  // instead of each keeping an independent AbortController map (the design this issue's
  // review explicitly flagged as the likeliest source of a Retry-vs-resync abort race).
  // Non-reactive — nothing renders off this directly (same pattern as loadedEventCursors).
  // Map<sessionId, { target, nextOffset, epoch, totalCount, controller }>
  const _hydrationLoadStateBySession = new Map()

  // Issue #2030 (stage 1b): tail-first history. The browser holds ONE contiguous suffix of a
  // session's (or archive's) history; this tracks where that suffix starts so older pages can
  // be fetched `before` it. Map<sessionId, {
  //   baseUrl,        // messages endpoint older pages are fetched from (live session or archive)
  //   startCursor,    // opaque cursor of the oldest loaded record
  //   hasMoreBefore,  // false once the whole history is loaded ("complete")
  //   historyId,      // changes if the server-side file was rewritten (cleanup/reset/migration)
  //   totalBytes,     // size of the history file at window time (progress denominator)
  //   snapped,        // window start landed on a user-turn boundary
  //   generation,     // bumps whenever the loaded suffix is replaced (backfill must restart)
  // }>. No entry == a legacy/fully-loaded session (complete).
  const historyBySession = ref(new Map())
  // Issue #2030 (stage 2 PR B): the server's history index (GET .../history_index) — the turn
  // list and compaction-group keys of the WHOLE history, which a tail-only load cannot derive.
  // Map<sessionId, { status: 'loading' | 'ready' | 'unavailable', historyId,
  //                  turns: [[cursor, messageId, sizeHint]] (markRaw: read-only, can be 50k+ entries) }>.
  // Dropped whenever the loaded suffix is replaced (reset / history_id change / resync gap-replace).
  // (The payload's `compactions` are not stored: compaction groups are keyed by message_id from the
  // loaded records themselves, see hooksForCompaction.)
  const historyIndexBySession = ref(new Map())
  // Non-reactive: Map<sessionId, { controller, promise, retryTimer }> for the in-flight request.
  const _historyIndexRequests = new Map()
  // Issue #2030 (stage 3 PR 3C-1): the pinned ISLAND — at most one window of history fetched by
  // `around=` for a jump (and grown by `before=` / `after=` pages) that is NOT contiguous with the
  // live suffix above. The suffix stays exactly what it was (messagesBySession & friends, the hot
  // path); the island lives in its own record target and is read through the loaded* union getters.
  // Map<sessionId, { id, baseUrl, source, historyId, startCursor, endCursor, anchorCursor,
  //   hasMoreBefore, hasMoreAfter, snapped, parentUnresolved, bytes, rev,
  //   messages, toolCalls }> — `messages`/`toolCalls` are snapshots of the raw target below
  // (re-sliced on every change so Vue sees a new array). Dropped on a history_id change, a
  // reset/clear, a new jump (replaced), or when it touches the suffix (merged into it).
  const islandBySession = shallowRef(new Map())
  // Non-reactive truth behind each island: { messages, messageIndex, toolCalls, toolIndex, retryIndex }.
  const _islandTargets = new Map()
  // One in-flight island request (around / before / after) per session; a new one aborts the old.
  const _islandControllers = new Map()
  // Latest-wins values the island's records determined (see _replayHistoryRecords): Map<sessionId, { stop, deferred, launch }>.
  const _islandLatest = new Map()
  // Island hook correlation, cached per island revision: Map<sessionId, { rev, result }>.
  const _islandHookCache = new Map()
  let _islandSeq = 0
  // Issue #2030 stage 4 PR 4d: exact cut points for the memory budget. One entry per older page that
  // was prepended to the suffix (`applyFetchedOlderPage`, or an island merging into it), NEWEST FIRST:
  // `{ startCursor, endCursor, count }` = the page covers [startCursor, endCursor) of the history file
  // and added `count` messages at the front. `endCursor` is exactly the cursor the next `before=` fetch
  // would have started from, so trimming whole segments off the OLD end needs no lookup: the new
  // `history.startCursor` IS the cut segment's `endCursor`. Non-reactive; valid for one `generation`.
  // Map<sessionId, { generation, segments }>
  const _ledgerBySession = new Map()
  // Same idea for the island: its pages in FILE order (base `around=` window, `before=` pages unshifted in
  // front, `after=` pages pushed behind), each `{ startCursor, endCursor, count, region }`.
  // Map<sessionId, { islandId, segments, minRegion, maxRegion }>
  const _islandLedger = new Map()
  // A tool entry may have records in several regions of the file (a start, later updates, an interrupt
  // marker). A trim may only release an entry whose records ALL lie in the released region, or the later
  // ones would be lost with it. Suffix: Map<sessionId, Map<toolUseId, page>> — the number of the OLDEST-
  // arrived (newest in file order = smallest) older page that carried the entry's records; absent == the
  // tail window or live (never released). Cleared for an id by any record applied outside an older-page
  // replay. Island: Map<sessionId, Map<toolUseId, { lo, hi }>> — range of region ordinals it appears in
  // (base 0, `before` pages -1.., `after` pages 1..).
  const _toolPageBySession = new Map()
  // Bytes appended to the history file by LIVE records since `history.totalBytes` was read (window / resync):
  // an estimate (JSON length) so the budget sees live growth. Reset whenever totalBytes is refreshed.
  const _liveBytesBySession = new Map()
  const _islandToolRegions = new Map()
  let _replayingOlder = false
  const HISTORY_INDEX_TIMEOUT_MS = 30000  // server waiter cap is 20 s, then 503
  const HISTORY_INDEX_RETRY_MS = 5000
  // A transient failure (503 index_building / timeout, twice) is retried later, a few times: it must
  // not lock turn navigation for a whole session. Non-reactive Map<sessionId, { timer, failures }>.
  const HISTORY_INDEX_COOLDOWN_MS = 30000
  const HISTORY_INDEX_MAX_FAILURES = 3
  const _historyIndexCooldowns = new Map()
  // One in-flight backfill page request per session; claiming aborts the previous one.
  const _backfillControllers = new Map()
  // Tool ids that arrived via an older (backfilled) page, per session. Stores that react to "a
  // tool just completed" (editHistory's auto-refresh) must not treat history as news.
  // Non-reactive. Map<sessionId, Set<toolUseId>>
  const _backfilledToolIds = new Map()

  // Issue #2131: per-session identity indices mirroring messagesBySession/toolCallsBySession
  // 1:1, so identity/retry/tool lookups in applyRecord are O(1) instead of O(n) array scans.
  // Non-reactive (same pattern as loadedEventCursors above) — nothing renders off these
  // directly, and walking/writing them should never pay Vue proxy overhead.
  // Map<sessionId, Map<dedupKey, arrayIndex>>
  const messageIndexBySession = new Map()
  // Map<sessionId, Map<toolUseId, arrayIndex>>
  const toolIndexBySession = new Map()
  // Map<sessionId, Map<retryMessageId, arrayIndex>>
  const retryIndexBySession = new Map()

  // Launch timestamp tracking (sessionId -> Unix timestamp in seconds)
  // Populated from client_launched system messages for uptime calculation
  const launchTimestampBySession = ref(new Map())

  // Issue #662: Track last stop_reason per session for truncation banner
  const lastStopReasonBySession = ref(new Map())

  // Issue #1300: Track deferred tool use per session for deferral banner
  // Map<sessionId, { id, name, input } | null>
  const deferredToolUseBySession = ref(new Map())

  // Issue #1746 (stage: subagents) / #1765: task_id-first background-agent (subagent)
  // tracking. Replaces the old tool_use_id-keyed taskActivityByToolUseId, which could not
  // bridge a resumed leg (its own tool_use_id) back to the agent's earlier leg(s).
  // Map<task_id, { task_id, session_id, legs: TaskLeg[] }> — mirrors the backend
  // TaskLegRegistry's TaskLegEntry shape (src/task_registry.py).
  const taskLegsByTaskId = ref(new Map())
  // Map<tool_use_id, task_id> — bridges a launch/resume Task tool_use to its task_id once
  // task_started arrives (one entry per leg, since every leg has its own launching tool_use).
  // Before task_started arrives for a brand new launch, a launch anchor keys provisionally
  // on its own tool_use_id (this map simply has no entry for it yet).
  const taskIdByLaunchToolUseId = ref(new Map())
  // Map<task_id, Map<legIndex, Message[]>> — narration (thinking/text) captured from subagent
  // assistant messages (metadata.parent_tool_use_id set) that MessageList.vue would otherwise
  // silently drop (issue #1671). Consumed by SubagentLegTranscript.vue.
  const narrationByTaskIdAndLeg = ref(new Map())

  const TASK_LIFECYCLE_SUBTYPES = new Set(['task_started', 'task_progress', 'task_notification', 'task_updated'])
  const TERMINAL_TASK_STATUSES = new Set(['completed', 'failed', 'stopped', 'killed'])
  const TASK_STATUS_DISPLAY_MAP = { killed: 'stopped' }
  // Mirrors backend NON_AGENT_TASK_TYPES (src/task_registry.py): task_started frames with one
  // of these task_types are not background agents and must not be registered as a leg. Missing/
  // undefined task_type still defaults to "is an agent" for backward compatibility.
  const NON_AGENT_TASK_TYPES = new Set(['local_bash'])

  // Issue #1955: cosmetic-only live-typing preview, per session (in-memory only, never
  // persisted, never a messagesBySession entry, never identity-bearing). `active` is true from
  // message_start until message_stop (drives the caret); content/thinking are display
  // accumulators only, never read by dedup/identity logic. pendingText/pendingThinking/rafHandle
  // are the rAF-batched flush scratch state.
  // Map<sessionId, { active, content, thinking, pendingText, pendingThinking, rafHandle }>
  const streamingPreviewBySession = ref(new Map())

  // Issue #1350: Hook correlation cache — non-reactive, internal memoization only.
  // Shape: Map<sessionId, { result: HookCorrelationResult, messageCount: number, lastId: string|null }>
  const _hookCorrelationCache = new Map()

  // Issue #1676: Dismissed background-agent notifications, per session.
  // Not persisted — dismissal resets on reload (session-local UI state only).
  const dismissedAgentNotifications = ref(new Map())

  // ========== COMPUTED ==========

  // Current session's messages. NOTE (issue #2030 4b): returns the raw per-session array, whose
  // identity survives an in-place append — do not build computeds/watchers on `.value.length`;
  // read the store getter (loadedMessages) inside the watcher getter instead.
  const currentMessages = computed(() => {
    const sessionStore = useSessionStore()
    return messagesBySession.value.get(sessionStore.currentSessionId) || []
  })

  // Current session's tool calls
  const currentToolCalls = computed(() => {
    const sessionStore = useSessionStore()
    return toolCallsBySession.value.get(sessionStore.currentSessionId) || []
  })

  // ========== ACTIONS ==========

  // Issue #1747: defensive ceiling on fetchAllMessagePages() looping — order of
  // hundreds of pages, purely to prevent a runaway request storm if the backend
  // ever violates its has_more contract. Not expected to trigger under real usage.
  const MAX_PAGINATION_ITERATIONS = 500

  // Issue #2140: default per-page inactivity timeout, used only when a caller invokes the
  // paginated path without specifying one (session.js/polling.js always pass their own
  // HISTORY_PAGE_INACTIVITY_TIMEOUT_MS explicitly; this is a safety-net default for any
  // other/direct caller, e.g. tests exercising loadMessages() in isolation).
  const DEFAULT_HISTORY_PAGE_INACTIVITY_MS = 30000

  /**
   * Issue #2140: aborts any existing in-flight history load for this session (if present)
   * and installs + returns a fresh AbortController for a new attempt. The single place
   * both retryHydration() (session.js) and the resync trigger (polling.js) go through
   * before starting a new loadMessages() call, so abort-before-start is enforced in one
   * place regardless of which side initiates — see _hydrationLoadStateBySession's own
   * comment above for why this collapses what would otherwise be two independently
   * maintained controller maps into one.
   */
  function claimHydrationLoad(sessionId) {
    const existing = _hydrationLoadStateBySession.get(sessionId)
    const controller = new AbortController()
    if (existing) {
      existing.controller.abort()
      existing.controller = controller
    } else {
      _hydrationLoadStateBySession.set(sessionId, {
        // Issue #2140 (builder-review fix): seed from whatever's ALREADY installed for this
        // session (if anything) rather than always an empty target. A fresh claim with
        // nothing to seed from (the common first-ever-load case) just gets an empty target,
        // same as before. But a resync (polling.js's _resyncSession, always resume:true) of
        // an already-fully-loaded session hits this same "no pending entry" branch — without
        // seeding, its first re-fetched page would install via _installPageAndNotify() with
        // only that one page's worth of records, visibly shrinking the message list before it
        // grows back page by page. Seeding from the current arrays means every re-fetched
        // page's records are already present (deduped via the seeded index Maps), so the
        // resync's redundant-but-necessary re-fetch-from-offset-0 never visibly regresses
        // what's already on screen.
        target: _seedRecordTargetFromCurrent(sessionId),
        nextOffset: 0,
        epoch: undefined,
        totalCount: 0,
        controller,
      })
    }
    return controller
  }

  /**
   * Issue #2140: builds a _createRecordTarget()-shaped accumulator pre-populated from this
   * session's CURRENTLY installed messages/tool calls/index Maps, or a genuinely empty one
   * if nothing is installed yet (the ordinary first-ever-load case). See claimHydrationLoad()
   * for why this matters specifically for a resync of an already-loaded session.
   */
  function _seedRecordTargetFromCurrent(sessionId) {
    const messages = messagesBySession.value.get(sessionId)
    const toolCalls = toolCallsBySession.value.get(sessionId)
    if (!messages && !toolCalls) return _createRecordTarget()
    return {
      messages: messages ? [...messages] : [],
      toolCalls: toolCalls ? [...toolCalls] : [],
      messageIndex: new Map(messageIndexBySession.get(sessionId) || []),
      toolIndex: new Map(toolIndexBySession.get(sessionId) || []),
      retryIndex: new Map(retryIndexBySession.get(sessionId) || []),
    }
  }

  /** Issue #2140: true while a paginated history load for this session hasn't finished. */
  function hasPendingHistoryLoad(sessionId) {
    return _hydrationLoadStateBySession.has(sessionId)
  }

  /**
   * Issue #2140: shared per-page apply step for both the single-shot (`limit` given) and
   * paginated (no `limit`) loadMessages() branches — capture init data, then replay each
   * record into `target` without triggering reactivity (see applyRecord()'s own comment).
   */
  function _applyPageToTarget(sessionId, pageMessages, target) {
    pageMessages.forEach(message => {
      // Capture init data for session info modal — not a per-record store side effect, so it
      // stays here rather than in applyRecord (same split as polling.js's own init-data
      // capture ahead of its applyRecord() call for the live path).
      if (message.type === 'system' &&
          (message.subtype === 'init' || message.metadata?.subtype === 'init') &&
          message.metadata?.init_data) {
        const sessionStore = useSessionStore()
        sessionStore.storeInitData(sessionId, message.metadata.init_data)
      }

      // notify:false — see applyRecord()'s own comment: avoid O(N) reactive broadcasts while
      // replaying a page's worth of history; _installPageAndNotify() covers all of them with
      // one reassignment per page instead.
      applyRecord(sessionId, message, 'load', { notify: false, target })
    })
  }

  /**
   * Issue #2140: installs `target` as this session's real state and bumps reactivity once —
   * shared by the single-shot branch (once) and fetchAllMessagePages's per-page loop (once
   * per completed page, which is what makes "pages already received stay visible/retained"
   * true for both on-screen progress and a resumed Retry/resync).
   */
  function _installPageAndNotify(sessionId, target) {
    _installRecordTarget(sessionId, target)
    messagesBySession.value = new Map(messagesBySession.value)
    toolCallsBySession.value = new Map(toolCallsBySession.value)
    lastStopReasonBySession.value = new Map(lastStopReasonBySession.value)
    deferredToolUseBySession.value = new Map(deferredToolUseBySession.value)
  }

  // ========== TAIL-FIRST HISTORY (Issue #2030, stage 1b) ==========

  function _setHistory(sessionId, patch) {
    const next = new Map(historyBySession.value)
    next.set(sessionId, { ...(next.get(sessionId) || {}), ...patch })
    historyBySession.value = next
  }

  function _deleteHistory(sessionId) {
    _dropIsland(sessionId)
    _ledgerBySession.delete(sessionId)
    _toolPageBySession.delete(sessionId)
    _liveBytesBySession.delete(sessionId)
    _dropHistoryIndex(sessionId)
    if (!historyBySession.value.has(sessionId)) return
    const next = new Map(historyBySession.value)
    next.delete(sessionId)
    historyBySession.value = next
  }

  function getHistoryState(sessionId) {
    return historyBySession.value.get(sessionId) || null
  }

  /** True when the whole history is loaded (or the session was never loaded tail-first). */
  function isHistoryComplete(sessionId) {
    const history = historyBySession.value.get(sessionId)
    return !history || !history.hasMoreBefore
  }

  /**
   * Backfill progress as `{ fraction, loadedBytes, totalBytes }`, or null for a session that
   * isn't tail-loaded. `fraction` is null when it can't be derived. The cursor is opaque by
   * contract; the file-backed reader happens to encode a byte offset (`b:<offset>`), which is
   * used here for a progress hint only — nothing else interprets a cursor.
   */
  function historyProgress(sessionId) {
    const history = historyBySession.value.get(sessionId)
    if (!history) return null
    const totalBytes = _fileBytes(sessionId, history)
    if (!history.hasMoreBefore) return { fraction: 1, loadedBytes: totalBytes, totalBytes }
    const match = /^b:(\d+)$/.exec(history.startCursor || '')
    if (!match || !totalBytes) return { fraction: null, loadedBytes: null, totalBytes }
    const loadedBytes = Math.max(0, totalBytes - Number(match[1]))
    return { fraction: Math.min(1, loadedBytes / totalBytes), loadedBytes, totalBytes }
  }

  // ---- history index (Issue #2030, stage 2 PR B) ----

  function _setHistoryIndex(sessionId, entry) {
    const next = new Map(historyIndexBySession.value)
    if (entry) next.set(sessionId, entry)
    else next.delete(sessionId)
    historyIndexBySession.value = next
  }

  function _dropHistoryIndex(sessionId) {
    const request = _historyIndexRequests.get(sessionId)
    if (request) {
      request.controller.abort()
      clearTimeout(request.retryTimer)
      _historyIndexRequests.delete(sessionId)
    }
    clearTimeout(_historyIndexCooldowns.get(sessionId)?.timer)
    _historyIndexCooldowns.delete(sessionId)
    if (historyIndexBySession.value.has(sessionId)) _setHistoryIndex(sessionId, null)
  }

  function getHistoryIndex(sessionId) {
    return historyIndexBySession.value.get(sessionId) || null
  }

  /** True when a message with this id is part of the loaded suffix. */
  function isMessageLoaded(sessionId, messageId) {
    return !!messageIndexBySession.get(sessionId)?.has(messageId) ||
      !!_islandTargets.get(sessionId)?.messageIndex.has(messageId)
  }

  function _scheduleIndexRetry(sessionId, historyId, generation) {
    const failures = (_historyIndexCooldowns.get(sessionId)?.failures || 0) + 1
    if (failures >= HISTORY_INDEX_MAX_FAILURES) return
    const timer = setTimeout(() => {
      const history = historyBySession.value.get(sessionId)
      const entry = historyIndexBySession.value.get(sessionId)
      if (!history || history.historyId !== historyId || history.generation !== generation ||
          entry?.status !== 'unavailable') return
      _setHistoryIndex(sessionId, null)
      ensureHistoryIndex(sessionId)
    }, HISTORY_INDEX_COOLDOWN_MS)
    _historyIndexCooldowns.set(sessionId, { timer, failures })
  }

  /**
   * Fetches the server history index for a tail-loaded session (one low-priority request;
   * idempotent while loading/ready). Resolves to the entry, or null when none applies (history
   * complete, no history state, stale response). A 404/405 (old server) or any failure after one
   * retry leaves status 'unavailable' — callers keep the stage-1 behaviour (turn navigation
   * locked until the history is complete).
   */
  function ensureHistoryIndex(sessionId) {
    const history = historyBySession.value.get(sessionId)
    if (!history || !history.hasMoreBefore) return Promise.resolve(null)
    const existing = historyIndexBySession.value.get(sessionId)
    if (existing && existing.historyId === history.historyId) {
      return _historyIndexRequests.get(sessionId)?.promise || Promise.resolve(existing)
    }
    _dropHistoryIndex(sessionId)

    const { historyId, generation } = history
    const url = history.baseUrl.replace(/\/messages$/, '/history_index')
    const controller = new AbortController()
    const request = { controller, promise: null, retryTimer: null }
    _historyIndexRequests.set(sessionId, request)
    const pending = (status, turns = []) => ({ status, historyId, turns })
    _setHistoryIndex(sessionId, pending('loading'))

    const isCurrent = () => {
      const now = historyBySession.value.get(sessionId)
      return _historyIndexRequests.get(sessionId) === request && !controller.signal.aborted &&
        !!now && now.generation === generation && now.historyId === historyId
    }
    const settle = (entry) => {
      if (_historyIndexRequests.get(sessionId) === request) _historyIndexRequests.delete(sessionId)
      _setHistoryIndex(sessionId, entry)
      return entry
    }
    const attempt = async (retried) => {
      try {
        const data = await _fetchHistoryJson(url, {
          signal: controller.signal, inactivityMs: HISTORY_INDEX_TIMEOUT_MS,
          label: `history index for session ${sessionId}`, lowPriority: true,
        })
        if (!isCurrent()) return null
        if (data.history_id !== historyId) { // the file changed under us: a resync will refetch
          if (_historyIndexRequests.get(sessionId) === request) _historyIndexRequests.delete(sessionId)
          _setHistoryIndex(sessionId, null)
          return null
        }
        pushDebugEvent('message', 'history-index', { sessionId, turns: data.turns?.length, compactions: data.compactions?.length })
        // Turn positions are compared with the loaded suffix's `b:<offset>` cursor: without such a
        // cursor the index cannot be used (stage-1 behaviour instead of a button that never unlocks).
        if (cursorOffset(historyBySession.value.get(sessionId).startCursor) == null) return settle(pending('unavailable'))
        _historyIndexCooldowns.delete(sessionId)
        return settle(pending('ready', markRaw(data.turns || [])))
      } catch (err) {
        if (!isCurrent()) return null
        const permanent = err?.status === 404 || err?.status === 405 || err?.status === 501
        if (!permanent && !retried) {
          // abortable: dropping the index mid-wait must settle this promise (callers await it)
          await new Promise(resolve => {
            request.retryTimer = setTimeout(resolve, HISTORY_INDEX_RETRY_MS)
            controller.signal.addEventListener('abort', () => { clearTimeout(request.retryTimer); resolve() }, { once: true })
          })
          if (!isCurrent()) return null
          return attempt(true)
        }
        pushDebugEvent('message', 'history-index-unavailable', { sessionId, status: err?.status, message: err?.message })
        const entry = settle(pending('unavailable'))
        if (!permanent) _scheduleIndexRetry(sessionId, historyId, generation)
        return entry
      }
    }
    request.promise = attempt(false)
    return request.promise
  }

  /** True when this tool call was loaded from an older page, i.e. it is history, not news. */
  function wasBackfilled(sessionId, toolUseId) {
    return !!_backfilledToolIds.get(sessionId)?.has(toolUseId)
  }

  /** Aborts the session's in-flight backfill page request (if any) and installs a new one. */
  function claimBackfill(sessionId) {
    _backfillControllers.get(sessionId)?.abort()
    const controller = new AbortController()
    _backfillControllers.set(sessionId, controller)
    return controller
  }

  function releaseBackfill(sessionId, controller) {
    if (_backfillControllers.get(sessionId) === controller) _backfillControllers.delete(sessionId)
  }

  function abortBackfill(sessionId) {
    _backfillControllers.get(sessionId)?.abort()
    _backfillControllers.delete(sessionId)
  }

  /**
   * One GET with its own inactivity abort, composed with the caller's (session-level) signal.
   * A timeout surfaces as a distinguished `isHydrationTimeout` error; the outer signal firing
   * propagates as the original abort. Shared by the tail window and every backfill page.
   */
  async function _fetchHistoryJson(url, { signal, inactivityMs = DEFAULT_HISTORY_PAGE_INACTIVITY_MS, label, lowPriority = false }) {
    const requestController = new AbortController()
    const fetchSignal = signal ? AbortSignal.any([signal, requestController.signal]) : requestController.signal
    const timeoutId = setTimeout(() => requestController.abort(), inactivityMs)
    try {
      return await api.get(url, lowPriority ? { signal: fetchSignal, priority: 'low' } : { signal: fetchSignal })
    } catch (err) {
      if (signal?.aborted) throw err
      if (requestController.signal.aborted) {
        const timeoutErr = new Error(`Timed out loading ${label}`)
        timeoutErr.isHydrationTimeout = true
        pushDebugEvent('message', 'hydration-timeout', { label })
        throw timeoutErr
      }
      throw err
    } finally {
      clearTimeout(timeoutId)
    }
  }

  /**
   * Bytes/second of the most recent fetch of `url`, from Resource Timing (encoded body over the
   * request's duration), or null when unavailable (older Safari, cross-origin, mocked fetch).
   * Feeds the backfill scheduler's slow-link detection where the Network Information API is absent.
   */
  function _measureTransferBps(url, sinceMs) {
    const entry = _findResourceEntry(url, sinceMs - 50)
    if (!entry?.encodedBodySize || !entry.duration) return null
    return entry.encodedBodySize / (entry.duration / 1000)
  }

  /** Newest Resource Timing entry for `url` that started at/after `sinceMs`, or null (unavailable). */
  function _findResourceEntry(url, sinceMs = 0) {
    try {
      const entries = performance.getEntriesByType('resource')
      for (let i = entries.length - 1; i >= 0; i--) {
        if (entries[i].name.endsWith(url) && entries[i].startTime >= sinceMs) return entries[i]
      }
    } catch {
      // Resource Timing unavailable
    }
    return null
  }

  /** Records that overlap what is already loaded prove the new window is contiguous with it. */
  function _overlapsLoaded(sessionId, records) {
    const index = messageIndexBySession.get(sessionId)
    const toolIndex = toolIndexBySession.get(sessionId)
    return records.some(r => (r.type === 'tool_call'
      ? !!r.tool_use_id && !!toolIndex?.has(r.tool_use_id)
      : !!r.message_id && !!index?.has(r.message_id)))
  }

  /**
   * Issue #2030: fetch and install the newest turn-snapped window of a session's (or archive's)
   * history. `options`: `{ signal, inactivityMs, baseUrl, resync, source }`.
   *
   * - First load: the window REPLACES whatever is installed and starts a fresh contiguous suffix.
   * - Resync (`resync: true`, polling.js after stream_reset/evicted): the window is applied ON TOP
   *   of the loaded suffix when it provably overlaps it (same `history_id`, shared records) — the
   *   loaded older part is kept and no re-backfill is needed. A window that does not overlap
   *   (a gap larger than the window), or a changed `history_id`, replaces the state and restarts
   *   backfill (the generation bump tells the scheduler).
   * Returns `{ messages, totalCount: null, hasMore, eventCursor, replaced }`.
   */
  async function loadHistoryTail(sessionId, options = {}) {
    const { signal: callerSignal, inactivityMs, baseUrl = `/api/sessions/${sessionId}/messages`, resync = false } = options
    const source = options.source || 'load'
    _discardStreamingPreview(sessionId, 'reload')
    const signal = callerSignal || claimHydrationLoad(sessionId).signal

    const windowUrl = `${baseUrl}?window=tail`
    const requestedAt = typeof performance !== 'undefined' ? performance.now() : 0
    const data = await _fetchHistoryJson(windowUrl, { signal, inactivityMs, label: `history window for session ${sessionId}` })
    if (signal.aborted) throw new DOMException('Aborted', 'AbortError')

    const records = data.messages || []
    const prior = historyBySession.value.get(sessionId)
    const hasLoaded = messagesBySession.value.has(sessionId) && !!prior
    const canMerge = resync && hasLoaded && prior.historyId === data.history_id && _overlapsLoaded(sessionId, records)

    let target
    if (canMerge) {
      target = _seedRecordTargetFromCurrent(sessionId)
    } else {
      // Replacing: an older-page backfill still running against the previous suffix is stale.
      abortBackfill(sessionId)
      _dropHistoryIndex(sessionId)
      _backfilledToolIds.delete(sessionId)
      target = _createRecordTarget()
      _ledgerBySession.delete(sessionId)
      _toolPageBySession.delete(sessionId)
    }
    if (source === 'archive') {
      _applyArchiveRecords(sessionId, records, target, { captureInit: true })
    } else {
      _applyPageToTarget(sessionId, records, target)
    }
    _installPageAndNotify(sessionId, target)
    // A reload/resync of an already-loaded live session may carry tool completions that happened
    // while the stream was down (they arrive as history, not as live records).
    if (hasLoaded && source !== 'archive') _notifyListeners(historyReloadListeners, sessionId)
    if (source === 'archive') {
      if (data.latest_init) sessionStore.storeArchiveInitData(sessionId, data.latest_init)
    } else if (data.latest_init) {
      sessionStore.storeInitData(sessionId, data.latest_init)
    }
    _hookCorrelationCache.delete(sessionId)

    _liveBytesBySession.delete(sessionId) // totalBytes is fresh again
    if (canMerge) {
      // Keep the older part we already hold; only the end moved.
      _setHistory(sessionId, { totalBytes: data.total_bytes })
    } else {
      _setHistory(sessionId, {
        budgetReached: false, // a replaced suffix starts over: the memory-budget state belonged to the old one
        trimmedBytes: 0,
        baseUrl,
        startCursor: data.start_cursor,
        hasMoreBefore: !!data.has_more_before,
        historyId: data.history_id,
        totalBytes: data.total_bytes,
        snapped: data.snapped !== false,
        generation: (prior?.generation || 0) + 1,
        source,
        windowBps: _measureTransferBps(windowUrl, requestedAt),
      })
    }
    // Stage 3 PR 3C-1: the island survives a resync while the history file is unchanged (stream_reset
    // / evicted never touch its content); a changed history_id drops it. A replaced suffix may now
    // reach the island, in which case they merge.
    if (islandBySession.value.has(sessionId)) {
      if (islandBySession.value.get(sessionId).historyId !== data.history_id) _dropIsland(sessionId)
      else if (!canMerge) _mergeIslandIfTouching(sessionId)
    }
    if (data.event_cursor !== undefined) loadedEventCursors.set(sessionId, data.event_cursor)
    // Only release the shared tracker if it is OURS (an archive view passes its own signal and
    // never claims it — it must not clear a live session's pending load for the same id).
    if (_hydrationLoadStateBySession.get(sessionId)?.controller.signal === signal) {
      _hydrationLoadStateBySession.delete(sessionId)
    }

    pushDebugEvent('message', 'history-window', {
      sessionId, records: records.length, hasMoreBefore: !!data.has_more_before, merged: canMerge, snapped: data.snapped,
    })
    return {
      messages: messagesBySession.value.get(sessionId) || [],
      totalCount: null,
      hasMore: canMerge ? !isHistoryComplete(sessionId) : !!data.has_more_before,
      eventCursor: data.event_cursor,
      replaced: !canMerge,
    }
  }

  /**
   * Fetches (does not apply) the next older page of `sessionId`'s history. Resolves to
   * `{ records, startCursor, hasMoreBefore, requestedBefore, historyId, generation, bytes }` or
   * null when there is nothing older. Throws an error with `isHistoryReset` when the server says
   * the cursor is no longer valid (409) or the file was rewritten (`history_id` changed) — the
   * caller restarts from the tail.
   */
  async function fetchOlderPage(sessionId, { signal, maxBytes, maxRecords, inactivityMs } = {}) {
    const history = historyBySession.value.get(sessionId)
    if (!history || !history.hasMoreBefore) return null
    const params = [`before=${encodeURIComponent(history.startCursor)}`]
    if (maxBytes) params.push(`max_bytes=${maxBytes}`)
    if (maxRecords) params.push(`max_records=${maxRecords}`)
    let data
    try {
      data = await _fetchHistoryJson(`${history.baseUrl}?${params.join('&')}`, {
        signal, inactivityMs, label: `older history for session ${sessionId}`, lowPriority: true,
      })
    } catch (err) {
      if (err?.status === 409) {
        const resetErr = new Error('History cursor no longer valid')
        resetErr.isHistoryReset = true
        throw resetErr
      }
      throw err
    }
    if (data.history_id !== history.historyId) {
      const resetErr = new Error('History file changed during backfill')
      resetErr.isHistoryReset = true
      throw resetErr
    }
    return {
      records: data.messages || [],
      startCursor: data.start_cursor,
      hasMoreBefore: !!data.has_more_before,
      requestedBefore: history.startCursor,
      historyId: data.history_id,
      generation: history.generation,
    }
  }

  /**
   * Applies a page returned by fetchOlderPage() — unless the loaded suffix moved on while it was
   * in flight (generation / cursor changed), in which case the page is stale and dropped.
   * Returns true when applied.
   */
  function applyFetchedOlderPage(sessionId, page, { source = 'load' } = {}) {
    const history = historyBySession.value.get(sessionId)
    if (!history || history.generation !== page.generation || history.startCursor !== page.requestedBefore) return false
    const newToolIds = _newToolIdsOf(sessionId, page.records)
    const prepended = applyOlderPage(sessionId, page.records, { source })
    _recordOlderSegment(sessionId, history.generation, {
      startCursor: page.startCursor, endCursor: page.requestedBefore, count: prepended,
    }, newToolIds)
    _setHistory(sessionId, { startCursor: page.startCursor, hasMoreBefore: page.hasMoreBefore })
    if (!page.hasMoreBefore) _hookCorrelationCache.delete(sessionId)
    // Stage 3 PR 3C-1: backfill growing the suffix upward may reach the island: they merge.
    if (islandBySession.value.has(sessionId)) _mergeIslandIfTouching(sessionId)
    return true
  }

  // ---- memory budget: ledger + oldest-end trim (Issue #2030, stage 4 PR 4d) ----

  function _recordOlderSegment(sessionId, generation, segment, newToolIds = []) {
    let ledger = _ledgerBySession.get(sessionId)
    if (!ledger || ledger.generation !== generation) {
      ledger = { generation, segments: [], pageSeq: 0 }
      _ledgerBySession.set(sessionId, ledger)
      _toolPageBySession.delete(sessionId)
    }
    segment.page = ++ledger.pageSeq
    ledger.segments.push(segment)
    if (newToolIds.length) {
      let pages = _toolPageBySession.get(sessionId)
      if (!pages) _toolPageBySession.set(sessionId, (pages = new Map()))
      for (const id of newToolIds) pages.set(id, segment.page)
    }
  }

  /**
   * An island that merged into the suffix keeps its page structure in the suffix ledger (so a later trim can
   * release it page by page instead of all at once). Falls back to ONE segment when the island's own ledger
   * does not account for exactly the messages that were prepended (overlap with the suffix, a replaced ledger).
   */
  function _recordIslandSegments(sessionId, generation, island, suffixStart, prepended, newToolIds) {
    const ledger = _islandLedger.get(sessionId)
    const segments = ledger && ledger.islandId === island.id ? ledger.segments : null
    if (!segments || segments.length < 2 || segments.reduce((n, seg) => n + seg.count, 0) !== prepended) {
      _recordOlderSegment(sessionId, generation, { startCursor: island.startCursor, endCursor: suffixStart, count: prepended }, newToolIds)
      return
    }
    const regions = _islandToolRegions.get(sessionId)
    const idsBySegment = segments.map(() => [])
    const regionIndex = new Map(segments.map((seg, i) => [seg.region, i]))
    for (const id of newToolIds) {
      // a tool is only as releasable as its NEWEST region (the segment closest to the live end)
      const index = regionIndex.get(regions?.get(id)?.hi)
      idsBySegment[index ?? segments.length - 1].push(id)
    }
    // the ledger lists the suffix's pages newest first: the island's last page first
    for (let i = segments.length - 1; i >= 0; i--) {
      const { startCursor, endCursor, count } = segments[i]
      _recordOlderSegment(sessionId, generation, { startCursor, endCursor, count }, idsBySegment[i])
    }
  }

  /** tool_use_ids of the page's tool_call records that have no entry in the suffix yet (they are created by this page). */
  function _newToolIdsOf(sessionId, records) {
    const toolIndex = toolIndexBySession.get(sessionId)
    const ids = []
    for (const record of records) {
      if (record.type === 'tool_call' && record.tool_use_id && !toolIndex?.has(record.tool_use_id)) ids.push(record.tool_use_id)
    }
    return ids
  }

  function _noteLiveBytes(sessionId, record) {
    if (!historyBySession.value.has(sessionId)) return
    let size = 0
    try { size = JSON.stringify(record).length + 1 } catch { /* unserialisable: not counted */ }
    _liveBytesBySession.set(sessionId, (_liveBytesBySession.get(sessionId) || 0) + size)
  }

  /** Size of the history file as far as this tab knows: the window's `total_bytes` plus what live records added. */
  function _fileBytes(sessionId, history) {
    return (history?.totalBytes || 0) + (_liveBytesBySession.get(sessionId) || 0)
  }

  /** History-file bytes covered by the loaded suffix (`totalBytes - startOffset`), or null for opaque cursors. */
  function _suffixLoadedBytes(sessionId, history) {
    const start = cursorOffset(history?.startCursor)
    return start == null ? null : Math.max(0, _fileBytes(sessionId, history) - start)
  }

  /** `{ segments, loadedBytes, cuttableMessages }` of the suffix's trim ledger, or null when there is none. Test/diagnostics aid. */
  function getTrimInfo(sessionId) {
    const history = historyBySession.value.get(sessionId)
    const ledger = _ledgerBySession.get(sessionId)
    if (!history || !ledger || ledger.generation !== history.generation) return null
    return { segments: ledger.segments.length, loadedBytes: _suffixLoadedBytes(sessionId, history), cuttableMessages: ledger.segments.reduce((n, seg) => n + seg.count, 0) }
  }

  /** The index of `messageId` within the SUFFIX (not the island), or undefined. */
  function getSuffixMessageIndex(sessionId, messageId) {
    return messageIndexBySession.get(sessionId)?.get(messageId)
  }

  /** Tool-use ids referenced by `messages` (assistant `metadata.tool_uses`). */
  function _toolUseIds(messages, into = new Set()) {
    for (const message of messages) {
      const uses = message.metadata?.tool_uses
      if (uses) for (const use of uses) if (use.id) into.add(use.id)
    }
    return into
  }

  /**
   * Cuts `front` messages off the front or `back` off the back of a record target and returns the new
   * target. A tool entry goes only when `releasable(tool)` says ALL its records lie in the released
   * region (see _toolPageBySession) and it is not pinned: still running / waiting for a permission, the
   * launch tool of a known subagent leg (or a tool running under one), or referenced by a message that stays (including `alsoKept`,
   * the other interval's messages). Everything else stays, owning message or not.
   */
  function _cutTarget(target, { front = 0, back = 0 }, alsoKept, releasable) {
    const total = target.messages.length
    const kept = front ? target.messages.slice(front) : target.messages.slice(0, total - back)
    const stays = _toolUseIds(alsoKept, _toolUseIds(kept))
    const toolCalls = target.toolCalls.filter(tool => {
      if (!releasable(tool) || stays.has(tool.id)) return true
      if (tool.status !== 'completed' && tool.status !== 'error') return true
      // the launch tool of a subagent leg, and the tools its (unreleased) narration refers to
      return taskIdByLaunchToolUseId.value.has(tool.id) ||
        (!!tool.parent_tool_use_id && taskIdByLaunchToolUseId.value.has(tool.parent_tool_use_id))
    })
    const messageIndex = new Map()
    for (const [key, idx] of target.messageIndex) {
      const next = idx - front
      if (next >= 0 && next < kept.length) messageIndex.set(key, next)
    }
    const retryIndex = new Map()
    for (const [key, idx] of target.retryIndex) {
      const next = idx - front
      if (next >= 0 && next < kept.length) retryIndex.set(key, next)
    }
    const toolIndex = new Map()
    toolCalls.forEach((tool, i) => toolIndex.set(tool.id, i))
    return {
      target: { messages: kept, messageIndex, toolCalls, toolIndex, retryIndex },
      removedMessages: total - kept.length,
      removedTools: target.toolCalls.length - toolCalls.length,
      removedToolIds: target.toolCalls.filter(tool => !toolIndex.has(tool.id)).map(tool => tool.id),
    }
  }

  /**
   * Releases the OLD end of the loaded suffix down to `targetBytes` of history file, a whole page of the
   * ledger at a time (so the new `startCursor` is an exact, server-snapped cursor and a later `before=`
   * fetch returns precisely the released page). `maxCutMessages` (from the message list: only rows far
   * above the viewport may go) bounds how many leading messages may be removed. Synchronous; the caller
   * decides WHEN (idle, no gesture, nothing in flight). `kind: 'background'` (a session that is no longer
   * the current one) leaves the backfill free to resume on re-entry; `'budget'` makes `budgetReached`
   * sticky until the suffix is replaced. Returns `{ trimmed, reason?, messages, tools, bytes }`.
   */
  function trimOldEnd(sessionId, { targetBytes, maxCutMessages = Infinity, kind = 'budget' } = {}) {
    const none = (reason) => ({ trimmed: false, reason })
    const history = historyBySession.value.get(sessionId)
    const ledger = _ledgerBySession.get(sessionId)
    if (!history) return none('no-history')
    const startOffset = cursorOffset(history.startCursor)
    if (startOffset == null) return none('opaque-cursor')
    if (!ledger || ledger.generation !== history.generation || !ledger.segments.length) return none('no-ledger')
    if (_hydrationLoadStateBySession.has(sessionId)) return none('loading')
    const segments = ledger.segments
    if (segments[segments.length - 1].startCursor !== history.startCursor) {
      _ledgerBySession.delete(sessionId) // the chain no longer reaches the loaded start: never guess
      return none('ledger-chain')
    }
    const totalBytes = _fileBytes(sessionId, history)
    if (totalBytes - startOffset <= targetBytes) return none('under-target')
    const messages = messagesBySession.value.get(sessionId) || []

    let cutSegments = 0
    let cutMessages = 0
    let newStart = startOffset
    for (let i = segments.length - 1; i >= 0; i--) {
      const segment = segments[i]
      const end = cursorOffset(segment.endCursor)
      if (end == null || cutMessages + segment.count > maxCutMessages) break
      cutSegments++
      cutMessages += segment.count
      newStart = end
      if (totalBytes - newStart <= targetBytes) break
    }
    if (!cutSegments) return none('guard')
    if (cutMessages >= messages.length) return none('would-empty')

    // A page request for the old edge is moot (its cursor no longer matches and applyFetchedOlderPage would
    // drop it anyway); abort it so it does not burn bandwidth.
    abortBackfill(sessionId)
    const boundary = segments[segments.length - cutSegments].endCursor
    const island = islandBySession.value.get(sessionId)
    const pages = _toolPageBySession.get(sessionId)
    const firstCutPage = segments[segments.length - cutSegments].page
    const cut = _cutTarget({
      messages,
      messageIndex: messageIndexBySession.get(sessionId) || new Map(),
      toolCalls: toolCallsBySession.value.get(sessionId) || [],
      toolIndex: toolIndexBySession.get(sessionId) || new Map(),
      retryIndex: retryIndexBySession.get(sessionId) || new Map(),
    }, { front: cutMessages }, island ? island.messages : [], (tool) => (pages?.get(tool.id) ?? 0) >= firstCutPage)
    segments.length -= cutSegments
    if (pages) for (const id of cut.removedToolIds) pages.delete(id)
    _installRecordTarget(sessionId, cut.target)
    messagesBySession.value = new Map(messagesBySession.value)
    toolCallsBySession.value = new Map(toolCallsBySession.value)
    const backfilled = _backfilledToolIds.get(sessionId)
    if (backfilled) for (const id of cut.removedToolIds) backfilled.delete(id)
    _hookCorrelationCache.delete(sessionId)
    const bytes = newStart - startOffset
    _setHistory(sessionId, {
      startCursor: boundary,
      hasMoreBefore: true,
      trimmedBytes: (history.trimmedBytes || 0) + bytes,
      budgetReached: kind === 'budget',
    })
    // An island that used to end before the (now later) suffix start simply has a larger gap; one that
    // overlapped it cannot exist (it would have been merged).
    pushDebugEvent('message', 'history-trimmed', { sessionId, kind, messages: cut.removedMessages, tools: cut.removedTools, bytes })
    return { trimmed: true, messages: cut.removedMessages, tools: cut.removedTools, bytes }
  }

  // ---- island: a pinned window of history away from the suffix (Issue #2030, stage 3 PR 3C-1) ----

  const ISLAND_REQUEST_INACTIVITY_MS = 30000

  function getIsland(sessionId) {
    return islandBySession.value.get(sessionId) || null
  }

  function _islandError(reason, message, extra = {}) {
    const err = new Error(message)
    err.reason = reason
    return Object.assign(err, extra)
  }

  /** Maps a failed island request to `err.reason`: not-found | busy | reset | superseded | aborted | failed. */
  function _mapIslandError(err, controller, outerSignal) {
    if (outerSignal?.aborted) return _islandError('aborted', 'Jump cancelled', { name: 'AbortError' })
    if (controller?.signal.aborted) return _islandError('superseded', 'Jump superseded')
    if (err?.status === 404) return _islandError('not-found', 'Message not found')
    if (err?.status === 503) return _islandError('busy', 'The server is busy, try again', { status: 503 })
    if (err?.status === 409 || err?.isHistoryReset) return _islandError('reset', 'History changed', { isHistoryReset: true })
    return err?.reason ? err : _islandError('failed', err?.message || 'Failed to load history', { cause: err })
  }

  /** Aborts the session's in-flight island request (around / before / after), if any. */
  function abortIsland(sessionId) {
    _islandControllers.get(sessionId)?.abort()
    _islandControllers.delete(sessionId)
  }

  function _claimIsland(sessionId) {
    _islandControllers.get(sessionId)?.abort()
    const controller = new AbortController()
    _islandControllers.set(sessionId, controller)
    return controller
  }

  function _releaseIsland(sessionId, controller) {
    if (_islandControllers.get(sessionId) === controller) _islandControllers.delete(sessionId)
  }

  /** Forgets the island's content only (a request in flight is left alone). */
  function _clearIslandState(sessionId) {
    _islandTargets.delete(sessionId)
    _islandLedger.delete(sessionId)
    _islandToolRegions.delete(sessionId)
    _islandLatest.delete(sessionId)
    _islandHookCache.delete(sessionId)
    if (!islandBySession.value.has(sessionId)) return
    const next = new Map(islandBySession.value)
    next.delete(sessionId)
    islandBySession.value = next
  }

  /** Drops the island and aborts whatever request is still working on it. */
  function _dropIsland(sessionId) {
    abortIsland(sessionId)
    if (islandBySession.value.has(sessionId)) pushDebugEvent('message', 'island-dropped', { sessionId })
    _clearIslandState(sessionId)
  }

  /** Publishes the island's current content/meta (re-sliced arrays: Vue sees a new value). */
  function _commitIsland(sessionId, patch) {
    const target = _islandTargets.get(sessionId)
    const prev = toRaw(islandBySession.value.get(sessionId)) || {}
    const next = new Map(islandBySession.value)
    next.set(sessionId, {
      ...prev, ...patch, rev: (prev.rev || 0) + 1,
      messages: target.messages.slice(), toolCalls: target.toolCalls.slice(),
    })
    islandBySession.value = next
  }

  /**
   * Tools that already exist in the suffix are owned by the suffix (global by tool_use_id, the
   * suffix wins): the island's record only fills what the suffix entry lacks, then goes.
   */
  function _shareIslandTools(sessionId, target) {
    const suffixIndex = toolIndexBySession.get(sessionId)
    const suffixTools = toolCallsBySession.value.get(sessionId)
    if (!suffixIndex || !suffixIndex.size || !suffixTools) return
    let shared = false
    for (const tool of target.toolCalls) {
      const idx = suffixIndex.get(tool.id)
      if (idx === undefined) continue
      mergeToolCallFields(suffixTools[idx], tool)
      shared = true
    }
    if (!shared) return
    target.toolCalls = target.toolCalls.filter(tool => !suffixIndex.has(tool.id))
    target.toolIndex = new Map(target.toolCalls.map((tool, i) => [tool.id, i]))
  }

  function _islandOverlapsSuffix(sessionId, target) {
    const index = messageIndexBySession.get(sessionId)
    return !!index && target.messages.some(m => m.message_id && index.has(m.message_id))
  }

  /** Merges the island into the suffix when the two touch/overlap. Returns true when merged. */
  function _mergeIslandIfTouching(sessionId) {
    const island = islandBySession.value.get(sessionId)
    const history = historyBySession.value.get(sessionId)
    const target = _islandTargets.get(sessionId)
    if (!island || !history || !target) return false
    const needOverlap = cursorOffset(island.endCursor) == null || cursorOffset(history.startCursor) == null
    if (!intervalsTouch(island, history, needOverlap && _islandOverlapsSuffix(sessionId, target))) return false
    mergeIslandIntoSuffix(sessionId)
    return true
  }

  /**
   * The island joins the suffix as if it had been an older backfill page (mergeOlderPage: records
   * already in the suffix are dropped, the suffix's tool entries win field by field).
   */
  function mergeIslandIntoSuffix(sessionId) {
    const island = islandBySession.value.get(sessionId)
    const target = _islandTargets.get(sessionId)
    const history = historyBySession.value.get(sessionId)
    if (!island || !target || !history) return false
    const current = {
      messages: messagesBySession.value.get(sessionId) || [],
      messageIndex: messageIndexBySession.get(sessionId) || new Map(),
      toolCalls: toolCallsBySession.value.get(sessionId) || [],
      toolIndex: toolIndexBySession.get(sessionId) || new Map(),
      retryIndex: retryIndexBySession.get(sessionId) || new Map(),
    }
    const suffixStartBefore = history.startCursor
    const newToolIds = target.toolCalls.filter(tool => !current.toolIndex.has(tool.id)).map(tool => tool.id)
    const merged = mergeOlderPage(current, target)
    _installRecordTarget(sessionId, merged)
    // What the island's records determined counts only where nothing newer did.
    const latest = _islandLatest.get(sessionId) || {}
    if (latest.stop && !lastStopReasonBySession.value.has(sessionId)) lastStopReasonBySession.value.set(sessionId, latest.stop[0])
    if (latest.deferred && !deferredToolUseBySession.value.has(sessionId)) deferredToolUseBySession.value.set(sessionId, latest.deferred[0])
    if (latest.launch && !launchTimestampBySession.value.has(sessionId)) launchTimestampBySession.value.set(sessionId, latest.launch[0])
    lastStopReasonBySession.value = new Map(lastStopReasonBySession.value)
    deferredToolUseBySession.value = new Map(deferredToolUseBySession.value)
    messagesBySession.value = new Map(messagesBySession.value)
    toolCallsBySession.value = new Map(toolCallsBySession.value)
    narrationByTaskIdAndLeg.value = new Map(narrationByTaskIdAndLeg.value)
    // The suffix now reaches back to the island's start — unless the window lay entirely inside the
    // suffix (it then reaches no further back than it already did).
    const islandStart = cursorOffset(island.startCursor)
    const suffixStart = cursorOffset(history.startCursor)
    const reachesFurther = islandStart != null && suffixStart != null ? islandStart < suffixStart : merged.prependedMessageCount > 0
    if (reachesFurther) {
      _setHistory(sessionId, { startCursor: island.startCursor, hasMoreBefore: island.hasMoreBefore })
      _recordIslandSegments(sessionId, history.generation, island, suffixStartBefore, merged.prependedMessageCount, newToolIds)
    } else if (merged.prependedMessageCount > 0) {
      _ledgerBySession.delete(sessionId) // records were added at the front without a known cut point: never guess
    }
    _hookCorrelationCache.delete(sessionId)
    // (A page request still working on the island is stale and is dropped when it lands; an in-flight
    // jump is NOT this island's and must keep running.)
    _clearIslandState(sessionId)
    pushDebugEvent('message', 'island-merged', { sessionId, prepended: merged.prependedMessageCount, hasMoreBefore: island.hasMoreBefore })
    return true
  }

  /**
   * THE jump path (issue #2030 stage 3 PR 3C-1): fetches ONE bounded window around `target` —
   * `{ cursor }` (`around=`, no lookup) or `{ messageId }` / `{ toolUseId }` (`around_id=`) — and
   * installs it as the island, replacing any previous one (aborting its request first). A window
   * that reaches the suffix merges into it at once. Resolves to `{ status: 'loaded' | 'complete' |
   * 'island' | 'merged', anchorCursor, parentUnresolved }`; rejects with `err.reason` 'not-found'
   * (404 message_not_found) | 'busy' (503 resolve_busy) | 'reset' (409 / history_id changed, the
   * island is dropped) | 'superseded' | 'aborted' | 'failed'. Whatever happens the suffix is untouched.
   */
  async function loadAround(sessionId, target, { signal: outerSignal, inactivityMs = ISLAND_REQUEST_INACTIVITY_MS } = {}) {
    const history = historyBySession.value.get(sessionId)
    if (!history || !history.hasMoreBefore) return { status: 'complete' } // fully loaded: nothing to fetch
    if (target.messageId && isMessageLoaded(sessionId, target.messageId)) return { status: 'loaded' }
    // A cursor at or after the suffix start is already loaded (the suffix reaches the end of the file).
    const targetOffset = cursorOffset(target.cursor)
    const suffixOffset = cursorOffset(history.startCursor)
    if (targetOffset != null && suffixOffset != null && targetOffset >= suffixOffset) return { status: 'loaded' }
    const enc = encodeURIComponent
    const params = target.cursor ? `around=${enc(target.cursor)}`
      : (target.messageId || target.toolUseId) ? `around_id=${enc(target.messageId || target.toolUseId)}` : null
    if (!params) throw _islandError('not-found', 'No jump target')

    const controller = _claimIsland(sessionId)
    const signal = outerSignal ? AbortSignal.any([outerSignal, controller.signal]) : controller.signal
    const url = `${history.baseUrl}?${params}`
    const startedAt = typeof performance !== 'undefined' ? performance.now() : 0
    try {
      let data
      try {
        data = await _fetchHistoryJson(url, { signal, inactivityMs, label: `history window for session ${sessionId}` })
      } catch (err) {
        throw _mapIslandError(err, controller, outerSignal)
      }
      if (controller.signal.aborted || _islandControllers.get(sessionId) !== controller) throw _islandError('superseded', 'Jump superseded')
      const now = historyBySession.value.get(sessionId)
      if (!now || now.generation !== history.generation) throw _islandError('superseded', 'Jump superseded')
      if (data.history_id !== now.historyId) {
        _dropIsland(sessionId)
        throw _islandError('reset', 'History changed', { isHistoryReset: true })
      }

      const records = data.messages || []
      const latest = {}
      const islandTarget = _replayHistoryRecords(sessionId, records, history.source || 'load', _createRecordTarget(), latest)
      _shareIslandTools(sessionId, islandTarget)
      _clearIslandState(sessionId) // replace: the previous island's records are exclusively its own
      _islandTargets.set(sessionId, islandTarget)
      _islandLatest.set(sessionId, latest)
      _commitIsland(sessionId, {
        id: ++_islandSeq,
        baseUrl: history.baseUrl,
        source: history.source || 'load',
        historyId: data.history_id,
        startCursor: data.start_cursor,
        endCursor: data.end_cursor,
        anchorCursor: data.anchor_cursor ?? null,
        hasMoreBefore: !!data.has_more_before,
        hasMoreAfter: !!data.has_more_after,
        snapped: data.snapped !== false,
        parentUnresolved: !!data.parent_unresolved,
        bytes: windowBytes(data.start_cursor, data.end_cursor) ?? records.length * 2048,
      })
      // The around window is the island's base segment (region 0): released like any page, as a whole.
      _noteIslandToolRegions(sessionId, records, 0)
      _islandLedger.set(sessionId, {
        islandId: _islandSeq,
        segments: [{ startCursor: data.start_cursor, endCursor: data.end_cursor, count: islandTarget.messages.length, region: 0 }],
        minRegion: 0,
        maxRegion: 0,
      })
      const merged = _mergeIslandIfTouching(sessionId)
      pushDebugEvent('message', 'island-window', {
        sessionId, records: records.length, merged, bytes: windowBytes(data.start_cursor, data.end_cursor), ms: typeof performance !== 'undefined' ? Math.round(performance.now() - startedAt) : null,
      })
      return { status: merged ? 'merged' : 'island', anchorCursor: data.anchor_cursor ?? null, parentUnresolved: !!data.parent_unresolved }
    } finally {
      _releaseIsland(sessionId, controller)
    }
  }

  /**
   * Fetches (does not apply) the island's next page on one edge: `before` (older, the topmost
   * interval's scroll-up page) or `after` ("Load newer": a forward page that stops at the suffix
   * start). Resolves to the page, or null when that edge has nothing more. Same error reasons as
   * loadAround. (Stage 4 PR 4d: no size cap — applyIslandPage evicts the far edge past the budget.)
   */
  async function fetchIslandPage(sessionId, edge, { signal: outerSignal, maxBytes = ISLAND_PAGE_BYTES, inactivityMs = ISLAND_REQUEST_INACTIVITY_MS } = {}) {
    const island = islandBySession.value.get(sessionId)
    const history = historyBySession.value.get(sessionId)
    if (!island || !history) return null
    if (edge === 'before' ? !island.hasMoreBefore : !island.hasMoreAfter) return null
    const enc = encodeURIComponent
    const from = edge === 'before' ? island.startCursor : island.endCursor
    const query = edge === 'before'
      ? `before=${enc(from)}&max_bytes=${maxBytes}`
      : `after=${enc(from)}&until=${enc(history.startCursor)}&max_bytes=${maxBytes}`
    const url = `${island.baseUrl}?${query}`
    const controller = _claimIsland(sessionId)
    const signal = outerSignal ? AbortSignal.any([outerSignal, controller.signal]) : controller.signal
    try {
      let data
      try {
        data = await _fetchHistoryJson(url, { signal, inactivityMs, label: `${edge} history for session ${sessionId}` })
      } catch (err) {
        throw _mapIslandError(err, controller, outerSignal)
      }
      if (controller.signal.aborted || _islandControllers.get(sessionId) !== controller) throw _islandError('superseded', 'Request superseded')
      if (data.history_id !== island.historyId) {
        _dropIsland(sessionId)
        throw _islandError('reset', 'History changed', { isHistoryReset: true })
      }
      return {
        edge,
        records: data.messages || [],
        startCursor: data.start_cursor,
        endCursor: data.end_cursor,
        hasMoreBefore: !!data.has_more_before,
        hasMoreAfter: !!data.has_more_after,
        islandId: island.id,
        requestedFrom: from,
      }
    } finally {
      _releaseIsland(sessionId, controller)
    }
  }

  /** Records the region ordinal of every tool_call record of an island page (see _islandToolRegions). */
  function _noteIslandToolRegions(sessionId, records, region) {
    let regions = _islandToolRegions.get(sessionId)
    if (!regions) _islandToolRegions.set(sessionId, (regions = new Map()))
    for (const record of records) {
      const id = record.type === 'tool_call' ? record.tool_use_id : null
      if (!id) continue
      const known = regions.get(id)
      if (known) { known.lo = Math.min(known.lo, region); known.hi = Math.max(known.hi, region) } else regions.set(id, { lo: region, hi: region })
    }
  }

  /**
   * Island budget (stage 4 PR 4d, replaces the interim 64 MiB stop): when the island holds more than the
   * budget, whole pages are released from the edge FARTHEST from the one that just grew, down to the trim
   * target. Returns `{ patch, bytes }` for the island meta, or null when nothing was released.
   */
  function _evictIslandFarEdge(sessionId, landedEdge, startCursor, endCursor, estimatedBytes) {
    const budget = getHistoryBudgetBytes()
    // opaque (non-offset) cursors: fall back to the island's own estimate (2 KiB per message)
    let bytes = windowBytes(startCursor, endCursor) ?? estimatedBytes
    const ledger = _islandLedger.get(sessionId)
    const target = _islandTargets.get(sessionId)
    if (bytes == null || bytes <= budget || !ledger || !target) return null
    const goal = trimTargetBytes(budget)
    const patch = {}
    let front = 0
    let back = 0
    let boundary = 0 // region ordinal of the nearest released page
    // The page that just landed is at the near edge; release from the opposite end, whole pages, never the last one.
    while (ledger.segments.length > 1 && bytes > goal) {
      const next = landedEdge === 'before' ? ledger.segments.at(-1) : ledger.segments[0]
      if (front + back + next.count >= target.messages.length) break // never empty the island
      const segment = landedEdge === 'before' ? ledger.segments.pop() : ledger.segments.shift()
      if (landedEdge === 'before') {
        back += segment.count
        boundary = segment.region
        patch.endCursor = segment.startCursor
        patch.hasMoreAfter = true
      } else {
        front += segment.count
        boundary = segment.region
        patch.startCursor = segment.endCursor
        patch.hasMoreBefore = true
      }
      const released = segment.count * 2048
      bytes = windowBytes(patch.startCursor ?? startCursor, patch.endCursor ?? endCursor) ?? bytes - released
    }
    if (!front && !back) return null
    const regions = _islandToolRegions.get(sessionId)
    const releasable = (tool) => {
      const range = regions?.get(tool.id)
      return !!range && (back ? range.lo >= boundary : range.hi <= boundary)
    }
    const cut = _cutTarget(target, { front, back }, messagesBySession.value.get(sessionId) || [], releasable)
    if (regions) for (const id of cut.removedToolIds) regions.delete(id)
    _islandTargets.set(sessionId, cut.target)
    _islandHookCache.delete(sessionId)
    pushDebugEvent('message', 'island-evicted', { sessionId, edge: landedEdge === 'before' ? 'after' : 'before', messages: cut.removedMessages, tools: cut.removedTools, bytes })
    return { patch, bytes }
  }

  /**
   * Applies a page returned by fetchIslandPage() unless the island moved on while it was in flight
   * (replaced / grown from the other edge / merged), in which case it is stale and dropped.
   * An `after` page that reaches the suffix merges the island into it. Returns true when applied.
   */
  function applyIslandPage(sessionId, page) {
    const island = islandBySession.value.get(sessionId)
    const target = _islandTargets.get(sessionId)
    if (!island || !target || island.id !== page.islandId) return false
    if ((page.edge === 'before' ? island.startCursor : island.endCursor) !== page.requestedFrom) return false

    let patch
    const latest = {}
    const known = _islandLatest.get(sessionId) || {}
    let ledger = _islandLedger.get(sessionId)
    if (!ledger || ledger.islandId !== island.id) {
      ledger = { islandId: island.id, segments: [], minRegion: 0, maxRegion: 0 }
      _islandLedger.set(sessionId, ledger)
    }
    if (page.edge === 'before') {
      const older = _replayHistoryRecords(sessionId, page.records, island.source, _createRecordTarget(), latest)
      const merged = mergeOlderPage(target, older)
      _islandTargets.set(sessionId, merged)
      const region = --ledger.minRegion
      _noteIslandToolRegions(sessionId, page.records, region)
      ledger.segments.unshift({ startCursor: page.startCursor, endCursor: page.requestedFrom, count: merged.prependedMessageCount, region })
      _islandLatest.set(sessionId, { ...latest, ...known }) // older records never beat what the island already determined
      patch = { startCursor: page.startCursor, hasMoreBefore: page.hasMoreBefore }
    } else {
      // Newer records grow the island's own target (the live-update semantics: newer wins).
      const countBefore = target.messages.length
      _replayHistoryRecords(sessionId, page.records, island.source, target, latest, true)
      const region = ++ledger.maxRegion
      _noteIslandToolRegions(sessionId, page.records, region)
      ledger.segments.push({ startCursor: page.requestedFrom, endCursor: page.endCursor, count: target.messages.length - countBefore, region })
      _islandLatest.set(sessionId, { ...known, ...latest })
      patch = { endCursor: page.endCursor, hasMoreAfter: page.hasMoreAfter }
    }
    _shareIslandTools(sessionId, _islandTargets.get(sessionId))
    const startCursor = patch.startCursor ?? island.startCursor
    const endCursor = patch.endCursor ?? island.endCursor
    let bytes = windowBytes(startCursor, endCursor) ?? island.bytes + page.records.length * 2048
    // Past the budget the edge FARTHEST from the one just extended is released, a page at a time.
    const evicted = _evictIslandFarEdge(sessionId, page.edge, startCursor, endCursor, bytes)
    if (evicted) {
      Object.assign(patch, evicted.patch)
      bytes = evicted.bytes
    }
    _commitIsland(sessionId, { ...patch, bytes })
    if (page.records.some(_touchesCompactionHooks)) _islandHookCache.delete(sessionId)
    pushDebugEvent('message', 'island-page', { sessionId, edge: page.edge, records: page.records.length, bytes })
    if (page.edge === 'after') _mergeIslandIfTouching(sessionId)
    return true
  }

  /** The suffix's messages, or island ++ suffix while an island exists (suffix array itself otherwise). */
  function loadedMessages(sessionId) {
    const suffix = messagesBySession.value.get(sessionId) || []
    const island = islandBySession.value.get(sessionId)
    return island ? island.messages.concat(suffix) : suffix
  }

  /** The suffix's tool calls, or island's ++ suffix's while an island exists (suffix wins on a shared id). */
  function loadedToolCalls(sessionId) {
    const suffix = toolCallsBySession.value.get(sessionId) || []
    const island = islandBySession.value.get(sessionId)
    if (!island) {
      _loadedToolCallsCache.delete(sessionId) // do not pin a dropped island's union
      return suffix
    }
    const suffixIndex = toolIndexBySession.get(sessionId)
    // Issue #2030 stage 4 PR 4b: this union was rebuilt (filter + concat over every tool) per call,
    // and MessageList reads it on every reactive pass. The inputs are append-only arrays that are
    // replaced (not edited) on a merge/trim, so identity + length of each input is a complete
    // revision key for the cached union.
    const cached = _loadedToolCallsCache.get(sessionId)
    if (cached && cached.island === island.toolCalls && cached.islandLength === island.toolCalls.length &&
        cached.suffix === suffix && cached.suffixLength === suffix.length && cached.suffixIndex === suffixIndex) {
      return cached.result
    }
    const result = island.toolCalls.filter(tool => !suffixIndex?.has(tool.id)).concat(suffix)
    _loadedToolCallsCache.set(sessionId, {
      island: island.toolCalls, islandLength: island.toolCalls.length,
      suffix, suffixLength: suffix.length, suffixIndex, result,
    })
    return result
  }
  // sessionId -> { island, islandLength, suffix, suffixLength, suffixIndex, result } (non-reactive)
  const _loadedToolCallsCache = new Map()

  /** One tool call by id from the loaded intervals (the reactive entry, so UI state written to it renders). */
  function findLoadedToolCall(sessionId, toolUseId) {
    const toolIndex = toolIndexBySession.get(sessionId)
    const idx = toolIndex?.get(toolUseId)
    if (idx !== undefined) return toolCallsBySession.value.get(sessionId)?.[idx]
    const island = islandBySession.value.get(sessionId)
    const islandIdx = _islandTargets.get(sessionId)?.toolIndex.get(toolUseId)
    return island && islandIdx !== undefined ? island.toolCalls[islandIdx] : undefined
  }

  // Issue #2030 stage 4 PR 4b: subscribers of live tool completions. Fired from
  // _applyToolCallRecord for source === 'live' records that leave the entry completed/error —
  // history pages (load / backfill / island / resync / archive) are not news and never fire.
  // The same tool may fire more than once (duplicate terminal updates); subscribers dedupe.
  const toolCompletedListeners = new Set()
  // Subscribers of `(sessionId)` when an already-loaded live session's suffix is re-fetched
  // (resync after stream_reset/evicted, or a repeated first-load): completions that happened while
  // the stream was down arrive as history, which onToolCompleted deliberately ignores.
  const historyReloadListeners = new Set()

  function _notifyListeners(listeners, ...args) {
    for (const listener of listeners) {
      try {
        listener(...args)
      } catch (e) {
        console.warn('history listener failed:', e)
      }
    }
  }

  /** Subscribes `listener(sessionId)` to reloads of an already-loaded session; returns unsubscribe. */
  function onHistoryReloaded(listener) {
    historyReloadListeners.add(listener)
    return () => historyReloadListeners.delete(listener)
  }

  /** Subscribes `listener(sessionId, toolUseId, toolName)`; returns the unsubscribe function. */
  function onToolCompleted(listener) {
    toolCompletedListeners.add(listener)
    return () => toolCompletedListeners.delete(listener)
  }

  function _touchesCompactionHooks(record) {
    const initData = record.type === 'system' ? record.metadata?.init_data : null
    return !!initData && (initData.status === 'compacting' || initData.hook_event === 'PreCompact')
  }

  /**
   * Replays a window of HISTORY records (an older backfill page, or an island window) into a fresh
   * record target, leaving the "latest wins" session state exactly as the newer records set it:
   * lastStopReason / deferredToolUse / launch timestamp keep any value already set (restored
   * after the replay), init data is not captured, the Task store is never touched, subagent
   * narration appended while replaying is moved in front of what is already held, and the page's
   * tool calls are marked as backfilled (history, not news). Returns the filled target (`older`
   * is a fresh one unless the caller grows an existing target with newer records).
   */
  function _replayHistoryRecords(sessionId, records, source, older = _createRecordTarget(), capture = null, newer = false) {
    const savedStop = lastStopReasonBySession.value.has(sessionId) ? [lastStopReasonBySession.value.get(sessionId)] : null
    const savedDeferred = deferredToolUseBySession.value.has(sessionId) ? [deferredToolUseBySession.value.get(sessionId)] : null
    const savedLaunch = launchTimestampBySession.value.has(sessionId) ? [launchTimestampBySession.value.get(sessionId)] : null
    const narrationLengths = new Map()
    for (const [taskId, perLeg] of narrationByTaskIdAndLeg.value) {
      for (const [legIndex, list] of perLeg) narrationLengths.set(`${taskId}:${legIndex}`, list.length)
    }

    _replayingOlder = true
    try {
      if (source === 'archive') {
        _applyArchiveRecords(sessionId, records, older, { captureInit: false })
      } else {
        for (const record of records) applyRecord(sessionId, record, 'load', { notify: false, target: older })
      }
    } finally {
      _replayingOlder = false
    }

    // Latest wins: a value the newer records already determined must survive the older replay.
    // If nothing newer determined one yet (the tail held no result / user message / launch), the
    // older page's value is the latest there is and is kept.
    // An island window (`capture` given) never leaves a value behind: it is older than the suffix
    // and the gap pages between them must still be able to beat it, so what it set is handed back
    // to the caller (applied only if nothing else set one by the time it merges).
    const restore = (key, map, saved) => {
      if (capture) {
        const now = map.value.has(sessionId) ? [map.value.get(sessionId)] : null
        if (now && (!saved || now[0] !== saved[0])) capture[key] = now
        if (!saved) map.value.delete(sessionId)
      }
      if (saved) map.value.set(sessionId, saved[0])
    }
    restore('stop', lastStopReasonBySession, savedStop)
    restore('deferred', deferredToolUseBySession, savedDeferred)
    restore('launch', launchTimestampBySession, savedLaunch)
    for (const [taskId, perLeg] of narrationByTaskIdAndLeg.value) {
      for (const [legIndex, list] of perLeg) {
        const added = list.length - (narrationLengths.get(`${taskId}:${legIndex}`) || 0)
        // older records go in front of what is held; records NEWER than the held ones (island growth) stay at the end
        if (added > 0 && !newer) perLeg.set(legIndex, list.splice(list.length - added, added).concat(list))
      }
    }

    let backfilled = _backfilledToolIds.get(sessionId)
    if (!backfilled) _backfilledToolIds.set(sessionId, (backfilled = new Set()))
    for (const tool of older.toolCalls) backfilled.add(tool.id)
    return older
  }

  /**
   * Prepend-merge of an older, contiguous page (oldest -> newest) UNDER the installed state.
   *
   * The page is replayed into its own accumulator (`applyRecord(..., target)`), then merged
   * (utils/historyMerge.js): messages are older ++ current; for a tool call present in both, the
   * newer (installed) entry wins field by field and the older record only fills what is absent,
   * so an older record's pre-streaming `input: {}` can never clobber a completed tool's input.
   *
   * Side effects that are "latest wins" in a full load must not be overwritten by an older page:
   * lastStopReason / deferredToolUse / launch timestamp keep any value already set by newer
   * records (restored after the replay), init data
   * is not captured (the window carries `latest_init`), and the Task store is never touched
   * (task state comes from the server snapshot, not from history replay). Subagent narration
   * appended while replaying is moved in front of the narration already held.
   */
  function applyOlderPage(sessionId, records, { source = 'load' } = {}) {
    const current = {
      messages: messagesBySession.value.get(sessionId) || [],
      messageIndex: messageIndexBySession.get(sessionId) || new Map(),
      toolCalls: toolCallsBySession.value.get(sessionId) || [],
      toolIndex: toolIndexBySession.get(sessionId) || new Map(),
      retryIndex: retryIndexBySession.get(sessionId) || new Map(),
    }

    const older = _replayHistoryRecords(sessionId, records, source)

    const merged = mergeOlderPage(current, older)
    _installRecordTarget(sessionId, merged)
    messagesBySession.value = new Map(messagesBySession.value)
    toolCallsBySession.value = new Map(toolCallsBySession.value)
    narrationByTaskIdAndLeg.value = new Map(narrationByTaskIdAndLeg.value)
    // The hook-correlation cache is kept across head-only prepends while the history is incomplete
    // (see getHookCorrelation), but a compaction group / PreCompact hook that just arrived must
    // show its indicators now — compaction groups are keyed by message_id, so they are correct
    // as soon as their records are loaded.
    if (records.some(_touchesCompactionHooks)) _hookCorrelationCache.delete(sessionId)
    pushDebugEvent('message', 'history-older-page', { sessionId, records: records.length, prepended: merged.prependedMessageCount })
    return merged.prependedMessageCount
  }

  /**
   * Issue #1747 / #2140: page through GET /api/sessions/{id}/messages, accumulating
   * results until the backend reports has_more === false, so callers never silently
   * truncate history at a single page's size.
   *
   * Issue #2140: each page's records are applied and installed into the reactive store
   * immediately after that page completes (via `state.target`/`state.nextOffset`, the same
   * accumulator tracked in _hydrationLoadStateBySession) instead of all at once at the end.
   * A per-page inactivity timer (`inactivityMs`) aborts only the in-flight page's own fetch,
   * composed with the caller's own `signal` (the claimed session-level controller) via
   * AbortSignal.any() — either one aborting ends the current page's fetch; the outer signal
   * firing propagates as a plain abort, the per-page timer firing propagates as a distinguished
   * timeout error naming the page. An epoch mismatch between pages (Backend restarted
   * mid-load) discards this session's data and throws a distinguished restart-required error
   * instead of silently mixing two processes' data.
   */
  async function fetchAllMessagePages(sessionId, pageSize, options = {}) {
    const {
      signal: outerSignal,
      inactivityMs = DEFAULT_HISTORY_PAGE_INACTIVITY_MS,
      onPageComplete,
      state,
    } = options

    let offset = state.nextOffset
    let totalCount = state.totalCount
    let eventCursor
    const messages = []

    for (let iteration = 0; iteration < MAX_PAGINATION_ITERATIONS; iteration++) {
      const pageIndex = Math.floor(offset / pageSize)
      const pageController = new AbortController()
      const fetchSignal = outerSignal ? AbortSignal.any([outerSignal, pageController.signal]) : pageController.signal
      const timeoutId = setTimeout(() => pageController.abort(), inactivityMs)

      let data
      try {
        data = await api.get(
          `/api/sessions/${sessionId}/messages?limit=${pageSize}&offset=${offset}`,
          { signal: fetchSignal }
        )
      } catch (err) {
        if (outerSignal?.aborted) {
          // The shared session-level controller is what fired — a genuine abort (claimed by
          // a concurrent Retry/resync attempt), not a page stall. Propagate as-is.
          throw err
        }
        if (pageController.signal.aborted) {
          const timeoutErr = new Error(
            `Timed out loading page ${pageIndex} (offset ${offset}) for session ${sessionId}`
          )
          timeoutErr.isHydrationTimeout = true
          timeoutErr.page = pageIndex
          pushDebugEvent('message', 'hydration-timeout', { sessionId, stage: 'loading_history', page: pageIndex })
          throw timeoutErr
        }
        throw err
      } finally {
        clearTimeout(timeoutId)
      }

      const pageMessages = data.messages || []
      totalCount = data.total_count || (offset + pageMessages.length)
      if (data.event_cursor !== undefined) {
        eventCursor = data.event_cursor
      }

      // Issue #2140 (AC6): a later page reporting a different PROCESS_EPOCH than the first
      // page means Backend restarted mid-load — resuming against new process state would
      // silently mix two processes' data. Discard what this attempt has installed so far and
      // surface a distinguished error so the caller restarts cleanly from offset 0.
      if (state.epoch === undefined) {
        state.epoch = data.epoch
      } else if (data.epoch !== undefined && data.epoch !== state.epoch) {
        pushDebugEvent('message', 'hydration-restart', { sessionId, oldEpoch: state.epoch, newEpoch: data.epoch })
        // Issue #2140 (builder-review fix): delete the tracked entry BEFORE calling
        // clearMessages() — clearMessages() aborts a still-pending entry's controller as
        // part of its own cleanup (see its own comment), and that controller here is THIS
        // attempt's own `state.controller`, the same one the caller (runDataHydrationChain)
        // is about to check for "was I pre-empted by someone else" once this throw
        // propagates. Deleting first means clearMessages() finds nothing to abort, so this
        // attempt's own controller stays unaborted and the caller correctly reaches its
        // isHydrationRestartRequired handling instead of mistaking this restart for a
        // concurrent-claim pre-emption and silently swallowing it.
        _hydrationLoadStateBySession.delete(sessionId)
        clearMessages(sessionId)
        const restartErr = new Error(
          `Backend restarted mid-load for session ${sessionId} (epoch ${state.epoch} -> ${data.epoch})`
        )
        restartErr.isHydrationRestartRequired = true
        throw restartErr
      }

      messages.push(...pageMessages)
      _applyPageToTarget(sessionId, pageMessages, state.target)
      _installPageAndNotify(sessionId, state.target)
      state.totalCount = totalCount

      pushDebugEvent('message', 'history-page', { sessionId, offset, records: pageMessages.length, totalCount })
      if (onPageComplete) {
        onPageComplete({ page: pageIndex, recordsReceived: state.target.messages.length, totalCount })
      }

      const hasMore = data.has_more || false
      if (!hasMore) {
        return { messages, totalCount, hasMore: false, eventCursor }
      }

      // Advance by the requested page size, not pageMessages.length: the backend's
      // offset/limit pagination applies to raw stored lines, but the returned
      // `messages` array can contain more entries than raw lines consumed (synthetic
      // tool_call messages are interleaved). Advancing by the response length would
      // desync from the backend's raw-line cursor and skip messages.
      offset += pageSize
      state.nextOffset = offset
    }

    console.error(
      `fetchAllMessagePages: exceeded ${MAX_PAGINATION_ITERATIONS} pages for session ${sessionId} ` +
      `(${messages.length} messages loaded so far). Stopping to avoid a runaway request storm — ` +
      `this indicates the backend has_more contract was violated.`
    )
    return { messages, totalCount, hasMore: true, eventCursor }
  }

  /**
   * Load messages for a session from backend.
   *
   * Issue #2140: `options` is only meaningful for the no-`limit` (full pagination) path:
   * `{ signal, inactivityMs, resume, onPageComplete }`. `resume: true` continues the tracked
   * in-progress accumulator (_hydrationLoadStateBySession) from its last completed page
   * instead of restarting at offset 0; omitted/false always starts fresh regardless of any
   * pre-existing entry for this session. `signal`, if omitted, is obtained by claiming (or
   * reusing an already-claimed) controller for this session — callers that already called
   * claimHydrationLoad() themselves (retryHydration(), polling.js's resync) pass its signal
   * through instead of claiming a second time.
   */
  async function loadMessages(sessionId, limit = null, offset = 0, options = {}) {
    // Issue #2030: tail-first mode — one turn-snapped window, older history backfills separately.
    if (options.window === 'tail') return loadHistoryTail(sessionId, options)
    try {
      let messages, totalCount, hasMore, eventCursor

      // Issue #1955: any live streaming preview for this session is purely cosmetic and was
      // never part of the canonical message array — discard it outright instead of merging.
      // The freshly-fetched history is authoritative; there is nothing to reconcile it against.
      _discardStreamingPreview(sessionId, 'reload')

      if (limit) {
        // Explicit limit requested: preserve today's single-shot-fetch behavior.
        const data = await api.get(
          `/api/sessions/${sessionId}/messages?limit=${limit}&offset=${offset}`
        )
        messages = data.messages || []
        totalCount = data.total_count || messages.length
        hasMore = data.has_more || false
        eventCursor = data.event_cursor

        // Issue #2110 (stage 4b-B) / #2131: thin fetch-then-apply-per-record wrapper — this
        // session's display list and tool list are rebuilt from scratch into a local plain
        // accumulator (outside the reactive Map/array proxies — see applyRecord()'s own
        // comment), then installed as this session's real state in one shot.
        const target = _createRecordTarget()
        _applyPageToTarget(sessionId, messages, target)
        _installPageAndNotify(sessionId, target)
      } else {
        // Issue #1747: no limit specified — page until has_more is false so the full
        // history loads, instead of silently truncating at one page.
        // Issue #2140 (builder-review fix): incremental per-page install + resumable/
        // abortable shared state. A caller that already called claimHydrationLoad() itself
        // (session.js's runDataHydrationChain, polling.js's _resyncSession) passes its
        // signal through here; ANY other caller (e.g. polling.js's state_change-to-'error'
        // handler, which calls this bare) gets claimed right here instead. This is
        // deliberately unconditional — never "reuse the existing entry's controller without
        // aborting it" — so a bare/unclaimed call can never silently race an already-running
        // fetchAllMessagePages() loop over the same shared state: it always aborts whatever
        // was there first, exactly like every other entry point into this shared tracker.
        const { signal: callerSignal, inactivityMs, resume, onPageComplete } = options
        const signal = callerSignal || claimHydrationLoad(sessionId).signal
        const state = _hydrationLoadStateBySession.get(sessionId)
        if (!resume) {
          state.target = _createRecordTarget()
          state.nextOffset = offset || 0
          state.epoch = undefined
          state.totalCount = 0
        }

        const result = await fetchAllMessagePages(sessionId, 10000, { signal, inactivityMs, onPageComplete, state })
        messages = result.messages
        totalCount = result.totalCount
        hasMore = result.hasMore
        eventCursor = result.eventCursor

        if (!hasMore) {
          _hydrationLoadStateBySession.delete(sessionId)
        }
      }

      // Issue #1000: Store event cursor from REST response for poll alignment
      if (eventCursor !== undefined) {
        loadedEventCursors.set(sessionId, eventCursor)
      }

      console.log(`Loaded ${messages.length} of ${totalCount} messages for session ${sessionId}`)

      const regularMessages = messagesBySession.value.get(sessionId) || []

      // Warn if there are more messages we didn't load
      if (hasMore) {
        console.warn(`Warning: Only loaded ${regularMessages.length} of ${totalCount} messages. Some messages may be missing.`)
      }

      return { messages: regularMessages, totalCount, hasMore }
    } catch (error) {
      console.error('Failed to load messages:', error)
      throw error
    }
  }

  /**
   * Issue #1746 (stage: subagents) / #1765: apply one Task lifecycle frame
   * (task_started/task_progress/task_notification/task_updated) to taskLegsByTaskId.
   * Mirrors src/task_registry.py's TaskLegRegistry.apply_frame() exactly, so live streaming
   * and (via hydrateBackgroundAgents' backend snapshot) reload converge on the same state.
   */
  function applyTaskLifecycleFrame(sessionId, subtype, metadata, timestamp) {
    const taskId = metadata?.task_id
    if (!taskId || !TASK_LIFECYCLE_SUBTYPES.has(subtype)) return

    if (subtype === 'task_started') {
      if (NON_AGENT_TASK_TYPES.has(metadata.task_type)) return
      let entry = taskLegsByTaskId.value.get(taskId)
      if (!entry) {
        entry = { task_id: taskId, session_id: sessionId, legs: [] }
        taskLegsByTaskId.value.set(taskId, entry)
      }
      const toolUseId = metadata.tool_use_id || null
      // Idempotency guard: a reconnect/replay must not duplicate a leg already recorded
      // (e.g. hydrateBackgroundAgents' snapshot already included it).
      if (toolUseId && entry.legs.some(leg => leg.tool_use_id === toolUseId)) return
      entry.legs.push({
        tool_use_id: toolUseId,
        description: metadata.description || null,
        started_at: timestamp,
        last_progress_at: timestamp,
        ended_at: null,
        status: 'running',
      })
      if (toolUseId) taskIdByLaunchToolUseId.value.set(toolUseId, taskId)
      taskLegsByTaskId.value = new Map(taskLegsByTaskId.value)
      taskIdByLaunchToolUseId.value = new Map(taskIdByLaunchToolUseId.value)
      return
    }

    const entry = taskLegsByTaskId.value.get(taskId)
    if (!entry) return
    const leg = entry.legs[entry.legs.length - 1]
    if (!leg) return

    if (subtype === 'task_progress') {
      if (leg.status !== 'running') return
      leg.last_progress_at = timestamp
      if (metadata.description && !leg.description) leg.description = metadata.description
      taskLegsByTaskId.value = new Map(taskLegsByTaskId.value)
      return
    }

    // task_notification / task_updated: terminal status carrier. First-terminal-wins.
    if (leg.status !== 'running') return
    const rawStatus = subtype === 'task_notification'
      ? metadata.status
      : (metadata.status || metadata.patch?.status)
    if (!TERMINAL_TASK_STATUSES.has(rawStatus)) return
    leg.status = TASK_STATUS_DISPLAY_MAP[rawStatus] || rawStatus
    leg.ended_at = timestamp
    // Mirrors src/task_registry.py: only task_notification carries the subagent's own
    // final report (`summary`) — task_updated (TaskStop-only termination) has no such field.
    if (subtype === 'task_notification' && metadata.summary) leg.result = metadata.summary
    taskLegsByTaskId.value = new Map(taskLegsByTaskId.value)
  }

  /**
   * Issue #1746 (stage: subagents): seed taskLegsByTaskId/taskIdByLaunchToolUseId from the
   * backend's already-reduced snapshot (GET /api/sessions/{id}/background_agents) — the
   * reload/reconnect source of truth, so gutter/anchor state is correct from a cold load
   * without waiting for live frames to replay. Call before loadMessages() for a session.
   */
  async function hydrateBackgroundAgents(sessionId) {
    try {
      const data = await api.get(`/api/sessions/${sessionId}/background_agents`)
      const agents = data.agents || []
      for (const agent of agents) {
        const taskId = agent.task_id
        if (!taskId) continue
        const legs = (agent.legs || []).map(leg => ({ ...leg }))
        taskLegsByTaskId.value.set(taskId, { task_id: taskId, session_id: sessionId, legs })
        for (const leg of legs) {
          if (leg.tool_use_id) taskIdByLaunchToolUseId.value.set(leg.tool_use_id, taskId)
        }
      }
      taskLegsByTaskId.value = new Map(taskLegsByTaskId.value)
      taskIdByLaunchToolUseId.value = new Map(taskIdByLaunchToolUseId.value)
      _rebuildSubagentNarration(sessionId)
    } catch (error) {
      console.warn(`Failed to hydrate background agents for session ${sessionId}:`, error)
    }
  }

  /**
   * Issue #2030: the tail window is installed as soon as it arrives, in parallel with the
   * background-agents snapshot, so subagent narration in the window can be replayed BEFORE the
   * snapshot has bridged launch tool_use ids to task ids (and is then silently unrouted). Re-route
   * whatever is loaded once the snapshot lands. Idempotent: narration of this session's tasks is
   * rebuilt from the loaded messages, in message order.
   */
  function _rebuildSubagentNarration(sessionId) {
    const messages = messagesBySession.value.get(sessionId)
    if (!messages || !messages.some(m => m.type === 'assistant' && m.metadata?.parent_tool_use_id)) return
    for (const [taskId, entry] of taskLegsByTaskId.value) {
      if (entry.session_id === sessionId) narrationByTaskIdAndLeg.value.delete(taskId)
    }
    for (const message of messages) {
      if (message.type === 'assistant' && message.metadata?.parent_tool_use_id) _routeSubagentNarration(message)
    }
    narrationByTaskIdAndLeg.value = new Map(narrationByTaskIdAndLeg.value)
  }

  /** Issue #1746 (stage: subagents): all known legs for a task_id, or null. */
  function getTaskLegEntry(taskId) {
    if (!taskId) return null
    return taskLegsByTaskId.value.get(taskId) || null
  }

  /**
   * Issue #1746 (stage: subagents) follow-up: every known task's leg entry for a session, so
   * MessageList.vue can surface leg-terminal (completed/failed/stopped) events as their own
   * main-timeline signals — spec §4.2's causal-moment list, not just the subagent's own nested
   * card. taskLegsByTaskId itself is intentionally not exposed raw (see getTaskLegEntry).
   */
  function allTaskLegEntriesForSession(sessionId) {
    if (!sessionId) return []
    return Array.from(taskLegsByTaskId.value.values()).filter(e => e.session_id === sessionId)
  }

  /** Issue #1746 (stage: subagents): resolve a launch/resume Task tool_use_id to its task_id. */
  function getTaskIdForLaunchToolUse(toolUseId) {
    if (!toolUseId) return null
    return taskIdByLaunchToolUseId.value.get(toolUseId) || null
  }

  /**
   * Issue #1746 (stage: subagents) follow-up (real repro data): parent_tool_use_id on ALL of a
   * subagent's child activity — its original run AND every subsequent resume — stays pinned to
   * the very FIRST leg's own launching tool_use_id. A resume (triggered via
   * `SendMessage(to: "<agent name>")`, not a fresh Task/Agent call) never becomes any child's
   * parent_tool_use_id itself; the underlying SDK continues reporting the original launch's id
   * for the whole nested session it's waking back up. So which LEG a piece of activity belongs
   * to can't be resolved by matching parent_tool_use_id against a specific leg's own id (that
   * always resolves to leg 0) — it has to be resolved by WHEN the activity happened: the most
   * recent leg whose started_at is at or before the activity's own timestamp.
   */
  function _resolveLegIndexForTimestamp(entry, timestamp) {
    if (!entry || !entry.legs.length) return -1
    if (timestamp == null) return entry.legs.length - 1
    let idx = 0
    for (let i = 0; i < entry.legs.length; i++) {
      const startedAt = entry.legs[i].started_at
      if (startedAt == null || startedAt <= timestamp) idx = i
      else break
    }
    return idx
  }

  /**
   * Issue #1746 (stage: subagents): route a subagent assistant message (narration) into
   * narrationByTaskIdAndLeg, resolved via its parent_tool_use_id (bridges to the task_id — see
   * _resolveLegIndexForTimestamp for why the LEG itself is then resolved by timestamp, not by
   * matching parent_tool_use_id against a specific leg's own id).
   */
  function _routeSubagentNarration(message) {
    const parentId = message.metadata?.parent_tool_use_id
    if (!parentId) return false
    const taskId = taskIdByLaunchToolUseId.value.get(parentId)
    if (!taskId) return false
    const entry = taskLegsByTaskId.value.get(taskId)
    const legIndex = _resolveLegIndexForTimestamp(entry, message.timestamp)
    if (legIndex === -1) return false

    if (!narrationByTaskIdAndLeg.value.has(taskId)) {
      narrationByTaskIdAndLeg.value.set(taskId, new Map())
    }
    const perLeg = narrationByTaskIdAndLeg.value.get(taskId)
    if (!perLeg.has(legIndex)) perLeg.set(legIndex, [])
    const list = perLeg.get(legIndex)
    // (an island replacing another one replays records already routed)
    if (message.message_id && list.some(m => m.message_id === message.message_id)) return true
    list.push(message)
    narrationByTaskIdAndLeg.value = new Map(narrationByTaskIdAndLeg.value)
    return true
  }

  /**
   * Issue #1746 (stage: subagents) follow-up: child tool calls for one specific leg, resolved
   * the same timestamp-window way as narration (see _resolveLegIndexForTimestamp) — NOT by
   * matching parent_tool_use_id against that leg's own launch/resume tool_use_id, since only
   * the very first leg's id ever appears as a child's parent_tool_use_id.
   */
  function childToolCallsForLeg(sessionId, taskId, legIndex) {
    if (!sessionId || !taskId) return []
    const entry = taskLegsByTaskId.value.get(taskId)
    if (!entry || !entry.legs.length) return []
    const rootToolUseId = entry.legs[0].tool_use_id
    if (!rootToolUseId) return []

    const windowStart = entry.legs[legIndex]?.started_at ?? -Infinity
    const windowEnd = entry.legs[legIndex + 1]?.started_at ?? Infinity

    const toolCalls = loadedToolCalls(sessionId)
    return toolCalls.filter(tc => {
      if (tc.parent_tool_use_id !== rootToolUseId) return false
      const ts = typeof tc.timestamp === 'number' ? tc.timestamp : new Date(tc.timestamp).getTime() / 1000
      return ts >= windowStart && ts < windowEnd
    })
  }

  /** Issue #1746 (stage: subagents): narration messages for one leg, oldest first. */
  function narrationForLeg(taskId, legIndex) {
    if (!taskId) return []
    return narrationByTaskIdAndLeg.value.get(taskId)?.get(legIndex) || []
  }

  // Issue #1746 (stage: subagents) follow-up: per-leg transcript expand state, shared here
  // (not a local component ref) — the global gutter chip (SubagentGlobalGutter.vue, rendered
  // once in MessageList.vue) and the inline anchor row (rendered deep inside AssistantMessage
  // -> SubagentTimeline) are separate DOM subtrees that both need to control/read the SAME
  // leg's expand state.
  const expandedLegs = ref(new Map()) // Map<`${taskId}:${legIndex}`, boolean>

  function isLegExpanded(taskId, legIndex) {
    if (!taskId) return false
    return !!expandedLegs.value.get(`${taskId}:${legIndex}`)
  }

  function setLegExpanded(taskId, legIndex, value) {
    if (!taskId) return
    expandedLegs.value.set(`${taskId}:${legIndex}`, value)
    expandedLegs.value = new Map(expandedLegs.value)
  }

  function toggleLegExpanded(taskId, legIndex) {
    setLegExpanded(taskId, legIndex, !isLegExpanded(taskId, legIndex))
  }

  // Issue #1748 (stage: windowing): which tool detail card is expanded within a given
  // ActivityTimeline instance — shared here (not a local component ref), same reason as
  // expandedLegs above: at a real (non-full) overscan value, a row scrolled out of the mounted
  // range fully unmounts and remounts with a fresh setup() on scroll-back, which would silently
  // collapse a tool card the user had deliberately expanded to read. `scopeKey` is whatever the
  // caller uses to identify one ActivityTimeline's own tool group (a messageId, or a fallback);
  // only one tool is ever expanded per scope, matching the pre-#1748 single-ref behavior.
  //
  // Issue #1748 (stage: windowing) review fix: `autoPermission` tracks whether the CURRENT
  // expansion was auto-triggered by a tool entering permission_required (vs. a manual click) —
  // ActivityTimeline's own watch uses this to auto-collapse only what it auto-expanded, never a
  // manual expand. This must be store-backed too (not a second local ref, as first written):
  // if the permission resolves off-screen (e.g. via PermissionQueue's always-mounted floating
  // panel) while the row is unmounted, a fresh mount with a local `expandedForPermission = false`
  // would forget the row was auto-expanded and never auto-collapse it on remount, leaving a
  // resolved permission's detail panel stuck open. Reproduced and confirmed during review.
  const expandedTimelineTool = ref(new Map()) // Map<scopeKey, toolId>
  const expandedTimelineToolAutoPermission = ref(new Map()) // Map<scopeKey, boolean>

  function getExpandedTimelineTool(scopeKey) {
    if (!scopeKey) return null
    return expandedTimelineTool.value.get(scopeKey) ?? null
  }

  function isExpandedTimelineToolAutoPermission(scopeKey) {
    if (!scopeKey) return false
    return !!expandedTimelineToolAutoPermission.value.get(scopeKey)
  }

  function setExpandedTimelineTool(scopeKey, toolId, { autoPermission = false } = {}) {
    if (!scopeKey) return
    if (toolId == null) {
      expandedTimelineTool.value.delete(scopeKey)
    } else {
      expandedTimelineTool.value.set(scopeKey, toolId)
    }
    expandedTimelineTool.value = new Map(expandedTimelineTool.value)
    expandedTimelineToolAutoPermission.value.set(scopeKey, toolId != null && autoPermission)
    expandedTimelineToolAutoPermission.value = new Map(expandedTimelineToolAutoPermission.value)
  }

  // Issue #1748 (stage: windowing) review fix: prune stale entries on session reset/archive-clear
  // — mirrors expandedLegs' per-session cleanup a few lines up. scopeKey is a message id or a
  // tool_use id (optionally suffixed `-run-N` for SubagentLegTranscript's multi-run case), so
  // matching against every message id / tool_use id still known for this session (before it's
  // deleted) catches both forms without needing to recompute exact run indices.
  function pruneExpandedTimelineToolForSession(sessionId) {
    const messages = messagesBySession.value.get(sessionId) || []
    const knownIds = new Set()
    for (const msg of messages) {
      if (msg.id) knownIds.add(msg.id)
      if (msg.message_id) knownIds.add(msg.message_id)
      for (const t of msg.metadata?.tool_uses || []) {
        if (t.id) knownIds.add(t.id)
      }
    }
    if (knownIds.size === 0) return
    let changed = false
    for (const key of expandedTimelineTool.value.keys()) {
      const runSuffixIndex = key.indexOf('-run-')
      const baseId = runSuffixIndex === -1 ? key : key.slice(0, runSuffixIndex)
      if (knownIds.has(key) || knownIds.has(baseId)) {
        expandedTimelineTool.value.delete(key)
        expandedTimelineToolAutoPermission.value.delete(key)
        changed = true
      }
    }
    if (changed) {
      expandedTimelineTool.value = new Map(expandedTimelineTool.value)
      expandedTimelineToolAutoPermission.value = new Map(expandedTimelineToolAutoPermission.value)
    }
  }

  // Issue #1748 (stage: windowing) review fix: ThinkingBlock's expand/collapse toggle is
  // store-backed for the same remount-survival reason as expandedTimelineTool above — flagged
  // during review as the same class of risk left unaddressed for this specific component (a
  // Thinking block is rendered per-segment inside AssistantMessage, itself inside a virtualized
  // row, so it unmounts/remounts exactly like ActivityTimeline does). `scopeKey` is the owning
  // segment's own message id (matching the :key already used for that segment in
  // AssistantMessage.vue), a simple boolean toggle — mirrors expandedLegs/isLegExpanded exactly.
  const thinkingBlockExpanded = ref(new Map()) // Map<scopeKey, boolean>

  function isThinkingBlockExpanded(scopeKey) {
    if (scopeKey == null) return false
    return !!thinkingBlockExpanded.value.get(scopeKey)
  }

  function toggleThinkingBlockExpanded(scopeKey) {
    if (scopeKey == null) return
    thinkingBlockExpanded.value.set(scopeKey, !isThinkingBlockExpanded(scopeKey))
    thinkingBlockExpanded.value = new Map(thinkingBlockExpanded.value)
  }

  function pruneThinkingBlockExpandedForSession(sessionId) {
    const messages = messagesBySession.value.get(sessionId) || []
    const knownIds = new Set()
    for (const msg of messages) {
      if (msg.id) knownIds.add(msg.id)
      if (msg.message_id) knownIds.add(msg.message_id)
    }
    if (knownIds.size === 0) return
    let changed = false
    for (const key of thinkingBlockExpanded.value.keys()) {
      if (knownIds.has(key)) {
        thinkingBlockExpanded.value.delete(key)
        changed = true
      }
    }
    if (changed) thinkingBlockExpanded.value = new Map(thinkingBlockExpanded.value)
  }

  // Issue #1843: collapsible CommCard expand/collapse toggle, store-backed for the same
  // remount-survival reason as thinkingBlockExpanded/expandedTimelineTool above (a comm
  // card is rendered inside a virtualized message row and unmounts/remounts on scroll).
  // scopeKey is toolCall.id for outbound comms, message.id || message.message_id for
  // inbound comms — a simple boolean toggle, mirrors thinkingBlockExpanded exactly.
  const expandedComms = ref(new Map()) // Map<scopeKey, boolean>

  function isCommExpanded(scopeKey) {
    if (scopeKey == null) return false
    return !!expandedComms.value.get(scopeKey)
  }

  function toggleCommExpanded(scopeKey) {
    if (scopeKey == null) return
    expandedComms.value.set(scopeKey, !isCommExpanded(scopeKey))
    expandedComms.value = new Map(expandedComms.value)
  }

  // Prunes against both message ids and tool_use ids (like pruneExpandedTimelineToolForSession)
  // since expandedComms scope keys come from both id shapes depending on direction.
  function pruneExpandedCommsForSession(sessionId) {
    const messages = messagesBySession.value.get(sessionId) || []
    const knownIds = new Set()
    for (const msg of messages) {
      if (msg.id) knownIds.add(msg.id)
      if (msg.message_id) knownIds.add(msg.message_id)
      for (const t of msg.metadata?.tool_uses || []) {
        if (t.id) knownIds.add(t.id)
      }
    }
    if (knownIds.size === 0) return
    let changed = false
    for (const key of expandedComms.value.keys()) {
      if (knownIds.has(key)) {
        expandedComms.value.delete(key)
        changed = true
      }
    }
    if (changed) expandedComms.value = new Map(expandedComms.value)
  }

  /**
   * Issue #1746 (stage: subagents): "needs attention" — true when a leg has an open permission
   * request on one of its own child tool calls. Resolved from already-available store data (no
   * new backend surface needed): any tool call whose parent_tool_use_id is the task's root
   * launch tool_use_id (see childToolCallsForLeg for why child activity always keys off the
   * root, never a specific leg's own id) and is currently awaiting a permission decision.
   */
  function hasOpenPermissionForTask(sessionId, taskId) {
    if (!sessionId || !taskId) return false
    const entry = taskLegsByTaskId.value.get(taskId)
    const rootToolUseId = entry?.legs?.[0]?.tool_use_id
    if (!rootToolUseId) return false
    const toolCalls = toolCallsBySession.value.get(sessionId) || []
    return toolCalls.some(tc =>
      tc.parent_tool_use_id === rootToolUseId && tc.status === 'permission_required'
    )
  }

  /**
   * Issue #1746 (stage: permissions): every open permission request in a session — main-session
   * and subagent alike — enriched for the floating PermissionQueue.vue surface. Resolved from
   * the same store data hasOpenPermissionForTask already reads; no new backend surface needed.
   * A tool call's parent_tool_use_id always pins to a task's FIRST leg's own launch tool_use_id
   * (see _resolveLegIndexForTimestamp's comment), so the task itself resolves in one lookup —
   * the OPEN leg is then whichever of that task's legs is currently 'running' (legs are
   * sequential, never concurrent, so this is unambiguous).
   */
  function openPermissionsForSession(sessionId) {
    if (!sessionId) return []
    const toolCalls = toolCallsBySession.value.get(sessionId) || []
    return toolCalls
      .filter(tc => tc.status === 'permission_required')
      .map(tc => {
        const taskId = tc.parent_tool_use_id ? getTaskIdForLaunchToolUse(tc.parent_tool_use_id) : null

        if (!taskId) {
          return {
            requestId: tc.permissionRequestId,
            toolCall: tc,
            taskId: null,
            legIndex: null,
            agentColor: getAssistantRowColor(),
            isSubagent: false,
            label: 'Main session',
          }
        }

        const entry = taskLegsByTaskId.value.get(taskId)
        const runningIdx = entry?.legs?.findIndex(leg => leg.status === 'running') ?? -1
        // Fall back to the most recent leg when none is currently 'running' (e.g. a terminal
        // frame lands before this leg's own child permission has resolved) — never leave
        // legIndex null just because the snapshot briefly has no running leg, or "view in
        // context" silently no-ops on a valid, still-open permission.
        const legIndex = runningIdx >= 0 ? runningIdx : (entry?.legs?.length ? entry.legs.length - 1 : -1)
        const leg = legIndex >= 0 ? entry.legs[legIndex] : null
        const launchToolCall = leg ? toolCalls.find(t => t.id === leg.tool_use_id) : null
        const launchInput = launchToolCall?.input || {}
        const label = leg?.description || launchInput.description || launchInput.prompt ||
          launchInput.summary || launchInput.message || 'Subagent task'

        return {
          requestId: tc.permissionRequestId,
          toolCall: tc,
          taskId,
          legIndex: legIndex >= 0 ? legIndex : null,
          agentColor: getAgentColor(slugifyAgentName(taskId)),
          isSubagent: true,
          label,
        }
      })
  }

  /**
   * Issue #2131: resolves the { messages, messageIndex, retryIndex } triple _applyMessageRecord
   * operates on. When `target` is supplied (loadMessages()/setArchiveMessages() building a
   * local plain accumulator outside the reactive proxy), it's used as-is — identical shape,
   * just not yet installed into the real per-session state. Otherwise resolves (lazily
   * creating) the real reactive-backed array plus its non-reactive index Maps, exactly
   * matching pre-#2131 behavior for every live/test call site.
   */
  function _resolveMessageTarget(sessionId, target) {
    if (target) return target
    if (!messagesBySession.value.has(sessionId)) {
      messagesBySession.value.set(sessionId, [])
    }
    if (!messageIndexBySession.has(sessionId)) {
      messageIndexBySession.set(sessionId, new Map())
    }
    if (!retryIndexBySession.has(sessionId)) {
      retryIndexBySession.set(sessionId, new Map())
    }
    return {
      messages: messagesBySession.value.get(sessionId),
      messageIndex: messageIndexBySession.get(sessionId),
      retryIndex: retryIndexBySession.get(sessionId),
    }
  }

  /** Issue #2131: same role as _resolveMessageTarget(), for the { toolCalls, toolIndex } pair. */
  function _resolveToolTarget(sessionId, target) {
    if (target) return target
    if (!toolCallsBySession.value.has(sessionId)) {
      toolCallsBySession.value.set(sessionId, [])
    }
    if (!toolIndexBySession.has(sessionId)) {
      toolIndexBySession.set(sessionId, new Map())
    }
    return {
      toolCalls: toolCallsBySession.value.get(sessionId),
      toolIndex: toolIndexBySession.get(sessionId),
    }
  }

  /**
   * Issue #2131: fresh plain accumulator for loadMessages()/setArchiveMessages() to replay a
   * whole session's history into via applyRecord()'s `target` param, entirely outside the
   * reactive Map/array proxies — see applyRecord()'s own comment for why.
   */
  function _createRecordTarget() {
    return {
      messages: [],
      messageIndex: new Map(),
      toolCalls: [],
      toolIndex: new Map(),
      retryIndex: new Map(),
    }
  }

  /**
   * Issue #2131: installs a finished _createRecordTarget() accumulator as this session's real
   * state — one assignment per Map, after the whole replay loop has already run against the
   * plain object. Callers still do their own `.value = new Map(...)` reactivity bump afterward.
   */
  function _installRecordTarget(sessionId, target) {
    messagesBySession.value.set(sessionId, target.messages)
    messageIndexBySession.set(sessionId, target.messageIndex)
    toolCallsBySession.value.set(sessionId, target.toolCalls)
    toolIndexBySession.set(sessionId, target.toolIndex)
    retryIndexBySession.set(sessionId, target.retryIndex)
  }

  /**
   * Issue #2131: the teardown counterpart to _installRecordTarget() — deletes the three
   * index Maps' entries for this session. Callers still separately delete
   * messagesBySession/toolCallsBySession themselves (clearMessages()/clearArchiveMessages()
   * delete those at a different point relative to their own pruning calls).
   */
  function _deleteRecordTargets(sessionId) {
    messageIndexBySession.delete(sessionId)
    toolIndexBySession.delete(sessionId)
    retryIndexBySession.delete(sessionId)
    _loadedToolCallsCache.delete(sessionId)
  }

  /**
   * Issue #2110 (stage 4b-B): the one entry point for every message/tool_call record,
   * regardless of where it came from (`source` ∈ 'live' | 'load' | 'archive'). `source`
   * gates ONLY notification/preview behavior (AC1) — every other side effect below runs
   * uniformly, closing the load/archive gaps the pre-refactor side-effect matrix documented
   * (truncation/deferral banners, subagent narration routing).
   *
   * `notify` (review fix): whether to trigger Vue reactivity immediately after applying this
   * one record — defaults to true for every normal caller (live dispatch, the content_block_
   * start pending-card synthesis, tests). `loadMessages()`/`setArchiveMessages()` pass `false`
   * while replaying a whole session's history in a tight loop and do ONE reassignment after the
   * loop instead: without this, each of N replayed records would individually reassign
   * messagesBySession.value (and friends), turning a full session load into O(N) reactive
   * broadcasts over an array of growing size — O(N²) render/diff work for the component
   * currently watching that session, where the old bulk-array-replace code was O(N). The DATA
   * mutation itself (push/set) always happens regardless of `notify` — only the "tell Vue"
   * step is deferred.
   *
   * `target` (issue #2131): optional { messages, messageIndex, toolCalls, toolIndex,
   * retryIndex } accumulator. Omitted by every existing call site (live dispatch, tests) —
   * applyRecord then resolves the real per-session reactive state as before, so the live path
   * is byte-for-byte unchanged. loadMessages()/setArchiveMessages() pass a local plain object
   * built fresh for the whole replay loop, so none of the O(n) cold-load work touches the
   * reactive Map/array proxies until the single assignment after the loop.
   */
  function applyRecord(sessionId, record, source, { notify = true, target = null } = {}) {
    if (!record || !record.type) return
    if (source === 'live') _noteLiveBytes(sessionId, record)
    if (record.type === 'tool_call') {
      _applyToolCallRecord(sessionId, record, notify, target, source)
      return
    }
    _applyMessageRecord(sessionId, record, source, notify, target)
  }

  /**
   * Issue #1955: the one dedup rule — is there already an entry with this backend id? No ->
   * push. Yes -> skip. `messagesBySession` is a pure mirror of the canonical/backend channel;
   * nothing else is ever spliced, merged, or replaced-in-place for streaming purposes (the
   * live-typing preview lives entirely in `streamingPreviewBySession` instead).
   *
   * Issue #2131: identity/retry lookups are O(1) via messageIndex/retryIndex (see
   * _resolveMessageTarget()) instead of O(n) `.some`/`.findIndex` scans — the `|| message.id`
   * dedup fallback is also dropped here: `message_id` is a required field minted once per
   * record by the backend's `_mint_message_id()`, and the `id` field that does appear in
   * fixtures belongs to nested tool_use content blocks, never a top-level message record.
   */
  function _applyMessageRecord(sessionId, message, source, notify = true, target = null) {
    // Issue #1486/#1575: drop internal SDK status/requesting messages — not displayable,
    // regardless of source.
    if (message.type === 'system') {
      const subtype = message.subtype || message.metadata?.subtype
      const status = message.metadata?.init_data?.status
      if (subtype === 'status' && status === 'requesting') {
        return
      }
    }

    const { messages, messageIndex, retryIndex } = _resolveMessageTarget(sessionId, target)

    // The one rule for identity/dedup: everything else below this point (api_retry collapse,
    // the push itself, and its side effects) is unconditional bookkeeping, not a second
    // identity check — this is the only place a message can be skipped as a duplicate.
    const dedupKey = message.message_id
    if (dedupKey && messageIndex.has(dedupKey)) {
      pushDebugEvent('message', 'dedup-skip', { sessionId, dedupKey })
      return
    }

    // Issue #894: api_retry in-place update — find existing message with same retry_message_id.
    // Open item (4b-B investigation): retry_message_id is injected into the live message_data
    // dict in SessionCoordinator._create_message_callback AFTER storage.append_message() has
    // already persisted the record (backend/claude_sdk.py::_process_sdk_message stores, then
    // invokes the callback with the same dict) — so a stored/archived api_retry record never
    // carries retry_message_id at all, by construction, not just by current absence. Unlike
    // subagent narration routing below (a gap that can close if archive task-leg hydration is
    // ever added), this one structurally can never fire for 'load'/'archive' — gated to 'live'
    // explicitly instead of left "uniform" for an invariant that will never hold otherwise.
    if (source === 'live' && message.metadata?.subtype === 'api_retry' && message.metadata?.retry_message_id) {
      const retryId = message.metadata.retry_message_id
      const existingIdx = retryIndex.get(retryId)
      if (existingIdx !== undefined) {
        messages[existingIdx] = markRaw({ ...messages[existingIdx], ...message })
        // Issue #2131 review fix: the merge above just overwrote this slot's message_id to
        // THIS attempt's own id — messageIndex must track it too (still pointing at
        // existingIdx, unchanged), or a later redelivery of this exact attempt would no
        // longer be recognized as a duplicate by the dedup check above (pre-#2131, the dedup
        // scan read message_id straight off the live array content, so this self-healed;
        // the cached index needs the same update explicitly). The superseded attempt's own
        // id is left in place too — harmless, since matching it still correctly points at
        // the (now-superseded) slot and a redelivery of IT should also be treated as a dup.
        if (dedupKey) messageIndex.set(dedupKey, existingIdx)
        if (notify) messagesBySession.value = new Map(messagesBySession.value)
        return
      }
      // No existing message: fall through to normal push (first in sequence)
    }

    const newIndex = messages.length
    messages.push(markRaw(message))
    if (dedupKey) messageIndex.set(dedupKey, newIndex)
    // Issue #2131 (AC1): gated the same as the collapse LOOKUP above (source === 'live') —
    // a stored/archived record structurally never carries retry_message_id (see the comment
    // above), so gating the two identically is simpler than an unconditional write and relies
    // on the same invariant the lookup already depends on, rather than a second one.
    if (source === 'live' && message.metadata?.retry_message_id) {
      retryIndex.set(message.metadata.retry_message_id, newIndex)
    }

    // Issue #1955 (AC1: source gates preview only): any canonical TOP-LEVEL assistant append
    // clears whatever the live preview was showing. Excludes subagent narration
    // (metadata.parent_tool_use_id set): those messages share this session's id but belong to a
    // background Task/Agent leg, not the top-level stream the preview mirrors. Scoped to 'live'
    // — 'load'/'archive' have no in-flight cosmetic preview to reconcile against.
    if (source === 'live' && message.type === 'assistant' && !message.metadata?.parent_tool_use_id) {
      const canonicalToolIds = (message.metadata?.tool_uses || []).map(t => t.id)
      _clearStreamingPreviewContent(sessionId, canonicalToolIds)
    }

    // Issue #662/#1300 (open item 2): truncation/deferral banners are independent of the
    // deleted orphan-tracking subsystem — applied uniformly now (closes a real load/archive
    // gap: reloading or archive-viewing a session truncated by max_tokens, or one with an open
    // deferred tool, previously never showed the corresponding banner).
    if (message.type === 'result') {
      const stopReason = message.metadata?.stop_reason || message.stop_reason || null
      lastStopReasonBySession.value.set(sessionId, stopReason)
      const dtu = message.metadata?.deferred_tool_use || null
      deferredToolUseBySession.value.set(sessionId, dtu)
      if (notify) {
        lastStopReasonBySession.value = new Map(lastStopReasonBySession.value)
        deferredToolUseBySession.value = new Map(deferredToolUseBySession.value)
      }
    }
    if (message.type === 'user' && !message.metadata?.has_tool_results) {
      deferredToolUseBySession.value.set(sessionId, null)
      lastStopReasonBySession.value.set(sessionId, null)
      if (notify) {
        deferredToolUseBySession.value = new Map(deferredToolUseBySession.value)
        lastStopReasonBySession.value = new Map(lastStopReasonBySession.value)
      }
    }

    // Issue #1746 (stage: subagents) / #1765: apply live Task lifecycle frames to the
    // task_id-first store. Scoped to 'live' — 'load' relies on the caller's own
    // hydrateBackgroundAgents() backend snapshot instead (see that function's own comment).
    // 'archive' has NO equivalent hydration call anywhere today (SessionView.vue's
    // loadArchiveMessages() calls neither hydrateBackgroundAgents() nor this frame application) —
    // archived sessions' subagent-timeline legs are a pre-existing, out-of-scope gap this stage
    // does not close, not something this uniform-vs-live split is actually covering for archive.
    // Left gated to 'live' (rather than made unconditional like subagent narration below) because
    // applying it uniformly here alone, without also adding the missing hydration/replay path for
    // archive, would not actually close that gap — it would just be unreachable code for 'archive'
    // the same way it is today, so there's no benefit to changing the gate without the real fix.
    if (source === 'live' && message.type === 'system') {
      const subtype = message.metadata?.subtype
      if (TASK_LIFECYCLE_SUBTYPES.has(subtype)) {
        applyTaskLifecycleFrame(sessionId, subtype, message.metadata, message.timestamp)
      }
      // Issue #2145: background-task snapshot. Live-only (history replay would feed stale
      // snapshots); client_launched/session_failed mean the previous process is gone.
      if (subtype === 'background_tasks_changed') {
        useBackgroundTasksStore().setTasks(sessionId, message.metadata?.init_data?.tasks)
      } else if (subtype === 'client_launched' || subtype === 'session_failed') {
        useBackgroundTasksStore().clear(sessionId)
      }
    }

    // Issue #1746 (stage: subagents) / open item: subagent narration routing, now applied
    // uniformly (closes the archive gap in the pre-refactor side-effect matrix). For archive
    // specifically this remains a no-op today — taskIdByLaunchToolUseId is never pre-seeded for
    // archive views (a separate, pre-existing gap outside this stage's scope) — but it costs
    // nothing to apply uniformly and stops being a no-op automatically if that gap is ever closed.
    if (message.type === 'assistant' && message.metadata?.parent_tool_use_id) {
      _routeSubagentNarration(message)
    }

    // Issue #1955 (AC1: source gates preview only) / AC2: restart/interrupt no longer sweep the
    // (deleted) browser-side orphan set — the backend's own tool_call record for an interrupted
    // tool already carries that terminal status directly. What's left here is purely the
    // cosmetic preview discard, scoped to 'live'.
    if (source === 'live' && message.type === 'system') {
      const subtype = message.metadata?.subtype
      if (subtype === 'client_launched') {
        _discardStreamingPreview(sessionId, 'restart')
      } else if (subtype === 'interrupt') {
        _discardStreamingPreview(sessionId, 'interrupt')
      }
    }

    // Launch timestamp tracking (issue #473) — uniform across sources (live and load already
    // did this identically before this stage).
    if (message.type === 'system' && message.metadata?.subtype === 'client_launched' && message.timestamp) {
      const ts = typeof message.timestamp === 'number'
        ? message.timestamp
        : new Date(message.timestamp).getTime() / 1000
      launchTimestampBySession.value.set(sessionId, ts)
    }

    // Trigger reactivity
    if (notify) messagesBySession.value = new Map(messagesBySession.value)
  }

  /**
   * Update a tool call (from WebSocket updates)
   *
   * Issue #2131: O(1) lookup via toolIndexBySession instead of an O(n) `.find` scan — a
   * long-running live session's tool list is itself unbounded.
   */
  function updateToolCall(sessionId, toolUseId, updates) {
    const toolCalls = toolCallsBySession.value.get(sessionId)
    const toolIndex = toolIndexBySession.get(sessionId)
    if (toolCalls && toolIndex) {
      const idx = toolIndex.get(toolUseId)
      const toolCall = idx !== undefined ? toolCalls[idx] : undefined
      if (toolCall) {
        Object.assign(toolCall, updates)

        // Trigger reactivity
        toolCallsBySession.value = new Map(toolCallsBySession.value)
      }
    }
  }

  /**
   * Handle permission response (user decision)
   */
  function handlePermissionResponse(sessionId, permissionResponse) {
    const toolUseId = permissionToToolMap.value.get(permissionResponse.request_id)

    if (toolUseId) {
      const updates = {
        permissionDecision: permissionResponse.decision,
        appliedUpdates: permissionResponse.applied_updates || []
      }

      if (permissionResponse.decision === 'allow') {
        updates.status = TOOL_STATUS_MAP.running
      } else {
        updates.status = TOOL_STATUS_MAP.denied
        updates.result = {
          error: true,
          message: permissionResponse.reasoning || 'Permission denied'
        }
        updates.isExpanded = false
      }

      // For AskUserQuestion, update the input with answers from updated_input
      if (permissionResponse.updated_input) {
        updates.input = permissionResponse.updated_input
      }

      updateToolCall(sessionId, toolUseId, updates)
    }
  }

  /**
   * Issue #2110 (stage 4b-B): denied/interrupted terminal-resolution side effects, shared by
   * both the create and update branches of _applyToolCallRecord so a tool whose very FIRST
   * observed record is already denied/interrupted (common on 'load'/'archive', which can
   * replay a tool's only-ever event) gets the same result message / orphaned stamp as one that
   * transitions into that state live.
   */
  function _applyTerminalResolution(toolCallObj, toolCall) {
    if (toolCall.status === 'denied') {
      toolCallObj.result = { error: true, message: 'Permission denied' }
      toolCallObj.isExpanded = false
    }
    if (toolCall.status === 'interrupted') {
      toolCallObj._isOrphaned = true
      toolCallObj._orphanedInfo = { reason: 'denied', message: 'Session was interrupted' }
      toolCallObj.isExpanded = false
    }
  }

  /**
   * Issue #324/#2110 (stage 4b-B): apply a unified tool_call record from the backend — the
   * complete tool lifecycle state in one payload, superseding separate tool_use/permission_
   * request/permission_response/tool_result correlation.
   *
   * `toolCall` fields: tool_use_id, name, input, status (pending/awaiting_permission/running/
   * completed/failed/denied/interrupted — the backend's own 7-value vocabulary), permission,
   * permission_granted, result, error, display.
   *
   * The backend status is normalized to the single frontend-display `status` field (5 values:
   * pending/permission_required/executing/completed/error — denied and interrupted both read as
   * 'completed', matching every direct `.status` consumer's expectation). Orphaned-ness (from
   * interrupted) is carried separately via the `_isOrphaned`/`_orphanedInfo` stamp, which
   * `useToolStatus.js`'s effectiveStatus/isOrphaned read directly — no backendStatus field, no
   * session-scoped orphan Map.
   */
  function _applyToolCallRecord(sessionId, toolCall, notify = true, target = null, source = 'live') {
    const toolUseId = toolCall.tool_use_id
    if (!toolUseId) {
      console.warn('Received tool_call without tool_use_id:', toolCall)
      return
    }

    // A record that is NOT part of an older-page/island replay is newer than any page: the entry now has a
    // record in the live part of the file, so a trim must not release it with an old page.
    if (!_replayingOlder) _toolPageBySession.get(sessionId)?.delete(toolUseId)
    const { toolCalls, toolIndex } = _resolveToolTarget(sessionId, target)
    // Issue #2131: O(1) identity lookup via toolIndex instead of an O(n) `.findIndex` scan.
    const existingIndex = toolIndex.has(toolUseId) ? toolIndex.get(toolUseId) : -1

    const frontendStatus = TOOL_STATUS_MAP[toolCall.status] || toolCall.status
    if (source === 'live') _backfilledToolIds.get(sessionId)?.delete(toolUseId) // live news, not history

    if (existingIndex !== -1) {
      // Update existing tool call
      const existing = toolCalls[existingIndex]

      // Guard: prevent regressing a terminal status to a non-terminal status
      const terminalStatuses = ['completed', 'error']
      const nonTerminalStatuses = ['pending', 'executing', 'permission_required']
      if (terminalStatuses.includes(existing.status) && nonTerminalStatuses.includes(frontendStatus)) {
        console.warn(`Ignoring status regression for tool ${toolUseId}: ${existing.status} → ${frontendStatus}`)
        return
      }
      // A tool already resolved via denial or interruption is terminal in a stronger sense than
      // plain 'completed'/'error' — a later conflicting tool_call update (e.g. a duplicate/stale
      // "failed" event) must not override the specific resolution already recorded. Locked via
      // the fields that already carry that specific resolution (permissionDecision/_isOrphaned)
      // rather than a separate backendStatus field.
      const lockedResolution = existing.permissionDecision === 'deny' ? 'denied'
        : existing._isOrphaned ? 'interrupted' : null
      if (lockedResolution && toolCall.status !== lockedResolution) {
        console.warn(`Ignoring conflicting update for ${lockedResolution} tool ${toolUseId}: → ${toolCall.status}`)
        return
      }

      existing.status = frontendStatus

      // Issue #1486: always accept the incoming input — the first event for a tool may arrive
      // with input:{} (from an intermediate AssistantMessage emitted while input_json_delta
      // events are still streaming); a subsequent event carries the fully-assembled input.
      if (toolCall.input !== undefined && toolCall.input !== null) {
        existing.input = toolCall.input
      }
      // Gated on turn_id like existing.messageId just below: a genuine live ToolCallUpdate
      // always carries turn_id, but the REST reload path can synthesize extra bookend
      // tool_call events (e.g. around an interrupted tool) that lack turn_id and carry a
      // stale/fallback created_at — those must not clobber the real captured timestamp.
      if (toolCall.created_at && toolCall.turn_id) {
        existing.timestamp = toolCall.created_at
      }

      // Issue #1774: persist AskUserQuestion answers independent of `.input`, which gets
      // overwritten by the terminal `completed` event (no `updated_input` on that event).
      if (toolCall.updated_input?.answers) {
        existing.answers = toolCall.updated_input.answers
      }

      // Issue #1694/#1958: owning assistant turn id, for permission-prompt anchoring
      if (toolCall.turn_id) {
        existing.messageId = toolCall.turn_id
      }

      // Update permission fields
      if (toolCall.permission) {
        existing.suggestions = toolCall.permission.suggestions || []
        existing.permissionRequestId = toolCall.request_id ?? existing.permissionRequestId  // request_id is added by backend for correlation
      }
      // Populate permissionToToolMap for handlePermissionResponse correlation
      if (toolCall.request_id && toolCall.status === 'awaiting_permission') {
        permissionToToolMap.value.set(toolCall.request_id, toolUseId)
        // Issue #699: Permission prompt notifications now driven by UI WebSocket
        // state_change → paused (see polling.js handleUIMessage)
      }
      if (toolCall.permission_granted !== null && toolCall.permission_granted !== undefined) {
        existing.permissionDecision = toolCall.permission_granted ? 'allow' : 'deny'
      }
      // Populate appliedUpdates from backend ToolCallUpdate data
      if (toolCall.applied_updates && toolCall.applied_updates.length > 0) {
        existing.appliedUpdates = toolCall.applied_updates
      }

      // Update result fields
      if (toolCall.result !== undefined) {
        existing.result = {
          error: toolCall.status === 'failed',
          content: toolCall.result
        }
      }
      if (toolCall.error) {
        existing.result = {
          error: true,
          content: toolCall.error
        }
      }

      _applyTerminalResolution(existing, toolCall)
      if (toolCall.status === 'completed' || toolCall.status === 'failed') {
        existing.isExpanded = false  // Auto-collapse on completion
      }

      // Issue #707: Auto-approval indicator
      if (toolCall.auto_approved_reason) {
        existing.autoApprovedReason = toolCall.auto_approved_reason
      }
      // Issue #1593: Sender attachment resource IDs for outbound comm chips
      if (toolCall.sender_attachments != null) {
        existing.senderAttachments = toolCall.sender_attachments
      }
    } else {
      // Create new tool call entry
      const newToolCall = {
        id: toolUseId,
        name: toolCall.name,
        input: toolCall.input,
        status: frontendStatus,
        permissionRequestId: toolCall.request_id,
        permissionDecision: toolCall.permission_granted != null
          ? (toolCall.permission_granted ? 'allow' : 'deny')
          : null,
        appliedUpdates: toolCall.applied_updates || [],
        suggestions: toolCall.permission?.suggestions || [],
        result: toolCall.result ? {
          error: toolCall.status === 'failed',
          content: toolCall.result
        } : null,
        explanation: null,
        timestamp: toolCall.created_at || new Date().toISOString(),
        isExpanded: !['completed', 'failed', 'denied', 'interrupted'].includes(toolCall.status),
        // Issue #195: Track parent Task tool for subagent grouping
        parent_tool_use_id: toolCall.parent_tool_use_id || null,
        // Issue #707: Auto-approval indicator
        autoApprovedReason: toolCall.auto_approved_reason || null,
        // Issue #953: Sub-agent ID for parallel permission disambiguation
        agentId: toolCall.agent_id || null,
        // Issue #1593: Sender attachment resource IDs for outbound comm chips
        senderAttachments: toolCall.sender_attachments || null,
        // Issue #1694/#1958: owning assistant turn id, for permission-prompt anchoring
        messageId: toolCall.turn_id || null,
        // Issue #1774: persist AskUserQuestion answers independent of `.input` churn
        answers: toolCall.updated_input?.answers || null,
      }

      if (toolCall.error) {
        newToolCall.result = {
          error: true,
          content: toolCall.error
        }
      }

      _applyTerminalResolution(newToolCall, toolCall)
      toolIndex.set(toolUseId, toolCalls.length)
      toolCalls.push(shallowReactive(newToolCall))
      // Populate permissionToToolMap for handlePermissionResponse correlation
      if (toolCall.request_id && toolCall.status === 'awaiting_permission') {
        permissionToToolMap.value.set(toolCall.request_id, toolUseId)
        // Issue #699: Permission prompt notifications now driven by UI WebSocket
        // state_change → paused (see polling.js handleUIMessage)
      }
    }

    // Trigger reactivity
    if (notify) toolCallsBySession.value = new Map(toolCallsBySession.value)

    // Issue #2030 stage 4 PR 4b: live completion event (replaces editHistory's whole-store watcher)
    if (source === 'live' && toolCompletedListeners.size) {
      const entry = existingIndex !== -1 ? toolCalls[existingIndex] : toolCalls[toolCalls.length - 1]
      if (entry && (entry.status === 'completed' || entry.status === 'error')) {
        _notifyListeners(toolCompletedListeners, sessionId, toolUseId, entry.name)
      }
    }

    // Handle task tool results. Issue #2030: live records only — the Tasks panel is hydrated from
    // the server snapshot (GET /api/sessions/{id}/tasks); history replay (load / backfill / resync /
    // archive) covers just a window of the history and would produce partial or wrong state.
    if (source === 'live' && ['completed', 'failed'].includes(toolCall.status) &&
        ['TaskCreate', 'TaskUpdate', 'TaskList', 'TaskGet'].includes(toolCall.name)) {
      try {
        const taskStore = useTaskStore()
        taskStore.handleTaskToolResult(sessionId, toolCall.name, toolCall.input, {
          error: toolCall.status === 'failed',
          content: toolCall.result
        })
      } catch (e) {
        console.warn('Failed to update task store:', e)
      }
    }
  }

  /**
   * Toggle tool call expansion
   *
   * Issue #2131: O(1) lookup via toolIndexBySession instead of an O(n) `.find` scan.
   */
  function toggleToolExpansion(sessionId, toolUseId) {
    const toolCalls = toolCallsBySession.value.get(sessionId)
    const toolIndex = toolIndexBySession.get(sessionId)
    if (toolCalls && toolIndex) {
      const idx = toolIndex.get(toolUseId)
      const toolCall = idx !== undefined ? toolCalls[idx] : undefined
      if (toolCall) {
        toolCall.isExpanded = !toolCall.isExpanded

        // Trigger reactivity
        toolCallsBySession.value = new Map(toolCallsBySession.value)
        return
      }
    }
    // A tool of the island (stage 3 PR 3C-1)
    const islandTool = islandBySession.value.has(sessionId) ? findLoadedToolCall(sessionId, toolUseId) : undefined
    if (islandTool) {
      islandTool.isExpanded = !islandTool.isExpanded
      islandBySession.value = new Map(islandBySession.value)
    }
  }

  /**
   * Clear messages for a session (for reset)
   */
  function clearMessages(sessionId) {
    _discardStreamingPreview(sessionId, 'clearMessages')  // Issue #1955: discard any in-flight preview
    // Issue #1748 review fix: prune before messages are gone (both read messagesBySession)
    pruneExpandedTimelineToolForSession(sessionId)
    pruneThinkingBlockExpandedForSession(sessionId)
    pruneExpandedCommsForSession(sessionId)
    messagesBySession.value.delete(sessionId)
    toolCallsBySession.value.delete(sessionId)
    // Issue #2131: the identity index Maps mirror messagesBySession/toolCallsBySession 1:1 —
    // same session-keyed-state cleanup pattern as taskLegsByTaskId/etc. just below.
    _deleteRecordTargets(sessionId)
    // Issue #2140 (builder-review fix): a deleted/reset session has no business resuming a
    // stale history load — and merely deleting the map entry isn't enough, since an
    // orphaned fetchAllMessagePages() loop already holds its OWN closure reference to this
    // `state` object and would keep calling _installPageAndNotify() after this clear,
    // silently resurrecting the data this very call is trying to wipe. Abort its controller
    // first so that loop's next page fetch actually rejects instead of running to completion.
    const pendingLoad = _hydrationLoadStateBySession.get(sessionId)
    if (pendingLoad) pendingLoad.controller.abort()
    _hydrationLoadStateBySession.delete(sessionId)
    // Issue #2030: a wiped session has no loaded suffix to extend — stop any backfill for it.
    abortBackfill(sessionId)
    _deleteHistory(sessionId)
    _backfilledToolIds.delete(sessionId)
    _hookCorrelationCache.delete(sessionId)

    // Issue #1746 (stage: subagents): clear only this session's task_id-scoped state
    // (ephemeral, reconstructs via hydrateBackgroundAgents + live frames on reload/reconnect).
    for (const [taskId, entry] of taskLegsByTaskId.value) {
      if (entry.session_id !== sessionId) continue
      taskLegsByTaskId.value.delete(taskId)
      narrationByTaskIdAndLeg.value.delete(taskId)
      for (const legIndex of entry.legs.keys()) {
        expandedLegs.value.delete(`${taskId}:${legIndex}`)
      }
      for (const leg of entry.legs) {
        if (leg.tool_use_id) taskIdByLaunchToolUseId.value.delete(leg.tool_use_id)
      }
    }
    taskLegsByTaskId.value = new Map(taskLegsByTaskId.value)
    taskIdByLaunchToolUseId.value = new Map(taskIdByLaunchToolUseId.value)
    narrationByTaskIdAndLeg.value = new Map(narrationByTaskIdAndLeg.value)
    expandedLegs.value = new Map(expandedLegs.value)

    // Trigger reactivity
    messagesBySession.value = new Map(messagesBySession.value)
    toolCallsBySession.value = new Map(toolCallsBySession.value)
  }

  // ========== STREAMING PREVIEW (Issue #1955) ==========
  // Cosmetic-only live-typing preview. Never a messagesBySession entry, never persisted, never
  // identity-bearing — see streamingPreviewBySession's own declaration comment for the model.

  function _startStreamingPreview(sessionId) {
    // No redelivery/overlap guarding needed (unlike the old placeholder model): a stray extra
    // message_start just resets cosmetic state — there is no data-loss risk because the preview
    // was never authoritative.
    const existing = streamingPreviewBySession.value.get(sessionId)
    pushDebugEvent('message', 'preview-start', {
      sessionId, hadExisting: !!existing, existingContentLen: existing?.content?.length || 0
    })
    streamingPreviewBySession.value.set(sessionId, {
      active: true,
      content: '',
      thinking: '',
      pendingText: '',
      pendingThinking: '',
      rafHandle: null,
      // Issue #1955 review fix: tracks whether a canonical frame for the CURRENTLY-OPEN turn
      // has already been observed, regardless of arrival order relative to deltas — see
      // _clearStreamingPreviewContent()/_endStreamingPreview() for why this makes dismissal
      // level-triggered instead of edge-triggered.
      canonicalSeen: false,
      // Issue #1573: tool_use ids (content_block_start) registered for THIS still-open turn
      // that have no rendering surface of their own yet — see _registerPendingToolInPreview().
      pendingTools: [],
      // Issue #1573 (review fix): tool_use ids already claimed by a canonical assistant message
      // for this still-open turn — see _clearStreamingPreviewContent(). Deliberately NOT the
      // same thing as `canonicalSeen`: _applyMessageRecord()'s own #1955 comment documents that a single
      // still-open turn can carry MULTIPLE canonical assistant messages ("multi-canonical-
      // message turn"), so `canonicalSeen` flips true after the FIRST one and then stays true
      // for the rest of the turn — gating registration on it would silently drop the indicator
      // for any tool whose content_block_start arrives after that first canonical message but
      // before ITS OWN. Gating per-id on `claimedToolIds` instead means each tool is judged only
      // against whether its own canonical message has actually landed yet.
      claimedToolIds: new Set(),
    })
    streamingPreviewBySession.value = new Map(streamingPreviewBySession.value)
  }

  /**
   * Issue #1573: record a newly-started tool_use so StreamingPreview.vue can show a transient
   * "Starting: <name>..." indicator for it. This follows the same level-triggered pattern as
   * `canonicalSeen` (see _clearStreamingPreviewContent()'s comment for the full rationale):
   * rather than assuming content_block_start always arrives before the canonical assistant
   * message for this turn, check the CURRENT state at the time this fires —
   * - `!preview.active`: this turn has already ended (message_stop seen) — a content_block_start
   *   this late is a stray/out-of-order delivery; the turn it belonged to is over, so there is
   *   nothing left to show a live indicator for.
   * - `preview.claimedToolIds.has(toolId)`: THIS tool's own canonical message already landed —
   *   the real tool card is already rendering via AssistantMessage.vue's normal segment-based
   *   path, so surfacing a duplicate "Starting..." indicator here would be a stale duplicate,
   *   not a lead (covers both a stray redelivery of this same content_block_start, and the
   *   general out-of-order-across-channels case).
   * Clearing happens in _clearStreamingPreviewContent(), scoped to exactly the tool ids the
   * arriving canonical message actually claims — i.e. each tool's indicator is handed off to
   * its own real card at the instant THAT card gains a rendering surface, never on an earlier
   * edge-triggered guess, and never blocked by an unrelated tool's canonical message landing
   * first in a multi-canonical-message turn.
   */
  function _registerPendingToolInPreview(sessionId, toolId, toolName) {
    const preview = streamingPreviewBySession.value.get(sessionId)
    if (!preview || !preview.active || preview.claimedToolIds.has(toolId)) return
    if (preview.pendingTools.some(t => t.id === toolId)) return
    preview.pendingTools = [...preview.pendingTools, { id: toolId, name: toolName }]
    streamingPreviewBySession.value = new Map(streamingPreviewBySession.value)
  }

  function _flushPreviewDelta(sessionId) {
    const preview = streamingPreviewBySession.value.get(sessionId)
    if (!preview) return
    if (preview.pendingText === '' && preview.pendingThinking === '') { preview.rafHandle = null; return }

    preview.content += preview.pendingText
    preview.thinking += preview.pendingThinking
    const addedChars = preview.pendingText.length + preview.pendingThinking.length
    preview.pendingText = ''
    preview.pendingThinking = ''
    preview.rafHandle = null

    streamingPreviewBySession.value = new Map(streamingPreviewBySession.value)
    pushDebugEvent('message', 'preview-delta', {
      sessionId, textLen: preview.content.length, thinkingLen: preview.thinking.length, addedChars
    })
  }

  /**
   * Issue #1955 (review fix, found via live testing: single-frame turns could leave a
   * permanent duplicate preview): resets the preview's displayed content whenever a canonical
   * assistant message appends (called from _applyMessageRecord(), live source only) — leaves `active`
   * untouched so a still-open stream keeps its caret. This is the mechanism resolving
   * "multi-canonical-message-per-turn": any canonical append clears whatever the preview was
   * showing, with no identity matching involved.
   *
   * Also marks `canonicalSeen = true` unconditionally (even when this call is a no-op because
   * content was already empty) — the canonical and delta channels are delivered independently
   * and CAN arrive out of order: a single-frame turn's canonical frame occasionally lands
   * before its own deltas, so this clear fires against an empty preview, the deltas then fill
   * it in afterward, and with only one frame in the turn there is no SECOND canonical append to
   * clear it again. `canonicalSeen` lets _endStreamingPreview() (message_stop) catch that case
   * and dismiss the preview itself once the canonical has definitely landed, regardless of
   * which arrived first — making dismissal level-triggered instead of edge-triggered.
   *
   * Issue #1573: also hands off `pendingTools` here, scoped to exactly the tool_use ids the
   * arriving canonical message carries (`canonicalToolIds`) — NOT a wholesale wipe of the whole
   * array. A wholesale wipe gated on this same call site would be wrong for a multi-canonical-
   * message turn: `canonicalSeen` is a one-way flag (true for the rest of the turn after the
   * FIRST canonical message), but a SECOND tool_use in the same still-open turn can start
   * streaming after that first message and before its OWN canonical message arrives — a
   * wholesale-wipe-on-first-canonical would silently drop that second tool's indicator with no
   * real card yet to replace it (reproducing the exact invisible-card gap this feature exists to
   * close). Recording claimed ids into `claimedToolIds` and filtering by id keeps each tool's
   * handoff independent of every other tool's in the same turn.
   */
  function _clearStreamingPreviewContent(sessionId, canonicalToolIds = []) {
    const preview = streamingPreviewBySession.value.get(sessionId)
    if (!preview) return
    pushDebugEvent('message', 'preview-clear', { sessionId, lenBefore: preview.content.length })
    preview.content = ''
    preview.thinking = ''
    preview.canonicalSeen = true
    if (canonicalToolIds.length) {
      for (const id of canonicalToolIds) preview.claimedToolIds.add(id)
      if (preview.pendingTools.length) {
        preview.pendingTools = preview.pendingTools.filter(t => !preview.claimedToolIds.has(t.id))
      }
    }
    streamingPreviewBySession.value = new Map(streamingPreviewBySession.value)
  }

  /**
   * Issue #1955: called from message_stop. Flushes any pending delta and ends the caret. Does
   * NOT clear content/thinking on its own — the preview persists (frozen) until the canonical
   * message actually appends (see _clearStreamingPreviewContent), so the swap from preview to
   * canonical message happens as one atomic visual transition instead of a gap.
   *
   * EXCEPTION (review fix): if the canonical frame for this turn was already observed BEFORE
   * message_stop (preview.canonicalSeen), clearing now is safe — the canonical bubble is
   * already showing, so there is no gap to create — and is in fact required, since a
   * single-frame turn has no future canonical append left to do it.
   */
  function _endStreamingPreview(sessionId) {
    const preview = streamingPreviewBySession.value.get(sessionId)
    if (!preview) return
    if (preview.rafHandle) { cancelAnimationFrame(preview.rafHandle); preview.rafHandle = null }
    _flushPreviewDelta(sessionId)
    preview.active = false
    if (preview.canonicalSeen) {
      preview.content = ''
      preview.thinking = ''
    }
    streamingPreviewBySession.value = new Map(streamingPreviewBySession.value)
    const nonEmpty = !!(preview.content || preview.thinking)
    pushDebugEvent('message', 'preview-end', {
      sessionId, finalLen: preview.content.length, nonEmpty, canonicalSeen: preview.canonicalSeen
    })
    // A non-empty preview at message_stop is by definition a leftover — auto-capture the
    // preceding timeline immediately rather than relying on a user noticing and reporting it.
    if (nonEmpty) flushDebugBuffer('preview-leftover')
  }

  /**
   * Issue #1955: discard the preview outright — used by interrupt, restart, session-terminated,
   * and the top of loadMessages()/clearMessages(). The preview never holds authoritative data,
   * so there is nothing to finalize or splice; simply deleting the map entry is always correct.
   */
  function _discardStreamingPreview(sessionId, reason = 'unspecified') {
    if (!streamingPreviewBySession.value.has(sessionId)) return
    pushDebugEvent('message', 'preview-discard', { sessionId, reason })
    streamingPreviewBySession.value.delete(sessionId)
    streamingPreviewBySession.value = new Map(streamingPreviewBySession.value)
  }

  function handleAssistantDelta(sessionId, data) {
    // data = { uuid, event } where event is the raw Anthropic streaming event dict
    const eventType = data?.event?.type
    if (!eventType) return

    switch (eventType) {
      case 'message_start':
        _startStreamingPreview(sessionId)
        break

      case 'content_block_start': {
        // Issue #1573/#2109: the server-authored `tool_call` record for this same
        // content_block_start already landed via the normal tool_call event handler —
        // backend/web_server.py::_create_message_callback emits it into the same
        // per-session EventQueue before the paired assistant_delta, so no client-side
        // synthesis is needed here. This branch only drives the transient streaming-preview
        // indicator off the card the server already created.
        const block = data.event.content_block
        if (block?.type === 'tool_use' && block.id) {
          // Skip the preview indicator when the card is already terminal:
          // _applyToolCallRecord's status-regression guard silently no-ops on a stray/late
          // content_block_start for an already-completed tool, and showing a "starting..."
          // indicator for it would be wrong.
          const currentCard = toolCallsBySession.value.get(sessionId)?.find(tc => tc.id === block.id)
          if (currentCard && !['completed', 'error'].includes(currentCard.status)) {
            // The card itself has no rendering surface yet — AssistantMessage.vue only shows
            // tool cards that belong to a message "segment", and none exists until the
            // canonical assistant message for this turn lands. Mirror it into the streaming
            // preview's own pendingTools list so StreamingPreview.vue can show a transient
            // "Starting: <name>..." indicator in the meantime.
            _registerPendingToolInPreview(sessionId, block.id, block.name)
          }
        }
        break
      }

      case 'content_block_delta': {
        const preview = streamingPreviewBySession.value.get(sessionId)
        if (!preview) break
        const deltaType = data.event.delta?.type
        if (deltaType === 'text_delta') {
          preview.pendingText += data.event.delta.text || ''
          if (!preview.rafHandle) preview.rafHandle = requestAnimationFrame(() => _flushPreviewDelta(sessionId))
        } else if (deltaType === 'thinking_delta') {
          preview.pendingThinking += data.event.delta.thinking || ''
          if (!preview.rafHandle) preview.rafHandle = requestAnimationFrame(() => _flushPreviewDelta(sessionId))
        }
        // input_json_delta: out of scope per §2, ignore
        break
      }

      case 'message_stop':
        _endStreamingPreview(sessionId)
        break
    }
  }

  // ========== SESSION STATE WATCHER ==========
  // Watch for session state changes to detect post-load terminations
  const sessionStore = useSessionStore()
  watch(
    () => {
      const sessions = Array.from(sessionStore.sessions.values())
      return sessions.map(s => ({ id: s.session_id, state: s.state }))
    },
    (newStates, oldStates) => {
      if (!oldStates) return

      // Check each session for state transitions
      newStates.forEach((newState, idx) => {
        const oldState = oldStates[idx]
        if (!oldState || oldState.id !== newState.id) return

        // Session transitioned from active/paused/starting to terminated/error
        const wasActive = ['active', 'paused', 'starting'].includes(oldState.state)
        const isInactive = !['active', 'paused', 'starting'].includes(newState.state)

        if (wasActive && isInactive) {
          // Issue #1955: discard any in-flight cosmetic preview so the caret doesn't linger.
          // AC2: no browser-side orphan sweep needed anymore — the backend's own tool_call
          // record for an interrupted tool already carries that terminal status directly.
          _discardStreamingPreview(newState.id, 'session-terminated')
        }
      })
    },
    { deep: true }
  )

  // ========== ARCHIVE SUPPORT (Issue #577) ==========

  /**
   * Set messages for an archived session view.
   * Issue #621: Process through the same unified tool pipeline as loadMessages()
   * so that tool_call messages populate toolCallsBySession and are filtered
   * from the display list. This ensures archived tools show correct completion
   * status and no blank message bubbles appear.
   */
  function setArchiveMessages(sessionId, rawMessages) {
    // Issue #2110 (stage 4b-B) / #2131: thin apply-per-record wrapper — reset the display/tool
    // lists into a local plain accumulator, let applyRecord() do the uniform work (identity
    // dedup, api_retry collapse, truncation/deferral banners, subagent narration routing,
    // tool_call upsert) entirely outside the reactive proxies, then install it in one shot.
    const target = _createRecordTarget()
    _applyArchiveRecords(sessionId, rawMessages, target, { captureInit: true })
    _installRecordTarget(sessionId, target)
    // Ensure reactivity even when `rawMessages` was empty or fully filtered out.
    messagesBySession.value = new Map(messagesBySession.value)
    toolCallsBySession.value = new Map(toolCallsBySession.value)
    lastStopReasonBySession.value = new Map(lastStopReasonBySession.value)
    deferredToolUseBySession.value = new Map(deferredToolUseBySession.value)
  }

  /**
   * Archive-view replay shared by setArchiveMessages() and the tail-first archive path
   * (loadHistoryTail / applyOlderPage with `source: 'archive'`). `captureInit: false` for older
   * pages: an older init record must never replace the newer one the window already captured.
   */
  function _applyArchiveRecords(sessionId, rawMessages, target, { captureInit }) {
    rawMessages.forEach(msg => {
      // Note: tool_call records need no special-case here — applyRecord() itself routes them
      // to the tool_call branch internally and never pushes them to the display list, so they
      // fall through the user/system filters below (neither matches type 'tool_call') straight
      // to the uniform applyRecord() call at the end of this callback.

      // Filter UserMessage entries that are purely tool results (no displayable text) —
      // archive-specific; live/load show these (they correlate the result against its tool
      // card inline instead of hiding it).
      if (msg.type === 'user' && Array.isArray(msg.content)) {
        const hasDisplayableContent = msg.content.some(
          block => block.type !== 'tool_result'
        )
        if (!hasDisplayableContent) {
          return
        }
      }

      // Filter SystemMessage entries with no displayable content — archive-specific.
      // (The 'status'/'requesting' suppression applyRecord/addMessage/loadMessages already
      // apply uniformly covers that case for every source, including this one.)
      if (msg.type === 'system') {
        const subtype = msg.subtype || msg.metadata?.subtype
        if (subtype === 'init') {
          // Issue #1829: capture init data for the archive-scoped Info modal view
          // before the (possibly early) return below discards this message.
          if (captureInit && msg.metadata?.init_data) {
            sessionStore.storeArchiveInitData(sessionId, msg.metadata.init_data)
          }
          if (!msg.content) {
            return
          }
        }
      }

      // notify:false — see applyRecord()'s own comment: avoid O(N) reactive broadcasts while
      // replaying a whole archived session in one shot; one final reassignment covers it.
      applyRecord(sessionId, msg, 'archive', { notify: false, target })
    })
  }

  /**
   * Clear archive messages when leaving archive view.
   */
  function clearArchiveMessages(sessionId) {
    pruneExpandedTimelineToolForSession(sessionId)  // Issue #1748 review fix
    pruneThinkingBlockExpandedForSession(sessionId)
    pruneExpandedCommsForSession(sessionId)
    messagesBySession.value.delete(sessionId)
    toolCallsBySession.value.delete(sessionId)
    // Issue #2131: the identity index Maps mirror messagesBySession/toolCallsBySession 1:1 —
    // same session-keyed-state cleanup pattern as lastStopReasonBySession/etc. just below.
    _deleteRecordTargets(sessionId)
    abortBackfill(sessionId)  // Issue #2030: archive views backfill older pages too
    _deleteHistory(sessionId)
    _backfilledToolIds.delete(sessionId)
    _hookCorrelationCache.delete(sessionId)
    // Issue #2110 (stage 4b-B review fix): applyRecord's uniform side effects now also write
    // lastStopReasonBySession/deferredToolUseBySession/launchTimestampBySession for the
    // 'archive' source — these are keyed only by sessionId, which an archive view shares with
    // its live counterpart (SessionView.vue reuses props.sessionId for both routes). Without
    // clearing them here too, an archived snapshot's stale truncation/deferral-banner or launch-
    // timestamp state would leak into the live session view when it next reactivates, exactly
    // like messagesBySession/toolCallsBySession above already guard against.
    lastStopReasonBySession.value.delete(sessionId)
    deferredToolUseBySession.value.delete(sessionId)
    launchTimestampBySession.value.delete(sessionId)
    sessionStore.clearArchiveInitData(sessionId)
    // shallowRef Maps: the deletes above do not notify on their own
    messagesBySession.value = new Map(messagesBySession.value)
    toolCallsBySession.value = new Map(toolCallsBySession.value)
  }

  // ========== HOOK CORRELATION (Issue #1350) ==========

  /**
   * Return the cached HookCorrelationResult for a session, recomputing only when
   * the message stream has actually advanced (different count or last message ID).
   */
  function getHookCorrelation(sessionId) {
    const suffixResult = _suffixHookCorrelation(sessionId)
    const island = islandBySession.value.get(sessionId)
    if (!island) return suffixResult
    // Stage 3 PR 3C-1: each interval is correlated on its own (an island never pairs with the
    // suffix across the gap); the result is the union of the two.
    let entry = _islandHookCache.get(sessionId)
    if (!entry || entry.rev !== island.rev) {
      entry = { rev: island.rev, result: correlateHooks(island.messages, island.toolCalls), combined: null }
      _islandHookCache.set(sessionId, entry)
    }
    if (entry.combined?.base !== suffixResult) {
      const merge = (a, b) => { const out = new Map(a); for (const [k, v] of b) out.set(k, v); return out }
      const union = (a, b) => new Set([...a, ...b])
      entry.combined = {
        base: suffixResult,
        result: {
          hooksByToolId: merge(entry.result.hooksByToolId, suffixResult.hooksByToolId),
          hooksByMessageId: merge(entry.result.hooksByMessageId, suffixResult.hooksByMessageId),
          hooksByCompactionKey: merge(entry.result.hooksByCompactionKey, suffixResult.hooksByCompactionKey),
          hooksOnClientLaunched: suffixResult.hooksOnClientLaunched,
          attachedHookMessageIds: union(entry.result.attachedHookMessageIds, suffixResult.attachedHookMessageIds),
          unattachedHookMessageIds: union(entry.result.unattachedHookMessageIds, suffixResult.unattachedHookMessageIds),
        },
      }
    }
    return entry.combined.result
  }

  function _suffixHookCorrelation(sessionId) {
    const messages = messagesBySession.value.get(sessionId) || []
    const count = messages.length
    const lastId = count > 0 ? (messages[count - 1].message_id || messages[count - 1].id || null) : null

    // Issue #2030: while only a suffix of the history is loaded, a head-only prepend (backfill)
    // must not invalidate the cache page by page — the whole-history correlation is recomputed
    // once on completion (applyFetchedOlderPage drops the entry). Live appends still change lastId.
    const cached = _hookCorrelationCache.get(sessionId)
    if (cached && cached.lastId === lastId &&
        (cached.messageCount === count || !isHistoryComplete(sessionId))) {
      return cached.result
    }

    const toolCalls = toolCallsBySession.value.get(sessionId) || []
    const result = correlateHooks(messages, toolCalls)
    _hookCorrelationCache.set(sessionId, { result, messageCount: count, lastId })
    return result
  }

  /** Returns hooks correlated to a specific tool call (PreToolUse + PostToolUse). */
  function hooksForToolCall(sessionId, toolId) {
    if (!sessionId || !toolId) return []
    return getHookCorrelation(sessionId).hooksByToolId.get(toolId) || []
  }

  /** Returns hooks correlated to a user or assistant message (UserPromptSubmit / Stop / etc.). */
  function hooksForMessageId(sessionId, messageId) {
    if (!sessionId || !messageId) return []
    return getHookCorrelation(sessionId).hooksByMessageId.get(messageId) || []
  }

  /** Returns hooks correlated to a compaction event group (keyed by its first record's message_id). */
  function hooksForCompaction(sessionId, groupKey) {
    if (!sessionId || !groupKey) return []
    return getHookCorrelation(sessionId).hooksByCompactionKey.get(groupKey) || []
  }

  /**
   * Returns true when a hook system message has been successfully correlated to a
   * parent element and should be hidden from the top-level displayable list.
   */
  function isHookMessageAttached(sessionId, messageId) {
    if (!sessionId || !messageId) return false
    return getHookCorrelation(sessionId).attachedHookMessageIds.has(messageId)
  }

  // ========== AGENT NOTIFICATIONS (Issue #1676) ==========
  // Background subagent notifications (agent_needs_input / agent_completed), tagged
  // by the backend as system messages with metadata.subtype === 'agent_notification'.
  // Rendered session-level via AgentNotificationStrip.vue rather than anchored to a
  // specific message/tool card (no reliable tool_use_id correlation — see #1676 plan).

  /**
   * Returns active (non-dismissed) agent notifications for a session, oldest first.
   *
   * Issue #1676: the CLI's include_hook_events plumbing is not confirmed to emit exactly
   * one message per Notification event (hook lifecycle events generally arrive as a
   * hook_started/hook_response pair) — dedupe by (notificationType, message) so an
   * unconfirmed duplicate phase never renders as two rows for the same event.
   */
  function agentNotificationsForSession(sessionId) {
    if (!sessionId) return []
    const messages = messagesBySession.value.get(sessionId) || []
    const dismissed = dismissedAgentNotifications.value.get(sessionId)

    const results = []
    const seen = new Set()
    for (const msg of messages) {
      if (msg.type !== 'system' || msg.metadata?.subtype !== 'agent_notification') continue
      // HookEventMessage.uuid is the only stable per-event id the backend surfaces for
      // this subtype; message_id/id are never populated for system messages.
      const id = msg.metadata?.uuid || msg.message_id || msg.id
      if (id && dismissed?.has(id)) continue

      const notificationType = msg.metadata?.notification_type || null
      const message = msg.metadata?.message || msg.content || ''
      const dedupeKey = `${notificationType}::${message}`
      if (seen.has(dedupeKey)) continue
      seen.add(dedupeKey)

      results.push({
        id,
        notificationType,
        label: msg.metadata?.label || null,
        message,
        title: msg.metadata?.title || null,
        timestamp: msg.timestamp || null,
        dismissed: false,
      })
    }
    return results
  }

  /** Dismisses a single agent notification for a session (session-local, not persisted). */
  function dismissAgentNotification(sessionId, notificationId) {
    if (!sessionId || !notificationId) return
    if (!dismissedAgentNotifications.value.has(sessionId)) {
      dismissedAgentNotifications.value.set(sessionId, new Set())
    }
    dismissedAgentNotifications.value.get(sessionId).add(notificationId)
    dismissedAgentNotifications.value = new Map(dismissedAgentNotifications.value)
  }

  // ========== RETURN ==========
  return {
    // State
    messagesBySession,
    toolCallsBySession,

    // Computed
    currentMessages,
    currentToolCalls,

    // Actions
    loadMessages,
    // Issue #2030 (stage 1b): tail-first history — window load, backfill pages, state
    loadHistoryTail,
    fetchOlderPage,
    applyFetchedOlderPage,
    applyOlderPage,
    claimBackfill,
    releaseBackfill,
    wasBackfilled,
    onToolCompleted,
    onHistoryReloaded,
    abortBackfill,
    historyBySession: readonly(historyBySession),
    historyIndexBySession: readonly(historyIndexBySession),
    getHistoryIndex,
    ensureHistoryIndex,
    isMessageLoaded,
    getHistoryState,
    // Issue #2030 stage 3 PR 3C-1: the pinned island + the jump path
    islandBySession: readonly(islandBySession),
    getIsland,
    loadAround,
    fetchIslandPage,
    applyIslandPage,
    loadedMessages,
    loadedToolCalls,
    findLoadedToolCall,
    isHistoryComplete,
    historyProgress,
    // Issue #2030 stage 4 PR 4d: memory budget
    trimOldEnd,
    getTrimInfo,
    getSuffixMessageIndex,
    // Issue #2140: shared per-session in-progress history-load tracker — the single source
    // of truth both session.js's retryHydration() and polling.js's resync trigger claim
    // through before starting a new loadMessages() call.
    claimHydrationLoad,
    hasPendingHistoryLoad,
    // Issue #2110 (stage 4b-B): the one entry point for every message/tool_call record,
    // regardless of source ('live' | 'load' | 'archive') — supersedes addMessage/handleToolCall.
    applyRecord,
    updateToolCall,
    handlePermissionResponse,
    toggleToolExpansion,
    clearMessages,
    // Issue #1486/#1955: streaming delta handler
    handleAssistantDelta,
    // Issue #1955: cosmetic-only live-typing preview, read reactively by StreamingPreview.vue
    streamingPreviewBySession: readonly(streamingPreviewBySession),

    // Launch timestamp tracking (Issue #473)
    launchTimestampBySession: readonly(launchTimestampBySession),

    // Archive support (Issue #577)
    setArchiveMessages,
    clearArchiveMessages,

    // Issue #662: Stop reason tracking for truncation banner
    lastStopReasonBySession: readonly(lastStopReasonBySession),

    // Issue #1300: Deferred tool use tracking for deferral banner
    deferredToolUseBySession: readonly(deferredToolUseBySession),

    // Issue #1746 (stage: subagents) / #1765: task_id-first background-agent tracking
    applyTaskLifecycleFrame,
    hydrateBackgroundAgents,
    getTaskLegEntry,
    getTaskIdForLaunchToolUse,
    narrationForLeg,
    childToolCallsForLeg,
    allTaskLegEntriesForSession,
    isLegExpanded,
    setLegExpanded,
    toggleLegExpanded,
    getExpandedTimelineTool,
    isExpandedTimelineToolAutoPermission,
    setExpandedTimelineTool,
    isThinkingBlockExpanded,
    toggleThinkingBlockExpanded,
    isCommExpanded,
    toggleCommExpanded,
    hasOpenPermissionForTask,
    openPermissionsForSession,

    // Issue #1000: Event cursors from REST /messages, consumed by connectSession()
    loadedEventCursors,

    // Issue #2131: per-session identity indices mirroring messagesBySession/toolCallsBySession
    // 1:1 — exposed read-only for test verification, same as loadedEventCursors above.
    messageIndexBySession,
    toolIndexBySession,
    retryIndexBySession,

    // Issue #1350: Hook correlation helpers
    hooksForToolCall,
    hooksForMessageId,
    hooksForCompaction,
    isHookMessageAttached,

    // Issue #1676: Background subagent notifications
    agentNotificationsForSession,
    dismissAgentNotification,
  }
})
