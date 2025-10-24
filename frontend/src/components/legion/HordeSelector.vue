<template>
  <div class="list-group-item list-group-item-action p-2" :class="{ active: isActive }">
    <div
      class="d-flex align-items-center justify-content-between mb-2"
      :style="{ cursor: selectedOverseerId ? 'pointer' : 'default' }"
      @click="onHeaderClick"
    >
      <div class="d-flex align-items-center">
        <span style="font-size: 1rem; margin-right: 0.5rem;">🌳</span>
        <span class="fw-semibold">Horde</span>
        <small class="text-muted ms-2">(Minion Hierarchy)</small>
      </div>
    </div>

    <!-- Overseer Dropdown -->
    <select
      v-model="selectedOverseerId"
      class="form-select form-select-sm"
      @change="onOverseerSelect"
    >
      <option value="">-- Select Overseer --</option>
      <option
        v-for="session in overseers"
        :key="session.session_id"
        :value="session.session_id"
      >
        {{ getStateIcon(session) }} {{ session.name }}
      </option>
    </select>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { useRouter } from 'vue-router'

const props = defineProps({
  project: {
    type: Object,
    required: true
  },
  sessions: {
    type: Array,
    default: () => []
  }
})

const router = useRouter()
const selectedOverseerId = ref('')

// Sync dropdown with current route (only if empty - for deep links)
watch(
  () => router.currentRoute.value,
  (route) => {
    // Only update if no overseer is currently selected (for deep link restoration)
    if (!selectedOverseerId.value && route.name === 'horde' && route.params.overseerId) {
      // Check if this horde route is for our project
      if (route.params.legionId === props.project.project_id) {
        selectedOverseerId.value = route.params.overseerId
      }
    }
  },
  { immediate: true }
)

// Filter for overseers (minions that have spawned children)
const overseers = computed(() => {
  return props.sessions.filter(session => session.is_overseer === true)
})

const isActive = computed(() => {
  const route = router.currentRoute.value
  return route.name === 'horde' && route.params.legionId === props.project.project_id
})

function getStateIcon(session) {
  const icons = {
    'created': '○',
    'starting': '◐',
    'active': '●',
    'paused': '⏸',
    'terminating': '◍',
    'terminated': '✗',
    'error': '⚠'
  }
  return icons[session.state] || '○'
}

function onHeaderClick() {
  // If an overseer is selected, navigate to horde view
  if (selectedOverseerId.value) {
    router.push(`/horde/${props.project.project_id}/${selectedOverseerId.value}`)
  }
}

function onOverseerSelect() {
  if (selectedOverseerId.value) {
    router.push(`/horde/${props.project.project_id}/${selectedOverseerId.value}`)
  }
}
</script>

<style scoped>
.list-group-item.active {
  background-color: #0d6efd;
  color: white;
}

.list-group-item:hover:not(.active) {
  background-color: #f8f9fa;
}
</style>
