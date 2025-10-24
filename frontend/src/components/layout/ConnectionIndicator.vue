<template>
  <span
    id="connection-indicator"
    class="badge"
    :class="statusClass"
  >
    {{ statusText }}
  </span>
</template>

<script setup>
import { computed } from 'vue'
import { useWebSocketStore } from '@/stores/websocket'

const wsStore = useWebSocketStore()

const statusClass = computed(() => {
  switch (wsStore.overallStatus) {
    case 'connected':
      return 'bg-success'
    case 'partial':
      return 'bg-warning'
    default:
      return 'bg-secondary'
  }
})

const statusText = computed(() => {
  switch (wsStore.overallStatus) {
    case 'connected':
      return 'Connected'
    case 'partial':
      return 'Partial Connection'
    default:
      return 'Disconnected'
  }
})
</script>
