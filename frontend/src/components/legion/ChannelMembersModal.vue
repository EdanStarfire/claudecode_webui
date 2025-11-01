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
          <!-- Add Member Section -->
          <div class="mb-3">
            <button
              class="btn btn-sm btn-primary"
              @click="showAddMemberSelector = !showAddMemberSelector"
              :disabled="isAddingMember || nonMemberMinions.length === 0"
            >
              ➕ Add Member
            </button>
            <small v-if="nonMemberMinions.length === 0" class="text-muted ms-2">
              (All minions are already members)
            </small>

            <!-- Add Member Selector (shown when button clicked) -->
            <div v-if="showAddMemberSelector && nonMemberMinions.length > 0" class="mt-2">
              <select
                v-model="selectedMinionToAdd"
                class="form-select form-select-sm"
                @change="onAddMember"
                :disabled="isAddingMember"
              >
                <option value="">-- Select minion to add --</option>
                <option
                  v-for="minion in nonMemberMinions"
                  :key="minion.session_id"
                  :value="minion.session_id"
                >
                  {{ minion.name }} {{ minion.role ? `(${minion.role})` : '(No role)' }}
                </option>
              </select>
              <div v-if="isAddingMember" class="text-muted small mt-1">
                <span class="spinner-border spinner-border-sm me-1"></span>
                Adding member...
              </div>
            </div>
          </div>

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
              <button
                class="btn btn-sm btn-outline-danger"
                @click="onRemoveMember(member.session_id)"
                :disabled="isRemovingMember"
                title="Remove from channel"
              >
                🗑️
              </button>
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
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import { useUIStore } from '@/stores/ui'
import { useLegionStore } from '@/stores/legion'
import { useSessionStore } from '@/stores/session'
import { useProjectStore } from '@/stores/project'

const uiStore = useUIStore()
const legionStore = useLegionStore()
const sessionStore = useSessionStore()
const projectStore = useProjectStore()

const modalElement = ref(null)
let modalInstance = null

const channel = ref(null)
const members = ref([])
const legionId = ref(null)
const channelId = ref(null)

const selectedMinionToAdd = ref('')
const showAddMemberSelector = ref(false)
const isAddingMember = ref(false)
const isRemovingMember = ref(false)

// Computed property for non-member minions
const nonMemberMinions = computed(() => {
  if (!legionId.value) return []

  // Get all minions in the legion
  const legion = projectStore.projects.get(legionId.value)
  if (!legion || !legion.session_ids) return []

  // Get all minion session objects
  const allMinions = legion.session_ids
    .map(sessionId => sessionStore.sessions.get(sessionId))
    .filter(Boolean)

  // Filter out members that are already in the channel
  const memberIds = new Set(members.value.map(m => m.session_id))
  return allMinions.filter(minion => !memberIds.has(minion.session_id))
})

// Watch for modal show requests from UI store
watch(
  () => uiStore.currentModal,
  (modal) => {
    if (modal?.name === 'channel-members' && modalInstance) {
      channel.value = modal.data?.channel || null
      members.value = modal.data?.members || []
      legionId.value = modal.data?.legionId || null
      channelId.value = modal.data?.channelId || null
      showAddMemberSelector.value = false
      selectedMinionToAdd.value = ''
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

// Add member to channel
async function onAddMember() {
  if (!selectedMinionToAdd.value || !channelId.value) return

  isAddingMember.value = true
  try {
    await legionStore.addMemberToChannel(channelId.value, selectedMinionToAdd.value)

    // Reload channel details to get updated member list
    const updatedChannel = await legionStore.loadChannelDetails(channelId.value)

    // Update local members list from the updated channel
    if (updatedChannel?.member_minion_ids) {
      members.value = updatedChannel.member_minion_ids
        .map(minionId => sessionStore.sessions.get(minionId))
        .filter(Boolean)
    }

    // Reset selector
    selectedMinionToAdd.value = ''
    showAddMemberSelector.value = false
  } catch (error) {
    console.error('Failed to add member:', error)
    alert(`Failed to add member: ${error.message}`)
  } finally {
    isAddingMember.value = false
  }
}

// Remove member from channel
async function onRemoveMember(minionId) {
  if (!minionId || !channelId.value) return

  const member = members.value.find(m => m.session_id === minionId)
  if (!confirm(`Remove ${member?.name || 'this member'} from the channel?`)) {
    return
  }

  isRemovingMember.value = true
  try {
    await legionStore.removeMemberFromChannel(channelId.value, minionId)

    // Reload channel details to get updated member list
    const updatedChannel = await legionStore.loadChannelDetails(channelId.value)

    // Update local members list from the updated channel
    if (updatedChannel?.member_minion_ids) {
      members.value = updatedChannel.member_minion_ids
        .map(minionId => sessionStore.sessions.get(minionId))
        .filter(Boolean)
    }
  } catch (error) {
    console.error('Failed to remove member:', error)
    alert(`Failed to remove member: ${error.message}`)
  } finally {
    isRemovingMember.value = false
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
