<template>
  <div class="edit-history-panel">
    <!-- Header -->
    <div class="panel-header">
      <span class="panel-title" title="Bash file changes are listed by command only — no inline diff. See the Diff tab for aggregate git changes.">Edit History</span>
      <div class="header-actions">
        <label class="bash-toggle" :title="showNonModifyingBash ? 'Hiding non-modifying Bash' : 'Showing only likely-modifying Bash'">
          <input type="checkbox" v-model="editHistoryStore.showNonModifyingBash" />
          All Bash
        </label>
        <button class="refresh-btn" @click="refresh" :disabled="isLoading" title="Refresh">
          <span :class="{ spinning: isLoading }">↺</span>
        </button>
      </div>
    </div>

    <!-- Loading state -->
    <div v-if="isLoading && !entries.length" class="state-placeholder">
      <span class="spinner">⟳</span> Loading…
    </div>

    <!-- Error state -->
    <div v-else-if="errorMsg" class="state-error">
      <span>{{ errorMsg }}</span>
      <button class="retry-btn" @click="refresh">Retry</button>
    </div>

    <!-- Empty state -->
    <div v-else-if="!entries.length" class="state-placeholder">
      No file modifications yet.
    </div>

    <!-- Entry list -->
    <div v-else class="entry-list">
      <div
        v-for="entry in entries"
        :key="entry.tool_use_id"
        class="entry-item"
        :class="{ expanded: isExpanded(entry.tool_use_id) }"
      >
        <!-- Entry header row -->
        <div class="entry-row" @click="entry.tool_name === 'Edit' ? openDiff(entry) : toggle(entry)">
          <span class="entry-icon">{{ toolIcon(entry.tool_name) }}</span>

          <span class="entry-label text-truncate" :title="entryLabel(entry)">
            {{ entryLabel(entry) }}
          </span>

          <span v-if="entry.tool_name === 'Write'" class="entry-stats write-lines">
            {{ entry.line_count }} lines
          </span>

          <span class="entry-ts" :title="fullTs(entry.timestamp)">
            {{ relativeTs(entry.timestamp) }}
          </span>

          <span class="entry-status" :title="statusTitle(entry.succeeded)">
            {{ statusIcon(entry.succeeded) }}
          </span>

          <span v-if="entry.tool_name === 'Edit'" class="expand-arrow" title="Open full-screen diff">⤢</span>
          <span v-else class="expand-arrow">{{ isExpanded(entry.tool_use_id) ? '▲' : '▼' }}</span>
        </div>

        <!-- Expanded detail -->
        <div v-if="entry.tool_name !== 'Edit' && isExpanded(entry.tool_use_id)" class="entry-detail">

          <!-- Write: content preview -->
          <template v-if="entry.tool_name === 'Write'">
            <div class="write-preview">
              <div class="write-meta">
                Wrote {{ entry.line_count }} lines to
                <code>{{ entry.file_path }}</code>
                <button class="view-full-btn" @click.stop="viewFullWrite(entry)">View Full</button>
              </div>
              <pre class="write-content">{{ writePreview(entry) }}</pre>
            </div>
          </template>

          <!-- Bash: command block -->
          <template v-else-if="entry.tool_name === 'Bash'">
            <div class="bash-detail">
              <pre class="bash-command">{{ entry.command }}</pre>
              <div class="bash-note">No inline diff — changed via Bash.</div>
            </div>
          </template>

        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, watch } from 'vue'
import { useEditHistoryStore } from '@/stores/editHistory'
import { useSessionStore } from '@/stores/session'
import { useResourceStore } from '@/stores/resource'
import { useDiffStore } from '@/stores/diff'
import { buildEditDiff } from '@/utils/diffRender'
import { getRelativeTime, formatFullTimestamp } from '@/utils/time'

const editHistoryStore = useEditHistoryStore()
const sessionStore = useSessionStore()
const resourceStore = useResourceStore()
const diffStore = useDiffStore()

const sessionId = computed(() => sessionStore.currentSessionId)

const sessionData = computed(() => editHistoryStore.entriesBySession.get(sessionId.value) || {})
const isLoading = computed(() => sessionData.value.loading || false)
const errorMsg = computed(() => sessionData.value.error || null)
const entries = computed(() => editHistoryStore.visibleEntries(sessionId.value))

function refresh() {
  if (sessionId.value) editHistoryStore.refreshHistory(sessionId.value)
}

function isExpanded(toolUseId) {
  return editHistoryStore.isExpanded(sessionId.value, toolUseId)
}

function toggle(entry) {
  if (sessionId.value) editHistoryStore.toggleExpand(sessionId.value, entry.tool_use_id)
}

function openDiff(entry) {
  if (!sessionId.value) return
  const { added, removed, text } = diffFor(entry)
  diffStore.openFullViewForEdit(sessionId.value, entry.file_path, text, {
    toolUseId: entry.tool_use_id,
    added,
    removed,
    succeeded: entry.succeeded
  })
}

// Lazy diff computation per entry (only when expanded)
const diffCache = new Map()
function diffFor(entry) {
  const key = entry.tool_use_id
  if (!diffCache.has(key)) {
    const inp = entry.input || {}
    diffCache.set(key, buildEditDiff(entry.file_path, inp.old_string, inp.new_string))
  }
  return diffCache.get(key)
}

function toolIcon(toolName) {
  switch (toolName) {
    case 'Edit': return '✏️'
    case 'Write': return '📝'
    case 'Bash': return '⚡'
    default: return '🔧'
  }
}

function entryLabel(entry) {
  if (entry.tool_name === 'Bash') {
    const cmd = entry.command || ''
    return cmd.length > 60 ? cmd.slice(0, 60) + '…' : cmd
  }
  const fp = entry.file_path || ''
  return fp.split('/').pop() || fp
}

function relativeTs(ts) {
  if (!ts) return ''
  return getRelativeTime(ts)
}

function fullTs(ts) {
  if (!ts) return ''
  return formatFullTimestamp(ts)
}

function statusIcon(succeeded) {
  if (succeeded === true) return '✓'
  if (succeeded === false) return '✗'
  return '…'
}

function statusTitle(succeeded) {
  if (succeeded === true) return 'Succeeded'
  if (succeeded === false) return 'Failed'
  return 'Pending'
}

function writePreview(entry) {
  const content = (entry.input || {}).content || ''
  const lines = content.split('\n')
  const preview = lines.slice(0, 100).join('\n')
  return lines.length > 100 ? preview + '\n…' : preview
}

function viewFullWrite(entry) {
  const fileName = (entry.file_path || 'file').split('/').pop()
  const content = (entry.input || {}).content || ''
  resourceStore.openWithDirectContent(`Write: ${fileName}`, content)
}

// Auto-load when session changes
watch(
  sessionId,
  (newId) => {
    if (newId && !editHistoryStore.entriesBySession.get(newId)) {
      editHistoryStore.loadHistory(newId)
    }
  },
  { immediate: true }
)
</script>

<style scoped>
.edit-history-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  font-size: 12px;
  overflow: hidden;
}

.panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 10px;
  border-bottom: 1px solid var(--bs-border-color);
  flex-shrink: 0;
}

.panel-title {
  font-weight: 600;
  color: var(--bs-emphasis-color);
  cursor: help;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.bash-toggle {
  display: flex;
  align-items: center;
  gap: 4px;
  cursor: pointer;
  color: var(--bs-secondary-color);
  font-size: 11px;
  user-select: none;
}

.refresh-btn {
  background: none;
  border: none;
  cursor: pointer;
  color: var(--bs-secondary-color);
  font-size: 16px;
  padding: 0 2px;
  line-height: 1;
}

.refresh-btn:hover { color: var(--bs-body-color); }
.refresh-btn:disabled { opacity: 0.4; cursor: not-allowed; }

.spinning {
  display: inline-block;
  animation: spin 1s linear infinite;
}

@keyframes spin { to { transform: rotate(360deg); } }

.state-placeholder,
.state-error {
  padding: 24px 16px;
  text-align: center;
  color: var(--bs-tertiary-color);
  font-size: 12px;
}

.state-error { color: var(--bs-danger-text-emphasis, #dc3545); }

.retry-btn {
  display: block;
  margin: 8px auto 0;
  padding: 4px 12px;
  background: var(--bs-tertiary-bg);
  border: 1px solid var(--bs-border-color);
  border-radius: 4px;
  cursor: pointer;
  color: var(--bs-body-color);
  font-size: 11px;
}

.entry-list {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
}

.entry-item {
  border-bottom: 1px solid var(--bs-border-color);
}

.entry-row {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  cursor: pointer;
  user-select: none;
}

.entry-row:hover {
  background: var(--bs-tertiary-bg);
}

.entry-icon {
  font-size: 13px;
  flex-shrink: 0;
}

.entry-label {
  flex: 1;
  min-width: 0;
  font-family: 'Courier New', monospace;
  font-size: 11px;
  color: var(--bs-body-color);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.entry-stats {
  flex-shrink: 0;
  font-size: 10px;
  font-family: 'Courier New', monospace;
}

.write-lines {
  color: var(--bs-secondary-color);
}

.entry-ts {
  flex-shrink: 0;
  color: var(--bs-tertiary-color);
  font-size: 10px;
  white-space: nowrap;
}

.entry-status {
  flex-shrink: 0;
  font-size: 11px;
  width: 14px;
  text-align: center;
}

.expand-arrow {
  flex-shrink: 0;
  color: var(--bs-tertiary-color);
  font-size: 9px;
}

/* Entry detail sections */
.entry-detail {
  padding: 0 8px 8px;
}

/* Write preview */
.write-preview {
  border: 1px solid var(--bs-border-color);
  border-radius: 4px;
  overflow: hidden;
}

.write-meta {
  background: var(--bs-tertiary-bg);
  padding: 4px 8px;
  font-size: 11px;
  color: var(--bs-secondary-color);
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.write-meta code {
  font-family: 'Courier New', monospace;
  color: var(--bs-link-color);
  font-size: 10px;
}

.view-full-btn {
  margin-left: auto;
  padding: 2px 8px;
  background: var(--bs-secondary-bg);
  border: 1px solid var(--bs-border-color);
  border-radius: 4px;
  cursor: pointer;
  font-size: 10px;
  color: var(--bs-link-color);
}

.view-full-btn:hover { background: var(--bs-tertiary-bg); }

.write-content {
  margin: 0;
  padding: 6px 8px;
  font-family: 'Courier New', monospace;
  font-size: 10px;
  color: var(--bs-body-color);
  background: var(--bs-secondary-bg);
  max-height: 200px;
  overflow: auto;
  white-space: pre;
}

/* Bash detail */
.bash-detail {
  background: var(--bs-tertiary-bg);
  border-radius: 4px;
  overflow: hidden;
}

.bash-command {
  margin: 0;
  padding: 8px 10px;
  font-family: 'Courier New', monospace;
  font-size: 11px;
  color: var(--bs-body-color);
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 160px;
  overflow: auto;
}

.bash-note {
  padding: 4px 10px;
  font-size: 10px;
  color: var(--bs-secondary-color);
  border-top: 1px solid var(--bs-border-color);
}
</style>
