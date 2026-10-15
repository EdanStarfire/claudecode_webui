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
watch(
  () => props.search.currentMatch.value,
  match => emit('navigate', match)
)
</script>
