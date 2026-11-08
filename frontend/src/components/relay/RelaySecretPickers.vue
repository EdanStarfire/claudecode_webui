<template>
  <form class="picker-form" data-testid="relay-secret-pickers" @submit.prevent="submit">
    <p class="picker-intro">
      The relay uses existing vault secrets. Pick the secrets that hold your Slack tokens; only their
      names are shown here and values never leave the Backend.
      <router-link to="/settings/secrets" data-testid="relay-create-secret-link">Create a secret</router-link>
    </p>

    <div v-for="f in FIELDS" :key="f.key" class="picker-field">
      <label :for="`${uid}-${f.key}`">{{ f.label }} <span v-if="f.optional" class="optional">(inbound stage, optional)</span></label>
      <select
        :id="`${uid}-${f.key}`"
        v-model="selection[f.key]"
        class="form-select form-select-sm"
        :data-testid="`relay-pick-${f.key}`"
      >
        <option value="">None selected</option>
        <option
          v-for="s in options(f.key)"
          :key="s.name"
          :value="s.name"
        >{{ s.name }}</option>
        <option v-if="current[f.key] && !known(current[f.key])" :value="current[f.key]">
          {{ current[f.key] }} (missing)
        </option>
      </select>
      <small v-if="missing[f.key]" class="picker-missing" role="alert" :data-testid="`relay-missing-${f.key}`">
        Credential secret missing: '{{ current[f.key] }}' no longer exists in the vault. Pick another secret.
      </small>
      <small v-else-if="f.key === 'user' && selection.user && !current.user" class="picker-hint">
        Must hold a value starting with xoxp-.
      </small>
      <small v-else-if="f.key === 'app' && selection.app && !current.app" class="picker-hint">
        Must hold a value starting with xapp-.
      </small>
    </div>

    <div v-if="error" class="picker-error" role="alert" data-testid="relay-secret-error">{{ error }}</div>
    <div class="picker-actions">
      <button type="submit" class="btn btn-sm btn-primary" :disabled="!canSubmit || saving" data-testid="relay-secrets-save">
        {{ saving ? 'Saving…' : 'Save credentials' }}
      </button>
      <small class="text-muted">Either secret can be saved on its own.</small>
    </div>
  </form>
</template>

<script setup>
import { computed, onMounted, reactive, watch } from 'vue'
import { useSecretsStore } from '@/stores/secrets'

const props = defineProps({
  // Public relay record: only secret NAMES and booleans.
  relay: { type: Object, required: true },
  saving: { type: Boolean, default: false },
  error: { type: String, default: '' },
})
const emit = defineEmits(['submit'])

const FIELDS = [
  { key: 'user', label: 'User OAuth token secret' },
  { key: 'app', label: 'App-level token secret', optional: true },
]
const uid = `relay-pick-${Math.random().toString(36).slice(2, 8)}`
const secretsStore = useSecretsStore()

const current = computed(() => ({
  user: props.relay.user_token_secret || '',
  app: props.relay.app_token_secret || '',
}))
const missing = computed(() => ({
  user: Boolean(props.relay.user_secret_missing),
  app: Boolean(props.relay.app_secret_missing),
}))
const selection = reactive({ user: current.value.user, app: current.value.app })
watch(current, (c) => { selection.user = c.user; selection.app = c.app })

const names = computed(() => new Set(secretsStore.secrets.map(s => s.name)))
const known = (name) => names.value.has(name)
// Only secrets that can hold a pasted token are offered (keeps ssh keys etc. out of the list).
const options = () => secretsStore.secrets
  .filter(s => !['ssh_key', 'ssh'].includes(s.type))
  .slice()
  .sort((a, b) => a.name.localeCompare(b.name))

const canSubmit = computed(() =>
  (selection.user && selection.user !== current.value.user) ||
  (selection.app && selection.app !== current.value.app))

function submit() {
  if (!canSubmit.value) return
  const payload = {}
  if (selection.user && selection.user !== current.value.user) payload.userSecret = selection.user
  if (selection.app && selection.app !== current.value.app) payload.appSecret = selection.app
  emit('submit', payload)
}

onMounted(() => secretsStore.fetchSecrets())
</script>

<style scoped>
.picker-form { display: flex; flex-direction: column; gap: 12px; }
.picker-intro { font-size: 12px; color: var(--bs-secondary-color); margin: 0; line-height: 1.5; }
.picker-field { display: flex; flex-direction: column; gap: 4px; }
.picker-field label { font-size: 12px; font-weight: 600; color: var(--bs-emphasis-color); }
.optional { font-weight: 400; color: var(--bs-secondary-color); }
.picker-hint { color: var(--bs-secondary-color); font-size: 11px; }
.picker-missing { color: #d29922; font-size: 12px; }
.picker-error {
  color: #f87171; font-size: 12px; padding: 6px 10px; border-radius: 6px;
  background: rgba(248, 113, 113, 0.08); border: 1px solid rgba(248, 113, 113, 0.25);
}
.picker-actions { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
</style>
