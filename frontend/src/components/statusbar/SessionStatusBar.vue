<template>
  <div class="session-status-bar border-top p-2" :class="uiStore.isRedBackground ? 'theme-red-panel' : 'bg-body-secondary'">
    <div class="d-flex justify-content-between align-items-center gap-2">
      <!-- Left side: Mode, Info, Manage -->
      <div class="d-flex gap-2 align-items-center">
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
          @click="editSession"
          :disabled="!session"
          aria-label="Edit session settings"
        >
          ✏️ <span class="button-label">Edit</span>
        </button>
        <button
          class="btn btn-sm btn-outline-secondary"
          @click="showManage"
          :disabled="!session"
          aria-label="Manage session"
        >
          🛠 <span class="button-label">Manage</span>
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

      <!-- Auto-resume pending indicator (Issue #2190) -->
      <AutoResumeBadge v-if="autoResumeSchedule" :fire-at="autoResumeSchedule.next_run" />

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

      <!-- Right side: Turn navigation + Read Aloud + Autoscroll -->
      <div class="d-flex gap-2">
        <button
          class="btn btn-sm btn-outline-secondary"
          @click="jumpToPrevTurn"
          :disabled="prevTurnLocked || !hasPrevTurn || isJumpingTurn"
          aria-label="Jump to previous turn"
          data-testid="turn-prev"
          :title="prevTurnLocked ? TURN_NAV_LOCKED_TITLE : 'Jump to previous user message or comm'"
        >
          <!-- while a jump loads, a spinner replaces the icon so the button does not grow (390 px phones) -->
          <span v-if="isJumpingTurn" class="spinner-border spinner-border-sm" aria-hidden="true" data-testid="turn-jump-progress"></span><template v-else>⏮️</template> <span class="button-label">Prev</span>
        </button>
        <button
          class="btn btn-sm btn-outline-secondary"
          @click="jumpToNextTurn"
          :disabled="nextTurnLocked || !hasNextTurn || isJumpingTurn"
          aria-label="Jump to next turn"
          data-testid="turn-next"
          :title="indexUnavailable || nextTurnLocked ? TURN_NAV_LOCKED_TITLE : 'Jump to next user message or comm'"
        >
          ⏭️ <span class="button-label">Next</span>
        </button>
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
          :class="{
            'btn-primary': effectiveAutoScrollState === 'on',
            'btn-warning': effectiveAutoScrollState === 'suspended',
            'btn-outline-secondary': effectiveAutoScrollState === 'off'
          }"
          @click="toggleAutoScroll"
          aria-label="Toggle auto-scroll"
          :aria-pressed="effectiveAutoScrollState === 'on'"
          :title="effectiveAutoScrollState === 'on' ? 'Auto-scroll on — following new messages' : effectiveAutoScrollState === 'suspended' ? 'Auto-scroll paused — scrolled up. Click to jump to bottom.' : 'Auto-scroll disabled'"
        >
          ⬇️ {{ effectiveAutoScrollState === 'on' ? 'ON' : effectiveAutoScrollState === 'suspended' ? 'PAUSED' : 'OFF' }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useSessionStore } from '@/stores/session'
import { useUIStore } from '@/stores/ui'
import { useMessageStore } from '@/stores/message'
import { useScheduleStore } from '@/stores/schedule'
import { historyBackfill } from '@/composables/useHistoryBackfill'
import { formatJumpSize, jumpNeedsConfirm } from '@/utils/turnNavigation'
import { AROUND_WINDOW_FILE_BYTES } from '@/utils/historyIntervals'
import RateLimitBadge from './RateLimitBadge.vue'
import AutoResumeBadge from './AutoResumeBadge.vue'

const props = defineProps({
  sessionId: {
    type: String,
    required: true
  },
  virtualNav: {
    type: Object,
    default: null
  }
})

const router = useRouter()
const sessionStore = useSessionStore()
const uiStore = useUIStore()
const scheduleStore = useScheduleStore()

const session = computed(() => sessionStore.sessions.get(props.sessionId))
// 'manual' is the canonical new-write value for the legacy 'default' mode; normalize
// so label/icon lookup and cycling (keyed on 'default') resolve correctly either way.
const currentMode = computed(() => {
  const mode = session.value?.current_permission_mode || 'default'
  return mode === 'manual' ? 'default' : mode
})

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
  default: 'Manual',
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

  // Try each subsequent mode in order, skipping any that the SDK rejects
  const currentIndex = modeOrder.indexOf(currentMode.value)
  for (let i = 1; i <= modeOrder.length; i++) {
    const tryMode = modeOrder[(currentIndex + i) % modeOrder.length]
    try {
      await sessionStore.setPermissionMode(props.sessionId, tryMode)
      return
    } catch {
      // Mode not available (e.g. plan restriction) — try next
    }
  }
}

const showInfo = () => {
  uiStore.showModal('session-info', { sessionId: props.sessionId })
}

const editSession = () => {
  router.push(`/settings/session/${props.sessionId}/general`)
}

const showManage = () => {
  uiStore.showModal('manage-session', { session: session.value })
}

const toggleReadAloud = () => {
  uiStore.setTTSReadAloud(!uiStore.ttsReadAloudEnabled)
}

// Issue #1631: Three-way effective auto-scroll state
// 'on' = pref enabled + at bottom, 'suspended' = pref enabled + scrolled up, 'off' = pref disabled
const effectiveAutoScrollState = computed(() => {
  if (!uiStore.autoScrollEnabled) return 'off'
  const sticky = uiStore.stickyToBottomBySession.get(props.sessionId)
  return sticky === false ? 'suspended' : 'on'
})

const toggleAutoScroll = () => {
  if (effectiveAutoScrollState.value === 'on') {
    uiStore.setAutoScroll(false)
  } else {
    uiStore.setAutoScroll(true)
    uiStore.requestScrollToBottom(props.sessionId)
  }
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

// Issue #2190: "rate-limited, auto-resume pending" indicator — independent of
// SessionState (Option B keeps the session truthfully ACTIVE while held), driven
// entirely by the existing schedule-store data already eagerly loaded for the
// current project (see AgentStrip.vue/BreadcrumbNavHeader.vue).
const autoResumeSchedule = computed(() => scheduleStore.getAutoResumeSchedule(props.sessionId))

// Issue #1848: jump to previous/next turn-boundary message (user message or inbound comm).
// virtualNav is MessageList's exposed API, forwarded down as a prop from SessionView.vue the
// same way it already is to PermissionQueue — provide/inject doesn't reach siblings.
const isJumpingTurn = ref(false)

// Issue #2030: turn navigation runs over the server's history index, so Prev works right after
// first paint on a tail-only load. A target that is not loaded opens ONE bounded window around it
// (historyBackfill.jumpAround → the pinned island; stage 3 PR 3C-1) — after a confirm on a slow
// link only, naming the window size. Prev and Next both walk the index across the gap between an
// island and the live end. Without a usable index (old server, failure, still loading) Prev keeps
// the stage-1 behaviour: locked until the whole history is loaded.
const messageStore = useMessageStore()
const TURN_NAV_LOCKED_TITLE = 'Loading older history…'

const prevTurn = computed(() => props.virtualNav?.findPrevTurn?.() ?? null)
const nextTurn = computed(() => props.virtualNav?.findNextTurn?.() ?? null)
// An 'idle' index (not requested) is fetched automatically on a normal link, so Prev waits for it
// like for a loading one; on a slow link nothing loads unrequested, so Prev stays enabled and
// pressing it fetches the index.
const slowLink = computed(() => historyBackfill.getMode(props.sessionId) === 'manual')
// Old server / failed index: the stage-1 behaviour — both buttons locked until the history is complete.
const indexUnavailable = computed(() =>
  !messageStore.isHistoryComplete(props.sessionId) && messageStore.getHistoryIndex(props.sessionId)?.status === 'unavailable')
const turnLocked = (turn) => indexUnavailable.value ||
  (turn?.kind === 'pending' && (turn.reason !== 'idle' || !slowLink.value))
const prevTurnLocked = computed(() => turnLocked(prevTurn.value))
const nextTurnLocked = computed(() => nextTurn.value?.kind === 'pending' && turnLocked(nextTurn.value))
const hasPrevTurn = computed(() => prevTurn.value != null)
const hasNextTurn = computed(() => !indexUnavailable.value && nextTurn.value != null)

async function jumpToTurn(direction) {
  if (isJumpingTurn.value) return
  isJumpingTurn.value = true
  try {
    const find = () => (direction === 'prev' ? props.virtualNav?.findPrevTurn?.() : props.virtualNav?.findNextTurn?.())
    let target = find()
    if (target?.kind === 'pending' && target.reason === 'idle') {
      await messageStore.ensureHistoryIndex(props.sessionId)
      target = find()
    }
    if (!target || target.kind === 'pending') return
    if (target.kind === 'loaded') {
      await props.virtualNav.scrollToItemIndex(target.index, { align: 'start', behavior: 'auto' })
      return
    }
    await jumpToUnloadedTurn(target)
  } finally {
    isJumpingTurn.value = false
  }
}

const jumpToPrevTurn = () => jumpToTurn('prev')
const jumpToNextTurn = () => jumpToTurn('next')

async function jumpToUnloadedTurn(target) {
  const sessionId = props.sessionId
  if (jumpNeedsConfirm({ slowLink: slowLink.value }) &&
      typeof window !== 'undefined' && typeof window.confirm === 'function' &&
      !window.confirm(`Jump to turn ${target.ordinal}? Loads about ${formatJumpSize(AROUND_WINDOW_FILE_BYTES)} of history`)) {
    return
  }
  props.virtualNav?.beginTurnJump?.()
  const jump = historyBackfill.jumpAround(sessionId, { cursor: target.cursor }, { label: `turn ${target.ordinal}`, ordinal: target.ordinal })
  try {
    await jump.promise
    await props.virtualNav?.scrollToTurnMessage?.(target.messageId)
  } catch {
    // cancelled, superseded, session switched or target missing: the loaded intervals are left as is
  } finally {
    props.virtualNav?.endTurnJump?.()
  }
}

</script>

<style scoped>
.session-status-bar {
  background-color: var(--bs-secondary-bg);
}

/* The progress text must not wrap the icon onto two lines (390 px phones). */
[data-testid='turn-prev'] {
  white-space: nowrap;
}

/* Hide button labels on mobile */
@media (max-width: 768px) {
  .button-label,
  .mode-label {
    display: none;
  }
}

</style>
