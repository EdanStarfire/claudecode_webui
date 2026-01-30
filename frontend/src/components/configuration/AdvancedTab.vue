<template>
  <div class="advanced-tab">
    <!-- System Prompt / Initialization Context -->
    <div class="mb-3">
      <label for="config-system-prompt" class="form-label">
        {{ isTemplateMode ? 'Default System Prompt' : 'Initialization Context' }}
      </label>
      <textarea
        id="config-system-prompt"
        class="form-control"
        :class="{ 'is-invalid': hasCharLimitError }"
        :value="promptValue"
        @input="handlePromptInput"
        :rows="isTemplateMode ? 4 : 5"
        :maxlength="charLimit"
        :placeholder="promptPlaceholder"
      ></textarea>
      <div class="form-text d-flex justify-content-between">
        <span>
          {{ promptHelpText }}
        </span>
        <span :class="{ 'text-danger': hasCharLimitError, 'text-warning': nearCharLimit }">
          {{ charCount }} / {{ charLimit }} chars
        </span>
      </div>
      <div class="invalid-feedback" v-if="hasCharLimitError">
        {{ isTemplateMode ? 'System prompt' : 'Initialization context' }} exceeds {{ charLimit }} character limit
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

    <!-- Additional System Prompt for sessions (appended to initialization context) -->
    <div v-if="isSessionMode" class="mb-3">
      <label for="config-additional-prompt" class="form-label">
        Additional Instructions
      </label>
      <textarea
        id="config-additional-prompt"
        class="form-control"
        :class="{ 'is-invalid': additionalPromptExceedsLimit }"
        :value="formData.system_prompt"
        @input="$emit('update:form-data', 'system_prompt', $event.target.value)"
        rows="3"
        :maxlength="6000"
        placeholder="Additional instructions to append to the system prompt..."
      ></textarea>
      <div class="form-text d-flex justify-content-between">
        <span>{{ formData.override_system_prompt ? 'These instructions will be used as the system prompt' : 'These instructions will be appended to the system prompt' }}</span>
        <span :class="{ 'text-danger': additionalPromptExceedsLimit, 'text-warning': additionalPromptNearLimit }">
          {{ additionalPromptCharCount }} / 6000 chars
        </span>
      </div>
      <div class="invalid-feedback" v-if="additionalPromptExceedsLimit">
        Additional instructions exceed 6000 character limit
      </div>
    </div>

    <!-- Sandbox Mode (session only) -->
    <div v-if="isSessionMode" class="mb-3 form-check">
      <input
        type="checkbox"
        class="form-check-input"
        id="config-sandbox"
        :checked="formData.sandbox_enabled"
        @change="$emit('update:form-data', 'sandbox_enabled', $event.target.checked)"
      />
      <label class="form-check-label" for="config-sandbox">
        Enable Sandbox Mode
      </label>
      <div class="form-text" :class="formData.sandbox_enabled ? 'text-info' : 'text-muted'">
        <small>
          {{ formData.sandbox_enabled
            ? 'Sandbox enabled: Session will have OS-level isolation restricting file system and network access.'
            : 'Sandbox mode restricts file system and network access for added security.'
          }}
        </small>
      </div>
    </div>

    <!-- Read-only display of system prompt config (edit-session) -->
    <div v-if="isEditSession && (formData.system_prompt || formData.override_system_prompt)" class="mb-3">
      <label class="form-label">System Prompt Configuration</label>
      <div class="card">
        <div class="card-body">
          <div v-if="formData.override_system_prompt" class="mb-2">
            <span class="badge bg-warning text-dark">Override Mode</span>
            <div class="form-text text-warning mt-1">
              Using custom prompt only (no Claude Code preset)
            </div>
          </div>
          <div v-else class="mb-2">
            <span class="badge bg-info">Append Mode</span>
            <div class="form-text mt-1">
              Custom instructions appended to Claude Code preset
            </div>
          </div>
          <div v-if="formData.system_prompt" class="mt-2">
            <strong class="small">Custom Instructions:</strong>
            <div class="form-control bg-light" style="height: auto; min-height: 60px; white-space: pre-wrap;" readonly>
              {{ formData.system_prompt }}
            </div>
          </div>
        </div>
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
  }
})

const emit = defineEmits(['update:form-data'])

// Computed
const isSessionMode = computed(() => props.mode === 'create-session' || props.mode === 'edit-session')
const isTemplateMode = computed(() => props.mode === 'create-template' || props.mode === 'edit-template')
const isEditSession = computed(() => props.mode === 'edit-session')

// Character limits
const charLimit = computed(() => isTemplateMode.value ? 6000 : 2000)

// Get the correct prompt value based on mode
const promptValue = computed(() => {
  return isTemplateMode.value ? props.formData.system_prompt : props.formData.initialization_context
})

const charCount = computed(() => promptValue.value?.length || 0)

const hasCharLimitError = computed(() => charCount.value > charLimit.value)

const nearCharLimit = computed(() => {
  const threshold = charLimit.value * 0.9
  return charCount.value > threshold && charCount.value <= charLimit.value
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

// Additional prompt (session only) validation
const additionalPromptCharCount = computed(() => props.formData.system_prompt?.length || 0)
const additionalPromptExceedsLimit = computed(() => additionalPromptCharCount.value > 6000)
const additionalPromptNearLimit = computed(() => {
  return additionalPromptCharCount.value > 5500 && additionalPromptCharCount.value <= 6000
})

// Methods
function handlePromptInput(event) {
  const value = event.target.value
  const field = isTemplateMode.value ? 'system_prompt' : 'initialization_context'
  emit('update:form-data', field, value)
}
</script>

<style scoped>
.card {
  background-color: var(--bs-gray-100);
}
</style>
