<template>
  <div class="channel-view d-flex flex-column h-100">
    <!-- Channel Header -->
    <div class="channel-header border-bottom p-3">
      <div class="d-flex align-items-center justify-content-between">
        <div>
          <h5 class="mb-0">
            💬 {{ channel?.name || 'Channel' }}
          </h5>
          <small class="text-muted">{{ legion?.name || 'Legion' }}</small>
        </div>
        <span class="badge bg-secondary">{{ memberCount }} members</span>
      </div>
    </div>

    <!-- Channel Content (3 sections) -->
    <div class="channel-content flex-grow-1 overflow-auto">
      <div v-if="loading" class="text-center text-muted py-4">
        <div class="spinner-border spinner-border-sm" role="status"></div>
        <div class="mt-2">Loading channel...</div>
      </div>

      <div v-else-if="error" class="text-center text-danger py-4">
        Failed to load channel: {{ error }}
      </div>

      <div v-else-if="!channel" class="text-center text-muted py-4">
        Channel not found
      </div>

      <div v-else class="p-3">
        <!-- Overview Section -->
        <div class="overview-section mb-4">
          <h6 class="text-muted border-bottom pb-2">Overview</h6>
          <div class="row g-3">
            <div class="col-md-6">
              <strong>Channel ID:</strong>
              <div class="font-monospace small text-muted">{{ channel.channel_id }}</div>
            </div>
            <div class="col-md-6">
              <strong>Created:</strong>
              <div class="text-muted">{{ formatDate(channel.created_at) }}</div>
            </div>
            <div class="col-12">
              <strong>Purpose:</strong>
              <div class="text-muted">{{ channel.purpose || 'No purpose specified' }}</div>
            </div>
            <div class="col-12">
              <strong>Description:</strong>
              <div class="text-muted">{{ channel.description || 'No description provided' }}</div>
            </div>
          </div>
        </div>

        <!-- Members Section -->
        <div class="members-section mb-4">
          <h6 class="text-muted border-bottom pb-2 d-flex align-items-center justify-content-between">
            <span>Members ({{ memberCount }})</span>
            <button
              class="btn btn-sm btn-primary"
              @click="showAddMemberSelector = !showAddMemberSelector"
            >
              ➕ Add Member
            </button>
          </h6>

          <!-- Add Member Selector -->
          <div v-if="showAddMemberSelector" class="mb-3">
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

        <!-- Comms Section -->
        <div class="comms-section">
          <h6 class="text-muted border-bottom pb-2">Communications ({{ commsCount }})</h6>

          <div v-if="commsLoading" class="text-center text-muted py-4">
            <div class="spinner-border spinner-border-sm" role="status"></div>
            <div class="mt-2">Loading comms...</div>
          </div>

          <div v-else-if="comms.length === 0" class="text-center text-muted py-3">
            No communications in this channel yet
          </div>

          <div v-else class="comm-list">
            <div
              v-for="comm in comms"
              :key="comm.comm_id"
              class="comm-item card mb-2"
            >
              <div class="card-body p-2">
                <div class="d-flex justify-content-between align-items-start mb-1">
                  <div>
                    <span class="badge" :class="getCommTypeBadge(comm.comm_type)">
                      {{ comm.comm_type }}
                    </span>
                    <strong class="ms-2">{{ getSenderName(comm) }}</strong>
                    <small class="text-muted ms-2">{{ formatTimestamp(comm.timestamp) }}</small>
                  </div>
                </div>
                <div v-if="comm.summary" class="text-muted small mb-1">
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
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { useLegionStore } from '../../stores/legion'
import { useProjectStore } from '../../stores/project'
import { useSessionStore } from '../../stores/session'

const props = defineProps({
  legionId: {
    type: String,
    required: true
  },
  channelId: {
    type: String,
    required: true
  }
})

const legionStore = useLegionStore()
const projectStore = useProjectStore()
const sessionStore = useSessionStore()

const loading = ref(true)
const error = ref(null)
const commsLoading = ref(false)
const showAddMemberSelector = ref(false)
const selectedMinionToAdd = ref('')

// Get legion and channel data
const legion = computed(() => projectStore.projects.get(props.legionId))
const channel = computed(() => legionStore.channelDetailsByChannel.get(props.channelId))
const comms = computed(() => legionStore.channelCommsByChannel.get(props.channelId) || [])

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
    .filter(Boolean)
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
  if (member.state === 'paused' && member.is_processing) {
    return 'status-blinking'
  }

  const state = member.is_processing ? 'processing' : member.state
  return (state === 'starting' || state === 'processing') ? 'status-blinking' : ''
}

function getStatusStyle(member) {
  if (member.state === 'paused' && member.is_processing) {
    return { backgroundColor: '#ffc107' }
  }

  const state = member.is_processing ? 'processing' : member.state

  const bgColorMap = {
    'created': '#d3d3d3',
    'starting': '#90ee90',
    'active': '#90ee90',
    'processing': '#dda0dd',
    'paused': '#d3d3d3',
    'terminating': '#ffcccb',
    'terminated': '#d3d3d3',
    'error': '#ffcccb'
  }

  return { backgroundColor: bgColorMap[state] || '#d3d3d3' }
}

function getSenderName(comm) {
  if (comm.from_user) return 'You'
  if (comm.from_minion_name) return comm.from_minion_name
  if (comm.from_minion_id) {
    const minion = sessionStore.sessions.get(comm.from_minion_id)
    return minion?.name || 'Unknown'
  }
  return 'Unknown'
}

function getCommTypeBadge(commType) {
  const badgeMap = {
    'task': 'bg-primary',
    'question': 'bg-info',
    'report': 'bg-success',
    'info': 'bg-secondary',
    'halt': 'bg-danger',
    'pivot': 'bg-warning'
  }
  return badgeMap[commType?.toLowerCase()] || 'bg-secondary'
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

async function onAddMember() {
  if (!selectedMinionToAdd.value || !props.channelId) return

  try {
    await legionStore.addMemberToChannel(props.channelId, selectedMinionToAdd.value)
    selectedMinionToAdd.value = ''
    showAddMemberSelector.value = false
  } catch (error) {
    console.error('Failed to add member:', error)
    alert('Failed to add member to channel')
  }
}

async function onRemoveMember(minionId) {
  const confirmRemove = confirm('Remove this minion from the channel?')
  if (!confirmRemove) return

  try {
    await legionStore.removeMemberFromChannel(props.channelId, minionId)
  } catch (error) {
    console.error('Failed to remove member:', error)
    alert('Failed to remove member from channel')
  }
}

// Load channel data on mount
onMounted(async () => {
  loading.value = true
  try {
    await legionStore.loadChannelDetails(props.channelId)

    // Load comms
    commsLoading.value = true
    await legionStore.loadChannelComms(props.channelId, 1000)
    commsLoading.value = false
  } catch (err) {
    console.error('Failed to load channel:', err)
    error.value = err.message || 'Unknown error'
  } finally {
    loading.value = false
  }
})

// Watch for channel changes
watch(() => props.channelId, async (newChannelId) => {
  if (newChannelId) {
    loading.value = true
    error.value = null
    try {
      await legionStore.loadChannelDetails(newChannelId)

      commsLoading.value = true
      await legionStore.loadChannelComms(newChannelId, 1000)
      commsLoading.value = false
    } catch (err) {
      console.error('Failed to load channel:', err)
      error.value = err.message || 'Unknown error'
    } finally {
      loading.value = false
    }
  }
})
</script>

<style scoped>
.channel-view {
  background-color: #fff;
}

.channel-header {
  background-color: #f8f9fa;
}

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
}

.overview-section,
.members-section,
.comms-section {
  background-color: #fff;
}
</style>
