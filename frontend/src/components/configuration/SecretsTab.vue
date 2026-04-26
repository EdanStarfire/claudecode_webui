<template>
  <div class="secrets-tab">
    <!-- Backend warning banner -->
    <div v-if="secretsStore.backendWarning" class="alert alert-warning small py-2 mb-3">
      <strong>Keyring Warning:</strong> {{ secretsStore.backendWarning }}
    </div>

    <div class="d-flex justify-content-between align-items-center mb-2">
      <h6 class="mb-0">
        Secrets Vault
        <span v-if="secretsStore.activeBackend" class="badge bg-secondary ms-2" style="font-size: 0.7rem; font-weight: normal;">
          {{ secretsStore.activeBackend }}
        </span>
      </h6>
    </div>

    <p class="text-muted small mb-3">
      Secret values are write-only and stored in the OS keyring.
      Values cannot be viewed after creation. Secrets are assigned to profiles or templates
      and injected at session start.
    </p>

    <!-- Secrets table -->
    <div v-if="secretsStore.secrets.length > 0" class="table-responsive mb-3">
      <table class="table table-sm table-hover">
        <thead>
          <tr>
            <th>Name</th>
            <th>Type</th>
            <th>Target Hosts</th>
            <th>Inject Env</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="secret in secretsStore.secrets" :key="secret.name">
            <td class="font-monospace small">{{ secret.name }}</td>
            <td>
              <span class="badge" :class="typeBadgeClass(secret.type)">{{ secret.type }}</span>
            </td>
            <td class="small">
              <span v-if="secret.target_hosts?.length">
                {{ secret.target_hosts.slice(0, 2).join(', ') }}
                <span v-if="secret.target_hosts.length > 2" class="text-muted">+{{ secret.target_hosts.length - 2 }}</span>
              </span>
              <span v-else class="text-muted">—</span>
            </td>
            <td class="small font-monospace">{{ secret.inject_env || '—' }}</td>
            <td class="text-end">
              <div class="btn-group btn-group-sm">
                <button
                  class="btn btn-outline-secondary"
                  @click="editSecret(secret)"
                  title="Edit secret (update value/metadata)"
                >Edit</button>
                <button
                  class="btn btn-outline-danger"
                  @click="confirmDelete(secret.name)"
                  title="Delete secret"
                >Delete</button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-else class="alert alert-secondary small mb-3">
      No secrets configured. Add one below to enable secret injection at session start.
    </div>

    <!-- Add / Edit secret form -->
    <div class="card">
      <div class="card-header py-2">
        <button
          class="btn btn-link btn-sm p-0 text-decoration-none"
          @click="toggleForm"
        >
          {{ showForm ? '− Hide' : (editingName ? `✎ Editing: ${editingName}` : '+ Add Secret') }}
        </button>
      </div>
      <div v-if="showForm" class="card-body">
        <div class="row g-2">
          <!-- Name (read-only when editing) -->
          <div class="col-6">
            <label class="form-label small mb-1">Name <span class="text-danger">*</span></label>
            <input
              v-model="form.name"
              type="text"
              class="form-control form-control-sm font-monospace"
              placeholder="github-token"
              :disabled="!!editingName"
            />
            <div class="form-text" style="font-size: 0.7rem">slug: a-z0-9_- only</div>
          </div>

          <!-- Type -->
          <div class="col-6">
            <label class="form-label small mb-1">Type</label>
            <select v-model="form.type" class="form-select form-select-sm">
              <option value="api_key">api_key</option>
              <option value="bearer">bearer</option>
              <option value="basic_auth">basic_auth</option>
              <option value="oauth2">oauth2</option>
              <option value="generic">generic</option>
              <option value="ssh">ssh</option>
            </select>
          </div>

          <!-- Target Hosts -->
          <div class="col-12">
            <label class="form-label small mb-1">Target Hosts <span class="text-danger">*</span></label>
            <input
              v-model="form.target_hosts_raw"
              type="text"
              class="form-control form-control-sm"
              placeholder="api.github.com, *.example.com"
            />
            <div class="form-text" style="font-size: 0.7rem">Comma-separated hostnames or wildcard patterns</div>
          </div>

          <!-- Secret Value -->
          <div class="col-12">
            <label class="form-label small mb-1">
              Secret Value
              <span v-if="!editingName" class="text-danger">*</span>
              <span v-else class="text-muted">(leave blank to keep existing)</span>
            </label>
            <input
              v-model="form.value"
              type="password"
              class="form-control form-control-sm"
              :placeholder="editingName ? 'Leave blank to keep current value' : 'Secret value (write-only)'"
              autocomplete="new-password"
            />
          </div>

          <!-- Inject Env -->
          <div class="col-6">
            <label class="form-label small mb-1">Inject Env Var</label>
            <input
              v-model="form.inject_env"
              type="text"
              class="form-control form-control-sm font-monospace"
              placeholder="GITHUB_TOKEN"
            />
          </div>

          <!-- Inject File path -->
          <div class="col-6">
            <label class="form-label small mb-1">Inject File Path</label>
            <input
              v-model="form.inject_file_path"
              type="text"
              class="form-control form-control-sm font-monospace"
              placeholder="/root/.config/gh/hosts.yml"
            />
          </div>

          <!-- Inject File format (shown only if path set) -->
          <template v-if="form.inject_file_path">
            <div class="col-4">
              <label class="form-label small mb-1">File Format</label>
              <select v-model="form.inject_file_format" class="form-select form-select-sm">
                <option value="raw">raw</option>
                <option value="yaml">yaml</option>
                <option value="json">json</option>
                <option value="toml">toml</option>
              </select>
            </div>
            <div class="col-4">
              <label class="form-label small mb-1">Permissions</label>
              <input
                v-model="form.inject_file_permissions"
                type="text"
                class="form-control form-control-sm font-monospace"
                placeholder="0600"
              />
            </div>
            <div class="col-4">
              <label class="form-label small mb-1">Key Path</label>
              <input
                v-model="form.inject_file_key_path"
                type="text"
                class="form-control form-control-sm font-monospace"
                placeholder="github.com.oauth_token"
              />
            </div>
          </template>

          <!-- Error -->
          <div v-if="formError" class="col-12">
            <div class="alert alert-danger py-1 px-2 small mb-0">{{ formError }}</div>
          </div>

          <div class="col-12 d-flex gap-2">
            <button
              class="btn btn-primary btn-sm"
              :disabled="saving || !canSave"
              @click="saveSecret"
            >
              <span v-if="saving" class="spinner-border spinner-border-sm me-1" role="status"></span>
              {{ editingName ? 'Update' : 'Save Secret' }}
            </button>
            <button class="btn btn-outline-secondary btn-sm" @click="resetForm">Cancel</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { useSecretsStore } from '@/stores/secrets'

const secretsStore = useSecretsStore()

const showForm = ref(false)
const editingName = ref(null)
const saving = ref(false)
const formError = ref(null)

const defaultForm = () => ({
  name: '',
  type: 'generic',
  target_hosts_raw: '',
  value: '',
  inject_env: '',
  inject_file_path: '',
  inject_file_format: 'raw',
  inject_file_permissions: '0600',
  inject_file_key_path: '',
})

const form = reactive(defaultForm())

const canSave = computed(() => {
  if (!form.name || !form.target_hosts_raw) return false
  if (!editingName.value && !form.value) return false
  return true
})

function toggleForm() {
  if (showForm.value && !editingName.value) {
    resetForm()
  } else {
    showForm.value = !showForm.value
  }
}

function editSecret(secret) {
  editingName.value = secret.name
  Object.assign(form, defaultForm())
  form.name = secret.name
  form.type = secret.type || 'generic'
  form.target_hosts_raw = (secret.target_hosts || []).join(', ')
  form.inject_env = secret.inject_env || ''
  if (secret.inject_file) {
    form.inject_file_path = secret.inject_file.path || ''
    form.inject_file_format = secret.inject_file.format || 'raw'
    form.inject_file_permissions = secret.inject_file.permissions || '0600'
    form.inject_file_key_path = secret.inject_file.key_path || ''
  }
  formError.value = null
  showForm.value = true
}

function resetForm() {
  Object.assign(form, defaultForm())
  editingName.value = null
  formError.value = null
  showForm.value = false
}

function buildPayload() {
  const hosts = form.target_hosts_raw
    .split(',')
    .map(h => h.trim())
    .filter(Boolean)

  const payload = {
    name: form.name,
    type: form.type,
    target_hosts: hosts,
  }
  if (form.value) payload.value = form.value
  if (form.inject_env) payload.inject_env = form.inject_env
  if (form.inject_file_path) {
    payload.inject_file = {
      path: form.inject_file_path,
      format: form.inject_file_format,
      permissions: form.inject_file_permissions,
      key_path: form.inject_file_key_path || null,
    }
  }
  return payload
}

async function saveSecret() {
  formError.value = null
  saving.value = true
  try {
    const payload = buildPayload()
    if (editingName.value) {
      await secretsStore.updateSecret(editingName.value, payload)
    } else {
      await secretsStore.createSecret(payload)
    }
    resetForm()
  } catch (e) {
    formError.value = e.message || 'Failed to save secret'
  } finally {
    saving.value = false
  }
}

async function confirmDelete(name) {
  if (!confirm(`Delete secret '${name}'? This also removes the value from the OS keyring.`)) return
  try {
    await secretsStore.deleteSecret(name)
  } catch (e) {
    console.error('Failed to delete secret:', e)
  }
}

function typeBadgeClass(type) {
  const map = {
    api_key: 'bg-primary',
    bearer: 'bg-success',
    basic_auth: 'bg-warning text-dark',
    oauth2: 'bg-info text-dark',
    generic: 'bg-secondary',
    ssh: 'bg-dark',
  }
  return map[type] || 'bg-secondary'
}

onMounted(() => {
  secretsStore.fetchSecrets()
  secretsStore.fetchBackendStatus()
})
</script>

<style scoped>
.secrets-tab {
  min-height: 200px;
}
.table th, .table td {
  vertical-align: middle;
}
</style>
