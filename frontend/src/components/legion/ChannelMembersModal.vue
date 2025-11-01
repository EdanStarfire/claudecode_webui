<template>
  <!-- Bootstrap Modal -->
  <div
    ref="modalElement"
    class="modal fade"
    id="channelMembersModal"
    tabindex="-1"
    aria-labelledby="channelMembersModalLabel"
    aria-hidden="true"
    data-bs-backdrop="true"
    data-bs-keyboard="true"
  >
    <div class="modal-dialog modal-dialog-centered">
      <div class="modal-content">
        <!-- Modal Header -->
        <div class="modal-header">
          <h5 class="modal-title" id="channelMembersModalLabel">Channel Members</h5>
          <button
            type="button"
            class="btn-close"
            data-bs-dismiss="modal"
            aria-label="Close"
          ></button>
        </div>

        <!-- Modal Body -->
        <div class="modal-body">
          <!-- Member Count -->
          <p class="member-count" v-if="members && members.length > 0">
            {{ members.length }} {{ members.length === 1 ? 'member' : 'members' }}
          </p>

          <!-- Member List -->
          <div v-if="members && members.length > 0" class="member-list">
            <div
              v-for="member in members"
              :key="member.session_id"
              class="member-item"
            >
              <div class="member-info">
                <div class="member-name">{{ member.name }}</div>
                <div class="member-role" v-if="member.role">{{ member.role }}</div>
                <div class="member-state">
                  <span
                    class="status-dot"
                    :class="getStatusClass(member.state)"
                    :style="getStatusStyle(member.state)"
                  ></span>
                  <span class="status-text">{{ formatState(member.state) }}</span>
                </div>
              </div>
            </div>
          </div>

          <!-- Empty State -->
          <div v-else class="empty-state text-center py-4">
            <p class="text-muted mb-0">No members in this channel yet.</p>
          </div>
        </div>

        <!-- Modal Footer -->
        <div class="modal-footer">
          <button
            type="button"
            class="btn btn-secondary"
            data-bs-dismiss="modal"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, watch, onMounted, onBeforeUnmount } from 'vue'
import { useUIStore } from '@/stores/ui'

const uiStore = useUIStore()
const modalElement = ref(null)
let modalInstance = null

const channel = ref(null)
const members = ref([])

// Watch for modal show requests from UI store
watch(
  () => uiStore.currentModal,
  (modal) => {
    if (modal?.name === 'channel-members' && modalInstance) {
      channel.value = modal.data?.channel || null
      members.value = modal.data?.members || []
      modalInstance.show()
    }
  }
)

// Status class for blinking animation (processing/starting states)
function getStatusClass(state) {
  if (state === 'processing' || state === 'starting') {
    return 'status-blinking'
  }
  return ''
}

// Status color mapping
function getStatusStyle(state) {
  const colorMap = {
    created: '#6c757d',      // gray
    starting: '#28a745',     // green
    active: '#28a745',       // green
    processing: '#da70d6',   // plum
    paused: '#ffc107',       // yellow/amber
    error: '#dc3545',        // red
    terminated: '#6c757d'    // gray
  }

  return { backgroundColor: colorMap[state] || '#6c757d' }
}

// Format state for display
function formatState(state) {
  if (!state) return 'Unknown'

  // Capitalize first letter and replace underscores with spaces
  return state.charAt(0).toUpperCase() + state.slice(1).replace(/_/g, ' ')
}

// Initialize Bootstrap modal
onMounted(() => {
  if (modalElement.value) {
    const bootstrap = window.bootstrap
    if (bootstrap && bootstrap.Modal) {
      modalInstance = new bootstrap.Modal(modalElement.value)

      // Handle modal hidden event
      modalElement.value.addEventListener('hidden.bs.modal', () => {
        uiStore.hideModal()
        channel.value = null
        members.value = []
      })
    }
  }
})

// Cleanup
onBeforeUnmount(() => {
  if (modalInstance) {
    modalInstance.dispose()
  }
})
</script>

<style scoped>
.member-count {
  margin-bottom: 1rem;
  color: #6c757d;
  font-size: 0.9rem;
}

.member-list {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.member-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0.75rem;
  background-color: #f8f9fa;
  border-radius: 4px;
  border: 1px solid #dee2e6;
}

.member-info {
  flex: 1;
}

.member-name {
  font-weight: 500;
  color: #212529;
  margin-bottom: 0.25rem;
}

.member-role {
  font-size: 0.85rem;
  color: #6c757d;
  margin-bottom: 0.25rem;
}

.member-state {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.85rem;
  color: #6c757d;
}

.status-dot {
  width: 12px;
  height: 12px;
  border-radius: 50%;
  border: 1px solid rgba(0, 0, 0, 0.15);
}

.status-blinking {
  animation: blink 1.5s infinite;
}

@keyframes blink {
  0%, 100% {
    opacity: 1;
  }
  50% {
    opacity: 0.5;
  }
}

.status-text {
  text-transform: capitalize;
}

.empty-state {
  padding: 2rem 1rem;
}
</style>
