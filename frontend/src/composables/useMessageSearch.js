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

  function runSearch() {
    matches.value = findMatches(sessionIdRef.value, query.value, { messageStore })
    currentIndex.value = matches.value.length > 0 ? 0 : -1
    notFound.value = false
  }

  function scheduleSearch() {
    clearTimeout(debounceTimer)
    debounceTimer = setTimeout(runSearch, DEBOUNCE_MS)
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
  }
}
