<template>
  <div class="advanced-tab">
    <!-- System Prompt / Initialization Context -->
    <div class="mb-3">
      <label for="config-system-prompt" class="form-label">
        {{ isTemplateMode ? 'Default System Prompt' : 'Initialization Context' }}
        <span v-if="fieldStates.initialization_context === 'autofilled'" class="field-indicator autofilled" title="Auto-filled from template">
          &lt;
        </span>
        <span v-if="fieldStates.initialization_context === 'modified'" class="field-indicator modified" title="Modified from template">
          *
        </span>
      </label>
      <textarea
        id="config-system-prompt"
        class="form-control"
        :class="initContextFieldClass"
        :value="promptValue"
        @input="handlePromptInput"
        :rows="isTemplateMode ? 4 : 5"
        :placeholder="promptPlaceholder"
      ></textarea>
      <div class="form-text">
        {{ promptHelpText }}
      </div>
    </div>

    <!-- Override System Prompt -->
    <div class="mb-3 form-check">
      <input
        type="checkbox"
        class="form-check-input"
        id="config-override-prompt"
        :checked="formData.override_system_prompt"
        @change="$emit('update:form-data', 'override_system_prompt', $event.target.checked)"
      />
      <label class="form-check-label" for="config-override-prompt">
        Override Claude Code preset (use custom prompt only)
      </label>
      <div class="form-text text-warning" v-if="formData.override_system_prompt">
        <small>Override mode may cause unexpected behaviors. Only custom instructions will be used (no Claude Code preset{{ !isTemplateMode ? ' or legion guide' : '' }}).</small>
      </div>
    </div>

  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  mode: {
    type: String,
    required: true
  },
  formData: {
    type: Object,
    required: true
  },
  errors: {
    type: Object,
    required: true
  },
  session: {
    type: Object,
    default: null
  },
  fieldStates: {
    type: Object,
    default: () => ({
      initialization_context: 'normal'
    })
  }
})

const emit = defineEmits(['update:form-data'])

// Computed
const isSessionMode = computed(() => props.mode === 'create-session' || props.mode === 'edit-session')
const isTemplateMode = computed(() => props.mode === 'create-template' || props.mode === 'edit-template')
// Get the correct prompt value based on mode
const promptValue = computed(() => {
  return props.formData.initialization_context
})

const promptPlaceholder = computed(() => {
  return isTemplateMode.value
    ? 'Optional default system prompt for sessions using this template'
    : 'Instructions and context for the session...'
})

const promptHelpText = computed(() => {
  if (props.formData.override_system_prompt) {
    return isTemplateMode.value
      ? 'This prompt will replace Claude Code\'s preset'
      : 'This context will replace Claude Code\'s preset and legion guide'
  }
  return isTemplateMode.value
    ? 'This prompt will be appended to Claude Code\'s preset'
    : 'This context will be appended to legion guide and Claude Code\'s preset'
})

// Field highlighting classes
const initContextFieldClass = computed(() => {
  const classes = {}
  if (props.fieldStates.initialization_context === 'autofilled') {
    classes['field-autofilled'] = true
  }
  if (props.fieldStates.initialization_context === 'modified') {
    classes['field-modified'] = true
  }
  return classes
})

// Methods
function handlePromptInput(event) {
  const value = event.target.value
  emit('update:form-data', 'initialization_context', value)
}
</script>

<style scoped>
.card {
  background-color: var(--bs-gray-100);
}

/* Field highlighting states */
.field-autofilled {
  background-color: #fffbea !important;
  transition: background-color 0.3s ease;
}

.field-modified {
  background-color: #ffe4cc !important;
  transition: background-color 0.3s ease;
}

/* Ensure text readability */
.field-autofilled,
.field-modified {
  color: #212529;
}

/* Ensure borders remain visible */
.form-control.field-autofilled,
.form-control.field-modified {
  border: 1px solid #dee2e6;
}

/* Focus states */
.form-control.field-autofilled:focus,
.form-control.field-modified:focus {
  border-color: #0d6efd;
  box-shadow: 0 0 0 0.25rem rgba(13, 110, 253, 0.25);
}

/* Field indicators */
.field-indicator {
  margin-left: 0.5rem;
  font-size: 0.75rem;
  font-weight: bold;
  cursor: help;
}

.field-indicator.autofilled {
  color: #856404;
}

.field-indicator.modified {
  color: #cc5500;
}

/* Smooth transitions */
.form-control {
  transition: background-color 0.3s ease, border-color 0.3s ease;
}
</style>
