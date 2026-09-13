<template>
  <div class="sandbox-sub-section">
    <!-- Bash Permissions -->
    <div class="sandbox-section-label">Bash Permissions</div>
    <div class="form-check form-switch mb-1 ms-3">
      <input class="form-check-input" type="checkbox" :id="id('auto-bash')"
        :checked="val.autoAllowBashIfSandboxed"
        :disabled="disabled"
        @change="update('autoAllowBashIfSandboxed', $event.target.checked)" />
      <label class="form-check-label" :for="id('auto-bash')" style="text-transform: none; letter-spacing: normal;">
        Auto-allow Bash when sandboxed
      </label>
    </div>
    <div class="form-check form-switch mb-1 ms-3">
      <input class="form-check-input" type="checkbox" :id="id('unsandboxed')"
        :checked="val.allowUnsandboxedCommands"
        :disabled="disabled"
        @change="update('allowUnsandboxedCommands', $event.target.checked)" />
      <label class="form-check-label" :for="id('unsandboxed')" style="text-transform: none; letter-spacing: normal;">
        Allow unsandboxed commands
      </label>
    </div>
    <div class="mb-2 ms-3">
      <label class="form-label">Excluded Commands</label>
      <input type="text" class="form-control form-control-sm"
        :value="val.excludedCommands"
        :disabled="disabled"
        @input="update('excludedCommands', $event.target.value)"
        placeholder="rm, dd, mkfs..." />
    </div>
    <div class="form-check form-switch mb-1 ms-3">
      <input class="form-check-input" type="checkbox" :id="id('weaker')"
        :checked="val.enableWeakerNestedSandbox"
        :disabled="disabled"
        @change="update('enableWeakerNestedSandbox', $event.target.checked)" />
      <label class="form-check-label" :for="id('weaker')" style="text-transform: none; letter-spacing: normal;">
        Enable weaker nested sandbox
      </label>
    </div>

    <!-- Network -->
    <div class="sandbox-section-label">Network</div>
    <div class="mb-1 ms-3">
      <label class="form-label">Allowed Domains</label>
      <input type="text" class="form-control form-control-sm"
        :value="val.network?.allowedDomains"
        :disabled="disabled"
        @input="updateNetwork('allowedDomains', $event.target.value)"
        placeholder="github.com, api.example.com" />
    </div>
    <div class="form-check form-switch mb-1 ms-3">
      <input class="form-check-input" type="checkbox" :id="id('local-binding')"
        :checked="val.network?.allowLocalBinding"
        :disabled="disabled"
        @change="updateNetwork('allowLocalBinding', $event.target.checked)" />
      <label class="form-check-label" :for="id('local-binding')" style="text-transform: none; letter-spacing: normal;">
        Allow local binding
      </label>
    </div>
    <div class="mb-1 ms-3">
      <label class="form-label">Allow Unix Sockets</label>
      <input type="text" class="form-control form-control-sm"
        :value="val.network?.allowUnixSockets"
        :disabled="disabled"
        @input="updateNetwork('allowUnixSockets', $event.target.value)"
        placeholder="/var/run/docker.sock" />
    </div>
    <div class="form-check form-switch mb-1 ms-3">
      <input class="form-check-input" type="checkbox" :id="id('all-unix')"
        :checked="val.network?.allowAllUnixSockets"
        :disabled="disabled"
        @change="updateNetwork('allowAllUnixSockets', $event.target.checked)" />
      <label class="form-check-label" :for="id('all-unix')" style="text-transform: none; letter-spacing: normal;">
        Allow all Unix sockets
      </label>
    </div>

    <!-- Credentials -->
    <div class="sandbox-section-label">Credentials</div>
    <p class="cred-note ms-3">
      Masking rules for files/environment variables visible inside the <strong>CLI-native OS-level sandbox</strong>
      (macOS/Linux bash sandboxing). This is <strong>unrelated to Docker-based isolation</strong> — it has no
      effect on Docker-isolated sessions and does not use this project's own Docker proxy/credential vault.
      Leave empty for no change in behavior.
    </p>

    <div class="ms-3 mb-1">
      <label class="form-label">Credential Files</label>
      <div v-if="!fileRows.length" class="cred-empty-hint">No file rules — sandboxed sessions see files as-is.</div>
      <div v-for="(row, idx) in fileRows" :key="idx" class="cred-row">
        <div class="cred-row-main">
          <input type="text" class="form-control form-control-sm font-monospace" v-model="row.path"
            :disabled="disabled" placeholder="/home/user/.config/gh/hosts.yml" @input="updateFileRow" />
          <select class="form-select form-select-sm" v-model="row.mode" :disabled="disabled" @change="updateFileRow">
            <option value="deny">Deny</option>
            <option value="allow">Allow</option>
            <option value="mask">Mask</option>
          </select>
          <button type="button" class="btn btn-outline-danger btn-sm" :disabled="disabled" @click="removeFileRow(idx)">×</button>
        </div>
        <div v-if="row.mode === 'mask'" class="cred-row-mask-fields">
          <div class="cred-field">
            <label>Decode</label>
            <select class="form-select form-select-sm" v-model="row.decode" :disabled="disabled" @change="updateFileRow">
              <option value="none">(none)</option>
              <option value="jwt">JWT</option>
            </select>
          </div>
          <div class="cred-field">
            <label>Mask Claims</label>
            <input type="text" class="form-control form-control-sm font-monospace" v-model="row.maskClaims"
              :disabled="disabled || row.decode !== 'jwt'" placeholder="sub, email" @input="updateFileRow" />
          </div>
          <div class="cred-field span-2">
            <label>Extract Pattern (regex, optional — masks only the matched substring)</label>
            <input type="text" class="form-control form-control-sm font-monospace" v-model="row.extract"
              :disabled="disabled" placeholder="sk-[A-Za-z0-9]+" @input="updateFileRow" />
          </div>
          <div v-if="row.extract" class="cred-field span-2">
            <label>On Extract No Match</label>
            <select class="form-select form-select-sm" v-model="row.onExtractNoMatch" :disabled="disabled" @change="updateFileRow">
              <option value="warn">Warn (leave unprotected, log)</option>
              <option value="deny">Deny (block access to the file/var)</option>
              <option value="error">Error (fail session startup)</option>
            </select>
          </div>
        </div>
      </div>
      <button type="button" class="btn btn-outline-secondary btn-sm" :disabled="disabled" @click="addFileRow">+ Add file rule</button>
    </div>

    <div class="ms-3 mb-2 mt-3">
      <label class="form-label">Credential Environment Variables</label>
      <div v-if="!envRows.length" class="cred-empty-hint">No environment variable rules.</div>
      <div v-for="(row, idx) in envRows" :key="idx" class="cred-row">
        <div class="cred-row-main">
          <input type="text" class="form-control form-control-sm font-monospace" v-model="row.name"
            :disabled="disabled" placeholder="ANTHROPIC_API_KEY" @input="updateEnvRow" />
          <select class="form-select form-select-sm" v-model="row.mode" :disabled="disabled" @change="updateEnvRow">
            <option value="deny">Deny</option>
            <option value="allow">Allow</option>
            <option value="mask">Mask</option>
          </select>
          <button type="button" class="btn btn-outline-danger btn-sm" :disabled="disabled" @click="removeEnvRow(idx)">×</button>
        </div>
        <div v-if="row.mode === 'mask'" class="cred-row-mask-fields">
          <div class="cred-field">
            <label>Decode</label>
            <select class="form-select form-select-sm" v-model="row.decode" :disabled="disabled" @change="updateEnvRow">
              <option value="none">(none)</option>
              <option value="jwt">JWT</option>
            </select>
          </div>
          <div class="cred-field">
            <label>Mask Claims</label>
            <input type="text" class="form-control form-control-sm font-monospace" v-model="row.maskClaims"
              :disabled="disabled || row.decode !== 'jwt'" placeholder="sub, email" @input="updateEnvRow" />
          </div>
          <div class="cred-field span-2">
            <label>Extract Pattern (regex, optional — masks only the matched substring)</label>
            <input type="text" class="form-control form-control-sm font-monospace" v-model="row.extract"
              :disabled="disabled" placeholder="sk-[A-Za-z0-9]+" @input="updateEnvRow" />
          </div>
          <div v-if="row.extract" class="cred-field span-2">
            <label>On Extract No Match</label>
            <select class="form-select form-select-sm" v-model="row.onExtractNoMatch" :disabled="disabled" @change="updateEnvRow">
              <option value="warn">Warn (leave unprotected, log)</option>
              <option value="deny">Deny (block access to the file/var)</option>
              <option value="error">Error (fail session startup)</option>
            </select>
          </div>
        </div>
      </div>
      <button type="button" class="btn btn-outline-secondary btn-sm" :disabled="disabled" @click="addEnvRow">+ Add variable rule</button>
    </div>

    <!-- Violation Handling -->
    <div class="sandbox-section-label">Violation Handling</div>
    <div class="mb-1 ms-3">
      <label class="form-label">Ignore File Violations</label>
      <input type="text" class="form-control form-control-sm"
        :value="val.ignoreViolations?.file"
        :disabled="disabled"
        @input="updateViolation('file', $event.target.value)"
        placeholder="File paths to ignore" />
    </div>
    <div class="mb-1 ms-3">
      <label class="form-label">Ignore Network Violations</label>
      <input type="text" class="form-control form-control-sm"
        :value="val.ignoreViolations?.network"
        :disabled="disabled"
        @input="updateViolation('network', $event.target.value)"
        placeholder="Network patterns to ignore" />
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'

const props = defineProps({
  value: { type: Object, default: () => ({}) },
  disabled: { type: Boolean, default: false },
})

const emit = defineEmits(['update:value'])

const prefix = Math.random().toString(36).slice(2, 7)
function id(suffix) { return `sb-${prefix}-${suffix}` }

const val = computed(() => props.value || {})

function update(field, newVal) {
  emit('update:value', { ...val.value, [field]: newVal })
}

function updateNetwork(field, newVal) {
  emit('update:value', {
    ...val.value,
    network: { ...(val.value.network || {}), [field]: newVal },
  })
}

function updateViolation(field, newVal) {
  emit('update:value', {
    ...val.value,
    ignoreViolations: { ...(val.value.ignoreViolations || {}), [field]: newVal },
  })
}

// Credentials: repeatable file/envVar masking rules, mirroring the CLI's
// sandbox.credentials.files/envVars schema (path|name, mode, decode, maskClaims, extract, onExtractNoMatch).
const fileRows = ref([])
const envRows = ref([])

function entryToRow(entry, key) {
  return {
    [key]: entry[key] || '',
    mode: entry.mode || 'deny',
    decode: entry.decode === 'jwt' ? 'jwt' : 'none',
    maskClaims: Array.isArray(entry.maskClaims) ? entry.maskClaims.join(', ') : '',
    extract: entry.extract || '',
    onExtractNoMatch: entry.onExtractNoMatch || 'warn',
  }
}

function rowToEntry(row, key) {
  const out = { [key]: row[key], mode: row.mode }
  if (row.mode === 'mask') {
    if (row.decode === 'jwt') {
      out.decode = 'jwt'
      const claims = row.maskClaims.split(',').map((s) => s.trim()).filter(Boolean)
      if (claims.length) out.maskClaims = claims
    }
    if (row.extract) {
      out.extract = row.extract
      out.onExtractNoMatch = row.onExtractNoMatch
    }
  }
  return out
}

// rowToEntry/entryToRow is a lossy round-trip (e.g. maskClaims trims/drops empty
// segments), so re-deriving fileRows/envRows from our own emit would fight the
// user mid-keystroke (a trailing "," or " " they just typed gets stripped
// before they can type the next character). Skip the resync once per self-emit.
let skipNextSync = false

watch(() => props.value, () => {
  if (skipNextSync) {
    skipNextSync = false
    return
  }
  fileRows.value = (val.value.credentials?.files || []).map((e) => entryToRow(e, 'path'))
  envRows.value = (val.value.credentials?.envVars || []).map((e) => entryToRow(e, 'name'))
}, { immediate: true })

function emitCredentials(listKey, rows, key) {
  skipNextSync = true
  emit('update:value', {
    ...val.value,
    credentials: {
      ...(val.value.credentials || {}),
      [listKey]: rows.map((r) => rowToEntry(r, key)),
    },
  })
}

function addFileRow() {
  fileRows.value.push({ path: '', mode: 'deny', decode: 'none', maskClaims: '', extract: '', onExtractNoMatch: 'warn' })
  updateFileRow()
}
function removeFileRow(idx) {
  fileRows.value.splice(idx, 1)
  updateFileRow()
}
function updateFileRow() {
  emitCredentials('files', fileRows.value, 'path')
}

function addEnvRow() {
  envRows.value.push({ name: '', mode: 'deny', decode: 'none', maskClaims: '', extract: '', onExtractNoMatch: 'warn' })
  updateEnvRow()
}
function removeEnvRow(idx) {
  envRows.value.splice(idx, 1)
  updateEnvRow()
}
function updateEnvRow() {
  emitCredentials('envVars', envRows.value, 'name')
}
</script>

<style scoped>
.sandbox-sub-section .form-label {
  font-size: 0.8rem;
  margin-bottom: 2px;
}

.cred-note {
  font-size: 0.72rem;
  line-height: 1.5;
  color: var(--bs-secondary-color);
  margin: 0 0 8px 0;
  padding: 6px 10px;
  background: var(--bs-secondary-bg);
  border: 1px dashed var(--bs-border-color);
  border-radius: 6px;
  max-width: 640px;
}

.cred-empty-hint {
  font-size: 0.78rem;
  color: var(--bs-tertiary-color);
  font-style: italic;
  padding: 2px 0 6px;
}

.cred-row {
  border: 1px solid var(--bs-border-color);
  background: var(--bs-secondary-bg);
  border-radius: 6px;
  padding: 8px;
  margin-bottom: 6px;
}

.cred-row-main {
  display: grid;
  grid-template-columns: 1fr 120px auto;
  gap: 8px;
  align-items: center;
}

.cred-row-mask-fields {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px dashed var(--bs-border-color);
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px 10px;
}

.cred-field label {
  display: block;
  font-size: 0.65rem;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  color: var(--bs-tertiary-color);
  margin-bottom: 2px;
}

.cred-field.span-2 {
  grid-column: 1 / -1;
}
</style>
