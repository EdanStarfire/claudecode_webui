<template>
  <div class="app-section">
    <SettingsToolbar
      title="Token Pricing"
      :show-save-cancel="dirty"
      :saving="saving"
      :save-disabled="hasErrors"
      @save="save"
      @cancel="cancel"
    />
    <div v-if="loading" class="section-status">
      <span class="status-spinner" aria-hidden="true">⟳</span> Loading…
    </div>
    <div v-else-if="error" class="section-error">{{ error }}</div>
    <div v-else class="section-body">
      <PricingTab
        :config="config.pricing || {}"
        :pricing-defaults="config.pricing_defaults || {}"
        @update:config="onPricingUpdate"
        @update:hasErrors="onHasErrors"
      />
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { apiGet, apiPut } from '@/utils/api'
import { useUsageStore } from '@/stores/usage'
import { useAnalyticsStore } from '@/stores/analytics'
import PricingTab from '@/components/configuration/PricingTab.vue'
import SettingsToolbar from '../SettingsToolbar.vue'

const config = ref({})
const originalConfig = ref({})
const loading = ref(false)
const saving = ref(false)
const error = ref(null)
const hasErrors = ref(false)

const usageStore = useUsageStore()
const analyticsStore = useAnalyticsStore()

const dirty = computed(
  () => JSON.stringify(config.value) !== JSON.stringify(originalConfig.value)
)

async function load() {
  loading.value = true
  error.value = null
  try {
    const data = await apiGet('/api/config')
    config.value = data.config
    originalConfig.value = JSON.parse(JSON.stringify(data.config))
  } catch (e) {
    error.value = e.message || 'Failed to load configuration'
  } finally {
    loading.value = false
  }
}

function onPricingUpdate(pricing) {
  config.value = { ...config.value, pricing }
}

function onHasErrors(val) {
  hasErrors.value = val
}

async function save() {
  saving.value = true
  error.value = null
  try {
    const originalRateKeys = Object.keys(originalConfig.value?.pricing?.rates || {})
    const newRateKeys = Object.keys(config.value?.pricing?.rates || {})
    const removedModels = originalRateKeys.filter((k) => !newRateKeys.includes(k))

    const payload = {
      pricing: {
        ...config.value.pricing,
        removed_models: removedModels,
      },
    }
    const data = await apiPut('/api/config', payload)
    config.value = data.config
    originalConfig.value = JSON.parse(JSON.stringify(data.config))

    usageStore.refreshAll()
    analyticsStore.refresh()
  } catch (e) {
    error.value = e.message || 'Failed to save'
  } finally {
    saving.value = false
  }
}

function cancel() {
  config.value = JSON.parse(JSON.stringify(originalConfig.value))
  error.value = null
}

onMounted(load)
</script>

<style scoped>
.app-section {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
}

.section-status {
  padding: 20px;
  color: #94a3b8;
  font-size: 13px;
}

.section-status .status-spinner {
  display: inline-block;
  animation: spin 0.8s linear infinite;
}

.section-error {
  padding: 16px 20px;
  color: #f87171;
  font-size: 13px;
  background: rgba(248, 113, 113, 0.08);
  border-bottom: 1px solid rgba(248, 113, 113, 0.2);
}

.section-body {
  flex: 1;
  padding: 20px;
  overflow-y: auto;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
