<template>
  <div
    ref="modalElement"
    class="modal fade"
    tabindex="-1"
    aria-labelledby="createMinionModalLabel"
    aria-hidden="true"
  >
    <div class="modal-dialog modal-dialog-centered">
      <div class="modal-content">
        <div class="modal-header">
          <h5 id="createMinionModalLabel" class="modal-title">Create Minion</h5>
          <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
        </div>
        <div class="modal-body">
          <form @submit.prevent="createMinion">
            <div class="mb-3">
              <label for="minion-name" class="form-label">
                Name <span class="text-danger">*</span>
              </label>
              <input
                id="minion-name"
                v-model="formData.name"
                type="text"
                class="form-control"
                required
                placeholder="main"
                pattern="[^\s]+"
                title="Minion name must be a single word with no spaces"
              />
              <div class="form-text">Must be a single word (no spaces) for #nametag matching</div>
            </div>

            <div class="mb-3">
              <label for="minion-role" class="form-label">Role</label>
              <input
                id="minion-role"
                v-model="formData.role"
                type="text"
                class="form-control"
                placeholder="e.g., Code Expert, Testing Specialist"
              />
              <div class="form-text">Optional role description for the minion</div>
            </div>

            <div class="mb-3">
              <label for="minion-context" class="form-label">Initialization Context</label>
              <textarea
                id="minion-context"
                v-model="formData.initialization_context"
                class="form-control"
                :class="{ 'is-invalid': initContextExceedsLimit }"
                rows="4"
                :maxlength="2000"
                placeholder="Instructions and context for the minion..."
              ></textarea>
              <div class="form-text d-flex justify-content-between">
                <span>{{ formData.override_system_prompt ? 'This context will replace Claude Code\'s preset and legion guide' : 'This context will be appended to legion guide and Claude Code\'s preset' }}</span>
                <span :class="{ 'text-danger': initContextExceedsLimit, 'text-warning': initContextNearLimit }">
                  {{ initContextCharCount }} / 2000 chars
                </span>
              </div>
              <div class="invalid-feedback" v-if="initContextExceedsLimit">
                Initialization context exceeds 2000 character limit (Windows command-line constraint)
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
                Override Claude Code preset (use custom context only)
              </label>
              <div class="form-text text-warning" v-if="formData.override_system_prompt">
                <small>⚠️ Override mode may cause unexpected behaviors. Only custom context will be used (no legion guide or Claude Code preset).</small>
              </div>
            </div>

            <div class="mb-3">
              <label for="minion-capabilities" class="form-label">Capabilities</label>
              <input
                id="minion-capabilities"
                v-model="capabilitiesInput"
                type="text"
                class="form-control"
                placeholder="e.g., python, testing, debugging"
              />
              <div class="form-text">Comma-separated list of capability keywords (optional)</div>
            </div>

            <!-- Permission Mode -->
            <div class="mb-3">
              <label for="permission-mode" class="form-label">Permission Mode</label>
              <select
                class="form-select"
                id="permission-mode"
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

            <!-- Allowed Tools -->
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

            <div v-if="errorMessage" class="alert alert-danger">
              {{ errorMessage }}
            </div>
          </form>
        </div>
        <div class="modal-footer">
          <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button>
          <button
            type="button"
            class="btn btn-primary"
            :disabled="!formData.name.trim() || isCreating"
            @click="createMinion"
          >
            <span v-if="isCreating" class="spinner-border spinner-border-sm me-2"></span>
            {{ isCreating ? 'Creating...' : 'Create Minion' }}
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
import { api } from '@/utils/api'

const router = useRouter()
const projectStore = useProjectStore()
const sessionStore = useSessionStore()
const uiStore = useUIStore()

// State
const modalElement = ref(null)
let modalInstance = null

const legionId = ref(null)
const formData = ref({
  name: '',
  role: '',
  initialization_context: '',
  override_system_prompt: false,
  capabilities: [],
  permission_mode: 'default',
  allowed_tools: ''
})
const capabilitiesInput = ref('')
const errorMessage = ref('')
const isCreating = ref(false)

// Computed
const capabilities = computed(() => {
  if (!capabilitiesInput.value.trim()) return []
  return capabilitiesInput.value
    .split(',')
    .map(c => c.trim())
    .filter(c => c.length > 0)
})

const allowedTools = computed(() => {
  if (!formData.value.allowed_tools.trim()) return null
  return formData.value.allowed_tools
    .split(',')
    .map(t => t.trim())
    .filter(t => t.length > 0)
})

// Computed - Initialization context character limits
const initContextCharCount = computed(() => {
  return formData.value.initialization_context.length
})

const initContextExceedsLimit = computed(() => {
  return initContextCharCount.value > 2000
})

const initContextNearLimit = computed(() => {
  return initContextCharCount.value > 1800 && initContextCharCount.value <= 2000
})

// Methods
function resetForm() {
  formData.value = {
    name: '',
    role: '',
    initialization_context: '',
    override_system_prompt: false,
    capabilities: [],
    permission_mode: 'default',
    allowed_tools: ''
  }
  capabilitiesInput.value = ''
  errorMessage.value = ''
  isCreating.value = false
}

function onModalHidden() {
  resetForm()
  uiStore.hideModal()
}

async function createMinion() {
  if (!formData.value.name.trim()) {
    errorMessage.value = 'Minion name is required'
    return
  }

  // Validate that name has no spaces
  if (formData.value.name.includes(' ')) {
    errorMessage.value = 'Minion name must be a single word with no spaces (for #nametag matching)'
    return
  }

  // Validate initialization context length
  if (initContextExceedsLimit.value) {
    errorMessage.value = 'Initialization context exceeds 2000 character limit'
    return
  }

  if (!legionId.value) {
    errorMessage.value = 'Legion ID is missing'
    return
  }

  isCreating.value = true
  errorMessage.value = ''

  try {
    const payload = {
      name: formData.value.name.trim(),
      role: formData.value.role.trim(),
      initialization_context: formData.value.initialization_context.trim(),
      override_system_prompt: formData.value.override_system_prompt,
      capabilities: capabilities.value,
      permission_mode: formData.value.permission_mode,
      allowed_tools: allowedTools.value
    }

    const response = await api.post(`/api/legions/${legionId.value}/minions`, payload)

    if (response.success && response.minion_id) {
      // Refresh projects to get updated session list
      await projectStore.fetchProjects()
      await sessionStore.fetchSessions()

      // Close modal
      if (modalInstance) {
        modalInstance.hide()
      }

      // Navigate to the new minion in spy mode
      router.push(`/spy/${legionId.value}/${response.minion_id}`)
    } else {
      errorMessage.value = 'Failed to create minion'
    }
  } catch (error) {
    console.error('Failed to create minion:', error)
    errorMessage.value = error.response?.data?.detail || 'Failed to create minion'
  } finally {
    isCreating.value = false
  }
}

// Watch for modal show requests from UI store
watch(
  () => uiStore.currentModal,
  (modal) => {
    if (modal?.name === 'create-minion' && modalInstance) {
      const data = modal.data || {}
      legionId.value = data.project?.project_id || null
      resetForm()
      modalInstance.show()
    }
  }
)

// Initialize Bootstrap modal
onMounted(() => {
  if (modalElement.value) {
    import('bootstrap/js/dist/modal').then((module) => {
      const Modal = module.default
      modalInstance = new Modal(modalElement.value)
      modalElement.value.addEventListener('hidden.bs.modal', onModalHidden)
    })
  }
})

onUnmounted(() => {
  if (modalElement.value) {
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
