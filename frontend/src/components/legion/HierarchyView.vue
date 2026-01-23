<template>
  <div class="hierarchy-view d-flex flex-column h-100">
    <!-- Hierarchy Header (at top) -->
    <HierarchyHeader :legion-id="legionId" />

    <!-- Tree View -->
    <div class="hierarchy-tree-container flex-grow-1 overflow-auto p-3">
      <!-- Loading State -->
      <div v-if="loading" class="text-center py-5">
        <div class="spinner-border text-primary" role="status">
          <span class="visually-hidden">Loading...</span>
        </div>
        <p class="text-muted mt-2">Loading hierarchy...</p>
      </div>

      <!-- Error State -->
      <div v-else-if="error" class="alert alert-danger">
        <strong>Failed to load hierarchy</strong>
        <p class="mb-0">{{ error }}</p>
      </div>

      <!-- Hierarchy Tree -->
      <div v-else-if="hierarchy" class="hierarchy-tree">
        <!-- User Root Node -->
        <div class="user-root-node mb-3 border rounded bg-white">
          <div class="node-row">
            <!-- Left Column: Status + Name (30%) -->
            <div class="node-left">
              <span class="me-2" style="font-size: 1.2rem;">👤</span>
              <strong>{{ hierarchy.name }}</strong>
              <span v-if="hasMinions" class="badge bg-primary ms-2">
                {{ hierarchy.children.length }} {{ hierarchy.children.length === 1 ? 'minion' : 'minions' }}
              </span>
            </div>

            <!-- Right Column: Last Comm (70%) -->
            <div class="node-right">
              <div v-if="hierarchy.last_comm" class="last-comm-preview">
                <span class="comm-direction">
                  → <strong>{{ getCommRecipient(hierarchy.last_comm) }}</strong>:
                </span>
                <span
                  class="comm-content"
                  :title="hierarchy.last_comm.content || ''"
                >
                  {{ getCommSummary(hierarchy.last_comm) }}
                </span>
              </div>
              <div v-else class="text-muted fst-italic">
                No communications yet
              </div>
            </div>
          </div>
        </div>

        <!-- Empty State -->
        <div v-if="!hasMinions" class="alert alert-info ms-4">
          <p class="mb-2">No minions yet</p>
          <small class="text-muted">Create a minion to get started</small>
        </div>

        <!-- Root Minions (user-spawned) -->
        <div v-else class="root-minions ms-4">
          <MinionTreeNode
            v-for="minion in hierarchy.children"
            :key="minion.id"
            :minion-data="minion"
            :level="1"
            @minion-click="handleMinionClick"
          />
        </div>
      </div>
    </div>

    <!-- Status Bar (at bottom) -->
    <HierarchyStatusBar :legion-id="legionId" />

    <!-- Fleet Control Modal -->
    <FleetControlModal />
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useProjectStore } from '../../stores/project'
import { useSessionStore } from '../../stores/session'
import { useWebSocketStore } from '../../stores/websocket'
import { useLegionStore } from '../../stores/legion'
import HierarchyHeader from '../header/HierarchyHeader.vue'
import HierarchyStatusBar from '../statusbar/HierarchyStatusBar.vue'
import MinionTreeNode from './MinionTreeNode.vue'
import FleetControlModal from './FleetControlModal.vue'
import { api } from '../../utils/api'

const props = defineProps({
  legionId: {
    type: String,
    required: true
  }
})

const router = useRouter()
const projectStore = useProjectStore()
const sessionStore = useSessionStore()
const websocketStore = useWebSocketStore()
const legionStore = useLegionStore()

// State
const hierarchy = ref(null)
const loading = ref(false)
const error = ref(null)

// Get legion
const legion = computed(() => projectStore.projects.get(props.legionId))

// Check if legion has any minions
const hasMinions = computed(() => {
  return hierarchy.value && hierarchy.value.children && hierarchy.value.children.length > 0
})

// Load hierarchy from API
async function loadHierarchy() {
  loading.value = true
  error.value = null

  try {
    const response = await api.get(`/api/legions/${props.legionId}/hierarchy`)
    hierarchy.value = response
    console.log('Loaded hierarchy:', response)
  } catch (err) {
    console.error('Failed to load hierarchy:', err)
    error.value = err.message || 'Unknown error'
  } finally {
    loading.value = false
  }
}

// Handle minion click - navigate to Spy View
function handleMinionClick(minionId) {
  router.push(`/spy/${props.legionId}/${minionId}`)
}

// Load on mount
onMounted(() => {
  loadHierarchy()
})

// Reload when legion ID changes
watch(() => props.legionId, () => {
  loadHierarchy()
})

// Helper: Get comm recipient name
function getCommRecipient(comm) {
  if (comm.to_user) {
    return 'User'
  } else if (comm.to_minion_name) {
    return comm.to_minion_name
  }
  return 'unknown'
}

// Helper: Get comm summary (prioritize summary, fallback to content, truncate at 150 chars)
function getCommSummary(comm) {
  const text = comm.summary || comm.content || ''
  if (text.length > 150) {
    return text.substring(0, 150) + '...'
  }
  return text
}

// Helper: Find minion node in hierarchy tree recursively
function findMinionInTree(node, minionId) {
  if (node.id === minionId) {
    return node
  }
  if (node.children) {
    for (const child of node.children) {
      const found = findMinionInTree(child, minionId)
      if (found) return found
    }
  }
  return null
}

// Handle state change events from UI WebSocket
function handleStateChange(data) {
  if (!hierarchy.value) return

  const sessionId = data.session_id

  // Find the minion in our hierarchy tree
  const minion = findMinionInTree(hierarchy.value, sessionId)
  if (minion && minion.type === 'minion') {
    // Update the minion's state
    minion.state = data.state
    console.log(`Updated minion ${minion.name} state to ${data.state}`)
  }
}

// Handle new comm events from Legion WebSocket
function handleNewComm(comm) {
  if (!hierarchy.value) return

  // If comm is from a minion, update that minion's last_comm
  if (comm.from_minion_id) {
    const minion = findMinionInTree(hierarchy.value, comm.from_minion_id)
    if (minion && minion.type === 'minion') {
      minion.last_comm = comm
      console.log(`Updated minion ${minion.name} last_comm`)
    }
  }

  // If comm is from user, update user root node
  if (comm.from_user && hierarchy.value.type === 'user') {
    hierarchy.value.last_comm = comm
    console.log('Updated user last_comm')
  }
}

// Subscribe to WebSocket events on mount
onMounted(() => {
  loadHierarchy()

  // Connect to legion WebSocket for comm updates
  legionStore.setCurrentLegion(props.legionId)
  websocketStore.connectLegion(props.legionId)

  // Watch for minion state changes from session store
  // The WebSocket updates the session store, which triggers this watcher
  watch(
    () => sessionStore.sessions,
    (sessions) => {
      if (!hierarchy.value) return

      // Update all minion states, is_processing, and latest_message in our hierarchy
      for (const [sessionId, session] of sessions) {
        if (session.is_minion && session.project_id === props.legionId) {
          const minion = findMinionInTree(hierarchy.value, sessionId)
          if (minion && minion.type === 'minion') {
            // Update state if changed
            if (minion.state !== session.state) {
              minion.state = session.state
              console.log(`Updated minion ${minion.name} state to ${session.state}`)
            }
            // Update is_processing if changed
            if (minion.is_processing !== session.is_processing) {
              minion.is_processing = session.is_processing
              console.log(`Updated minion ${minion.name} is_processing to ${session.is_processing}`)
            }
            // Update latest_message fields if changed (issue #291)
            if (minion.latest_message !== session.latest_message) {
              minion.latest_message = session.latest_message
              minion.latest_message_type = session.latest_message_type
              minion.latest_message_time = session.latest_message_time
              console.log(`Updated minion ${minion.name} latest_message`)
            }
          }
        }
      }
    },
    { deep: true }
  )

  // Watch for new comms (from Legion WebSocket)
  watch(
    () => legionStore.commsByLegion.get(props.legionId),
    (comms) => {
      if (comms && comms.length > 0) {
        // Get the most recent comm
        const latestComm = comms[comms.length - 1]
        handleNewComm(latestComm)
      }
    },
    { deep: true }
  )

  // Watch for new minions being created (reload hierarchy)
  watch(
    () => sessionStore.sessions.size,
    (newSize, oldSize) => {
      // Only reload if sessions increased (new minion created)
      if (newSize > oldSize) {
        // Check if a new minion for this legion was created
        const sessions = Array.from(sessionStore.sessions.values())
        const hasNewMinion = sessions.some(s =>
          s.is_minion &&
          s.project_id === props.legionId &&
          !findMinionInTree(hierarchy.value, s.session_id)
        )

        if (hasNewMinion) {
          console.log('New minion detected, reloading hierarchy')
          loadHierarchy()
        }
      }
    }
  )
})

// Cleanup on unmount
onUnmounted(() => {
  // Legion WebSocket cleanup is handled by the store
  console.log('HierarchyView unmounted')
})
</script>

<style scoped>
.hierarchy-view {
  height: 100%;
}

.hierarchy-tree-container {
  background-color: #f8f9fa;
}

.user-root-node {
  background-color: #ffffff;
  border-color: #0d6efd;
  border-width: 2px;
  padding: 0.75rem;
}

/* Two-column layout: 30% left, 70% right */
.node-row {
  display: flex;
  gap: 1rem;
  align-items: flex-start;
}

.node-left {
  flex: 0 0 30%;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.25rem;
}

.node-right {
  flex: 1;
  min-width: 0; /* Allow text truncation */
  padding-left: 1rem;
  border-left: 1px solid #dee2e6;
}

.last-comm-preview {
  font-size: 0.9rem;
  line-height: 1.4;
}

.comm-direction {
  color: #6c757d;
  font-size: 0.85rem;
}

.comm-content {
  color: #212529;
  word-wrap: break-word;
  cursor: help; /* Show help cursor on hover to indicate tooltip */
}
</style>
