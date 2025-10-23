<template>
  <div class="list-group-item list-group-item-action p-2">
    <div class="d-flex align-items-center justify-content-between mb-2">
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
import { ref, computed } from 'vue'
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

// Filter for overseers (minions that have spawned children)
const overseers = computed(() => {
  return props.sessions.filter(session => session.is_overseer === true)
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

function onOverseerSelect() {
  if (selectedOverseerId.value) {
    router.push(`/horde/${props.project.project_id}/${selectedOverseerId.value}`)
  }
}
</script>
