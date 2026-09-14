<template>
  <div
    class="hook-entry"
    :class="{ disabled: !local.enabled }"
    draggable="true"
    @dragstart="onDragStart"
    @dragover.prevent
    @drop="$emit('drag-drop')"
    @dragend="$emit('drag-end')"
  >
    <div class="hook-entry-head">
      <span class="grip" title="Drag to reorder">&#8942;&#8942;</span>
      <span class="type-pill" :class="local.type === 'http' ? 'type-http' : 'type-command'">
        {{ local.type === 'http' ? 'HTTP' : 'Command' }}
      </span>
      <span class="spacer"></span>
      <div class="form-check form-switch mb-0">
        <input class="form-check-input" type="checkbox" v-model="local.enabled" title="Enabled" />
      </div>
      <button class="btn btn-sm btn-outline-danger" @click="$emit('remove')" title="Remove">&times;</button>
    </div>

    <div class="hook-entry-body">
      <label class="form-label small mb-1">Events (one entry &rarr; one rule per event &times; matcher)</label>
      <div class="event-chips">
        <span v-for="ev in local.events" :key="ev" class="event-chip">
          {{ ev }}
          <button type="button" @click="removeEvent(ev)">&times;</button>
        </span>
      </div>
      <div class="event-input-wrap">
        <input
          type="text"
          class="form-control form-control-sm"
          v-model="eventInput"
          placeholder="Type to search events, or enter any custom event name…"
          @focus="showSuggest = true"
          @blur="onEventBlur"
        />
        <div v-if="showSuggest" class="autosuggest-list">
          <div
            v-for="m in eventMatches"
            :key="m.name"
            class="autosuggest-item"
            @mousedown.prevent="selectEvent(m.name)"
          >
            {{ m.name }}
            <span class="ev-desc">{{ m.desc }}</span>
          </div>
          <div
            v-if="eventInput.trim() && !eventMatches.some(m => m.name === eventInput.trim())"
            class="autosuggest-item autosuggest-custom"
            @mousedown.prevent="selectEvent(eventInput.trim())"
          >
            Use custom value "{{ eventInput.trim() }}"
          </div>
        </div>
      </div>

      <div class="row g-2 mt-2">
        <div class="col-6">
          <label class="form-label small mb-1">Matcher <span class="text-muted">(optional)</span></label>
          <input
            type="text"
            class="form-control form-control-sm"
            v-model="local.matcher"
            placeholder="e.g. Bash, Write|Edit, or leave blank for all"
          />
        </div>
        <div class="col-6">
          <label class="form-label small mb-1">Hook Type</label>
          <select class="form-select form-select-sm" v-model="local.type">
            <option value="command">Command / Script</option>
            <option value="http">HTTP</option>
          </select>
        </div>
      </div>

      <template v-if="local.type === 'http'">
        <div class="mt-2">
          <label class="form-label small mb-1">URL</label>
          <input
            type="text"
            class="form-control form-control-sm"
            v-model="local.url"
            placeholder="https://hooks.example.com/..."
          />
        </div>
        <div class="row g-2 mt-2">
          <div class="col-6">
            <label class="form-label small mb-1">Headers</label>
            <input
              type="text"
              class="form-control form-control-sm"
              v-model="headersText"
              @blur="applyHeadersText"
              placeholder="Header: value, Other: value"
            />
          </div>
          <div class="col-6">
            <label class="form-label small mb-1">Allowed Env Vars</label>
            <input
              type="text"
              class="form-control form-control-sm"
              v-model="envVarsText"
              @blur="applyEnvVarsText"
              placeholder="AUDIT_TOKEN, comma-separated"
            />
          </div>
        </div>
        <div class="mt-2">
          <label class="form-label small mb-1">Timeout (seconds)</label>
          <input type="number" class="form-control form-control-sm" v-model.number="local.timeout" placeholder="default" />
        </div>
        <div class="form-text small mt-1">
          Matches the CLI's native HTTP hook schema: only env vars listed in Allowed Env Vars are
          interpolated into headers; unlisted <code>$VAR</code> references resolve to empty strings.
        </div>
      </template>
      <template v-else>
        <div class="mt-2">
          <label class="form-label small mb-1">
            Command <span class="text-muted">— inline shell, or a script path (e.g. <code>uv run hook.py</code>)</span>
          </label>
          <textarea class="form-control form-control-sm" rows="2" v-model="local.command"></textarea>
        </div>
        <div class="mt-2">
          <label class="form-label small mb-1">Timeout (seconds)</label>
          <input type="number" class="form-control form-control-sm" v-model.number="local.timeout" placeholder="default" />
        </div>
      </template>
    </div>
  </div>
</template>

<script setup>
import { reactive, ref, computed, watch } from 'vue'
import { KNOWN_HOOK_EVENTS } from '@/stores/hooks'

const props = defineProps({
  modelValue: {
    type: Object,
    required: true,
  },
})

const emit = defineEmits(['update:modelValue', 'remove', 'drag-start', 'drag-drop', 'drag-end'])

function onDragStart(event) {
  // Firefox refuses to start a native drag operation unless dataTransfer.setData()
  // is called during dragstart — Chrome is lenient about this, Firefox is not.
  event.dataTransfer?.setData('text/plain', '')
  emit('drag-start')
}

const local = reactive({ ...props.modelValue })

watch(local, () => {
  emit('update:modelValue', { ...local })
}, { deep: true })

// ── Events chip input ─────────────────────────────────────────────────────
const eventInput = ref('')
const showSuggest = ref(false)

const eventMatches = computed(() => {
  const q = eventInput.value.trim().toLowerCase()
  if (!q) return KNOWN_HOOK_EVENTS
  return KNOWN_HOOK_EVENTS.filter(e => e.name.toLowerCase().includes(q))
})

function selectEvent(name) {
  if (!name) return
  if (!local.events.includes(name)) local.events.push(name)
  eventInput.value = ''
  showSuggest.value = false
}

function removeEvent(name) {
  local.events = local.events.filter(e => e !== name)
}

function onEventBlur() {
  // Delay so a mousedown on a suggestion item can register first.
  setTimeout(() => { showSuggest.value = false }, 0)
}

// ── Headers / Allowed Env Vars text <-> object conversion ────────────────
function headersToText(headers) {
  return Object.entries(headers || {}).map(([k, v]) => `${k}: ${v}`).join(', ')
}

function parseHeadersText(text) {
  const out = {}
  // Split only on a comma that precedes what looks like the next "Header-Name:"
  // pair, not every comma — otherwise a header value containing a comma itself
  // (e.g. "Accept: text/html,application/json") gets truncated/dropped.
  for (const part of (text || '').split(/,(?=\s*[A-Za-z0-9-]+\s*:)/)) {
    const idx = part.indexOf(':')
    if (idx === -1) continue
    const key = part.slice(0, idx).trim()
    const val = part.slice(idx + 1).trim()
    if (key) out[key] = val
  }
  return Object.keys(out).length ? out : null
}

const headersText = ref(headersToText(props.modelValue.headers))
function applyHeadersText() {
  local.headers = parseHeadersText(headersText.value)
}

const envVarsText = ref((props.modelValue.allowed_env_vars || []).join(', '))
function applyEnvVarsText() {
  const items = envVarsText.value.split(',').map(s => s.trim()).filter(Boolean)
  local.allowed_env_vars = items.length ? items : null
}
</script>

<style scoped>
.hook-entry {
  border: 1px solid var(--bs-border-color);
  border-radius: 7px;
  background: var(--bs-tertiary-bg);
  margin-bottom: 10px;
  overflow: hidden;
}

.hook-entry.disabled {
  opacity: 0.55;
}

.hook-entry-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border-bottom: 1px solid var(--bs-border-color);
}

.hook-entry-head .grip {
  color: var(--bs-secondary-color);
  cursor: grab;
}

.hook-entry-head .spacer {
  flex: 1;
}

.hook-entry-body {
  padding: 12px;
}

.type-pill {
  font-size: 0.65rem;
  font-weight: 700;
  text-transform: uppercase;
  padding: 2px 8px;
  border-radius: 4px;
  letter-spacing: 0.04em;
}

.type-command {
  background: #1e3a5f;
  color: #7dd3fc;
}

.type-http {
  background: #3f2d1e;
  color: #fdba74;
}

.event-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 6px;
}

.event-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: #1e3a5f;
  color: #93c5fd;
  border: 1px solid rgba(37, 99, 235, 0.35);
  padding: 3px 6px 3px 10px;
  border-radius: 12px;
  font-size: 0.72rem;
  font-weight: 500;
}

.event-chip button {
  background: none;
  border: none;
  color: #93c5fd;
  cursor: pointer;
  font-size: 0.8rem;
  padding: 0 2px;
  line-height: 1;
}

.event-input-wrap {
  position: relative;
}

.autosuggest-list {
  position: absolute;
  top: 100%;
  left: 0;
  right: 0;
  z-index: 20;
  background: var(--bs-secondary-bg);
  border: 1px solid var(--bs-border-color);
  border-radius: 5px;
  margin-top: 2px;
  max-height: 180px;
  overflow-y: auto;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.4);
}

.autosuggest-item {
  padding: 6px 10px;
  font-size: 0.78rem;
  cursor: pointer;
}

.autosuggest-item:hover {
  background: var(--bs-tertiary-bg);
}

.autosuggest-item .ev-desc {
  color: var(--bs-secondary-color);
  font-size: 0.68rem;
  display: block;
}

.autosuggest-custom {
  border-top: 1px solid var(--bs-border-color);
  color: var(--bs-secondary-color);
}
</style>
