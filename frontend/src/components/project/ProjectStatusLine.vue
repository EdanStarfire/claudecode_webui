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
        :class="{ 'progress-bar-striped progress-bar-animated': isAnimatedState(session) }"
        :style="{
          width: segmentWidth,
          backgroundColor: getStatusColor(session),
          cursor: 'help'
        }"
        :title="getTooltip(session)"
      ></div>
    </template>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { getDisplayState, getStatusColor, isAnimatedState } from '@/composables/useSessionState'

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

function getTooltip(session) {
  const state = getDisplayState(session)
  const stateLabel = state.charAt(0).toUpperCase() + state.slice(1)
  const nameLabel = session.name || 'Unnamed Session'
  // Issue #349: All sessions are minions - always show role if present
  const roleLabel = session.role ? ` (${session.role})` : ''
  return `${nameLabel}${roleLabel}: ${stateLabel}`
}
</script>

<style scoped>
.project-status-line {
  margin: 0;
}
</style>
