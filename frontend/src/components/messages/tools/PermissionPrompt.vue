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
            v-for="(suggestion, sgIdx) in toolCall.suggestions"
            :key="sgIdx"
            class="suggestion-group"
          >
            <!-- addRules: per-rule checkboxes -->
            <template v-if="suggestion.type === 'addRules' && suggestion.rules?.length">
              <span class="rule-label">Allow:</span>
              <div v-for="(rule, ruleIdx) in suggestion.rules" :key="ruleIdx" class="suggestion-item suggestion-item-editable">
                <input
                  type="checkbox"
                  :id="`sg-${toolCall.id}-${sgIdx}-${ruleIdx}`"
                  v-model="checkedItems[`${sgIdx}-${ruleIdx}`]"
                  :disabled="isSubmittingPermission"
                />
                <div class="editable-rule-group">
                  <div class="editable-rule">
                    <input
                      type="text"
                      v-model="editedSuggestions[`${sgIdx}-${ruleIdx}`]"
                      @input="onRuleEdit(sgIdx, ruleIdx)"
                      :disabled="isSubmittingPermission"
                      class="rule-input"
                      :class="{
                        'rule-input-dirty': isDirty(sgIdx, ruleIdx) && !validationErrors[`${sgIdx}-${ruleIdx}`],
                        'rule-input-error': !!validationErrors[`${sgIdx}-${ruleIdx}`]
                      }"
                    />
                    <button
                      v-if="isDirty(sgIdx, ruleIdx)"
                      class="reset-btn"
                      @click="resetRule(sgIdx, ruleIdx)"
                      title="Reset to original"
                      type="button"
                    >&#x21BA;</button>
                    <span v-if="isDirty(sgIdx, ruleIdx) && !validationErrors[`${sgIdx}-${ruleIdx}`]" class="edited-badge">edited</span>
                  </div>
                  <div v-if="validationErrors[`${sgIdx}-${ruleIdx}`]" class="validation-error">{{ validationErrors[`${sgIdx}-${ruleIdx}`] }}</div>
                </div>
              </div>
            </template>

            <!-- addDirectories: per-directory checkboxes -->
            <template v-else-if="suggestion.type === 'addDirectories' && suggestion.directories?.length">
              <div v-for="(dir, dirIdx) in suggestion.directories" :key="dirIdx" class="suggestion-item">
                <input
                  type="checkbox"
                  :id="`sg-${toolCall.id}-${sgIdx}-${dirIdx}`"
                  v-model="checkedItems[`${sgIdx}-${dirIdx}`]"
                  :disabled="isSubmittingPermission"
                />
                <label :for="`sg-${toolCall.id}-${sgIdx}-${dirIdx}`">
                  <code>Add directory: {{ dir }}</code>
                </label>
              </div>
            </template>

            <!-- setMode and other single-item types -->
            <template v-else>
              <div class="suggestion-item">
                <input
                  type="checkbox"
                  :id="`sg-${toolCall.id}-${sgIdx}-0`"
                  v-model="checkedItems[`${sgIdx}-0`]"
                  :disabled="isSubmittingPermission"
                />
                <label :for="`sg-${toolCall.id}-${sgIdx}-0`">
                  <code>{{ formatSuggestion(suggestion) }}</code>
                </label>
              </div>
            </template>
          </div>
        </div>

        <div class="permission-buttons">
          <button
            v-if="hasSuggestions"
            class="btn-timeline btn-approve"
            @click="handlePermissionDecision('allow', true)"
            :disabled="isSubmittingPermission || hasValidationErrors"
          >
            {{ isSubmittingPermission && permissionAction === 'approve-apply' ? 'Submitting...' : checkedItemCount < totalItemCount ? `Approve & Apply (${checkedItemCount}/${totalItemCount})` : 'Approve & Apply' }}
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
              aria-label="Permission guidance message"
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
const checkedItems = ref({})
const editedSuggestions = ref({})
const validationErrors = ref({})
const questionHandlerRef = ref(null)
const currentAnswers = ref({})

// Initialize per-item checkboxes and editable rule text
watch(() => props.toolCall.suggestions, (suggestions) => {
  if (suggestions && suggestions.length > 0) {
    const checked = {}
    const edited = {}
    suggestions.forEach((sg, sgIdx) => {
      if (sg.type === 'addRules' && sg.rules?.length) {
        sg.rules.forEach((rule, ruleIdx) => {
          checked[`${sgIdx}-${ruleIdx}`] = true
          edited[`${sgIdx}-${ruleIdx}`] = rule.ruleContent
            ? `${rule.toolName}(${rule.ruleContent})`
            : rule.toolName
        })
      } else if (sg.type === 'addDirectories' && sg.directories?.length) {
        sg.directories.forEach((_, dirIdx) => {
          checked[`${sgIdx}-${dirIdx}`] = true
        })
      } else {
        checked[`${sgIdx}-0`] = true
      }
    })
    checkedItems.value = checked
    editedSuggestions.value = edited
    validationErrors.value = {}
  }
}, { immediate: true })

// Count total and checked items for button label
const totalItemCount = computed(() => {
  if (!props.toolCall.suggestions) return 0
  let count = 0
  props.toolCall.suggestions.forEach(sg => {
    if (sg.type === 'addRules' && sg.rules?.length) count += sg.rules.length
    else if (sg.type === 'addDirectories' && sg.directories?.length) count += sg.directories.length
    else count += 1
  })
  return count
})

const checkedItemCount = computed(() => {
  return Object.values(checkedItems.value).filter(Boolean).length
})

const selectedSuggestions = computed(() => {
  if (!props.toolCall.suggestions) return []
  const result = []
  props.toolCall.suggestions.forEach((sg, sgIdx) => {
    if (sg.type === 'addRules' && sg.rules?.length) {
      const filteredRules = sg.rules.filter((_, ruleIdx) => checkedItems.value[`${sgIdx}-${ruleIdx}`])
      if (filteredRules.length > 0) result.push({ ...sg, rules: filteredRules, _sgIdx: sgIdx })
    } else if (sg.type === 'addDirectories' && sg.directories?.length) {
      const filteredDirs = sg.directories.filter((_, dirIdx) => checkedItems.value[`${sgIdx}-${dirIdx}`])
      if (filteredDirs.length > 0) result.push({ ...sg, directories: filteredDirs })
    } else if (checkedItems.value[`${sgIdx}-0`]) {
      result.push(sg)
    }
  })
  return result
})

const isAskUserQuestion = computed(() => props.toolCall.name === 'AskUserQuestion')

const hasValidAnswers = computed(() => {
  if (!isAskUserQuestion.value || !questionHandlerRef.value) return false
  return questionHandlerRef.value.allQuestionsAnswered
})

const hasSuggestions = computed(() => {
  return props.toolCall.suggestions && props.toolCall.suggestions.length > 0
})

const hasValidationErrors = computed(() => {
  if (!props.toolCall.suggestions) return false
  for (let sgIdx = 0; sgIdx < props.toolCall.suggestions.length; sgIdx++) {
    const sg = props.toolCall.suggestions[sgIdx]
    if (sg.type !== 'addRules' || !sg.rules?.length) continue
    for (let ruleIdx = 0; ruleIdx < sg.rules.length; ruleIdx++) {
      if (!checkedItems.value[`${sgIdx}-${ruleIdx}`]) continue
      if (validationErrors.value[`${sgIdx}-${ruleIdx}`]) return true
    }
  }
  return false
})

// Editable suggestion helpers
function getOriginalRuleText(sgIdx, ruleIdx) {
  const rule = props.toolCall.suggestions?.[sgIdx]?.rules?.[ruleIdx]
  if (!rule) return ''
  return rule.ruleContent ? `${rule.toolName}(${rule.ruleContent})` : rule.toolName
}

function isDirty(sgIdx, ruleIdx) {
  return editedSuggestions.value[`${sgIdx}-${ruleIdx}`] !== getOriginalRuleText(sgIdx, ruleIdx)
}

function resetRule(sgIdx, ruleIdx) {
  const key = `${sgIdx}-${ruleIdx}`
  editedSuggestions.value[key] = getOriginalRuleText(sgIdx, ruleIdx)
  delete validationErrors.value[key]
}

function validatePermissionFormat(text) {
  if (!text || !text.trim()) return 'Permission rule cannot be empty'
  const trimmed = text.trim()
  if (!/^[A-Z]/.test(trimmed)) return 'Tool name must start with uppercase letter'
  const parenIdx = trimmed.indexOf('(')
  if (parenIdx === -1) {
    if (!/^[A-Za-z_]+$/.test(trimmed)) return 'Invalid tool name format'
    return null
  }
  if (!trimmed.endsWith(')')) return 'Missing closing parenthesis'
  const toolName = trimmed.substring(0, parenIdx)
  if (!/^[A-Za-z_]+$/.test(toolName)) return 'Invalid tool name format'
  let depth = 0
  for (let i = parenIdx; i < trimmed.length; i++) {
    if (trimmed[i] === '(') depth++
    else if (trimmed[i] === ')') depth--
    if (depth < 0) return 'Unbalanced parentheses'
  }
  if (depth !== 0) return 'Unbalanced parentheses'
  return null
}

function onRuleEdit(sgIdx, ruleIdx) {
  const key = `${sgIdx}-${ruleIdx}`
  validationErrors.value[key] = validatePermissionFormat(editedSuggestions.value[key])
}

function parseRuleText(text) {
  const trimmed = text.trim()
  const parenIdx = trimmed.indexOf('(')
  if (parenIdx === -1) return { toolName: trimmed, ruleContent: '' }
  return {
    toolName: trimmed.substring(0, parenIdx),
    ruleContent: trimmed.substring(parenIdx + 1, trimmed.lastIndexOf(')'))
  }
}

function reconstructEditedSuggestions(suggestions) {
  return suggestions.map(sg => {
    if (sg.type !== 'addRules' || !sg.rules?.length) return sg
    const sgIdx = sg._sgIdx
    if (sgIdx === undefined) return sg
    const editedRules = sg.rules.map(rule => {
      // Find this rule's original index in the source suggestion
      const originalRules = props.toolCall.suggestions[sgIdx]?.rules || []
      const ruleIdx = originalRules.indexOf(rule)
      if (ruleIdx === -1) return rule
      const key = `${sgIdx}-${ruleIdx}`
      const editedText = editedSuggestions.value[key]
      if (!editedText || editedText === getOriginalRuleText(sgIdx, ruleIdx)) return rule
      return parseRuleText(editedText)
    })
    const { _sgIdx, ...rest } = sg
    return { ...rest, rules: editedRules }
  })
}

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

function deduplicateSuggestions(suggestions) {
  const seenRules = new Set()
  const seenDirs = new Set()
  return suggestions.map(sg => {
    if (sg.type === 'addRules' && sg.rules?.length) {
      const uniqueRules = sg.rules.filter(rule => {
        const key = rule.ruleContent ? `${rule.toolName}(${rule.ruleContent})` : rule.toolName
        if (seenRules.has(key)) return false
        seenRules.add(key)
        return true
      })
      if (uniqueRules.length === 0) return null
      return { ...sg, rules: uniqueRules }
    }
    if (sg.type === 'addDirectories' && sg.directories?.length) {
      const uniqueDirs = sg.directories.filter(dir => {
        if (seenDirs.has(dir)) return false
        seenDirs.add(dir)
        return true
      })
      if (uniqueDirs.length === 0) return null
      return { ...sg, directories: uniqueDirs }
    }
    return sg
  }).filter(Boolean)
}

async function handlePermissionDecision(decision, applySuggestions, guidance = null) {
  if (isSubmittingPermission.value) return
  isSubmittingPermission.value = true

  // Reconstruct suggestions with any user edits, then deduplicate
  let filtered = selectedSuggestions.value
  const effectiveApply = applySuggestions && filtered.length > 0
  if (effectiveApply) {
    filtered = reconstructEditedSuggestions(filtered)
    filtered = deduplicateSuggestions(filtered)
  }

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
  width: 100%;
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
  padding: 8px 10px;
  background: #eff6ff;
  border: 1px solid #bfdbfe;
  border-radius: 4px;
  width: 100%;
  box-sizing: border-box;
}

.suggestions-label {
  font-size: 11px;
  font-weight: 600;
  color: #1e40af;
  margin-bottom: 4px;
}

.suggestion-group {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 4px;
  padding: 6px 8px;
  margin-bottom: 4px;
  width: 100%;
  box-sizing: border-box;
}

.suggestion-group:last-child {
  margin-bottom: 0;
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

.suggestion-item-editable {
  align-items: flex-start;
}

.editable-rules-wrapper {
  display: flex;
  flex-direction: column;
  gap: 2px;
  flex: 1;
  min-width: 0;
}

.rule-label {
  font-size: 11px;
  color: #1e40af;
  font-weight: 500;
}

.editable-rule-group {
  display: flex;
  flex-direction: column;
  gap: 1px;
}

.editable-rule {
  display: flex;
  align-items: center;
  gap: 4px;
}

.rule-input {
  font-family: monospace;
  font-size: 11px;
  padding: 1px 4px;
  border: 1px solid #cbd5e1;
  border-radius: 2px;
  background: white;
  flex: 1;
  min-width: 180px;
}

.rule-input:focus {
  outline: none;
  border-color: #3b82f6;
  box-shadow: 0 0 0 1px #3b82f6;
}

.rule-input-dirty {
  border-color: #22c55e;
  background: #f0fdf4;
}

.rule-input-error {
  border-color: #ef4444;
  background: #fef2f2;
}

.reset-btn {
  background: none;
  border: none;
  cursor: pointer;
  font-size: 14px;
  padding: 0 2px;
  color: #64748b;
  line-height: 1;
}
.reset-btn:hover { color: #1e40af; }

.edited-badge {
  font-size: 9px;
  background: #dbeafe;
  color: #1e40af;
  padding: 0 4px;
  border-radius: 2px;
  white-space: nowrap;
}

.validation-error {
  font-size: 9px;
  color: #ef4444;
  padding-left: 2px;
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

/* Issue #716: Responsive overrides for small screens */
@media (max-width: 375px) {
  .rule-input {
    min-width: 100px;
  }

  .suggestions-section {
    padding: 4px 6px;
  }

  .suggestion-item {
    font-size: 11px;
  }

  .permission-buttons {
    gap: 4px;
  }

  .btn-timeline {
    padding: 4px 8px;
    font-size: 11px;
  }
}
</style>
