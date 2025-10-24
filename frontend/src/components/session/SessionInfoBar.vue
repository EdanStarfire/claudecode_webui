<template>
  <div class="alert alert-info mb-0 rounded-0 d-flex justify-content-between align-items-center">
    <div class="d-flex align-items-center gap-2">
      <span class="badge" :class="stateClass">{{ displayState }}</span>
      <span class="fw-semibold">{{ session.name || session.session_id }}</span>
      <div v-if="session.error_message" class="alert alert-danger mb-0 py-1 px-2">
        {{ session.error_message }}
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  session: {
    type: Object,
    required: true
  }
})

const displayState = computed(() => {
  if (props.session.is_processing) return 'PROCESSING'
  return props.session.state.toUpperCase()
})

const stateClass = computed(() => {
  const stateColors = {
    'created': 'bg-secondary',
    'starting': 'bg-warning',
    'active': 'bg-success',
    'processing': 'bg-primary',
    'paused': 'bg-warning',
    'terminating': 'bg-warning',
    'terminated': 'bg-dark',
    'error': 'bg-danger'
  }

  const state = props.session.is_processing ? 'processing' : props.session.state
  return stateColors[state] || 'bg-secondary'
})
</script>
