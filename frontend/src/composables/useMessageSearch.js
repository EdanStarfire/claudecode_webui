import { ref, computed, watch } from 'vue'
import { findMatches, searchServer, MIN_QUERY_LENGTH } from '@/search/messageSearch'

const DEBOUNCE_MS = 200
// A 409 (cursor from another file generation) restarts the search; a server that keeps answering
// it is treated as failed.
const MAX_RESTARTS = 2

/**
 * Reactive UI state for the message-list search feature (issue #2012). Wraps the pure
 * `messageSearch.js` module with debounced querying, match navigation, and session-change reset.
 *
 * Issue #2030 (stage 3): results are newest-first in BOTH modes ("result 3 of N+", next = older,
 * prev = newer; "N+" until the server search is exhausted, an exact count otherwise). While only a
 * suffix of the history is loaded, the query runs on the server over the WHOLE history
 * (newest-first pages, "Load more results", auto-fetch when navigating past the last loaded
 * result). A running server search keeps its results, selection and highlight when the history
 * completes meanwhile; the NEXT query picks the mode again. Whenever the whole history is already loaded, or the
 * server search is unavailable (404 / 503 `search_busy` / network failure / repeated 409), it runs
 * over the loaded messages (`findMatches`) — with the "loaded only" indicator in the failure case.
 *
 * `sessionIdRef` is a Ref<string|null> — when it changes, matches/query are reset so results
 * never leak across sessions. `getPageLimit()` (optional) sizes server pages (smaller on slow links).
 */
export function useMessageSearch(sessionIdRef, { messageStore, getPageLimit } = {}) {
  const isOpen = ref(false)
  const query = ref('')
  const matches = ref([])
  const currentIndex = ref(-1)
  const notFound = ref(false)
  // 'idle' | 'local' (loaded messages == the whole history) | 'searching' (first results pending)
  // | 'server' (server results shown) | 'fallback' (server unavailable: loaded messages only)
  const phase = ref('idle')
  const loadingMore = ref(false)
  const hasMore = ref(false)
  const percent = ref(null)
  // Bumped on every next()/prev() so the bar re-navigates even when the selected match is unchanged.
  const navTick = ref(0)

  let debounceTimer = null
  let run = 0 // generation of the active search; stale async work compares against it
  let job = null // the active server search: { run, url, q, signal, cursor, exhausted, ... }
  let controller = null

  function abortInflight() {
    run++
    controller?.abort()
    controller = null
    job = null
    loadingMore.value = false
  }

  // The one place "cleared" is defined, so the query watcher's empty-query branch and reset()
  // can't drift into two different ideas of what clearing means.
  function clearMatches() {
    clearTimeout(debounceTimer)
    abortInflight()
    matches.value = []
    currentIndex.value = -1
    notFound.value = false
    phase.value = 'idle'
    hasMore.value = false
    percent.value = null
  }

  // Identity of a match across re-runs: older pages prepend matches, shifting every index.
  const matchKey = (m) => `${m.messageId}|${m.toolId}|${m.field}|${m.matchStart}`

  const isComplete = (sessionId) => !messageStore?.isHistoryComplete || messageStore.isHistoryComplete(sessionId)

  function searchUrlFor(sessionId) {
    const base = messageStore?.getHistoryState?.(sessionId)?.baseUrl || `/api/sessions/${sessionId}/messages`
    return base.replace(/\/messages$/, '/search')
  }

  function selectIndex(previous) {
    let index = matches.value.length > 0 ? 0 : -1
    if (previous) {
      const kept = matches.value.findIndex(m => matchKey(m) === matchKey(previous))
      if (kept !== -1) index = kept
    }
    currentIndex.value = index
  }

  // `findMatches` is chronological; the UI has ONE convention in both modes, the server's: newest
  // message first (next = older, prev = newer), a message's own matches in occurrence order.
  function newestFirst(list) {
    const groups = new Map()
    for (const m of list) {
      const group = groups.get(m.messageId)
      if (group) group.push(m)
      else groups.set(m.messageId, [m])
    }
    return [...groups.values()].reverse().flat()
  }

  function runLocal(previous, nextPhase) {
    matches.value = newestFirst(findMatches(sessionIdRef.value, query.value, { messageStore }))
    selectIndex(previous)
    phase.value = nextPhase
    hasMore.value = false
    percent.value = null
  }

  function fallBack(failed, previous) {
    if (failed.run !== run) return
    abortInflight()
    runLocal(previous, 'fallback')
  }

  /**
   * Fetches pages of `searchJob` until at least one NEW match arrived or the history is exhausted
   * (a page of the 64 MiB scan budget can come back empty). Resolves to false when it was
   * superseded by a newer search or fell back.
   */
  async function advance(searchJob, previous) {
    let before = matches.value.length
    while (searchJob.run === run) {
      let page
      try {
        page = await searchServer(searchJob.url, searchJob.q, searchJob.cursor, {
          signal: searchJob.signal, limit: getPageLimit?.(),
        })
      } catch (err) {
        if (searchJob.run !== run || searchJob.signal.aborted) return false
        if (err?.status === 409 && searchJob.restarts < MAX_RESTARTS) {
          // The history file changed under the cursor: start over, keeping the selected match.
          searchJob.restarts++
          searchJob.cursor = null
          searchJob.keys.clear()
          matches.value = []
          currentIndex.value = -1
          before = 0
          continue
        }
        // A page that fails after results are already shown keeps them (the button retries);
        // only a search with nothing to show falls back to the loaded messages.
        if (matches.value.length > 0 && err?.status !== 409) return false
        fallBack(searchJob, previous)
        return false
      }
      if (searchJob.run !== run) return false
      if (page.superseded) {
        // Our OWN older query's answer is ignored above (stale run). A superseded answer for the
        // current query means another flight displaced it: retry once, then fall back.
        if (searchJob.supersededRetries++ < 1) continue
        if (matches.value.length > 0) return false
        fallBack(searchJob, previous)
        return false
      }
      const fresh = page.matches.filter(m => !searchJob.keys.has(matchKey(m)))
      fresh.forEach(m => searchJob.keys.add(matchKey(m)))
      if (fresh.length) matches.value = matches.value.concat(fresh)
      searchJob.cursor = page.nextCursor
      searchJob.exhausted = page.exhausted || !page.nextCursor
      hasMore.value = !searchJob.exhausted
      percent.value = searchJob.exhausted ? null : (page.scannedFraction != null ? Math.round(page.scannedFraction * 100) : null)
      if (currentIndex.value < 0 && matches.value.length) selectIndex(previous)
      if (searchJob.exhausted || matches.value.length > before) break
    }
    if (searchJob.run !== run) return false
    phase.value = 'server'
    return true
  }

  async function runServer(previous) {
    abortInflight()
    controller = new AbortController()
    const searchJob = {
      run,
      url: searchUrlFor(sessionIdRef.value),
      q: query.value.trim(),
      signal: controller.signal,
      cursor: null,
      exhausted: false,
      restarts: 0,
      supersededRetries: 0,
      keys: new Set(),
    }
    job = searchJob
    matches.value = []
    currentIndex.value = -1
    notFound.value = false
    phase.value = 'searching'
    hasMore.value = false
    percent.value = null
    await advance(searchJob, previous)
  }

  function runSearch({ preserveSelection = false, localOnly = false } = {}) {
    const previous = preserveSelection ? currentMatch.value : null
    const sessionId = sessionIdRef.value
    if (!sessionId || query.value.trim().length < MIN_QUERY_LENGTH) {
      clearMatches()
      return
    }
    if (localOnly || isComplete(sessionId)) {
      abortInflight()
      runLocal(previous, phase.value === 'fallback' ? 'fallback' : 'local')
    } else {
      runServer(previous)
    }
    if (!preserveSelection) notFound.value = false
  }

  /** "Load more results": the next page of the active server search (no-op otherwise). */
  async function loadMore() {
    const searchJob = job
    if (!searchJob || searchJob.exhausted || loadingMore.value || phase.value !== 'server') return false
    loadingMore.value = true
    try {
      return await advance(searchJob, currentMatch.value)
    } finally {
      if (searchJob.run === run) loadingMore.value = false
    }
  }

  function scheduleSearch(options) {
    clearTimeout(debounceTimer)
    debounceTimer = setTimeout(() => runSearch(options), DEBOUNCE_MS)
  }

  watch(query, () => {
    if (!query.value.trim()) {
      clearMatches()
      return
    }
    notFound.value = false
    scheduleSearch()
  })

  watch(sessionIdRef, reset)

  // Matches over the LOADED messages (whole history loaded, or the server search unavailable):
  // every applied older page re-runs an active query so newly loaded matches appear. Server
  // results do not depend on what is loaded.
  watch(
    () => messageStore?.getHistoryState?.(sessionIdRef.value)?.startCursor,
    () => {
      if (!query.value.trim()) return
      // A complete history that a memory trim made partial again must search the whole history on the server.
      if (phase.value === 'local' && !isComplete(sessionIdRef.value)) scheduleSearch({ preserveSelection: true })
      else if (phase.value === 'local' || phase.value === 'fallback') scheduleSearch({ preserveSelection: true, localOnly: true })
    }
  )

  // The server search could not be used and not everything is loaded: say so.
  const loadedOnly = computed(() => {
    const sessionId = sessionIdRef.value
    return phase.value === 'fallback' && !!sessionId && !isComplete(sessionId)
  })

  const currentMatch = computed(() => {
    if (currentIndex.value < 0 || currentIndex.value >= matches.value.length) return null
    return matches.value[currentIndex.value]
  })

  function open() {
    isOpen.value = true
  }

  function reset() {
    query.value = ''
    clearMatches()
  }

  function close() {
    isOpen.value = false
    reset()
  }

  async function next() {
    const total = matches.value.length
    if (total === 0 || loadingMore.value) return
    notFound.value = false
    navTick.value++
    if (currentIndex.value === total - 1 && hasMore.value && phase.value === 'server') {
      // Past the last loaded result of an unfinished search: fetch the next page and step into it
      // (an exhausted search without new results wraps like the loaded search).
      const at = currentIndex.value
      await loadMore()
      if (currentIndex.value === at) currentIndex.value = matches.value.length > total ? total : 0
      return
    }
    currentIndex.value = (currentIndex.value + 1) % total
  }

  function prev() {
    if (matches.value.length === 0) return
    notFound.value = false
    navTick.value++
    currentIndex.value = (currentIndex.value - 1 + matches.value.length) % matches.value.length
  }

  function markNotFound() {
    notFound.value = true
  }

  return {
    isOpen,
    query,
    matches,
    currentIndex,
    currentMatch,
    notFound,
    phase,
    hasMore,
    loadingMore,
    percent,
    navTick,
    loadedOnly,
    open,
    close,
    next,
    prev,
    loadMore,
    markNotFound,
  }
}
