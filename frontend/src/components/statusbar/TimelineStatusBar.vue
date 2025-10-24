<template>
  <div class="timeline-status-bar border-top p-2 bg-light">
    <div class="d-flex justify-content-end">
      <!-- Autoscroll Toggle -->
      <button
        class="btn btn-sm"
        :class="uiStore.autoScrollEnabled ? 'btn-primary' : 'btn-outline-secondary'"
        @click="toggleAutoScroll"
      >
        ⬇️ {{ uiStore.autoScrollEnabled ? 'ON' : 'OFF' }}
      </button>
    </div>
  </div>
</template>

<script setup>
import { useUIStore } from '@/stores/ui'

const props = defineProps({
  legionId: {
    type: String,
    required: true
  }
})

const uiStore = useUIStore()

function toggleAutoScroll() {
  uiStore.setAutoScroll(!uiStore.autoScrollEnabled)
}
</script>

<style scoped>
.timeline-status-bar {
  background-color: #f8f9fa;
}
</style>
