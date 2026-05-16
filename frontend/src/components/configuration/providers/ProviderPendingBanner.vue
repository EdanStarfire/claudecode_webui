<template>
  <div class="alert alert-warning d-flex align-items-center justify-content-between gap-2 py-2 mb-3">
    <span class="small">
      Catalog has pending changes — restart the LiteLLM proxy for them to take effect.
    </span>
    <div class="d-flex align-items-center gap-2 flex-shrink-0">
      <span v-if="error" class="text-danger small">{{ error }}</span>
      <button
        class="btn btn-warning btn-sm"
        :disabled="store.restarting"
        @click="restart"
      >
        <span v-if="store.restarting" class="spinner-border spinner-border-sm me-1" role="status" />
        Restart proxy now
      </button>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useProviderCatalogStore } from '@/stores/providerCatalog'

const store = useProviderCatalogStore()
const error = ref(null)

async function restart() {
  error.value = null
  try {
    await store.restartProxy()
  } catch (e) {
    error.value = e?.data?.detail || e?.message || 'Restart failed'
  }
}
</script>
