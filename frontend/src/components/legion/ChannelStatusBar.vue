<template>
  <div class="channel-status-bar d-flex gap-2">
    <!-- Info Button -->
    <button
      class="btn btn-sm btn-outline-secondary"
      type="button"
      @click="showInfo"
      title="View channel information"
    >
      <span class="button-icon">📋</span>
      <span class="button-label">Info</span>
    </button>

    <!-- Members Button -->
    <button
      class="btn btn-sm btn-outline-secondary"
      type="button"
      @click="showMembers"
      title="View channel members"
    >
      <span class="button-icon">👥</span>
      <span class="button-label">Members ({{ memberCount }})</span>
    </button>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useUIStore } from '@/stores/ui'

const props = defineProps({
  channel: {
    type: Object,
    required: true
  },
  members: {
    type: Array,
    default: () => []
  }
})

const uiStore = useUIStore()

const memberCount = computed(() => props.members?.length || 0)

function showInfo() {
  uiStore.showModal('channel-info', {
    channel: props.channel
  })
}

function showMembers() {
  uiStore.showModal('channel-members', {
    channel: props.channel,
    members: props.members
  })
}
</script>

<style scoped>
.channel-status-bar {
  background-color: #f8f9fa;
  border-top: 1px solid #dee2e6;
  padding: 0.5rem;
}

.button-icon {
  margin-right: 0.25rem;
}

/* Mobile: Hide button labels to save space */
@media (max-width: 768px) {
  .button-label {
    display: none;
  }

  .button-icon {
    margin-right: 0;
  }
}
</style>
