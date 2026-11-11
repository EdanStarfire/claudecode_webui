<template>
  <div class="health" data-testid="inbound-health">
    <div class="fact" data-testid="health-connection">
      <span class="fact-k">Connection</span>
      <span class="fact-v">
        <span class="chip" :class="chipClass" :title="connectionTitle" data-testid="health-status">{{ statusLabel }}</span>
        <span v-if="since" class="soft" :title="sinceTitle" data-testid="health-since">{{ since }}</span>
        <span v-if="health.inflight" class="soft" data-testid="health-inflight">{{ health.inflight }} in flight</span>
      </span>
    </div>
    <div class="fact" data-testid="health-monitors">
      <span class="fact-k">Monitors</span>
      <span class="fact-v" :title="monitorsTitle">{{ monitorsLine(health) }}</span>
    </div>
    <div class="fact" data-testid="health-window">
      <span class="fact-k">Last 24 h</span>
      <span class="fact-v ellipsis" :title="windowTitle">{{ windowLine(health) }}</span>
    </div>
    <div v-if="health.attention?.length" class="tags" data-testid="health-attention">
      <span v-for="item in health.attention" :key="item.code" class="attn" :title="attentionHint(item)" :data-code="item.code" data-testid="health-tag">
        <RelayTag :tone="item.severity === 'attention' ? 'amber' : 'neutral'">{{ attentionLabel(item) }}</RelayTag>
        <router-link class="audit-link" :to="{ path: '/settings/relays', query: auditQuery(item) }" title="Show the matching audit rows" data-testid="health-audit-link">audit →</router-link>
      </span>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import RelayTag from './RelayTag.vue'
import {
  STATUS_LABELS, attentionHint, attentionLabel, auditQuery, fullTime, monitorsLine, sinceText, windowLine,
} from '@/utils/relayInbound'

const props = defineProps({
  health: { type: Object, required: true },
  nowSec: { type: Number, default: () => Date.now() / 1000 },
})

const statusLabel = computed(() => STATUS_LABELS[props.health.status] || props.health.status)
const chipClass = computed(() => ({
  'chip--ok': props.health.status === 'connected',
  'chip--bad': props.health.status === 'auth_failed',
  'chip--missing': props.health.status === 'transport_unavailable',
  'chip--muted': ['disabled', 'stopped'].includes(props.health.status),
}))
const since = computed(() => sinceText(props.health.status_since, props.nowSec))
const sinceTitle = computed(() => `${fullTime(props.health.status_since)}. After a Backend restart this is read from the audit when possible, otherwise it counts from the restart.`)
const connectionTitle = computed(() => [props.health.detail && `detail: ${props.health.detail}`, 'In-flight count and connection time are since the last restart.'].filter(Boolean).join('. '))
const monitorsTitle = computed(() => {
  const r = props.health.monitors?.by_reason || {}
  return Object.entries(r).map(([k, n]) => `${k.replace(/_/g, ' ')}: ${n}`).join('\n') || 'Monitors on this relay'
})
const windowTitle = computed(() => {
  const lb = props.health.last_backfill
  const aud = props.health.last_backfill_audit
  const lines = ['Counted from the audit log, so they survive a restart.']
  if (lb) lines.push(`Last catch-up since restart: ${fullTime(lb.at)}${lb.gap ? ' (incomplete)' : ''}.`)
  if (aud) lines.push(`Last catch-up in the audit: ${fullTime(aud.ts)}${aud.code === 'gap_notice' ? ' (incomplete)' : ''}.`)
  return lines.join('\n')
})
</script>

<style scoped>
.health { display: flex; flex-direction: column; gap: 4px; font-size: 12px; }
.fact { display: flex; gap: 10px; align-items: baseline; min-width: 0; }
.fact-k { flex: 0 0 84px; color: var(--bs-secondary-color); font-weight: 500; }
.fact-v { flex: 1 1 auto; min-width: 0; display: inline-flex; gap: 8px; align-items: baseline; }
.ellipsis { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.soft { color: var(--bs-secondary-color); }
.chip { font-size: 11px; padding: 1px 8px; border-radius: 10px; border: 1px solid var(--bs-border-color); }
.chip--ok { color: #3fb950; border-color: #3fb95066; }
.chip--missing { color: #d29922; border-color: #d2992266; }
.chip--bad { color: #f87171; border-color: #f8717166; }
.chip--muted { color: var(--bs-secondary-color); }
.tags { display: flex; flex-wrap: wrap; gap: 4px 10px; margin-top: 2px; }
.attn { display: inline-flex; gap: 4px; align-items: baseline; }
.audit-link { font-size: 10px; color: var(--bs-secondary-color); text-decoration: none; }
.audit-link:hover { color: var(--bs-body-color); text-decoration: underline; }
@media (max-width: 600px) {
  .fact { flex-wrap: wrap; gap: 0 10px; }
  .fact-k { flex-basis: 100%; }
  .ellipsis { white-space: normal; }
}
</style>
