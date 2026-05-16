<template>
  <div class="card mb-3">
    <div class="card-body py-2 px-3">
      <div class="d-flex align-items-center justify-content-between flex-wrap gap-2">
        <div class="d-flex align-items-center gap-3 flex-wrap">
          <span class="d-flex align-items-center gap-1">
            <span
              class="status-pill"
              :class="store.status.running ? 'status-running' : 'status-stopped'"
            >
              {{ store.status.running ? 'running' : 'stopped' }}
            </span>
          </span>
          <span class="status-field">
            <span class="text-muted small">Port:</span>
            <span class="small ms-1 font-monospace">{{ store.status.port ?? '—' }}</span>
          </span>
          <span class="status-field">
            <span class="text-muted small">Models:</span>
            <span class="small ms-1">{{ store.status.model_count }}</span>
          </span>
          <span class="status-field">
            <span class="text-muted small">Last restart:</span>
            <span class="small ms-1">{{ lastRestartLabel }}</span>
          </span>
        </div>
        <button
          class="btn btn-outline-secondary btn-sm"
          :disabled="refreshing"
          @click="refresh"
        >
          <span v-if="refreshing" class="spinner-border spinner-border-sm me-1" role="status" />
          Refresh
        </button>
      </div>

      <div v-if="store.status.last_error" class="mt-2">
        <details>
          <summary class="text-danger small" style="cursor:pointer">Last error</summary>
          <pre class="error-pre mt-1">{{ store.status.last_error }}</pre>
        </details>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useProviderCatalogStore } from '@/stores/providerCatalog'
import { getRelativeTime } from '@/utils/time'

const store = useProviderCatalogStore()

const refreshing = ref(false)

const lastRestartLabel = computed(() => {
  if (!store.status.last_restart) return 'never'
  return getRelativeTime(store.status.last_restart)
})

async function refresh() {
  refreshing.value = true
  try {
    await store.fetchStatus()
  } finally {
    refreshing.value = false
  }
}

onMounted(() => {
  store.fetchStatus()
})
</script>

<style scoped>
.status-pill {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 12px;
  font-size: 0.75rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.status-running {
  background: rgba(63, 185, 80, 0.15);
  color: #3fb950;
  border: 1px solid rgba(63, 185, 80, 0.3);
}

.status-stopped {
  background: rgba(220, 53, 69, 0.1);
  color: #dc3545;
  border: 1px solid rgba(220, 53, 69, 0.25);
}

.status-field {
  white-space: nowrap;
}

.error-pre {
  font-size: 0.75rem;
  background: rgba(220, 53, 69, 0.08);
  border: 1px solid rgba(220, 53, 69, 0.2);
  border-radius: 4px;
  padding: 6px 8px;
  color: #dc3545;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 150px;
  overflow-y: auto;
}
</style>
