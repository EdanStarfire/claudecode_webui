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
              <!-- Expiry indicator for oauth2 -->
              <span
                v-if="secret.type === 'oauth2' && expiryLabel(secret)"
                class="badge ms-1"
                :class="expiryBadgeClass(secret)"
                :title="expiryTitle(secret)"
              >{{ expiryLabel(secret) }}</span>
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
                <!-- Manual refresh for oauth2 -->
                <button
                  v-if="secret.type === 'oauth2'"
                  class="btn btn-outline-info"
                  :disabled="refreshingName === secret.name"
                  @click="triggerRefresh(secret.name)"
                  title="Manually refresh OAuth2 access token now"
                >
                  <span v-if="refreshingName === secret.name" class="spinner-border spinner-border-sm" role="status"></span>
                  <span v-else>↻</span>
                </button>
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

          <!-- basic_auth: username -->
          <template v-if="form.type === 'basic_auth'">
            <div class="col-6">
              <label class="form-label small mb-1">Username <span class="text-danger">*</span></label>
              <input
                v-model="form.username"
                type="text"
                class="form-control form-control-sm"
                placeholder="user@example.com"
                autocomplete="off"
              />
              <div class="form-text" style="font-size: 0.7rem">Stored as plaintext metadata (not in keyring)</div>
            </div>
          </template>

          <!-- Secret Value (password for basic_auth) -->
          <div :class="form.type === 'basic_auth' ? 'col-6' : 'col-12'">
            <label class="form-label small mb-1">
              {{ form.type === 'basic_auth' ? 'Password' : 'Secret Value' }}
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

          <!-- api_key: injection spec -->
          <template v-if="form.type === 'api_key'">
            <div class="col-12">
              <div class="border rounded p-2 bg-light">
                <div class="small fw-semibold mb-2 text-secondary">Injection Config</div>
                <div class="row g-2">
                  <div class="col-4">
                    <label class="form-label small mb-1">Location</label>
                    <select v-model="form.injection_location" class="form-select form-select-sm">
                      <option value="header">header</option>
                      <option value="query_param">query_param</option>
                    </select>
                  </div>
                  <template v-if="form.injection_location === 'header'">
                    <div class="col-4">
                      <label class="form-label small mb-1">Header Name</label>
                      <input
                        v-model="form.injection_header_name"
                        type="text"
                        class="form-control form-control-sm font-monospace"
                        placeholder="Authorization"
                      />
                    </div>
                    <div class="col-4">
                      <label class="form-label small mb-1">Prefix</label>
                      <input
                        v-model="form.injection_prefix"
                        type="text"
                        class="form-control form-control-sm font-monospace"
                        placeholder="Bearer"
                      />
                      <div class="form-text" style="font-size: 0.7rem">Leave blank for no prefix</div>
                    </div>
                  </template>
                  <template v-if="form.injection_location === 'query_param'">
                    <div class="col-8">
                      <label class="form-label small mb-1">Query Param Name <span class="text-danger">*</span></label>
                      <input
                        v-model="form.injection_param_name"
                        type="text"
                        class="form-control form-control-sm font-monospace"
                        placeholder="api_key"
                      />
                    </div>
                  </template>
                </div>
              </div>
            </div>
          </template>

          <!-- oauth2: refresh + scrub blocks -->
          <template v-if="form.type === 'oauth2'">
            <div class="col-12">
              <div class="border rounded p-2 bg-light mb-2">
                <div class="small fw-semibold mb-2 text-secondary">OAuth2 Refresh</div>
                <div class="row g-2">
                  <div class="col-12">
                    <label class="form-label small mb-1">Token URL <span class="text-danger">*</span></label>
                    <input
                      v-model="form.refresh_token_url"
                      type="text"
                      class="form-control form-control-sm font-monospace"
                      placeholder="https://github.com/login/oauth/access_token"
                    />
                  </div>
                  <div class="col-6">
                    <label class="form-label small mb-1">Client ID <span class="text-danger">*</span></label>
                    <input
                      v-model="form.refresh_client_id"
                      type="text"
                      class="form-control form-control-sm font-monospace"
                      placeholder="Iv1.abc123"
                    />
                  </div>
                  <div class="col-6">
                    <label class="form-label small mb-1">Refresh Token Secret <span class="text-danger">*</span></label>
                    <select v-model="form.refresh_token_secret_name" class="form-select form-select-sm font-monospace">
                      <option value="">— select secret —</option>
                      <option v-for="s in otherSecrets" :key="s.name" :value="s.name">{{ s.name }}</option>
                    </select>
                    <div class="form-text" style="font-size: 0.7rem">Name of the sibling refresh_token secret</div>
                  </div>
                  <div class="col-6">
                    <label class="form-label small mb-1">Client Secret (optional)</label>
                    <select v-model="form.refresh_client_secret_secret_name" class="form-select form-select-sm font-monospace">
                      <option value="">— none —</option>
                      <option v-for="s in otherSecrets" :key="s.name" :value="s.name">{{ s.name }}</option>
                    </select>
                  </div>
                  <div class="col-3">
                    <label class="form-label small mb-1">Buffer (s)</label>
                    <input
                      v-model.number="form.refresh_buffer_seconds"
                      type="number"
                      class="form-control form-control-sm"
                      placeholder="60"
                      min="0"
                    />
                  </div>
                  <div class="col-3">
                    <label class="form-label small mb-1">Expires At</label>
                    <input
                      v-model="form.refresh_expires_at"
                      type="datetime-local"
                      class="form-control form-control-sm"
                    />
                  </div>
                </div>
              </div>

              <div class="border rounded p-2 bg-light">
                <div class="small fw-semibold mb-2 text-secondary">Response Capture (scrub)</div>
                <div class="row g-2">
                  <div class="col-12">
                    <label class="form-label small mb-1">URL Path Pattern</label>
                    <input
                      v-model="form.scrub_url_path"
                      type="text"
                      class="form-control form-control-sm font-monospace"
                      placeholder="/login/oauth/access_token"
                    />
                    <div class="form-text" style="font-size: 0.7rem">Substring match on request path to trigger capture</div>
                  </div>
                  <div class="col-6">
                    <label class="form-label small mb-1">JSONPath Matcher</label>
                    <input
                      v-model="form.scrub_matcher_jsonpath"
                      type="text"
                      class="form-control form-control-sm font-monospace"
                      placeholder="$.access_token"
                    />
                  </div>
                  <div class="col-6">
                    <label class="form-label small mb-1">Regex Matcher</label>
                    <input
                      v-model="form.scrub_matcher_regex"
                      type="text"
                      class="form-control form-control-sm font-monospace"
                      placeholder="access_token=([^&]+)"
                    />
                  </div>
                  <div class="col-12">
                    <div class="form-check form-check-sm">
                      <input
                        v-model="form.scrub_update_on_change"
                        class="form-check-input"
                        type="checkbox"
                        id="scrubUpdateOnChange"
                      />
                      <label class="form-check-label small" for="scrubUpdateOnChange">
                        Auto-update keyring when captured value changes
                      </label>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </template>

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
const refreshingName = ref(null)

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
  // basic_auth
  username: '',
  // api_key injection spec
  injection_location: 'header',
  injection_header_name: 'Authorization',
  injection_prefix: 'Bearer',
  injection_param_name: '',
  // oauth2 refresh spec
  refresh_token_url: '',
  refresh_client_id: '',
  refresh_token_secret_name: '',
  refresh_client_secret_secret_name: '',
  refresh_buffer_seconds: 60,
  refresh_expires_at: '',
  // scrub spec (oauth2)
  scrub_url_path: '',
  scrub_matcher_jsonpath: '',
  scrub_matcher_regex: '',
  scrub_update_on_change: true,
})

const form = reactive(defaultForm())

const canSave = computed(() => {
  if (!form.name || !form.target_hosts_raw) return false
  if (!editingName.value && !form.value) return false
  if (form.type === 'basic_auth' && !form.username) return false
  if (form.type === 'api_key' && form.injection_location === 'query_param' && !form.injection_param_name) return false
  if (form.type === 'oauth2') {
    if (!form.refresh_token_url || !form.refresh_client_id || !form.refresh_token_secret_name) return false
  }
  return true
})

/** Other secrets available for sibling-secret dropdowns. */
const otherSecrets = computed(() =>
  secretsStore.secrets.filter(s => s.name !== editingName.value)
)

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
  form.username = secret.username || ''
  if (secret.inject_file) {
    form.inject_file_path = secret.inject_file.path || ''
    form.inject_file_format = secret.inject_file.format || 'raw'
    form.inject_file_permissions = secret.inject_file.permissions || '0600'
    form.inject_file_key_path = secret.inject_file.key_path || ''
  }
  if (secret.injection) {
    form.injection_location = secret.injection.location || 'header'
    form.injection_header_name = secret.injection.header_name || 'Authorization'
    form.injection_prefix = secret.injection.prefix !== undefined ? secret.injection.prefix : 'Bearer'
    form.injection_param_name = secret.injection.param_name || ''
  }
  if (secret.refresh) {
    form.refresh_token_url = secret.refresh.token_url || ''
    form.refresh_client_id = secret.refresh.client_id || ''
    form.refresh_token_secret_name = secret.refresh.refresh_token_secret_name || ''
    form.refresh_client_secret_secret_name = secret.refresh.client_secret_secret_name || ''
    form.refresh_buffer_seconds = secret.refresh.buffer_seconds ?? 60
    form.refresh_expires_at = secret.refresh.expires_at
      ? new Date(secret.refresh.expires_at).toISOString().slice(0, 16)
      : ''
  }
  if (secret.scrub) {
    form.scrub_url_path = secret.scrub.url_path || ''
    form.scrub_matcher_jsonpath = secret.scrub.matcher_jsonpath || ''
    form.scrub_matcher_regex = secret.scrub.matcher_regex || ''
    form.scrub_update_on_change = secret.scrub.update_on_change !== false
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
  if (form.type === 'basic_auth' && form.username) {
    payload.username = form.username
  }
  if (form.type === 'api_key') {
    payload.injection = {
      location: form.injection_location,
      header_name: form.injection_header_name || 'Authorization',
      prefix: form.injection_prefix,
      param_name: form.injection_param_name || null,
    }
  }
  if (form.type === 'oauth2') {
    payload.refresh = {
      token_url: form.refresh_token_url,
      client_id: form.refresh_client_id,
      refresh_token_secret_name: form.refresh_token_secret_name,
      client_secret_secret_name: form.refresh_client_secret_secret_name || null,
      buffer_seconds: form.refresh_buffer_seconds,
      expires_at: form.refresh_expires_at ? new Date(form.refresh_expires_at).toISOString() : null,
    }
    payload.scrub = {
      url_path: form.scrub_url_path || null,
      matcher_jsonpath: form.scrub_matcher_jsonpath || null,
      matcher_regex: form.scrub_matcher_regex || null,
      update_on_change: form.scrub_update_on_change,
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

async function triggerRefresh(name) {
  refreshingName.value = name
  try {
    await secretsStore.refreshSecret(name)
  } catch (e) {
    console.error('Failed to refresh secret:', e)
    alert(`Refresh failed: ${e.message || e}`)
  } finally {
    refreshingName.value = null
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

function expiryLabel(secret) {
  const expiresAt = secret.refresh?.expires_at
  if (!expiresAt) return null
  const exp = new Date(expiresAt)
  const now = new Date()
  const diffMs = exp - now
  if (diffMs < 0) return 'expired'
  const diffMin = Math.floor(diffMs / 60000)
  if (diffMin < 60) return `${diffMin}m`
  const diffH = Math.floor(diffMs / 3600000)
  if (diffH < 48) return `${diffH}h`
  return `${Math.floor(diffH / 24)}d`
}

function expiryBadgeClass(secret) {
  const expiresAt = secret.refresh?.expires_at
  if (!expiresAt) return 'bg-secondary'
  const exp = new Date(expiresAt)
  const diffMs = exp - new Date()
  if (diffMs < 0) return 'bg-danger'
  if (diffMs < 300000) return 'bg-danger'     // < 5 min
  if (diffMs < 3600000) return 'bg-warning text-dark'  // < 1 hr
  return 'bg-success'
}

function expiryTitle(secret) {
  const expiresAt = secret.refresh?.expires_at
  if (!expiresAt) return ''
  return `Expires: ${new Date(expiresAt).toLocaleString()}`
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
