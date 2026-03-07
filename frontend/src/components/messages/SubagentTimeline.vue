<template>
  <div class="subagent-timeline-bubble" :class="statusClass">
    <div class="subagent-header" @click="collapsed = !collapsed">
      <span class="subagent-icon">&#x1F916;</span>
      <span v-if="subagentType" class="subagent-type-badge">{{ subagentType }}</span>
      <span class="subagent-description">{{ taskDescription }}</span>
      <span class="subagent-status-badge" :class="statusBadgeClass">{{ statusLabel }}</span>
      <span class="subagent-toggle">{{ collapsed ? '\u25B6' : '\u25BC' }}</span>
    </div>

    <!-- Issue #689: Live activity bar from task lifecycle messages -->
    <div v-if="!collapsed && taskActivity" class="subagent-activity-bar">
      <span v-if="taskActivity.subtype === 'task_notification'" class="activity-icon activity-done">&#x2714;</span>
      <span v-else class="activity-spinner"></span>
      <span class="activity-description">{{ activityDescription }}</span>
      <span v-if="taskActivity.last_tool_name" class="activity-tool-badge">{{ taskActivity.last_tool_name }}</span>
      <span class="activity-timestamp">{{ activityTime }}</span>
    </div>

    <div v-if="!collapsed" class="subagent-body">
      <!-- Child tools timeline -->
      <ActivityTimeline
        v-if="childTools.length > 0"
        :tools="childTools"
        :messageId="taskToolCall.id"
      />

      <!-- Empty state: subagent starting -->
      <div v-else-if="isRunning" class="subagent-placeholder">
        <span class="placeholder-spinner"></span>
        Subagent starting...
      </div>

      <!-- Empty state: completed with no child tools -->
      <div v-else-if="isCompleted" class="subagent-placeholder subagent-placeholder-done">
        No tool activity recorded
      </div>

      <!-- Task result summary -->
      <div v-if="hasResult && resultSummary" class="subagent-result" :class="{ 'subagent-result-error': isError }">
        <div class="result-label">
          {{ isError ? 'Error:' : 'Result:' }}
          <a v-if="isResultTruncated" class="view-full-link" @click.stop="openFullResult">View Full</a>
        </div>
        <pre class="result-content">{{ resultSummary }}</pre>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useMessageStore } from '@/stores/message'
import { useSessionStore } from '@/stores/session'
import { useResourceStore } from '@/stores/resource'
import { getEffectiveStatusForTool } from '@/composables/useToolStatus'
import { getRelativeTime } from '@/utils/time'
import ActivityTimeline from './tools/ActivityTimeline.vue'

const props = defineProps({
  taskToolCall: {
    type: Object,
    required: true
  }
})

const messageStore = useMessageStore()
const sessionStore = useSessionStore()
const resourceStore = useResourceStore()
const collapsed = ref(false)

// Extract Task tool metadata
const subagentType = computed(() => {
  return props.taskToolCall.input?.subagent_type || null
})

const taskDescription = computed(() => {
  const desc = props.taskToolCall.input?.description
  if (desc) return desc.length > 120 ? desc.slice(0, 120) + '...' : desc
  const prompt = props.taskToolCall.input?.prompt
  if (prompt) return prompt.length > 120 ? prompt.slice(0, 120) + '...' : prompt
  return 'Subagent task'
})

// Issue #689: Task activity from message store
const taskActivity = computed(() => messageStore.getTaskActivity(props.taskToolCall.id))

const activityDescription = computed(() => {
  if (!taskActivity.value) return ''
  const desc = taskActivity.value.description || ''
  return desc.length > 80 ? desc.slice(0, 80) + '...' : desc
})

const activityTime = computed(() => {
  if (!taskActivity.value?.timestamp) return ''
  return getRelativeTime(taskActivity.value.timestamp)
})

// Status computation — augmented by task activity (Issue #689 Step 4)
const effectiveStatus = computed(() => {
  const toolStatus = getEffectiveStatusForTool(props.taskToolCall)
  // If task_notification arrived with completed status before tool result, show completed early
  if (taskActivity.value?.subtype === 'task_notification' && taskActivity.value?.status === 'completed') {
    if (['pending', 'executing'].includes(toolStatus)) {
      return 'completed'
    }
  }
  return toolStatus
})

const isRunning = computed(() => {
  return ['pending', 'executing'].includes(effectiveStatus.value)
})

const isCompleted = computed(() => {
  return ['completed', 'orphaned'].includes(effectiveStatus.value)
})

const statusLabel = computed(() => {
  const map = {
    'pending': 'starting',
    'executing': 'running',
    'permission_required': 'awaiting permission',
    'completed': 'completed',
    'error': 'error',
    'orphaned': 'interrupted'
  }
  return map[effectiveStatus.value] || effectiveStatus.value
})

const statusClass = computed(() => {
  return `subagent-${effectiveStatus.value}`
})

const statusBadgeClass = computed(() => {
  return `badge-${effectiveStatus.value}`
})

// Child tools: all tool calls with parent_tool_use_id matching this Task
const childTools = computed(() => {
  const sessionId = sessionStore.currentSessionId
  if (!sessionId) return []

  const allToolCalls = messageStore.toolCallsBySession.get(sessionId) || []
  return allToolCalls.filter(tc => tc.parent_tool_use_id === props.taskToolCall.id)
})

// Result
const hasResult = computed(() => {
  return props.taskToolCall.result != null
})

const isError = computed(() => {
  return props.taskToolCall.result?.error === true
})

const fullResultContent = computed(() => {
  if (!hasResult.value) return ''
  const content = props.taskToolCall.result?.content || props.taskToolCall.result?.message || ''
  if (typeof content !== 'string') return JSON.stringify(content, null, 2)
  return content
})

const resultSummary = computed(() => {
  if (!hasResult.value) return null
  const content = fullResultContent.value
  return content.length > 500 ? content.slice(0, 500) + '...' : content
})

const isResultTruncated = computed(() => fullResultContent.value.length > 500)

function openFullResult() {
  resourceStore.openWithDirectContent('Task Result', fullResultContent.value)
}
</script>

<style scoped>
.subagent-timeline-bubble {
  margin-top: 8px;
  border: 1px solid #d8b4fe;
  border-radius: 8px;
  background: #faf5ff;
  overflow: hidden;
}

.subagent-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  background: #f3e8ff;
  cursor: pointer;
  user-select: none;
  flex-wrap: wrap;
}

.subagent-header:hover {
  background: #ede4fb;
}

.subagent-icon {
  font-size: 16px;
  flex-shrink: 0;
}

.subagent-type-badge {
  background: #7c3aed;
  color: white;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 600;
  text-transform: lowercase;
  font-family: 'Courier New', monospace;
  flex-shrink: 0;
}

.subagent-description {
  font-size: 13px;
  color: #4c1d95;
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.subagent-status-badge {
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 600;
  flex-shrink: 0;
}

.badge-pending,
.badge-executing {
  background: #dbeafe;
  color: #1e40af;
}

.badge-completed {
  background: #d1fae5;
  color: #065f46;
}

.badge-error {
  background: #fee2e2;
  color: #991b1b;
}

.badge-orphaned {
  background: #fef3c7;
  color: #92400e;
}

.badge-permission_required {
  background: #fef3c7;
  color: #92400e;
}

.subagent-toggle {
  font-size: 10px;
  color: #7c3aed;
  flex-shrink: 0;
}

/* Issue #689: Activity bar */
.subagent-activity-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  background: #ede9fe;
  border-top: 1px solid #d8b4fe;
  font-size: 12px;
  color: #4c1d95;
  min-height: 30px;
}

.activity-spinner {
  width: 12px;
  height: 12px;
  border: 2px solid #d8b4fe;
  border-top-color: #7c3aed;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
  flex-shrink: 0;
}

.activity-icon.activity-done {
  color: #059669;
  font-size: 14px;
  flex-shrink: 0;
}

.activity-description {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.activity-tool-badge {
  background: #7c3aed;
  color: white;
  padding: 1px 6px;
  border-radius: 3px;
  font-size: 10px;
  font-weight: 600;
  font-family: 'Courier New', monospace;
  flex-shrink: 0;
}

.activity-timestamp {
  color: #7c3aed;
  font-size: 11px;
  flex-shrink: 0;
  opacity: 0.7;
}

.subagent-body {
  padding: 8px 12px;
}

.subagent-placeholder {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px;
  color: #7c3aed;
  font-size: 13px;
  font-style: italic;
}

.subagent-placeholder-done {
  color: #94a3b8;
}

.placeholder-spinner {
  width: 14px;
  height: 14px;
  border: 2px solid #d8b4fe;
  border-top-color: #7c3aed;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.subagent-result {
  margin-top: 8px;
  border: 1px solid #e2e8f0;
  border-radius: 4px;
  overflow: hidden;
}

.subagent-result-error {
  border-color: #f87171;
}

.result-label {
  padding: 4px 8px;
  font-size: 11px;
  font-weight: 600;
  color: #64748b;
  background: #f1f5f9;
  border-bottom: 1px solid #e2e8f0;
}

.subagent-result-error .result-label {
  background: #fee2e2;
  color: #991b1b;
}

.result-content {
  margin: 0;
  padding: 8px;
  font-family: 'Courier New', monospace;
  font-size: 11px;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 200px;
  overflow-y: auto;
  line-height: 1.4;
  color: #334155;
}

.view-full-link {
  float: right;
  font-size: 11px;
  font-weight: 500;
  color: #0d6efd;
  cursor: pointer;
  text-decoration: none;
}

.view-full-link:hover {
  text-decoration: underline;
}

/* Status-specific border colors */
.subagent-executing {
  border-color: #93c5fd;
}

.subagent-completed {
  border-color: #86efac;
}

.subagent-error {
  border-color: #fca5a5;
}

.subagent-orphaned {
  border-color: #fcd34d;
}
</style>
