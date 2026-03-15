<template>
  <div class="mcp-server-panel">
    <label class="form-label mb-1">MCP Servers</label>

    <!-- System MCP Servers -->
    <div class="mcp-section">
      <div class="mcp-section-header">System MCP Servers</div>
      <div v-if="configStore.loading" class="text-muted small ps-2 py-1">Loading...</div>
      <div v-else-if="globalConfigs.length === 0" class="text-muted small ps-2 py-1">
        No global MCP servers configured. Add them in Application Settings.
      </div>
      <div
        v-for="cfg in globalConfigs"
        :key="cfg.id"
        class="mcp-server-row d-flex align-items-center justify-content-between py-1 px-2"
      >
        <div class="d-flex align-items-center gap-2">
          <span
            v-if="systemRuntimeStatus(cfg.slug)"
            class="badge"
            :class="statusBadgeClass(systemRuntimeStatus(cfg.slug))"
          >{{ systemRuntimeStatus(cfg.slug) }}</span>
          <span class="badge bg-info" style="font-size: 0.65rem;" v-if="!systemRuntimeStatus(cfg.slug)">{{ cfg.type }}</span>
          <span class="server-name small">{{ cfg.name }}</span>
          <span v-if="!cfg.enabled" class="text-muted small">(disabled)</span>
        </div>
        <div class="d-flex align-items-center gap-2">
          <button
            v-if="systemRuntimeStatus(cfg.slug) === 'failed'"
            class="btn btn-outline-warning btn-sm py-0 px-1"
            style="font-size: 0.75rem"
            @click="$emit('reconnect', cfg.slug)"
          >Retry</button>
          <div class="form-check form-switch mb-0">
            <input
              class="form-check-input"
              type="checkbox"
              :checked="isSystemSelected(cfg.id)"
              :disabled="!cfg.enabled"
              @change="toggleSystemServer(cfg.id, $event.target.checked)"
            />
          </div>
        </div>
      </div>
    </div>

    <!-- Claude AI MCP Servers -->
    <div class="mcp-section">
      <div class="mcp-section-header d-flex align-items-center justify-content-between">
        <span>Claude AI MCP Servers</span>
        <div class="form-check form-switch mb-0">
          <input
            class="form-check-input"
            type="checkbox"
            :checked="claudeAiEnabled"
            @change="$emit('update:claude-ai-enabled', $event.target.checked)"
          />
        </div>
      </div>
      <template v-if="sessionActive && claudeAiEnabled">
        <div v-if="claudeAiServers.length === 0" class="text-muted small ps-2 py-1">
          No Claude AI servers active.
        </div>
        <McpServerRow
          v-for="server in claudeAiServers"
          :key="server.name"
          :server="server"
          @toggle="(name, enabled) => $emit('toggle', name, enabled)"
          @reconnect="(name) => $emit('reconnect', name)"
        />
      </template>
      <div v-else-if="!claudeAiEnabled" class="text-muted small ps-2 py-1">
        Disabled. Sets <code>ENABLE_CLAUDEAI_MCP_SERVERS=false</code>.
      </div>
    </div>

    <!-- Local MCP Servers -->
    <div v-if="localServers.length > 0" class="mcp-section">
      <div class="mcp-section-header">Local MCP Servers</div>
      <McpServerRow
        v-for="server in localServers"
        :key="server.name"
        :server="server"
        @toggle="(name, enabled) => $emit('toggle', name, enabled)"
        @reconnect="(name) => $emit('reconnect', name)"
      />
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted } from 'vue'
import { useMcpConfigStore } from '@/stores/mcpConfig'
import McpServerRow from './McpServerRow.vue'

const configStore = useMcpConfigStore()

const props = defineProps({
  mcpServerIds: {
    type: Array,
    default: () => []
  },
  claudeAiEnabled: {
    type: Boolean,
    default: true
  },
  runtimeServers: {
    type: Array,
    default: () => []
  },
  sessionActive: {
    type: Boolean,
    default: false
  }
})

const emit = defineEmits([
  'update:mcp-server-ids',
  'update:claude-ai-enabled',
  'toggle',
  'reconnect'
])

onMounted(() => {
  if (configStore.configList().length === 0) {
    configStore.fetchConfigs()
  }
})

const globalConfigs = computed(() => configStore.configList())

// Build a set of slugs from global configs for categorization
const systemSlugs = computed(() => {
  return new Set(globalConfigs.value.map(c => c.slug))
})

// Map runtime servers by name for quick lookup
const runtimeByName = computed(() => {
  const map = new Map()
  for (const server of props.runtimeServers) {
    map.set(server.name, server)
  }
  return map
})

// Get runtime status for a system server by slug
function systemRuntimeStatus(slug) {
  const server = runtimeByName.value.get(slug)
  return server?.status || null
}

// Check if a global config ID is selected
function isSystemSelected(id) {
  return (props.mcpServerIds || []).includes(id)
}

// Toggle a system server selection
function toggleSystemServer(id, checked) {
  const current = [...(props.mcpServerIds || [])]
  if (checked) {
    if (!current.includes(id)) current.push(id)
  } else {
    const idx = current.indexOf(id)
    if (idx >= 0) current.splice(idx, 1)
  }
  emit('update:mcp-server-ids', current)
}

// Detect Claude AI servers by name pattern (e.g. "claude.ai Sentry", "claude.ai Gmail")
function isClaudeAiServer(name) {
  const lower = name.toLowerCase()
  return lower.startsWith('claude.ai ') || lower.startsWith('claude_ai_') || lower.startsWith('claude.ai_')
}

// Claude AI servers: runtime servers matching Claude AI naming, excluding system slugs
const claudeAiServers = computed(() => {
  if (!props.sessionActive) return []
  return props.runtimeServers.filter(s =>
    isClaudeAiServer(s.name) && !systemSlugs.value.has(s.name)
  )
})

// Local servers: everything else from runtime that isn't system or Claude AI
const localServers = computed(() => {
  if (!props.sessionActive) return []
  return props.runtimeServers.filter(s =>
    !systemSlugs.value.has(s.name) && !isClaudeAiServer(s.name)
  )
})

function statusBadgeClass(status) {
  const map = {
    connected: 'bg-success',
    failed: 'bg-danger',
    pending: 'bg-warning text-dark',
    disabled: 'bg-secondary',
    'needs-auth': 'bg-needs-auth',
  }
  return map[status] || 'bg-secondary'
}
</script>

<style scoped>
.mcp-section {
  margin-bottom: 0.5rem;
}

.mcp-section-header {
  font-size: 0.75rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--bs-secondary);
  padding: 0.25rem 0.5rem;
  border-bottom: 1px solid var(--bs-border-color-translucent, rgba(0,0,0,0.1));
  margin-bottom: 0.25rem;
}

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
