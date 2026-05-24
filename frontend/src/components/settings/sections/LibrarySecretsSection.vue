<template>
  <div class="lib-section">
    <SettingsToolbar title="Secrets" />

    <!-- Backend warning banner -->
    <div v-if="secretsStore.backendWarning" class="backend-warning">
      <strong>Keyring:</strong> {{ secretsStore.backendWarning }}
      <span v-if="secretsStore.activeBackend" class="backend-chip">{{ secretsStore.activeBackend }}</span>
    </div>

    <!-- Refresh error banner -->
    <div v-if="refreshError" class="refresh-error">
      <strong>Refresh failed:</strong> {{ refreshError }}
      <button class="refresh-error-close" @click="refreshError = null">✕</button>
    </div>

    <div v-if="secretsStore.loading" class="section-status">
      <span class="status-spinner" aria-hidden="true">⟳</span> Loading…
    </div>
    <div v-else-if="secretsStore.error" class="section-error">{{ secretsStore.error }}</div>

    <div v-else class="section-body">
      <div
        v-for="typeEntry in SECRET_TYPES"
        :key="typeEntry.key"
        class="category-block"
      >
        <div class="category-header">
          <span class="category-label">{{ typeEntry.label }}</span>
          <button
            class="category-add-btn"
            :title="`New ${typeEntry.label} secret`"
            @click="quickCreate(typeEntry.key)"
          >+</button>
        </div>

        <div v-if="secretsByType(typeEntry.key).length === 0" class="empty-state">
          No {{ typeEntry.label.toLowerCase() }} secrets. Click + to create one.
        </div>

        <div
          v-for="secret in secretsByType(typeEntry.key)"
          :key="secret.name"
          class="secret-row"
          :class="{ 'secret-row--confirm': pendingDeleteName === secret.name }"
          role="button"
          tabindex="0"
          @click="pendingDeleteName !== secret.name && openEdit(secret)"
          @keydown.enter.prevent="pendingDeleteName !== secret.name && openEdit(secret)"
          @keydown.space.prevent="pendingDeleteName !== secret.name && openEdit(secret)"
        >
          <template v-if="pendingDeleteName === secret.name">
            <span class="confirm-text">Delete "{{ secret.name }}"?</span>
            <div class="confirm-btns">
              <button
                class="btn-confirm-delete"
                :disabled="deleting"
                @click.stop="executeDelete(secret.name)"
              >{{ deleting ? '…' : 'Delete' }}</button>
              <button class="btn-confirm-cancel" @click.stop="cancelDelete">Cancel</button>
            </div>
          </template>
          <template v-else>
            <div class="row-left">
              <span class="secret-name">{{ secret.name }}</span>
              <div class="row-meta">
                <!-- Target hosts summary -->
                <span v-if="secret.type !== 'ssh_key' && secret.target_hosts?.length" class="meta-chip">
                  {{ secret.target_hosts.slice(0, 2).join(', ') }}<span v-if="secret.target_hosts.length > 2"> +{{ secret.target_hosts.length - 2 }}</span>
                </span>
                <!-- SSH fingerprint -->
                <span v-if="secret.type === 'ssh_key' && secret.fingerprint_sha256" class="meta-chip meta-chip--mono">
                  {{ secret.fingerprint_sha256.slice(0, 20) }}…
                </span>
                <!-- OAuth2 expiry chip -->
                <span
                  v-if="secret.type === 'oauth2' && expiryLabel(secret)"
                  class="expiry-chip"
                  :class="expiryChipClass(secret)"
                  :title="expiryTitle(secret)"
                >{{ expiryLabel(secret) }}</span>
              </div>
            </div>
            <div class="row-right">
              <!-- Manual oauth2 refresh -->
              <button
                v-if="secret.type === 'oauth2'"
                class="row-refresh-btn"
                :disabled="refreshingName === secret.name"
                title="Manually refresh OAuth2 access token"
                @click.stop="triggerRefresh(secret.name)"
              >
                <span v-if="refreshingName === secret.name" class="refresh-spinner">⟳</span>
                <span v-else>↻</span>
              </button>
              <button
                class="row-delete-btn"
                title="Delete secret"
                @click.stop="pendingDeleteName = secret.name"
              >✕</button>
              <span class="row-chevron" aria-hidden="true">›</span>
            </div>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useSecretsStore } from '@/stores/secrets'
import SettingsToolbar from '../SettingsToolbar.vue'

const router = useRouter()
const secretsStore = useSecretsStore()
const pendingDeleteName = ref(null)
const deleting = ref(false)
const refreshingName = ref(null)
const refreshError = ref(null)

const SECRET_TYPES = [
  { key: 'api_key',    label: 'API Key' },
  { key: 'bearer',     label: 'Bearer Token' },
  { key: 'basic_auth', label: 'Basic Auth' },
  { key: 'oauth2',     label: 'OAuth2' },
  { key: 'ssh_key',    label: 'SSH Key' },
  { key: 'generic',    label: 'Generic' },
]

function secretsByType(typeKey) {
  return secretsStore.secrets
    .filter(s => s.type === typeKey)
    .sort((a, b) => a.name.localeCompare(b.name))
}

function openEdit(secret) {
  router.push(`/settings/secret/${encodeURIComponent(secret.name)}/general`)
}

function quickCreate(typeKey) {
  router.push(`/settings/secret/__new__/general?type=${typeKey}`)
}

function cancelDelete() {
  pendingDeleteName.value = null
}

async function executeDelete(name) {
  if (deleting.value) return
  deleting.value = true
  try {
    await secretsStore.deleteSecret(name)
  } catch (err) {
    console.error('Delete secret failed:', err)
  } finally {
    deleting.value = false
    pendingDeleteName.value = null
  }
}

async function triggerRefresh(name) {
  refreshingName.value = name
  refreshError.value = null
  try {
    await secretsStore.refreshSecret(name)
  } catch (err) {
    console.error('Failed to refresh secret:', err)
    refreshError.value = err.message || String(err)
  } finally {
    refreshingName.value = null
  }
}

function expiryLabel(secret) {
  const expiresAt = secret.refresh?.expires_at
  if (!expiresAt) return null
  const exp = new Date(expiresAt)
  const diffMs = exp - new Date()
  if (diffMs < 0) return 'expired'
  const diffMin = Math.floor(diffMs / 60000)
  if (diffMin < 60) return `${diffMin}m`
  const diffH = Math.floor(diffMs / 3600000)
  if (diffH < 48) return `${diffH}h`
  return `${Math.floor(diffH / 24)}d`
}

function expiryChipClass(secret) {
  const expiresAt = secret.refresh?.expires_at
  if (!expiresAt) return ''
  const diffMs = new Date(expiresAt) - new Date()
  if (diffMs < 0) return 'expiry-chip--expired'
  if (diffMs < 300000) return 'expiry-chip--expired'
  if (diffMs < 3600000) return 'expiry-chip--warn'
  return 'expiry-chip--ok'
}

function expiryTitle(secret) {
  const expiresAt = secret.refresh?.expires_at
  if (!expiresAt) return ''
  return `Expires: ${new Date(expiresAt).toLocaleString()}`
}

onMounted(() => {
  secretsStore.fetchSecrets()
  secretsStore.fetchBackendStatus()
})
</script>

<style scoped>
.lib-section {
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

.backend-chip {
  padding: 1px 6px;
  border-radius: 4px;
  background: rgba(210, 153, 34, 0.15);
  font-size: 11px;
  font-family: monospace;
}

/* ── Refresh error ────────────────────────────── */
.refresh-error {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 16px;
  font-size: 12px;
  background: rgba(248, 113, 113, 0.1);
  border-bottom: 1px solid rgba(248, 113, 113, 0.3);
  color: #f87171;
  flex-shrink: 0;
}

.refresh-error-close {
  margin-left: auto;
  width: 18px;
  height: 18px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: none;
  background: none;
  color: #f87171;
  font-size: 11px;
  cursor: pointer;
  flex-shrink: 0;
}

/* ── Loading / error ──────────────────────────── */
.section-status {
  padding: 20px;
  color: var(--bs-secondary-color);
  font-size: 13px;
}

.status-spinner {
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

/* ── Section body ─────────────────────────────── */
.section-body {
  flex: 1;
  overflow-y: auto;
  padding: 16px 20px;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

/* ── Category ─────────────────────────────────── */
.category-block {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.category-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 2px 4px;
  border-bottom: 1px solid var(--bs-border-color);
}

.category-label {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--bs-secondary-color);
}

.category-add-btn {
  width: 22px;
  height: 22px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 5px;
  border: 1px solid var(--bs-border-color);
  background: none;
  color: var(--bs-secondary-color);
  font-size: 16px;
  line-height: 1;
  cursor: pointer;
  transition: background 0.12s, color 0.12s, border-color 0.12s;
}

.category-add-btn:hover:not(:disabled) {
  background: #06b6d4;
  border-color: #06b6d4;
  color: #fff;
}

/* ── Rows ─────────────────────────────────────── */
.empty-state {
  padding: 14px 12px;
  text-align: center;
  color: var(--bs-tertiary-color);
  font-size: 12px;
  background: var(--bs-tertiary-bg);
  border-radius: 7px;
  border: 1px dashed var(--bs-border-color);
}

.secret-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 9px 12px;
  border-radius: 7px;
  border: 1px solid var(--bs-border-color);
  cursor: pointer;
  background: var(--bs-body-bg);
  transition: background 0.12s, border-color 0.12s;
}

.secret-row:hover {
  background: var(--bs-tertiary-bg);
  border-color: #06b6d4;
}

.secret-row:hover .row-delete-btn {
  opacity: 1;
}

.secret-row:focus-visible {
  outline: 2px solid #06b6d4;
  outline-offset: 1px;
}

.secret-row--confirm {
  border-color: rgba(248, 113, 113, 0.5);
  background: rgba(248, 113, 113, 0.06);
  cursor: default;
}

.secret-row--confirm:hover {
  border-color: rgba(248, 113, 113, 0.7);
  background: rgba(248, 113, 113, 0.06);
}

.row-left {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
  flex: 1;
}

.secret-name {
  font-size: 13px;
  font-weight: 500;
  font-family: monospace;
  color: var(--bs-emphasis-color);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.row-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.meta-chip {
  font-size: 11px;
  color: var(--bs-tertiary-color);
}

.meta-chip--mono {
  font-family: monospace;
}

.expiry-chip {
  display: inline-block;
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 600;
}

.expiry-chip--ok      { background: rgba(6, 182, 212, 0.15); color: #06b6d4; }
.expiry-chip--warn    { background: rgba(210, 153, 34, 0.2); color: #d29922; }
.expiry-chip--expired { background: rgba(248, 113, 113, 0.2); color: #f87171; }

.row-right {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}

.row-chevron {
  font-size: 18px;
  color: var(--bs-tertiary-color);
  line-height: 1;
}

.row-delete-btn {
  width: 22px;
  height: 22px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 4px;
  border: none;
  background: none;
  color: var(--bs-tertiary-color);
  font-size: 12px;
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.12s, background 0.12s, color 0.12s;
}

.row-delete-btn:hover {
  background: rgba(248, 113, 113, 0.15);
  color: #f87171;
}

.row-refresh-btn {
  width: 24px;
  height: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 4px;
  border: 1px solid var(--bs-border-color);
  background: none;
  color: var(--bs-secondary-color);
  font-size: 14px;
  cursor: pointer;
  transition: background 0.12s, border-color 0.12s, color 0.12s;
}

.row-refresh-btn:hover:not(:disabled) {
  background: rgba(6, 182, 212, 0.1);
  border-color: #06b6d4;
  color: #06b6d4;
}

.row-refresh-btn:disabled {
  opacity: 0.5;
  cursor: default;
}

.refresh-spinner {
  display: inline-block;
  animation: spin 0.8s linear infinite;
}

/* ── Inline confirm ────────────────────────────── */
.confirm-text {
  font-size: 12px;
  color: var(--bs-secondary-color);
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
}

.confirm-btns {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
}

.btn-confirm-delete,
.btn-confirm-cancel {
  padding: 3px 10px;
  border-radius: 5px;
  font-size: 12px;
  cursor: pointer;
  border: 1px solid;
  transition: background 0.12s, border-color 0.12s, color 0.12s;
}

.btn-confirm-delete {
  background: rgba(248, 113, 113, 0.15);
  border-color: rgba(248, 113, 113, 0.4);
  color: #f87171;
}

.btn-confirm-delete:hover:not(:disabled) {
  background: #f87171;
  border-color: #f87171;
  color: #fff;
}

.btn-confirm-delete:disabled {
  opacity: 0.5;
  cursor: default;
}

.btn-confirm-cancel {
  background: none;
  border-color: var(--bs-border-color);
  color: var(--bs-secondary-color);
}

.btn-confirm-cancel:hover {
  background: var(--bs-secondary-bg);
  color: var(--bs-emphasis-color);
}

@keyframes spin {
  to { transform: rotate(360deg); }
}
</style>
