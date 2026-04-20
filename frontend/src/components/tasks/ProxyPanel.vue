<template>
  <div class="proxy-panel">
    <!-- Sub-tab bar -->
    <div class="proxy-sub-tabs d-flex align-items-center gap-1 px-3 py-2 border-bottom">
      <button
        class="btn btn-sm"
        :class="proxyStore.activeSubTab === 'http' ? 'btn-primary' : 'btn-outline-secondary'"
        @click="proxyStore.setSubTab('http')"
      >
        HTTP
        <span v-if="proxyStore.currentHttpCount > 0" class="ms-1 badge bg-secondary">
          {{ proxyStore.currentHttpCount }}
        </span>
      </button>
      <button
        class="btn btn-sm"
        :class="proxyStore.activeSubTab === 'dns' ? 'btn-primary' : 'btn-outline-secondary'"
        @click="proxyStore.setSubTab('dns')"
      >
        DNS
        <span v-if="proxyStore.currentDnsCount > 0" class="ms-1 badge bg-secondary">
          {{ proxyStore.currentDnsCount }}
        </span>
      </button>
    </div>

    <!-- HTTP sub-tab -->
    <div v-show="proxyStore.activeSubTab === 'http'" class="proxy-log-list">
      <div v-if="proxyStore.currentHttpLogs.length === 0" class="empty-state">
        No HTTP requests logged yet
      </div>
      <div
        v-for="entry in proxyStore.currentHttpLogs"
        :key="`${entry.ts}-${entry.host}-${entry.path}`"
        class="proxy-entry http-entry"
      >
        <span class="entry-status" :class="entry.allowed ? 'status-allowed' : 'status-blocked'">
          {{ entry.allowed ? '✓' : '✗' }}
        </span>
        <span class="entry-method">{{ entry.method }}</span>
        <span
          class="entry-http-status"
          :class="httpStatusClass(entry.status)"
        >{{ entry.status }}</span>
        <span class="entry-host">{{ entry.host }}</span>
        <span class="entry-path" :title="entry.path">{{ entry.path }}</span>
        <span class="entry-time">{{ formatTime(entry.ts) }}</span>
      </div>
    </div>

    <!-- DNS sub-tab -->
    <div v-show="proxyStore.activeSubTab === 'dns'" class="proxy-log-list">
      <div v-if="proxyStore.currentDnsLogs.length === 0" class="empty-state">
        No DNS queries logged yet
      </div>
      <div
        v-for="(entry, idx) in proxyStore.currentDnsLogs"
        :key="idx"
        class="proxy-entry dns-entry"
      >
        <span class="entry-status" :class="entry.result === 'NOERROR' ? 'status-allowed' : 'status-blocked'">
          {{ entry.result === 'NOERROR' ? '✓' : '✗' }}
        </span>
        <span class="entry-query-type">{{ entry.query_type }}</span>
        <span class="entry-hostname">{{ entry.hostname }}</span>
        <span class="entry-result" :class="entry.result === 'NOERROR' ? 'text-success' : 'text-danger'">
          {{ entry.result }}
        </span>
      </div>
    </div>

    <!-- Loading / error states -->
    <div v-if="proxyStore.loading && proxyStore.currentHttpLogs.length === 0 && proxyStore.currentDnsLogs.length === 0" class="loading-state">
      Loading...
    </div>
    <div v-if="proxyStore.error" class="error-state">
      {{ proxyStore.error }}
    </div>
  </div>
</template>

<script setup>
import { computed, watch, onUnmounted } from 'vue'
import { useProxyStore } from '@/stores/proxy'
import { useSessionStore } from '@/stores/session'
import { useUIStore } from '@/stores/ui'

const proxyStore = useProxyStore()
const sessionStore = useSessionStore()
const uiStore = useUIStore()

const isVisible = computed(() => uiStore.rightSidebarActiveTab === 'proxy')
const sessionId = computed(() => sessionStore.currentSessionId)

// Start/stop polling based on visibility and session
watch([isVisible, sessionId], ([visible, sid]) => {
  proxyStore.stopPolling()
  if (visible && sid && sessionStore.currentSession?.docker_proxy_enabled) {
    proxyStore.loadAllLogs(sid)
    proxyStore.startPolling(sid)
  }
}, { immediate: true })

onUnmounted(() => {
  proxyStore.stopPolling()
})

function formatTime(ts) {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

function httpStatusClass(status) {
  if (!status) return ''
  if (status >= 200 && status < 300) return 'http-2xx'
  if (status >= 400 && status < 500) return 'http-4xx'
  if (status >= 500) return 'http-5xx'
  return ''
}
</script>

<style scoped>
.proxy-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.proxy-sub-tabs {
  flex-shrink: 0;
}

.proxy-sub-tabs .btn-sm {
  font-size: 11px;
  padding: 2px 8px;
}

.proxy-log-list {
  flex: 1;
  overflow-y: auto;
  font-family: monospace;
  font-size: 12px;
}

.empty-state {
  padding: 16px;
  color: #94a3b8;
  text-align: center;
  font-size: 12px;
}

.proxy-entry {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  border-bottom: 1px solid #f1f5f9;
  min-width: 0;
  line-height: 1.4;
}

.proxy-entry:hover {
  background: #f8fafc;
}

.entry-status {
  flex-shrink: 0;
  font-weight: bold;
  width: 14px;
  text-align: center;
}

.status-allowed {
  color: #16a34a;
}

.status-blocked {
  color: #dc2626;
}

.entry-method {
  flex-shrink: 0;
  width: 36px;
  color: #6366f1;
  font-weight: 600;
  font-size: 11px;
}

.entry-http-status {
  flex-shrink: 0;
  width: 28px;
  font-size: 11px;
}

.http-2xx {
  color: #16a34a;
}

.http-4xx {
  color: #d97706;
}

.http-5xx {
  color: #dc2626;
}

.entry-host {
  flex-shrink: 0;
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: #334155;
}

.entry-path {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: #64748b;
  min-width: 0;
}

.entry-time {
  flex-shrink: 0;
  color: #94a3b8;
  font-size: 11px;
}

.entry-query-type {
  flex-shrink: 0;
  width: 36px;
  color: #6366f1;
  font-weight: 600;
  font-size: 11px;
}

.entry-hostname {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: #334155;
  min-width: 0;
}

.entry-result {
  flex-shrink: 0;
  font-size: 11px;
}

.loading-state {
  padding: 16px;
  color: #94a3b8;
  text-align: center;
  font-size: 12px;
}

.error-state {
  padding: 8px 16px;
  color: #dc2626;
  font-size: 12px;
}
</style>
