<template>
  <div class="horde-view d-flex flex-column h-100">
    <!-- Horde Header (at top) -->
    <HordeHeader :legion-id="legionId" :overseer-id="overseerId" />

    <!-- Tree View -->
    <div class="horde-tree-container flex-grow-1 overflow-auto p-3">
      <div v-if="!overseer" class="alert alert-danger">
        Overseer not found
      </div>
      <div v-else class="horde-tree">
        <MinionTreeNode :minion-id="overseerId" :level="0" />
      </div>
    </div>

    <!-- Status Bar (at bottom) -->
    <HordeStatusBar :legion-id="legionId" :overseer-id="overseerId" />
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
