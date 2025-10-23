<template>
  <div class="list-group-item list-group-item-action p-2">
    <div class="d-flex align-items-center justify-content-between mb-2">
      <div class="d-flex align-items-center">
        <span style="font-size: 1rem; margin-right: 0.5rem;">🔍</span>
        <span class="fw-semibold">Spy</span>
        <small class="text-muted ms-2">(Inspect Minion)</small>
      </div>
      <span v-if="selectedMinion" class="minion-state-indicator" :class="stateClass">
        👤
      </span>
    </div>

    <!-- Minion Dropdown -->
    <select
      v-model="selectedMinionId"
      class="form-select form-select-sm"
      @change="onMinionSelect"
    >
      <option value="">-- Select Minion --</option>
      <option
        v-for="session in sessions"
        :key="session.session_id"
        :value="session.session_id"
      >
        {{ getStateIcon(session) }} {{ getMinionLabel(session) }}
      </option>
    </select>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useSessionStore } from '@/stores/session'

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
const sessionStore = useSessionStore()

const selectedMinionId = ref('')

const selectedMinion = computed(() => {
  if (!selectedMinionId.value) return null
  return props.sessions.find(s => s.session_id === selectedMinionId.value)
})

const stateClass = computed(() => {
  if (!selectedMinion.value) return ''
  const state = selectedMinion.value.is_processing ? 'processing' : selectedMinion.value.state
  return state === 'starting' || state === 'processing' ? 'status-blinking' : ''
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

function getMinionLabel(session) {
  if (session.is_minion && session.role) {
    return `${session.name} (${session.role})`
  }
  return session.name
}

function onMinionSelect() {
  if (selectedMinionId.value) {
    router.push(`/spy/${props.project.project_id}/${selectedMinionId.value}`)
  }
}

// Watch for current session changes to restore selection
watch(() => sessionStore.currentSessionId, (newSessionId) => {
  if (newSessionId && props.sessions.some(s => s.session_id === newSessionId)) {
    selectedMinionId.value = newSessionId
  }
})
</script>

<style scoped>
.minion-state-indicator {
  background-color: #f0f0f0;
  border: 2px solid #6c757d;
  border-radius: 4px;
  padding: 2px 4px;
  font-size: 0.875rem;
}

.status-blinking {
  animation: blink 1.5s infinite;
}

@keyframes blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}
</style>
