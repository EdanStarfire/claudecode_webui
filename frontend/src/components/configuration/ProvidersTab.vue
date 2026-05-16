<template>
  <div class="lib-section">
    <SettingsToolbar title="Providers">
      <template #actions>
        <button
          class="toolbar-btn"
          :disabled="showAddProvider || editingProviderId !== null"
          @click="startAddProvider"
        >+ Add Provider</button>
      </template>
    </SettingsToolbar>

    <div class="status-area">
      <ProviderStatusCard />
      <ProviderPendingBanner v-if="store.pendingChanges" />
    </div>

    <div v-if="store.loading && !store.loaded" class="section-status">
      <span class="status-spinner" aria-hidden="true">⟳</span> Loading…
    </div>

    <div v-else class="section-body">

      <!-- Add Provider inline form -->
      <div v-if="showAddProvider" class="inline-form-card">
        <div class="inline-form-title">New Provider</div>
        <div class="form-grid">
          <div class="form-field">
            <label class="field-label">ID</label>
            <input
              v-model="providerForm.id"
              class="field-input font-monospace"
              placeholder="e.g. bedrock-sonnet"
              :class="{ 'is-invalid': providerFormErrors.id }"
            />
            <div v-if="providerFormErrors.id" class="field-error">{{ providerFormErrors.id }}</div>
            <div class="field-hint">Slug: lowercase letters, digits, hyphens, underscores.</div>
          </div>
          <div class="form-field">
            <label class="field-label">Display Name</label>
            <input
              v-model="providerForm.display_name"
              class="field-input"
              placeholder="e.g. AWS Bedrock — Sonnet"
              :class="{ 'is-invalid': providerFormErrors.display_name }"
            />
            <div v-if="providerFormErrors.display_name" class="field-error">{{ providerFormErrors.display_name }}</div>
          </div>
          <div class="form-field">
            <label class="field-label">Provider Type</label>
            <select v-model="providerTypeSelection" class="field-input" @change="onProviderTypeChange(providerForm, providerTypeSelection)">
              <option v-for="t in KNOWN_TYPES" :key="t" :value="t">{{ t }}</option>
              <option :value="CUSTOM_SENTINEL">Custom…</option>
            </select>
            <input
              v-if="providerTypeSelection === CUSTOM_SENTINEL"
              v-model="providerForm.provider_type"
              class="field-input font-monospace mt-1"
              placeholder="e.g. my-provider"
              :class="{ 'is-invalid': providerFormErrors.provider_type }"
            />
            <div v-if="providerFormErrors.provider_type" class="field-error">{{ providerFormErrors.provider_type }}</div>
          </div>
        </div>
        <div class="form-field mt-2">
          <label class="field-label">LiteLLM Params</label>
          <LiteLLMParamsEditor v-model="providerForm.litellm_params_template" />
        </div>
        <div v-if="providerFormError" class="form-error-banner">{{ providerFormError }}</div>
        <div class="form-actions">
          <button class="btn-save" :disabled="savingProvider" @click="saveNewProvider">
            {{ savingProvider ? '…' : 'Save' }}
          </button>
          <button class="btn-cancel" @click="cancelAddProvider">Cancel</button>
        </div>
      </div>

      <div v-if="store.entries.length === 0 && !showAddProvider" class="empty-state">
        No provider entries configured. Click <strong>+ Add Provider</strong> to expose alternative LLM backends.
      </div>

      <!-- Provider groups -->
      <div v-for="entry in store.entries" :key="entry.id" class="category-block">

        <!-- Provider heading -->
        <div
          class="category-header"
          :class="{ 'is-confirm': pendingDeleteId === entry.id }"
        >
          <template v-if="pendingDeleteId === entry.id">
            <span class="confirm-text">Delete "{{ entry.display_name }}"?</span>
            <div class="confirm-btns">
              <button class="btn-confirm-delete" :disabled="deletingProvider" @click="executeDeleteProvider(entry.id)">
                {{ deletingProvider ? '…' : 'Delete' }}
              </button>
              <button class="btn-confirm-cancel" @click="pendingDeleteId = null">Cancel</button>
            </div>
          </template>
          <template v-else>
            <div class="provider-heading">
              <span class="category-label">{{ entry.display_name }}</span>
              <span class="provider-type-tag">{{ entry.provider_type }}</span>
              <span class="provider-id-tag font-monospace">{{ entry.id }}</span>
            </div>
            <div class="provider-actions">
              <button
                class="action-btn"
                :disabled="editingProviderId !== null || showAddProvider"
                @click="startEditProvider(entry)"
              >Edit</button>
              <button
                class="action-btn action-btn--danger"
                @click="pendingDeleteId = entry.id"
              >Delete</button>
              <button
                class="action-btn action-btn--add"
                :disabled="addingModelForProvider === entry.id || editingModelKey !== null"
                @click="startAddModel(entry.id)"
              >+ Model</button>
            </div>
          </template>
        </div>

        <!-- Edit Provider inline form -->
        <div v-if="editingProviderId === entry.id" class="inline-form-card">
          <div class="inline-form-title">Edit Provider: {{ entry.id }}</div>
          <div class="form-grid">
            <div class="form-field">
              <label class="field-label">Display Name</label>
              <input
                v-model="editProviderForm.display_name"
                class="field-input"
                :class="{ 'is-invalid': editProviderFormErrors.display_name }"
              />
              <div v-if="editProviderFormErrors.display_name" class="field-error">{{ editProviderFormErrors.display_name }}</div>
            </div>
            <div class="form-field">
              <label class="field-label">Provider Type</label>
              <select v-model="editProviderTypeSelection" class="field-input" @change="onProviderTypeChange(editProviderForm, editProviderTypeSelection)">
                <option v-for="t in KNOWN_TYPES" :key="t" :value="t">{{ t }}</option>
                <option :value="CUSTOM_SENTINEL">Custom…</option>
              </select>
              <input
                v-if="editProviderTypeSelection === CUSTOM_SENTINEL"
                v-model="editProviderForm.provider_type"
                class="field-input font-monospace mt-1"
                placeholder="e.g. my-provider"
                :class="{ 'is-invalid': editProviderFormErrors.provider_type }"
              />
              <div v-if="editProviderFormErrors.provider_type" class="field-error">{{ editProviderFormErrors.provider_type }}</div>
            </div>
          </div>
          <div class="form-field mt-2">
            <label class="field-label">LiteLLM Params</label>
            <LiteLLMParamsEditor v-model="editProviderForm.litellm_params_template" />
          </div>
          <div v-if="editProviderFormError" class="form-error-banner">{{ editProviderFormError }}</div>
          <div class="form-actions">
            <button class="btn-save" :disabled="savingProvider" @click="saveEditProvider(entry)">
              {{ savingProvider ? '…' : 'Save' }}
            </button>
            <button class="btn-cancel" @click="cancelEditProvider">Cancel</button>
          </div>
        </div>

        <!-- Model rows -->
        <template v-for="(model, idx) in entry.models" :key="model.id">
          <!-- Edit Model inline form -->
          <div
            v-if="editingModelKey && editingModelKey.providerId === entry.id && editingModelKey.idx === idx"
            class="inline-form-card model-form"
          >
            <div class="form-grid form-grid--3col">
              <div class="form-field">
                <label class="field-label">ID</label>
                <input
                  v-model="modelForm.id"
                  class="field-input font-monospace"
                  disabled
                />
              </div>
              <div class="form-field">
                <label class="field-label">Display Name</label>
                <input
                  v-model="modelForm.display_name"
                  class="field-input"
                  :class="{ 'is-invalid': modelFormErrors.display_name }"
                />
                <div v-if="modelFormErrors.display_name" class="field-error">{{ modelFormErrors.display_name }}</div>
              </div>
              <div class="form-field">
                <label class="field-label">LiteLLM Model</label>
                <input
                  v-model="modelForm.litellm_model"
                  class="field-input font-monospace"
                  placeholder="e.g. bedrock/claude-3-5-sonnet"
                  :class="{ 'is-invalid': modelFormErrors.litellm_model }"
                />
                <div v-if="modelFormErrors.litellm_model" class="field-error">{{ modelFormErrors.litellm_model }}</div>
              </div>
            </div>
            <div v-if="modelFormError" class="form-error-banner">{{ modelFormError }}</div>
            <div class="form-actions">
              <button class="btn-save" :disabled="savingModel" @click="saveEditModel(entry)">
                {{ savingModel ? '…' : 'Save' }}
              </button>
              <button class="btn-cancel" @click="cancelEditModel">Cancel</button>
            </div>
          </div>

          <!-- Delete model confirm row -->
          <div
            v-else-if="pendingDeleteModel && pendingDeleteModel.providerId === entry.id && pendingDeleteModel.idx === idx"
            class="model-row model-row--confirm"
          >
            <span class="confirm-text">Delete model "{{ model.display_name }}"?</span>
            <div class="confirm-btns">
              <button class="btn-confirm-delete" :disabled="deletingModel" @click="executeDeleteModel(entry)">
                {{ deletingModel ? '…' : 'Delete' }}
              </button>
              <button class="btn-confirm-cancel" @click="pendingDeleteModel = null">Cancel</button>
            </div>
          </div>

          <!-- Normal model row -->
          <div v-else class="model-row">
            <span class="model-name">{{ model.display_name }}</span>
            <span class="model-litellm font-monospace">{{ model.litellm_model }}</span>
            <div class="row-actions">
              <button
                class="row-action-btn"
                :disabled="editingModelKey !== null || addingModelForProvider !== null"
                @click="startEditModel(entry.id, idx, model)"
              >Edit</button>
              <button
                class="row-action-btn row-action-btn--danger"
                @click="pendingDeleteModel = { providerId: entry.id, idx }"
              >Delete</button>
            </div>
          </div>
        </template>

        <!-- Add Model inline form -->
        <div v-if="addingModelForProvider === entry.id" class="inline-form-card model-form">
          <div class="form-grid form-grid--3col">
            <div class="form-field">
              <label class="field-label">ID</label>
              <input
                v-model="modelForm.id"
                class="field-input font-monospace"
                placeholder="model-id"
                :class="{ 'is-invalid': modelFormErrors.id }"
              />
              <div v-if="modelFormErrors.id" class="field-error">{{ modelFormErrors.id }}</div>
            </div>
            <div class="form-field">
              <label class="field-label">Display Name</label>
              <input
                v-model="modelForm.display_name"
                class="field-input"
                placeholder="Display Name"
                :class="{ 'is-invalid': modelFormErrors.display_name }"
              />
              <div v-if="modelFormErrors.display_name" class="field-error">{{ modelFormErrors.display_name }}</div>
            </div>
            <div class="form-field">
              <label class="field-label">LiteLLM Model</label>
              <input
                v-model="modelForm.litellm_model"
                class="field-input font-monospace"
                placeholder="e.g. bedrock/claude-3-5-sonnet"
                :class="{ 'is-invalid': modelFormErrors.litellm_model }"
              />
              <div v-if="modelFormErrors.litellm_model" class="field-error">{{ modelFormErrors.litellm_model }}</div>
            </div>
          </div>
          <div v-if="modelFormError" class="form-error-banner">{{ modelFormError }}</div>
          <div class="form-actions">
            <button class="btn-save" :disabled="savingModel" @click="saveNewModel(entry)">
              {{ savingModel ? '…' : 'Save' }}
            </button>
            <button class="btn-cancel" @click="addingModelForProvider = null">Cancel</button>
          </div>
        </div>

        <div
          v-if="entry.models.length === 0 && addingModelForProvider !== entry.id"
          class="models-empty"
        >
          No models. Click <strong>+ Model</strong> to add one.
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { useProviderCatalogStore } from '@/stores/providerCatalog'
import SettingsToolbar from '@/components/settings/SettingsToolbar.vue'
import ProviderStatusCard from './providers/ProviderStatusCard.vue'
import ProviderPendingBanner from './providers/ProviderPendingBanner.vue'
import LiteLLMParamsEditor from './providers/LiteLLMParamsEditor.vue'

const KNOWN_TYPES = ['anthropic', 'bedrock', 'vertex', 'openai', 'lmstudio', 'azure', 'openai-compatible']
const CUSTOM_SENTINEL = '__custom__'
const SLUG_RE = /^[a-z0-9_-]+$/

const store = useProviderCatalogStore()

// ── UI state ──────────────────────────────────────────────────────────────
const showAddProvider      = ref(false)
const editingProviderId    = ref(null)
const addingModelForProvider = ref(null)
const editingModelKey      = ref(null)   // { providerId, idx } | null
const pendingDeleteId      = ref(null)
const pendingDeleteModel   = ref(null)   // { providerId, idx } | null

// ── Saving flags ──────────────────────────────────────────────────────────
const savingProvider  = ref(false)
const deletingProvider = ref(false)
const savingModel     = ref(false)
const deletingModel   = ref(false)

// ── Add Provider form ─────────────────────────────────────────────────────
const providerTypeSelection = ref('anthropic')
const providerForm = reactive({
  id: '',
  display_name: '',
  provider_type: 'anthropic',
  litellm_params_template: {},
})
const providerFormErrors = reactive({ id: '', display_name: '', provider_type: '' })
const providerFormError = ref(null)

// ── Edit Provider form ────────────────────────────────────────────────────
const editProviderTypeSelection = ref('anthropic')
const editProviderForm = reactive({
  display_name: '',
  provider_type: 'anthropic',
  litellm_params_template: {},
})
const editProviderFormErrors = reactive({ display_name: '', provider_type: '' })
const editProviderFormError = ref(null)

// ── Model form (shared add/edit) ──────────────────────────────────────────
const modelForm = reactive({ id: '', display_name: '', litellm_model: '' })
const modelFormErrors = reactive({ id: '', display_name: '', litellm_model: '' })
const modelFormError = ref(null)

// ── Provider type helpers ─────────────────────────────────────────────────
function effectiveProviderType(form, selection) {
  return selection === CUSTOM_SENTINEL ? form.provider_type.trim() : selection
}

function onProviderTypeChange(form, selectionRef) {
  form.provider_type = selectionRef.value === CUSTOM_SENTINEL ? '' : selectionRef.value
}

// ── Entry payload helper ──────────────────────────────────────────────────
function entryPayload(entry, overrides = {}) {
  return {
    id: entry.id,
    display_name: entry.display_name,
    provider_type: entry.provider_type,
    litellm_params_template: entry.litellm_params_template,
    models: entry.models,
    ...overrides,
  }
}

// ── Validation ────────────────────────────────────────────────────────────
function validateProvider(form, selection, errors, isAdd) {
  let ok = true
  errors.display_name = ''
  errors.provider_type = ''

  if (isAdd) {
    errors.id = ''
    if (!form.id.trim()) {
      errors.id = 'ID is required.'
      ok = false
    } else if (!SLUG_RE.test(form.id)) {
      errors.id = 'ID must be lowercase letters, digits, hyphens, or underscores.'
      ok = false
    } else if (store.entries.some(e => e.id === form.id)) {
      errors.id = 'An entry with this ID already exists.'
      ok = false
    }
  }

  if (!form.display_name.trim()) {
    errors.display_name = 'Display Name is required.'
    ok = false
  }

  if (!effectiveProviderType(form, selection)) {
    errors.provider_type = 'Provider Type is required.'
    ok = false
  }

  return ok
}

function validateModel(form, errors, existingIds, isAdd) {
  let ok = true
  errors.id = ''
  errors.display_name = ''
  errors.litellm_model = ''

  if (isAdd) {
    if (!form.id.trim()) {
      errors.id = 'ID is required.'
      ok = false
    } else if (existingIds.includes(form.id)) {
      errors.id = 'A model with this ID already exists in this provider.'
      ok = false
    }
  }

  if (!form.display_name.trim()) {
    errors.display_name = 'Display Name is required.'
    ok = false
  }

  if (!form.litellm_model.trim()) {
    errors.litellm_model = 'LiteLLM Model is required.'
    ok = false
  }

  return ok
}

// ── Add Provider ──────────────────────────────────────────────────────────
function startAddProvider() {
  closeAllForms()
  Object.assign(providerForm, { id: '', display_name: '', provider_type: 'anthropic', litellm_params_template: {} })
  Object.assign(providerFormErrors, { id: '', display_name: '', provider_type: '' })
  providerTypeSelection.value = 'anthropic'
  providerFormError.value = null
  showAddProvider.value = true
}

function cancelAddProvider() {
  showAddProvider.value = false
}

async function saveNewProvider() {
  if (!validateProvider(providerForm, providerTypeSelection.value, providerFormErrors, true)) return
  savingProvider.value = true
  providerFormError.value = null
  try {
    await store.createEntry({
      id: providerForm.id.trim(),
      display_name: providerForm.display_name.trim(),
      provider_type: effectiveProviderType(providerForm, providerTypeSelection.value),
      litellm_params_template: providerForm.litellm_params_template,
      models: [],
    })
    showAddProvider.value = false
  } catch (e) {
    providerFormError.value = e?.data?.detail || e?.message || 'Save failed'
  } finally {
    savingProvider.value = false
  }
}

// ── Edit Provider ─────────────────────────────────────────────────────────
function startEditProvider(entry) {
  closeAllForms()
  Object.assign(editProviderForm, {
    display_name: entry.display_name,
    provider_type: entry.provider_type,
    litellm_params_template: { ...(entry.litellm_params_template || {}) },
  })
  editProviderTypeSelection.value = KNOWN_TYPES.includes(entry.provider_type)
    ? entry.provider_type
    : CUSTOM_SENTINEL
  Object.assign(editProviderFormErrors, { display_name: '', provider_type: '' })
  editProviderFormError.value = null
  editingProviderId.value = entry.id
}

function cancelEditProvider() {
  editingProviderId.value = null
}

async function saveEditProvider(entry) {
  if (!validateProvider(editProviderForm, editProviderTypeSelection.value, editProviderFormErrors, false)) return
  savingProvider.value = true
  editProviderFormError.value = null
  try {
    await store.updateEntry(entry.id, entryPayload(entry, {
      display_name: editProviderForm.display_name.trim(),
      provider_type: effectiveProviderType(editProviderForm, editProviderTypeSelection.value),
      litellm_params_template: editProviderForm.litellm_params_template,
    }))
    editingProviderId.value = null
  } catch (e) {
    editProviderFormError.value = e?.data?.detail || e?.message || 'Save failed'
  } finally {
    savingProvider.value = false
  }
}

// ── Delete Provider ───────────────────────────────────────────────────────
async function executeDeleteProvider(entryId) {
  deletingProvider.value = true
  try {
    await store.deleteEntry(entryId)
    pendingDeleteId.value = null
  } catch (e) {
    pendingDeleteId.value = null
  } finally {
    deletingProvider.value = false
  }
}

// ── Add Model ─────────────────────────────────────────────────────────────
function startAddModel(providerId) {
  closeAllForms()
  Object.assign(modelForm, { id: '', display_name: '', litellm_model: '' })
  Object.assign(modelFormErrors, { id: '', display_name: '', litellm_model: '' })
  modelFormError.value = null
  addingModelForProvider.value = providerId
}

async function saveNewModel(entry) {
  const existingIds = entry.models.map(m => m.id)
  if (!validateModel(modelForm, modelFormErrors, existingIds, true)) return
  savingModel.value = true
  modelFormError.value = null
  try {
    const newModel = {
      id: modelForm.id.trim(),
      display_name: modelForm.display_name.trim(),
      litellm_model: modelForm.litellm_model.trim(),
    }
    await store.updateEntry(entry.id, entryPayload(entry, { models: [...entry.models, newModel] }))
    addingModelForProvider.value = null
  } catch (e) {
    modelFormError.value = e?.data?.detail || e?.message || 'Save failed'
  } finally {
    savingModel.value = false
  }
}

// ── Edit Model ────────────────────────────────────────────────────────────
function startEditModel(providerId, idx, model) {
  closeAllForms()
  Object.assign(modelForm, { id: model.id, display_name: model.display_name, litellm_model: model.litellm_model })
  Object.assign(modelFormErrors, { id: '', display_name: '', litellm_model: '' })
  modelFormError.value = null
  editingModelKey.value = { providerId, idx }
}

function cancelEditModel() {
  editingModelKey.value = null
}

async function saveEditModel(entry) {
  if (!validateModel(modelForm, modelFormErrors, [], false)) return
  savingModel.value = true
  modelFormError.value = null
  try {
    const { idx } = editingModelKey.value
    const updatedModels = entry.models.map((m, i) =>
      i === idx
        ? { id: m.id, display_name: modelForm.display_name.trim(), litellm_model: modelForm.litellm_model.trim() }
        : m
    )
    await store.updateEntry(entry.id, entryPayload(entry, { models: updatedModels }))
    editingModelKey.value = null
  } catch (e) {
    modelFormError.value = e?.data?.detail || e?.message || 'Save failed'
  } finally {
    savingModel.value = false
  }
}

// ── Delete Model ──────────────────────────────────────────────────────────
async function executeDeleteModel(entry) {
  deletingModel.value = true
  try {
    const { idx } = pendingDeleteModel.value
    const updatedModels = entry.models.filter((_, i) => i !== idx)
    await store.updateEntry(entry.id, entryPayload(entry, { models: updatedModels }))
    pendingDeleteModel.value = null
  } catch (e) {
    pendingDeleteModel.value = null
  } finally {
    deletingModel.value = false
  }
}

// ── Close all open forms ──────────────────────────────────────────────────
function closeAllForms() {
  showAddProvider.value = false
  editingProviderId.value = null
  addingModelForProvider.value = null
  editingModelKey.value = null
  pendingDeleteId.value = null
  pendingDeleteModel.value = null
}

onMounted(() => store.fetchIfEmpty())
</script>

<style scoped>
.lib-section {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
}

.toolbar-btn {
  padding: 3px 10px;
  border-radius: 5px;
  font-size: 12px;
  cursor: pointer;
  border: 1px solid var(--bs-border-color);
  background: none;
  color: var(--bs-secondary-color);
  transition: background 0.12s, color 0.12s;
}
.toolbar-btn:hover:not(:disabled) {
  background: var(--bs-secondary-bg);
  color: var(--bs-emphasis-color);
}
.toolbar-btn:disabled {
  opacity: 0.5;
  cursor: default;
}

.status-area {
  padding: 8px 16px 0;
}

.section-status {
  padding: 20px;
  color: var(--bs-secondary-color);
  font-size: 13px;
}

.status-spinner {
  display: inline-block;
  animation: spin 0.8s linear infinite;
}

/* ── Section body ─────────────────────────────── */
.section-body {
  flex: 1;
  overflow-y: auto;
  padding: 16px 20px;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

/* ── Empty state ─────────────────────────────── */
.empty-state {
  padding: 20px 12px;
  text-align: center;
  color: var(--bs-tertiary-color);
  font-size: 12px;
  background: var(--bs-tertiary-bg);
  border-radius: 7px;
  border: 1px dashed var(--bs-border-color);
}

/* ── Category block (one per provider) ───────── */
.category-block {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.category-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 6px 4px 6px;
  border-bottom: 1px solid var(--bs-border-color);
}

.category-header.is-confirm {
  border-color: rgba(248, 113, 113, 0.4);
  background: rgba(248, 113, 113, 0.05);
  border-radius: 6px;
  padding: 6px 8px;
}

.provider-heading {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  flex: 1;
}

.category-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--bs-emphasis-color);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.provider-type-tag {
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 10px;
  background: var(--bs-secondary-bg);
  color: var(--bs-secondary-color);
  border: 1px solid var(--bs-border-color);
  flex-shrink: 0;
}

.provider-id-tag {
  font-size: 10px;
  color: var(--bs-tertiary-color);
  flex-shrink: 0;
}

.provider-actions {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}

.action-btn {
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 11px;
  cursor: pointer;
  border: 1px solid var(--bs-border-color);
  background: none;
  color: var(--bs-secondary-color);
  transition: background 0.12s, color 0.12s, border-color 0.12s;
}
.action-btn:hover:not(:disabled) {
  background: var(--bs-secondary-bg);
  color: var(--bs-emphasis-color);
}
.action-btn:disabled {
  opacity: 0.4;
  cursor: default;
}
.action-btn--danger:hover:not(:disabled) {
  background: rgba(248, 113, 113, 0.15);
  border-color: rgba(248, 113, 113, 0.4);
  color: #f87171;
}
.action-btn--add:hover:not(:disabled) {
  background: rgba(99, 102, 241, 0.12);
  border-color: rgba(99, 102, 241, 0.4);
  color: #818cf8;
}

/* ── Model rows ──────────────────────────────── */
.model-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 7px 10px;
  border-radius: 6px;
  border: 1px solid var(--bs-border-color);
  background: var(--bs-body-bg);
  font-size: 12px;
  margin-left: 12px;
}
.model-row:hover .row-actions {
  opacity: 1;
}
.model-row--confirm {
  border-color: rgba(248, 113, 113, 0.5);
  background: rgba(248, 113, 113, 0.06);
  cursor: default;
}
.model-row--confirm:hover {
  border-color: rgba(248, 113, 113, 0.7);
}

.model-name {
  font-weight: 500;
  color: var(--bs-emphasis-color);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  min-width: 80px;
  flex: 0 0 auto;
}

.model-litellm {
  font-size: 11px;
  color: var(--bs-tertiary-color);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  flex: 1;
  min-width: 0;
}

.row-actions {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
  opacity: 0;
  transition: opacity 0.12s;
}

.row-action-btn {
  padding: 1px 7px;
  border-radius: 4px;
  font-size: 11px;
  cursor: pointer;
  border: 1px solid var(--bs-border-color);
  background: none;
  color: var(--bs-secondary-color);
  transition: background 0.12s, color 0.12s, border-color 0.12s;
}
.row-action-btn:hover:not(:disabled) {
  background: var(--bs-secondary-bg);
  color: var(--bs-emphasis-color);
}
.row-action-btn:disabled {
  opacity: 0.4;
  cursor: default;
}
.row-action-btn--danger:hover:not(:disabled) {
  background: rgba(248, 113, 113, 0.15);
  border-color: rgba(248, 113, 113, 0.4);
  color: #f87171;
}

.models-empty {
  margin-left: 12px;
  padding: 8px 10px;
  font-size: 11px;
  color: var(--bs-tertiary-color);
  background: var(--bs-tertiary-bg);
  border-radius: 5px;
  border: 1px dashed var(--bs-border-color);
}

/* ── Inline form card ────────────────────────── */
.inline-form-card {
  padding: 12px 14px;
  border-radius: 7px;
  border: 1px solid var(--bs-border-color);
  background: var(--bs-tertiary-bg);
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.model-form {
  margin-left: 12px;
}

.inline-form-title {
  font-size: 11px;
  font-weight: 600;
  color: var(--bs-secondary-color);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.form-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}
.form-grid--3col {
  grid-template-columns: 1fr 1fr 1fr;
}

.form-field {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.field-label {
  font-size: 11px;
  font-weight: 500;
  color: var(--bs-secondary-color);
}

.field-input {
  padding: 4px 8px;
  font-size: 12px;
  border: 1px solid var(--bs-border-color);
  border-radius: 5px;
  background: var(--bs-body-bg);
  color: var(--bs-body-color);
  outline: none;
  transition: border-color 0.12s;
}
.field-input:focus {
  border-color: #6366f1;
}
.field-input.is-invalid {
  border-color: #f87171;
}
.field-input:disabled {
  opacity: 0.6;
  cursor: default;
}

.field-error {
  font-size: 10px;
  color: #f87171;
}

.field-hint {
  font-size: 10px;
  color: var(--bs-tertiary-color);
}

.form-error-banner {
  font-size: 11px;
  color: #f87171;
  background: rgba(248, 113, 113, 0.1);
  border: 1px solid rgba(248, 113, 113, 0.25);
  border-radius: 5px;
  padding: 6px 10px;
}

.form-actions {
  display: flex;
  gap: 6px;
  align-items: center;
}

.btn-save {
  padding: 4px 14px;
  border-radius: 5px;
  font-size: 12px;
  cursor: pointer;
  border: 1px solid #6366f1;
  background: #6366f1;
  color: #fff;
  transition: background 0.12s, border-color 0.12s;
}
.btn-save:hover:not(:disabled) { background: #4f52c9; border-color: #4f52c9; }
.btn-save:disabled { opacity: 0.5; cursor: default; }

.btn-cancel {
  padding: 4px 12px;
  border-radius: 5px;
  font-size: 12px;
  cursor: pointer;
  border: 1px solid var(--bs-border-color);
  background: none;
  color: var(--bs-secondary-color);
  transition: background 0.12s, color 0.12s;
}
.btn-cancel:hover { background: var(--bs-secondary-bg); color: var(--bs-emphasis-color); }

/* ── Inline confirm ──────────────────────────── */
.confirm-text {
  font-size: 12px;
  color: var(--bs-secondary-color);
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.confirm-btns {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
}

.btn-confirm-delete,
.btn-confirm-cancel {
  padding: 3px 10px;
  border-radius: 5px;
  font-size: 12px;
  cursor: pointer;
  border: 1px solid;
  transition: background 0.12s, border-color 0.12s, color 0.12s;
}

.btn-confirm-delete {
  background: rgba(248, 113, 113, 0.15);
  border-color: rgba(248, 113, 113, 0.4);
  color: #f87171;
}
.btn-confirm-delete:hover:not(:disabled) {
  background: #f87171;
  border-color: #f87171;
  color: #fff;
}
.btn-confirm-delete:disabled { opacity: 0.5; cursor: default; }

.btn-confirm-cancel {
  background: none;
  border-color: var(--bs-border-color);
  color: var(--bs-secondary-color);
}
.btn-confirm-cancel:hover { background: var(--bs-secondary-bg); color: var(--bs-emphasis-color); }

@keyframes spin { to { transform: rotate(360deg); } }
</style>
