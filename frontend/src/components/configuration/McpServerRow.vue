<template>
  <div class="mcp-server-row d-flex align-items-center justify-content-between py-1 px-2">
    <div class="d-flex align-items-center gap-2">
      <span class="badge" :class="statusBadgeClass">{{ server.status }}</span>
      <span class="server-name small">{{ server.name }}</span>
    </div>
    <div class="d-flex align-items-center gap-2">
      <button
        v-if="server.status === 'failed'"
        class="btn btn-outline-warning btn-sm py-0 px-1"
        style="font-size: 0.75rem"
        @click="$emit('reconnect', server.name)"
      >
        Retry
      </button>
      <div class="form-check form-switch mb-0">
        <input
          class="form-check-input"
          type="checkbox"
          :id="`mcp-toggle-${server.name}`"
          :checked="server.status !== 'disabled'"
          @change="$emit('toggle', server.name, $event.target.checked)"
        />
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  server: {
    type: Object,
    required: true
  }
})

defineEmits(['toggle', 'reconnect'])

const statusBadgeClass = computed(() => {
  const map = {
    connected: 'bg-success',
    failed: 'bg-danger',
    pending: 'bg-warning text-dark',
    disabled: 'bg-secondary',
    'needs-auth': 'bg-needs-auth',
  }
  return map[props.server.status] || 'bg-secondary'
})
</script>

<style scoped>
.mcp-server-row {
  border-radius: 0.25rem;
  background: var(--bs-gray-100, #f8f9fa);
}

.mcp-server-row + .mcp-server-row {
  margin-top: 0.25rem;
}

.server-name {
  font-family: monospace;
}

.bg-needs-auth {
  background-color: #fd7e14 !important;
  color: #fff;
}
</style>
