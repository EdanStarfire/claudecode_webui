<template>
  <div class="list-group-item list-group-item-action p-2" :class="{ active: isActive }">
    <div
      class="d-flex align-items-center justify-content-between mb-2"
      :style="{ cursor: selectedMinionId ? 'pointer' : 'default' }"
      @click="onHeaderClick"
    >
      <div class="d-flex align-items-center">
        <span style="font-size: 1rem; margin-right: 0.5rem;">🔍</span>
        <span class="fw-semibold">Spy</span>
        <small class="text-muted ms-2">(Inspect Minion)</small>
      </div>
      <div class="d-flex align-items-center gap-2">
        <button
          v-if="selectedMinion"
          class="btn btn-sm btn-outline-secondary"
          @click.stop="showMinionInfo"
          title="View minion details"
        >
          👁️
        </button>
        <span v-if="selectedMinion" class="minion-state-indicator" :class="stateClass" :style="stateStyle">
          👤
        </span>
      </div>
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
import { useUIStore } from '@/stores/ui'

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
const uiStore = useUIStore()

const selectedMinionId = ref('')

// Sync dropdown with current route (only if empty - for deep links)
watch(
  () => router.currentRoute.value,
  (route) => {
    // Only update if no minion is currently selected (for deep link restoration)
    if (!selectedMinionId.value && route.name === 'spy' && route.params.minionId) {
      // Check if this spy route is for our project
      if (route.params.legionId === props.project.project_id) {
        selectedMinionId.value = route.params.minionId
      }
    }
  },
  { immediate: true }
)

const selectedMinion = computed(() => {
  if (!selectedMinionId.value) return null
  return props.sessions.find(s => s.session_id === selectedMinionId.value)
})

const isActive = computed(() => {
  const route = router.currentRoute.value
  return route.name === 'spy' && route.params.legionId === props.project.project_id
})

const stateClass = computed(() => {
  if (!selectedMinion.value) return ''

  // Special case: PAUSED + processing = pending prompt (yellow blinking)
  if (selectedMinion.value.state === 'paused' && selectedMinion.value.is_processing) {
    return 'status-blinking'
  }

  const state = selectedMinion.value.is_processing ? 'processing' : selectedMinion.value.state

  // Blink for starting and processing states
  return (state === 'starting' || state === 'processing') ? 'status-blinking' : ''
})

const stateStyle = computed(() => {
  if (!selectedMinion.value) return {}

  // Special case: PAUSED + processing = pending prompt (warning yellow)
  if (selectedMinion.value.state === 'paused' && selectedMinion.value.is_processing) {
    return {
      backgroundColor: '#ffc107'  // warning yellow
    }
  }

  const state = selectedMinion.value.is_processing ? 'processing' : selectedMinion.value.state

  // Match the light colors from SessionItem status dots
  const bgColorMap = {
    'created': '#d3d3d3',      // light grey
    'starting': '#90ee90',     // light green
    'active': '#90ee90',       // light green
    'processing': '#dda0dd',   // light purple/plum
    'paused': '#d3d3d3',       // light grey
    'terminating': '#ffcccb',  // light red
    'terminated': '#d3d3d3',   // light grey
    'error': '#ffcccb'         // light red
  }

  return {
    backgroundColor: bgColorMap[state] || '#d3d3d3'
  }
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

function onHeaderClick() {
  // If a minion is selected, navigate to spy view
  if (selectedMinionId.value) {
    router.push(`/spy/${props.project.project_id}/${selectedMinionId.value}`)
  }
}

function onMinionSelect() {
  if (selectedMinionId.value) {
    // Navigate to spy view for selected minion
    router.push(`/spy/${props.project.project_id}/${selectedMinionId.value}`)
  } else {
    // User selected "-- Select Minion --", exit to no-session view
    router.push('/')
  }
}

function showMinionInfo() {
  if (selectedMinion.value) {
    uiStore.showModal('minion-view', {
      session: selectedMinion.value
    })
  }
}

// Watch for current session changes to restore selection
watch(() => sessionStore.currentSessionId, (newSessionId) => {
  // If session cleared (e.g., deleted), clear dropdown selection and navigate away
  if (!newSessionId) {
    selectedMinionId.value = ''
    // Only navigate if we're currently in spy mode for this project
    const route = router.currentRoute.value
    if (route.name === 'spy' && route.params.legionId === props.project.project_id) {
      router.push('/')
    }
  }
  // If session selected and it's in our minion list, sync it
  else if (props.sessions.some(s => s.session_id === newSessionId)) {
    selectedMinionId.value = newSessionId
  }
})
</script>

<style scoped>
.list-group-item.active {
  background-color: #0d6efd;
  color: white;
}

.list-group-item:hover:not(.active) {
  background-color: #f8f9fa;
}

.minion-state-indicator {
  border-radius: 4px;
  padding: 2px 4px;
  font-size: 0.875rem;
  border: 1px solid rgba(0, 0, 0, 0.15);
}

.status-blinking {
  animation: blink 1.5s infinite;
}

@keyframes blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}
</style>
