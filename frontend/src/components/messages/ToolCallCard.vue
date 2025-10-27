<template>
  <div
    class="tool-call-card card mb-3"
    :class="cardClass"
  >
    <!-- Card Header (always visible) -->
    <div
      class="card-header d-flex align-items-center justify-content-between cursor-pointer"
      @click="toggleExpansion"
    >
      <div class="d-flex align-items-center gap-2 flex-grow-1">
        <span class="tool-status-icon">{{ statusIcon }}</span>
        <strong class="tool-name">{{ toolCall.name }}</strong>
        <span class="text-muted small">{{ statusText }}</span>
      </div>
      <div class="d-flex align-items-center gap-2">
        <small class="text-muted">{{ formattedTimestamp }}</small>
        <i class="bi" :class="toolCall.isExpanded ? 'bi-chevron-up' : 'bi-chevron-down'"></i>
      </div>
    </div>

    <!-- Card Body (collapsible) -->
    <div v-if="toolCall.isExpanded" class="card-body">
      <!-- Orphaned Tool Banner (if applicable) -->
      <div v-if="isOrphaned" class="alert alert-warning mb-3">
        <div class="d-flex align-items-center">
          <i class="bi bi-x-circle me-2" style="font-size: 1.2rem;"></i>
          <div>
            <strong>Tool Execution Cancelled</strong>
            <p class="mb-0 small">{{ orphanedInfo?.message || 'Session was terminated' }}</p>
          </div>
        </div>
      </div>

      <!-- Tool-specific content (using specialized handler component) -->
      <component :is="toolHandlerComponent" :toolCall="toolCall" />

      <!-- Permission Prompt (if applicable and not orphaned) -->
      <div v-if="toolCall.status === 'permission_required' && !isOrphaned" class="permission-prompt mt-3">
        <div class="alert alert-warning mb-3">
          <p class="mb-2"><strong>🔐 Permission Required</strong></p>
          <p class="mb-0">Claude wants to use the <code class="tool-name">{{ toolCall.name }}</code> tool.</p>
        </div>

        <!-- Suggestions (if any) -->
        <div v-if="hasSuggestions" class="suggestions-section mb-3">
          <div class="alert alert-info mb-0">
            <h6 class="mb-2">
              <i class="bi bi-lightbulb me-2"></i>
              Suggested Permission Update
            </h6>
            <p class="mb-2 small">Claude suggests updating your permissions:</p>
            <div class="suggestion-details p-2 bg-white rounded">
              <div v-for="(suggestion, index) in toolCall.suggestions" :key="index" class="suggestion-item">
                <code>{{ formatSuggestion(suggestion) }}</code>
              </div>
            </div>
            <small class="text-muted mt-2 d-block">
              This will prevent future permission prompts for this action.
            </small>
          </div>
        </div>

        <!-- Permission Buttons -->
        <div class="permission-buttons d-flex gap-2 mb-3">
          <!-- Approve & Apply (if suggestions exist) -->
          <button
            v-if="hasSuggestions"
            class="btn btn-success"
            @click="handlePermissionDecision('allow', true)"
            :disabled="isSubmittingPermission"
          >
            <i class="bi bi-check-circle me-1"></i>
            {{ isSubmittingPermission && permissionAction === 'approve-apply' ? '⏳ Submitting...' : '✅ Approve & Apply' }}
          </button>

          <!-- Approve Only (if suggestions exist) or regular Approve -->
          <button
            class="btn"
            :class="hasSuggestions ? 'btn-outline-success' : 'btn-success'"
            @click="handlePermissionDecision('allow', false)"
            :disabled="isSubmittingPermission"
          >
            <i class="bi bi-check-circle me-1"></i>
            {{ isSubmittingPermission && permissionAction === 'approve' ? '⏳ Submitting...' : (hasSuggestions ? '✅ Approve Only' : '✅ Approve') }}
          </button>

          <!-- Deny -->
          <button
            class="btn btn-danger"
            @click="handlePermissionDecision('deny', false)"
            :disabled="isSubmittingPermission"
          >
            <i class="bi bi-x-circle me-1"></i>
            {{ isSubmittingPermission && permissionAction === 'deny' ? '⏳ Submitting...' : '❌ Deny' }}
          </button>
        </div>

        <!-- Provide Guidance -->
        <div class="guidance-section mt-3">
          <label class="form-label fw-bold">
            <i class="bi bi-chat-left-text me-1"></i>
            Provide Guidance (Optional)
          </label>
          <p class="text-muted small mb-2">
            Provide guidance to help Claude retry with better context. If provided, Claude will continue with your guidance instead of stopping.
          </p>
          <textarea
            v-model="guidanceMessage"
            class="form-control mb-2"
            rows="3"
            placeholder="e.g., 'Try using a different approach...' or 'Make sure to check the file exists first...'"
            :disabled="isSubmittingPermission"
          ></textarea>
          <button
            v-if="guidanceMessage.trim()"
            class="btn btn-warning btn-sm"
            @click="handlePermissionDecision('deny', false, guidanceMessage)"
            :disabled="isSubmittingPermission"
          >
            <i class="bi bi-arrow-repeat me-1"></i>
            {{ isSubmittingPermission && permissionAction === 'deny-guidance' ? '⏳ Submitting...' : '💡 Provide Guidance & Continue' }}
          </button>
        </div>
      </div>

      <!-- Orphaned Permission Message (when permission was pending but session ended) -->
      <div v-if="toolCall.status === 'permission_required' && isOrphaned" class="mt-3">
        <div class="alert alert-warning mb-0">
          <div class="d-flex align-items-center">
            <i class="bi bi-shield-x me-2" style="font-size: 1.2rem;"></i>
            <div>
              <strong>Permission Request Cancelled</strong>
              <p class="mb-0 small">{{ orphanedInfo?.message || 'Session was terminated before permission could be granted' }}</p>
            </div>
          </div>
        </div>
      </div>

      <!-- Permission Decision (if denied) -->
      <div v-if="toolCall.permissionDecision === 'deny'" class="tool-section mt-3">
        <div class="alert alert-danger mb-0">
          <i class="bi bi-x-circle"></i>
          Permission denied
          <span v-if="toolCall.result?.message"> - {{ toolCall.result.message }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { useMessageStore } from '@/stores/message'
import { useSessionStore } from '@/stores/session'
import { useWebSocketStore } from '@/stores/websocket'
import { formatTimestamp } from '@/utils/time'
import BaseToolHandler from '@/components/tools/BaseToolHandler.vue'
import ReadToolHandler from '@/components/tools/ReadToolHandler.vue'
import WriteToolHandler from '@/components/tools/WriteToolHandler.vue'
import BashToolHandler from '@/components/tools/BashToolHandler.vue'
import TodoToolHandler from '@/components/tools/TodoToolHandler.vue'
import EditToolHandler from '@/components/tools/EditToolHandler.vue'
import SearchToolHandler from '@/components/tools/SearchToolHandler.vue'
import WebToolHandler from '@/components/tools/WebToolHandler.vue'
import TaskToolHandler from '@/components/tools/TaskToolHandler.vue'
import NotebookEditToolHandler from '@/components/tools/NotebookEditToolHandler.vue'
import ShellToolHandler from '@/components/tools/ShellToolHandler.vue'
import CommandToolHandler from '@/components/tools/CommandToolHandler.vue'
import SkillToolHandler from '@/components/tools/SkillToolHandler.vue'
import ExitPlanModeToolHandler from '@/components/tools/ExitPlanModeToolHandler.vue'

const props = defineProps({
  toolCall: {
    type: Object,
    required: true
  }
})

const messageStore = useMessageStore()
const sessionStore = useSessionStore()
const wsStore = useWebSocketStore()

// Permission handling state
const isSubmittingPermission = ref(false)
const permissionAction = ref(null)
const guidanceMessage = ref('')

// Orphaned tool detection
const isOrphaned = computed(() => {
  return messageStore.isToolUseOrphaned(sessionStore.currentSessionId, props.toolCall.id)
})

const orphanedInfo = computed(() => {
  return messageStore.getOrphanedInfo(sessionStore.currentSessionId, props.toolCall.id)
})

// Tool handler component registry
const toolHandlers = {
  'Read': ReadToolHandler,
  'Write': WriteToolHandler,
  'Bash': BashToolHandler,
  'TodoWrite': TodoToolHandler,
  'Edit': EditToolHandler,
  'Grep': SearchToolHandler,
  'Glob': SearchToolHandler,
  'WebFetch': WebToolHandler,
  'WebSearch': WebToolHandler,
  'Task': TaskToolHandler,
  'NotebookEdit': NotebookEditToolHandler,
  'BashOutput': ShellToolHandler,
  'KillShell': ShellToolHandler,
  'SlashCommand': CommandToolHandler,
  'Skill': SkillToolHandler,
  'ExitPlanMode': ExitPlanModeToolHandler,
}

// Select appropriate handler component based on tool name
const toolHandlerComponent = computed(() => {
  const toolName = props.toolCall.name

  // Check for specialized handler
  if (toolHandlers[toolName]) {
    return toolHandlers[toolName]
  }

  // Default to BaseToolHandler
  return BaseToolHandler
})

// Computed properties
const cardClass = computed(() => {
  const classes = []

  switch (props.toolCall.status) {
    case 'pending':
      classes.push('border-secondary')
      break
    case 'permission_required':
      classes.push('border-warning')
      break
    case 'executing':
      classes.push('border-primary')
      break
    case 'completed':
      if (isError.value) {
        classes.push('border-danger')
      } else if (props.toolCall.permissionDecision === 'deny') {
        classes.push('border-danger')
      } else {
        classes.push('border-success')
      }
      break
    case 'error':
      classes.push('border-danger')
      break
  }

  return classes.join(' ')
})

const statusIcon = computed(() => {
  switch (props.toolCall.status) {
    case 'pending':
      return '🔄'
    case 'permission_required':
      return '❓'
    case 'executing':
      return '⚡'
    case 'completed':
      if (props.toolCall.permissionDecision === 'deny') return '❌'
      return isError.value ? '💥' : '✅'
    case 'error':
      return '💥'
    default:
      return '🔧'
  }
})

const statusText = computed(() => {
  switch (props.toolCall.status) {
    case 'pending':
      return 'Pending'
    case 'permission_required':
      return 'Awaiting Permission'
    case 'executing':
      return 'Executing...'
    case 'completed':
      if (props.toolCall.permissionDecision === 'deny') return 'Denied'
      return isError.value ? 'Error' : 'Completed'
    case 'error':
      return 'Error'
    default:
      return 'Unknown'
  }
})

const formattedTimestamp = computed(() => {
  return formatTimestamp(props.toolCall.timestamp)
})

const isError = computed(() => {
  return props.toolCall.result?.error || props.toolCall.status === 'error'
})

const hasSuggestions = computed(() => {
  return props.toolCall.suggestions && props.toolCall.suggestions.length > 0
})

function toggleExpansion() {
  const sessionId = sessionStore.currentSessionId
  if (sessionId) {
    messageStore.toggleToolExpansion(sessionId, props.toolCall.id)
  }
}

function formatSuggestion(suggestion) {
  if (suggestion.type === 'setMode') {
    return `Set permission mode to "${suggestion.mode}"`
  } else if (suggestion.type === 'allow') {
    return `Allow tool: ${suggestion.tool}`
  }
  return JSON.stringify(suggestion)
}

async function handlePermissionDecision(decision, applySuggestions, guidance = null) {
  if (isSubmittingPermission.value) return

  isSubmittingPermission.value = true

  if (decision === 'allow') {
    permissionAction.value = applySuggestions ? 'approve-apply' : 'approve'
  } else if (guidance) {
    permissionAction.value = 'deny-guidance'
  } else {
    permissionAction.value = 'deny'
  }

  try {
    // Immediately update the toolCall locally with permission decision (don't wait for backend)
    // This matches vanilla JS behavior from static/app.js:1930-1945
    const sessionId = sessionStore.currentSessionId
    if (sessionId) {
      const appliedUpdates = applySuggestions && props.toolCall.suggestions ? props.toolCall.suggestions : []

      messageStore.handlePermissionResponse(sessionId, {
        request_id: props.toolCall.permissionRequestId,
        decision: decision,
        reasoning: decision === 'allow' ? 'User allowed permission' : 'User denied permission',
        applied_updates: appliedUpdates
      })
    }

    // Send apply_suggestions flag to backend
    await wsStore.sendPermissionResponse(
      props.toolCall.permissionRequestId,
      decision,
      applySuggestions,  // Boolean flag, not the suggestions array
      guidance
    )
  } catch (error) {
    console.error('Failed to send permission response:', error)
  } finally {
    isSubmittingPermission.value = false
    permissionAction.value = null
  }
}
</script>

<style scoped>
.tool-call-card {
  border-width: 2px;
  transition: all 0.2s ease;
}

.tool-call-card .card-header {
  background-color: rgba(0, 0, 0, 0.02);
  user-select: none;
}

.tool-call-card .card-header:hover {
  background-color: rgba(0, 0, 0, 0.05);
}

.cursor-pointer {
  cursor: pointer;
}

.tool-status-icon {
  font-size: 1.2rem;
  line-height: 1;
}

.tool-name {
  color: #0d6efd;
  font-family: 'Courier New', monospace;
  font-size: 0.95rem;
}

/* Border colors based on status */
.border-secondary {
  border-color: #6c757d !important;
}

.border-warning {
  border-color: #ffc107 !important;
}

.border-primary {
  border-color: #0d6efd !important;
}

.border-success {
  border-color: #198754 !important;
}

.border-danger {
  border-color: #dc3545 !important;
}

/* Permission prompt styles */
.permission-prompt .tool-name {
  padding: 0.25rem 0.5rem;
  background: #fff3cd;
  border-radius: 0.25rem;
  font-size: 0.95rem;
  font-weight: 600;
  color: #856404;
}

.suggestion-item {
  padding: 0.25rem;
  font-size: 0.9rem;
}

.suggestion-item code {
  padding: 0.25rem 0.5rem;
  background: #f8f9fa;
  border-radius: 0.25rem;
  font-size: 0.85rem;
}

.guidance-section textarea {
  resize: vertical;
  min-height: 80px;
  font-family: inherit;
}

.guidance-section .form-label {
  margin-bottom: 0.5rem;
}
</style>
