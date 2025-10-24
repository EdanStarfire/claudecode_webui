<template>
  <div class="progress project-status-line" style="height: 4px; border-radius: 0">
    <template v-if="!sessions || sessions.length === 0">
      <!-- Empty project - gray segment -->
      <div class="progress-bar bg-secondary" style="width: 100%"></div>
    </template>
    <template v-else>
      <!-- One segment per session -->
      <div
        v-for="session in sessions"
        :key="session.session_id"
        class="progress-bar"
        :class="{ 'progress-bar-striped progress-bar-animated': isAnimated(session) }"
        :style="{
          width: segmentWidth,
          backgroundColor: getColor(session),
          cursor: 'help'
        }"
        :title="getTooltip(session)"
      ></div>
    </template>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  project: {
    type: Object,
    required: true
  },
  sessions: {
    type: Array,
    default: () => []
  }
})

const segmentWidth = computed(() => {
  if (!props.sessions || props.sessions.length === 0) return '100%'
  return `${100 / props.sessions.length}%`
})

function getDisplayState(session) {
  // Special case: PAUSED + processing = waiting for permission response (yellow blinking)
  if (session.state === 'paused' && session.is_processing) {
    return 'pending-prompt'
  }
  // Normal case: processing overrides state
  return session.is_processing ? 'processing' : session.state
}

function getColor(session) {
  const state = getDisplayState(session)
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
    'failed': '#ffb3b3'        // light red
  }
  return colorMap[state] || '#d3d3d3'
}

function isAnimated(session) {
  const state = getDisplayState(session)
  return state === 'starting' || state === 'processing' || state === 'pending-prompt'
}

function getTooltip(session) {
  const state = getDisplayState(session)
  const stateLabel = state.charAt(0).toUpperCase() + state.slice(1)
  const nameLabel = session.name || 'Unnamed Session'
  const roleLabel = session.is_minion && session.role ? ` (${session.role})` : ''
  return `${nameLabel}${roleLabel}: ${stateLabel}`
}
</script>

<style scoped>
.project-status-line {
  margin: 0;
}
</style>
