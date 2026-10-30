<template>
  <div class="history-gap-row" role="group" aria-label="History not loaded" data-testid="history-gap-row">
    <div class="history-gap-text">
      <span v-if="loading" class="spinner-border spinner-border-sm flex-shrink-0" aria-hidden="true"></span>
      <span data-testid="history-gap-label">{{ label }}</span>
      <span v-if="error" class="text-danger" data-testid="history-gap-error">{{ error }}</span>
    </div>
    <div class="history-gap-actions">
      <button
        type="button"
        class="btn btn-sm btn-outline-secondary"
        data-testid="history-gap-load-newer"
        :disabled="loading || full"
        @click="$emit('load-newer')"
      >Load newer</button>
      <button
        type="button"
        class="btn btn-sm btn-outline-secondary"
        data-testid="history-gap-jump-latest"
        @click="$emit('jump-latest')"
      >Jump to latest</button>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { formatHistorySize } from '@/utils/historyIntervals'

// Issue #2030 (stage 3 PR 3C-1): the stretch of history between a jumped-to island and the live
// end is not loaded. "Load newer" appends one `after=` page to the island (reaching the live end
// merges the two and this row disappears); "Jump to latest" scrolls to the live end and keeps the
// island until the next jump.
const props = defineProps({
  approxBytes: { type: Number, default: null },
  loading: { type: Boolean, default: false },
  error: { type: String, default: null },
  // The island reached its interim size cap: it grows no further.
  full: { type: Boolean, default: false },
})
defineEmits(['load-newer', 'jump-latest'])

const label = computed(() => {
  const size = formatHistorySize(props.approxBytes)
  const base = size ? `About ${size} of history not loaded` : 'History not loaded'
  return props.full ? `${base} — this section is full, jump to the latest or elsewhere` : base
})
</script>

<style scoped>
.history-gap-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;
  margin: 8px 16px;
  padding: 8px 12px;
  border: 1px dashed var(--bs-border-color);
  border-radius: 6px;
  background: var(--bs-tertiary-bg);
  color: var(--bs-secondary-color);
  font-size: 0.875rem;
}

.history-gap-text {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  min-width: 0;
}

.history-gap-actions {
  display: flex;
  gap: 8px;
}

/* Phones: stacked full-width buttons with comfortable (>= 44 px) touch targets. */
@media (max-width: 768px) {
  .history-gap-row {
    flex-direction: column;
    align-items: stretch;
    margin: 8px 12px;
  }

  .history-gap-actions {
    flex-direction: column;
  }

  .history-gap-actions .btn {
    min-height: 44px;
  }
}
</style>
