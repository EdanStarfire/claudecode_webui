<template>
  <details class="setup-guide" :open="open" data-testid="relay-setup-guide">
    <summary>Slack app setup</summary>
    <div v-if="loadError" class="guide-error" role="alert">{{ loadError }}</div>
    <div v-else-if="!data" class="guide-loading">Loading…</div>
    <div v-else class="guide-body">
      <ol class="guide-steps">
        <li v-for="step in data.steps" :key="step.title">
          <strong>{{ step.title }}.</strong> {{ step.detail }}
        </li>
      </ol>

      <div class="guide-row">
        <h4>App manifest</h4>
        <label class="inbound-toggle">
          <input type="checkbox" v-model="includeInbound" @change="reload" data-testid="relay-include-inbound" />
          Include inbound items (next stage)
        </label>
      </div>
      <div class="manifest-actions">
        <button type="button" class="btn btn-sm btn-outline-secondary" data-testid="relay-manifest-copy" @click="copy">
          {{ copied ? 'Copied' : 'Copy' }}
        </button>
        <button type="button" class="btn btn-sm btn-outline-secondary" data-testid="relay-manifest-download" @click="download">
          Download slack-app-manifest.json
        </button>
      </div>
      <pre class="manifest-view" data-testid="relay-manifest-json">{{ data.manifest_json }}</pre>

      <h4>Scopes</h4>
      <div v-for="group in groups" :key="group.key" class="scope-group">
        <div class="scope-group-title">{{ group.label }}</div>
        <ul v-if="group.items.length">
          <li v-for="item in group.items" :key="item.name">
            <code>{{ item.name }}</code> <span class="scope-reason">{{ item.reason }}</span>
          </li>
        </ul>
        <div v-else class="scope-reason">None.</div>
      </div>
      <p class="scope-note">No bot user and no bot scopes: the relay acts only with your own user token.</p>
    </div>
  </details>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRelayStore } from '@/stores/relay'

const props = defineProps({
  relayId: { type: String, required: true },
  open: { type: Boolean, default: false },
})

const store = useRelayStore()
const data = ref(null)
const loadError = ref('')
const includeInbound = ref(true)
const copied = ref(false)

const groups = computed(() => {
  if (!data.value) return []
  const s = data.value.scopes
  const out = [
    { key: 'required', label: 'Required (user token)', items: s.required },
    { key: 'optional', label: 'Optional (granular search alternatives, not in the manifest)', items: s.optional },
  ]
  if (includeInbound.value) out.push({ key: 'inbound', label: 'Inbound (next stage)', items: s.inbound })
  return out
})

let reloadSeq = 0
async function reload() {
  const seq = ++reloadSeq
  loadError.value = ''
  try {
    const result = await store.loadManifest(props.relayId, includeInbound.value)
    if (seq === reloadSeq) data.value = result // a slower, older response never wins
  } catch (e) {
    if (seq === reloadSeq) loadError.value = e.message
  }
}

async function copy() {
  try {
    await navigator.clipboard.writeText(data.value.manifest_json)
    copied.value = true
    setTimeout(() => { copied.value = false }, 1500)
  } catch {
    loadError.value = 'Copy failed: select the manifest text manually.'
  }
}

function download() {
  const blob = new Blob([data.value.manifest_json], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = 'slack-app-manifest.json'
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

onMounted(reload)
</script>

<style scoped>
.setup-guide { border: 1px solid var(--bs-border-color); border-radius: 8px; padding: 10px 14px; }
.setup-guide summary { cursor: pointer; font-weight: 600; font-size: 13px; }
.guide-body { margin-top: 10px; font-size: 13px; }
.guide-steps { padding-left: 20px; line-height: 1.6; }
.guide-row { display: flex; align-items: center; justify-content: space-between; gap: 8px; flex-wrap: wrap; }
.guide-row h4, .guide-body h4 { font-size: 12px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--bs-secondary-color); margin: 14px 0 6px; }
.inbound-toggle { font-size: 12px; display: flex; align-items: center; gap: 6px; }
.manifest-actions { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 8px; }
.manifest-view {
  max-height: 260px; overflow: auto; font-size: 11px; padding: 10px;
  background: var(--bs-tertiary-bg); border-radius: 6px; white-space: pre-wrap; word-break: break-word;
}
.scope-group-title { font-weight: 600; font-size: 12px; margin-top: 8px; }
.scope-group ul { padding-left: 18px; margin: 4px 0; }
.scope-reason { color: var(--bs-secondary-color); font-size: 12px; }
.scope-note { color: var(--bs-secondary-color); font-size: 12px; margin-top: 10px; }
.guide-error { color: var(--tool-error-text); font-size: 12px; }
.guide-loading { color: var(--bs-secondary-color); font-size: 12px; padding: 8px 0; }
</style>
