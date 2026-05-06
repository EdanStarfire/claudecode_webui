<template>
  <div class="header-row1">
    <h1 class="header-title">Claude Code WebUI</h1>
    <div class="header-right">
      <div class="header-indicator" :class="uiConnected ? 'connected' : 'disconnected'" data-testid="connection-indicator">
        <span class="indicator-dot"></span>
        {{ uiConnected ? 'Connected' : 'Disconnected' }}
      </div>
      <button
        class="header-btn theme-toggle-btn"
        :class="`theme-btn-${uiStore.theme}`"
        @click="uiStore.cycleTheme()"
        :title="`Theme: ${themeLabel} — click to cycle`"
        :aria-label="`Theme: ${themeLabel} — click to cycle`"
      >●</button>
      <button
        class="header-btn analytics-nav-btn"
        :class="{ 'nav-active': isAnalyticsRoute }"
        title="Analytics"
        aria-label="Analytics dashboard"
        @click="toggleAnalytics()"
      >◈</button>
      <button
        class="header-btn audit-nav-btn"
        :class="{ 'nav-active': isAuditRoute }"
        title="Audit"
        aria-label="Audit timeline"
        @click="toggleAudit()"
      >⎗</button>
      <button class="header-btn" @click="uiStore.showRestartModal()" title="Restart server" aria-label="Restart server">
        ↻
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

const uiStore = useUIStore()
const wsStore = usePollingStore()
const sessionStore = useSessionStore()
const route = useRoute()
const router = useRouter()

const uiConnected = computed(() => wsStore.uiConnected)

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

const lastContentRoute = ref('/')
watch(() => route.path, (path) => {
  if (!isSpecialRoute(path)) lastContentRoute.value = path
}, { immediate: true })

function toggleSettings() {
  if (isSettingsRoute.value) {
    const sessionId = route.params.sessionId
    if (sessionId && sessionId !== '__new__') {
      router.push(`/session/${sessionId}`)
    } else {
      router.push(lastContentRoute.value)
    }
    return
  }
  const sessionId = sessionStore.currentSessionId
  if (sessionId) {
    router.push(`/settings/session/${sessionId}/general`)
  } else {
    router.push('/settings/features')
  }
}

function toggleAnalytics() {
  router.push(isAnalyticsRoute.value ? lastContentRoute.value : '/analytics')
}

function toggleAudit() {
  router.push(isAuditRoute.value ? lastContentRoute.value : '/audit')
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
}

.header-indicator.connected {
  color: var(--bs-secondary-color);
}

.header-indicator.disconnected {
  color: #ef4444;
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

.header-btn {
  background: none;
  border: 1px solid var(--bs-border-color);
  border-radius: 6px;
  color: var(--bs-body-color);
  font-size: 14px;
  width: 28px;
  height: 28px;
  padding: 0;
  cursor: pointer;
  transition: all 0.15s;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.header-btn:hover {
  background: var(--bs-secondary-bg);
  border-color: var(--bs-border-color);
}

.theme-btn-light      { color: #94a3b8; }
.theme-btn-dark       { color: #818cf8; border-color: #818cf8; }
.theme-btn-sensitive-light { color: #ef4444; border-color: #fca5a5; }
.theme-btn-sensitive-dark  { color: #f87171; border-color: #f87171; }

.analytics-nav-btn,
.audit-nav-btn {
  font-size: 14px;
}

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
