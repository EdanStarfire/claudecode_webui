<template>
  <div
    class="modal fade"
    id="channelDetailModal"
    tabindex="-1"
    aria-labelledby="channelDetailModalLabel"
    aria-hidden="true"
    ref="modalElement"
  >
    <div class="modal-dialog modal-dialog-centered modal-xl">
      <div class="modal-content">
        <div class="modal-header">
          <h5 class="modal-title" id="channelDetailModalLabel">
            💬 {{ channel?.name || 'Channel Details' }}
          </h5>
          <button type="button" class="btn-close" @click="closeModal" aria-label="Close"></button>
        </div>
        <div class="modal-body">
          <div v-if="loading" class="text-center text-muted py-4">
            <div class="spinner-border spinner-border-sm" role="status">
              <span class="visually-hidden">Loading...</span>
            </div>
            <div class="mt-2">Loading channel details...</div>
          </div>

          <div v-else-if="!channel" class="text-center text-muted py-4">
            Channel not found
          </div>

          <div v-else>
            <!-- Tabs -->
            <ul class="nav nav-tabs" id="channelDetailTabs" role="tablist">
              <li class="nav-item" role="presentation">
                <button
                  class="nav-link active"
                  id="overview-tab"
                  data-bs-toggle="tab"
                  data-bs-target="#overview"
                  type="button"
                  role="tab"
                  aria-controls="overview"
                  aria-selected="true"
                >
                  Overview
                </button>
              </li>
              <li class="nav-item" role="presentation">
                <button
                  class="nav-link"
                  id="members-tab"
                  data-bs-toggle="tab"
                  data-bs-target="#members"
                  type="button"
                  role="tab"
                  aria-controls="members"
                  aria-selected="false"
                >
                  Members ({{ memberCount }})
                </button>
              </li>
              <li class="nav-item" role="presentation">
                <button
                  class="nav-link"
                  id="comms-tab"
                  data-bs-toggle="tab"
                  data-bs-target="#comms"
                  type="button"
                  role="tab"
                  aria-controls="comms"
                  aria-selected="false"
                  @click="loadCommsIfNeeded"
                >
                  Comms ({{ commsCount }})
                </button>
              </li>
            </ul>

            <!-- Tab Content -->
            <div class="tab-content mt-3" id="channelDetailTabContent">
              <!-- Overview Tab -->
              <div
                class="tab-pane fade show active"
                id="overview"
                role="tabpanel"
                aria-labelledby="overview-tab"
              >
                <div class="mb-3">
                  <h6 class="text-muted">Channel ID</h6>
                  <div class="font-monospace small">{{ channel.channel_id }}</div>
                </div>

                <div class="mb-3">
                  <h6 class="text-muted">Name</h6>
                  <div>{{ channel.name }}</div>
                </div>

                <div class="mb-3">
                  <h6 class="text-muted">Purpose</h6>
                  <div>{{ channel.purpose || 'No purpose specified' }}</div>
                </div>

                <div class="mb-3">
                  <h6 class="text-muted">Description</h6>
                  <div>{{ channel.description || 'No description provided' }}</div>
                </div>

                <div class="mb-3">
                  <h6 class="text-muted">Created</h6>
                  <div>{{ formatDate(channel.created_at) }}</div>
                </div>
              </div>

              <!-- Members Tab -->
              <div
                class="tab-pane fade"
                id="members"
                role="tabpanel"
                aria-labelledby="members-tab"
              >
                <!-- Add Member Section -->
                <div class="mb-3">
                  <div class="d-flex align-items-center gap-2">
                    <button
                      class="btn btn-sm btn-primary"
                      @click="showAddMemberSelector = !showAddMemberSelector"
                    >
                      ➕ Add Member
                    </button>
                  </div>

                  <!-- Add Member Selector (collapsible) -->
                  <div v-if="showAddMemberSelector" class="mt-2">
                    <select
                      v-model="selectedMinionToAdd"
                      class="form-select form-select-sm"
                      @change="onAddMember"
                    >
                      <option value="">-- Select Minion to Add --</option>
                      <option
                        v-for="minion in nonMemberMinions"
                        :key="minion.session_id"
                        :value="minion.session_id"
                      >
                        {{ minion.name }} ({{ minion.role || 'No role' }})
                      </option>
                    </select>
                  </div>
                </div>

                <!-- Empty state -->
                <div v-if="members.length === 0" class="text-center text-muted py-3">
                  No members in this channel yet
                </div>

                <!-- Member list -->
                <div v-else class="list-group">
                  <div
                    v-for="member in members"
                    :key="member.session_id"
                    class="list-group-item d-flex align-items-center justify-content-between"
                  >
                    <div class="d-flex align-items-center gap-2">
                      <!-- Status dot -->
                      <span
                        class="status-dot"
                        :class="getStatusClass(member)"
                        :style="getStatusStyle(member)"
                        :title="member.state"
                      ></span>
                      <div>
                        <div class="fw-semibold">{{ member.name }}</div>
                        <small class="text-muted">{{ member.role || 'No role' }}</small>
                      </div>
                    </div>
                    <button
                      class="btn btn-sm btn-outline-danger"
                      @click="onRemoveMember(member.session_id)"
                      title="Remove member from channel"
                    >
                      Remove
                    </button>
                  </div>
                </div>
              </div>

              <!-- Comms Tab -->
              <div
                class="tab-pane fade"
                id="comms"
                role="tabpanel"
                aria-labelledby="comms-tab"
              >
                <div v-if="commsLoading" class="text-center text-muted py-4">
                  <div class="spinner-border spinner-border-sm" role="status">
                    <span class="visually-hidden">Loading...</span>
                  </div>
                  <div class="mt-2">Loading comms...</div>
                </div>

                <div v-else-if="comms.length === 0" class="text-center text-muted py-3">
                  No communications in this channel yet
                </div>

                <div v-else class="accordion" id="channelCommsAccordion">
                  <div
                    v-for="(comm, index) in comms"
                    :key="comm.comm_id"
                    class="accordion-item"
                  >
                    <h2 class="accordion-header" :id="`heading-${index}`">
                      <button
                        class="accordion-button collapsed"
                        type="button"
                        data-bs-toggle="collapse"
                        :data-bs-target="`#collapse-${index}`"
                        :aria-expanded="false"
                        :aria-controls="`collapse-${index}`"
                      >
                        <div class="d-flex align-items-center gap-2 w-100">
                          <span>{{ getCommIcon(comm.comm_type) }}</span>
                          <div class="flex-grow-1">
                            <strong>{{ comm.from_user ? 'User' : comm.from_minion_name || 'Unknown' }}</strong>
                            <small class="text-muted ms-2">{{ formatTimestamp(comm.timestamp) }}</small>
                          </div>
                          <span class="badge bg-secondary">{{ comm.comm_type }}</span>
                        </div>
                      </button>
                    </h2>
                    <div
                      :id="`collapse-${index}`"
                      class="accordion-collapse collapse"
                      :aria-labelledby="`heading-${index}`"
                      data-bs-parent="#channelCommsAccordion"
                    >
                      <div class="accordion-body">
                        <div v-if="comm.summary" class="mb-2">
                          <strong>Summary:</strong> {{ comm.summary }}
                        </div>
                        <div class="comm-content">{{ comm.content }}</div>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
        <div class="modal-footer">
          <button type="button" class="btn btn-secondary" @click="closeModal">Close</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { useLegionStore } from '@/stores/legion'
import { useSessionStore } from '@/stores/session'
import { Modal } from 'bootstrap'

const legionStore = useLegionStore()
const sessionStore = useSessionStore()

const modalElement = ref(null)
const modalInstance = ref(null)
const loading = ref(false)
const commsLoading = ref(false)
const commsLoaded = ref(false)
const showAddMemberSelector = ref(false)
const selectedMinionToAdd = ref('')

const channel = computed(() => legionStore.selectedChannel)
const comms = computed(() => legionStore.selectedChannelComms)

const memberCount = computed(() => {
  return channel.value?.member_minion_ids?.length || 0
})

const commsCount = computed(() => {
  return comms.value.length
})

// Get member details from session store
const members = computed(() => {
  if (!channel.value?.member_minion_ids) return []

  return channel.value.member_minion_ids
    .map(minionId => sessionStore.sessions.get(minionId))
    .filter(Boolean) // Filter out any undefined sessions
})

// Get non-member minions for the "Add Member" selector
const nonMemberMinions = computed(() => {
  if (!channel.value) return []

  const memberIds = new Set(channel.value.member_minion_ids || [])
  const allMinions = Array.from(sessionStore.sessions.values())
    .filter(session => session.is_minion)

  return allMinions.filter(minion => !memberIds.has(minion.session_id))
})

function getStatusClass(member) {
  // Special case: PAUSED + processing = pending prompt (yellow blinking)
  if (member.state === 'paused' && member.is_processing) {
    return 'status-blinking'
  }

  const state = member.is_processing ? 'processing' : member.state

  // Blink for starting and processing states
  return (state === 'starting' || state === 'processing') ? 'status-blinking' : ''
}

function getStatusStyle(member) {
  // Special case: PAUSED + processing = pending prompt (warning yellow)
  if (member.state === 'paused' && member.is_processing) {
    return {
      backgroundColor: '#ffc107'  // warning yellow
    }
  }

  const state = member.is_processing ? 'processing' : member.state

  // Match the light colors from SessionItem status dots
  const bgColorMap = {
    'created': '#d3d3d3',      // light grey
    'starting': '#90ee90',     // light green
    'active': '#90ee90',       // light green
    'processing': '#dda0dd',   // light purple/plum
    'paused': '#d3d3d3',       // light grey
    'terminating': '#ffcccb',  // light red
    'terminated': '#d3d3d3',   // light grey
    'error': '#ffcccb'         // light red
  }

  return {
    backgroundColor: bgColorMap[state] || '#d3d3d3'
  }
}

function formatDate(dateString) {
  if (!dateString) return 'N/A'
  const date = new Date(dateString)
  return date.toLocaleString()
}

function formatTimestamp(timestamp) {
  if (!timestamp) return 'N/A'
  const date = new Date(timestamp)
  return date.toLocaleString()
}

function getCommIcon(commType) {
  const icons = {
    'task': '📋',
    'question': '❓',
    'report': '📊',
    'info': 'ℹ️',
    'halt': '🛑',
    'pivot': '🔄',
    'thought': '💭',
    'spawn': '🐣',
    'dispose': '🗑️',
    'system': '⚙️'
  }
  return icons[commType?.toLowerCase()] || '💬'
}

async function onAddMember() {
  if (!selectedMinionToAdd.value || !channel.value) return

  try {
    await legionStore.addMemberToChannel(channel.value.channel_id, selectedMinionToAdd.value)
    selectedMinionToAdd.value = ''
    showAddMemberSelector.value = false
  } catch (error) {
    console.error('Failed to add member:', error)
    alert('Failed to add member to channel')
  }
}

async function onRemoveMember(minionId) {
  if (!channel.value) return

  const confirmRemove = confirm('Remove this minion from the channel?')
  if (!confirmRemove) return

  try {
    await legionStore.removeMemberFromChannel(channel.value.channel_id, minionId)
  } catch (error) {
    console.error('Failed to remove member:', error)
    alert('Failed to remove member from channel')
  }
}

async function loadCommsIfNeeded() {
  if (commsLoaded.value || !channel.value) return

  commsLoading.value = true
  try {
    await legionStore.loadChannelComms(channel.value.channel_id, 1000)
    commsLoaded.value = true
  } catch (error) {
    console.error('Failed to load channel comms:', error)
  } finally {
    commsLoading.value = false
  }
}

function closeModal() {
  if (modalInstance.value) {
    modalInstance.value.hide()
  }
  legionStore.closeChannelModal()
  commsLoaded.value = false
}

// Watch for selected channel changes to show/hide modal
watch(() => legionStore.selectedChannelId, async (newChannelId) => {
  if (newChannelId) {
    loading.value = true
    try {
      await legionStore.loadChannelDetails(newChannelId)
      if (modalInstance.value) {
        modalInstance.value.show()
      }
    } catch (error) {
      console.error('Failed to load channel details:', error)
    } finally {
      loading.value = false
    }
  } else {
    if (modalInstance.value) {
      modalInstance.value.hide()
    }
  }
})

onMounted(() => {
  if (modalElement.value) {
    modalInstance.value = new Modal(modalElement.value)

    // Listen for modal hide event to clear selection
    modalElement.value.addEventListener('hidden.bs.modal', () => {
      legionStore.closeChannelModal()
      commsLoaded.value = false
    })
  }
})

onUnmounted(() => {
  if (modalInstance.value) {
    modalInstance.value.dispose()
  }
})
</script>

<style scoped>
.status-dot {
  width: 12px;
  height: 12px;
  border-radius: 50%;
  display: inline-block;
  border: 1px solid rgba(0, 0, 0, 0.15);
}

.status-blinking {
  animation: blink 1.5s infinite;
}

@keyframes blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}

.comm-content {
  white-space: pre-wrap;
  word-wrap: break-word;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
}

.accordion-button {
  font-size: 0.9rem;
}

.modal-xl {
  max-width: 1200px;
}
</style>
