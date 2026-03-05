<template>
  <div class="features-tab">
    <h6 class="mb-2">Workflow Skill Sync</h6>
    <p class="text-muted small mb-3">
      When enabled, WebUI automatically syncs workflow skills to <code>~/.claude/skills/</code>
      at startup. These skills provide plan management, git helpers, and other development
      workflow automation for Claude Code sessions.
    </p>

    <div class="form-check form-switch mb-3">
      <input
        class="form-check-input"
        type="checkbox"
        id="skillSyncEnabled"
        :checked="config?.skill_sync_enabled"
        @change="toggleSync"
      >
      <label class="form-check-label" for="skillSyncEnabled">
        Enable Skill Syncing
      </label>
    </div>

    <div class="d-flex align-items-center gap-3">
      <button
        class="btn btn-outline-secondary btn-sm"
        @click="syncNow"
        :disabled="!config?.skill_sync_enabled || syncing"
      >
        <span v-if="syncing">
          <span class="spinner-border spinner-border-sm" role="status"></span>
          Syncing...
        </span>
        <span v-else>Sync Now</span>
      </button>

      <span class="text-muted small">
        <template v-if="!config?.skill_sync_enabled">Syncing disabled</template>
        <template v-else-if="statusLoading">Loading status...</template>
        <template v-else-if="lastSyncTime">Last synced {{ formatRelative(lastSyncTime) }}</template>
        <template v-else>Never synced</template>
      </span>
    </div>

    <div v-if="syncResult" class="alert alert-success mt-3 py-2 small">
      Sync complete: {{ syncResult.added }} added, {{ syncResult.updated }} updated,
      {{ syncResult.removed }} removed
    </div>
    <div v-if="syncError" class="alert alert-danger mt-3 py-2 small">
      {{ syncError }}
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { apiGet, apiPost } from '@/utils/api'

const props = defineProps({
  config: { type: Object, default: () => ({}) }
})

const emit = defineEmits(['update:config'])

const syncing = ref(false)
const syncResult = ref(null)
const syncError = ref(null)
const statusLoading = ref(false)
const lastSyncTime = ref(null)

function toggleSync(event) {
  emit('update:config', {
    ...props.config,
    skill_sync_enabled: event.target.checked
  })
}

async function syncNow() {
  syncing.value = true
  syncResult.value = null
  syncError.value = null
  try {
    const data = await apiPost('/api/skills/sync')
    syncResult.value = data
    lastSyncTime.value = new Date().toISOString()
  } catch (e) {
    syncError.value = e.message || 'Sync failed'
  } finally {
    syncing.value = false
  }
}

async function loadStatus() {
  statusLoading.value = true
  try {
    const data = await apiGet('/api/skills/status')
    lastSyncTime.value = data.last_sync_time
  } catch {
    // Non-critical, just skip
  } finally {
    statusLoading.value = false
  }
}

function formatRelative(isoString) {
  if (!isoString) return 'Never'
  const date = new Date(isoString)
  const now = new Date()
  const diffMs = now - date
  const diffSec = Math.floor(diffMs / 1000)
  if (diffSec < 60) return 'just now'
  const diffMin = Math.floor(diffSec / 60)
  if (diffMin < 60) return `${diffMin} minute${diffMin !== 1 ? 's' : ''} ago`
  const diffHr = Math.floor(diffMin / 60)
  if (diffHr < 24) return `${diffHr} hour${diffHr !== 1 ? 's' : ''} ago`
  const diffDay = Math.floor(diffHr / 24)
  return `${diffDay} day${diffDay !== 1 ? 's' : ''} ago`
}

onMounted(() => {
  loadStatus()
})
</script>
