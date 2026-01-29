<template>
  <div class="progress session-state-status-line" style="height: 4px; border-radius: 0">
    <div
      v-if="session"
      class="progress-bar"
      :class="{ 'progress-bar-striped progress-bar-animated': isAnimated }"
      :style="{
        width: '100%',
        backgroundColor: statusColor
      }"
      :title="statusTooltip"
    ></div>
    <div
      v-else
      class="progress-bar bg-secondary"
      style="width: 100%"
      title="No session selected"
    ></div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useSessionStore } from '@/stores/session'

const props = defineProps({
  sessionId: {
    type: String,
    required: true
  }
})

const sessionStore = useSessionStore()

const session = computed(() => sessionStore.sessions.get(props.sessionId))

/**
 * Get display state - matches ProjectStatusLine.vue logic
 * Processing state overrides session state
 * Special case: paused + processing = pending-prompt (waiting for permission)
 */
const displayState = computed(() => {
  if (!session.value) return 'unknown'

  // Special case: PAUSED + processing = waiting for permission response (yellow blinking)
  if (session.value.state === 'paused' && session.value.is_processing) {
    return 'pending-prompt'
  }

  // Normal case: processing overrides state
  return session.value.is_processing ? 'processing' : session.value.state
})

/**
 * Get color for current state - matches ProjectStatusLine.vue color mapping
 */
const statusColor = computed(() => {
  const state = displayState.value
  const colorMap = {
    'created': '#d3d3d3',      // grey
    'starting': '#90ee90',     // light green
    'active': '#90ee90',       // light green
    'running': '#90ee90',      // light green
    'processing': '#dda0dd',   // light purple
    'paused': '#d3d3d3',       // grey
    'pending-prompt': '#ffc107',  // warning yellow (permission waiting)
    'terminated': '#d3d3d3',   // grey
    'error': '#ffb3b3',        // light red
    'failed': '#ffb3b3',       // light red
    'unknown': '#d3d3d3'       // grey
  }
  return colorMap[state] || '#d3d3d3'
})

/**
 * Determine if status line should be animated
 * Active states: starting, processing, pending-prompt
 */
const isAnimated = computed(() => {
  const state = displayState.value
  return state === 'starting' || state === 'processing' || state === 'pending-prompt'
})

/**
 * Generate tooltip text for status line
 * Shows session name and current state
 */
const statusTooltip = computed(() => {
  if (!session.value) return 'No session selected'

  const state = displayState.value
  const stateLabel = state.charAt(0).toUpperCase() + state.slice(1).replace('-', ' ')
  const nameLabel = session.value.name || 'Unnamed Session'
  // Issue #349: All sessions are minions - always show role if present
  const roleLabel = session.value.role ? ` (${session.value.role})` : ''

  return `${nameLabel}${roleLabel}: ${stateLabel}`
})
</script>

<style scoped>
.session-state-status-line {
  margin: 0;
}
</style>
