<template>
  <div class="audit-turns-tab">
    <div v-if="auditStore.turnsLoading" class="text-center py-5 text-muted">
      <div class="spinner-border mb-2"></div>
      <div>Loading turns…</div>
    </div>

    <div v-else-if="auditStore.turnsError" class="alert alert-danger m-3">
      {{ auditStore.turnsError }}
    </div>

    <div v-else-if="!auditStore.turns.length && !auditStore.standalones.length" class="text-center text-muted py-5">
      No audit data in this time range.
    </div>

    <div v-else class="turns-list p-2">
      <!-- Interleave turns and standalones by timestamp -->
      <template v-for="item in interleaved" :key="item._key">
        <TurnCard v-if="item._type === 'turn'" :turn="item" />
        <LifecycleRow v-else-if="item.event_type === 'lifecycle'" :event="item" />
        <CommRow v-else-if="item.event_type === 'comm'" :event="item" />
        <EventRow v-else :event="item" />
      </template>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useAuditStore } from '../../stores/audit.js'
import TurnCard from './TurnCard.vue'
import LifecycleRow from './LifecycleRow.vue'
import CommRow from './CommRow.vue'
import EventRow from './EventRow.vue'

const auditStore = useAuditStore()

const interleaved = computed(() => {
  const turns = auditStore.turns.map(t => ({ ...t, _type: 'turn', _ts: t.started_at, _key: `turn-${t.turn_id}` }))
  const standalones = auditStore.standalones.map(e => ({ ...e, _type: 'standalone', _ts: e.timestamp, _key: `sa-${e.id}` }))
  return [...turns, ...standalones].sort((a, b) => b._ts - a._ts)
})
</script>

<style scoped>
.turns-list { max-width: 100%; }
</style>
