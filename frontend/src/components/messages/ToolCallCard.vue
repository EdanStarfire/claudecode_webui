<template>
  <div
    class="tool-call-card card"
    :class="cardClass"
  >
    <!-- Card Header (always visible) -->
    <div
      class="card-header d-flex align-items-center justify-content-between cursor-pointer"
      @click="toggleExpansion"
    >
      <div class="d-flex align-items-center gap-2 flex-grow-1 min-width-0">
        <span class="tool-status-icon">{{ statusIcon }}</span>
        <strong class="tool-name" :title="toolSummaryTooltip">{{ toolSummary }}</strong>
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
      <div v-if="toolCall.permissionDecision === 'deny'" class="tool-section">
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
import SlashCommandToolHandler from '@/components/tools/SlashCommandToolHandler.vue'
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
  'SlashCommand': SlashCommandToolHandler,
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

const formattedTimestamp = computed(() => {
  return formatTimestamp(props.toolCall.timestamp)
})

const isError = computed(() => {
  return props.toolCall.result?.error || props.toolCall.status === 'error'
})

const hasSuggestions = computed(() => {
  return props.toolCall.suggestions && props.toolCall.suggestions.length > 0
})

// ========== Tool Summary Generators ==========

// Helper: Extract basename from file path
function getBasename(path) {
  if (!path) return ''
  // Handle both forward and backslashes
  const parts = path.replace(/\\/g, '/').split('/')
  return parts[parts.length - 1]
}

// Helper: Truncate bash command to max length
function truncateBashCommand(cmd, maxLen = 200) {
  if (!cmd) return ''
  if (cmd.length <= maxLen) return cmd
  return cmd.substring(0, maxLen) + '...'
}

// Helper: Extract exit code from bash result
function getExitCode(result) {
  if (!result) return null
  // Check for exit code in result content or message
  if (result.content) {
    const match = result.content.match(/exit code (\d+)/i)
    if (match) return parseInt(match[1])
  }
  // Default: success = 0, error = 1
  return result.error ? 1 : 0
}

// Helper: Count lines in diff
function countDiffLines(oldStr, newStr) {
  const oldLines = oldStr ? oldStr.split('\n').length : 0
  const newLines = newStr ? newStr.split('\n').length : 0
  return { added: newLines, removed: oldLines }
}

// Tool summary computed property
const toolSummary = computed(() => {
  const toolName = props.toolCall.name
  const input = props.toolCall.input || {}
  const result = props.toolCall.result
  const status = props.toolCall.status

  switch (toolName) {
    case 'Bash':
    case 'Shell':
    case 'Command': {
      const cmd = truncateBashCommand(input.command, 200)
      if (status === 'completed' && result) {
        const exitCode = getExitCode(result)
        return `Bash: ${cmd} (exit ${exitCode})`
      }
      return `Bash: ${cmd}`
    }

    case 'SlashCommand': {
      const cmd = truncateBashCommand(input.command, 200)
      if (status === 'completed' && result) {
        return `SlashCommand: ${cmd}`
      }
      return `SlashCommand: ${cmd}`
    }

    case 'Edit': {
      const filename = getBasename(input.file_path)
      if (status === 'completed' && result && !result.error) {
        const { added, removed } = countDiffLines(input.old_string, input.new_string)
        if (added === removed) {
          return `Edit: ${filename} (${added} lines changed)`
        }
        return `Edit: ${filename} (${added} lines added, ${removed} removed)`
      }
      return `Edit: ${filename}`
    }

    case 'Read': {
      const filename = getBasename(input.file_path)
      const hasRange = input.offset !== undefined || input.limit !== undefined
      if (hasRange) {
        const start = (input.offset || 0) + 1
        const end = input.limit ? (input.offset || 0) + input.limit : '∞'
        return `Read: ${filename} (lines ${start}-${end})`
      }
      return `Read: ${filename} (full file)`
    }

    case 'Write': {
      const filename = getBasename(input.file_path)
      if (status === 'completed' && result && !result.error && input.content) {
        const lineCount = input.content.split('\n').length
        return `Write: ${filename} (${lineCount} lines written)`
      }
      return `Write: ${filename}`
    }

    case 'Skill': {
      const skillName = input.command || 'Unknown'
      if (status === 'completed' && result) {
        return result.error ? `Skill: ${skillName} (error)` : `Skill: ${skillName} (completed)`
      }
      return `Skill: ${skillName}`
    }

    case 'Task': {
      const agentType = input.subagent_type || 'general'
      const description = input.description || 'Task'
      if (status === 'completed' && result) {
        return result.error ? `Task: (${agentType}) ${description} (error)` : `Task: (${agentType}) ${description} (completed)`
      }
      return `Task: (${agentType}) ${description}`
    }

    case 'TodoWrite': {
      if (input.todos && Array.isArray(input.todos)) {
        const completed = input.todos.filter(t => t.status === 'completed').length
        const total = input.todos.length
        const inProgress = input.todos.find(t => t.status === 'in_progress')
        if (inProgress) {
          return `Todo: Working on "${inProgress.content}"`
        }
        return `Todo: ${completed}/${total} Tasks Completed`
      }
      return 'Todo: Updated'
    }

    case 'Grep':
    case 'Glob': {
      const pattern = input.pattern || ''
      const toolType = toolName === 'Grep' ? 'Grep' : 'Glob'
      if (status === 'completed' && result && !result.error) {
        // Count results - result content varies by output_mode
        let resultCount = 0
        if (result.content) {
          const lines = result.content.split('\n').filter(l => l.trim())
          resultCount = lines.length
        }
        return `${toolType}: "${pattern}" (${resultCount} results)`
      }
      return `${toolType}: "${pattern}"`
    }

    case 'WebFetch':
    case 'WebSearch': {
      const url = input.url || input.query || ''
      let domain = ''
      try {
        if (url) {
          const parsedUrl = new URL(url.startsWith('http') ? url : `https://${url}`)
          domain = parsedUrl.hostname
        }
      } catch (e) {
        // If URL parsing fails, use truncated URL
        domain = url.length > 50 ? url.substring(0, 50) + '...' : url
      }
      if (status === 'completed' && result) {
        return result.error ? `Web: ${domain} (error)` : `Web: ${domain} (success)`
      }
      return `Web: ${domain || url}`
    }

    case 'NotebookEdit': {
      const filename = getBasename(input.notebook_path)
      const cellNum = input.cell_number !== undefined ? input.cell_number : '?'
      const mode = input.edit_mode || 'replace'
      return `NotebookEdit: ${filename} cell #${cellNum} (${mode})`
    }

    case 'ExitPlanMode': {
      return 'ExitPlanMode: Plan submitted'
    }

    case 'BashOutput': {
      const bashId = input.bash_id || 'unknown'
      return `BashOutput: Read output from ${bashId}`
    }

    case 'KillShell': {
      const shellId = input.shell_id || 'unknown'
      return `KillShell: Terminate ${shellId}`
    }

    default: {
      return `${toolName}: executed`
    }
  }
})

// Tooltip with full details
const toolSummaryTooltip = computed(() => {
  const toolName = props.toolCall.name
  const input = props.toolCall.input || {}

  // For bash commands, show full command in tooltip (no truncation)
  if (['Bash', 'Shell', 'Command', 'SlashCommand'].includes(toolName)) {
    return input.command || toolName
  }

  // For file operations, show full path
  if (['Edit', 'Read', 'Write', 'NotebookEdit'].includes(toolName)) {
    return input.file_path || input.notebook_path || toolName
  }

  // For other tools, show tool name
  return toolName
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
  padding: 0.2rem 0.5rem;
}

.tool-call-card .card-header:hover {
  background-color: rgba(0, 0, 0, 0.05);
}

.tool-call-card .card-body {
  padding: 0.2rem;
}

.tool-section {
  padding-bottom: 0.2rem;
}

.cursor-pointer {
  cursor: pointer;
}

.tool-status-icon {
  font-size: 1.0rem;
  line-height: 1;
}

.tool-name {
  color: #0d6efd;
  font-family: 'Courier New', monospace;
  font-size: 0.95rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 100%;
  flex-shrink: 1;
  min-width: 0;
}

.min-width-0 {
  min-width: 0;
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
