<template>
  <form class="token-form" autocomplete="off" @submit.prevent="submit">
    <div class="token-field">
      <label :for="`${uid}-user`">User OAuth token</label>
      <input
        :id="`${uid}-user`"
        v-model="userToken"
        type="password"
        autocomplete="off"
        spellcheck="false"
        placeholder="xoxp-…"
        class="form-control form-control-sm token-input"
        data-testid="relay-user-token"
      />
      <small v-if="userHint" class="token-hint" data-testid="relay-user-hint">{{ userHint }}</small>
    </div>
    <div class="token-field">
      <label :for="`${uid}-app`">App-level token <span class="optional">(inbound stage, optional)</span></label>
      <input
        :id="`${uid}-app`"
        v-model="appToken"
        type="password"
        autocomplete="off"
        spellcheck="false"
        placeholder="xapp-…"
        class="form-control form-control-sm token-input"
        data-testid="relay-app-token"
      />
      <small v-if="appHint" class="token-hint" data-testid="relay-app-hint">{{ appHint }}</small>
    </div>
    <div v-if="error" class="token-error" role="alert" data-testid="relay-token-error">{{ error }}</div>
    <div class="token-actions">
      <button type="submit" class="btn btn-sm btn-primary" :disabled="!canSubmit || saving">
        {{ saving ? 'Saving…' : 'Save tokens' }}
      </button>
      <small class="text-muted">Either token can be saved on its own. Values are never shown again.</small>
    </div>
  </form>
</template>

<script setup>
import { computed, ref } from 'vue'

defineProps({
  saving: { type: Boolean, default: false },
  error: { type: String, default: '' },
})

// Emits the typed values once and clears the inputs immediately, success or failure:
// the component never keeps a token after submit and the parent never stores one.
const emit = defineEmits(['submit'])

const uid = `relay-tok-${Math.random().toString(36).slice(2, 8)}`
const userToken = ref('')
const appToken = ref('')

const userHint = computed(() =>
  userToken.value && !userToken.value.startsWith('xoxp-')
    ? 'A user token starts with xoxp-' : '')
const appHint = computed(() =>
  appToken.value && !appToken.value.startsWith('xapp-')
    ? 'An app-level token starts with xapp-' : '')
const canSubmit = computed(() =>
  Boolean(userToken.value || appToken.value) && !userHint.value && !appHint.value)

function submit() {
  if (!canSubmit.value) return
  const payload = { userToken: userToken.value, appToken: appToken.value }
  userToken.value = ''
  appToken.value = ''
  emit('submit', payload)
}
</script>

<style scoped>
.token-form { display: flex; flex-direction: column; gap: 12px; }
.token-field { display: flex; flex-direction: column; gap: 4px; }
.token-field label { font-size: 12px; font-weight: 600; color: var(--bs-emphasis-color); }
.optional { font-weight: 400; color: var(--bs-secondary-color); }
.token-input { font-family: monospace; }
.token-hint { color: #d29922; font-size: 11px; }
.token-error {
  color: #f87171; font-size: 12px; padding: 6px 10px; border-radius: 6px;
  background: rgba(248, 113, 113, 0.08); border: 1px solid rgba(248, 113, 113, 0.25);
}
.token-actions { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
</style>
