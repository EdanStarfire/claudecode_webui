<template>
  <div
    class="modal fade"
    id="fleetControlModal"
    tabindex="-1"
    aria-labelledby="fleetControlModalLabel"
    aria-hidden="true"
    ref="modalElement"
  >
    <div class="modal-dialog modal-dialog-centered">
      <div class="modal-content">
        <div class="modal-header">
          <h5 class="modal-title" id="fleetControlModalLabel">
            {{ modalTitle }}
          </h5>
          <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
        </div>
        <div class="modal-body">
          <!-- Loading State -->
          <div v-if="isOperating" class="text-center py-4">
            <div class="spinner-border text-primary mb-3" role="status">
              <span class="visually-hidden">Processing...</span>
            </div>
            <p class="text-muted">{{ loadingMessage }}</p>
          </div>

          <!-- Result View (Success/Error) -->
          <div v-else-if="resultView">
            <div
              class="alert mb-3"
              :class="resultView.type === 'success' ? 'alert-success' : 'alert-danger'"
            >
              <strong>{{ resultView.title }}</strong>
            </div>
            <p class="text-muted">{{ resultView.message }}</p>
          </div>

          <!-- Halt Confirmation -->
          <div v-else-if="confirmationView === 'halt'">
            <div class="alert alert-warning mb-3">
              <strong>⚠️ Emergency Halt</strong>
            </div>
            <p class="text-muted">
              Are you sure you want to halt all <strong>{{ activeMinionsCount }}</strong> active minion(s) in this legion?
            </p>
            <p class="text-muted">
              This will interrupt their current processing. Minions will remain in ACTIVE state but will stop processing.
            </p>
          </div>

          <!-- Resume Confirmation -->
          <div v-else-if="confirmationView === 'resume'">
            <div class="alert alert-info mb-3">
              <strong>Resume All Minions</strong>
            </div>
            <p class="text-muted">
              This will send "continue" to all <strong>{{ totalMinionsCount }}</strong> minion(s) in this legion.
            </p>
            <p class="text-muted">
              Minions will resume processing from where they were interrupted.
            </p>
          </div>
        </div>
        <div class="modal-footer">
          <!-- Result View Buttons -->
          <template v-if="resultView">
            <button type="button" class="btn btn-primary" data-bs-dismiss="modal">OK</button>
          </template>

          <!-- Confirmation View Buttons -->
          <template v-else-if="confirmationView && !isOperating">
            <button type="button" class="btn btn-secondary" @click="cancelConfirmation">Cancel</button>
            <button
              type="button"
              class="btn"
              :class="confirmationView === 'halt' ? 'btn-warning' : 'btn-success'"
              @click="executeConfirmation"
            >
              {{ confirmationView === 'halt' ? 'Halt All' : 'Resume All' }}
            </button>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useUIStore } from '@/stores/ui'
import { useLegionStore } from '@/stores/legion'
import { Modal } from 'bootstrap'

const uiStore = useUIStore()
const legionStore = useLegionStore()

// State
const isOperating = ref(false)
const loadingMessage = ref('')
const confirmationView = ref(null) // 'halt', 'resume', or null
const resultView = ref(null) // { type: 'success'|'error', title: string, message: string }
const modalElement = ref(null)
let modalInstance = null

// Data passed from parent
const legionId = ref(null)
const activeMinionsCount = ref(0)
const totalMinionsCount = ref(0)

// Computed
const modalTitle = computed(() => {
  if (resultView.value) {
    return resultView.value.type === 'success' ? 'Success' : 'Error'
  }
  if (confirmationView.value === 'halt') return 'Emergency Halt All Minions'
  if (confirmationView.value === 'resume') return 'Resume All Minions'
  return 'Fleet Control'
})

// Initialize Bootstrap modal
onMounted(() => {
  if (modalElement.value) {
    modalInstance = new Modal(modalElement.value)

    // Reset state when modal is hidden
    modalElement.value.addEventListener('hidden.bs.modal', () => {
      confirmationView.value = null
      resultView.value = null
      isOperating.value = false
    })
  }
})

onUnmounted(() => {
  if (modalInstance) {
    modalInstance.dispose()
  }
})

// Watch for modal triggers from UI store
watch(
  () => uiStore.currentModal,
  (newModal) => {
    if (!newModal || newModal.name !== 'fleet-control') return

    const data = newModal.data
    if (!data) return

    // Set up modal data
    legionId.value = data.legionId
    activeMinionsCount.value = data.activeMinionsCount || 0
    totalMinionsCount.value = data.totalMinionsCount || 0
    confirmationView.value = data.operation // 'halt' or 'resume'
    resultView.value = null

    // Show modal
    if (modalInstance) {
      modalInstance.show()
    }
  }
)

// Cancel confirmation and close modal
function cancelConfirmation() {
  confirmationView.value = null
  if (modalInstance) {
    modalInstance.hide()
  }
}

// Execute the pending confirmation action
async function executeConfirmation() {
  if (confirmationView.value === 'halt') {
    await executeHaltAll()
  } else if (confirmationView.value === 'resume') {
    await executeResumeAll()
  }
}

// Execute halt all operation
async function executeHaltAll() {
  isOperating.value = true
  loadingMessage.value = 'Halting all minions...'

  try {
    const result = await legionStore.haltAll(legionId.value)

    // Show success result
    const failureMsg = result.failed_minions && result.failed_minions.length > 0
      ? ` Failed to halt ${result.failed_minions.length} minion(s).`
      : ''

    resultView.value = {
      type: 'success',
      title: 'Halt Complete',
      message: `Halted ${result.halted_count} of ${result.total_minions} minion(s).${failureMsg}`
    }
  } catch (error) {
    console.error('Failed to halt all minions:', error)
    resultView.value = {
      type: 'error',
      title: 'Halt Failed',
      message: `Failed to halt minions: ${error.message || 'Unknown error'}`
    }
  } finally {
    isOperating.value = false
    confirmationView.value = null
  }
}

// Execute resume all operation
async function executeResumeAll() {
  isOperating.value = true
  loadingMessage.value = 'Resuming all minions...'

  try {
    const result = await legionStore.resumeAll(legionId.value)

    // Show success result
    const failureMsg = result.failed_minions && result.failed_minions.length > 0
      ? ` Failed to resume ${result.failed_minions.length} minion(s).`
      : ''

    resultView.value = {
      type: 'success',
      title: 'Resume Complete',
      message: `Resumed ${result.resumed_count} of ${result.total_minions} minion(s).${failureMsg}`
    }
  } catch (error) {
    console.error('Failed to resume all minions:', error)
    resultView.value = {
      type: 'error',
      title: 'Resume Failed',
      message: `Failed to resume minions: ${error.message || 'Unknown error'}`
    }
  } finally {
    isOperating.value = false
    confirmationView.value = null
  }
}
</script>

<style scoped>
/* Modal styles match SessionManageModal */
.modal-body {
  min-height: 150px;
}

.alert {
  margin-bottom: 1rem;
}
</style>
