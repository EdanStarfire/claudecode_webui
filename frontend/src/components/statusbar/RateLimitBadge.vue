<template>
  <span
    class="badge rate-limit-badge"
    :class="colorClass"
    :title="`${label} window: ${pct?.toFixed(1)}% used, resets ${formattedReset}`"
  >
    {{ label }} {{ pct?.toFixed(0) }}%
  </span>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  label: String,
  pct: Number,
  resetsAt: String
})

const colorClass = computed(() => {
  if (props.pct == null) return 'bg-secondary'
  if (props.pct < 50) return 'bg-success'
  if (props.pct < 80) return 'bg-warning text-dark'
  return 'bg-danger'
})

const formattedReset = computed(() => {
  if (!props.resetsAt) return 'unknown'
  return new Date(props.resetsAt).toLocaleString()
})
</script>
