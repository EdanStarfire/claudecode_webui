<template>
  <div
    class="modal fade"
    id="minionViewModal"
    tabindex="-1"
    aria-labelledby="minionViewModalLabel"
    aria-hidden="true"
    ref="modalElement"
  >
    <div class="modal-dialog modal-dialog-centered modal-lg">
      <div class="modal-content">
        <div class="modal-header">
          <h5 class="modal-title" id="minionViewModalLabel">Minion Details</h5>
          <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
        </div>
        <div class="modal-body">
          <div v-if="!session" class="text-center text-muted py-4">
            No minion data available
          </div>

          <div v-else>
            <!-- Minion Name -->
            <div class="mb-3">
              <h6 class="text-muted">Name</h6>
              <div>{{ session.name || 'Unnamed Minion' }}</div>
            </div>

            <!-- Session ID -->
            <div class="mb-3">
              <h6 class="text-muted">Session ID</h6>
              <div class="font-monospace small">{{ session.session_id }}</div>
            </div>

            <!-- Role -->
            <div class="mb-3">
              <h6 class="text-muted">Role</h6>
              <div>{{ session.role || 'Not specified' }}</div>
            </div>

            <!-- Current State -->
            <div class="mb-3">
              <h6 class="text-muted">Current State</h6>
              <div>
                <span
                  class="badge"
                  :class="getStateBadgeClass(session.state)"
                >
                  {{ session.state }}
                </span>
              </div>
            </div>

            <!-- Capabilities -->
            <div class="mb-3">
              <h6 class="text-muted">Capabilities</h6>
              <div v-if="session.capabilities && session.capabilities.length > 0">
                <div class="d-flex flex-wrap gap-1">
                  <span
                    v-for="capability in session.capabilities"
                    :key="capability"
                    class="badge bg-secondary"
                  >
                    {{ capability }}
                  </span>
                </div>
              </div>
              <div v-else class="text-muted small">None</div>
            </div>

            <!-- Initialization Context -->
            <div class="mb-3">
              <h6 class="text-muted">Initialization Context</h6>
              <div
                v-if="session.initialization_context"
                class="bg-light p-2 rounded small"
                style="max-height: 200px; overflow-y: auto; white-space: pre-wrap;"
              >{{ session.initialization_context }}</div>
              <div v-else class="text-muted small">Not specified</div>
            </div>

            <!-- Overseer Status -->
            <div class="mb-3">
              <h6 class="text-muted">Overseer</h6>
              <div>
                <span v-if="session.is_overseer" class="badge bg-info">Yes</span>
                <span v-else class="badge bg-secondary">No</span>
              </div>
            </div>

            <!-- Parent Overseer -->
            <div class="mb-3">
              <h6 class="text-muted">Parent Overseer</h6>
              <div v-if="session.parent_overseer_id" class="font-monospace small">
                {{ session.parent_overseer_id }}
              </div>
              <div v-else class="text-muted small">
                {{ session.is_minion ? 'Root overseer (user-created)' : 'N/A' }}
              </div>
            </div>

            <!-- Child Minions -->
            <div v-if="session.is_overseer" class="mb-3">
              <h6 class="text-muted">Child Minions</h6>
              <div v-if="session.child_minion_ids && session.child_minion_ids.length > 0">
                <ul class="list-unstyled mb-0">
                  <li
                    v-for="childId in session.child_minion_ids"
                    :key="childId"
                    class="font-monospace small"
                  >
                    {{ childId }}
                  </li>
                </ul>
              </div>
              <div v-else class="text-muted small">No children</div>
            </div>

            <!-- Channel Memberships -->
            <div class="mb-3">
              <h6 class="text-muted">Channel Memberships</h6>
              <div v-if="session.channel_ids && session.channel_ids.length > 0">
                <div class="d-flex flex-wrap gap-1">
                  <span
                    v-for="channelId in session.channel_ids"
                    :key="channelId"
                    class="badge bg-info"
                  >
                    {{ channelId }}
                  </span>
                </div>
              </div>
              <div v-else class="text-muted small">No channels</div>
            </div>

            <!-- Created Timestamp -->
            <div class="mb-3">
              <h6 class="text-muted">Created</h6>
              <div class="small">{{ formatTimestamp(session.created_at) }}</div>
            </div>

            <!-- Last Active Timestamp -->
            <div class="mb-3">
              <h6 class="text-muted">Last Active</h6>
              <div class="small">{{ formatTimestamp(session.updated_at) }}</div>
            </div>
          </div>
        </div>
        <div class="modal-footer">
          <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Close</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted, watch } from 'vue'
import { useUIStore } from '@/stores/ui'

const uiStore = useUIStore()

// State
const session = ref(null)
const modalElement = ref(null)
let modalInstance = null

// Get badge class for session state
function getStateBadgeClass(state) {
  const classMap = {
    'created': 'bg-secondary',
    'starting': 'bg-info',
    'active': 'bg-success',
    'paused': 'bg-warning',
    'terminating': 'bg-warning',
    'terminated': 'bg-secondary',
    'error': 'bg-danger'
  }
  return classMap[state] || 'bg-secondary'
}

// Format timestamp for display
function formatTimestamp(timestamp) {
  if (!timestamp) return 'N/A'

  try {
    const date = new Date(timestamp)

    // Check if valid date
    if (isNaN(date.getTime())) return 'Invalid date'

    // Format as: Oct 27, 2025 2:30 PM
    return date.toLocaleString('en-US', {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
      hour12: true
    })
  } catch (error) {
    console.error('Error formatting timestamp:', error)
    return 'Invalid date'
  }
}

// Reset state
function resetState() {
  session.value = null
}

// Handle modal hidden event
function onModalHidden() {
  resetState()
  uiStore.hideModal()
}

// Watch for modal show requests from UI store
watch(
  () => uiStore.currentModal,
  (modal) => {
    if (modal?.name === 'minion-view' && modalInstance) {
      const data = modal.data || {}
      session.value = data.session
      modalInstance.show()
    }
  }
)

// Initialize Bootstrap modal
onMounted(() => {
  if (modalElement.value) {
    import('bootstrap/js/dist/modal').then(({ default: Modal }) => {
      modalInstance = new Modal(modalElement.value)
      modalElement.value.addEventListener('hidden.bs.modal', onModalHidden)
    })
  }
})

// Cleanup
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
.badge {
  font-weight: normal;
}

.list-unstyled {
  padding-left: 0;
}

.list-unstyled li {
  padding: 0.25rem 0;
}
</style>
