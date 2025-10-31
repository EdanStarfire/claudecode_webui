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
import { computed, watch, ref } from 'vue'
import { useWebSocketStore } from '@/stores/websocket'

const wsStore = useWebSocketStore()
const flashActive = ref(false)

// Watch for status changes and trigger flash animation
watch(() => wsStore.overallStatus, (newStatus, oldStatus) => {
  if (newStatus !== oldStatus && oldStatus !== undefined) {
    console.log(`🔄 Connection status changed: ${oldStatus} → ${newStatus}`)

    // Trigger flash animation
    flashActive.value = true
    setTimeout(() => {
      flashActive.value = false
    }, 600)
  }
})

const statusClass = computed(() => {
  const classes = []

  switch (wsStore.overallStatus) {
    case 'connected':
      classes.push('bg-success')
      break
    case 'partial':
      classes.push('bg-warning')
      break
    default:
      classes.push('bg-secondary')
  }

  if (flashActive.value) {
    classes.push('status-flash')
  }

  return classes.join(' ')
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

<style scoped>
.badge {
  transition: all 0.2s ease;
  font-weight: 600;
  padding: 0.5rem 0.75rem;
  font-size: 0.9rem;
  min-width: 120px;
  text-align: center;
}

/* Flash animation on status change */
@keyframes statusFlash {
  0% { transform: scale(1); box-shadow: none; }
  50% { transform: scale(1.15); box-shadow: 0 0 15px currentColor; }
  100% { transform: scale(1); box-shadow: none; }
}

.status-flash {
  animation: statusFlash 0.6s ease-out;
}

/* Pulse for disconnected/warning */
@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.65; }
}

.bg-secondary {
  background-color: #dc3545 !important; /* Red for disconnected */
  animation: pulse 2s ease-in-out infinite;
}

.bg-warning {
  animation: pulse 3s ease-in-out infinite;
}
</style>
