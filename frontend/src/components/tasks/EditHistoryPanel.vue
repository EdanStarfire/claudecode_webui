<template>
  <div class="edit-history-panel">
    <!-- Header -->
    <div class="panel-header">
      <span class="panel-title">Edit History</span>
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

    <!-- Gap warning banner -->
    <div class="gap-warning">
      Bash file changes listed by command only — no inline diff.
      See the <strong>Diff</strong> tab for aggregate git changes.
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
        <div class="entry-row" @click="toggle(entry)">
          <span class="entry-icon">{{ toolIcon(entry.tool_name) }}</span>

          <span class="entry-label text-truncate" :title="entryLabel(entry)">
            {{ entryLabel(entry) }}
          </span>

          <span v-if="entry.tool_name === 'Edit'" class="entry-stats">
            <span class="stat-diff" v-if="isExpanded(entry.tool_use_id)">
              <span class="stat-removed">-{{ diffFor(entry).removed }}</span>
              <span class="stat-added">+{{ diffFor(entry).added }}</span>
            </span>
          </span>
          <span v-else-if="entry.tool_name === 'Write'" class="entry-stats write-lines">
            {{ entry.line_count }} lines
          </span>

          <span class="entry-ts" :title="fullTs(entry.timestamp)">
            {{ relativeTs(entry.timestamp) }}
          </span>

          <span class="entry-status" :title="statusTitle(entry.succeeded)">
            {{ statusIcon(entry.succeeded) }}
          </span>

          <span class="expand-arrow">{{ isExpanded(entry.tool_use_id) ? '▲' : '▼' }}</span>
        </div>

        <!-- Expanded detail -->
        <div v-if="isExpanded(entry.tool_use_id)" class="entry-detail">

          <!-- Edit: inline diff -->
          <template v-if="entry.tool_name === 'Edit'">
            <div class="diff-container">
              <div
                v-for="(line, i) in diffFor(entry).lines"
                :key="i"
                class="diff-line"
                :class="{
                  'diff-line-removed': line.type === 'removed',
                  'diff-line-added': line.type === 'added',
                  'diff-line-context': line.type === 'context',
                  'diff-line-hunk': line.type === 'hunk'
                }"
              >
                <span class="diff-marker">{{
                  line.type === 'removed' ? '-' :
                  line.type === 'added' ? '+' :
                  line.type === 'hunk' ? '@' : ' '
                }}</span>
                <span class="diff-content">{{ line.content }}</span>
              </div>
            </div>
          </template>

          <!-- Write: content preview -->
          <template v-else-if="entry.tool_name === 'Write'">
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
import { buildEditDiff } from '@/utils/diffRender'
import { getRelativeTime, formatFullTimestamp, parseTimestamp } from '@/utils/time'

const editHistoryStore = useEditHistoryStore()
const sessionStore = useSessionStore()
const resourceStore = useResourceStore()

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
  return getRelativeTime(parseTimestamp(ts))
}

function fullTs(ts) {
  if (!ts) return ''
  return formatFullTimestamp(parseTimestamp(ts))
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
  border-bottom: 1px solid #e2e8f0;
  flex-shrink: 0;
}

.panel-title {
  font-weight: 600;
  color: #334155;
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
  color: #64748b;
  font-size: 11px;
  user-select: none;
}

.refresh-btn {
  background: none;
  border: none;
  cursor: pointer;
  color: #64748b;
  font-size: 16px;
  padding: 0 2px;
  line-height: 1;
}

.refresh-btn:hover { color: #334155; }
.refresh-btn:disabled { opacity: 0.4; cursor: not-allowed; }

.spinning {
  display: inline-block;
  animation: spin 1s linear infinite;
}

@keyframes spin { to { transform: rotate(360deg); } }

.gap-warning {
  background: #fef9c3;
  border-bottom: 1px solid #fde68a;
  padding: 5px 10px;
  font-size: 11px;
  color: #92400e;
  flex-shrink: 0;
}

.state-placeholder,
.state-error {
  padding: 24px 16px;
  text-align: center;
  color: #94a3b8;
  font-size: 12px;
}

.state-error { color: #dc3545; }

.retry-btn {
  display: block;
  margin: 8px auto 0;
  padding: 4px 12px;
  background: #f1f5f9;
  border: 1px solid #e2e8f0;
  border-radius: 4px;
  cursor: pointer;
  font-size: 11px;
}

.entry-list {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
}

.entry-item {
  border-bottom: 1px solid #f1f5f9;
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
  background: #f8fafc;
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
  color: #334155;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.entry-stats {
  flex-shrink: 0;
  font-size: 10px;
  font-family: 'Courier New', monospace;
}

.stat-removed { color: #dc3545; }
.stat-added { color: #198754; }
.stat-diff { display: flex; gap: 4px; }

.write-lines {
  color: #64748b;
}

.entry-ts {
  flex-shrink: 0;
  color: #94a3b8;
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
  color: #94a3b8;
  font-size: 9px;
}

/* Entry detail sections */
.entry-detail {
  padding: 0 8px 8px;
}

/* Diff (Edit) */
.diff-container {
  font-family: 'Courier New', monospace;
  font-size: 11px;
  border: 1px solid #e2e8f0;
  border-radius: 4px;
  overflow: auto;
  max-height: 240px;
}

.diff-line {
  display: flex;
  white-space: nowrap;
  line-height: 1.4;
}

.diff-line-removed { background: #ffebe9; color: #24292f; }
.diff-line-removed .diff-marker { background: #ffcecb; color: #dc3545; }
.diff-line-added { background: #dafbe1; color: #24292f; }
.diff-line-added .diff-marker { background: #abf2bc; color: #198754; }
.diff-line-context { background: #fff; color: #57606a; }
.diff-line-context .diff-marker { background: #f6f8fa; color: #57606a; }
.diff-line-hunk { background: #f6f8fa; color: #0969da; font-weight: 600; }
.diff-line-hunk .diff-marker { background: #e1e4e8; color: #0969da; }

.diff-marker {
  width: 1.6rem;
  flex-shrink: 0;
  text-align: center;
  font-weight: 700;
  user-select: none;
}

.diff-content {
  padding: 0 6px;
  white-space: pre;
}

/* Write preview */
.write-preview {
  border: 1px solid #e2e8f0;
  border-radius: 4px;
  overflow: hidden;
}

.write-meta {
  background: #f1f5f9;
  padding: 4px 8px;
  font-size: 11px;
  color: #64748b;
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.write-meta code {
  font-family: 'Courier New', monospace;
  color: #0d6efd;
  font-size: 10px;
}

.view-full-btn {
  margin-left: auto;
  padding: 2px 8px;
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 4px;
  cursor: pointer;
  font-size: 10px;
  color: #3b82f6;
}

.view-full-btn:hover { background: #f0f9ff; }

.write-content {
  margin: 0;
  padding: 6px 8px;
  font-family: 'Courier New', monospace;
  font-size: 10px;
  color: #334155;
  background: #fff;
  max-height: 200px;
  overflow: auto;
  white-space: pre;
}

/* Bash detail */
.bash-detail {
  background: #1e1e2e;
  border-radius: 4px;
  overflow: hidden;
}

.bash-command {
  margin: 0;
  padding: 8px 10px;
  font-family: 'Courier New', monospace;
  font-size: 11px;
  color: #cdd6f4;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 160px;
  overflow: auto;
}

.bash-note {
  padding: 4px 10px;
  font-size: 10px;
  color: #6c7086;
  border-top: 1px solid #313244;
}
</style>
