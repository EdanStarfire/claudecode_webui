<template>
  <div
    ref="modalRef"
    class="modal fade"
    tabindex="-1"
    aria-labelledby="permissionPreviewModalLabel"
    aria-hidden="true"
  >
    <div class="modal-dialog modal-lg">
      <div class="modal-content">
        <div class="modal-header">
          <h5 class="modal-title" id="permissionPreviewModalLabel">
            Effective Permissions Preview
          </h5>
          <button
            type="button"
            class="btn-close"
            data-bs-dismiss="modal"
            aria-label="Close"
          ></button>
        </div>
        <div class="modal-body">
          <!-- Loading state -->
          <div v-if="loading" class="text-center py-4">
            <div class="spinner-border text-primary" role="status">
              <span class="visually-hidden">Loading...</span>
            </div>
            <p class="mt-2 text-muted">Loading permissions...</p>
          </div>

          <!-- Error state -->
          <div v-else-if="error" class="alert alert-danger">
            {{ error }}
          </div>

          <!-- Permissions list -->
          <div v-else>
            <div class="mb-3">
              <h6>Settings Sources</h6>
              <div class="small text-muted mb-2">
                Loading permissions from:
                <span v-for="(source, index) in settingSources" :key="source">
                  <code>{{ source }}</code><span v-if="index < settingSources.length - 1">, </span>
                </span>
              </div>
            </div>

            <div v-if="permissions.length === 0" class="alert alert-info">
              No pre-approved permissions found in the selected settings sources.
              All tool usages will prompt for approval.
            </div>

            <div v-else>
              <h6>Pre-approved Permissions ({{ permissions.length }})</h6>
              <table class="table table-sm table-striped">
                <thead>
                  <tr>
                    <th>Permission</th>
                    <th>Source(s)</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="perm in permissions" :key="perm.permission">
                    <td>
                      <code>{{ perm.permission }}</code>
                    </td>
                    <td>
                      <span
                        v-for="(source, idx) in perm.sources"
                        :key="source"
                        class="badge me-1"
                        :class="getSourceBadgeClass(source)"
                      >
                        {{ source }}
                      </span>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>

            <!-- Session allowed tools -->
            <div v-if="sessionAllowedTools.length > 0" class="mt-3">
              <h6>Session Allowed Tools</h6>
              <p class="small text-muted">
                These tools are explicitly allowed for this session:
              </p>
              <div>
                <span
                  v-for="tool in sessionAllowedTools"
                  :key="tool"
                  class="badge bg-success me-1 mb-1"
                >
                  {{ tool }}
                </span>
              </div>
            </div>

            <!-- Session disallowed tools -->
            <div v-if="sessionDisallowedTools.length > 0" class="mt-3">
              <h6>Session Disallowed Tools</h6>
              <p class="small text-muted">
                These tools are explicitly denied for this session:
              </p>
              <div>
                <span
                  v-for="tool in sessionDisallowedTools"
                  :key="tool"
                  class="badge bg-danger me-1 mb-1"
                >
                  {{ tool }}
                </span>
              </div>
            </div>
          </div>
        </div>
        <div class="modal-footer">
          <button
            type="button"
            class="btn btn-secondary"
            data-bs-dismiss="modal"
          >
            Close
          </button>
          <button
            type="button"
            class="btn btn-outline-primary"
            @click="refresh"
            :disabled="loading"
          >
            Refresh
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, watch, nextTick } from 'vue'
import { Modal } from 'bootstrap'
import { api } from '@/utils/api'

const props = defineProps({
  workingDirectory: {
    type: String,
    required: true
  },
  settingSources: {
    type: Array,
    default: () => ['user', 'project', 'local']
  },
  sessionAllowedTools: {
    type: Array,
    default: () => []
  },
  sessionDisallowedTools: {
    type: Array,
    default: () => []
  }
})

const emit = defineEmits(['close'])

// Refs
const modalRef = ref(null)
let modalInstance = null

// State
const loading = ref(false)
const error = ref('')
const permissions = ref([])

// Initialize modal
watch(modalRef, (el) => {
  if (el) {
    modalInstance = new Modal(el)
    el.addEventListener('hidden.bs.modal', () => {
      emit('close')
    })
  }
})

// Methods
function show() {
  if (modalInstance) {
    modalInstance.show()
    loadPermissions()
  }
}

function hide() {
  if (modalInstance) {
    modalInstance.hide()
  }
}

async function loadPermissions() {
  loading.value = true
  error.value = ''
  permissions.value = []

  try {
    const response = await api.post('/api/permissions/preview', {
      working_directory: props.workingDirectory,
      setting_sources: props.settingSources,
      session_allowed_tools: props.sessionAllowedTools
    })
    permissions.value = response.permissions || []
  } catch (err) {
    console.error('Failed to load permissions:', err)
    error.value = err.response?.data?.detail || err.message || 'Failed to load permissions'
  } finally {
    loading.value = false
  }
}

function refresh() {
  loadPermissions()
}

function getSourceBadgeClass(source) {
  switch (source) {
    case 'user': return 'bg-info'
    case 'project': return 'bg-primary'
    case 'local': return 'bg-secondary'
    case 'session': return 'bg-success'
    default: return 'bg-dark'
  }
}

// Expose methods
defineExpose({ show, hide })
</script>

<style scoped>
.table code {
  font-size: 0.875rem;
}

.badge {
  font-size: 0.75rem;
}
</style>
