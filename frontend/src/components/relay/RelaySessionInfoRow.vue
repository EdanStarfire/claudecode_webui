<template>
  <div v-if="summary" class="mb-3" data-testid="relay-info-row">
    <h6 class="text-muted">Relay</h6>
    <div class="info-line">
      <span data-testid="relay-info-name">{{ relayName }}</span>
      <span v-if="summary.handle" class="font-monospace" data-testid="relay-info-handle">!{{ summary.handle }}</span>
      <span class="text-muted" data-testid="relay-info-count">{{ summary.policy_count }} channel {{ summary.policy_count === 1 ? 'policy' : 'policies' }}</span>
      <button type="button" class="btn btn-sm btn-link p-0" data-testid="relay-info-policies" @click="go(`/settings/session/${sessionId}/relay`)">Policies</button>
      <button type="button" class="btn btn-sm btn-link p-0" data-testid="relay-info-audit" @click="go(`/settings/relays?session=${encodeURIComponent(sessionId)}`)">Audit</button>
    </div>
    <div v-if="notAttachedReason" class="restart-line" data-testid="relay-info-restart">
      <span>{{ notAttachedReason }}</span>
      <button type="button" class="btn btn-sm btn-link p-0" data-testid="relay-info-restart-action" @click="restart">Restart</button>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { useSessionStore } from '@/stores/session'
import { useRelayStore } from '@/stores/relay'
import { useRestartRequired } from '@/composables/useRestartRequired'

const props = defineProps({ sessionId: { type: String, required: true } })
const emit = defineEmits(['navigate'])

const router = useRouter()
const sessionStore = useSessionStore()
const relayStore = useRelayStore()
relayStore.fetchIfEmpty()

const summary = computed(() => sessionStore.getSession(props.sessionId)?.relay_summary || null)
const relayName = computed(() =>
  relayStore.relays.find(r => r.relay_id === summary.value?.relay_id)?.name || summary.value?.adapter_type || 'Relay')

const idRef = computed(() => props.sessionId)
const { reasons, openRestart } = useRestartRequired(idRef)
const notAttachedReason = computed(() => reasons.value.find(r => r.startsWith('Relay attached')) || '')

function go(path) {
  emit('navigate')
  router.push(path)
}

function restart() {
  emit('navigate')
  openRestart()
}
</script>

<style scoped>
.info-line, .restart-line { display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 12px; font-size: 14px; }
.restart-line { margin-top: 4px; font-size: 12px; color: #d29922; }
</style>
