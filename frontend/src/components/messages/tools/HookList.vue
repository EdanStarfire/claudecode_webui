<template>
  <div class="hook-list">
    <div class="hook-list-header">
      <span class="hook-icon">⚙</span>
      <span class="hook-list-title">Hooks ({{ hooks.length }})</span>
      <span v-if="failureCount > 0" class="hook-failure-count">· {{ failureCount }} failed</span>
    </div>
    <div class="hook-rows">
      <div
        v-for="hook in hooks"
        :key="hook.started_message_id || hook.hook_id"
        class="hook-row"
        :class="hook.status"
      >
        <div class="hook-row-header" @click="toggleExpand(hook.started_message_id || hook.hook_id)">
          <span class="hook-status-chip" :class="'chip-' + hook.status">
            {{ statusLabel(hook.status) }}
          </span>
          <span class="hook-name">{{ hook.hook_name || hook.hook_id || '(unknown)' }}</span>
          <span class="hook-event-badge">{{ hook.hook_event }}</span>
          <span class="hook-chevron" :class="{ expanded: expandedId === (hook.started_message_id || hook.hook_id) }">›</span>
        </div>
        <div
          v-if="expandedId === (hook.started_message_id || hook.hook_id)"
          class="hook-row-body"
        >
          <div v-if="hook.exit_code != null" class="hook-meta-line">
            <span class="hook-meta-label">Exit code:</span>
            <code>{{ hook.exit_code }}</code>
          </div>
          <div v-if="hook.status === 'pending'" class="hook-meta-line hook-pending-note">
            Started at {{ formatTs(hook.started_ts) }} — no response received yet
          </div>
          <div v-if="hook.started_ts && hook.completed_ts" class="hook-meta-line">
            <span class="hook-meta-label">Duration:</span>
            {{ durationMs(hook.started_ts, hook.completed_ts) }} ms
          </div>
          <div v-if="hook.stdout" class="hook-output-section">
            <div class="hook-meta-label">stdout</div>
            <pre class="hook-output-pre">{{ truncate(hook.stdout) }}</pre>
            <button v-if="hook.stdout.length > TRUNCATE_LIMIT" class="hook-show-full" @click.stop="showFull('stdout', hook)">
              Show full output ({{ hook.stdout.length }} bytes)
            </button>
          </div>
          <div v-if="hook.stderr" class="hook-output-section">
            <div class="hook-meta-label">stderr</div>
            <pre class="hook-output-pre hook-stderr">{{ truncate(hook.stderr) }}</pre>
            <button v-if="hook.stderr.length > TRUNCATE_LIMIT" class="hook-show-full" @click.stop="showFull('stderr', hook)">
              Show full output ({{ hook.stderr.length }} bytes)
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { useResourceStore } from '@/stores/resource'

const props = defineProps({
  hooks: {
    type: Array,
    required: true,
  },
})

const TRUNCATE_LIMIT = 2048

const expandedId = ref(null)

const failureCount = computed(() => props.hooks.filter(h => h.status === 'failure').length)

function toggleExpand(id) {
  expandedId.value = expandedId.value === id ? null : id
}

function statusLabel(status) {
  if (status === 'success') return '✓'
  if (status === 'failure') return '✗'
  return '…'
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

function showFull(field, hook) {
  const resourceStore = useResourceStore()
  const content = field === 'stdout' ? hook.stdout : hook.stderr
  const title = `${hook.hook_name || hook.hook_id} — ${field}`
  resourceStore.openWithDirectContent(title, content)
}
</script>

<style scoped>
.hook-list {
  margin-top: 8px;
  border: 1px solid var(--bs-border-color);
  border-radius: 4px;
  overflow: hidden;
  font-size: 12px;
}

.hook-list-header {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 5px 8px;
  background: var(--bs-tertiary-bg);
  border-bottom: 1px solid var(--bs-border-color);
  font-weight: 600;
  color: var(--bs-secondary-color);
}

.hook-icon {
  font-size: 11px;
}

.hook-list-title {
  font-size: 11px;
}

.hook-failure-count {
  color: var(--hook-failure-text);
  font-size: 11px;
}

.hook-rows {
  display: flex;
  flex-direction: column;
}

.hook-row {
  border-bottom: 1px solid var(--bs-border-color);
}

.hook-row:last-child {
  border-bottom: none;
}

.hook-row-header {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 8px;
  cursor: pointer;
  user-select: none;
}

.hook-row-header:hover {
  background: var(--bs-tertiary-bg);
}

.hook-status-chip {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  border-radius: 3px;
  font-size: 11px;
  font-weight: 700;
  flex-shrink: 0;
}

.chip-success {
  background: var(--hook-badge-success-bg);
  color: var(--hook-badge-text);
}

.chip-failure {
  background: var(--hook-badge-failure-bg);
  color: var(--hook-badge-text);
}

.chip-pending {
  background: var(--hook-badge-pending-bg);
  color: var(--hook-badge-text);
}

.hook-name {
  flex: 1;
  font-family: monospace;
  font-size: 11px;
  color: var(--bs-body-color);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.hook-event-badge {
  font-size: 10px;
  padding: 1px 5px;
  border-radius: 2px;
  background: rgba(59, 130, 246, 0.15);
  color: var(--bs-link-color);
  font-weight: 600;
  flex-shrink: 0;
}

.hook-chevron {
  color: var(--bs-secondary-color);
  font-size: 14px;
  transition: transform 0.15s;
  display: inline-block;
  flex-shrink: 0;
}

.hook-chevron.expanded {
  transform: rotate(90deg);
}

.hook-row-body {
  padding: 6px 8px 8px 8px;
  background: var(--bs-secondary-bg);
  border-top: 1px solid var(--bs-border-color);
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.hook-meta-line {
  display: flex;
  align-items: baseline;
  gap: 6px;
  font-size: 11px;
  color: var(--bs-body-color);
}

.hook-meta-label {
  font-weight: 600;
  color: var(--bs-secondary-color);
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.02em;
  flex-shrink: 0;
}

.hook-pending-note {
  color: var(--hook-pending-text);
  font-style: italic;
}

.hook-output-section {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.hook-output-pre {
  margin: 0;
  padding: 4px 6px;
  font-family: 'Courier New', monospace;
  font-size: 10px;
  background: var(--bs-tertiary-bg);
  border: 1px solid var(--bs-border-color);
  border-radius: 3px;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 120px;
  overflow-y: auto;
  color: var(--bs-body-color);
}

.hook-stderr {
  color: var(--hook-failure-text);
}

.hook-show-full {
  align-self: flex-start;
  background: none;
  border: none;
  color: var(--bs-link-color);
  font-size: 10px;
  cursor: pointer;
  padding: 0;
  text-decoration: underline;
}

.hook-show-full:hover {
  opacity: 0.8;
}
</style>
