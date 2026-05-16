<template>
  <div class="modal fade show d-block" tabindex="-1" @click.self="$emit('close')">
    <div class="modal-dialog modal-lg modal-dialog-scrollable">
      <div class="modal-content">

        <div class="modal-header py-2">
          <h5 class="modal-title fs-6">
            {{ isEdit ? `Edit Provider: ${form.id}` : 'Add Provider' }}
          </h5>
          <button type="button" class="btn-close btn-sm" @click="$emit('close')" />
        </div>

        <div class="modal-body">
          <div v-if="saveError" class="alert alert-danger small py-2 mb-3">
            {{ saveError }}
          </div>

          <div class="mb-3">
            <label class="form-label small fw-semibold">ID</label>
            <input
              v-model="form.id"
              type="text"
              class="form-control form-control-sm font-monospace"
              :class="{ 'is-invalid': idError }"
              placeholder="e.g. bedrock-sonnet"
              :disabled="isEdit"
              @input="idError = ''"
            />
            <div v-if="idError" class="invalid-feedback">{{ idError }}</div>
            <div class="form-text small">Slug only: lowercase letters, digits, hyphens, underscores.</div>
          </div>

          <div class="mb-3">
            <label class="form-label small fw-semibold">Display Name</label>
            <input
              v-model="form.display_name"
              type="text"
              class="form-control form-control-sm"
              :class="{ 'is-invalid': displayNameError }"
              placeholder="e.g. AWS Bedrock — Sonnet"
              @input="displayNameError = ''"
            />
            <div v-if="displayNameError" class="invalid-feedback">{{ displayNameError }}</div>
          </div>

          <div class="mb-3">
            <label class="form-label small fw-semibold">Provider Type</label>
            <select
              v-model="providerTypeSelection"
              class="form-select form-select-sm"
              @change="onProviderTypeChange"
            >
              <option value="anthropic">anthropic</option>
              <option value="bedrock">bedrock</option>
              <option value="vertex">vertex</option>
              <option value="openai">openai</option>
              <option value="lmstudio">lmstudio</option>
              <option value="azure">azure</option>
              <option value="openai-compatible">openai-compatible</option>
              <option :value="CUSTOM_SENTINEL">Custom…</option>
            </select>
            <input
              v-if="providerTypeSelection === CUSTOM_SENTINEL"
              v-model="form.provider_type"
              type="text"
              class="form-control form-control-sm mt-2 font-monospace"
              :class="{ 'is-invalid': providerTypeError }"
              placeholder="e.g. my-provider"
              @input="providerTypeError = ''"
            />
            <div v-if="providerTypeError" class="invalid-feedback">{{ providerTypeError }}</div>
          </div>

          <div class="mb-3">
            <label class="form-label small fw-semibold">LiteLLM Params</label>
            <LiteLLMParamsEditor v-model="form.litellm_params_template" />
          </div>

          <div class="mb-3">
            <label class="form-label small fw-semibold">Models</label>
            <div v-if="modelsError" class="text-danger small mb-1">{{ modelsError }}</div>
            <ProviderModelsEditor v-model="form.models" @update:modelValue="modelsError = ''" />
          </div>
        </div>

        <div class="modal-footer py-2">
          <button type="button" class="btn btn-secondary btn-sm" @click="$emit('close')">Cancel</button>
          <button
            type="button"
            class="btn btn-primary btn-sm"
            :disabled="saving"
            @click="save"
          >
            <span v-if="saving" class="spinner-border spinner-border-sm me-1" role="status" />
            Save
          </button>
        </div>

      </div>
    </div>
  </div>
  <div class="modal-backdrop fade show" />
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { useProviderCatalogStore } from '@/stores/providerCatalog'
import LiteLLMParamsEditor from './providers/LiteLLMParamsEditor.vue'
import ProviderModelsEditor from './providers/ProviderModelsEditor.vue'

const KNOWN_TYPES = ['anthropic', 'bedrock', 'vertex', 'openai', 'lmstudio', 'azure', 'openai-compatible']
const CUSTOM_SENTINEL = '__custom__'
const SLUG_RE = /^[a-z0-9_-]+$/

const props = defineProps({
  entry: { type: Object, default: null },
  existingIds: { type: Array, default: () => [] },
})

const emit = defineEmits(['close', 'saved'])

const store = useProviderCatalogStore()

const isEdit = computed(() => props.entry !== null)

const form = ref({
  id: '',
  display_name: '',
  provider_type: '',
  litellm_params_template: {},
  models: [],
})

const providerTypeSelection = ref('anthropic')

const idError = ref('')
const displayNameError = ref('')
const providerTypeError = ref('')
const modelsError = ref('')
const saveError = ref(null)
const saving = ref(false)

const effectiveProviderType = computed(() =>
  providerTypeSelection.value === CUSTOM_SENTINEL
    ? form.value.provider_type.trim()
    : providerTypeSelection.value
)

function initForm() {
  if (props.entry) {
    form.value = {
      id: props.entry.id,
      display_name: props.entry.display_name,
      provider_type: props.entry.provider_type,
      litellm_params_template: { ...(props.entry.litellm_params_template || {}) },
      models: (props.entry.models || []).map(m => ({ ...m })),
    }
    providerTypeSelection.value = KNOWN_TYPES.includes(props.entry.provider_type)
      ? props.entry.provider_type
      : CUSTOM_SENTINEL
  } else {
    form.value = { id: '', display_name: '', provider_type: 'anthropic', litellm_params_template: {}, models: [] }
    providerTypeSelection.value = 'anthropic'
  }
}

function onProviderTypeChange() {
  form.value.provider_type = providerTypeSelection.value === CUSTOM_SENTINEL ? '' : providerTypeSelection.value
}

function validate() {
  let ok = true

  if (!form.value.id.trim()) {
    idError.value = 'ID is required.'
    ok = false
  } else if (!SLUG_RE.test(form.value.id)) {
    idError.value = 'ID must be lowercase letters, digits, hyphens, or underscores.'
    ok = false
  } else if (!isEdit.value && props.existingIds.includes(form.value.id)) {
    idError.value = 'An entry with this ID already exists.'
    ok = false
  }

  if (!form.value.display_name.trim()) {
    displayNameError.value = 'Display Name is required.'
    ok = false
  }

  if (!effectiveProviderType.value) {
    providerTypeError.value = 'Provider Type is required.'
    ok = false
  }

  if (form.value.models.length === 0) {
    modelsError.value = 'At least one model is required.'
    ok = false
  } else {
    const hasIncomplete = form.value.models.some(m => !m.id.trim() || !m.display_name.trim() || !m.litellm_model.trim())
    if (hasIncomplete) {
      modelsError.value = 'All model rows must have ID, Display Name, and LiteLLM Model filled in.'
      ok = false
    } else {
      const ids = form.value.models.map(m => m.id)
      if (new Set(ids).size !== ids.length) {
        modelsError.value = 'Model IDs must be unique within this entry.'
        ok = false
      }
    }
  }

  return ok
}

async function save() {
  saveError.value = null
  if (!validate()) return

  const payload = {
    id: form.value.id.trim(),
    display_name: form.value.display_name.trim(),
    provider_type: effectiveProviderType.value,
    litellm_params_template: form.value.litellm_params_template,
    models: form.value.models,
  }

  saving.value = true
  try {
    if (isEdit.value) {
      await store.updateEntry(props.entry.id, payload)
    } else {
      await store.createEntry(payload)
    }
    emit('saved')
  } catch (e) {
    saveError.value = e?.data?.detail || e?.message || 'Save failed'
  } finally {
    saving.value = false
  }
}

watch(() => props.entry, initForm, { immediate: true })
</script>

<style scoped>
.modal {
  background: rgba(0, 0, 0, 0.15);
}
</style>
