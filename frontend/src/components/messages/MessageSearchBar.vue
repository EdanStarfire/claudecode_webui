<template>
  <div class="message-search-bar">
    <button
      v-if="!search.isOpen.value"
      class="btn btn-sm btn-outline-secondary message-search-toggle"
      @click="handleOpen"
      aria-label="Search messages"
      title="Search messages"
    >
      &#x1F50D;
    </button>

    <div v-else class="message-search-expanded">
      <div class="message-search-row">
        <input
          ref="inputEl"
          v-model="search.query.value"
          type="text"
          class="form-control form-control-sm message-search-input"
          placeholder="Search messages…"
          @keydown="onKeydown"
        />
        <span class="message-search-count">{{ countLabel }}</span>
        <button
          class="btn btn-sm btn-outline-secondary"
          :disabled="!hasMatches"
          @click="search.prev()"
          aria-label="Previous match"
        >&#x2039;</button>
        <button
          class="btn btn-sm btn-outline-secondary"
          :disabled="!hasMatches"
          @click="search.next()"
          aria-label="Next match"
        >&#x203A;</button>
        <button
          class="btn btn-sm btn-outline-secondary"
          @click="handleClose"
          aria-label="Close search"
        >&#xD7;</button>
      </div>
      <!-- Issue #2030: its own line below the controls (never squeezed beside the input), with a
           short form on narrow screens. Shown when the whole-history server search is unavailable
           (the search then covers the loaded messages only), and for "Load more results". -->
      <div
        v-if="search.loadedOnly?.value"
        class="message-search-scope small text-muted"
        data-testid="search-history-scope"
        title="The server search is unavailable: only the messages loaded so far are searched."
      >
        <span class="scope-long">Searching loaded messages only</span>
        <span class="scope-short">Loaded only</span>
      </div>
      <div v-if="showMore" class="message-search-more small">
        <button
          class="btn btn-sm btn-outline-secondary py-0"
          data-testid="search-load-more"
          :disabled="search.loadingMore.value"
          @click="search.loadMore()"
        >{{ search.loadingMore.value ? `Searching…${percentLabel}` : 'Load more results' }}</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, ref, watch } from 'vue'

const props = defineProps({
  search: { type: Object, required: true },
})
const emit = defineEmits(['navigate'])

const inputEl = ref(null)

const hasMatches = computed(() => props.search.matches.value.length > 0)

const percentLabel = computed(() => {
  const percent = props.search.percent?.value
  return percent != null ? ` ${percent} %` : ''
})

// "N+" while the server search has more pages; "Searching… X %" until the first result arrives.
const countLabel = computed(() => {
  const trimmedQuery = props.search.query.value.trim()
  if (!trimmedQuery) return 'Search…'
  if (props.search.notFound.value) return "Couldn't locate match"
  const total = props.search.matches.value.length
  if (total === 0) {
    return props.search.phase?.value === 'searching' ? `Searching…${percentLabel.value}` : 'No results'
  }
  const plus = props.search.hasMore?.value ? '+' : ''
  return `${props.search.currentIndex.value + 1} of ${total}${plus}`
})

const showMore = computed(() => !!props.search.hasMore?.value && props.search.matches.value.length > 0)

function handleOpen() {
  props.search.open()
  nextTick(() => inputEl.value?.focus())
}

function handleClose() {
  props.search.close()
}

function onKeydown(e) {
  if (e.key === 'Enter') {
    e.preventDefault()
    if (e.shiftKey) props.search.prev()
    else props.search.next()
  } else if (e.key === 'Escape') {
    e.preventDefault()
    handleClose()
  }
}

// Issue #2012: navigate (and highlight) whenever the current match changes — on query results
// landing, next()/prev(), or clearing to no match (emits null so the parent clears any stale
// highlight rather than leaving one in a now-irrelevant row).
// Issue #2030: keyed (not by object identity) so an older page re-running the same query, which
// rebuilds the matches array, doesn't yank the view back to an already-selected match.
watch(
  () => {
    const m = props.search.currentMatch.value
    return m ? `${props.search.query.value}|${m.messageId}|${m.toolId}|${m.field}|${m.matchStart}|${m.occurrenceIndex}|${props.search.navTick?.value ?? 0}` : null
  },
  () => emit('navigate', props.search.currentMatch.value)
)
</script>
