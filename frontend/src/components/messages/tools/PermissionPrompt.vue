<template>
  <div class="permission-prompt">
    <!-- Permission Prompt (active) -->
    <div v-if="effectiveStatus === 'permission_required' && !isOrphaned" class="permission-section">
      <!-- AskUserQuestion UI -->
      <template v-if="isAskUserQuestion">
        <div class="detail-banner detail-banner-info">
          <strong>Claude is asking for your input</strong>
        </div>
        <AskUserQuestionToolHandler
          ref="questionHandlerRef"
          :toolCall="toolCall"
          :disabled="isSubmittingPermission"
          @answer="handleQuestionAnswer"
        />
        <div class="permission-buttons">
          <button
            class="btn-timeline btn-primary"
            @click="submitQuestionAnswers"
            :disabled="isSubmittingPermission || !hasValidAnswers"
          >
            {{ isSubmittingPermission ? 'Submitting...' : 'Submit Answers' }}
          </button>
          <button
            class="btn-timeline btn-secondary"
            @click="handlePermissionDecision('deny', false)"
            :disabled="isSubmittingPermission"
          >
            Skip
          </button>
        </div>
      </template>

      <!-- Standard Permission UI -->
      <template v-else>
        <div class="detail-banner detail-banner-warning">
          <strong>Permission Required</strong>
          <p>Claude wants to use <code>{{ toolCall.name }}</code></p>
        </div>

        <!-- Suggestions -->
        <div v-if="hasSuggestions" class="suggestions-section">
          <p class="suggestions-label">Suggested Permission Updates:</p>
          <div
            v-for="(suggestion, index) in toolCall.suggestions"
            :key="index"
            class="suggestion-item"
          >
            <input
              type="checkbox"
              :id="`sg-${toolCall.id}-${index}`"
              v-model="checkedSuggestions[index]"
              :disabled="isSubmittingPermission"
            />
            <label :for="`sg-${toolCall.id}-${index}`">
              <code>{{ formatSuggestion(suggestion) }}</code>
            </label>
          </div>
        </div>

        <div class="permission-buttons">
          <button
            v-if="hasSuggestions"
            class="btn-timeline btn-approve"
            @click="handlePermissionDecision('allow', true)"
            :disabled="isSubmittingPermission"
          >
            {{ isSubmittingPermission && permissionAction === 'approve-apply' ? 'Submitting...' : 'Approve & Apply' }}
          </button>
          <button
            class="btn-timeline"
            :class="hasSuggestions ? 'btn-approve-outline' : 'btn-approve'"
            @click="handlePermissionDecision('allow', false)"
            :disabled="isSubmittingPermission"
          >
            {{ hasSuggestions ? 'Approve Only' : 'Approve' }}
          </button>
          <button
            class="btn-timeline btn-deny"
            @click="handlePermissionDecision('deny', false)"
            :disabled="isSubmittingPermission"
          >
            Deny
          </button>
        </div>

        <!-- Guidance -->
        <div class="guidance-section">
          <a
            v-if="!showGuidance"
            href="#"
            class="guidance-link"
            @click.prevent="expandGuidance"
          >
            Add guidance...
          </a>
          <template v-if="showGuidance">
            <textarea
              ref="guidanceTextarea"
              v-model="guidanceMessage"
              class="guidance-textarea"
              rows="1"
              placeholder="e.g., 'Try using a different approach...'"
              :disabled="isSubmittingPermission"
              @input="autoResizeGuidance"
            ></textarea>
            <button
              v-if="guidanceMessage.trim()"
              class="btn-timeline btn-guidance"
              @click="handlePermissionDecision('deny', false, guidanceMessage)"
              :disabled="isSubmittingPermission"
            >
              Provide Guidance & Continue
            </button>
          </template>
        </div>
      </template>
    </div>

    <!-- Orphaned Permission -->
    <div v-if="effectiveStatus === 'permission_required' && isOrphaned" class="detail-banner detail-banner-warning">
      <strong>Permission Request Cancelled</strong>
      <p>{{ orphanedInfo?.message || 'Session was terminated before permission could be granted' }}</p>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, toRef, watch, nextTick } from 'vue'
import { useMessageStore } from '@/stores/message'
import { useSessionStore } from '@/stores/session'
import { useWebSocketStore } from '@/stores/websocket'
import { useToolStatus } from '@/composables/useToolStatus'
import AskUserQuestionToolHandler from '@/components/tools/AskUserQuestionToolHandler.vue'

const props = defineProps({
  toolCall: { type: Object, required: true }
})

const messageStore = useMessageStore()
const sessionStore = useSessionStore()
const wsStore = useWebSocketStore()

const { effectiveStatus, isOrphaned, orphanedInfo } = useToolStatus(toRef(props, 'toolCall'))

// Local state
const isSubmittingPermission = ref(false)
const permissionAction = ref(null)
const guidanceMessage = ref('')
const showGuidance = ref(false)
const guidanceTextarea = ref(null)
const checkedSuggestions = ref({})
const questionHandlerRef = ref(null)
const currentAnswers = ref({})

// Initialize suggestion checkboxes
watch(() => props.toolCall.suggestions, (suggestions) => {
  if (suggestions && suggestions.length > 0) {
    const checked = {}
    suggestions.forEach((_, index) => { checked[index] = true })
    checkedSuggestions.value = checked
  }
}, { immediate: true })

const selectedSuggestions = computed(() => {
  if (!props.toolCall.suggestions) return []
  return props.toolCall.suggestions.filter((_, index) => checkedSuggestions.value[index])
})

const isAskUserQuestion = computed(() => props.toolCall.name === 'AskUserQuestion')

const hasValidAnswers = computed(() => {
  if (!isAskUserQuestion.value || !questionHandlerRef.value) return false
  return questionHandlerRef.value.allQuestionsAnswered
})

const hasSuggestions = computed(() => {
  return props.toolCall.suggestions && props.toolCall.suggestions.length > 0
})

// Permission handlers
function handleQuestionAnswer(answers) {
  currentAnswers.value = answers
}

async function submitQuestionAnswers() {
  if (isSubmittingPermission.value || !questionHandlerRef.value) return
  isSubmittingPermission.value = true
  permissionAction.value = 'submit-answers'
  try {
    const sessionId = sessionStore.currentSessionId
    const answers = questionHandlerRef.value.getAnswers()
    const updatedInput = {
      questions: props.toolCall.input?.questions || [],
      answers: answers
    }
    if (sessionId) {
      messageStore.updateToolCall(sessionId, props.toolCall.id, { input: updatedInput })
      messageStore.handlePermissionResponse(sessionId, {
        request_id: props.toolCall.permissionRequestId,
        decision: 'allow',
        reasoning: 'User answered questions',
        applied_updates: []
      })
    }
    await wsStore.sendPermissionResponseWithInput(
      props.toolCall.permissionRequestId, 'allow', updatedInput
    )
  } catch (error) {
    console.error('Failed to submit question answers:', error)
  } finally {
    isSubmittingPermission.value = false
    permissionAction.value = null
  }
}

async function handlePermissionDecision(decision, applySuggestions, guidance = null) {
  if (isSubmittingPermission.value) return
  isSubmittingPermission.value = true

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
    await wsStore.sendPermissionResponse(
      props.toolCall.permissionRequestId, decision, effectiveApply, guidance,
      effectiveApply ? filtered : null
    )
  } catch (error) {
    console.error('Failed to send permission response:', error)
  } finally {
    isSubmittingPermission.value = false
    permissionAction.value = null
  }
}

function formatSuggestion(suggestion) {
  if (suggestion.type === 'setMode') {
    return `Set mode: ${suggestion.mode}`
  } else if (suggestion.type === 'addRules' && suggestion.rules?.length) {
    const ruleStrs = suggestion.rules.map(r => r.ruleContent ? `${r.toolName}(${r.ruleContent})` : r.toolName)
    return `Allow: ${ruleStrs.join(', ')}`
  } else if (suggestion.type === 'addDirectories' && suggestion.directories?.length) {
    return `Add directories: ${suggestion.directories.join(', ')}`
  }
  return JSON.stringify(suggestion)
}

function expandGuidance() {
  showGuidance.value = true
  nextTick(() => { guidanceTextarea.value?.focus() })
}

function autoResizeGuidance() {
  const textarea = guidanceTextarea.value
  if (!textarea) return
  textarea.style.height = 'auto'
  const maxHeight = parseFloat(getComputedStyle(textarea).lineHeight) * 6 + 10
  textarea.style.height = Math.min(textarea.scrollHeight, maxHeight) + 'px'
}
</script>

<style scoped>
.permission-prompt {
  margin-top: 4px;
  font-size: 13px;
}

/* Banners */
.detail-banner {
  padding: 6px 10px;
  border-radius: 4px;
  margin-bottom: 8px;
  font-size: 12px;
}

.detail-banner strong { display: block; margin-bottom: 2px; }
.detail-banner p { margin: 0; opacity: 0.8; }

.detail-banner-warning {
  background: #fef3c7;
  border: 1px solid #fbbf24;
  color: #92400e;
}

.detail-banner-info {
  background: #dbeafe;
  border: 1px solid #60a5fa;
  color: #1e40af;
}

/* Permission UI */
.permission-section {
  margin-top: 0;
}

.permission-buttons {
  display: flex;
  gap: 6px;
  margin-top: 8px;
  flex-wrap: wrap;
}

.btn-timeline {
  padding: 4px 12px;
  border-radius: 4px;
  border: 1px solid #e2e8f0;
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s;
  background: white;
  color: #334155;
}

.btn-timeline:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.btn-approve {
  background: #22c55e;
  border-color: #22c55e;
  color: white;
}
.btn-approve:hover:not(:disabled) { background: #16a34a; }

.btn-approve-outline {
  border-color: #22c55e;
  color: #16a34a;
}
.btn-approve-outline:hover:not(:disabled) { background: #f0fdf4; }

.btn-deny {
  background: #ef4444;
  border-color: #ef4444;
  color: white;
}
.btn-deny:hover:not(:disabled) { background: #dc2626; }

.btn-primary {
  background: #3b82f6;
  border-color: #3b82f6;
  color: white;
}
.btn-primary:hover:not(:disabled) { background: #2563eb; }

.btn-secondary {
  background: #f1f5f9;
  border-color: #cbd5e1;
  color: #475569;
}
.btn-secondary:hover:not(:disabled) { background: #e2e8f0; }

.btn-guidance {
  background: #f59e0b;
  border-color: #f59e0b;
  color: white;
}
.btn-guidance:hover:not(:disabled) { background: #d97706; }

/* Suggestions */
.suggestions-section {
  margin: 8px 0;
  padding: 6px 8px;
  background: #eff6ff;
  border: 1px solid #bfdbfe;
  border-radius: 4px;
}

.suggestions-label {
  font-size: 11px;
  font-weight: 600;
  color: #1e40af;
  margin-bottom: 4px;
}

.suggestion-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 3px 0;
  font-size: 12px;
}

.suggestion-item input[type="checkbox"] {
  width: 14px;
  height: 14px;
}

.suggestion-item code {
  font-size: 11px;
  background: white;
  padding: 1px 4px;
  border-radius: 2px;
}

/* Guidance */
.guidance-section {
  margin-top: 8px;
}

.guidance-link {
  font-size: 11px;
  color: #64748b;
}

.guidance-textarea {
  width: 100%;
  border: 1px solid #cbd5e1;
  border-radius: 4px;
  padding: 4px 8px;
  font-size: 12px;
  resize: none;
  min-height: 28px;
  margin-top: 4px;
}
</style>
