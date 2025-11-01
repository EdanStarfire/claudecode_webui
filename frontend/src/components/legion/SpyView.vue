<template>
  <div class="spy-view d-flex flex-column overflow-hidden">
    <!--
      Spy mode is just viewing a single minion session.
      We reuse the SessionView component directly.
    -->
    <SessionView :session-id="minionId" :is-spy-mode="true" />
  </div>
</template>

<script setup>
import { onMounted, onBeforeUnmount } from 'vue'
import { useWebSocketStore } from '@/stores/websocket'
import SessionView from '../session/SessionView.vue'

const props = defineProps({
  legionId: {
    type: String,
    required: true
  },
  minionId: {
    type: String,
    required: true
  }
})

const wsStore = useWebSocketStore()

// Connect to Legion WebSocket to receive broad legion-wide updates
// (minion creation, state changes, etc.)
onMounted(() => {
  wsStore.connectLegion(props.legionId)
})

onBeforeUnmount(() => {
  wsStore.disconnectLegion()
})
</script>

<style scoped>
.spy-view {
  height: 100%;
  min-height: 0; /* Critical for flex children to shrink */
}
</style>
