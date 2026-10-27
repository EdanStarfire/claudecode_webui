import { ref, computed, watch } from 'vue'
import { findMatches } from '@/search/messageSearch'

const DEBOUNCE_MS = 200

/**
 * Reactive UI state for the message-list search feature (issue #2012). Wraps the pure
 * `messageSearch.js` module with debounced querying, match navigation, and session-change reset.
 *
 * `sessionIdRef` is a Ref<string|null> — when it changes, matches/query are reset so results
 * never leak across sessions.
 */
export function useMessageSearch(sessionIdRef, { messageStore } = {}) {
  const isOpen = ref(false)
  const query = ref('')
  const matches = ref([])
  const currentIndex = ref(-1)
  const notFound = ref(false)

  let debounceTimer = null

  // The one place "cleared" is defined, so the query watcher's empty-query branch and reset()
  // can't drift into two different ideas of what clearing means.
  function clearMatches() {
    clearTimeout(debounceTimer)
    matches.value = []
    currentIndex.value = -1
    notFound.value = false
  }

  // Identity of a match across re-runs: older pages prepend matches, shifting every index.
  const matchKey = (m) => `${m.messageId}|${m.toolId}|${m.field}|${m.matchStart}`

  function runSearch({ preserveSelection = false } = {}) {
    const previous = preserveSelection ? currentMatch.value : null
    matches.value = findMatches(sessionIdRef.value, query.value, { messageStore })
    let index = matches.value.length > 0 ? 0 : -1
    if (previous) {
      const kept = matches.value.findIndex(m => matchKey(m) === matchKey(previous))
      if (kept !== -1) index = kept
    }
    currentIndex.value = index
    if (!preserveSelection) notFound.value = false
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

  // Issue #2030 (stage 1b): search covers the LOADED messages only while older history is still
  // backfilling. `historyScope` drives the visible "Searching N % of history — still loading"
  // indicator; every applied older page re-runs an active query so newly loaded matches appear.
  const historyScope = computed(() => {
    const sessionId = sessionIdRef.value
    if (!sessionId || !messageStore?.isHistoryComplete || messageStore.isHistoryComplete(sessionId)) return null
    const fraction = messageStore.historyProgress(sessionId)?.fraction
    return { percent: fraction != null ? Math.round(fraction * 100) : null }
  })

  watch(
    () => messageStore?.getHistoryState?.(sessionIdRef.value)?.startCursor,
    () => {
      if (query.value.trim()) scheduleSearch({ preserveSelection: true })
    }
  )

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

  function next() {
    if (matches.value.length === 0) return
    notFound.value = false
    currentIndex.value = (currentIndex.value + 1) % matches.value.length
  }

  function prev() {
    if (matches.value.length === 0) return
    notFound.value = false
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
    open,
    close,
    next,
    prev,
    markNotFound,
    historyScope,
  }
}
