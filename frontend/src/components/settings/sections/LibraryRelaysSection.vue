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
            <dt>Inbound</dt>
            <dd>
              <span
                class="chip"
                :class="inboundChip.cls"
                :title="inboundChip.title"
                data-testid="relay-inbound-chip"
              >{{ inboundChip.label }}</span>
            </dd>
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

        <!-- Tabs: every panel stays mounted (v-show) so the credentials form and its dirty state
             survive a tab switch -->
        <div class="tabs" role="tablist" aria-label="Relay sections" data-testid="relay-tabs">
          <button
            v-for="t in TABS"
            :key="t.key"
            type="button"
            role="tab"
            class="tab"
            :class="{ on: activeTab === t.key }"
            :aria-selected="activeTab === t.key"
            :data-testid="`relay-tab-${t.key}`"
            @click="selectTab(t.key)"
          >{{ t.label }}<span v-if="t.key === 'inbound' && inboundAttention" class="dot" title="Inbound needs attention" data-testid="relay-tab-dot"></span></button>
        </div>

        <div v-show="activeTab === 'setup'" class="panel" role="tabpanel" data-testid="relay-panel-setup">
          <!-- Credentials: references to existing vault secrets -->
          <div class="relay-card">
            <h3>Credentials</h3>
            <RelaySecretPickers ref="pickers" :relay="relay" :saving="savingSecrets" :error="secretError" @submit="saveSecrets" @dirty="onDirty" />
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
        </div>

        <div v-show="activeTab === 'inbound'" class="panel" role="tabpanel" data-testid="relay-panel-inbound">
          <div class="relay-card">
            <RelayInboundTab
              :relay-id="relay.relay_id"
              :active="activeTab === 'inbound'"
              :initial-session="routeSession"
              @attention="inboundAttention = $event"
            />
          </div>
        </div>

        <div v-show="activeTab === 'audit'" class="panel" role="tabpanel" data-testid="relay-panel-audit">
          <div class="relay-card" data-testid="relay-audit-card">
            <RelayAuditView :relay-id="relay.relay_id" />
          </div>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useRelayStore } from '@/stores/relay'
import { useSettingsStore } from '@/stores/settings'
import SettingsToolbar from '../SettingsToolbar.vue'
import RelaySecretPickers from '../../relay/RelaySecretPickers.vue'
import RelaySetupGuide from '../../relay/RelaySetupGuide.vue'
import RelayAuditView from '../../relay/RelayAuditView.vue'
import RelayInboundTab from '../../relay/RelayInboundTab.vue'

const store = useRelayStore()
const route = useRoute()
const router = useRouter()
const settingsStore = useSettingsStore()
const pickers = ref(null)
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

// Tabs (`?tab=setup|inbound|audit`). A legacy deep link with only `session` / `channel` / `events`
// (the session info row's Audit link) opens Audit.
const TABS = [{ key: 'setup', label: 'Setup' }, { key: 'inbound', label: 'Inbound' }, { key: 'audit', label: 'Audit' }]
function tabFromRoute() {
  const q = route?.query || {}
  if (TABS.some(t => t.key === q.tab)) return q.tab
  return q.session || q.channel || q.events ? 'audit' : 'setup'
}
const activeTab = ref(tabFromRoute())
const inboundAttention = ref(false)
const routeSession = computed(() => (typeof route?.query?.session === 'string' ? route.query.session : ''))
function selectTab(key) {
  activeTab.value = key
  if (router && route?.query?.tab !== key) router.replace({ query: { ...route.query, tab: key } }).catch(() => {})
}
watch(() => route?.query, () => { activeTab.value = tabFromRoute() })

const INBOUND_CHIPS = {
  connected: ['connected', 'chip--ok'],
  connecting: ['connecting', ''],
  backoff: ['reconnecting', ''],
  transport_unavailable: ['unavailable', 'chip--missing'],
  auth_failed: ['auth failed', 'chip--bad'],
  disabled: ['off', 'chip--muted'],
  stopped: ['off', 'chip--muted'],
}
const ONE_BACKEND = 'Use one Backend per Slack app-level token.'
const inboundChip = computed(() => {
  const r = relay.value || {}
  const status = r.inbound_enabled === false ? 'disabled' : (r.inbound_status || 'stopped')
  const [label, cls] = INBOUND_CHIPS[status] || [status, '']
  const detail = r.inbound_detail ? ` (${r.inbound_detail})` : ''
  return { label, cls, title: `Inbound: ${label}${detail}. ${ONE_BACKEND}` }
})

const areaKey = computed(() => `relay:${relay.value?.relay_id}:inbound`)
function onDirty(dirty) {
  if (!relay.value) return
  if (dirty) settingsStore.setField(areaKey.value, 'dirty', true)
  else settingsStore.markClean(areaKey.value)
}
// Used by the settings dirty guard ("Apply"): the one Save for credentials + inbound toggle.
async function save() {
  const payload = pickers.value?.payload()
  if (payload) await saveSecrets(payload)
}
defineExpose({ save })

let statusTimer = null
function pollStatus() {
  statusTimer = setInterval(() => {
    if (relay.value && !document.hidden) store.refreshInboundStatus(relay.value.relay_id).catch(() => {})
  }, 10000)
}

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
    const { inboundEnabled, ...secretRefs } = selection
    if (secretRefs.userSecret || secretRefs.appSecret) {
      await store.saveSecrets(relay.value.relay_id, secretRefs)
    }
    if (inboundEnabled !== undefined) {
      await store.updateRelay(relay.value.relay_id, { inbound_enabled: inboundEnabled })
    }
    settingsStore.markClean(areaKey.value)
    store.refreshInboundStatus(relay.value.relay_id).catch(() => {})
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

onMounted(() => { store.loadRelays(); pollStatus() })
onBeforeUnmount(() => {
  clearTimeout(oauthTimer)
  clearInterval(statusTimer)
})
</script>

<style scoped>
.lib-section { display: flex; flex-direction: column; height: 100%; overflow: hidden; }
.section-status, .section-error { padding: 16px 20px; font-size: 13px; }
.section-error { color: var(--tool-error-text); }
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
.chip--ok { color: var(--tool-success-text); border-color: var(--tool-success-border); }
.chip--missing { color: var(--perm-amber-suggestion-strong); border-color: var(--perm-amber-suggestion-border); }
.chip--bad { color: var(--tool-error-text); border-color: var(--tool-error-border); }
.chip--muted { color: var(--bs-secondary-color); }
.tabs { display: flex; gap: 2px; overflow-x: auto; white-space: nowrap; border-bottom: 1px solid var(--bs-border-color); flex: 0 0 auto; }
.tab {
  background: none; border: 0; border-bottom: 2px solid transparent; padding: 6px 14px; font-size: 13px;
  color: var(--bs-secondary-color); cursor: pointer; flex: 0 0 auto;
}
.tab.on { color: var(--bs-body-color); border-bottom-color: var(--bs-body-color); font-weight: 600; }
.dot { display: inline-block; width: 6px; height: 6px; border-radius: 50%; background: var(--bs-warning); margin-left: 6px; vertical-align: middle; }
.panel { display: flex; flex-direction: column; gap: 16px; }
.card-actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.test-result { margin-top: 10px; padding: 8px 12px; border-radius: 6px; font-size: 13px; }
.test-result--ok { background: var(--tool-success-bg); border: 1px solid var(--tool-success-border); }
.test-result--fail { background: var(--tool-error-bg); border: 1px solid var(--tool-error-border); color: var(--tool-error-text); }
.msg-error { margin-top: 8px; color: var(--tool-error-text); font-size: 12px; }
summary { cursor: pointer; font-weight: 600; font-size: 13px; }
.oauth-note { margin-top: 8px; line-height: 1.5; }
.oauth-form { display: flex; flex-direction: column; gap: 8px; margin-top: 10px; max-width: 460px; font-size: 12px; }
@media (max-width: 600px) {
  .section-body { padding: 12px; }
  .facts { grid-template-columns: 1fr; }
  .facts dt { margin-top: 4px; }
}
</style>
