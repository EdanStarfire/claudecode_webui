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
          v-if="server.status === 'failed'"
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
        <button
          class="btn btn-link btn-sm p-0 text-muted"
          @click="toolsExpanded = !toolsExpanded"
        >
          {{ toolsExpanded ? 'Hide' : 'Show' }} {{ server.tools.length }} tool{{ server.tools.length !== 1 ? 's' : '' }}
        </button>
        <div v-if="toolsExpanded" class="tool-list mt-1">
          <div
            v-for="tool in server.tools"
            :key="tool.name"
            class="tool-item small py-1 px-2"
          >
            <code>{{ tool.name }}</code>
            <span v-if="tool.description" class="text-muted ms-2">{{ tool.description }}</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'

const props = defineProps({
  server: {
    type: Object,
    required: true
  }
})

defineEmits(['reconnect'])

const toolsExpanded = ref(false)

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

.tool-list {
  max-height: 200px;
  overflow-y: auto;
}

.tool-item {
  border-radius: 0.25rem;
  background: var(--bs-gray-100, #f8f9fa);
}

.tool-item + .tool-item {
  margin-top: 2px;
}

.bg-needs-auth {
  background-color: #fd7e14 !important;
  color: #fff;
}
</style>
