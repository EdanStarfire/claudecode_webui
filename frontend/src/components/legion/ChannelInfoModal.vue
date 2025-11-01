<template>
  <!-- Bootstrap Modal -->
  <div
    ref="modalElement"
    class="modal fade"
    id="channelInfoModal"
    tabindex="-1"
    aria-labelledby="channelInfoModalLabel"
    aria-hidden="true"
    data-bs-backdrop="true"
    data-bs-keyboard="true"
  >
    <div class="modal-dialog modal-dialog-centered">
      <div class="modal-content">
        <!-- Modal Header -->
        <div class="modal-header">
          <h5 class="modal-title" id="channelInfoModalLabel">Channel Information</h5>
          <button
            type="button"
            class="btn-close"
            data-bs-dismiss="modal"
            aria-label="Close"
          ></button>
        </div>

        <!-- Modal Body -->
        <div class="modal-body">
          <div v-if="channel">
            <!-- Channel Name -->
            <div class="info-section">
              <label>Channel Name</label>
              <div class="info-value">{{ channel.name }}</div>
            </div>

            <!-- Purpose/Description -->
            <div class="info-section">
              <label>Purpose</label>
              <div class="info-value">{{ channel.purpose || 'No purpose specified' }}</div>
            </div>

            <!-- Description (if different from purpose) -->
            <div class="info-section" v-if="channel.description && channel.description !== channel.purpose">
              <label>Description</label>
              <div class="info-value">{{ channel.description }}</div>
            </div>

            <!-- Created Timestamp -->
            <div class="info-section">
              <label>Created</label>
              <div class="info-value">{{ formatTimestamp(channel.created_at) }}</div>
            </div>

            <!-- Member Count -->
            <div class="info-section">
              <label>Members</label>
              <div class="info-value">{{ channel.member_minion_ids?.length || 0 }} member(s)</div>
            </div>

            <!-- Channel ID (advanced info) -->
            <div class="info-section">
              <label>Channel ID</label>
              <div class="info-value">
                <code style="font-size: 0.85rem;">{{ channel.channel_id }}</code>
              </div>
            </div>
          </div>

          <div v-else class="text-muted text-center py-3">
            No channel data available
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
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import { useUIStore } from '@/stores/ui'

const uiStore = useUIStore()
const modalElement = ref(null)
let modalInstance = null

const channel = ref(null)

// Watch for modal show requests from UI store
watch(
  () => uiStore.currentModal,
  (modal) => {
    if (modal?.name === 'channel-info' && modalInstance) {
      channel.value = modal.data?.channel || null
      modalInstance.show()
    }
  }
)

// Format timestamp for display
function formatTimestamp(timestamp) {
  if (!timestamp) return 'Unknown'

  try {
    const date = new Date(timestamp)
    return date.toLocaleString('en-US', {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit'
    })
  } catch (error) {
    return timestamp
  }
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
.info-section {
  margin-bottom: 1rem;
}

.info-section:last-child {
  margin-bottom: 0;
}

.info-section label {
  display: block;
  font-weight: 600;
  color: #495057;
  margin-bottom: 0.25rem;
  font-size: 0.9rem;
}

.info-value {
  color: #212529;
  padding: 0.5rem;
  background-color: #f8f9fa;
  border-radius: 4px;
  word-wrap: break-word;
}

code {
  background-color: transparent;
  padding: 0;
}
</style>
