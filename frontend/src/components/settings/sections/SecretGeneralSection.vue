<template>
  <div class="settings-section">
    <SettingsToolbar
      :title="isNew ? 'New Secret' : 'General'"
      :show-save-cancel="isNew || isDirty"
      :saving="saving"
      :save-disabled="!canSave"
      @save="handleSave"
      @cancel="handleCancel"
    />

    <!-- Backend warning banner -->
    <div v-if="secretsStore.backendWarning" class="backend-warning">
      <strong>Keyring:</strong> {{ secretsStore.backendWarning }}
    </div>

    <div v-if="!isNew && !entity" class="section-loading">Loading…</div>

    <div v-else class="section-body">
      <!-- Save error -->
      <div v-if="saveError" class="save-error">{{ saveError }}</div>

      <!-- Name -->
      <div class="field-row">
        <label class="field-label">Name</label>
        <div class="field-control">
          <input
            type="text"
            class="field-input field-input--mono"
            :value="draftName"
            :disabled="!isNew"
            :placeholder="isNew ? 'my-secret-name' : ''"
            @input="setField('name', $event.target.value)"
          />
          <div class="field-helper">
            a-z0-9, underscores, dashes — set at creation, immutable after save
          </div>
        </div>
      </div>

      <!-- Type -->
      <div class="field-row">
        <label class="field-label">Type</label>
        <div class="field-control">
          <select
            v-if="isNew"
            class="field-input"
            :value="typeValue"
            @change="setField('type', $event.target.value)"
          >
            <option v-for="t in TYPE_OPTIONS" :key="t.key" :value="t.key">{{ t.label }}</option>
          </select>
          <span v-else class="field-value-readonly">{{ typeLabel }}</span>
          <div v-if="!isNew" class="field-helper">Type cannot be changed. Delete and recreate to use a different type.</div>
        </div>
      </div>

      <!-- Target Hosts (not for ssh_key) -->
      <div v-if="typeValue !== 'ssh_key'" class="field-row">
        <label class="field-label">Target Hosts</label>
        <div class="field-control">
          <input
            type="text"
            class="field-input"
            :value="draftField('target_hosts_raw') ?? targetHostsRaw"
            placeholder="api.example.com, *.example.com"
            @input="setField('target_hosts_raw', $event.target.value)"
          />
          <div class="field-helper">Comma-separated hostnames or wildcard patterns. Required.</div>
        </div>
      </div>

      <!-- basic_auth: username -->
      <div v-if="typeValue === 'basic_auth'" class="field-row">
        <label class="field-label">Username</label>
        <div class="field-control">
          <input
            type="text"
            class="field-input"
            :value="draftField('username') ?? (entity?.username ?? '')"
            placeholder="user@example.com"
            autocomplete="off"
            @input="setField('username', $event.target.value)"
          />
          <div class="field-helper">Stored as plaintext metadata (not in keyring).</div>
        </div>
      </div>

      <!-- Value / Password / Private Key -->
      <div class="field-row">
        <label class="field-label">{{ valueLabel }}</label>
        <div class="field-control">
          <textarea
            v-if="typeValue === 'ssh_key'"
            class="field-input field-textarea field-textarea--mono"
            :value="draftField('value') ?? ''"
            rows="6"
            :placeholder="isNew ? '-----BEGIN OPENSSH PRIVATE KEY-----\n...' : 'Leave blank to keep current key'"
            autocomplete="off"
            spellcheck="false"
            @input="setField('value', $event.target.value)"
          />
          <input
            v-else
            type="password"
            class="field-input"
            :value="draftField('value') ?? ''"
            :placeholder="isNew ? 'Secret value (write-only)' : 'Leave blank to keep existing value'"
            autocomplete="new-password"
            @input="setField('value', $event.target.value)"
          />
          <div v-if="!isNew" class="field-helper">Values are write-only. Leave blank to keep the current value.</div>
        </div>
      </div>

      <!-- ssh_key derived info (read-only, editing existing only) -->
      <template v-if="typeValue === 'ssh_key' && !isNew && sshKeyMeta">
        <div class="field-row">
          <label class="field-label">Key Type</label>
          <div class="field-control">
            <span class="field-value-readonly field-value--mono">{{ sshKeyMeta.key_type || '—' }}</span>
          </div>
        </div>
        <div class="field-row">
          <label class="field-label">Fingerprint (SHA256)</label>
          <div class="field-control">
            <div class="copy-row">
              <code class="field-value-readonly field-value--mono">{{ sshKeyMeta.fingerprint_sha256 || '—' }}</code>
              <button
                v-if="sshKeyMeta.fingerprint_sha256"
                class="copy-btn"
                title="Copy fingerprint"
                @click="copyToClipboard(sshKeyMeta.fingerprint_sha256)"
              >Copy</button>
            </div>
          </div>
        </div>
        <div class="field-row">
          <label class="field-label">Public Key</label>
          <div class="field-control">
            <div class="copy-row copy-row--wrap">
              <code class="field-value-readonly field-value--mono field-value--break">{{ sshKeyMeta.public_key_openssh || '—' }}</code>
              <button
                v-if="sshKeyMeta.public_key_openssh"
                class="copy-btn copy-btn--top"
                title="Copy public key"
                @click="copyToClipboard(sshKeyMeta.public_key_openssh)"
              >Copy</button>
            </div>
            <div class="field-helper">Paste into GitHub → Repository → Settings → Deploy keys</div>
          </div>
        </div>
      </template>

      <!-- api_key injection block -->
      <template v-if="typeValue === 'api_key'">
        <div class="sub-section-header">Injection Config</div>
        <div class="field-row">
          <label class="field-label">Location</label>
          <div class="field-control">
            <select
              class="field-input field-input--narrow"
              :value="draftField('injection_location') ?? (entity?.injection?.location ?? 'header')"
              @change="setField('injection_location', $event.target.value)"
            >
              <option value="header">header</option>
              <option value="query_param">query_param</option>
            </select>
          </div>
        </div>
        <template v-if="injectionLocation === 'header'">
          <div class="field-row">
            <label class="field-label">Header Name</label>
            <div class="field-control">
              <input
                type="text"
                class="field-input field-input--mono"
                :value="draftField('injection_header_name') ?? (entity?.injection?.header_name ?? 'Authorization')"
                placeholder="Authorization"
                @input="setField('injection_header_name', $event.target.value)"
              />
            </div>
          </div>
          <div class="field-row">
            <label class="field-label">Prefix</label>
            <div class="field-control">
              <input
                type="text"
                class="field-input field-input--mono field-input--narrow"
                :value="draftField('injection_prefix') ?? (entity?.injection?.prefix ?? 'Bearer')"
                placeholder="Bearer"
                @input="setField('injection_prefix', $event.target.value)"
              />
              <div class="field-helper">Leave blank for no prefix (value sent bare).</div>
            </div>
          </div>
        </template>
        <template v-if="injectionLocation === 'query_param'">
          <div class="field-row">
            <label class="field-label">Query Param Name</label>
            <div class="field-control">
              <input
                type="text"
                class="field-input field-input--mono"
                :value="draftField('injection_param_name') ?? (entity?.injection?.param_name ?? '')"
                placeholder="api_key"
                @input="setField('injection_param_name', $event.target.value)"
              />
            </div>
          </div>
        </template>
      </template>

      <!-- oauth2 refresh block -->
      <template v-if="typeValue === 'oauth2'">
        <div class="sub-section-header">OAuth2 Token Refresh</div>
        <div class="field-row">
          <label class="field-label">Token URL</label>
          <div class="field-control">
            <input
              type="text"
              class="field-input field-input--mono"
              :value="draftField('refresh_token_url') ?? (entity?.refresh?.token_url ?? '')"
              placeholder="https://provider.example.com/oauth/token"
              @input="setField('refresh_token_url', $event.target.value)"
            />
          </div>
        </div>
        <div class="field-row">
          <label class="field-label">Client ID</label>
          <div class="field-control">
            <input
              type="text"
              class="field-input field-input--mono"
              :value="draftField('refresh_client_id') ?? (entity?.refresh?.client_id ?? '')"
              placeholder="Iv1.abc123"
              @input="setField('refresh_client_id', $event.target.value)"
            />
          </div>
        </div>
        <div class="field-row">
          <label class="field-label">Refresh Token Secret</label>
          <div class="field-control">
            <select
              class="field-input field-input--mono"
              :value="draftField('refresh_token_secret_name') ?? (entity?.refresh?.refresh_token_secret_name ?? '')"
              @change="setField('refresh_token_secret_name', $event.target.value)"
            >
              <option value="">— select secret —</option>
              <option v-for="s in otherSecrets" :key="s.name" :value="s.name">{{ s.name }}</option>
            </select>
            <div class="field-helper">Name of the sibling secret holding the refresh token value.</div>
          </div>
        </div>
        <div class="field-row">
          <label class="field-label">Client Secret (optional)</label>
          <div class="field-control">
            <select
              class="field-input field-input--mono"
              :value="draftField('refresh_client_secret_secret_name') ?? (entity?.refresh?.client_secret_secret_name ?? '')"
              @change="setField('refresh_client_secret_secret_name', $event.target.value)"
            >
              <option value="">— none —</option>
              <option v-for="s in otherSecrets" :key="s.name" :value="s.name">{{ s.name }}</option>
            </select>
          </div>
        </div>
        <div class="field-row">
          <label class="field-label">Buffer (seconds)</label>
          <div class="field-control">
            <input
              type="number"
              class="field-input field-input--narrow"
              min="0"
              :value="draftField('refresh_buffer_seconds') ?? (entity?.refresh?.buffer_seconds ?? 60)"
              @input="setField('refresh_buffer_seconds', Number($event.target.value))"
            />
            <div class="field-helper">Refresh this many seconds before the token expires.</div>
          </div>
        </div>
        <div class="field-row">
          <label class="field-label">Expires At</label>
          <div class="field-control">
            <input
              type="datetime-local"
              class="field-input field-input--narrow"
              :value="draftField('refresh_expires_at') ?? expiresAtForInput"
              @input="setField('refresh_expires_at', $event.target.value)"
            />
            <div class="field-helper">Optional. Leave blank if the token doesn't expire or will refresh automatically.</div>
          </div>
        </div>

        <!-- oauth2 scrub block -->
        <div class="sub-section-header">Response Capture (Scrub)</div>
        <div class="field-row">
          <label class="field-label">URL Path Pattern</label>
          <div class="field-control">
            <input
              type="text"
              class="field-input field-input--mono"
              :value="draftField('scrub_url_path') ?? (entity?.scrub?.url_path ?? '')"
              placeholder="/login/oauth/access_token"
              @input="setField('scrub_url_path', $event.target.value)"
            />
            <div class="field-helper">Substring match on request path to trigger capture.</div>
          </div>
        </div>
        <div class="field-row">
          <label class="field-label">JSONPath Matcher</label>
          <div class="field-control">
            <input
              type="text"
              class="field-input field-input--mono"
              :value="draftField('scrub_matcher_jsonpath') ?? (entity?.scrub?.matcher_jsonpath ?? '')"
              placeholder="$.access_token"
              @input="setField('scrub_matcher_jsonpath', $event.target.value)"
            />
          </div>
        </div>
        <div class="field-row">
          <label class="field-label">Regex Matcher</label>
          <div class="field-control">
            <input
              type="text"
              class="field-input field-input--mono"
              :value="draftField('scrub_matcher_regex') ?? (entity?.scrub?.matcher_regex ?? '')"
              placeholder="access_token=([^&amp;]+)"
              @input="setField('scrub_matcher_regex', $event.target.value)"
            />
          </div>
        </div>
        <div class="field-row">
          <label class="field-label">Auto-update</label>
          <div class="field-control">
            <label class="toggle-label">
              <input
                type="checkbox"
                :checked="draftField('scrub_update_on_change') ?? (entity?.scrub?.update_on_change !== false)"
                @change="setField('scrub_update_on_change', $event.target.checked)"
              />
              <span>Update keyring when captured value changes</span>
            </label>
          </div>
        </div>
      </template>

      <!-- Common injection section -->
      <div class="sub-section-header">Injection</div>
      <div class="field-row">
        <label class="field-label">Inject Env Var</label>
        <div class="field-control">
          <input
            type="text"
            class="field-input field-input--mono"
            :value="draftField('inject_env') ?? (entity?.inject_env ?? '')"
            placeholder="GITHUB_TOKEN"
            @input="setField('inject_env', $event.target.value)"
          />
          <div class="field-helper">Environment variable name the secret will be exported as.</div>
        </div>
      </div>
      <div class="field-row">
        <label class="field-label">Inject File Path</label>
        <div class="field-control">
          <input
            type="text"
            class="field-input field-input--mono"
            :value="draftField('inject_file_path') ?? (entity?.inject_file?.path ?? '')"
            placeholder="/etc/credentials/example.yml"
            @input="setField('inject_file_path', $event.target.value)"
          />
        </div>
      </div>
      <template v-if="injectFilePath">
        <div class="field-row">
          <label class="field-label">File Format</label>
          <div class="field-control">
            <select
              class="field-input field-input--narrow"
              :value="draftField('inject_file_format') ?? (entity?.inject_file?.format ?? 'raw')"
              @change="setField('inject_file_format', $event.target.value)"
            >
              <option value="raw">raw</option>
              <option value="yaml">yaml</option>
              <option value="json">json</option>
              <option value="toml">toml</option>
            </select>
          </div>
        </div>
        <div class="field-row">
          <label class="field-label">Permissions</label>
          <div class="field-control">
            <input
              type="text"
              class="field-input field-input--narrow field-input--mono"
              :value="draftField('inject_file_permissions') ?? (entity?.inject_file?.permissions ?? '0600')"
              placeholder="0600"
              @input="setField('inject_file_permissions', $event.target.value)"
            />
          </div>
        </div>
        <div class="field-row">
          <label class="field-label">Key Path</label>
          <div class="field-control">
            <input
              type="text"
              class="field-input field-input--mono"
              :value="draftField('inject_file_key_path') ?? (entity?.inject_file?.key_path ?? '')"
              placeholder="github.com.oauth_token"
              @input="setField('inject_file_key_path', $event.target.value)"
            />
            <div class="field-helper">Dot-path into the file structure where the secret value is placed.</div>
          </div>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useSettingsStore } from '@/stores/settings'
import { useSecretsStore } from '@/stores/secrets'
import SettingsToolbar from '../SettingsToolbar.vue'

const SECTION_KEY = 'general'

const route = useRoute()
const router = useRouter()
const settingsStore = useSettingsStore()
const secretsStore = useSecretsStore()

const saving = ref(false)
const saveError = ref(null)

const TYPE_OPTIONS = [
  { key: 'api_key',    label: 'API Key' },
  { key: 'bearer',     label: 'Bearer Token' },
  { key: 'basic_auth', label: 'Basic Auth' },
  { key: 'oauth2',     label: 'OAuth2' },
  { key: 'ssh_key',    label: 'SSH Key' },
  { key: 'generic',    label: 'Generic' },
]

const TYPE_LABELS = Object.fromEntries(TYPE_OPTIONS.map(t => [t.key, t.label]))

const secretName = computed(() => route.params.secretName || '')
const areaKey    = computed(() => `secret:${secretName.value}:${SECTION_KEY}`)
const isNew      = computed(() => secretName.value === '__new__')
const newType    = computed(() => route.query.type || 'generic')
const entity     = computed(() => isNew.value ? null : secretsStore.secrets.find(s => s.name === secretName.value))
const draft      = computed(() => settingsStore.getDraft(areaKey.value))
const isDirty    = computed(() => settingsStore.dirtyAreas.has(areaKey.value))

const canSave = computed(() => {
  if (isNew.value) {
    const name = draftField('name') ?? ''
    if (!name.trim()) return false
    if (typeValue.value !== 'ssh_key') {
      const hosts = (draftField('target_hosts_raw') ?? '').trim()
      if (!hosts) return false
    }
    const val = draftField('value') ?? ''
    if (!val) return false
    if (typeValue.value === 'basic_auth' && !(draftField('username') ?? '').trim()) return false
    if (typeValue.value === 'api_key' && injectionLocation.value === 'query_param') {
      if (!(draftField('injection_param_name') ?? '').trim()) return false
    }
    if (typeValue.value === 'oauth2') {
      if (!(draftField('refresh_token_url') ?? '').trim()) return false
      if (!(draftField('refresh_client_id') ?? '').trim()) return false
      if (!(draftField('refresh_token_secret_name') ?? '')) return false
    }
  }
  return true
})

// Other secrets available for oauth2 sibling dropdowns
const otherSecrets = computed(() =>
  secretsStore.secrets.filter(s => s.name !== secretName.value)
)

// ── Derived display values ───────────────────────────────────────────────────

function draftField(key) {
  return draft.value ? (key in draft.value ? draft.value[key] : undefined) : undefined
}

const typeValue = computed(() => {
  if (isNew.value) return draftField('type') ?? newType.value
  return entity.value?.type ?? 'generic'
})

const typeLabel = computed(() => TYPE_LABELS[typeValue.value] ?? typeValue.value)

const draftName = computed(() => {
  if (isNew.value) return draftField('name') ?? ''
  return entity.value?.name ?? ''
})

const targetHostsRaw = computed(() =>
  (entity.value?.target_hosts ?? []).join(', ')
)

const valueLabel = computed(() => {
  if (typeValue.value === 'basic_auth') return 'Password'
  if (typeValue.value === 'ssh_key') return 'Private Key (PEM)'
  return 'Secret Value'
})

const injectionLocation = computed(() =>
  draftField('injection_location') ?? entity.value?.injection?.location ?? 'header'
)

const injectFilePath = computed(() =>
  draftField('inject_file_path') ?? entity.value?.inject_file?.path ?? ''
)

const expiresAtForInput = computed(() => {
  const ts = entity.value?.refresh?.expires_at
  if (!ts) return ''
  try { return new Date(ts).toISOString().slice(0, 16) } catch { return '' }
})

const sshKeyMeta = computed(() => {
  if (!entity.value || entity.value.type !== 'ssh_key') return null
  const { public_key_openssh, fingerprint_sha256, key_type } = entity.value
  if (!public_key_openssh && !fingerprint_sha256) return null
  return { public_key_openssh, fingerprint_sha256, key_type }
})

// ── Draft helpers ────────────────────────────────────────────────────────────

function setField(key, value) {
  settingsStore.setField(areaKey.value, key, value)
}

// ── Seed new draft on mount ──────────────────────────────────────────────────

onMounted(async () => {
  await secretsStore.fetchIfEmpty()
  secretsStore.fetchBackendStatus()
  if (isNew.value && !draft.value) {
    setField('name', '')
    setField('type', newType.value)
  }
})

// ── Build save payload ───────────────────────────────────────────────────────

function buildPayload() {
  const d = draft.value || {}

  const targetHostsRawValue = draftField('target_hosts_raw') ?? targetHostsRaw.value
  const hosts = targetHostsRawValue
    .split(',')
    .map(h => h.trim())
    .filter(Boolean)

  const payload = {}

  if (isNew.value) {
    payload.name = (d.name || '').trim()
    payload.type = typeValue.value
    payload.target_hosts = hosts
  } else {
    // On edit: never send type; send target_hosts only if changed
    if ('target_hosts_raw' in d) payload.target_hosts = hosts
  }

  // Value: only include if non-empty
  const valueVal = draftField('value') ?? ''
  if (valueVal) payload.value = valueVal

  // inject_env
  const injectEnvVal = draftField('inject_env') ?? entity.value?.inject_env ?? ''
  if (injectEnvVal) payload.inject_env = injectEnvVal
  else if ('inject_env' in d) payload.inject_env = null

  // inject_file
  const filePath = draftField('inject_file_path') ?? entity.value?.inject_file?.path ?? ''
  if (filePath) {
    payload.inject_file = {
      path: filePath,
      format: draftField('inject_file_format') ?? entity.value?.inject_file?.format ?? 'raw',
      permissions: draftField('inject_file_permissions') ?? entity.value?.inject_file?.permissions ?? '0600',
      key_path: draftField('inject_file_key_path') ?? entity.value?.inject_file?.key_path ?? null,
    }
  } else if ('inject_file_path' in d) {
    payload.inject_file = null
  }

  // basic_auth
  if (typeValue.value === 'basic_auth') {
    const username = draftField('username') ?? entity.value?.username ?? ''
    if (username) payload.username = username
  }

  // api_key injection
  if (typeValue.value === 'api_key') {
    const loc = injectionLocation.value
    payload.injection = {
      location: loc,
      header_name: draftField('injection_header_name') ?? entity.value?.injection?.header_name ?? 'Authorization',
      prefix: draftField('injection_prefix') ?? entity.value?.injection?.prefix ?? 'Bearer',
      param_name: draftField('injection_param_name') ?? entity.value?.injection?.param_name ?? null,
    }
  }

  // oauth2 refresh + scrub
  if (typeValue.value === 'oauth2') {
    const expiresAtRaw = draftField('refresh_expires_at') ?? expiresAtForInput.value
    payload.refresh = {
      token_url: draftField('refresh_token_url') ?? entity.value?.refresh?.token_url ?? '',
      client_id: draftField('refresh_client_id') ?? entity.value?.refresh?.client_id ?? '',
      refresh_token_secret_name: draftField('refresh_token_secret_name') ?? entity.value?.refresh?.refresh_token_secret_name ?? '',
      client_secret_secret_name: draftField('refresh_client_secret_secret_name') ?? entity.value?.refresh?.client_secret_secret_name ?? null,
      buffer_seconds: draftField('refresh_buffer_seconds') ?? entity.value?.refresh?.buffer_seconds ?? 60,
      expires_at: expiresAtRaw ? new Date(expiresAtRaw).toISOString() : null,
    }
    payload.scrub = {
      url_path: draftField('scrub_url_path') ?? entity.value?.scrub?.url_path ?? null,
      matcher_jsonpath: draftField('scrub_matcher_jsonpath') ?? entity.value?.scrub?.matcher_jsonpath ?? null,
      matcher_regex: draftField('scrub_matcher_regex') ?? entity.value?.scrub?.matcher_regex ?? null,
      update_on_change: draftField('scrub_update_on_change') ?? entity.value?.scrub?.update_on_change !== false,
    }
  }

  return payload
}

// ── Save / cancel ────────────────────────────────────────────────────────────

async function handleSave() {
  saveError.value = null
  saving.value = true
  try {
    const payload = buildPayload()
    if (isNew.value) {
      await secretsStore.createSecret(payload)
      settingsStore.markClean(areaKey.value)
      router.push(`/settings/secret/${encodeURIComponent(payload.name)}/general`)
    } else {
      await secretsStore.updateSecret(secretName.value, payload)
      settingsStore.markClean(areaKey.value)
    }
  } catch (err) {
    saveError.value = err.message || 'Failed to save secret'
  } finally {
    saving.value = false
  }
}

function handleCancel() {
  if (isNew.value) {
    settingsStore.discardDraft(areaKey.value)
    router.push('/settings/secrets')
  } else {
    settingsStore.discardDraft(areaKey.value)
  }
}

defineExpose({ save: handleSave, cancel: handleCancel })

// ── Clipboard helper ─────────────────────────────────────────────────────────

async function copyToClipboard(text) {
  try {
    await navigator.clipboard.writeText(text)
  } catch {
    const el = document.createElement('textarea')
    el.value = text
    document.body.appendChild(el)
    el.select()
    document.execCommand('copy')
    document.body.removeChild(el)
  }
}
</script>

<style scoped>
.settings-section {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
}

/* ── Backend warning ─────────────────────────── */
.backend-warning {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 16px;
  font-size: 12px;
  background: rgba(210, 153, 34, 0.1);
  border-bottom: 1px solid rgba(210, 153, 34, 0.3);
  color: #d29922;
  flex-shrink: 0;
}

.section-loading {
  padding: 24px 20px;
  color: var(--bs-secondary-color);
  font-size: 13px;
}

/* ── Save error ─────────────────────────────── */
.save-error {
  margin: 12px 20px 0;
  padding: 8px 12px;
  background: rgba(248, 113, 113, 0.1);
  border: 1px solid rgba(248, 113, 113, 0.3);
  border-radius: 6px;
  color: #f87171;
  font-size: 12px;
  flex-shrink: 0;
}

.section-body {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 0;
}

/* ── Field rows ──────────────────────────────── */
.field-row {
  display: grid;
  grid-template-columns: 220px 1fr;
  gap: 12px;
  align-items: start;
  padding: 12px 0;
  border-bottom: 1px solid var(--bs-border-color);
}

@container settings-area (max-width: 599px) {
  .field-row {
    grid-template-columns: 1fr;
  }
}

.field-label {
  font-size: 13px;
  font-weight: 500;
  color: var(--bs-emphasis-color);
  padding-top: 6px;
  white-space: nowrap;
}

.field-control {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.field-input {
  width: 100%;
  padding: 6px 10px;
  border: 1px solid var(--bs-border-color);
  border-radius: 6px;
  background: var(--bs-body-bg);
  color: var(--bs-body-color);
  font-size: 13px;
  outline: none;
  transition: border-color 0.12s;
}

.field-input:focus {
  border-color: #06b6d4;
}

.field-input:disabled {
  opacity: 0.6;
  cursor: default;
}

.field-input--narrow {
  max-width: 200px;
}

.field-input--mono {
  font-family: monospace;
}

.field-textarea {
  resize: vertical;
  min-height: 100px;
  font-family: inherit;
}

.field-textarea--mono {
  font-family: monospace;
  font-size: 12px;
}

.field-helper {
  font-size: 11px;
  color: var(--bs-tertiary-color);
  line-height: 1.5;
}

.field-value-readonly {
  font-size: 13px;
  color: var(--bs-body-color);
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.field-value--mono {
  font-family: monospace;
}

.field-value--break {
  word-break: break-all;
  flex: 1;
}

/* ── Sub-section headers ─────────────────────── */
.sub-section-header {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--bs-tertiary-color);
  margin-top: 20px;
  margin-bottom: 4px;
  padding-bottom: 4px;
  border-bottom: 1px solid var(--bs-border-color);
}

/* ── Copy row ────────────────────────────────── */
.copy-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.copy-row--wrap {
  align-items: flex-start;
}

.copy-btn {
  flex-shrink: 0;
  padding: 3px 10px;
  border-radius: 5px;
  border: 1px solid var(--bs-border-color);
  background: none;
  color: var(--bs-secondary-color);
  font-size: 11px;
  cursor: pointer;
  transition: background 0.12s, border-color 0.12s, color 0.12s;
}

.copy-btn:hover {
  border-color: #06b6d4;
  color: #06b6d4;
}

.copy-btn--top {
  align-self: flex-start;
}

/* ── Toggle ──────────────────────────────────── */
.toggle-label {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  cursor: pointer;
}
</style>
