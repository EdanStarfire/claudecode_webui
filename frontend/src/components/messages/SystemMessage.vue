<template>
  <div class="card system-message bg-info bg-opacity-10 border-info">
    <div class="card-body py-2 px-3">
      <div class="d-flex align-items-center gap-2">
        <i class="bi bi-info-circle text-info"></i>
        <small class="text-muted flex-grow-1">{{ message.content }}</small>
        <small class="text-muted">{{ formattedTimestamp }}</small>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { formatTimestamp } from '@/utils/time'

const props = defineProps({
  message: {
    type: Object,
    required: true
  }
})

const formattedTimestamp = computed(() => {
  return formatTimestamp(props.message.timestamp)
})
</script>

<style scoped>
.system-message {
  max-width: 100%;
  font-size: 0.9rem;
}

.system-message .card-body {
  padding: 0.5rem 0.75rem;
}
</style>
