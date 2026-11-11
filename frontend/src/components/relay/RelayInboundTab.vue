<template>
  <div class="inbound" data-testid="inbound-tab">
    <section>
      <h4>Health</h4>
      <div v-if="healthError" class="err" role="alert" data-testid="inbound-health-error">{{ healthError }}</div>
      <RelayInboundHealth v-if="health" :health="health" :now-sec="nowSec" />
      <div v-else-if="!healthError" class="muted">Loading…</div>
    </section>

    <section>
      <h4>Monitors</h4>
      <RelayMonitorList :relay-id="relayId" :initial-session="initialSession" :active="active" :poll-ms="pollMs" @changed="loadHealth" />
    </section>

    <section>
      <h4>Recent activity</h4>
      <RelayActivityFeed :relay-id="relayId" :active="active" />
    </section>
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRelayStore } from '@/stores/relay'
import RelayInboundHealth from './RelayInboundHealth.vue'
import RelayMonitorList from './RelayMonitorList.vue'
import RelayActivityFeed from './RelayActivityFeed.vue'
import { hasAttention } from '@/utils/relayInbound'

const props = defineProps({
  relayId: { type: String, required: true },
  active: { type: Boolean, default: true }, // the tab is visible: only then is anything polled
  initialSession: { type: String, default: '' },
  pollMs: { type: Number, default: 10000 },
})
const emit = defineEmits(['attention'])

const store = useRelayStore()
const health = ref(null)
const healthError = ref('')
const nowSec = ref(Date.now() / 1000)
let seq = 0
let timer = null
let controller = typeof AbortController !== 'undefined' ? new AbortController() : null
let inflight = false

async function loadHealth() {
  const mine = ++seq
  inflight = true
  try {
    const res = await store.loadInboundHealth(props.relayId, { signal: controller?.signal })
    if (mine !== seq) return
    health.value = res
    healthError.value = ''
    nowSec.value = Date.now() / 1000
    emit('attention', hasAttention(res))
  } catch (e) {
    if (mine === seq && e?.name !== 'AbortError') healthError.value = store.errorMessage(e)
  } finally {
    if (mine === seq) inflight = false
  }
}

function stop() {
  clearInterval(timer)
  timer = null
}
function start() {
  stop()
  timer = setInterval(() => { if (!document.hidden && !inflight) loadHealth() }, props.pollMs)
}

function onVisible() { if (!document.hidden && props.active) loadHealth() }
onMounted(() => {
  document.addEventListener('visibilitychange', onVisible)
  if (!props.active) loadHealth()
}) // one read so the tab's dot is right even while another tab is showing
watch(() => props.active, (on) => {
  if (on) { loadHealth(); start() } else stop()
}, { immediate: true })
onBeforeUnmount(() => {
  stop()
  seq++
  controller?.abort()
  document.removeEventListener('visibilitychange', onVisible)
})
defineExpose({ reload: loadHealth })
</script>

<style scoped>
.inbound { display: flex; flex-direction: column; gap: 16px; }
h4 { font-size: 12px; font-weight: 600; color: var(--bs-secondary-color); margin: 0 0 6px; text-transform: none; }
.muted { color: var(--bs-secondary-color); font-size: 12px; }
.err { color: var(--tool-error-text); font-size: 12px; }
</style>
