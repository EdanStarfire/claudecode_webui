<template>
  <div class="header-row1">
    <div class="header-brand">
      <img class="header-logo" src="/favicon-32x32.png" alt="ccWebUI logo" width="20" height="20">
      <h1 class="header-title">ccWebUI</h1>
    </div>
    <div class="header-right">
      <div
        class="header-indicator"
        :class="combinedConnectionState"
        data-testid="connection-indicator"
        role="status"
        :aria-label="connectionAriaLabel"
        :title="justSubmitted ? '✓ Debug buffer sent' : 'Right-click (or long-press) to submit the debug buffer'"
        @contextmenu.prevent="handleFlush"
        @touchstart="onTouchStart"
        @touchmove="onTouchMove"
        @touchend="onTouchEnd"
        @touchcancel="onTouchCancel"
      >
        <span class="indicator-dot"></span>
        <span v-if="justSubmitted" class="debug-sent-confirm">✓ Sent</span>
      </div>
      <TrayDropdown />
      <button
        class="header-btn theme-toggle-btn"
        :class="`theme-btn-${uiStore.theme}`"
        @click="uiStore.cycleTheme()"
        :title="`Theme: ${themeLabel} — click to cycle`"
        :aria-label="`Theme: ${themeLabel} — click to cycle`"
      ><svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
        <path d="M2 4.5h7a1.5 1.5 0 0 1 1.5 1.5v.5a1.5 1.5 0 0 1-1.5 1.5H2Z"></path>
        <path d="M5 8v3.5a1.5 1.5 0 0 0 1.5 1.5h0A1.5 1.5 0 0 0 8 11.5V10"></path>
        <path d="M12.5 6.5A1.5 1.5 0 0 1 14 8v3a1.5 1.5 0 0 1-1.5 1.5h0A1.5 1.5 0 0 1 11 11V8a1.5 1.5 0 0 1 1.5-1.5Z"></path>
      </svg></button>
      <button
        class="header-btn analytics-nav-btn"
        :class="{ 'nav-active': isAnalyticsRoute }"
        title="Analytics"
        aria-label="Analytics dashboard"
        @click="toggleAnalytics()"
      ><svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
        <path d="M2.5 13.5v-9"></path>
        <path d="M5.5 13.5v-5"></path>
        <path d="M8.5 13.5v-7"></path>
        <path d="M11.5 13.5v-3"></path>
        <path d="M2.5 13.5h11"></path>
      </svg></button>
      <button
        class="header-btn audit-nav-btn"
        :class="{ 'nav-active': isAuditRoute }"
        title="Audit"
        aria-label="Audit timeline"
        @click="toggleAudit()"
      ><svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
        <path d="M6 2.5h4a.5.5 0 0 1 .5.5v1H5.5V3a.5.5 0 0 1 .5-.5Z"></path>
        <path d="M4.5 3.5H11a1 1 0 0 1 1 1V13a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V4.5a1 1 0 0 1 1-1Z"></path>
        <path d="M6 7.5h4"></path>
        <path d="M6 10h4"></path>
      </svg></button>
      <button class="header-btn" @click="uiStore.showRestartModal()" title="Restart server" aria-label="Restart server">
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
          <path d="M4.5 6.5a3.25 3.25 0 0 1 6.25-1.25A2.5 2.5 0 0 1 11.5 10.5H5A2.25 2.25 0 0 1 4.5 6.5Z"></path>
          <path d="M8 7v4.5m0 0L6.25 9.75M8 11.5l1.75-1.75"></path>
        </svg>
      </button>
      <button
        class="header-btn settings-btn"
        :class="{ 'settings-active': isSettingsRoute }"
        title="Settings"
        aria-label="Settings"
        @click="toggleSettings()"
      >
        ⚙
      </button>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useUIStore } from '@/stores/ui'
import { usePollingStore } from '@/stores/polling'
import { useSessionStore } from '@/stores/session'
import { useRoute, useRouter } from 'vue-router'
import TrayDropdown from './TrayDropdown.vue'
import { useLongPress } from '@/composables/useLongPress'
import { flushDebugBuffer } from '@/composables/useDebugBuffer'

const uiStore = useUIStore()
const wsStore = usePollingStore()
const sessionStore = useSessionStore()
const route = useRoute()
const router = useRouter()

// Epic #1990 stage 5b: this single dot is the app's only live connection indicator — it
// derives directly from each StreamConnection's own `state`, never from wall-clock
// inference. `idle`/`connecting` both read as "disconnected" (a session that has never
// yet connected looks the same as the brief in-flight-fetch state); `archived` is handled
// as a separate override below rather than through this severity table, since it's a
// calm/informational state that should only win when nothing else needs attention.
function labelForState(state) {
  if (state === 'idle' || state === 'connecting') return 'disconnected'
  if (state === 'unreachable') return 'backend-unreachable'
  if (state === 'backoff') return 'reconnecting'
  if (state === 'resyncing') return 'resyncing'
  if (state === 'connected') return 'connected'
  return null
}

// Worst-to-best: an outright browser<->Frontend disconnect is the most alarming (Backend's
// status can't even be observed), followed by a genuine Backend outage (issue #1989 —
// "degraded" ranked worse than plain "unreachable" since it needs a manual restart rather
// than self-healing), then active retry, then an in-progress resync (the slot "stalled"
// used to occupy), then healthy.
const STATE_SEVERITY = {
  disconnected: 0,
  'backend-degraded': 1,
  'backend-unreachable': 2,
  reconnecting: 3,
  resyncing: 4,
  partial: 4.5,
  connected: 5,
}

// Issue #1977 (AC4): a failed initial data load (transport connected, but projects/sessions
// never loaded) reuses the severity slot just below "connected" the old 'stalled' label used
// to occupy — distinct from the new `resyncing` state, which means something different (an
// active evicted/stream_reset recovery in progress), not "data load failed".
const uiChannelState = computed(() => {
  if (uiStore.appDataStatus === 'failed' && wsStore.uiState === 'connected') return 'partial'
  return labelForState(wsStore.uiState) || 'disconnected'
})
// Gated on wsStore's OWN currentSessionId (the session its connection is actually
// targeting), not sessionStore.currentSessionId — archive views set this explicitly via
// markSessionArchived() now (AC3/AC4), so there is no more stale/leftover id from a
// different, already-torn-down session to leak into this computation.
const sessionChannelState = computed(() => {
  if (!wsStore.currentSessionId) return null
  return labelForState(wsStore.sessionState)
})
const isSessionArchived = computed(() => !!wsStore.currentSessionId && wsStore.sessionState === 'archived')

// Issue #1989: Backend-health signal, independent of the transport states above — stays
// 'connected' from either transport's point of view throughout a pure Backend-side
// outage, so this is the only thing that can surface it on the indicator. Checked across
// both streams' own backendStatus field (decision 1: kept separate per StreamConnection
// instance, not collapsed into `state`) since whichever one last polled successfully may
// differ by a beat.
const backendChannelState = computed(() => {
  if (wsStore.uiBackendStatus === 'degraded' || wsStore.sessionBackendStatus === 'degraded') return 'backend-degraded'
  if (wsStore.uiBackendStatus === 'unreachable' || wsStore.sessionBackendStatus === 'unreachable') return 'backend-unreachable'
  return null
})

const combinedConnectionState = computed(() => {
  const states = [uiChannelState.value]
  if (sessionChannelState.value) states.push(sessionChannelState.value)
  if (backendChannelState.value) states.push(backendChannelState.value)
  const worst = states.reduce((w, s) => (STATE_SEVERITY[s] < STATE_SEVERITY[w] ? s : w))
  // AC3/AC4: an archived/no-live-stream session view reads as its own distinct state
  // instead of either a stale "connected" or a spurious "disconnected" — but only when
  // nothing else actually needs attention (a genuine UI/backend problem still wins).
  if (worst === 'connected' && isSessionArchived.value) return 'archived'
  return worst
})

const CONNECTION_LABELS = {
  connected: 'Connected',
  reconnecting: 'Reconnecting',
  resyncing: 'Resyncing',
  partial: 'Partially loaded',
  disconnected: 'Disconnected',
  'backend-unreachable': 'Backend unreachable',
  'backend-degraded': 'Backend degraded',
  archived: 'Archived',
}
const connectionAriaLabel = computed(() => `Connection status: ${CONNECTION_LABELS[combinedConnectionState.value]}`)

// Issue #1931: right-click/long-press the connection indicator to submit the debug ring buffer.
const justSubmitted = ref(false)
let justSubmittedTimer = null

async function handleFlush() {
  const attempted = await flushDebugBuffer('manual')
  if (!attempted) return
  justSubmitted.value = true
  clearTimeout(justSubmittedTimer)
  justSubmittedTimer = setTimeout(() => { justSubmitted.value = false }, 2000)
}

const { onTouchStart, onTouchMove, onTouchEnd, onTouchCancel } = useLongPress(handleFlush)

const THEME_LABELS = {
  'light':           'Light',
  'dark':            'Dark',
  'sensitive-light': 'Sensitive Light',
  'sensitive-dark':  'Sensitive Dark',
}
const themeLabel = computed(() => THEME_LABELS[uiStore.theme] || 'Light')
const isSettingsRoute  = computed(() => route.path.startsWith('/settings/'))
const isAnalyticsRoute = computed(() => route.path === '/analytics')
const isAuditRoute     = computed(() => route.path === '/audit')

function isSpecialRoute(path) {
  return path.startsWith('/settings/') || path === '/analytics' || path === '/audit'
}

watch(() => route.path, (path) => {
  if (!isSpecialRoute(path)) uiStore.setLastContentRoute(path)
}, { immediate: true })

function toggleSettings() {
  if (isSettingsRoute.value) {
    router.push(uiStore.lastContentRoute)
    return
  }
  const sessionId = sessionStore.currentSessionId
  router.push(sessionId ? `/settings/session/${sessionId}/general` : '/settings/features')
}

function toggleAnalytics() {
  router.push(isAnalyticsRoute.value ? uiStore.lastContentRoute : '/analytics')
}

function toggleAudit() {
  router.push(isAuditRoute.value ? uiStore.lastContentRoute : '/audit')
}
</script>

<style scoped>
.header-row1 {
  height: 42px;
  background: var(--bs-tertiary-bg);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 16px;
  flex-shrink: 0;
  z-index: 100;
}

.header-brand {
  display: flex;
  align-items: center;
  gap: 8px;
}

.header-logo {
  width: 20px;
  height: 20px;
  display: block;
}

.header-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--bs-emphasis-color);
  margin: 0;
  letter-spacing: 0.3px;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 8px;
}

.header-indicator {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  color: var(--bs-secondary-color);
  position: relative;
}

.debug-sent-confirm {
  position: absolute;
  top: 100%;
  left: 0;
  margin-top: 4px;
  padding: 2px 6px;
  border-radius: 4px;
  background-color: #22c55e;
  color: #fff;
  font-size: 11px;
  white-space: nowrap;
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.15);
  z-index: 10;
}

.header-indicator.connected {
  color: var(--bs-secondary-color);
}

.header-indicator.disconnected {
  color: #ef4444;
}

.header-indicator.reconnecting {
  color: #f59e0b;
}

/* Epic #1990 stage 5b: an active evicted/stream_reset resync — occupies the slot the old
   per-session "stalled" heartbeat-watchdog state used to, same amber-orange hue and
   "attention needed" pulse, but now driven by the StreamConnection's own `resyncing`
   state rather than a wall-clock staleness inference. */
.header-indicator.resyncing {
  color: #fd7e14;
}

/* Issue #1977 (AC4): transport connected, but the initial projects/sessions load failed —
   a calmer amber than reconnecting/resyncing since the transport itself is fine. */
.header-indicator.partial {
  color: #f59e0b;
}

/* Issue #1989: Backend-side outage, violet family — distinct from the transport-level
   red/amber/orange states above, since it's a different failure mode (browser<->Frontend
   is fine, Frontend<->Backend is not). backend-degraded uses a darker shade of the same
   hue (not red — #dc2626 read as near-identical to disconnected's #ef4444 in review) so
   the two backend states read as a related pair while still being distinguishable from
   each other by severity. */
.header-indicator.backend-unreachable {
  color: #a855f7;
}

.header-indicator.backend-degraded {
  color: #7e22ce;
}

/* Epic #1990 stage 5b (AC3/AC4): archived/no-live-stream session view — a calm, neutral
   informational state, deliberately NOT part of the alarm-color family above (nothing is
   wrong; there's simply no live stream to show) and no pulse animation. */
.header-indicator.archived {
  color: var(--bs-secondary-color);
}

.indicator-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--bs-secondary-color);
}

.header-indicator.connected .indicator-dot {
  background: #22c55e;
}

.header-indicator.disconnected .indicator-dot {
  background: #ef4444;
  animation: pulse-error 1.5s infinite;
}

.header-indicator.reconnecting .indicator-dot {
  background: #f59e0b;
  animation: pulse-error 1.5s infinite;
}

.header-indicator.resyncing .indicator-dot {
  background: #fd7e14;
  animation: pulse-error 1.5s infinite;
}

.header-indicator.partial .indicator-dot {
  background: #f59e0b;
  animation: pulse-error 1.5s infinite;
}

.header-indicator.backend-unreachable .indicator-dot {
  background: #a855f7;
  animation: pulse-error 1.5s infinite;
}

.header-indicator.backend-degraded .indicator-dot {
  background: #7e22ce;
  animation: pulse-error 1.5s infinite;
}

.header-indicator.archived .indicator-dot {
  background: var(--bs-secondary-color);
}

.theme-btn-light      { color: #94a3b8; }
.theme-btn-dark       { color: #818cf8; border-color: #818cf8; }
.theme-btn-sensitive-light { color: #ef4444; border-color: #fca5a5; }
.theme-btn-sensitive-dark  { color: #f87171; border-color: #f87171; }

.analytics-nav-btn.nav-active,
.audit-nav-btn.nav-active {
  border-color: #6366f1;
  color: var(--bs-link-color);
  background: rgba(99, 102, 241, 0.1);
}

.settings-btn {
  color: var(--bs-link-color);
}

.settings-btn.settings-active {
  border-color: #6366f1;
  color: var(--bs-link-color);
  background: rgba(99, 102, 241, 0.1);
}

@keyframes pulse-error {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}
</style>
