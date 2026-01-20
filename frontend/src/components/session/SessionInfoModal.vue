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
          <div v-if="!initData" class="text-center text-muted py-4">
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
                :value="initData.cwd || 'Not specified'"
                readonly
                @click="selectPath"
              />
            </div>

            <!-- Model -->
            <div class="mb-3">
              <h6 class="text-muted">Model</h6>
              <div>{{ getModelDisplayName(initData.model) }}</div>
            </div>

            <!-- Permission Mode -->
            <div class="mb-3">
              <h6 class="text-muted">Permission Mode</h6>
              <div>{{ initData.permissionMode || 'default' }}</div>
            </div>

            <!-- Pre-Authorized Tools -->
            <div class="mb-3">
              <h6 class="text-muted">Pre-Authorized Tools</h6>
              <div v-if="(initData.allowed_tools || initData.tools) && (initData.allowed_tools || initData.tools).length > 0">
                <div class="d-flex flex-wrap gap-1">
                  <span
                    v-for="tool in (initData.allowed_tools || initData.tools)"
                    :key="tool"
                    class="badge bg-secondary"
                  >
                    {{ tool }}
                  </span>
                </div>
              </div>
              <div v-else class="text-muted small">No pre-authorized tools (will prompt for permissions)</div>
            </div>

            <!-- Commands -->
            <div v-if="initData.commands && initData.commands.length > 0" class="mb-3">
              <h6 class="text-muted">Commands</h6>
              <div class="d-flex flex-wrap gap-1">
                <span
                  v-for="command in initData.commands"
                  :key="command"
                  class="badge bg-info"
                >
                  {{ command }}
                </span>
              </div>
            </div>

            <!-- System Prompt -->
            <div v-if="initData.systemPrompt" class="mb-3">
              <h6 class="text-muted">System Prompt</h6>
              <pre class="bg-light p-2 rounded small" style="max-height: 200px; overflow-y: auto;">{{ formatSystemPrompt(initData.systemPrompt) }}</pre>
            </div>

            <!-- Settings (full data dump for advanced users) -->
            <div class="mb-3">
              <h6 class="text-muted">
                Raw Configuration
                <button
                  class="btn btn-sm btn-outline-secondary ms-2"
                  @click="showRawData = !showRawData"
                >
                  {{ showRawData ? 'Hide' : 'Show' }}
                </button>
              </h6>
              <pre
                v-if="showRawData"
                class="bg-light p-2 rounded small"
                style="max-height: 300px; overflow-y: auto;"
              >{{ JSON.stringify(initData, null, 2) }}</pre>
            </div>
          </div>
        </div>
        <div class="modal-footer">
          <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Close</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useSessionStore } from '@/stores/session'
import { useMessageStore } from '@/stores/message'
import { useUIStore } from '@/stores/ui'

const sessionStore = useSessionStore()
const messageStore = useMessageStore()
const uiStore = useUIStore()

// State
const sessionId = ref(null)
const showRawData = ref(false)
const modalElement = ref(null)
let modalInstance = null

// Model display names mapping
const modelDisplayNames = {
  'sonnet': 'Sonnet 4.5',
  'opus': 'Opus 4.5',
  'haiku': 'Haiku 4.5',
  'opusplan': 'OpusPlan (Opus + Sonnet)'
}

// Get init data - try session store first, then search messages
const initData = computed(() => {
  if (!sessionId.value) {
    return null
  }

  // First check if we have it in session store
  const storedInitData = sessionStore.initData.get(sessionId.value)
  if (storedInitData) {
    return storedInitData
  }

  // Fall back to searching messages for init message
  const messages = messageStore.messagesBySession.get(sessionId.value) || []
  const initMessage = messages.find(msg =>
    msg.type === 'system' &&
    (msg.subtype === 'init' || msg.metadata?.subtype === 'init') &&
    msg.metadata?.init_data
  )

  return initMessage?.metadata?.init_data || null
})

// Get human-readable model display name
function getModelDisplayName(modelId) {
  if (!modelId) {
    return 'Default (Sonnet 4.5)'
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
      showRawData.value = false  // Reset just the raw data toggle, not sessionId
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

/* Mobile responsive */
@media (max-width: 768px) {
  .path-input {
    font-size: 0.8rem;
    padding: 0.375rem 0.5rem;
  }
}
</style>
