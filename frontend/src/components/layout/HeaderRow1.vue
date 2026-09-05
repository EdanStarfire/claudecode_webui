<template>
  <div class="header-row1">
    <div class="header-brand">
      <img class="header-logo" src="/favicon-32x32.png" alt="ccWebUI logo" width="20" height="20">
      <h1 class="header-title">ccWebUI</h1>
    </div>
    <div class="header-right">
      <div
        class="header-indicator"
        :class="uiConnected ? 'connected' : 'disconnected'"
        data-testid="connection-indicator"
        role="status"
        :aria-label="connectionAriaLabel"
      >
        <span class="indicator-dot"></span>
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
import { computed, watch } from 'vue'
import { useUIStore } from '@/stores/ui'
import { usePollingStore } from '@/stores/polling'
import { useSessionStore } from '@/stores/session'
import { useRoute, useRouter } from 'vue-router'
import TrayDropdown from './TrayDropdown.vue'

const uiStore = useUIStore()
const wsStore = usePollingStore()
const sessionStore = useSessionStore()
const route = useRoute()
const router = useRouter()

const uiConnected = computed(() => wsStore.uiConnected)
const connectionAriaLabel = computed(() => `Connection status: ${uiConnected.value ? 'Connected' : 'Disconnected'}`)

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
