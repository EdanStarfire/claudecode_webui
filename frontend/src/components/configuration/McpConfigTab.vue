<template>
  <div class="mcp-config-tab">
    <p class="text-muted small mb-3">
      Define global MCP server configurations that can be attached to sessions and templates.
      Changes require a session restart to take effect.
    </p>

    <!-- Config list -->
    <div v-if="configStore.configList().length === 0 && !showForm" class="text-center text-muted py-3">
      <i class="bi bi-plug" style="font-size: 1.5rem;"></i>
      <p class="mt-2 mb-0">No MCP servers configured</p>
    </div>

    <div v-for="config in configStore.configList()" :key="config.id" class="config-item">
      <div class="d-flex align-items-center justify-content-between">
        <div class="d-flex align-items-center gap-2">
          <span class="badge" :class="config.enabled ? 'bg-success' : 'bg-secondary'">
            {{ config.type }}
          </span>
          <span class="fw-medium">{{ config.name }}</span>
          <span v-if="!config.enabled" class="badge bg-warning text-dark">disabled</span>
        </div>
        <div class="d-flex gap-1">
          <button class="btn btn-sm btn-outline-primary py-0 px-1" @click="editConfig(config)" title="Edit">
            <i class="bi bi-pencil" style="font-size: 0.75rem;"></i>
          </button>
          <button class="btn btn-sm btn-outline-danger py-0 px-1" @click="confirmDelete(config)" title="Delete">
            <i class="bi bi-trash" style="font-size: 0.75rem;"></i>
          </button>
        </div>
      </div>
      <div class="small text-muted mt-1">
        <span v-if="config.type === 'stdio'">{{ config.command }} {{ (config.args || []).join(' ') }}</span>
        <span v-else>{{ config.url }}</span>
      </div>
    </div>

    <!-- Add button -->
    <button
      v-if="!showForm"
      class="btn btn-outline-primary btn-sm mt-2"
      @click="startCreate"
    >
      <i class="bi bi-plus"></i> Add MCP Server
    </button>

    <!-- Create/Edit form -->
    <div v-if="showForm" class="config-form mt-2 p-2 border rounded">
      <h6 class="mb-2">{{ editingId ? 'Edit' : 'Add' }} MCP Server</h6>

      <div class="mb-2">
        <label class="form-label">Name</label>
        <input type="text" class="form-control form-control-sm" v-model="form.name" placeholder="e.g., Sentry" />
      </div>

      <div class="mb-2">
        <label class="form-label">Type</label>
        <select class="form-select form-select-sm" v-model="form.type">
          <option value="stdio">stdio</option>
          <option value="sse">SSE</option>
          <option value="http">HTTP</option>
        </select>
      </div>

      <!-- stdio fields -->
      <template v-if="form.type === 'stdio'">
        <div class="mb-2">
          <label class="form-label">Command</label>
          <input type="text" class="form-control form-control-sm" v-model="form.command" placeholder="e.g., npx" />
        </div>
        <div class="mb-2">
          <label class="form-label">Arguments</label>
          <input
            type="text"
            class="form-control form-control-sm"
            :value="(form.args || []).join(' ')"
            @input="form.args = $event.target.value.split(/\s+/).filter(Boolean)"
            placeholder="e.g., -y @sentry/mcp-server"
          />
        </div>
        <div class="mb-2">
          <label class="form-label">Environment Variables</label>
          <div v-for="(val, key) in form.env" :key="key" class="d-flex gap-1 mb-1">
            <input type="text" class="form-control form-control-sm" :value="key" disabled style="flex: 1;" />
            <input
              type="text"
              class="form-control form-control-sm"
              :value="val"
              @input="form.env[key] = $event.target.value"
              style="flex: 2;"
            />
            <button class="btn btn-sm btn-outline-danger py-0 px-1" @click="removeEnvVar(key)">&times;</button>
          </div>
          <div class="d-flex gap-1">
            <input type="text" class="form-control form-control-sm" v-model="newEnvKey" placeholder="KEY" style="flex: 1;" />
            <input type="text" class="form-control form-control-sm" v-model="newEnvVal" placeholder="value" style="flex: 2;" />
            <button class="btn btn-sm btn-outline-primary py-0 px-1" @click="addEnvVar" :disabled="!newEnvKey.trim()">
              <i class="bi bi-plus"></i>
            </button>
          </div>
        </div>
      </template>

      <!-- sse/http fields -->
      <template v-else>
        <div class="mb-2">
          <label class="form-label">URL</label>
          <input type="text" class="form-control form-control-sm" v-model="form.url" placeholder="https://..." />
        </div>
        <div class="mb-2">
          <label class="form-label">Headers</label>
          <div v-for="(val, key) in form.headers" :key="key" class="d-flex gap-1 mb-1">
            <input type="text" class="form-control form-control-sm" :value="key" disabled style="flex: 1;" />
            <input
              type="text"
              class="form-control form-control-sm"
              :value="val"
              @input="form.headers[key] = $event.target.value"
              style="flex: 2;"
            />
            <button class="btn btn-sm btn-outline-danger py-0 px-1" @click="removeHeader(key)">&times;</button>
          </div>
          <div class="d-flex gap-1">
            <input type="text" class="form-control form-control-sm" v-model="newHeaderKey" placeholder="Header" style="flex: 1;" />
            <input type="text" class="form-control form-control-sm" v-model="newHeaderVal" placeholder="value" style="flex: 2;" />
            <button class="btn btn-sm btn-outline-primary py-0 px-1" @click="addHeader" :disabled="!newHeaderKey.trim()">
              <i class="bi bi-plus"></i>
            </button>
          </div>
        </div>
      </template>

      <div class="form-check mb-2">
        <input class="form-check-input" type="checkbox" id="mcp-enabled" v-model="form.enabled" />
        <label class="form-check-label" for="mcp-enabled">Enabled</label>
      </div>

      <div v-if="formError" class="alert alert-danger py-1 small mb-2">{{ formError }}</div>

      <div class="d-flex gap-2">
        <button class="btn btn-primary btn-sm" @click="saveForm" :disabled="saving">
          {{ saving ? 'Saving...' : (editingId ? 'Update' : 'Create') }}
        </button>
        <button class="btn btn-secondary btn-sm" @click="cancelForm">Cancel</button>
      </div>
    </div>

    <!-- Delete confirmation -->
    <div v-if="deletingConfig" class="mt-2 p-2 border border-danger rounded">
      <p class="small mb-2">Delete <strong>{{ deletingConfig.name }}</strong>? Sessions using this server will no longer have it on next restart.</p>
      <div class="d-flex gap-2">
        <button class="btn btn-danger btn-sm" @click="doDelete" :disabled="saving">Delete</button>
        <button class="btn btn-secondary btn-sm" @click="deletingConfig = null">Cancel</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { useMcpConfigStore } from '@/stores/mcpConfig'

const configStore = useMcpConfigStore()

const showForm = ref(false)
const editingId = ref(null)
const saving = ref(false)
const formError = ref(null)
const deletingConfig = ref(null)

const form = reactive({
  name: '',
  type: 'stdio',
  command: '',
  args: [],
  env: {},
  url: '',
  headers: {},
  enabled: true,
})

const newEnvKey = ref('')
const newEnvVal = ref('')
const newHeaderKey = ref('')
const newHeaderVal = ref('')

onMounted(() => {
  configStore.fetchConfigs()
})

function resetForm() {
  form.name = ''
  form.type = 'stdio'
  form.command = ''
  form.args = []
  form.env = {}
  form.url = ''
  form.headers = {}
  form.enabled = true
  editingId.value = null
  formError.value = null
  newEnvKey.value = ''
  newEnvVal.value = ''
  newHeaderKey.value = ''
  newHeaderVal.value = ''
}

function startCreate() {
  resetForm()
  showForm.value = true
}

function editConfig(config) {
  editingId.value = config.id
  form.name = config.name
  form.type = config.type
  form.command = config.command || ''
  form.args = [...(config.args || [])]
  form.env = { ...(config.env || {}) }
  form.url = config.url || ''
  form.headers = { ...(config.headers || {}) }
  form.enabled = config.enabled
  formError.value = null
  showForm.value = true
}

function cancelForm() {
  showForm.value = false
  resetForm()
}

function addEnvVar() {
  if (newEnvKey.value.trim()) {
    form.env[newEnvKey.value.trim()] = newEnvVal.value
    newEnvKey.value = ''
    newEnvVal.value = ''
  }
}

function removeEnvVar(key) {
  delete form.env[key]
}

function addHeader() {
  if (newHeaderKey.value.trim()) {
    form.headers[newHeaderKey.value.trim()] = newHeaderVal.value
    newHeaderKey.value = ''
    newHeaderVal.value = ''
  }
}

function removeHeader(key) {
  delete form.headers[key]
}

async function saveForm() {
  saving.value = true
  formError.value = null
  try {
    const data = {
      name: form.name,
      type: form.type,
      enabled: form.enabled,
    }
    if (form.type === 'stdio') {
      data.command = form.command
      data.args = form.args
      data.env = Object.keys(form.env).length > 0 ? form.env : null
    } else {
      data.url = form.url
      data.headers = Object.keys(form.headers).length > 0 ? form.headers : null
    }

    if (editingId.value) {
      await configStore.updateConfig(editingId.value, data)
    } else {
      await configStore.createConfig(data)
    }
    showForm.value = false
    resetForm()
  } catch (error) {
    formError.value = error?.data?.detail || error.message || 'Failed to save'
  } finally {
    saving.value = false
  }
}

function confirmDelete(config) {
  deletingConfig.value = config
}

async function doDelete() {
  saving.value = true
  try {
    await configStore.deleteConfig(deletingConfig.value.id)
    deletingConfig.value = null
  } catch (error) {
    formError.value = error.message || 'Failed to delete'
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.config-item {
  padding: 0.5rem;
  border-bottom: 1px solid var(--bs-border-color);
}

.config-item:last-child {
  border-bottom: none;
}

.config-form {
  background: var(--bs-gray-100, #f8f9fa);
}
</style>
