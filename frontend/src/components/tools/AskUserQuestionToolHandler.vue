<template>
  <div class="ask-user-question-handler">
    <!-- Read-only mode: Show completed answers -->
    <template v-if="isCompleted">
      <div v-if="hasAnswers" class="answers-summary">
        <div v-for="(answer, question) in completedAnswers" :key="question" class="answer-item mb-2">
          <div class="answer-question text-muted small">{{ question }}</div>
          <div class="answer-value fw-medium">{{ answer }}</div>
        </div>
      </div>
      <div v-else class="text-muted small">
        No answers recorded.
      </div>
    </template>

    <!-- Interactive mode: Show questions with selectable options -->
    <template v-else>
      <div v-if="questions.length > 0" class="questions-container">
        <div v-for="(question, qIndex) in questions" :key="qIndex" class="question-block mb-3">
          <!-- Question Header/Label -->
          <div class="question-header d-flex align-items-center gap-2 mb-2">
            <span v-if="question.header" class="badge bg-secondary">{{ question.header }}</span>
            <span class="question-text fw-medium">{{ question.question }}</span>
          </div>

          <!-- Options -->
          <div class="options-list">
            <div
              v-for="(option, oIndex) in question.options"
              :key="oIndex"
              class="option-item form-check"
              :class="{ 'selected': isSelected(qIndex, option.label) }"
            >
              <!-- Multi-select uses checkboxes -->
              <input
                v-if="question.multiSelect"
                type="checkbox"
                class="form-check-input"
                :id="`q${qIndex}-opt${oIndex}`"
                :checked="isSelected(qIndex, option.label)"
                @change="toggleOption(qIndex, option.label)"
              >
              <!-- Single-select uses radio buttons -->
              <input
                v-else
                type="radio"
                class="form-check-input"
                :id="`q${qIndex}-opt${oIndex}`"
                :name="`question-${qIndex}`"
                :checked="isSelected(qIndex, option.label)"
                @change="selectOption(qIndex, option.label)"
              >
              <label class="form-check-label" :for="`q${qIndex}-opt${oIndex}`">
                <span class="option-label">{{ option.label }}</span>
                <span v-if="option.description" class="option-description text-muted d-block small">
                  {{ option.description }}
                </span>
              </label>
            </div>

            <!-- "Other" option with free-text input -->
            <div class="option-item form-check other-option">
              <input
                v-if="question.multiSelect"
                type="checkbox"
                class="form-check-input"
                :id="`q${qIndex}-other`"
                :checked="hasOtherSelected(qIndex)"
                @change="toggleOther(qIndex)"
              >
              <input
                v-else
                type="radio"
                class="form-check-input"
                :id="`q${qIndex}-other`"
                :name="`question-${qIndex}`"
                :checked="hasOtherSelected(qIndex)"
                @change="selectOther(qIndex)"
              >
              <label class="form-check-label" :for="`q${qIndex}-other`">
                <span class="option-label">Other</span>
              </label>
              <!-- Free-text input for "Other" -->
              <div v-if="hasOtherSelected(qIndex)" class="other-input mt-2">
                <input
                  type="text"
                  class="form-control form-control-sm"
                  v-model="otherTexts[qIndex]"
                  placeholder="Enter your custom answer..."
                  @input="updateOtherText(qIndex)"
                >
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- Empty state if no questions -->
      <div v-else class="alert alert-info mb-0">
        No questions to display.
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'

const props = defineProps({
  toolCall: {
    type: Object,
    required: true
  }
})

const emit = defineEmits(['answer'])

// Check if tool is completed (has result)
const isCompleted = computed(() => {
  return props.toolCall.status === 'completed' || props.toolCall.result != null
})

// Get answers from completed result
const completedAnswers = computed(() => {
  if (!isCompleted.value) return {}

  // Try to get answers from result
  const result = props.toolCall.result
  if (result?.content) {
    // Result content might be a string or object
    if (typeof result.content === 'object' && result.content.answers) {
      return result.content.answers
    }
    // Try parsing if it's a string
    if (typeof result.content === 'string') {
      try {
        const parsed = JSON.parse(result.content)
        if (parsed.answers) return parsed.answers
      } catch {
        // Not JSON, ignore
      }
    }
  }

  // Fallback: try to get from input if answers were merged
  const input = props.toolCall.input || {}
  if (input.answers) {
    return input.answers
  }

  return {}
})

const hasAnswers = computed(() => {
  return Object.keys(completedAnswers.value).length > 0
})

// Parse questions from toolCall input
const questions = computed(() => {
  const input = props.toolCall.input || {}
  return input.questions || []
})

// Store selected answers per question
// For single-select: string value
// For multi-select: array of string values
const selections = ref({})

// Store "Other" text inputs
const otherTexts = ref({})

// Initialize selections when questions change
watch(questions, (newQuestions) => {
  newQuestions.forEach((q, index) => {
    if (!(index in selections.value)) {
      selections.value[index] = q.multiSelect ? [] : null
    }
    if (!(index in otherTexts.value)) {
      otherTexts.value[index] = ''
    }
  })
}, { immediate: true })

// Check if an option is selected
function isSelected(qIndex, label) {
  const selection = selections.value[qIndex]
  if (Array.isArray(selection)) {
    return selection.includes(label)
  }
  return selection === label
}

// Check if "Other" is selected
function hasOtherSelected(qIndex) {
  const selection = selections.value[qIndex]
  if (Array.isArray(selection)) {
    return selection.includes('__OTHER__')
  }
  return selection === '__OTHER__'
}

// Single-select: select an option
function selectOption(qIndex, label) {
  selections.value[qIndex] = label
  emitAnswers()
}

// Single-select: select "Other"
function selectOther(qIndex) {
  selections.value[qIndex] = '__OTHER__'
  emitAnswers()
}

// Multi-select: toggle an option
function toggleOption(qIndex, label) {
  const selection = selections.value[qIndex]
  if (!Array.isArray(selection)) {
    selections.value[qIndex] = [label]
  } else {
    const index = selection.indexOf(label)
    if (index === -1) {
      selection.push(label)
    } else {
      selection.splice(index, 1)
    }
  }
  emitAnswers()
}

// Multi-select: toggle "Other"
function toggleOther(qIndex) {
  const selection = selections.value[qIndex]
  if (!Array.isArray(selection)) {
    selections.value[qIndex] = ['__OTHER__']
  } else {
    const index = selection.indexOf('__OTHER__')
    if (index === -1) {
      selection.push('__OTHER__')
    } else {
      selection.splice(index, 1)
    }
  }
  emitAnswers()
}

// Update "Other" text
function updateOtherText(qIndex) {
  emitAnswers()
}

// Build and emit answers object
function emitAnswers() {
  const answers = {}

  questions.value.forEach((q, index) => {
    const selection = selections.value[index]
    const questionText = q.question

    if (q.multiSelect) {
      // Multi-select: comma-separated labels
      if (Array.isArray(selection) && selection.length > 0) {
        const labels = selection.map(s => {
          if (s === '__OTHER__') {
            return otherTexts.value[index] || ''
          }
          return s
        }).filter(s => s)  // Remove empty strings

        answers[questionText] = labels.join(', ')
      }
    } else {
      // Single-select: single label or custom text
      if (selection === '__OTHER__') {
        answers[questionText] = otherTexts.value[index] || ''
      } else if (selection) {
        answers[questionText] = selection
      }
    }
  })

  emit('answer', answers)
}

// Computed: check if all questions have answers
const allQuestionsAnswered = computed(() => {
  return questions.value.every((q, index) => {
    const selection = selections.value[index]

    if (q.multiSelect) {
      if (!Array.isArray(selection) || selection.length === 0) {
        return false
      }
      // If "Other" is selected, check that text is provided
      if (selection.includes('__OTHER__') && !otherTexts.value[index]) {
        return false
      }
    } else {
      if (!selection) {
        return false
      }
      // If "Other" is selected, check that text is provided
      if (selection === '__OTHER__' && !otherTexts.value[index]) {
        return false
      }
    }

    return true
  })
})

// Expose for parent component
defineExpose({
  allQuestionsAnswered,
  getAnswers: () => {
    const answers = {}
    questions.value.forEach((q, index) => {
      const selection = selections.value[index]
      const questionText = q.question

      if (q.multiSelect) {
        if (Array.isArray(selection) && selection.length > 0) {
          const labels = selection.map(s => {
            if (s === '__OTHER__') {
              return otherTexts.value[index] || ''
            }
            return s
          }).filter(s => s)

          answers[questionText] = labels.join(', ')
        }
      } else {
        if (selection === '__OTHER__') {
          answers[questionText] = otherTexts.value[index] || ''
        } else if (selection) {
          answers[questionText] = selection
        }
      }
    })
    return answers
  }
})
</script>

<style scoped>
.ask-user-question-handler {
  padding: 0.5rem;
}

/* Read-only answers display */
.answers-summary {
  background: #f8f9fa;
  border: 1px solid #dee2e6;
  border-radius: 0.375rem;
  padding: 0.75rem;
}

.answer-item {
  padding-bottom: 0.5rem;
  border-bottom: 1px solid #eee;
}

.answer-item:last-child {
  padding-bottom: 0;
  border-bottom: none;
  margin-bottom: 0 !important;
}

.answer-question {
  font-size: 0.85rem;
}

.answer-value {
  color: #0d6efd;
}

/* Interactive questions display */
.question-block {
  background: #f8f9fa;
  border: 1px solid #dee2e6;
  border-radius: 0.375rem;
  padding: 1rem;
}

.question-header {
  border-bottom: 1px solid #dee2e6;
  padding-bottom: 0.5rem;
  margin-bottom: 0.75rem;
}

.question-text {
  color: #212529;
  font-size: 0.95rem;
}

.options-list {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.option-item {
  padding: 0.5rem 0.75rem;
  border: 1px solid #dee2e6;
  border-radius: 0.25rem;
  background: #fff;
  transition: all 0.15s ease;
}

.option-item:hover {
  background: #f0f7ff;
  border-color: #0d6efd;
}

.option-item.selected {
  background: #e7f1ff;
  border-color: #0d6efd;
}

.option-label {
  font-weight: 500;
  color: #212529;
}

.option-description {
  margin-top: 0.25rem;
  line-height: 1.4;
}

.other-option {
  border-style: dashed;
}

.other-input {
  margin-left: 1.5rem;
}

.form-check-input {
  cursor: pointer;
}

.form-check-label {
  cursor: pointer;
  width: 100%;
}
</style>
