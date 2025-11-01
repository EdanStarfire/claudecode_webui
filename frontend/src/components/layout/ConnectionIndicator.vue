<template>
  <div class="connection-indicators">
    <!-- UI WebSocket Indicator -->
    <div
      class="indicator"
      :class="getIndicatorClass('ui')"
      :title="getTooltip('ui')"
    >
      🌐
    </div>

    <!-- Session WebSocket Indicator -->
    <div
      class="indicator"
      :class="getIndicatorClass('session')"
      :title="getTooltip('session')"
    >
      💬
    </div>

    <!-- Legion WebSocket Indicator -->
    <div
      class="indicator"
      :class="getIndicatorClass('legion')"
      :title="getTooltip('legion')"
    >
      👥
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useWebSocketStore } from '@/stores/websocket'

const wsStore = useWebSocketStore()

/**
 * Get indicator background class based on connection state
 * @param {string} type - 'ui', 'session', or 'legion'
 * @returns {string} CSS class for background color
 */
function getIndicatorClass(type) {
  const connectionMap = {
    ui: { connected: wsStore.uiConnected, retryCount: wsStore.uiRetryCount },
    session: { connected: wsStore.sessionConnected, retryCount: wsStore.sessionRetryCount },
    legion: { connected: wsStore.legionConnected, retryCount: wsStore.legionRetryCount }
  }

  const { connected, retryCount } = connectionMap[type]

  // Connecting/Reconnecting: yellow (retry count > 0 but trying to connect)
  if (retryCount > 0 && !connected) {
    return 'indicator-connecting'
  }

  // Connected: green
  if (connected) {
    return 'indicator-connected'
  }

  // Disconnected: red
  return 'indicator-disconnected'
}

/**
 * Get tooltip text with connection details
 * @param {string} type - 'ui', 'session', or 'legion'
 * @returns {string} Tooltip text
 */
function getTooltip(type) {
  const labels = {
    ui: 'UI WebSocket',
    session: 'Session WebSocket',
    legion: 'Legion WebSocket'
  }

  const descriptions = {
    ui: 'Global UI state updates',
    session: 'Session message streaming',
    legion: 'Multi-agent communications'
  }

  const connectionMap = {
    ui: { connected: wsStore.uiConnected, retryCount: wsStore.uiRetryCount },
    session: { connected: wsStore.sessionConnected, retryCount: wsStore.sessionRetryCount },
    legion: { connected: wsStore.legionConnected, retryCount: wsStore.legionRetryCount }
  }

  const { connected, retryCount } = connectionMap[type]

  let status = 'Disconnected'
  if (connected) {
    status = 'Connected'
  } else if (retryCount > 0) {
    status = `Reconnecting (attempt ${retryCount})`
  }

  return `${labels[type]}\n${descriptions[type]}\nStatus: ${status}`
}
</script>

<style scoped>
.connection-indicators {
  display: flex;
  gap: 4px;
  align-items: center;
}

.indicator {
  width: 28px;
  height: 28px;
  border-radius: 6px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
  transition: all 0.3s ease;
  cursor: help;
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
}

.indicator:hover {
  transform: scale(1.1);
  box-shadow: 0 4px 8px rgba(0, 0, 0, 0.15);
}

/* Connected: Green background */
.indicator-connected {
  background-color: #28a745;
}

/* Connecting/Reconnecting: Yellow background with pulse animation */
.indicator-connecting {
  background-color: #ffc107;
  animation: pulse 2s ease-in-out infinite;
}

/* Disconnected: Red background with pulse animation */
.indicator-disconnected {
  background-color: #dc3545;
  animation: pulse 2s ease-in-out infinite;
}

/* Pulse animation for connecting/disconnected states */
@keyframes pulse {
  0%, 100% {
    opacity: 1;
  }
  50% {
    opacity: 0.6;
  }
}

/* Mobile responsiveness */
@media (max-width: 768px) {
  .indicator {
    width: 24px;
    height: 24px;
    font-size: 14px;
  }

  .connection-indicators {
    gap: 3px;
  }
}
</style>
