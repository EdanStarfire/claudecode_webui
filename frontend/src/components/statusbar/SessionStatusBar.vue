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
        <button
          class="btn btn-sm btn-outline-secondary"
          @click="showEdit"
          :disabled="!session"
          aria-label="Edit session"
        >
          🖊️ <span class="button-label">Edit</span>
        </button>
      </div>

      <!-- Rate Limit Indicator (Issue #899) -->
      <div v-if="hasRateLimitData" class="d-flex gap-2 align-items-center rate-limit-indicator">
        <RateLimitBadge
          v-if="uiStore.rateLimits?.five_hour?.used_percentage != null"
          label="5h"
          :pct="uiStore.rateLimits.five_hour.used_percentage"
          :resets-at="uiStore.rateLimits.five_hour?.resets_at"
        />
        <RateLimitBadge
          v-if="uiStore.rateLimits?.seven_day?.used_percentage != null"
          label="7d"
          :pct="uiStore.rateLimits.seven_day.used_percentage"
          :resets-at="uiStore.rateLimits.seven_day?.resets_at"
        />
      </div>

      <!-- Center: Context usage indicator (issue #905) -->
      <div v-if="contextPct !== null" class="d-flex align-items-center gap-1">
        <small class="text-muted">Ctx</small>
        <div class="progress" style="width: 60px; height: 8px;" :title="contextTitle">
          <div
            class="progress-bar"
            :class="contextBarClass"
            :style="{ width: contextPct + '%' }"
          ></div>
        </div>
        <small :class="contextTextClass">{{ contextPct }}%</small>
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
import RateLimitBadge from './RateLimitBadge.vue'

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
  dontAsk: '🚫',
  auto: '🤖',
  bypassPermissions: '☢️'
}

const modeLabels = {
  default: 'Default',
  acceptEdits: 'Accept Edits',
  plan: 'Plan',
  dontAsk: "Don't Ask",
  auto: 'Auto',
  bypassPermissions: 'Bypass'
}

const modeIcon = computed(() => modeIcons[currentMode.value] || '🔒')
const modeName = computed(() => modeLabels[currentMode.value] || currentMode.value)

// Mode cycling order: all 6 modes
const modeOrder = ['default', 'acceptEdits', 'plan', 'dontAsk', 'auto', 'bypassPermissions']

const cycleMode = async () => {
  if (!session.value) return

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

const showEdit = () => {
  uiStore.showModal('edit-session', { session: session.value })
}

const toggleReadAloud = () => {
  uiStore.setTTSReadAloud(!uiStore.ttsReadAloudEnabled)
}

const toggleAutoScroll = () => {
  uiStore.setAutoScroll(!uiStore.autoScrollEnabled)
}

// Issue #905: Context window usage
const contextPct = computed(() => session.value?.context_pct ?? null)
const contextInputTokens = computed(() => session.value?.context_input_tokens ?? null)
const contextWindow = computed(() => session.value?.context_window ?? null)

const contextTitle = computed(() => {
  if (contextInputTokens.value === null) return ''
  const used = (contextInputTokens.value / 1000).toFixed(0)
  const total = (contextWindow.value / 1000).toFixed(0)
  return `${used}K / ${total}K tokens (${contextPct.value}%)`
})

const contextBarClass = computed(() => {
  const p = contextPct.value
  if (p === null) return ''
  if (p >= 80) return 'bg-danger'
  if (p >= 50) return 'bg-warning'
  return 'bg-success'
})

const contextTextClass = computed(() => {
  const p = contextPct.value
  if (p === null) return ''
  if (p >= 80) return 'text-danger'
  if (p >= 50) return 'text-warning'
  return 'text-success'
})

const hasRateLimitData = computed(() =>
  uiStore.rateLimits?.five_hour?.used_percentage != null ||
  uiStore.rateLimits?.seven_day?.used_percentage != null
)
</script>

<style scoped>
.session-status-bar {
  background-color: #f8f9fa;
}

/* Hide button labels on mobile */
@media (max-width: 768px) {
  .button-label,
  .mode-label {
    display: none;
  }
}
</style>
