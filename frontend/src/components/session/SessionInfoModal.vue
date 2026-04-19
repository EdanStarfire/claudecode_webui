<template>
  <div
    class="modal fade"
    id="sessionInfoModal"
    tabindex="-1"
    aria-labelledby="sessionInfoModalLabel"
    aria-hidden="true"
    ref="modalElement"
  >
    <div class="modal-dialog modal-dialog-centered modal-lg">
      <div class="modal-content">
        <div class="modal-header">
          <h5 class="modal-title" id="sessionInfoModalLabel">Session Configuration</h5>
          <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
        </div>
        <div class="modal-body">
          <!-- Loading state -->
          <div v-if="isLoading" class="text-center py-4">
            <div class="spinner-border spinner-border-sm me-2" role="status"></div>
            Loading session info...
          </div>

          <!-- Error state -->
          <div v-else-if="fetchError" class="text-danger small py-2">{{ fetchError }}</div>

          <!-- No data -->
          <div v-else-if="!displayData" class="text-center text-muted py-4">
            No session configuration data available
          </div>

          <div v-else>
            <!-- Session ID -->
            <div class="mb-3">
              <h6 class="text-muted">Session ID</h6>
              <div class="font-monospace small">{{ sessionId }}</div>
            </div>

            <!-- Working Directory -->
            <div class="mb-3">
              <h6 class="text-muted">Working Directory</h6>
              <input
                type="text"
                class="form-control form-control-sm font-monospace path-input"
                :value="displayData.cwd || 'Not specified'"
                readonly
                @click="selectPath"
              />
            </div>

            <!-- Model -->
            <div class="mb-3">
              <h6 class="text-muted">Model</h6>
              <div>{{ getModelDisplayName(displayData.model) }}</div>
            </div>

            <!-- Permission Mode -->
            <div class="mb-3">
              <h6 class="text-muted">Permission Mode</h6>
              <div>{{ displayData.permissionMode || 'default' }}</div>
            </div>

            <!-- Proxy Status (only when proxy enabled) -->
            <div v-if="currentProxyStatus" class="mb-3">
              <h6 class="text-muted">Proxy Status</h6>
              <div class="mb-1">
                <small class="text-muted">Sidecar: </small>
                <span :class="currentProxyStatus.sidecar_running ? 'text-success' : 'text-secondary'">
                  {{ currentProxyStatus.sidecar_running ? 'Running' : 'Stopped' }}
                </span>
              </div>
              <div v-if="currentProxyStatus.effective_domains?.length" class="mb-1">
                <small class="text-muted">Allowed Domains ({{ currentProxyStatus.effective_domains.length }}):</small>
                <div class="d-flex flex-wrap gap-1 mt-1">
                  <span
                    v-for="d in currentProxyStatus.effective_domains"
                    :key="d"
                    class="badge bg-success-subtle text-success-emphasis"
                  >{{ d }}</span>
                </div>
              </div>
              <div v-if="currentProxyStatus.active_credentials?.length" class="mb-1">
                <small class="text-muted">Active Credentials:</small>
                <div class="d-flex flex-wrap gap-1 mt-1">
                  <span
                    v-for="c in currentProxyStatus.active_credentials"
                    :key="c"
                    class="badge bg-info-subtle text-info-emphasis"
                  >{{ c }}</span>
                </div>
              </div>
              <div v-if="currentBlockedLog.length" class="mb-1">
                <small class="text-muted">Recent Blocked Connections:</small>
                <div class="small mt-1" style="max-height: 150px; overflow-y: auto;">
                  <div v-for="(entry, i) in currentBlockedLog" :key="i" class="text-danger-emphasis">
                    {{ entry.ts }} — {{ entry.host }}{{ entry.path }} ({{ entry.method }})
                  </div>
                </div>
              </div>
            </div>

            <!-- Memory Directory (when custom directory is configured) -->
            <div v-if="displayData.auto_memory_directory" class="mb-3">
              <h6 class="text-muted">Memory Directory</h6>
              <input
                type="text"
                class="form-control form-control-sm font-monospace path-input"
                :value="displayData.auto_memory_directory"
                readonly
                @click="selectPath"
              />
            </div>

            <!-- Pre-Authorized Tools -->
            <div class="mb-3">
              <h6 class="text-muted">Pre-Authorized Tools</h6>
              <div v-if="nonMcpTools.length > 0">
                <div class="d-flex flex-wrap gap-1">
                  <span
                    v-for="tool in nonMcpTools"
                    :key="tool"
                    class="badge bg-secondary"
                  >
                    {{ tool }}
                  </span>
                </div>
              </div>
              <div v-else class="text-muted small">No pre-authorized tools (will prompt for permissions)</div>
            </div>

            <!-- MCP Servers -->
            <div class="mb-3">
              <h6 class="text-muted">MCP Servers</h6>
              <!-- System servers (from global config) -->
              <div v-if="systemMcpServers.length > 0">
                <div class="mcp-category-header">System MCP Servers</div>
                <McpServerDetail
                  v-for="server in systemMcpServers"
                  :key="server.name"
                  :server="server"
                  @reconnect="handleReconnect"
                />
              </div>
              <!-- Claude AI servers -->
              <div v-if="claudeAiMcpServers.length > 0">
                <div class="mcp-category-header">Claude AI MCP Servers</div>
                <McpServerDetail
                  v-for="server in claudeAiMcpServers"
                  :key="server.name"
                  :server="server"
                  @reconnect="handleReconnect"
                />
              </div>
              <!-- Local servers -->
              <div v-if="localMcpServers.length > 0">
                <div class="mcp-category-header">Local MCP Servers</div>
                <McpServerDetail
                  v-for="server in localMcpServers"
                  :key="server.name"
                  :server="server"
                  @reconnect="handleReconnect"
                />
              </div>
              <div v-if="mcpServers.length === 0" class="text-muted small">
                No MCP servers active.
              </div>
            </div>

            <!-- System Prompt -->
            <div v-if="displayData.systemPrompt" class="mb-3">
              <h6 class="text-muted">System Prompt</h6>
              <pre class="bg-light p-2 rounded small" style="max-height: 200px; overflow-y: auto;">{{ formatSystemPrompt(displayData.systemPrompt) }}</pre>
            </div>

            <!-- SDK Session Info (git branch, summary) -->
            <div v-if="sdkSessionInfo" class="mb-3">
              <h6 class="text-muted">SDK Session</h6>
              <div v-if="sdkSessionInfo.git_branch" class="small font-monospace">
                Branch: {{ sdkSessionInfo.git_branch }}
              </div>
              <div v-if="sdkSessionInfo.summary" class="small text-muted mt-1">
                {{ sdkSessionInfo.summary }}
              </div>
            </div>

            <!-- Settings (full data dump for advanced users) -->
            <div class="mb-3">
              <h6 class="text-muted">
                Raw Configuration
                <button
                  class="btn btn-sm btn-outline-secondary ms-2"
                  @click="showRawData = !showRawData"
                  :aria-expanded="showRawData"
                >
                  {{ showRawData ? 'Hide' : 'Show' }}
                </button>
              </h6>
              <pre
                v-if="showRawData"
                class="bg-light p-2 rounded small"
                style="max-height: 300px; overflow-y: auto;"
              >{{ JSON.stringify(sessionData, null, 2) }}</pre>
            </div>
          </div>
        </div>
        <div class="modal-footer">
          <button
            class="btn btn-outline-secondary btn-sm"
            @click="fetchSessionInfo(sessionId)"
            :disabled="isLoading || !sessionId"
          >
            Refresh
          </button>
          <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Close</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useSessionStore } from '@/stores/session'
import { useUIStore } from '@/stores/ui'
import { useMcpStore } from '@/stores/mcp'
import { useMcpConfigStore } from '@/stores/mcpConfig'
import { useProxyStore } from '@/stores/proxy'
import { api } from '@/utils/api'
import McpServerDetail from './McpServerDetail.vue'

const sessionStore = useSessionStore()
const uiStore = useUIStore()
const mcpStore = useMcpStore()
const mcpConfigStore = useMcpConfigStore()
const proxyStore = useProxyStore()

// State
const sessionId = ref(null)
const showRawData = ref(false)
const modalElement = ref(null)
const sessionData = ref(null)
const isLoading = ref(false)
const fetchError = ref(null)
let modalInstance = null

// Model display names mapping
const modelDisplayNames = {
  'sonnet': 'Sonnet',
  'opus': 'Opus',
  'haiku': 'Haiku',
  'opusplan': 'OpusPlan (Opus + Sonnet)'
}

// Derive display data from API response, falling back to Pinia initData
const displayData = computed(() => {
  const s = sessionData.value?.session
  if (s) {
    return {
      cwd: s.working_directory,
      model: s.model,
      permissionMode: s.current_permission_mode,
      allowed_tools: s.allowed_tools || [],
      systemPrompt: s.system_prompt || s.override_system_prompt,
      auto_memory_mode: s.auto_memory_mode,
      auto_memory_directory: s.auto_memory_directory,
    }
  }
  // Fallback: Pinia initData (populated from SDK init message)
  if (!sessionId.value) return null
  return sessionStore.initData.get(sessionId.value) || null
})

const sdkSessionInfo = computed(() => sessionData.value?.sdk_session_info || null)

// Proxy status — only shown when proxy is enabled for this session
const currentProxyStatus = computed(() => {
  if (!sessionId.value) return null
  const status = proxyStore.proxyStatus(sessionId.value)
  return status?.proxy_enabled ? status : null
})

const currentBlockedLog = computed(() => {
  if (!sessionId.value) return []
  return proxyStore.blockedLog(sessionId.value)
})

// Filter out mcp__* tools from pre-authorized list (shown in MCP section instead)
const nonMcpTools = computed(() => {
  const tools = displayData.value?.allowed_tools || displayData.value?.tools || []
  return tools.filter(t => !t.startsWith('mcp__'))
})

// MCP servers for the current session
const mcpServers = computed(() => {
  if (!sessionId.value) return []
  return mcpStore.mcpServers(sessionId.value)
})

// Categorize MCP servers
const systemSlugs = computed(() => {
  return new Set(mcpConfigStore.configList().map(c => c.slug))
})

const systemMcpServers = computed(() => {
  return mcpServers.value.filter(s => systemSlugs.value.has(s.name))
})

function isClaudeAiServer(name) {
  const lower = name.toLowerCase()
  return lower.startsWith('claude.ai ') || lower.startsWith('claude_ai_') || lower.startsWith('claude.ai_')
}

const claudeAiMcpServers = computed(() => {
  return mcpServers.value.filter(s =>
    isClaudeAiServer(s.name) && !systemSlugs.value.has(s.name)
  )
})

const localMcpServers = computed(() => {
  return mcpServers.value.filter(s =>
    !systemSlugs.value.has(s.name) && !isClaudeAiServer(s.name)
  )
})

function handleReconnect(name) {
  if (sessionId.value) {
    mcpStore.reconnectServer(sessionId.value, name)
  }
}

// Fetch session info from API
async function fetchSessionInfo(sid) {
  if (!sid) return
  isLoading.value = true
  fetchError.value = null
  try {
    const result = await api.get(`/api/sessions/${sid}`)
    sessionData.value = result
  } catch (e) {
    fetchError.value = e.message
  } finally {
    isLoading.value = false
  }
}

// Get human-readable model display name
function getModelDisplayName(modelId) {
  if (!modelId) {
    return 'Default (Sonnet)'
  }
  return modelDisplayNames[modelId] || modelId
}

// Format system prompt for display
function formatSystemPrompt(prompt) {
  if (typeof prompt === 'string') {
    return prompt
  }
  if (typeof prompt === 'object' && prompt.type === 'preset') {
    return `Preset: ${prompt.preset}`
  }
  return JSON.stringify(prompt, null, 2)
}

// Auto-select path text on click for easy copying
function selectPath(event) {
  event.target.select()
}

// Reset state
function resetState() {
  sessionId.value = null
  showRawData.value = false
  sessionData.value = null
  fetchError.value = null
}

// Handle modal hidden event
function onModalHidden() {
  resetState()
  uiStore.hideModal()
}

// Watch for modal show requests from UI store
watch(
  () => uiStore.currentModal,
  (modal) => {
    if (modal?.name === 'session-info' && modalInstance) {
      const data = modal.data || {}
      sessionId.value = data.sessionId
      sessionData.value = null
      showRawData.value = false
      if (data.sessionId) {
        fetchSessionInfo(data.sessionId)
        // Fetch MCP status if session is active
        const session = sessionStore.sessions.get(data.sessionId)
        if (session?.state === 'active') {
          mcpStore.fetchMcpStatus(data.sessionId)
        }
        if (mcpConfigStore.configList().length === 0) {
          mcpConfigStore.fetchConfigs()
        }
        // Fetch proxy status and blocked log if proxy is enabled
        if (session?.docker_proxy_enabled) {
          proxyStore.fetchProxyStatus(data.sessionId)
          proxyStore.fetchBlockedLog(data.sessionId)
        }
      }
      modalInstance.show()
    }
  }
)

// Initialize Bootstrap modal
onMounted(() => {
  if (modalElement.value) {
    import('bootstrap/js/dist/modal').then(({ default: Modal }) => {
      modalInstance = new Modal(modalElement.value)
      modalElement.value.addEventListener('hidden.bs.modal', onModalHidden)
    })
  }
})

// Cleanup
onUnmounted(() => {
  if (modalElement.value) {
    modalElement.value.removeEventListener('hidden.bs.modal', onModalHidden)
  }
  if (modalInstance) {
    modalInstance.dispose()
  }
})
</script>

<style scoped>
pre {
  white-space: pre-wrap;
  word-wrap: break-word;
  margin-bottom: 0;
}

.badge {
  font-weight: normal;
}

/* Read-only path input styling */
.path-input {
  background-color: #f8f9fa;
  cursor: text;
  color: #495057;
  overflow-x: auto;
  white-space: nowrap;
  font-size: 0.875rem;
}

.path-input:focus {
  background-color: #f8f9fa;
  outline: 2px solid #0d6efd;
  outline-offset: 2px;
}

.mcp-category-header {
  font-size: 0.7rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--bs-secondary);
  padding: 0.25rem 0;
  margin-top: 0.5rem;
}

.mcp-category-header:first-child {
  margin-top: 0;
}

/* Mobile responsive */
@media (max-width: 768px) {
  .path-input {
    font-size: 0.8rem;
    padding: 0.375rem 0.5rem;
  }
}
</style>
