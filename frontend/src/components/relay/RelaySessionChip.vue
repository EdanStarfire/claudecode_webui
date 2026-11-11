<template>
  <span
    v-if="summary"
    class="relay-chip"
    :class="{ 'icon-only': iconOnly }"
    :title="title"
    :aria-label="title"
    data-testid="relay-session-chip"
  >
    <svg width="9" height="9" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><path d="M2 3h12a1 1 0 0 1 1 1v6a1 1 0 0 1-1 1H7l-3 3v-3H2a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z"/></svg>
    <span v-if="summary.handle && !iconOnly" class="relay-chip-handle">!{{ summary.handle }}</span>
    <span v-if="summary.attention" class="relay-chip-dot" data-testid="relay-chip-dot" aria-hidden="true"></span>
  </span>
</template>

<script setup>
import { computed } from 'vue'
import { relayChipShowsText, summaryTitle } from '@/utils/relayInbound'

const props = defineProps({
  summary: { type: Object, default: null },
  // the session's slug: when the relay handle equals it the name already says it, so only the icon shows
  slug: { type: String, default: '' },
})
const title = computed(() => summaryTitle(props.summary))
// icon only when the handle equals the slug (the name already says it); a missing slug keeps the text
const iconOnly = computed(() => !!props.summary?.handle && !!props.slug && !relayChipShowsText(props.summary, props.slug))
</script>

<style scoped>
/* Same quiet look as the other agent-row decorations: theme surface, border and text, no brand colour. */
.relay-chip {
  position: relative; display: inline-flex; align-items: center; justify-content: center; gap: 3px; flex-shrink: 0;
  padding: 0 5px; height: 15px; min-width: 15px; border-radius: 8px; font-size: 10px; line-height: 1;
  color: var(--bs-secondary-color); background: var(--bs-secondary-bg); border: 1px solid var(--bs-border-color);
}
.relay-chip.icon-only { width: 15px; padding: 0; border-radius: 50%; }
.relay-chip-dot {
  position: absolute; top: -3px; right: -3px; width: 6px; height: 6px; border-radius: 50%;
  background: var(--bs-warning); border: 1px solid var(--bs-body-bg);
}
.relay-chip-handle { max-width: 90px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
@media (max-width: 600px) {
  .relay-chip-handle { display: none; }
  .relay-chip { width: 15px; padding: 0; border-radius: 50%; }
}
</style>
