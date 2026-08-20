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

    <hr class="my-3">

    <h6 class="mb-2">Agent Strip</h6>
    <div class="mb-3">
      <label class="form-label" for="maxPeekCards">Max peek cards</label>
      <input
        id="maxPeekCards"
        type="number"
        class="form-control form-control-sm"
        :class="{ 'is-invalid': maxPeekCardsError }"
        min="1"
        step="1"
        :value="config?.max_peek_cards ?? 100"
        @input="onMaxPeekCardsInput($event.target.value)"
      >
      <div v-if="maxPeekCardsError" class="invalid-feedback">{{ maxPeekCardsError }}</div>
      <small class="form-text text-muted">
        Maximum peek cards rendered in a collapsed agent stack before showing a sentinel summary
        (default: 100, minimum: 1). Larger values increase strip width; smaller values rely
        more heavily on the sentinel chip for at-a-glance state visibility.
      </small>
    </div>

    <div class="mb-3">
      <label class="form-label" for="maxSubagentsPerSession">Max subagents per session</label>
      <input
        id="maxSubagentsPerSession"
        type="number"
        class="form-control form-control-sm"
        :class="{ 'is-invalid': maxSubagentsError }"
        min="1"
        max="200"
        step="1"
        :value="config?.max_subagents_per_session ?? 200"
        @input="onMaxSubagentsInput($event.target.value)"
      >
      <div v-if="maxSubagentsError" class="invalid-feedback">{{ maxSubagentsError }}</div>
      <small class="form-text text-muted">
        Global cap on concurrent subagent spawns per session (default: 200, CC's own ceiling).
        Lower this to bound runaway delegation loops.
      </small>
    </div>

    <div class="form-check form-switch mb-3">
      <input
        class="form-check-input"
        type="checkbox"
        id="forwardSubagentText"
        :checked="config?.forward_subagent_text"
        @change="toggleForwardSubagentText"
      >
      <label class="form-check-label" for="forwardSubagentText">
        Forward subagent text and thinking
      </label>
      <small class="form-text text-muted d-block">
        Streams subagent (Task tool) assistant text and thinking into the parent session's
        message stream (default: on). Forwarded content is hidden from the main timeline and
        does not yet render anywhere else. Takes effect on the next session start, not
        mid-session.
      </small>
    </div>

    <div class="form-check form-switch mb-3">
      <input
        class="form-check-input"
        type="checkbox"
        id="allowBackgroundAgent"
        :checked="config?.allow_background_agent"
        @change="toggleAllowBackgroundAgent"
      >
      <label class="form-check-label" for="allowBackgroundAgent">
        Allow background Agent execution in Legion sessions
      </label>
      <small class="form-text text-muted d-block">
        Permits <code>Agent(run_in_background=true)</code> calls in Legion sessions instead of
        denying them and redirecting to <code>mcp__legion__spawn_minion</code> (default: off).
        Takes effect on the next session start, not mid-session.
      </small>
    </div>

    <div class="mb-3">
      <label class="form-label" for="resumeBatchSize">Resume batch size</label>
      <input
        id="resumeBatchSize"
        type="number"
        class="form-control form-control-sm"
        :class="{ 'is-invalid': resumeBatchSizeError }"
        min="1"
        step="1"
        :value="config?.resume_batch_size ?? 10"
        @input="onResumeBatchSizeInput($event.target.value)"
      >
      <div v-if="resumeBatchSizeError" class="invalid-feedback">{{ resumeBatchSizeError }}</div>
      <small class="form-text text-muted">
        Default number of stopped sessions started concurrently by "Resume Sessions" on a
        project's overview page (default: 10, minimum: 1). Can be overridden per-operation
        at the confirm step.
      </small>
    </div>

    <div class="mb-3">
      <label class="form-label" for="resumeBatchDelaySeconds">Resume batch delay (seconds)</label>
      <input
        id="resumeBatchDelaySeconds"
        type="number"
        class="form-control form-control-sm"
        :class="{ 'is-invalid': resumeBatchDelayError }"
        min="0"
        step="1"
        :value="config?.resume_batch_delay_seconds ?? 5"
        @input="onResumeBatchDelayInput($event.target.value)"
      >
      <div v-if="resumeBatchDelayError" class="invalid-feedback">{{ resumeBatchDelayError }}</div>
      <small class="form-text text-muted">
        Default pause (seconds) between resume batches, after each batch's launches settle and
        before the next batch starts (default: 5, minimum: 0 = no delay). Can be overridden
        per-operation at the confirm step.
      </small>
    </div>

    <div class="form-check form-switch mb-3">
      <input
        class="form-check-input"
        type="checkbox"
        id="enableExperimentalNavHeader"
        :checked="config?.enable_experimental_nav_header"
        @change="toggleExperimentalNavHeader"
      >
      <label class="form-check-label" for="enableExperimentalNavHeader">
        Experimental breadcrumb nav header
      </label>
      <small class="form-text text-muted d-block">
        Replaces the project pill bar and agent strip with a single-row breadcrumb nav
        (project › session), reclaiming vertical space. Project and session details move into
        click-to-open dropdowns. Experimental — behavior may change. Applies immediately, no
        reload needed.
      </small>
    </div>

    <hr class="my-3">

    <h6 class="mb-2">Legion</h6>
    <div class="mb-3">
      <label class="form-label" for="maxConcurrentMinions">Max Concurrent Minions</label>
      <input
        id="maxConcurrentMinions"
        type="number"
        class="form-control form-control-sm"
        :class="{ 'is-invalid': maxMinionsError }"
        min="1"
        step="1"
        :value="legionConfig?.max_concurrent_minions ?? 20"
        @input="onMaxMinionsInput($event.target.value)"
      >
      <div v-if="maxMinionsError" class="invalid-feedback">{{ maxMinionsError }}</div>
      <small class="form-text text-muted">
        Maximum number of concurrent minions per Legion project (default: 20)
      </small>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { apiGet, apiPost } from '@/utils/api'

const props = defineProps({
  config: { type: Object, default: () => ({}) },
  legionConfig: { type: Object, default: () => ({}) }
})

const emit = defineEmits(['update:config', 'update:legionConfig'])

const maxMinionsError = ref(null)
const maxPeekCardsError = ref(null)
const maxSubagentsError = ref(null)
const resumeBatchSizeError = ref(null)
const resumeBatchDelayError = ref(null)

function onMaxPeekCardsInput(value) {
  const parsed = parseInt(value, 10)
  if (!Number.isInteger(parsed) || parsed < 1) {
    maxPeekCardsError.value = 'Must be a positive integer'
    return
  }
  maxPeekCardsError.value = null
  emit('update:config', { ...props.config, max_peek_cards: parsed })
}

function onMaxSubagentsInput(value) {
  const parsed = parseInt(value, 10)
  if (!Number.isInteger(parsed) || parsed < 1 || parsed > 200) {
    maxSubagentsError.value = 'Must be an integer between 1 and 200'
    return
  }
  maxSubagentsError.value = null
  emit('update:config', { ...props.config, max_subagents_per_session: parsed })
}

function onResumeBatchSizeInput(value) {
  const parsed = parseInt(value, 10)
  if (!Number.isInteger(parsed) || parsed < 1) {
    resumeBatchSizeError.value = 'Must be a positive integer'
    return
  }
  resumeBatchSizeError.value = null
  emit('update:config', { ...props.config, resume_batch_size: parsed })
}

function onResumeBatchDelayInput(value) {
  const parsed = parseInt(value, 10)
  if (!Number.isInteger(parsed) || parsed < 0) {
    resumeBatchDelayError.value = 'Must be a non-negative integer'
    return
  }
  resumeBatchDelayError.value = null
  emit('update:config', { ...props.config, resume_batch_delay_seconds: parsed })
}

function onMaxMinionsInput(value) {
  const parsed = parseInt(value, 10)
  if (!Number.isInteger(parsed) || parsed < 1) {
    maxMinionsError.value = 'Must be a positive integer'
    return
  }
  maxMinionsError.value = null
  emit('update:legionConfig', {
    ...props.legionConfig,
    max_concurrent_minions: parsed
  })
}

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

function toggleForwardSubagentText(event) {
  emit('update:config', {
    ...props.config,
    forward_subagent_text: event.target.checked
  })
}

function toggleAllowBackgroundAgent(event) {
  emit('update:config', {
    ...props.config,
    allow_background_agent: event.target.checked
  })
}

function toggleExperimentalNavHeader(event) {
  emit('update:config', {
    ...props.config,
    enable_experimental_nav_header: event.target.checked
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
