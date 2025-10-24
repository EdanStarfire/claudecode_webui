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
                rows="4"
                placeholder="Instructions and context for the minion..."
              ></textarea>
              <div class="form-text">Instructions and context to initialize the minion with</div>
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
  capabilities: []
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

// Methods
function resetForm() {
  formData.value = {
    name: '',
    role: '',
    initialization_context: '',
    capabilities: []
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
      capabilities: capabilities.value
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
