<template>
  <div class="horde-view d-flex flex-column h-100">
    <!-- Horde Header (at top) -->
    <HordeHeader :legion-id="legionId" />

    <!-- Tree View -->
    <div class="horde-tree-container flex-grow-1 overflow-auto p-3">
      <div class="horde-tree">
        <!-- User Root Node -->
        <div class="user-root-node mb-3 p-3 border rounded bg-white">
          <div class="d-flex align-items-center">
            <span class="me-2" style="font-size: 1.2rem;">👤</span>
            <strong>User (you)</strong>
            <span v-if="hasMinions" class="badge bg-primary ms-2">
              {{ rootMinions.length }} {{ rootMinions.length === 1 ? 'minion' : 'minions' }}
            </span>
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
            v-for="minion in rootMinions"
            :key="minion.session_id"
            :minion-id="minion.session_id"
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
import { computed } from 'vue'
import { useSessionStore } from '../../stores/session'
import { useProjectStore } from '../../stores/project'
import HordeHeader from '../header/HordeHeader.vue'
import HordeStatusBar from '../statusbar/HordeStatusBar.vue'
import MinionTreeNode from './MinionTreeNode.vue'

const props = defineProps({
  legionId: {
    type: String,
    required: true
  }
})

const sessionStore = useSessionStore()
const projectStore = useProjectStore()

// Get legion
const legion = computed(() => projectStore.projects.get(props.legionId))

// Get all sessions for this legion (minions)
const allMinions = computed(() => {
  return Array.from(sessionStore.sessions.values()).filter(
    session => session.is_minion && session.project_id === props.legionId
  )
})

// Get root-level minions (user-spawned: parent_overseer_id === null)
const rootMinions = computed(() => {
  return allMinions.value.filter(
    minion => minion.parent_overseer_id === null
  )
})

// Check if legion has any minions
const hasMinions = computed(() => allMinions.value.length > 0)
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
}
</style>
