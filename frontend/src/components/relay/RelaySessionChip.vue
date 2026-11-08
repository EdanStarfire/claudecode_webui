<template>
  <span
    v-if="summary"
    class="relay-chip"
    :title="title"
    data-testid="relay-session-chip"
  >
    <svg width="9" height="9" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><path d="M2 3h12a1 1 0 0 1 1 1v6a1 1 0 0 1-1 1H7l-3 3v-3H2a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z"/></svg>
    <span v-if="summary.handle" class="relay-chip-handle">!{{ summary.handle }}</span>
  </span>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({ summary: { type: Object, default: null } })
const title = computed(() => {
  const n = props.summary?.policy_count ?? 0
  return `Relay attached${props.summary?.handle ? ` as !${props.summary.handle}` : ''} · ${n} channel ${n === 1 ? 'policy' : 'policies'}`
})
</script>

<style scoped>
.relay-chip {
  display: inline-flex; align-items: center; gap: 3px; flex-shrink: 0;
  padding: 0 5px; height: 14px; border-radius: 7px; font-size: 10px; color: #fff; background: #4a154b;
}
.relay-chip-handle { max-width: 90px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
@media (max-width: 600px) {
  .relay-chip-handle { display: none; }
}
</style>
