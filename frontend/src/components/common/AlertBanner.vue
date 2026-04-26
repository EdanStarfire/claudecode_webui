<template>
  <div v-if="alerts.length > 0" class="alert-banner-stack">
    <div
      v-for="alert in alerts"
      :key="alert.id"
      class="alert-banner"
      :class="alert.watchdog === 'idle' ? 'alert-idle' : 'alert-error-rate'"
    >
      <div class="alert-body">
        <div class="alert-title">
          <span class="alert-icon">{{ alert.watchdog === 'idle' ? '⏱' : '⚠' }}</span>
          <strong>{{ alertTitle(alert) }}</strong>
        </div>
        <div class="alert-detail">{{ alertDetail(alert) }}</div>
        <router-link
          v-if="alert.session_id"
          :to="`/session/${alert.session_id}`"
          class="alert-link"
          @click="dismiss(alert.id)"
        >Open session</router-link>
      </div>
      <button class="alert-dismiss" @click="dismiss(alert.id)" title="Dismiss">×</button>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useUIStore } from '@/stores/ui'

const uiStore = useUIStore()
const alerts = computed(() => uiStore.watchdogAlerts)

function dismiss(id) {
  uiStore.dismissAlert(id)
}

function alertTitle(alert) {
  const name = alert.session_name || alert.session_id || 'Session'
  if (alert.watchdog === 'idle') {
    return `Idle: ${name}`
  }
  return `Error rate: ${name}`
}

function alertDetail(alert) {
  if (alert.watchdog === 'idle') {
    const secs = alert.details?.idle_seconds
    if (secs == null) return 'Session has been idle'
    const mins = Math.floor(secs / 60)
    return mins > 0
      ? `No activity for ${mins}m ${Math.floor(secs % 60)}s`
      : `No activity for ${Math.floor(secs)}s`
  }
  const rate = alert.details?.error_rate
  const count = alert.details?.tool_error_count
  const total = alert.details?.tool_call_count
  if (rate != null) {
    return `${count}/${total} recent tool calls failed (${Math.round(rate * 100)}%)`
  }
  return 'High tool failure rate detected'
}
</script>

<style scoped>
.alert-banner-stack {
  position: fixed;
  top: 12px;
  right: 16px;
  z-index: 2000;
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-width: 360px;
  pointer-events: none;
}

.alert-banner {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
  padding: 10px 12px;
  border-radius: 6px;
  border: 1px solid transparent;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
  pointer-events: auto;
  background: #fff;
  animation: slide-in 0.2s ease;
}

.alert-idle {
  border-color: #f0ad4e;
  background: #fffbf0;
}

.alert-error-rate {
  border-color: #d9534f;
  background: #fff5f5;
}

.alert-body {
  flex: 1;
  min-width: 0;
}

.alert-title {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 2px;
}

.alert-icon {
  font-size: 14px;
  line-height: 1;
}

.alert-detail {
  font-size: 12px;
  color: #555;
  margin-bottom: 4px;
}

.alert-link {
  font-size: 12px;
  color: #337ab7;
  text-decoration: none;
}

.alert-link:hover {
  text-decoration: underline;
}

.alert-dismiss {
  flex-shrink: 0;
  background: none;
  border: none;
  font-size: 18px;
  line-height: 1;
  cursor: pointer;
  color: #888;
  padding: 0 2px;
}

.alert-dismiss:hover {
  color: #333;
}

@keyframes slide-in {
  from { transform: translateX(20px); opacity: 0; }
  to   { transform: translateX(0);    opacity: 1; }
}
</style>
