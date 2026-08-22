<template>
  <div class="mcp-server-detail card mb-2">
    <div class="card-body py-2 px-3">
      <!-- Header: name + status + reconnect -->
      <div class="d-flex align-items-center justify-content-between mb-1">
        <div class="d-flex align-items-center gap-2">
          <span class="badge" :class="statusBadgeClass">{{ server.status }}</span>
          <strong class="server-name small">{{ server.name }}</strong>
        </div>
        <button
          class="btn btn-outline-warning btn-sm py-0 px-2"
          @click="$emit('reconnect', server.name)"
        >
          Reconnect
        </button>
      </div>

      <!-- Server info -->
      <div class="server-info small text-muted">
        <span v-if="server.version">Version: {{ server.version }}</span>
        <span v-if="server.version && server.scope" class="mx-1">&middot;</span>
        <span v-if="server.scope">Scope: {{ server.scope }}</span>
      </div>

      <!-- Error message -->
      <div v-if="server.error" class="text-danger small mt-1">
        {{ server.error }}
      </div>

      <!-- Collapsible tool list -->
      <div v-if="server.tools && server.tools.length > 0" class="mt-2">
        <McpToolList :tools="server.tools" />
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import McpToolList from './McpToolList.vue'

const props = defineProps({
  server: {
    type: Object,
    required: true
  }
})

defineEmits(['reconnect'])

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
.server-name {
  font-family: monospace;
}

.bg-needs-auth {
  background-color: #fd7e14 !important;
  color: #fff;
}
</style>
