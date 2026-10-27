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
           short form on narrow screens. Search only covers the loaded messages until the history
           has fully arrived. -->
      <div
        v-if="search.historyScope?.value"
        class="message-search-scope small text-muted"
        data-testid="search-history-scope"
        title="Search covers the messages loaded so far; older history is still loading."
      >
        <span class="scope-long">{{ scopeLabel }}</span>
        <span class="scope-short">{{ scopeShortLabel }}</span>
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

const countLabel = computed(() => {
  const trimmedQuery = props.search.query.value.trim()
  if (!trimmedQuery) return 'Search…'
  if (props.search.notFound.value) return "Couldn't locate match"
  const total = props.search.matches.value.length
  if (total === 0) return 'No results'
  return `${props.search.currentIndex.value + 1} of ${total}`
})

// Issue #2030: search only sees loaded messages until the full history has arrived.
const scopeLabel = computed(() => {
  const percent = props.search.historyScope?.value?.percent
  return percent != null ? `Searching ${percent} % of history — still loading` : 'Searching loaded history — still loading'
})

const scopeShortLabel = computed(() => {
  const percent = props.search.historyScope?.value?.percent
  return percent != null ? `${percent} % loaded` : 'Partial history'
})

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
    return m ? `${props.search.query.value}|${m.messageId}|${m.toolId}|${m.field}|${m.matchStart}|${m.occurrenceIndex}` : null
  },
  () => emit('navigate', props.search.currentMatch.value)
)
</script>
