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
        <span class="tool-name" :title="toolSummaryTooltip">{{ toolSummary }}</span>
      </div>
      <div class="d-flex align-items-center gap-2">
        <small class="text-muted">{{ formattedTimestamp }}</small>
        <span :aria-label="toolCall.isExpanded ? 'Collapse' : 'Expand'">{{ toolCall.isExpanded ? '▾' : '▸' }}</span>
      </div>
    </div>

    <!-- Card Body (collapsible) -->
    <div v-if="toolCall.isExpanded" class="card-body">
      <!-- Orphaned Tool Banner (if applicable) -->
      <div v-if="isOrphaned" class="alert alert-warning mb-3">
        <div class="d-flex align-items-center">
          <span class="me-2" style="font-size: 1.2rem;">⏹️</span>
          <div>
            <strong>Tool Execution Cancelled</strong>
            <p class="mb-0 small">{{ orphanedInfo?.message || 'Session was terminated' }}</p>
          </div>
        </div>
      </div>

      <!-- Tool-specific content (using specialized handler component) -->
      <!-- Skip for AskUserQuestion in permission_required state - shown in permission prompt instead -->
      <component
        v-if="!(isAskUserQuestion && effectiveStatus === 'permission_required')"
        :is="toolHandlerComponent"
        :toolCall="toolCall"
      />

      <!-- Permission Prompt (if applicable and not orphaned) -->
      <!-- Issue #310: Use effectiveStatus which prefers backend state -->
      <div v-if="effectiveStatus === 'permission_required' && !isOrphaned" class="permission-prompt mt-3">
        <!-- AskUserQuestion: Show question UI instead of standard permission buttons -->
        <template v-if="isAskUserQuestion">
          <div class="alert alert-info mb-3">
            <p class="mb-0"><strong>❓ Claude is asking for your input</strong></p>
          </div>

          <!-- Question Handler with interactive options -->
          <!-- Issue #412: Pass disabled state to disable inputs during submission -->
          <AskUserQuestionToolHandler
            ref="questionHandlerRef"
            :toolCall="toolCall"
            :disabled="isSubmittingPermission"
            @answer="handleQuestionAnswer"
          />

          <!-- Submit Button -->
          <div class="question-buttons d-flex gap-2 mt-3">
            <button
              class="btn btn-primary"
              @click="submitQuestionAnswers"
              :disabled="isSubmittingPermission || !hasValidAnswers"
            >
              {{ isSubmittingPermission ? '⏳ Submitting...' : '📤 Submit Answers' }}
            </button>
            <button
              class="btn btn-outline-secondary"
              @click="handlePermissionDecision('deny', false)"
              :disabled="isSubmittingPermission"
            >
              Skip Question
            </button>
          </div>
        </template>

        <!-- Standard Permission UI for other tools -->
        <template v-else>
          <div class="alert alert-warning mb-3">
            <p class="mb-2"><strong>🔐 Permission Required</strong></p>
            <p class="mb-0">Claude wants to use the <code class="tool-name">{{ toolCall.name }}</code> tool.</p>
          </div>

          <!-- Suggestions (if any) - shown as individual checkboxes -->
          <div v-if="hasSuggestions" class="suggestions-section mb-3">
            <div class="alert alert-info mb-0">
              <h6 class="mb-2">
                💾 Suggested Permission Updates
              </h6>
              <p class="mb-2 small">Select which permissions to apply:</p>
              <div class="suggestion-checkboxes p-2 bg-white rounded">
                <div
                  v-for="(suggestion, index) in toolCall.suggestions"
                  :key="index"
                  class="form-check suggestion-check-item"
                >
                  <input
                    class="form-check-input"
                    type="checkbox"
                    :id="`suggestion-${toolCall.id}-${index}`"
                    v-model="checkedSuggestions[index]"
                    :disabled="isSubmittingPermission"
                  />
                  <label
                    class="form-check-label"
                    :for="`suggestion-${toolCall.id}-${index}`"
                  >
                    <code>{{ formatSuggestion(suggestion) }}</code>
                  </label>
                </div>
              </div>
              <small class="text-muted mt-2 d-block">
                Checked permissions will be applied to prevent future prompts.
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
              {{ isSubmittingPermission && permissionAction === 'approve-apply' ? '⏳ Submitting...' : '✅ Approve & Apply' }}
            </button>

            <!-- Approve Only (if suggestions exist) or regular Approve -->
            <button
              class="btn"
              :class="hasSuggestions ? 'btn-outline-success' : 'btn-success'"
              @click="handlePermissionDecision('allow', false)"
              :disabled="isSubmittingPermission"
            >
              {{ isSubmittingPermission && permissionAction === 'approve' ? '⏳ Submitting...' : (hasSuggestions ? '✅ Approve Only' : '✅ Approve') }}
            </button>

            <!-- Deny -->
            <button
              class="btn btn-danger"
              @click="handlePermissionDecision('deny', false)"
              :disabled="isSubmittingPermission"
            >
              🚫 {{ isSubmittingPermission && permissionAction === 'deny' ? 'Submitting...' : 'Deny' }}
            </button>
          </div>

          <!-- Provide Guidance (collapsed by default) -->
          <div class="guidance-section mt-3">
            <a
              v-if="!showGuidance"
              href="#"
              class="text-muted small"
              @click.prevent="expandGuidance"
            >
              Add guidance...
            </a>
            <template v-if="showGuidance">
              <label class="form-label fw-bold">
                Guidance (Optional)
              </label>
              <p class="text-muted small mb-2">
                Provide guidance to help Claude retry with better context.
              </p>
              <textarea
                ref="guidanceTextarea"
                v-model="guidanceMessage"
                class="form-control guidance-textarea mb-2"
                rows="1"
                placeholder="e.g., 'Try using a different approach...'"
                :disabled="isSubmittingPermission"
                @input="autoResizeGuidance"
              ></textarea>
              <button
                v-if="guidanceMessage.trim()"
                class="btn btn-warning btn-sm"
                @click="handlePermissionDecision('deny', false, guidanceMessage)"
                :disabled="isSubmittingPermission"
              >
                {{ isSubmittingPermission && permissionAction === 'deny-guidance' ? '⏳ Submitting...' : '🔀 Provide Guidance & Continue' }}
              </button>
            </template>
          </div>
        </template>
      </div>

      <!-- Orphaned Permission Message (when permission was pending but session ended) -->
      <!-- Issue #310: Use effectiveStatus which prefers backend state -->
      <div v-if="effectiveStatus === 'permission_required' && isOrphaned" class="mt-3">
        <div class="alert alert-warning mb-0">
          <div class="d-flex align-items-center">
            <span class="me-2" style="font-size: 1.2rem;">🚧</span>
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
          🚫 Permission denied
          <span v-if="toolCall.result?.message"> - {{ toolCall.result.message }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, nextTick } from 'vue'
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
import TaskCreateToolHandler from '@/components/tools/TaskCreateToolHandler.vue'
import TaskUpdateToolHandler from '@/components/tools/TaskUpdateToolHandler.vue'
import TaskListToolHandler from '@/components/tools/TaskListToolHandler.vue'
import TaskGetToolHandler from '@/components/tools/TaskGetToolHandler.vue'
import NotebookEditToolHandler from '@/components/tools/NotebookEditToolHandler.vue'
import ShellToolHandler from '@/components/tools/ShellToolHandler.vue'
import CommandToolHandler from '@/components/tools/CommandToolHandler.vue'
import SkillToolHandler from '@/components/tools/SkillToolHandler.vue'
import SlashCommandToolHandler from '@/components/tools/SlashCommandToolHandler.vue'
import ExitPlanModeToolHandler from '@/components/tools/ExitPlanModeToolHandler.vue'
import AskUserQuestionToolHandler from '@/components/tools/AskUserQuestionToolHandler.vue'

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
const showGuidance = ref(false)
const guidanceTextarea = ref(null)

// Suggestion checkbox state
const checkedSuggestions = ref({})

// Initialize checkboxes when suggestions appear
watch(() => props.toolCall.suggestions, (suggestions) => {
  if (suggestions && suggestions.length > 0) {
    const checked = {}
    suggestions.forEach((_, index) => {
      checked[index] = true
    })
    checkedSuggestions.value = checked
  }
}, { immediate: true })

// Compute filtered suggestions based on checkbox state
const selectedSuggestions = computed(() => {
  if (!props.toolCall.suggestions) return []
  return props.toolCall.suggestions.filter((_, index) => checkedSuggestions.value[index])
})

// AskUserQuestion handling state
const questionHandlerRef = ref(null)
const currentAnswers = ref({})

// Check if this is an AskUserQuestion tool
const isAskUserQuestion = computed(() => {
  return props.toolCall.name === 'AskUserQuestion'
})

// Check if we have valid answers for all questions
const hasValidAnswers = computed(() => {
  if (!isAskUserQuestion.value) return false
  if (!questionHandlerRef.value) return false
  return questionHandlerRef.value.allQuestionsAnswered
})

// Handle answer updates from question handler
function handleQuestionAnswer(answers) {
  currentAnswers.value = answers
}

// Submit question answers
async function submitQuestionAnswers() {
  if (isSubmittingPermission.value) return
  if (!questionHandlerRef.value) return

  isSubmittingPermission.value = true
  permissionAction.value = 'submit-answers'

  try {
    const sessionId = sessionStore.currentSessionId
    const answers = questionHandlerRef.value.getAnswers()

    // Build updated_input with questions and answers per SDK format
    const updatedInput = {
      questions: props.toolCall.input?.questions || [],
      answers: answers
    }

    // Update local store - include answers in the tool call input for display after completion
    if (sessionId) {
      // First update the tool call's input to include the answers
      messageStore.updateToolCall(sessionId, props.toolCall.id, {
        input: updatedInput
      })

      // Then handle the permission response
      messageStore.handlePermissionResponse(sessionId, {
        request_id: props.toolCall.permissionRequestId,
        decision: 'allow',
        reasoning: 'User answered questions',
        applied_updates: []
      })
    }

    // Send permission response with updated_input to backend
    await wsStore.sendPermissionResponseWithInput(
      props.toolCall.permissionRequestId,
      'allow',
      updatedInput
    )
  } catch (error) {
    console.error('Failed to submit question answers:', error)
  } finally {
    isSubmittingPermission.value = false
    permissionAction.value = null
  }
}

// Orphaned tool detection
// Issue #310 + #324: First checks backend-provided state, then falls back to local tracking
const isOrphaned = computed(() => {
  // Issue #324: Check unified tool_call backendStatus for interrupted/denied states
  if (props.toolCall.backendStatus === 'interrupted') {
    return true
  }
  // Issue #310: Check message store tracking
  return messageStore.isToolUseOrphaned(sessionStore.currentSessionId, props.toolCall.id)
})

const orphanedInfo = computed(() => {
  return messageStore.getOrphanedInfo(sessionStore.currentSessionId, props.toolCall.id)
})

// Issue #310: Backend-provided tool state (for display projection)
const backendToolState = computed(() => {
  return messageStore.getBackendToolState(sessionStore.currentSessionId, props.toolCall.id)
})

// Issue #310 + #324: Effective status - prefer backend state if available
const effectiveStatus = computed(() => {
  // Issue #324: Check unified tool_call backendStatus first (from handleToolCall)
  if (props.toolCall.backendStatus) {
    const backendState = props.toolCall.backendStatus
    // Map backend status to frontend status
    const stateToStatus = {
      'pending': 'pending',
      'awaiting_permission': 'permission_required',
      'running': 'executing',
      'completed': 'completed',
      'failed': 'error',
      'denied': 'completed',  // Denied shows as completed with special styling
      'interrupted': 'completed'  // Interrupted shows as completed with orphaned styling
    }
    return stateToStatus[backendState] || backendState
  }

  // Issue #310: Check backend display projection state
  if (backendToolState.value) {
    const backendState = backendToolState.value.state
    // Map backend state to frontend status
    const stateToStatus = {
      'pending': 'pending',
      'permission_required': 'permission_required',
      'executing': 'executing',
      'completed': 'completed',
      'failed': 'error',
      'orphaned': 'completed'  // Orphaned shows as completed with special styling
    }
    return stateToStatus[backendState] || backendState
  }

  // Fall back to local status
  return props.toolCall.status
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
  'TaskCreate': TaskCreateToolHandler,
  'TaskUpdate': TaskUpdateToolHandler,
  'TaskList': TaskListToolHandler,
  'TaskGet': TaskGetToolHandler,
  'NotebookEdit': NotebookEditToolHandler,
  'BashOutput': ShellToolHandler,
  'KillShell': ShellToolHandler,
  'SlashCommand': SlashCommandToolHandler,
  'Skill': SkillToolHandler,
  'ExitPlanMode': ExitPlanModeToolHandler,
  'AskUserQuestion': AskUserQuestionToolHandler,
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
// Issue #310: Use effectiveStatus which prefers backend state
const cardClass = computed(() => {
  const classes = []

  // Check for orphaned state first (highest priority)
  if (isOrphaned.value) {
    classes.push('border-warning')
    return classes.join(' ')
  }

  switch (effectiveStatus.value) {
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

// Issue #310: Use effectiveStatus which prefers backend state
const statusIcon = computed(() => {
  // Check for orphaned state first
  if (isOrphaned.value) {
    return '⏹️'
  }

  switch (effectiveStatus.value) {
    case 'pending':
      return '🔄'
    case 'permission_required':
      return '❓'
    case 'executing':
      return '⚡'
    case 'completed':
      if (props.toolCall.permissionDecision === 'deny') return '❌'
      return isError.value ? '❗' : '✅'
    case 'error':
      return '❗'
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

// Helper: Extract actual command from bash command (remove cd prefix)
function extractBashCommand(cmd) {
  if (!cmd) return ''
  // Remove cd "..." && prefix to show only the actual command
  // Match: cd "any path" && actual_command
  const match = cmd.match(/cd\s+"[^"]+"\s+&&\s+(.+)$/)
  if (match) {
    return match[1]
  }
  // No cd prefix found, return command as-is
  return cmd
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
// Issue #310: Use effectiveStatus which prefers backend state
const toolSummary = computed(() => {
  const toolName = props.toolCall.name
  const input = props.toolCall.input || {}
  const result = props.toolCall.result
  const status = effectiveStatus.value

  switch (toolName) {
    case 'Bash':
    case 'Shell':
    case 'Command': {
      // Extract actual command (remove cd prefix), then truncate
      const extractedCmd = extractBashCommand(input.command)
      const cmd = truncateBashCommand(extractedCmd, 200)
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
      const skillName = input.skill || 'Unknown'
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

      // For WebSearch, the query is not a URL - display it directly
      if (toolName === 'WebSearch') {
        const query = input.query || ''
        const displayQuery = query.length > 50 ? query.substring(0, 50) + '...' : query
        if (status === 'completed' && result) {
          return result.error ? `Web: ${displayQuery} (error)` : `Web: ${displayQuery} (success)`
        }
        return `Web: ${displayQuery}`
      }

      // For WebFetch, extract domain from URL
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

    case 'AskUserQuestion': {
      const questions = input.questions || []
      const questionCount = questions.length
      if (status === 'completed' && result && !result.error) {
        return `AskUserQuestion: ${questionCount} question(s) answered`
      }
      if (status === 'permission_required') {
        return `AskUserQuestion: ${questionCount} question(s) awaiting response`
      }
      return `AskUserQuestion: ${questionCount} question(s)`
    }

    case 'TaskCreate': {
      const subject = input.subject || ''
      const truncatedSubject = subject.length > 40 ? subject.substring(0, 40) + '...' : subject
      if (status === 'completed' && result && !result.error) {
        return `TaskCreate: "${truncatedSubject}" (created)`
      }
      return `TaskCreate: "${truncatedSubject}"`
    }

    case 'TaskUpdate': {
      const taskId = input.taskId || '?'
      const newStatus = input.status || ''
      if (newStatus) {
        return `TaskUpdate: #${taskId} -> ${newStatus}`
      }
      return `TaskUpdate: #${taskId}`
    }

    case 'TaskList': {
      if (status === 'completed' && result && !result.error) {
        return 'TaskList: Retrieved task list'
      }
      return 'TaskList: Fetching tasks'
    }

    case 'TaskGet': {
      const taskId = input.taskId || '?'
      if (status === 'completed' && result && !result.error) {
        return `TaskGet: #${taskId} (retrieved)`
      }
      return `TaskGet: #${taskId}`
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
    return `Set mode: ${suggestion.mode}`
  } else if (suggestion.type === 'addRules' && suggestion.rules?.length) {
    const ruleStrs = suggestion.rules.map(r => {
      return r.ruleContent ? `${r.toolName}(${r.ruleContent})` : r.toolName
    })
    return `Allow: ${ruleStrs.join(', ')}`
  } else if (suggestion.type === 'addDirectories' && suggestion.directories?.length) {
    return `Add directories: ${suggestion.directories.join(', ')}`
  }
  return JSON.stringify(suggestion)
}

function expandGuidance() {
  showGuidance.value = true
  nextTick(() => {
    if (guidanceTextarea.value) {
      guidanceTextarea.value.focus()
    }
  })
}

function autoResizeGuidance() {
  const textarea = guidanceTextarea.value
  if (!textarea) return
  textarea.style.height = 'auto'
  const maxHeight = parseFloat(getComputedStyle(textarea).lineHeight) * 6 + 10
  textarea.style.height = Math.min(textarea.scrollHeight, maxHeight) + 'px'
}

async function handlePermissionDecision(decision, applySuggestions, guidance = null) {
  if (isSubmittingPermission.value) return

  isSubmittingPermission.value = true

  // Determine effective applySuggestions: if user checked none, treat as approve-only
  const filtered = selectedSuggestions.value
  const effectiveApply = applySuggestions && filtered.length > 0

  if (decision === 'allow') {
    permissionAction.value = effectiveApply ? 'approve-apply' : 'approve'
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
      const appliedUpdates = effectiveApply ? filtered : []

      messageStore.handlePermissionResponse(sessionId, {
        request_id: props.toolCall.permissionRequestId,
        decision: decision,
        reasoning: decision === 'allow' ? 'User allowed permission' : 'User denied permission',
        applied_updates: appliedUpdates
      })
    }

    // Send permission response with selected suggestions to backend
    await wsStore.sendPermissionResponse(
      props.toolCall.permissionRequestId,
      decision,
      effectiveApply,
      guidance,
      effectiveApply ? filtered : null
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

.suggestion-check-item {
  padding: 0.5rem 0.25rem 0.5rem 2rem;
  min-height: 44px;
  display: flex;
  align-items: center;
}

.suggestion-check-item .form-check-input {
  width: 1.2em;
  height: 1.2em;
  margin-top: 0;
}

.suggestion-check-item .form-check-label {
  font-size: 0.9rem;
  cursor: pointer;
}

.suggestion-check-item .form-check-label code {
  padding: 0.25rem 0.5rem;
  background: #f8f9fa;
  border-radius: 0.25rem;
  font-size: 0.85rem;
}

.guidance-section .guidance-textarea {
  resize: none;
  overflow-y: auto;
  min-height: calc(1.5em + 0.75rem + 2px);
  font-family: inherit;
}

.guidance-section .form-label {
  margin-bottom: 0.5rem;
}
</style>
