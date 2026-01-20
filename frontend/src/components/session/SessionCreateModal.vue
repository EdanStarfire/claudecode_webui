<template>
  <div
    class="modal fade"
    id="createSessionModal"
    tabindex="-1"
    aria-labelledby="createSessionModalLabel"
    aria-hidden="true"
    ref="modalElement"
  >
    <div class="modal-dialog modal-lg">
      <div class="modal-content">
        <div class="modal-header">
          <h5 class="modal-title" id="createSessionModalLabel">
            Create New Session
            <span v-if="project" class="text-muted small">in {{ project.name }}</span>
          </h5>
          <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
        </div>
        <div class="modal-body">
          <form @submit.prevent="handleSubmit">
            <!-- Session Name -->
            <div class="mb-3">
              <label for="sessionName" class="form-label">Session Name</label>
              <input
                type="text"
                class="form-control"
                id="sessionName"
                v-model="formData.name"
                :class="{ 'is-invalid': errors.name }"
                placeholder="main"
                required
              />
              <div class="invalid-feedback" v-if="errors.name">{{ errors.name }}</div>
            </div>

            <!-- Permission Mode -->
            <div class="mb-3">
              <label for="permissionMode" class="form-label">Permission Mode</label>
              <select
                class="form-select"
                id="permissionMode"
                v-model="formData.permission_mode"
              >
                <option value="default">Default (Prompt for tools not in settings)</option>
                <option value="acceptEdits">Accept Edits (Auto-approve Edit/Write)</option>
                <option value="plan">Plan Mode (Auto-resets after ExitPlanMode)</option>
                <option value="bypassPermissions">⚠️ Bypass Permissions (No prompts - use with caution)</option>
              </select>
              <div class="form-text">
                Controls which tool actions require permission prompts
              </div>
            </div>

            <!-- Model Selection -->
            <div class="mb-3">
              <label for="model" class="form-label">Model</label>
              <select
                class="form-select"
                id="model"
                v-model="formData.model"
              >
                <option value="sonnet">
                  Sonnet 4.5 (Recommended) - Coding & complex agents
                </option>
                <option value="opus">
                  Opus 4.5 - Complex reasoning tasks
                </option>
                <option value="haiku">
                  Haiku 4.5 - Fastest for simple tasks
                </option>
                <option value="opusplan">
                  OpusPlan - Opus planning + Sonnet execution
                </option>
              </select>
              <small class="form-text text-muted" v-if="selectedModelInfo">
                {{ selectedModelInfo.description }}
              </small>
            </div>

            <!-- System Prompt Append -->
            <div class="mb-3">
              <label for="systemPrompt" class="form-label">Additional Instructions (Optional)</label>
              <textarea
                class="form-control"
                id="systemPrompt"
                v-model="formData.system_prompt"
                :class="{ 'is-invalid': systemPromptExceedsLimit }"
                rows="3"
                :maxlength="6000"
                placeholder="Additional instructions to append to the system prompt..."
              ></textarea>
              <div class="form-text d-flex justify-content-between">
                <span>{{ formData.override_system_prompt ? 'These instructions will replace Claude Code\'s default system prompt' : 'These instructions will be appended to Claude Code\'s default system prompt' }}</span>
                <span :class="{ 'text-danger': systemPromptExceedsLimit, 'text-warning': systemPromptNearLimit }">
                  {{ systemPromptCharCount }} / 6000 chars
                </span>
              </div>
              <div class="invalid-feedback" v-if="systemPromptExceedsLimit">
                System prompt exceeds 6000 character limit (Windows command-line constraint)
              </div>
            </div>

            <!-- Override System Prompt -->
            <div class="mb-3 form-check">
              <input
                type="checkbox"
                class="form-check-input"
                id="overrideSystemPrompt"
                v-model="formData.override_system_prompt"
              />
              <label class="form-check-label" for="overrideSystemPrompt">
                Override Claude Code preset (use custom prompt only)
              </label>
              <div class="form-text text-warning" v-if="formData.override_system_prompt">
                <small>⚠️ Override mode may cause unexpected behaviors. Only custom instructions will be used (no Claude Code preset).</small>
              </div>
            </div>

            <!-- Tool Allowlist -->
            <div class="mb-3">
              <label for="allowed-tools" class="form-label">Allowed Tools (Optional)</label>
              <input
                type="text"
                class="form-control"
                id="allowed-tools"
                v-model="formData.allowed_tools"
                placeholder="bash, read, edit, write, glob, grep, webfetch, websearch, task, todo"
              />
              <div class="form-text">
                Comma-separated list of allowed tools. Leave empty to allow all tools.
              </div>
            </div>

            <!-- Start Immediately -->
            <div class="mb-3 form-check">
              <input
                type="checkbox"
                class="form-check-input"
                id="startImmediately"
                v-model="formData.startImmediately"
              />
              <label class="form-check-label" for="startImmediately">
                Start session immediately after creation
              </label>
            </div>

            <div v-if="errorMessage" class="alert alert-danger" role="alert">
              {{ errorMessage }}
            </div>
          </form>
        </div>
        <div class="modal-footer">
          <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button>
          <button
            type="button"
            class="btn btn-primary"
            @click="handleSubmit"
            :disabled="isSubmitting"
          >
            <span v-if="isSubmitting" class="spinner-border spinner-border-sm me-2"></span>
            {{ isSubmitting ? 'Creating...' : 'Create Session' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useProjectStore } from '@/stores/project'
import { useSessionStore } from '@/stores/session'
import { useUIStore } from '@/stores/ui'

const router = useRouter()
const projectStore = useProjectStore()
const sessionStore = useSessionStore()
const uiStore = useUIStore()

// State
const project = ref(null)
const formData = ref({
  name: '',
  permission_mode: 'default',
  model: 'sonnet',
  system_prompt: '',
  override_system_prompt: false,
  allowed_tools: '',
  startImmediately: true
})
const errors = ref({
  name: ''
})
const isSubmitting = ref(false)
const errorMessage = ref('')
const modalElement = ref(null)
let modalInstance = null

// Model information for descriptions
const modelInfo = {
  'sonnet': {
    description: 'Smart model for coding and complex agents - best balance of speed & capability'
  },
  'opus': {
    description: 'Most capable model for complex reasoning, advanced analysis, and sophisticated tasks'
  },
  'haiku': {
    description: 'Optimized for speed and cost-efficiency on straightforward tasks'
  },
  'opusplan': {
    description: 'Uses Opus for planning phase, Sonnet for execution - best of both worlds'
  }
}

// Computed - System prompt character limits
const systemPromptCharCount = computed(() => {
  return formData.value.system_prompt.length
})

const systemPromptExceedsLimit = computed(() => {
  return systemPromptCharCount.value > 6000
})

const systemPromptNearLimit = computed(() => {
  return systemPromptCharCount.value > 5500 && systemPromptCharCount.value <= 6000
})

// Computed - Selected model info
const selectedModelInfo = computed(() => {
  return modelInfo[formData.value.model] || null
})

// Validate form
function validate() {
  errors.value = {}
  let isValid = true

  if (!formData.value.name.trim()) {
    errors.value.name = 'Session name is required'
    isValid = false
  }

  if (systemPromptExceedsLimit.value) {
    isValid = false
  }

  return isValid
}

// Handle form submission
async function handleSubmit() {
  if (!validate() || !project.value) {
    return
  }

  isSubmitting.value = true
  errorMessage.value = ''

  try {
    // Parse allowed_tools from comma-separated string
    const toolsList = formData.value.allowed_tools
      .split(',')
      .map(t => t.trim())
      .filter(t => t.length > 0)

    const sessionData = {
      name: formData.value.name,
      permission_mode: formData.value.permission_mode,
      model: formData.value.model,
      system_prompt: formData.value.system_prompt.trim() || null,
      override_system_prompt: formData.value.override_system_prompt,
      allowed_tools: toolsList
    }

    const session = await sessionStore.createSession(project.value.project_id, sessionData)

    // Start session if requested
    if (formData.value.startImmediately) {
      await sessionStore.startSession(session.session_id)
    }

    // Navigate to session
    router.push(`/session/${session.session_id}`)

    // Close modal
    if (modalInstance) {
      modalInstance.hide()
    }

    // Reset form
    resetForm()
  } catch (error) {
    console.error('Failed to create session:', error)
    errorMessage.value = error.message || 'Failed to create session. Please try again.'
  } finally {
    isSubmitting.value = false
  }
}

// Reset form
function resetForm() {
  formData.value = {
    name: '',
    permission_mode: 'default',
    model: 'sonnet',
    system_prompt: '',
    override_system_prompt: false,
    allowed_tools: '',
    startImmediately: true
  }
  errors.value = {}
  errorMessage.value = ''
}

// Handle modal shown event
function onModalShown() {
  // Focus name input
  const nameInput = document.getElementById('sessionName')
  if (nameInput) {
    nameInput.focus()
  }
}

// Handle modal hidden event
function onModalHidden() {
  resetForm()
  project.value = null
  uiStore.hideModal()
}

// Watch for modal show requests from UI store
watch(
  () => uiStore.currentModal,
  (modal) => {
    if (modal?.name === 'create-session' && modalInstance) {
      const data = modal.data || {}
      project.value = data.project
      modalInstance.show()
    }
  }
)

// Initialize Bootstrap modal
onMounted(() => {
  if (modalElement.value) {
    import('bootstrap/js/dist/modal').then(({ default: Modal }) => {
      modalInstance = new Modal(modalElement.value)

      modalElement.value.addEventListener('shown.bs.modal', onModalShown)
      modalElement.value.addEventListener('hidden.bs.modal', onModalHidden)
    })
  }
})

// Cleanup
onUnmounted(() => {
  if (modalElement.value) {
    modalElement.value.removeEventListener('shown.bs.modal', onModalShown)
    modalElement.value.removeEventListener('hidden.bs.modal', onModalHidden)
  }
  if (modalInstance) {
    modalInstance.dispose()
  }
})
</script>

<style scoped>
.spinner-border-sm {
  width: 1rem;
  height: 1rem;
  border-width: 0.15em;
}
</style>
