<template>
  <div class="session-status-bar border-top p-2" :class="uiStore.isRedBackground ? 'theme-red-panel' : 'bg-light'">
    <div class="d-flex justify-content-between align-items-center gap-2">
      <!-- Left side: Mode, Info, Manage -->
      <div class="d-flex gap-2">
        <button
          class="btn btn-sm btn-outline-secondary"
          @click="cycleMode"
          :disabled="!session"
          aria-label="Cycle permission mode"
        >
          {{ modeIcon }} <span class="mode-label">{{ modeName }}</span>
        </button>
        <button
          class="btn btn-sm btn-outline-secondary"
          @click="showInfo"
          :disabled="!session"
          aria-label="View session info"
        >
          ℹ️ <span class="button-label">Info</span>
        </button>
        <button
          class="btn btn-sm btn-outline-secondary"
          @click="showManage"
          :disabled="!session"
          aria-label="Manage session"
        >
          ⚙️ <span class="button-label">Manage</span>
        </button>
      </div>

      <!-- Right side: Read Aloud + Autoscroll -->
      <div class="d-flex gap-2">
        <button
          class="btn btn-sm"
          :class="uiStore.ttsReadAloudEnabled ? 'btn-primary' : 'btn-outline-secondary'"
          @click="toggleReadAloud"
          aria-label="Toggle read aloud"
          :aria-pressed="uiStore.ttsReadAloudEnabled"
        >
          {{ uiStore.ttsReadAloudEnabled ? '\uD83D\uDD0A' : '\uD83D\uDD07' }}
          <span class="button-label">{{ uiStore.ttsReadAloudEnabled ? 'ON' : 'OFF' }}</span>
        </button>
        <button
          class="btn btn-sm"
          :class="uiStore.autoScrollEnabled ? 'btn-primary' : 'btn-outline-secondary'"
          @click="toggleAutoScroll"
          aria-label="Toggle auto-scroll"
          :aria-pressed="uiStore.autoScrollEnabled"
        >
          ⬇️ {{ uiStore.autoScrollEnabled ? 'ON' : 'OFF' }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useSessionStore } from '@/stores/session'
import { useUIStore } from '@/stores/ui'

const props = defineProps({
  sessionId: {
    type: String,
    required: true
  }
})

const sessionStore = useSessionStore()
const uiStore = useUIStore()

const session = computed(() => sessionStore.sessions.get(props.sessionId))
const currentMode = computed(() => session.value?.current_permission_mode || 'default')

// Mode icon mapping
const modeIcons = {
  default: '🔒',
  acceptEdits: '✏️',
  plan: '📋',
  bypassPermissions: '☢️'
}

const modeIcon = computed(() => modeIcons[currentMode.value] || '🔒')
const modeName = computed(() => currentMode.value)

// Mode cycling order (default → acceptEdits → plan → default)
// Note: bypassPermissions is NOT in the cycle, but will display if session is in that mode
const modeOrder = ['default', 'acceptEdits', 'plan']

const cycleMode = async () => {
  if (!session.value) return

  // If currently in bypassPermissions, cycle to default
  // (bypassPermissions is not part of the normal cycle)
  if (currentMode.value === 'bypassPermissions') {
    try {
      await sessionStore.setPermissionMode(props.sessionId, 'default')
    } catch (error) {
      console.error('Failed to cycle permission mode:', error)
    }
    return
  }

  // Normal cycling through default → acceptEdits → plan → default
  const currentIndex = modeOrder.indexOf(currentMode.value)
  const nextIndex = (currentIndex + 1) % modeOrder.length
  const nextMode = modeOrder[nextIndex]

  try {
    await sessionStore.setPermissionMode(props.sessionId, nextMode)
  } catch (error) {
    console.error('Failed to cycle permission mode:', error)
  }
}

const showInfo = () => {
  uiStore.showModal('session-info', { sessionId: props.sessionId })
}

const showManage = () => {
  uiStore.showModal('manage-session', { session: session.value })
}

const toggleReadAloud = () => {
  uiStore.setTTSReadAloud(!uiStore.ttsReadAloudEnabled)
}

const toggleAutoScroll = () => {
  uiStore.setAutoScroll(!uiStore.autoScrollEnabled)
}
</script>

<style scoped>
.session-status-bar {
  background-color: #f8f9fa;
}

/* Hide button labels on mobile, keep mode label visible */
@media (max-width: 768px) {
  .button-label {
    display: none;
  }
}
</style>
