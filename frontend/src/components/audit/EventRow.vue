<template>
  <div class="event-row d-flex align-items-start gap-2 py-1 px-2 rounded" :class="rowClass">
    <span class="event-dot flex-shrink-0 mt-1" :class="dotClass" title="event type"></span>
    <div class="event-body flex-grow-1 min-w-0">
      <div class="d-flex align-items-baseline gap-2 flex-wrap">
        <span class="event-type badge" :class="badgeClass">{{ event.event_type }}</span>
        <span v-if="event.tool_name" class="text-muted small">{{ event.tool_name }}</span>
        <span v-if="event.status" class="text-muted small fst-italic">{{ event.status }}</span>
        <span class="event-time text-muted small ms-auto flex-shrink-0">
          {{ formatTime(displayTs) }}
          <span v-if="isFallback" class="text-warning ms-1" title="Storage time (SDK timestamp unavailable)">*</span>
        </span>
      </div>
      <div v-if="event.summary" class="event-summary text-truncate small mt-1">{{ event.summary }}</div>
      <div v-if="event.session_name" class="text-muted" style="font-size:0.7rem">{{ event.session_name }}</div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  event: { type: Object, required: true },
})

const displayTs = computed(() => props.event.source_ts ?? props.event.timestamp)
const isFallback = computed(() => !props.event.source_ts && props.event.timestamp)

const dotClass = computed(() => {
  const s = props.event.status
  const t = props.event.event_type
  if (s === 'error') return 'bg-danger'
  if (s === 'denied' || s === 'halted') return 'bg-warning'
  if (t === 'comm') return 'bg-primary'
  if (t === 'lifecycle') return 'bg-secondary'
  if (t === 'watchdog') return 'bg-warning'
  if (t === 'permission') return 'bg-info'
  return 'bg-success'
})

const badgeClass = computed(() => {
  const t = props.event.event_type
  if (t === 'tool_call') return 'bg-success text-dark'
  if (t === 'permission') return 'bg-info text-dark'
  if (t === 'lifecycle') return 'bg-secondary'
  if (t === 'comm') return 'bg-primary'
  if (t === 'watchdog') return 'bg-warning text-dark'
  return 'bg-light text-dark'
})

const rowClass = computed(() => {
  const s = props.event.status
  if (s === 'error') return 'event-row--error'
  if (s === 'denied') return 'event-row--denied'
  return ''
})

function formatTime(ts) {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}
</script>

<style scoped>
.event-row { border-left: 3px solid transparent; }
.event-row--error { border-left-color: var(--bs-danger); }
.event-row--denied { border-left-color: var(--bs-warning); }
.event-row:hover { background: rgba(255,255,255,0.05); }
.event-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
.event-summary { color: var(--bs-secondary-color); }
</style>
