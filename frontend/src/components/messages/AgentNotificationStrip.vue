<template>
  <div v-if="notifications.length" class="agent-notification-strip">
    <div
      v-for="notification in notifications"
      :key="notification.id || notification.message"
      class="agent-notification-row"
      :class="rowClass(notification)"
    >
      <span class="notification-icon">{{ icon(notification) }}</span>
      <span class="notification-body">
        <strong v-if="notification.label" class="notification-label">{{ notification.label }}</strong>
        <span class="notification-message">{{ notification.message }}</span>
      </span>
      <button
        v-if="notification.notificationType === 'agent_needs_input'"
        type="button"
        class="notification-reply-btn"
        @click="$emit('reply', notification.label)"
      >
        Reply to agent
      </button>
      <button
        type="button"
        class="notification-dismiss"
        aria-label="Dismiss"
        @click="$emit('dismiss', notification.id)"
      >
        &times;
      </button>
    </div>
  </div>
</template>

<script setup>
defineProps({
  notifications: {
    type: Array,
    default: () => [],
  },
})

defineEmits(['reply', 'dismiss'])

function isFailure(notification) {
  return (notification.message || '').toLowerCase().includes('failed')
}

function rowClass(notification) {
  if (notification.notificationType === 'agent_needs_input') return 'needs-input'
  if (isFailure(notification)) return 'failure'
  return 'success'
}

function icon(notification) {
  if (notification.notificationType === 'agent_needs_input') return '❓'
  return isFailure(notification) ? '❌' : '✅'
}
</script>

<style scoped>
.agent-notification-strip {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin: 4px 16px 8px 16px;
}

.agent-notification-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  border-radius: 6px;
  border: 1px solid;
  font-size: 0.82rem;
}

.agent-notification-row.needs-input {
  background: var(--hook-pending-bg);
  border-color: var(--hook-pending-border);
  color: var(--hook-pending-text);
}

.agent-notification-row.success {
  background: var(--hook-success-bg);
  border-color: var(--hook-success-border);
  color: var(--hook-success-text);
}

.agent-notification-row.failure {
  background: var(--hook-failure-bg);
  border-color: var(--hook-failure-border);
  color: var(--hook-failure-text);
}

.notification-icon {
  flex-shrink: 0;
  font-size: 0.9rem;
}

.notification-body {
  flex: 1;
  min-width: 0;
  display: flex;
  gap: 6px;
  align-items: baseline;
  flex-wrap: wrap;
}

.notification-label {
  font-weight: 600;
}

.notification-message {
  word-break: break-word;
}

.notification-reply-btn {
  flex-shrink: 0;
  background: none;
  border: 1px solid currentColor;
  border-radius: 4px;
  color: inherit;
  font-size: 0.75rem;
  padding: 2px 8px;
  cursor: pointer;
  opacity: 0.85;
}

.notification-reply-btn:hover {
  opacity: 1;
}

.notification-dismiss {
  flex-shrink: 0;
  background: none;
  border: none;
  color: inherit;
  font-size: 1.1rem;
  cursor: pointer;
  padding: 0 2px;
  line-height: 1;
  opacity: 0.6;
}

.notification-dismiss:hover {
  opacity: 1;
}
</style>
