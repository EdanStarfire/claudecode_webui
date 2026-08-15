<template>
  <div class="msg-wrapper msg-system">
    <div
      class="msg-pill"
      :class="pillClass"
      @click="toggleExpand"
    >
      <!-- Agent Spawned -->
      <template v-if="subtype === 'task_started'">
        <i class="bi bi-play-circle-fill pill-icon task-icon-started"></i>
        <span class="pill-text">{{ displayContent }}</span>
        <span v-if="taskTypeLabel" class="pill-badge pill-badge-muted">{{ taskTypeLabel }}</span>
      </template>

      <!-- Agent Progress -->
      <template v-else-if="subtype === 'task_progress'">
        <i class="bi bi-fast-forward-fill pill-icon task-icon-progress"></i>
        <span class="pill-text">{{ displayContent }}</span>
        <span v-if="lastToolName" class="pill-badge pill-badge-muted">{{ lastToolName }}</span>
      </template>

      <!-- Agent Completed/Failed/Stopped -->
      <template v-else-if="subtype === 'task_notification'">
        <i class="bi pill-icon" :class="notificationIconClass"></i>
        <span class="pill-text">{{ displayContent }}</span>
      </template>

      <!-- API Retry Progress -->
      <template v-else-if="isApiRetry">
        <i class="bi bi-arrow-clockwise pill-icon retry-icon"></i>
        <span class="pill-text">Retrying API connection...</span>
        <span v-if="retryBadge" class="pill-badge pill-badge-retry">{{ retryBadge }}</span>
      </template>

      <!-- Session Failed -->
      <template v-else-if="isSessionFailed">
        <span class="pill-icon">&#x26A0;&#xFE0F;</span>
        <span class="pill-text">{{ props.message.content }}</span>
      </template>

      <!-- Default system messages -->
      <template v-else>
        <span v-if="isStderr" class="pill-icon">&#x26A0;&#xFE0F;</span>
        <span v-else-if="isCompactionStatus" class="pill-icon">&#x1F5DC;&#xFE0F;</span>
        <span v-else-if="isHook" class="pill-icon">&#x2699;&#xFE0F;</span>
        <span class="pill-text">{{ displayContent || fallbackLabel }}</span>
      </template>

      <!-- Cross-session badge -->
      <span v-if="isCrossSession" class="pill-badge pill-badge-session" :title="taskSessionId">
        {{ truncatedSessionId }}
      </span>

      <span class="pill-time">{{ formattedTimestamp }}</span>
      <span class="pill-chevron" :class="{ 'chevron-open': expanded }">&#x25B6;</span>
    </div>
    <div v-if="isHook && expanded" class="hook-detail">
      <pre class="hook-json">{{ hookJson }}</pre>
    </div>
    <div v-if="isSessionFailed && sessionFailedDetails" class="session-failed-toggle">
      <button class="details-toggle-btn" @click.stop="toggleExpand">
        {{ expanded ? 'Hide details' : 'Show details' }}
      </button>
    </div>
    <div v-if="isSessionFailed && expanded && sessionFailedDetails" class="session-failed-detail">
      <pre class="error-detail-pre">{{ sessionFailedDetails }}</pre>
    </div>
    <div v-if="isGenericDetail && expanded" class="raw-detail">
      <pre class="raw-json">{{ rawJson }}</pre>
    </div>
    <div v-if="resultErrors.length" class="result-errors">
      <div v-for="(err, i) in resultErrors" :key="i" class="result-error-item">
        {{ err }}
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { formatTimestamp } from '@/utils/time'
import { useSessionStore } from '@/stores/session'

const props = defineProps({
  message: {
    type: Object,
    required: true
  }
})

const sessionStore = useSessionStore()
const expanded = ref(false)

const formattedTimestamp = computed(() => {
  return formatTimestamp(props.message.timestamp)
})

const subtype = computed(() => props.message.metadata?.subtype)

// Task-specific computed properties
const taskType = computed(() => props.message.metadata?.task_type)
const taskTypeLabel = computed(() => {
  const typeMap = {
    'in_process_teammate': 'teammate',
    'subprocess': 'subprocess',
  }
  return typeMap[taskType.value] || taskType.value
})
const lastToolName = computed(() => props.message.metadata?.last_tool_name)
const taskStatus = computed(() => props.message.metadata?.status)
const taskSessionId = computed(() => props.message.metadata?.task_session_id)

// Cross-session detection
const isCrossSession = computed(() => {
  if (!taskSessionId.value) return false
  const currentSession = sessionStore.sessions.get(sessionStore.currentSessionId)
  const currentCcSessionId = currentSession?.claude_code_session_id
  if (!currentCcSessionId) return false
  return taskSessionId.value !== currentCcSessionId
})

const truncatedSessionId = computed(() => {
  if (!taskSessionId.value) return ''
  return taskSessionId.value.length > 12
    ? taskSessionId.value.substring(0, 12) + '...'
    : taskSessionId.value
})

// Task notification icon
const notificationIconClass = computed(() => {
  switch (taskStatus.value) {
    case 'completed': return 'bi-check-circle-fill task-icon-completed'
    case 'failed': return 'bi-x-circle-fill task-icon-failed'
    case 'stopped': return 'bi-dash-circle-fill task-icon-stopped'
    default: return 'bi-info-circle-fill task-icon-progress'
  }
})

// Issue #894: API retry computed properties
const isApiRetry = computed(() => subtype.value === 'api_retry')

const retryBadge = computed(() => {
  if (!isApiRetry.value) return ''
  const attempt = props.message.metadata?.attempt
  const maxRetries = props.message.metadata?.max_retries
  const waitSec = props.message.metadata?.wait_sec
  const parts = []
  if (attempt && maxRetries) parts.push(`${attempt}/${maxRetries}`)
  else if (attempt) parts.push(`attempt ${attempt}`)
  if (waitSec) parts.push(`~${waitSec}s`)
  return parts.join(' · ')
})

// Pill class computation
const pillClass = computed(() => ({
  'pill-compaction': isCompactionStatus.value,
  'pill-stderr': isStderr.value,
  'pill-session-failed': isSessionFailed.value,
  'pill-hook': isHook.value && !isHookError.value,
  'pill-hook-error': isHookError.value,
  'pill-expandable': true,
  'pill-retry': isApiRetry.value,
  'pill-task-started': subtype.value === 'task_started',
  'pill-task-progress': subtype.value === 'task_progress',
  'pill-task-completed': subtype.value === 'task_notification' && taskStatus.value === 'completed',
  'pill-task-failed': subtype.value === 'task_notification' && taskStatus.value === 'failed',
  'pill-task-stopped': subtype.value === 'task_notification' && taskStatus.value === 'stopped',
}))

// Check if this is a compaction status message
const isCompactionStatus = computed(() => {
  return subtype.value === 'status' &&
         props.message.metadata?.init_data?.status === 'compacting'
})

// Check if this is a plain stderr message
const isStderr = computed(() => subtype.value === 'stderr')

// Check if this is a session_failed message
const isSessionFailed = computed(() => subtype.value === 'session_failed')

// Full error detail for session_failed expand section
const sessionFailedDetails = computed(() => props.message.metadata?.error_details || null)

// Check if this is a hook message (hook_started or hook_response)
const isHook = computed(() => {
  const st = subtype.value
  return st === 'hook_started' || st === 'hook_response'
})

// Check if this is a hook error (non-zero exit code)
const isHookError = computed(() => {
  if (!isHook.value) return false
  if (subtype.value !== 'hook_response') return false
  const exitCode = props.message.metadata?.exit_code ?? props.message.metadata?.init_data?.exit_code ?? props.message.metadata?.init_data?.exitCode
  return exitCode !== undefined && exitCode !== null && exitCode !== 0
})

const displayContent = computed(() => {
  if (isCompactionStatus.value) {
    return 'Context compaction in progress...'
  }
  const content = props.message.content || ''
  // Allow longer display for stderr messages since they often contain important error details
  const maxLen = isStderr.value ? 200 : 80
  if (content.length > maxLen) {
    return content.substring(0, maxLen - 3) + '...'
  }
  return content
})

// Raw hook data for expanded view
const hookJson = computed(() => {
  const data = props.message.metadata?.init_data
  if (!data) return '{}'
  try {
    return JSON.stringify(data, null, 2)
  } catch {
    return String(data)
  }
})

// ResultMessage turn-level errors
const resultErrors = computed(() => {
  if (props.message.type !== 'result') return []
  return props.message.metadata?.errors || []
})

// Issue #1746 (stage: layout) follow-up: plain "status" pings and similar generic system
// messages sometimes carry no content at all (known pre-existing gap: loadMessages()'s
// history replay doesn't always populate these the way the realtime stream does — tracked
// separately). Rather than hiding the row or leaving it blank, fall back to "type: subtype"
// so there's still something to anchor to.
const fallbackLabel = computed(() => {
  const type = props.message.type || 'system'
  return subtype.value ? `${type}: ${subtype.value}` : type
})

// Generic raw-JSON detail for subtypes without a dedicated detail view.
// session_failed messages with no error_details fall through here too,
// so the chevron/expand affordance never opens onto an empty panel.
const isGenericDetail = computed(() => {
  if (isHook.value) return false
  if (isSessionFailed.value) return !sessionFailedDetails.value
  return true
})

const rawJson = computed(() => {
  const data = props.message.metadata
  if (!data || Object.keys(data).length === 0) return '{}'
  try {
    return JSON.stringify(data, null, 2)
  } catch {
    return String(data)
  }
})

function toggleExpand() {
  expanded.value = !expanded.value
}
</script>

<style scoped>
.msg-wrapper {
  padding: 8px 16px;
}

.msg-system {
  display: flex;
  flex-direction: column;
  align-items: stretch;
}

/* Issue #1746 (stage: layout) follow-up: reskinned as a muted divider row, bordered
   top and bottom (not a single line cutting left-to-right through the text) — matching
   the "what's happening at this stage" phase-divider look from the reference mockup. */
.msg-pill {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 16px;
  margin: 0 -16px;
  border-top: 1px solid var(--bs-border-color);
  border-bottom: 1px solid var(--bs-border-color);
}

.pill-time {
  margin-left: auto;
}

.pill-expandable {
  cursor: pointer;
  user-select: none;
}

.pill-expandable:hover .pill-text {
  color: var(--bs-body-color);
}

.pill-hook .pill-text {
  color: #1e40af;
}

.pill-hook-error .pill-text {
  color: #991b1b;
}

.pill-stderr .pill-text {
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
  color: #991b1b;
  white-space: normal;
  word-break: break-word;
}

.pill-icon {
  font-size: 12px;
}

/* Task icon colors */
.task-icon-started {
  color: #16a34a;
}

.task-icon-progress {
  color: #2563eb;
}

.task-icon-completed {
  color: #16a34a;
}

.task-icon-failed {
  color: #dc2626;
}

.task-icon-stopped {
  color: #ca8a04;
}

.pill-text {
  font-size: 12px;
  color: var(--bs-secondary-color);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.pill-time {
  font-size: 11px;
  color: var(--bs-secondary-color);
  white-space: nowrap;
}

.pill-badge {
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 10px;
  white-space: nowrap;
}

.pill-badge-muted {
  background: #e2e8f0;
  color: #64748b;
}

.pill-badge-session {
  background: #dbeafe;
  color: #1e40af;
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
}

.pill-badge-retry {
  background: #fef9c3;
  color: #713f12;
}

.pill-retry .pill-text {
  color: #713f12;
}

.retry-icon {
  color: #ca8a04;
  animation: spin 1.2s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.pill-chevron {
  font-size: 8px;
  color: var(--bs-secondary-color);
  transition: transform 0.15s ease;
  display: inline-block;
  flex-shrink: 0;
}

.chevron-open {
  transform: rotate(90deg);
}

.result-errors {
  margin-top: 4px;
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.result-error-item {
  background: var(--tool-error-bg, #fef2f2);
  border: 1px solid var(--tool-error-border, #fecaca);
  border-radius: 6px;
  padding: 4px 10px;
  font-size: 12px;
  color: #991b1b;
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
  word-break: break-word;
  white-space: pre-wrap;
}

.hook-detail {
  margin-top: 4px;
  width: 100%;
}

.hook-json {
  background: #1e293b;
  color: #e2e8f0;
  border: 1px solid #334155;
  border-radius: 8px;
  padding: 10px 14px;
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
  font-size: 11px;
  line-height: 1.5;
  overflow-x: auto;
  white-space: pre;
  margin: 0;
  max-height: 400px;
  overflow-y: auto;
}

.raw-detail {
  margin-top: 4px;
  width: 100%;
}

.raw-json {
  background: #1e293b;
  color: #cbd5e1;
  border: 1px solid #334155;
  border-radius: 8px;
  padding: 10px 14px;
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
  font-size: 11px;
  line-height: 1.5;
  overflow-x: auto;
  white-space: pre;
  margin: 0;
  max-height: 400px;
  overflow-y: auto;
}

.pill-session-failed .pill-text {
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
  color: #991b1b;
  white-space: normal;
  word-break: break-word;
}

.session-failed-toggle {
  margin-top: 4px;
}

.details-toggle-btn {
  background: none;
  border: none;
  color: #991b1b;
  font-size: 11px;
  cursor: pointer;
  padding: 0;
  text-decoration: underline;
}

.details-toggle-btn:hover {
  color: #7f1d1d;
}

.session-failed-detail {
  margin-top: 4px;
  width: 100%;
}

.error-detail-pre {
  background: #1e293b;
  color: #fca5a5;
  border: 1px solid #7f1d1d;
  border-radius: 8px;
  padding: 10px 14px;
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
  font-size: 11px;
  line-height: 1.5;
  overflow-x: auto;
  white-space: pre;
  margin: 0;
  max-height: 400px;
  overflow-y: auto;
}
</style>
