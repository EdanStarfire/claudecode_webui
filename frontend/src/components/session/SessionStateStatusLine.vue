<template>
  <div class="progress session-state-status-line" style="height: 4px; border-radius: 0">
    <div
      v-if="session"
      class="progress-bar"
      :class="{ 'progress-bar-striped progress-bar-animated': isAnimated }"
      :style="{
        width: '100%',
        backgroundColor: effectiveColor
      }"
      :title="statusTooltip"
      :aria-label="`Session status: ${displayState}${isUnreviewed ? ' (new since last viewed)' : ''}`"
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
import { useSessionState, UNREAD_COLOR } from '@/composables/useSessionState'

const props = defineProps({
  sessionId: {
    type: String,
    required: true
  }
})

const sessionStore = useSessionStore()

const session = computed(() => sessionStore.sessions.get(props.sessionId))

const { displayState, statusColor, isAnimated } = useSessionState(session)

const isUnreviewed = computed(() => sessionStore.isUnreviewed(props.sessionId))
const effectiveColor = computed(() => isUnreviewed.value ? UNREAD_COLOR : statusColor.value)

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
