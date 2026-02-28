<template>
  <div
    class="modal fade"
    id="deletedAgentsModal"
    tabindex="-1"
    aria-labelledby="deletedAgentsModalLabel"
    aria-hidden="true"
    ref="modalElement"
  >
    <div class="modal-dialog modal-dialog-centered">
      <div class="modal-content">
        <div class="modal-header">
          <h5 class="modal-title" id="deletedAgentsModalLabel">Deleted Agents</h5>
          <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
        </div>
        <div class="modal-body">
          <div v-if="loading" class="text-center py-4">
            <div class="spinner-border spinner-border-sm text-primary" role="status">
              <span class="visually-hidden">Loading...</span>
            </div>
            <span class="ms-2 text-muted">Loading deleted agents...</span>
          </div>

          <div v-else-if="agents.length === 0" class="text-center py-4">
            <p class="text-muted mb-0">No deleted agents with archives found for this project.</p>
          </div>

          <div v-else class="deleted-agents-list">
            <div
              v-for="agent in agents"
              :key="agent.agent_id"
              class="deleted-agent-row"
              @click="selectAgent(agent)"
            >
              <div class="da-info">
                <div class="da-name">{{ agent.name || 'Unknown' }}</div>
                <div class="da-role" v-if="agent.role">{{ agent.role }}</div>
              </div>
              <div class="da-meta">
                <span class="da-archives">{{ agent.archive_count }} archive{{ agent.archive_count !== 1 ? 's' : '' }}</span>
                <span v-if="agent.last_archived_at" class="da-date">{{ formatDate(agent.last_archived_at) }}</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, watch, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useSessionStore } from '@/stores/session'
import { useUIStore } from '@/stores/ui'

const router = useRouter()
const sessionStore = useSessionStore()
const uiStore = useUIStore()

const modalElement = ref(null)
let modalInstance = null

const loading = ref(false)
const agents = ref([])
let currentProjectId = null

function formatDate(timestamp) {
  if (!timestamp) return ''
  const d = new Date(timestamp * 1000)
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

async function loadDeletedAgents(projectId) {
  loading.value = true
  agents.value = []
  try {
    const response = await fetch(`/api/projects/${projectId}/deleted-agents`)
    if (response.ok) {
      const data = await response.json()
      agents.value = data.agents || []
    }
  } catch (e) {
    console.error('Failed to load deleted agents:', e)
  } finally {
    loading.value = false
  }
}

async function selectAgent(agent) {
  // Fetch archives to get the latest archive ID
  try {
    const response = await fetch(`/api/projects/${currentProjectId}/archives/${agent.agent_id}`)
    if (response.ok) {
      const data = await response.json()
      const archives = data.archives || []
      const latest = archives.length > 0 ? archives[archives.length - 1] : null

      const latestArchiveId = latest?.archive_id || null
      sessionStore.addGhostAgent(agent.agent_id, {
        name: agent.name,
        role: agent.role,
        archiveCount: agent.archive_count,
        latestArchiveId,
        projectId: currentProjectId
      })

      // Auto-navigate to latest archive
      if (latestArchiveId) {
        router.push(`/archive/agent/${agent.agent_id}/${latestArchiveId}`)
      }
    }
  } catch (e) {
    console.error('Failed to load archives for agent:', e)
  }

  if (modalInstance) {
    modalInstance.hide()
  }
}

function onModalHidden() {
  uiStore.hideModal()
}

watch(
  () => uiStore.currentModal,
  (modal) => {
    if (modal?.name === 'deleted-agents' && modalInstance) {
      const data = modal.data || {}
      currentProjectId = data.project?.project_id
      if (currentProjectId) {
        loadDeletedAgents(currentProjectId)
      }
      modalInstance.show()
    }
  }
)

onMounted(() => {
  if (modalElement.value) {
    import('bootstrap/js/dist/modal').then(({ default: Modal }) => {
      modalInstance = new Modal(modalElement.value)
      modalElement.value.addEventListener('hidden.bs.modal', onModalHidden)
    })
  }
})
</script>

<style scoped>
.deleted-agents-list {
  max-height: 400px;
  overflow-y: auto;
}

.deleted-agent-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 12px;
  border-bottom: 1px solid #f1f5f9;
  cursor: pointer;
  transition: background 0.1s;
}

.deleted-agent-row:last-child {
  border-bottom: none;
}

.deleted-agent-row:hover {
  background: #f8fafc;
}

.da-info {
  min-width: 0;
}

.da-name {
  font-size: 14px;
  font-weight: 600;
  color: #1e293b;
}

.da-role {
  font-size: 11px;
  color: #64748b;
}

.da-meta {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  flex-shrink: 0;
}

.da-archives {
  font-size: 11px;
  color: #475569;
  font-weight: 500;
}

.da-date {
  font-size: 10px;
  color: #94a3b8;
}
</style>
