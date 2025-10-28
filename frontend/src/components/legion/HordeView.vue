<template>
  <div class="horde-view d-flex flex-column h-100">
    <!-- Horde Header (at top) -->
    <HordeHeader :legion-id="legionId" />

    <!-- Tree View -->
    <div class="horde-tree-container flex-grow-1 overflow-auto p-3">
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
      <div v-else-if="hierarchy" class="horde-tree">
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
                  → <strong>{{ hierarchy.last_comm.to_minion_name || hierarchy.last_comm.to_channel_name || 'unknown' }}</strong>:
                </span>
                <span class="comm-content">{{ hierarchy.last_comm.content }}</span>
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
          />
        </div>
      </div>
    </div>

    <!-- Status Bar (at bottom) -->
    <HordeStatusBar :legion-id="legionId" />
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { useProjectStore } from '../../stores/project'
import HordeHeader from '../header/HordeHeader.vue'
import HordeStatusBar from '../statusbar/HordeStatusBar.vue'
import MinionTreeNode from './MinionTreeNode.vue'
import { api } from '../../utils/api'

const props = defineProps({
  legionId: {
    type: String,
    required: true
  }
})

const projectStore = useProjectStore()

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
    const response = await api.get(`/api/legions/${props.legionId}/hordes`)
    hierarchy.value = response
    console.log('Loaded hierarchy:', response)
  } catch (err) {
    console.error('Failed to load hierarchy:', err)
    error.value = err.message || 'Unknown error'
  } finally {
    loading.value = false
  }
}

// Load on mount
onMounted(() => {
  loadHierarchy()
})

// Reload when legion ID changes
watch(() => props.legionId, () => {
  loadHierarchy()
})
</script>

<style scoped>
.horde-view {
  height: 100%;
}

.horde-tree-container {
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
}
</style>
