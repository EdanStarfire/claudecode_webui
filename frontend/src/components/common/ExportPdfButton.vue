<template>
  <button
    v-if="displayMode === 'markdown'"
    class="copy-btn print-btn"
    @click.stop="$emit('click')"
    :disabled="feedback === 'printing'"
    title="Export as PDF"
  >
    <svg v-if="feedback === 'done'" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
      <polyline points="20 6 9 17 4 12"></polyline>
    </svg>
    <span v-else-if="feedback === 'printing'" class="spinner-border spinner-border-sm" role="status"></span>
    <svg v-else width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
      <polyline points="6 9 6 2 18 2 18 9"></polyline>
      <path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"></path>
      <rect x="6" y="14" width="12" height="8"></rect>
    </svg>
    <span class="copy-label">{{ feedback === 'done' ? 'Done!' : feedback === 'printing' ? 'Printing...' : 'Export PDF' }}</span>
  </button>
</template>

<script setup>
defineProps({
  feedback: {
    type: String,
    default: ''
  },
  displayMode: {
    type: String,
    default: 'raw'
  }
})

defineEmits(['click'])
</script>
