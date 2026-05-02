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
        :class="{ 'theme-active': uiStore.isRedBackground }"
        @click="uiStore.toggleBackgroundColor()"
        :title="uiStore.isRedBackground ? 'Disable sensitive environment mode' : 'Enable sensitive environment mode'"
        :aria-label="uiStore.isRedBackground ? 'Disable sensitive environment mode' : 'Enable sensitive environment mode'"
        :aria-pressed="uiStore.isRedBackground"
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
        </svg>
      </button>
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
      <button class="header-btn" @click="uiStore.showModal('global-config')" title="Application Settings (legacy)" aria-label="Application Settings (legacy)">
        ⚙
      </button>
      <button
        class="header-btn settings-new-btn"
        :class="{ 'settings-active': isSettingsRoute }"
        title="Settings (new UI)"
        aria-label="Settings (new UI)"
        @click="toggleSettings()"
      >
        ⚙<sup class="new-badge" aria-hidden="true">new</sup>
      </button>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useUIStore } from '@/stores/ui'
import { usePollingStore } from '@/stores/polling'
import { useRoute, useRouter } from 'vue-router'

const uiStore = useUIStore()
const wsStore = usePollingStore()
const route = useRoute()
const router = useRouter()

const uiConnected = computed(() => wsStore.uiConnected)
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
  router.push(isSettingsRoute.value ? lastContentRoute.value : '/settings/features')
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
  background: #1e293b;
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
  color: #f1f5f9;
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
  color: #94a3b8;
}

.header-indicator.connected {
  color: #94a3b8;
}

.header-indicator.disconnected {
  color: #ef4444;
}

.indicator-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #94a3b8;
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
  border: 1px solid #334155;
  border-radius: 6px;
  color: #e2e8f0;
  font-size: 14px;
  padding: 4px 8px;
  cursor: pointer;
  transition: all 0.15s;
  display: flex;
  align-items: center;
  gap: 4px;
}

.header-btn:hover {
  background: #334155;
  border-color: #475569;
}

.theme-toggle-btn svg {
  display: block;
}

.header-btn.theme-active {
  border-color: #ef4444;
  color: #f87171;
}

.analytics-nav-btn,
.audit-nav-btn {
  font-size: 14px;
}

.analytics-nav-btn.nav-active,
.audit-nav-btn.nav-active {
  border-color: #6366f1;
  color: #a5b4fc;
  background: rgba(99, 102, 241, 0.1);
}

.settings-new-btn {
  color: #a5b4fc;
  position: relative;
}

.settings-new-btn.settings-active {
  border-color: #6366f1;
  color: #a5b4fc;
  background: rgba(99, 102, 241, 0.1);
}

.new-badge {
  font-size: 7px;
  font-weight: 700;
  background: #6366f1;
  color: #fff;
  padding: 1px 3px;
  border-radius: 2px;
  vertical-align: super;
  line-height: 1;
  letter-spacing: 0;
}

@keyframes pulse-error {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}
</style>
