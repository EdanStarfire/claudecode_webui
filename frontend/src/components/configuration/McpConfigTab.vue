<template>
  <div class="mcp-config-tab">
    <p class="text-muted small mb-3">
      Define global MCP server configurations that can be attached to sessions and templates.
      Changes require a session restart to take effect.
    </p>

    <!-- Config list -->
    <div v-if="configStore.configList().length === 0 && !showForm" class="text-center text-muted py-3">
      <span style="font-size: 1.5rem;">&#x1F50C;</span>
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
          <button class="btn btn-sm btn-outline-secondary config-action-btn" @click="copyServer(config)" :title="copiedId === config.id ? 'Copied!' : 'Copy JSON'">
            {{ copiedId === config.id ? '✓' : '⎘' }}
          </button>
          <button class="btn btn-sm btn-outline-primary config-action-btn" @click="editConfig(config)" title="Edit">&#9998;</button>
          <button class="btn btn-sm btn-outline-danger config-action-btn" @click="confirmDelete(config)" title="Delete">&times;</button>
        </div>
      </div>
      <div class="small text-muted mt-1">
        <span v-if="config.type === 'stdio'">{{ config.command }} {{ (config.args || []).join(' ') }}</span>
        <span v-else>{{ config.url }}</span>
      </div>
    </div>

    <!-- Action buttons row -->
    <div class="d-flex gap-2 mt-2 flex-wrap">
      <button
        v-if="!showForm && !showImport"
        class="btn btn-outline-primary btn-sm"
        @click="startCreate"
      >
        + Add MCP Server
      </button>
      <button
        v-if="!showForm && !showImport && configStore.configList().length > 0"
        class="btn btn-outline-secondary btn-sm"
        @click="exportAll"
        :title="exportCopied ? 'Copied to clipboard!' : 'Export all servers as JSON'"
      >
        {{ exportCopied ? '✓ Copied' : '↑ Export All' }}
      </button>
      <button
        v-if="!showForm && !showImport"
        class="btn btn-outline-secondary btn-sm"
        @click="showImport = true"
      >
        ↓ Import
      </button>
    </div>

    <!-- Create/Edit form -->
    <div v-if="showForm" class="config-form mt-2 p-2 border rounded">
      <h6 class="mb-2">{{ editingId ? 'Edit' : 'Add' }} MCP Server</h6>

      <div class="mb-2">
        <label class="form-label">Name</label>
        <input type="text" class="form-control form-control-sm" v-model="form.name" placeholder="e.g., Sentry" />
      </div>

      <div class="mb-2">
        <label class="form-label">Type</label>
        <div class="model-btn-group">
          <button
            type="button"
            class="model-btn"
            :class="{ active: form.type === 'stdio' }"
            @click="form.type = 'stdio'"
          >STDIO</button>
          <button
            type="button"
            class="model-btn"
            :class="{ active: form.type === 'sse' }"
            @click="form.type = 'sse'"
          >SSE</button>
          <button
            type="button"
            class="model-btn"
            :class="{ active: form.type === 'http' }"
            @click="form.type = 'http'"
          >HTTP</button>
        </div>
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
            v-model="rawArgs"
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
              +
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
              +
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

    <!-- Import panel -->
    <div v-if="showImport" class="config-form mt-2 p-2 border rounded">
      <h6 class="mb-2">Import MCP Servers</h6>
      <p class="text-muted small mb-2">
        Paste a JSON object with server names as keys (standard <code>mcpServers</code> format).
        New servers will be added; existing servers (matched by name) will be updated.
      </p>

      <!-- Paste area (only shown before preview) -->
      <template v-if="!importPreview">
        <div class="mb-2">
          <label class="form-label">JSON Configuration</label>
          <textarea
            class="form-control form-control-sm font-monospace"
            v-model="importJson"
            rows="6"
            placeholder='{"my-server": {"type": "stdio", "command": "npx", "args": ["-y", "@my/mcp"]}}'
          ></textarea>
        </div>
        <div v-if="importError" class="alert alert-danger py-1 small mb-2">{{ importError }}</div>
        <div class="d-flex gap-2">
          <button class="btn btn-primary btn-sm" @click="previewImport" :disabled="saving || !importJson.trim()">
            {{ saving ? 'Validating...' : 'Preview Import' }}
          </button>
          <button class="btn btn-secondary btn-sm" @click="cancelImport">Cancel</button>
        </div>
      </template>

      <!-- Preview results -->
      <template v-else>
        <div class="mb-2">
          <div class="d-flex gap-3 small mb-2">
            <span class="text-success">+ {{ importPreview.summary.create }} to add</span>
            <span class="text-warning">~ {{ importPreview.summary.update }} to update</span>
            <span v-if="importPreview.summary.skip > 0" class="text-danger">✗ {{ importPreview.summary.skip }} skipped</span>
          </div>
          <div v-for="item in importPreview.preview" :key="item.name" class="preview-item small">
            <span
              class="badge me-1"
              :class="{
                'bg-success': item.action === 'create',
                'bg-warning text-dark': item.action === 'update',
                'bg-danger': item.action === 'skip',
              }"
            >{{ item.action }}</span>
            <span class="fw-medium">{{ item.name || '(unnamed)' }}</span>
            <span v-if="item.reason" class="text-muted ms-1">— {{ item.reason }}</span>
          </div>
        </div>
        <div v-if="importError" class="alert alert-danger py-1 small mb-2">{{ importError }}</div>
        <div v-if="importSuccess" class="alert alert-success py-1 small mb-2">{{ importSuccess }}</div>
        <div class="d-flex gap-2">
          <button
            class="btn btn-primary btn-sm"
            @click="commitImport"
            :disabled="saving || importPreview.summary.create + importPreview.summary.update === 0"
          >
            {{ saving ? 'Importing...' : `Import ${importPreview.summary.create + importPreview.summary.update} Server(s)` }}
          </button>
          <button class="btn btn-outline-secondary btn-sm" @click="importPreview = null">Back</button>
          <button class="btn btn-secondary btn-sm" @click="cancelImport">Cancel</button>
        </div>
      </template>
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

const rawArgs = ref('')
const newEnvKey = ref('')
const newEnvVal = ref('')
const newHeaderKey = ref('')
const newHeaderVal = ref('')

// Export state
const copiedId = ref(null)
const exportCopied = ref(false)

// Import state
const showImport = ref(false)
const importJson = ref('')
const importError = ref(null)
const importSuccess = ref(null)
const importPreview = ref(null)

onMounted(() => {
  configStore.fetchConfigs()
})

function resetForm() {
  form.name = ''
  form.type = 'stdio'
  form.command = ''
  form.args = []
  rawArgs.value = ''
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
  rawArgs.value = (config.args || []).join(' ')
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
      data.args = rawArgs.value.split(/\s+/).filter(Boolean)
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

// Export: copy a single server as named dict {"serverName": {...}}
async function copyServer(config) {
  try {
    const result = await configStore.exportConfigs([config.id])
    await navigator.clipboard.writeText(JSON.stringify(result, null, 2))
    copiedId.value = config.id
    setTimeout(() => { copiedId.value = null }, 2000)
  } catch {
    // fallback: ignored
  }
}

// Export: copy all servers as named dict
async function exportAll() {
  try {
    const result = await configStore.exportConfigs()
    await navigator.clipboard.writeText(JSON.stringify(result, null, 2))
    exportCopied.value = true
    setTimeout(() => { exportCopied.value = false }, 2000)
  } catch {
    // fallback: ignored
  }
}

// Parse import JSON — accepts named dict {"name": {...}} format
function parseImportJson(raw) {
  const parsed = JSON.parse(raw.trim())
  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
    throw new Error('Expected a JSON object with server names as keys')
  }
  return parsed
}

// Import: validate and preview
async function previewImport() {
  importError.value = null
  saving.value = true
  try {
    let servers
    try {
      servers = parseImportJson(importJson.value)
    } catch (e) {
      importError.value = `Invalid JSON: ${e.message}`
      return
    }
    const result = await configStore.importConfigs(servers, true)
    importPreview.value = result
  } catch (error) {
    importError.value = error?.data?.detail || error.message || 'Validation failed'
  } finally {
    saving.value = false
  }
}

// Import: commit after preview
async function commitImport() {
  importError.value = null
  importSuccess.value = null
  saving.value = true
  try {
    const servers = parseImportJson(importJson.value)
    const result = await configStore.importConfigs(servers, false)
    const count = result.imported.length
    importSuccess.value = `Successfully imported ${count} server(s).`
    // Refresh the config list
    await configStore.fetchConfigs()
    // Auto-close after short delay
    setTimeout(() => {
      showImport.value = false
      importJson.value = ''
      importPreview.value = null
      importError.value = null
      importSuccess.value = null
    }, 1500)
  } catch (error) {
    importError.value = error?.data?.detail || error.message || 'Import failed'
  } finally {
    saving.value = false
  }
}

function cancelImport() {
  showImport.value = false
  importJson.value = ''
  importPreview.value = null
  importError.value = null
  importSuccess.value = null
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

.config-action-btn {
  width: 1.5rem;
  height: 1.5rem;
  padding: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 0.8rem;
  line-height: 1;
}

.preview-item {
  padding: 0.2rem 0;
  border-bottom: 1px solid var(--bs-border-color);
}

.preview-item:last-child {
  border-bottom: none;
}
</style>
