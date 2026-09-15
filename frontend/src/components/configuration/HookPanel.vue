<template>
  <div class="hook-panel">
    <p class="text-muted small mb-3">
      Define global hook configurations that can be attached to sessions, templates, and config
      profiles. WebUI hooks are always additive to any disk-based <code>.claude/settings.json</code>
      hooks — they never replace or reorder them. Changes require a session restart to take effect.
    </p>

    <div v-if="hookStore.configList().length === 0 && !showForm" class="text-center text-muted py-3">
      <span style="font-size: 1.5rem;">&#x1FA9D;</span>
      <p class="mt-2 mb-0">No hooks configured</p>
    </div>

    <div v-for="config in hookStore.configList()" :key="config.id" class="hook-item">
      <div class="d-flex align-items-center justify-content-between">
        <div class="d-flex align-items-center gap-2 flex-wrap">
          <span class="fw-medium">{{ config.name }}</span>
          <span class="badge bg-secondary">{{ (config.hooks || []).length }} hook{{ (config.hooks || []).length === 1 ? '' : 's' }}</span>
          <span v-if="!config.enabled" class="badge bg-warning text-dark">disabled</span>
        </div>
        <div class="d-flex gap-1 align-items-center">
          <div class="form-check form-switch mb-0">
            <input
              class="form-check-input"
              type="checkbox"
              :checked="config.enabled"
              @change="toggleEnabled(config, $event.target.checked)"
              title="Enabled globally"
            />
          </div>
          <button class="btn btn-sm btn-outline-primary config-action-btn" @click="editConfig(config)" title="Edit">&#9998;</button>
          <button class="btn btn-sm btn-outline-danger config-action-btn" @click="confirmDelete(config)" title="Delete">&times;</button>
        </div>
      </div>
    </div>

    <div class="d-flex gap-2 mt-2 flex-wrap">
      <button v-if="!showForm" class="btn btn-outline-primary btn-sm" @click="startCreate">
        + New Hook
      </button>
    </div>

    <div v-if="showForm" class="hook-form mt-2 p-2 border rounded">
      <h6 class="mb-2">{{ editingConfig ? 'Edit' : 'New' }} Hook</h6>
      <HookEditor
        :config="editingConfig"
        :saving="saving"
        :error="formError"
        @save="onSave"
        @cancel="cancelForm"
      />
    </div>

    <div v-if="deletingConfig" class="mt-2 p-2 border border-danger rounded">
      <p class="small mb-2">
        Delete <strong>{{ deletingConfig.name }}</strong>? Sessions/templates/profiles attaching
        it will no longer inject it on next restart.
      </p>
      <div class="d-flex gap-2">
        <button class="btn btn-danger btn-sm" @click="doDelete" :disabled="saving">Delete</button>
        <button class="btn btn-secondary btn-sm" @click="deletingConfig = null">Cancel</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useHookConfigStore } from '@/stores/hooks'
import HookEditor from './HookEditor.vue'

const hookStore = useHookConfigStore()

const showForm = ref(false)
const editingConfig = ref(null)
const saving = ref(false)
const formError = ref(null)
const deletingConfig = ref(null)

onMounted(() => {
  hookStore.fetchConfigs()
})

function startCreate() {
  editingConfig.value = null
  formError.value = null
  showForm.value = true
}

function editConfig(config) {
  editingConfig.value = config
  formError.value = null
  showForm.value = true
}

function cancelForm() {
  showForm.value = false
  editingConfig.value = null
  formError.value = null
}

async function toggleEnabled(config, checked) {
  try {
    await hookStore.updateConfig(config.id, { enabled: checked })
  } catch (error) {
    console.error('Failed to toggle hook config:', error)
  }
}

async function onSave(payload) {
  saving.value = true
  formError.value = null
  try {
    if (editingConfig.value) {
      await hookStore.updateConfig(editingConfig.value.id, payload)
    } else {
      await hookStore.createConfig(payload)
    }
    showForm.value = false
    editingConfig.value = null
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
    await hookStore.deleteConfig(deletingConfig.value.id)
    deletingConfig.value = null
  } catch (error) {
    console.error('Failed to delete hook config:', error)
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.hook-item {
  padding: 0.5rem;
  border-bottom: 1px solid var(--bs-border-color);
}

.hook-item:last-child {
  border-bottom: none;
}

.hook-form {
  background: var(--bs-secondary-bg);
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
</style>
