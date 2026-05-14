<template>
  <div class="pricing-tab">
    <p class="text-muted small mb-3">
      All values in USD per 1 M tokens. Changes take effect immediately on the next cost
      query — no restart needed.
    </p>

    <!-- Default model selector -->
    <div class="mb-4">
      <label class="form-label small fw-semibold" for="defaultModel">Default model</label>
      <select
        id="defaultModel"
        class="form-select form-select-sm"
        style="max-width: 320px"
        :value="config?.default_model"
        @change="onDefaultModelChange"
      >
        <option v-for="modelId in rateKeys" :key="modelId" :value="modelId">{{ modelId }}</option>
      </select>
      <small class="form-text text-muted">
        Used as fallback for sessions whose model has no configured rates.
      </small>
    </div>

    <!-- Rate table -->
    <div class="table-responsive">
      <table class="table table-sm align-middle mb-2">
        <thead>
          <tr class="small text-muted">
            <th class="ps-0">Model</th>
            <th class="text-center" style="min-width:85px">Input</th>
            <th class="text-center" style="min-width:85px">Output</th>
            <th class="text-center" style="min-width:95px">Cache write</th>
            <th class="text-center" style="min-width:85px">Cache read</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(rates, modelId) in config?.rates" :key="modelId">
            <td class="ps-0 font-monospace small text-nowrap align-top pt-2">{{ modelId }}</td>
            <td v-for="field in RATE_FIELDS" :key="field" class="py-1 text-center">
              <input
                type="number"
                class="form-control form-control-sm text-end rate-input"
                :class="{ 'is-invalid': errors[`${modelId}:${field}`] }"
                min="0"
                step="0.01"
                :value="rates[field]"
                @input="onRateInput(modelId, field, $event.target.value)"
              >
              <div v-if="errors[`${modelId}:${field}`]" class="invalid-feedback d-block small-feedback">
                {{ errors[`${modelId}:${field}`] }}
              </div>
            </td>
            <td class="align-top pt-2 text-end text-nowrap ps-2">
              <button
                v-if="modelId in (pricingDefaults || {})"
                class="btn btn-link btn-sm py-0 px-1 text-muted"
                title="Reset to built-in default"
                @click="resetToDefault(modelId)"
              >↺ Reset</button>
              <button
                v-else
                class="btn btn-link btn-sm py-0 px-1 text-danger"
                :disabled="modelId === config?.default_model"
                :title="modelId === config?.default_model ? 'Cannot remove the default model' : 'Remove model'"
                @click="removeModel(modelId)"
              >🗑 Remove</button>
            </td>
          </tr>

          <!-- New model row -->
          <tr v-if="addingModel">
            <td class="ps-0 align-top pt-1">
              <input
                type="text"
                class="form-control form-control-sm font-monospace"
                :class="{ 'is-invalid': newModelIdError }"
                v-model.trim="newModelId"
                placeholder="model-id"
              >
              <div v-if="newModelIdError" class="invalid-feedback d-block small-feedback">
                {{ newModelIdError }}
              </div>
            </td>
            <td v-for="field in RATE_FIELDS" :key="field" class="py-1 text-center">
              <input
                type="number"
                class="form-control form-control-sm text-end rate-input"
                :class="{ 'is-invalid': errors[`__new__:${field}`] }"
                min="0"
                step="0.01"
                :value="newRates[field]"
                @input="onNewRateInput(field, $event.target.value)"
              >
              <div v-if="errors[`__new__:${field}`]" class="invalid-feedback d-block small-feedback">
                {{ errors[`__new__:${field}`] }}
              </div>
            </td>
            <td class="align-top pt-2 text-end text-nowrap ps-2">
              <button class="btn btn-link btn-sm py-0 px-1 text-success" @click="confirmAddModel">✓ Add</button>
              <button class="btn btn-link btn-sm py-0 px-1 text-secondary ms-1" @click="cancelAddModel">✕</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <button
      v-if="!addingModel"
      class="btn btn-outline-secondary btn-sm"
      @click="startAddModel"
    >+ Add model</button>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'

const RATE_FIELDS = ['input', 'output', 'cache_write', 'cache_read']

const props = defineProps({
  config: { type: Object, default: () => ({ default_model: '', rates: {} }) },
  pricingDefaults: { type: Object, default: () => ({}) },
})

const emit = defineEmits(['update:config', 'update:hasErrors'])

const errors = ref({})
const addingModel = ref(false)
const newModelId = ref('')
const newModelIdError = ref(null)
const newRates = ref({ input: 0, output: 0, cache_write: 0, cache_read: 0 })

const rateKeys = computed(() => Object.keys(props.config?.rates || {}))

const hasErrors = computed(
  () => Object.keys(errors.value).length > 0 || !!newModelIdError.value || addingModel.value
)

watch(hasErrors, (val) => emit('update:hasErrors', val), { immediate: true })

function setError(key, msg) {
  errors.value = { ...errors.value, [key]: msg }
}

function clearError(key) {
  const e = { ...errors.value }
  delete e[key]
  errors.value = e
}

function clearModelErrors(modelId) {
  const e = { ...errors.value }
  for (const field of RATE_FIELDS) delete e[`${modelId}:${field}`]
  errors.value = e
}

function validateRate(value) {
  const n = parseFloat(value)
  if (Number.isNaN(n)) return 'Must be a valid number'
  if (n < 0) return 'Must be ≥ 0'
  if (n > 1000) return 'Unusually high — verify this is USD/1M tokens'
  return null
}

function onRateInput(modelId, field, value) {
  const key = `${modelId}:${field}`
  const err = validateRate(value)
  if (err) {
    setError(key, err)
    return
  }
  clearError(key)
  const newRatesObj = {
    ...props.config.rates,
    [modelId]: { ...props.config.rates[modelId], [field]: parseFloat(value) },
  }
  emit('update:config', { ...props.config, rates: newRatesObj })
}

function onDefaultModelChange(event) {
  emit('update:config', { ...props.config, default_model: event.target.value })
}

function resetToDefault(modelId) {
  const def = props.pricingDefaults[modelId]
  if (!def) return
  clearModelErrors(modelId)
  const newRatesObj = { ...props.config.rates, [modelId]: { ...def } }
  emit('update:config', { ...props.config, rates: newRatesObj })
}

function removeModel(modelId) {
  clearModelErrors(modelId)
  const newRatesObj = { ...props.config.rates }
  delete newRatesObj[modelId]
  emit('update:config', { ...props.config, rates: newRatesObj })
}

function startAddModel() {
  addingModel.value = true
  newModelId.value = ''
  newModelIdError.value = null
  newRates.value = { input: 0, output: 0, cache_write: 0, cache_read: 0 }
  clearModelErrors('__new__')
}

function onNewRateInput(field, value) {
  const key = `__new__:${field}`
  const err = validateRate(value)
  if (err) {
    setError(key, err)
  } else {
    clearError(key)
    newRates.value = { ...newRates.value, [field]: parseFloat(value) }
  }
}

function confirmAddModel() {
  const id = newModelId.value.trim()
  if (!id) {
    newModelIdError.value = 'Model ID is required'
    return
  }
  if (/\s/.test(id)) {
    newModelIdError.value = 'Model ID must not contain spaces'
    return
  }
  if (props.config?.rates && id in props.config.rates) {
    newModelIdError.value = 'Model already exists'
    return
  }
  if (RATE_FIELDS.some((f) => errors.value[`__new__:${f}`])) return
  newModelIdError.value = null
  clearModelErrors('__new__')
  const newRatesObj = { ...props.config.rates, [id]: { ...newRates.value } }
  addingModel.value = false
  emit('update:config', { ...props.config, rates: newRatesObj })
}

function cancelAddModel() {
  addingModel.value = false
  newModelIdError.value = null
  clearModelErrors('__new__')
}
</script>

<style scoped>
.rate-input {
  width: 80px;
  min-width: 70px;
  margin: 0 auto;
}

.small-feedback {
  font-size: 0.7rem;
  white-space: nowrap;
}
</style>
