<template>
  <div class="horde-view d-flex flex-column h-100">
    <!-- Header -->
    <div class="horde-header p-3 border-bottom">
      <div class="d-flex justify-content-between align-items-center">
        <div v-if="overseer">
          <h5 class="mb-0">
            <span class="me-2">🏰</span>
            {{ legionName }} - {{ overseer.name }}'s Horde
          </h5>
          <small class="text-muted">{{ overseer.role || 'Overseer' }}</small>
        </div>
        <div v-else>
          <h5 class="mb-0">
            <span class="me-2">🏰</span>
            {{ legionName }} - Horde View
          </h5>
        </div>
      </div>
    </div>

    <!-- Tree View -->
    <div class="horde-tree-container flex-grow-1 overflow-auto p-3">
      <div v-if="!overseer" class="alert alert-danger">
        Overseer not found
      </div>
      <div v-else class="horde-tree">
        <MinionTreeNode :minion-id="overseerId" :level="0" />
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useSessionStore } from '../../stores/session'
import { useProjectStore } from '../../stores/project'
import MinionTreeNode from './MinionTreeNode.vue'

const props = defineProps({
  legionId: {
    type: String,
    required: true
  },
  overseerId: {
    type: String,
    required: true
  }
})

const sessionStore = useSessionStore()
const projectStore = useProjectStore()

// Get legion
const legion = computed(() => projectStore.projects.get(props.legionId))
const legionName = computed(() => legion.value?.name || 'Legion')

// Get overseer session
const overseer = computed(() => sessionStore.sessions.get(props.overseerId))
</script>

<style scoped>
.horde-view {
  height: 100%;
}

.horde-tree-container {
  background-color: #f8f9fa;
}
</style>
