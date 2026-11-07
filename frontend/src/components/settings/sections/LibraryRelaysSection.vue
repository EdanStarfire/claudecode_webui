<template>
  <div class="lib-section">
    <SettingsToolbar title="Relays" />

    <div v-if="store.loading && !store.loaded" class="section-status">Loading…</div>
    <div v-else-if="store.error" class="section-error">{{ store.error }}</div>

    <div v-else class="section-body">
      <!-- Empty state: create -->
      <div v-if="!relay" class="relay-card" data-testid="relay-empty">
        <h3>Slack relay</h3>
        <p class="muted">
          A relay lets agent sessions read and post in your Slack workspace as you, using your own
          user token. One relay per Backend.
        </p>
        <form class="create-form" @submit.prevent="create">
          <input
            v-model="newName"
            class="form-control form-control-sm"
            placeholder="Relay name"
            maxlength="80"
            data-testid="relay-create-name"
          />
          <button type="submit" class="btn btn-sm btn-primary" :disabled="!newName.trim() || creating" data-testid="relay-create">
            {{ creating ? 'Creating…' : 'Create Slack relay' }}
          </button>
        </form>
        <div v-if="createError" class="msg-error" role="alert" data-testid="relay-create-error">{{ createError }}</div>
      </div>

      <template v-else>
        <!-- Detail card -->
        <div class="relay-card" data-testid="relay-detail">
          <div class="card-head">
            <h3>{{ relay.name }}</h3>
            <span class="chip">{{ relay.adapter_type }}</span>
          </div>
          <dl class="facts">
            <dt>Workspace</dt>
            <dd data-testid="relay-workspace">{{ relay.workspace_name || 'Unknown until tested' }}</dd>
            <dt>Owner user</dt>
            <dd data-testid="relay-owner">{{ relay.owner_user_id || 'Unknown until tested' }}</dd>
            <dt>Tokens</dt>
            <dd>
              <span class="chip" :class="relay.has_user_token ? 'chip--ok' : 'chip--missing'" data-testid="relay-chip-user">
                user token {{ chipState(relay.has_user_token, relay.user_secret_missing) }}
              </span>
              <span class="chip" :class="relay.has_app_token ? 'chip--ok' : 'chip--missing'" data-testid="relay-chip-app">
                app token {{ chipState(relay.has_app_token, relay.app_secret_missing) }}
              </span>
            </dd>
          </dl>

          <div class="card-actions">
            <button class="btn btn-sm btn-outline-primary" :disabled="testing || !relay.has_user_token" data-testid="relay-test" @click="runTest">
              {{ testing ? 'Testing…' : 'Test connection' }}
            </button>
            <template v-if="!confirmDelete">
              <button class="btn btn-sm btn-outline-danger" data-testid="relay-delete" @click="confirmDelete = true">Delete relay</button>
            </template>
            <template v-else>
              <span class="muted">Delete this relay and its stored tokens?</span>
              <button class="btn btn-sm btn-danger" :disabled="deleting" data-testid="relay-delete-confirm" @click="remove">Delete</button>
              <button class="btn btn-sm btn-outline-secondary" @click="confirmDelete = false">Cancel</button>
            </template>
          </div>

          <div v-if="testResult" class="test-result" :class="testResult.ok ? 'test-result--ok' : 'test-result--fail'" role="status" data-testid="relay-test-result">
            <template v-if="testResult.ok">
              Connected to <strong>{{ testResult.workspace_name }}</strong> as
              <strong>{{ testResult.user || testResult.user_id }}</strong>.
            </template>
            <template v-else>Test failed: {{ testResult.error }}</template>
          </div>
          <div v-if="actionError" class="msg-error" role="alert">{{ actionError }}</div>
        </div>

        <!-- Credentials: references to existing vault secrets -->
        <div class="relay-card">
          <h3>Credentials</h3>
          <RelaySecretPickers :relay="relay" :saving="savingSecrets" :error="secretError" @submit="saveSecrets" />
        </div>

        <!-- Setup guide -->
        <RelaySetupGuide :relay-id="relay.relay_id" :open="!relay.has_user_token" />

        <!-- OAuth -->
        <details class="relay-card" data-testid="relay-advanced">
          <summary>Advanced: authorize with OAuth</summary>
          <div v-if="!oauthAvailable" class="muted oauth-note" data-testid="relay-oauth-unavailable">
            Slack accepts OAuth redirects only to https or localhost URLs. Open the UI over https or
            on localhost to use OAuth; otherwise paste the tokens above.
          </div>
          <form v-else class="oauth-form" autocomplete="off" @submit.prevent="startOAuth" data-testid="relay-oauth">
            <label>Client ID
              <input v-model="oauthClientId" class="form-control form-control-sm" autocomplete="off" />
            </label>
            <label>Client secret
              <input v-model="oauthClientSecret" type="password" class="form-control form-control-sm" autocomplete="off" placeholder="write-only" />
            </label>
            <small class="muted">Redirect URL to register in the Slack app: <code>{{ redirectUri }}</code></small>
            <button type="submit" class="btn btn-sm btn-outline-primary" :disabled="oauthBusy || !oauthClientId" data-testid="relay-oauth-start">
              {{ oauthBusy ? 'Waiting for Slack…' : 'Authorize with Slack' }}
            </button>
            <div v-if="oauthMessage" class="muted" role="status">{{ oauthMessage }}</div>
          </form>
        </details>
      </template>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRelayStore } from '@/stores/relay'
import SettingsToolbar from '../SettingsToolbar.vue'
import RelaySecretPickers from '../../relay/RelaySecretPickers.vue'
import RelaySetupGuide from '../../relay/RelaySetupGuide.vue'

const store = useRelayStore()
const relay = computed(() => store.relay)

const newName = ref('Slack')
const creating = ref(false)
const createError = ref('')
const testing = ref(false)
const testResult = ref(null)
const actionError = ref('')
const confirmDelete = ref(false)
const deleting = ref(false)
const savingSecrets = ref(false)
const secretError = ref('')

const chipState = (has, missing) => (has ? 'set' : missing ? 'secret missing' : 'missing')

const LOCAL_HOSTS = ['localhost', '127.0.0.1', '::1', '[::1]']
const oauthAvailable = computed(() =>
  window.location.protocol === 'https:' || LOCAL_HOSTS.includes(window.location.hostname))
const redirectUri = computed(() => `${window.location.origin}/oauth/callback`)

const oauthClientId = ref('')
const oauthClientSecret = ref('')
const oauthBusy = ref(false)
const oauthMessage = ref('')
let oauthTimer = null

async function create() {
  creating.value = true
  createError.value = ''
  try {
    await store.createRelay(newName.value.trim())
  } catch (e) {
    createError.value = e.message
    await store.loadRelays() // a 409 means one already exists
  } finally {
    creating.value = false
  }
}

async function runTest() {
  testing.value = true
  actionError.value = ''
  testResult.value = null
  try {
    testResult.value = await store.testRelay(relay.value.relay_id)
  } catch (e) {
    actionError.value = e.message
  } finally {
    testing.value = false
  }
}

async function remove() {
  deleting.value = true
  actionError.value = ''
  try {
    await store.deleteRelay(relay.value.relay_id)
    testResult.value = null
    confirmDelete.value = false
  } catch (e) {
    actionError.value = e.message
  } finally {
    deleting.value = false
  }
}

async function saveSecrets(selection) {
  savingSecrets.value = true
  secretError.value = ''
  try {
    await store.saveSecrets(relay.value.relay_id, selection)
  } catch (e) {
    secretError.value = e.message
  } finally {
    savingSecrets.value = false
  }
}

async function startOAuth() {
  oauthBusy.value = true
  oauthMessage.value = ''
  clearTimeout(oauthTimer)
  const relayId = relay.value.relay_id
  const secret = oauthClientSecret.value
  oauthClientSecret.value = ''
  try {
    const patch = { oauth_client_id: oauthClientId.value }
    if (secret) patch.oauth_client_secret = secret
    await store.updateRelay(relayId, patch)
    const { auth_url: authUrl, state } = await store.oauthInitiate(relayId, redirectUri.value)
    window.open(authUrl, '_blank', 'noopener')
    pollOAuth(relayId, state, 0)
  } catch (e) {
    oauthMessage.value = e.message
    oauthBusy.value = false
  }
}

function pollOAuth(relayId, state, attempt) {
  oauthTimer = setTimeout(async () => {
    try {
      const status = await store.oauthStatus(relayId, state)
      if (status.status === 'pending' && attempt < 120) return pollOAuth(relayId, state, attempt + 1)
      oauthMessage.value = status.status === 'complete'
        ? 'Authorized.' : (status.error ? `Authorization failed: ${status.error}` : `Authorization ${status.status}.`)
      await store.loadRelays()
    } catch (e) {
      oauthMessage.value = e.message
    }
    oauthBusy.value = false
  }, 1500)
}

onMounted(() => store.loadRelays())
onBeforeUnmount(() => clearTimeout(oauthTimer))
</script>

<style scoped>
.lib-section { display: flex; flex-direction: column; height: 100%; overflow: hidden; }
.section-status, .section-error { padding: 16px 20px; font-size: 13px; }
.section-error { color: #f87171; }
.section-body { flex: 1; overflow-y: auto; padding: 16px 20px; display: flex; flex-direction: column; gap: 16px; }
.relay-card { border: 1px solid var(--bs-border-color); border-radius: 8px; padding: 14px 16px; background: var(--bs-body-bg); }
.relay-card h3 { font-size: 15px; margin: 0 0 8px; }
.card-head { display: flex; align-items: center; gap: 8px; }
.card-head h3 { margin: 0; }
.muted { color: var(--bs-secondary-color); font-size: 12px; }
.create-form { display: flex; gap: 8px; flex-wrap: wrap; max-width: 480px; }
.create-form input { flex: 1; min-width: 160px; }
.facts { display: grid; grid-template-columns: 110px 1fr; gap: 6px 12px; font-size: 13px; margin: 10px 0; }
.facts dt { color: var(--bs-secondary-color); font-weight: 500; }
.facts dd { margin: 0; word-break: break-word; }
.chip { font-size: 11px; padding: 2px 8px; border-radius: 10px; border: 1px solid var(--bs-border-color); margin-right: 6px; display: inline-block; }
.chip--ok { color: #3fb950; border-color: #3fb95066; }
.chip--missing { color: #d29922; border-color: #d2992266; }
.card-actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.test-result { margin-top: 10px; padding: 8px 12px; border-radius: 6px; font-size: 13px; }
.test-result--ok { background: rgba(63, 185, 80, 0.1); border: 1px solid rgba(63, 185, 80, 0.3); }
.test-result--fail { background: rgba(248, 113, 113, 0.08); border: 1px solid rgba(248, 113, 113, 0.25); color: #f87171; }
.msg-error { margin-top: 8px; color: #f87171; font-size: 12px; }
summary { cursor: pointer; font-weight: 600; font-size: 13px; }
.oauth-note { margin-top: 8px; line-height: 1.5; }
.oauth-form { display: flex; flex-direction: column; gap: 8px; margin-top: 10px; max-width: 460px; font-size: 12px; }
@media (max-width: 600px) {
  .section-body { padding: 12px; }
  .facts { grid-template-columns: 1fr; }
  .facts dt { margin-top: 4px; }
}
</style>
