<template>
  <span
    class="settings-toolbar-chip"
    :class="`chip-${type.toLowerCase()}`"
    :title="tooltip"
  >
    <span class="chip-badge">{{ type }}:</span>
    <span class="chip-label">{{ label }}</span>
  </span>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  type: { type: String, required: true },  // 'T' | 'P'
  label: { type: String, required: true },
})

const tooltip = computed(() => {
  if (props.type === 'T') return `Template: ${props.label}`
  if (props.type === 'P') return `Profile: ${props.label}`
  return props.label
})
</script>

<style scoped>
.settings-toolbar-chip {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 2px 8px;
  border-radius: 12px;
  font-size: 11px;
  font-weight: 500;
  border: 1px solid transparent;
  cursor: default;
  user-select: none;
}

.chip-t {
  background: rgba(31, 111, 235, 0.1);
  border-color: rgba(31, 111, 235, 0.27);
  color: #58a6ff;
}

.chip-p {
  background: rgba(63, 185, 80, 0.1);
  border-color: rgba(63, 185, 80, 0.27);
  color: #3fb950;
}

.chip-badge {
  font-weight: 700;
  opacity: 0.75;
}
</style>
