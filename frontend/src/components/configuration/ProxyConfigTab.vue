<template>
  <div class="proxy-config-tab">
    <div class="d-flex justify-content-between align-items-center mb-3">
      <h6 class="mb-0">Credential Vault</h6>
    </div>

    <p class="text-muted small mb-3">
      Credential values are write-only. Values cannot be viewed after creation.
      Credentials are injected into the proxy sidecar by name at session start.
    </p>

    <!-- Credential table -->
    <div v-if="proxyStore.credentials.length > 0" class="table-responsive mb-3">
      <table class="table table-sm table-hover">
        <thead>
          <tr>
            <th>Name</th>
            <th>Host Pattern</th>
            <th>Header</th>
            <th>Delivery</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="cred in proxyStore.credentials" :key="cred.name">
            <td class="font-monospace small">{{ cred.name }}</td>
            <td class="small">{{ cred.host_pattern }}</td>
            <td class="small">{{ cred.header_name }}</td>
            <td class="small">
              <span v-if="cred.delivery?.type === 'env'">env: {{ cred.delivery.var }}</span>
              <span v-else-if="cred.delivery?.type === 'file'">file: {{ cred.delivery.path }}</span>
              <span v-else class="text-muted">—</span>
            </td>
            <td class="text-end">
              <button
                class="btn btn-outline-danger btn-sm"
                @click="deleteCredential(cred.name)"
                title="Delete credential"
              >Delete</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-else class="alert alert-secondary small mb-3">
      No credentials configured. Add one below to enable credential injection for proxy-enabled sessions.
    </div>

    <!-- Add credential form (inline) -->
    <div class="card">
      <div class="card-header py-2">
        <button
          class="btn btn-link btn-sm p-0 text-decoration-none"
          @click="showAddForm = !showAddForm"
        >
          {{ showAddForm ? '− Hide' : '+ Add Credential' }}
        </button>
      </div>
      <div v-if="showAddForm" class="card-body">
        <div class="row g-2">
          <div class="col-6">
            <label class="form-label small mb-1">Name <span class="text-danger">*</span></label>
            <input
              v-model="form.name"
              type="text"
              class="form-control form-control-sm"
              placeholder="github_token"
            />
          </div>
          <div class="col-6">
            <label class="form-label small mb-1">Host Pattern <span class="text-danger">*</span></label>
            <input
              v-model="form.host_pattern"
              type="text"
              class="form-control form-control-sm"
              placeholder="api.github.com"
            />
          </div>
          <div class="col-6">
            <label class="form-label small mb-1">Header Name</label>
            <input
              v-model="form.header_name"
              type="text"
              class="form-control form-control-sm"
              placeholder="Authorization"
            />
          </div>
          <div class="col-6">
            <label class="form-label small mb-1">Value Format</label>
            <input
              v-model="form.value_format"
              type="text"
              class="form-control form-control-sm"
              placeholder="Bearer {value}"
            />
          </div>
          <div class="col-12">
            <label class="form-label small mb-1">Secret Value <span class="text-danger">*</span></label>
            <input
              v-model="form.real_value"
              type="password"
              class="form-control form-control-sm"
              placeholder="Secret value (write-only)"
              autocomplete="new-password"
            />
          </div>

          <!-- Delivery type -->
          <div class="col-12">
            <label class="form-label small mb-1">Delivery</label>
            <div class="btn-group btn-group-sm" role="group">
              <input
                type="radio"
                class="btn-check"
                v-model="form.delivery.type"
                value="env"
                id="delivery-env"
              />
              <label class="btn btn-outline-secondary" for="delivery-env">Env Var</label>
              <input
                type="radio"
                class="btn-check"
                v-model="form.delivery.type"
                value="file"
                id="delivery-file"
              />
              <label class="btn btn-outline-secondary" for="delivery-file">File</label>
            </div>
          </div>

          <div v-if="form.delivery.type === 'env'" class="col-12">
            <label class="form-label small mb-1">Env Variable Name</label>
            <input
              v-model="form.delivery.var"
              type="text"
              class="form-control form-control-sm"
              placeholder="GH_TOKEN"
            />
          </div>

          <div v-if="form.delivery.type === 'file'" class="col-6">
            <label class="form-label small mb-1">File Path</label>
            <input
              v-model="form.delivery.path"
              type="text"
              class="form-control form-control-sm"
              placeholder="/run/secrets/token"
            />
          </div>
          <div v-if="form.delivery.type === 'file'" class="col-6">
            <label class="form-label small mb-1">Content Template</label>
            <input
              v-model="form.delivery.content_template"
              type="text"
              class="form-control form-control-sm"
              placeholder="{placeholder}"
            />
          </div>

          <div v-if="formError" class="col-12">
            <div class="alert alert-danger alert-sm py-1 px-2 small mb-0">{{ formError }}</div>
          </div>

          <div class="col-12 d-flex gap-2">
            <button
              class="btn btn-primary btn-sm"
              :disabled="saving || !form.name || !form.host_pattern || !form.real_value"
              @click="saveCredential"
            >
              <span v-if="saving" class="spinner-border spinner-border-sm me-1" role="status"></span>
              Save Credential
            </button>
            <button class="btn btn-outline-secondary btn-sm" @click="resetForm">Cancel</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { useProxyStore } from '@/stores/proxy'

const proxyStore = useProxyStore()

const showAddForm = ref(false)
const saving = ref(false)
const formError = ref(null)

const defaultForm = () => ({
  name: '',
  host_pattern: '',
  header_name: 'Authorization',
  value_format: 'Bearer {value}',
  real_value: '',
  delivery: {
    type: 'env',
    var: '',
    path: '',
    content_template: '{placeholder}',
  },
})

const form = reactive(defaultForm())

function resetForm() {
  Object.assign(form, defaultForm())
  formError.value = null
  showAddForm.value = false
}

async function saveCredential() {
  formError.value = null
  if (!form.value_format.includes('{value}')) {
    formError.value = 'Value Format must contain {value}'
    return
  }
  saving.value = true
  try {
    const delivery = { type: form.delivery.type }
    if (form.delivery.type === 'env') {
      delivery.var = form.delivery.var
    } else {
      delivery.path = form.delivery.path
      if (form.delivery.content_template) {
        delivery.content_template = form.delivery.content_template
      }
    }
    await proxyStore.createCredential({
      name: form.name,
      host_pattern: form.host_pattern,
      header_name: form.header_name,
      value_format: form.value_format,
      real_value: form.real_value,
      delivery,
    })
    resetForm()
  } catch (e) {
    formError.value = e.message || 'Failed to save credential'
  } finally {
    saving.value = false
  }
}

async function deleteCredential(name) {
  if (!confirm(`Delete credential '${name}'? This cannot be undone.`)) return
  try {
    await proxyStore.deleteCredential(name)
  } catch (e) {
    console.error('Failed to delete credential:', e)
  }
}

onMounted(() => {
  proxyStore.fetchCredentials()
})
</script>

<style scoped>
.proxy-config-tab {
  min-height: 200px;
}

.table th, .table td {
  vertical-align: middle;
}
</style>
