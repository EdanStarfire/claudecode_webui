<template>
  <div class="hook-pill-strip" :class="'align-' + align">
    <div
      v-for="hook in hooks"
      :key="hook.started_message_id || hook.hook_id"
      class="hook-pill"
      :class="hook.status"
      @click="toggleExpand(hook.started_message_id || hook.hook_id)"
    >
      <span class="pill-icon">⚙</span>
      <span class="pill-name">{{ hook.hook_name || hook.hook_event || '(hook)' }}</span>
      <span class="pill-status-dot" :class="'dot-' + hook.status"></span>

      <transition name="pill-expand">
        <div
          v-if="expandedId === (hook.started_message_id || hook.hook_id)"
          class="pill-detail"
          @click.stop
        >
          <div class="pill-detail-row">
            <span class="pill-detail-label">Event:</span>
            <span>{{ hook.hook_event }}</span>
          </div>
          <div v-if="hook.exit_code != null" class="pill-detail-row">
            <span class="pill-detail-label">Exit:</span>
            <code>{{ hook.exit_code }}</code>
          </div>
          <div v-if="hook.status === 'pending'" class="pill-detail-row pill-pending-note">
            Started at {{ formatTs(hook.started_ts) }} — no response yet
          </div>
          <div v-if="hook.started_ts && hook.completed_ts" class="pill-detail-row">
            <span class="pill-detail-label">Duration:</span>
            <span>{{ durationMs(hook.started_ts, hook.completed_ts) }} ms</span>
          </div>
          <div v-if="hook.stdout" class="pill-detail-output">
            <div class="pill-detail-label">stdout</div>
            <pre class="pill-output-pre">{{ truncate(hook.stdout) }}</pre>
          </div>
          <div v-if="hook.stderr" class="pill-detail-output">
            <div class="pill-detail-label">stderr</div>
            <pre class="pill-output-pre pill-stderr">{{ truncate(hook.stderr) }}</pre>
          </div>
        </div>
      </transition>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'

defineProps({
  hooks: {
    type: Array,
    required: true,
  },
  align: {
    type: String,
    default: 'left',
    validator: v => ['left', 'center', 'right'].includes(v),
  },
})

const TRUNCATE_LIMIT = 2048
const expandedId = ref(null)

function toggleExpand(id) {
  expandedId.value = expandedId.value === id ? null : id
}

function truncate(text) {
  if (!text || text.length <= TRUNCATE_LIMIT) return text
  return text.slice(0, TRUNCATE_LIMIT)
}

function durationMs(startedTs, completedTs) {
  if (startedTs == null || completedTs == null) return '?'
  return Math.round((completedTs - startedTs) * 1000)
}

function formatTs(ts) {
  if (!ts) return '?'
  return new Date(ts * 1000).toLocaleTimeString()
}
</script>

<style scoped>
.hook-pill-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 4px;
}

.align-left {
  justify-content: flex-start;
}

.align-center {
  justify-content: center;
}

.align-right {
  justify-content: flex-end;
}

.hook-pill {
  position: relative;
  display: flex;
  flex-direction: column;
  cursor: pointer;
  border-radius: 4px;
  border: 1px solid var(--bs-border-color);
  background: var(--bs-secondary-bg);
  overflow: hidden;
  font-size: 11px;
  transition: border-color 0.15s;
}

.hook-pill.success {
  border-color: var(--hook-success-border);
  background: var(--hook-success-bg);
}

.hook-pill.failure {
  border-color: var(--hook-failure-border);
  background: var(--hook-failure-bg);
}

.hook-pill.pending {
  border-color: var(--hook-pending-border);
  background: var(--hook-pending-bg);
}

/* Pill header row — always visible */
.hook-pill > .pill-icon,
.hook-pill > .pill-name,
.hook-pill > .pill-status-dot {
  /* these are siblings; lay them out via flex on the pill itself for the header */
}

/* Use a pseudo-row to keep the inline header tight */
.hook-pill {
  flex-direction: row;
  flex-wrap: wrap;
  align-items: center;
  padding: 2px 6px;
  gap: 4px;
  min-width: 0;
}

.pill-icon {
  flex-shrink: 0;
  opacity: 0.7;
}

.pill-name {
  font-family: monospace;
  font-size: 11px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 160px;
  color: var(--bs-body-color);
}

.hook-pill.success .pill-name { color: var(--hook-success-text); }
.hook-pill.failure .pill-name { color: var(--hook-failure-text); }
.hook-pill.pending .pill-name { color: var(--hook-pending-text); }

.pill-status-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex-shrink: 0;
}

.dot-success { background: var(--hook-badge-success-bg); }
.dot-failure { background: var(--hook-badge-failure-bg); }
.dot-pending { background: var(--hook-badge-pending-bg); }

/* Expanded detail — renders below the pill header as a full-width block */
.pill-detail {
  width: 100%;
  padding: 5px 6px 5px 6px;
  margin-top: 4px;
  border-top: 1px solid var(--bs-border-color);
  display: flex;
  flex-direction: column;
  gap: 3px;
  font-size: 11px;
}

.pill-detail-row {
  display: flex;
  align-items: baseline;
  gap: 5px;
}

.pill-detail-label {
  font-weight: 600;
  color: var(--bs-secondary-color);
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.02em;
  flex-shrink: 0;
}

.pill-pending-note {
  color: var(--hook-pending-text);
  font-style: italic;
}

.pill-detail-output {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.pill-output-pre {
  margin: 0;
  padding: 3px 5px;
  font-family: 'Courier New', monospace;
  font-size: 10px;
  background: var(--bs-tertiary-bg);
  border: 1px solid var(--bs-border-color);
  border-radius: 3px;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 100px;
  overflow-y: auto;
  color: var(--bs-body-color);
}

.pill-stderr {
  color: var(--hook-failure-text);
}

/* Expand animation */
.pill-expand-enter-active,
.pill-expand-leave-active {
  transition: opacity 0.15s;
}

.pill-expand-enter-from,
.pill-expand-leave-to {
  opacity: 0;
}
</style>
