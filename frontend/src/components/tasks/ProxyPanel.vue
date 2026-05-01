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
      <button
        class="btn btn-sm"
        :class="proxyStore.activeSubTab === 'socks5' ? 'btn-primary' : 'btn-outline-secondary'"
        @click="proxyStore.setSubTab('socks5')"
      >
        SOCKS5
        <span v-if="proxyStore.currentSocks5Count > 0" class="ms-1 badge bg-secondary">
          {{ proxyStore.currentSocks5Count }}
        </span>
      </button>
      <button
        class="btn btn-sm ms-auto"
        :class="hideAnthropic ? 'btn-primary' : 'btn-outline-secondary'"
        title="Hide Anthropic traffic"
        @click="hideAnthropic = !hideAnthropic"
      >
        Hide Anthropic
      </button>
    </div>

    <!-- HTTP sub-tab -->
    <div v-show="proxyStore.activeSubTab === 'http'" class="proxy-log-list">
      <div v-if="filteredHttpLogs.length === 0" class="empty-state">
        No HTTP requests logged yet
      </div>
      <div
        v-for="entry in [...filteredHttpLogs].reverse()"
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
      </div>
    </div>

    <!-- DNS sub-tab -->
    <div v-show="proxyStore.activeSubTab === 'dns'" class="proxy-log-list">
      <div v-if="filteredDnsLogs.length === 0" class="empty-state">
        No DNS queries logged yet
      </div>
      <div
        v-for="(entry, idx) in filteredDnsLogs"
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

    <!-- SOCKS5 sub-tab -->
    <div v-show="proxyStore.activeSubTab === 'socks5'" class="proxy-log-list">
      <div v-if="proxyStore.currentSocks5Logs.length === 0" class="empty-state">
        No SOCKS5 connections logged yet
      </div>
      <div
        v-for="(entry, idx) in [...proxyStore.currentSocks5Logs].reverse()"
        :key="idx"
        class="proxy-entry socks5-entry"
      >
        <span class="entry-status" :class="entry.allowed ? 'status-allowed' : 'status-blocked'">
          {{ entry.allowed ? '✓' : '✗' }}
        </span>
        <span class="entry-host">{{ entry.host }}</span>
        <span class="entry-port">:{{ entry.port }}</span>
        <span
          v-if="!entry.allowed"
          class="entry-reason"
          :title="entry.reason"
        >{{ abbreviateReason(entry.reason) }}</span>
      </div>
    </div>

    <!-- Loading / error states -->
    <div v-if="proxyStore.loading && proxyStore.currentHttpLogs.length === 0 && proxyStore.currentDnsLogs.length === 0 && proxyStore.currentSocks5Logs.length === 0" class="loading-state">
      Loading...
    </div>
    <div v-if="proxyStore.error" class="error-state">
      {{ proxyStore.error }}
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch, onUnmounted } from 'vue'
import { useProxyStore } from '@/stores/proxy'
import { useSessionStore } from '@/stores/session'
import { useUIStore } from '@/stores/ui'

const proxyStore = useProxyStore()
const sessionStore = useSessionStore()
const uiStore = useUIStore()

const isVisible = computed(() => uiStore.rightSidebarActiveTab === 'proxy')
const sessionId = computed(() => sessionStore.currentSessionId)

// Persist hide-anthropic preference in localStorage (default: true = filter on)
let _initHide = true
try { _initHide = JSON.parse(localStorage.getItem('webui-proxy-hide-anthropic') ?? 'true') } catch {}
const hideAnthropic = ref(_initHide)
watch(hideAnthropic, (val) => { try { localStorage.setItem('webui-proxy-hide-anthropic', JSON.stringify(val)) } catch {} })

function isAnthropicDomain(host) {
  if (!host) return false
  const h = host.toLowerCase()
  return h === 'anthropic.com' || h.endsWith('.anthropic.com')
}

const filteredHttpLogs = computed(() => {
  if (!hideAnthropic.value) return proxyStore.currentHttpLogs
  return proxyStore.currentHttpLogs.filter(e => !isAnthropicDomain(e.host))
})

const filteredDnsLogs = computed(() => {
  if (!hideAnthropic.value) return proxyStore.currentDnsLogs
  return proxyStore.currentDnsLogs.filter(e => !isAnthropicDomain(e.hostname))
})

function isProxyEnabledForCurrent() {
  const session = sessionStore.currentSession
  if (!session) return false
  if (session.config?.docker_proxy_enabled === true) return true
  const ec = sessionStore.effectiveConfigBySession.get(session.session_id)
  return ec?.docker_proxy_enabled === true
}

// Start/stop polling based on visibility and session
watch([isVisible, sessionId], ([visible, sid]) => {
  proxyStore.stopPolling()
  if (visible && sid && isProxyEnabledForCurrent()) {
    proxyStore.loadAllLogs(sid)
    proxyStore.startPolling(sid)
  }
}, { immediate: true })

onUnmounted(() => {
  proxyStore.stopPolling()
})

function httpStatusClass(status) {
  if (!status) return ''
  if (status >= 200 && status < 300) return 'http-2xx'
  if (status >= 400 && status < 500) return 'http-4xx'
  if (status >= 500) return 'http-5xx'
  return ''
}

const REASON_LABELS = {
  not_allowlisted: 'blocked',
  ip_literal: 'ip-literal',
  no_address: 'no-addr',
  empty_address: 'no-addr',
}

function abbreviateReason(reason) {
  return REASON_LABELS[reason] ?? reason
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

.entry-port {
  flex-shrink: 0;
  color: #64748b;
  font-size: 11px;
}

.entry-reason {
  flex-shrink: 0;
  font-size: 10px;
  padding: 1px 5px;
  border-radius: 3px;
  background: #fee2e2;
  color: #dc2626;
  font-weight: 600;
  cursor: default;
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
