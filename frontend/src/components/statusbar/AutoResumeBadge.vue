<template>
  <span
    class="badge auto-resume-badge bg-warning text-dark"
    :title="`Rate-limited — auto-resume scheduled for ${formattedFireAt}`"
  >
    ⏳ Auto-resume {{ formattedFireAt }}
  </span>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  fireAt: {
    type: Number,
    default: null
  }
})

const formattedFireAt = computed(() => {
  if (!props.fireAt) return 'pending'
  return new Date(props.fireAt * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
})
</script>
