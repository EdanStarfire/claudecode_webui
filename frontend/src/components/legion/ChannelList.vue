<template>
  <div class="accordion mt-2" id="channelsAccordion">
    <div class="accordion-item">
      <h2 class="accordion-header">
        <button
          class="accordion-button collapsed"
          type="button"
          data-bs-toggle="collapse"
          data-bs-target="#channelsCollapsePanel"
          aria-expanded="false"
          aria-controls="channelsCollapsePanel"
        >
          <span style="font-size: 1rem; margin-right: 0.5rem;">💬</span>
          <span class="fw-semibold">Channels</span>
          <small class="text-muted ms-2">({{ channelCount }})</small>
        </button>
      </h2>
      <div
        id="channelsCollapsePanel"
        class="accordion-collapse collapse"
        data-bs-parent="#channelsAccordion"
      >
        <div class="accordion-body p-0">
          <!-- Empty state -->
          <div v-if="channels.length === 0" class="text-muted text-center py-3" style="font-size: 0.875rem;">
            No channels yet
          </div>

          <!-- Channel list -->
          <div v-else class="list-group list-group-flush">
            <div
              v-for="channel in channels"
              :key="channel.channel_id"
              class="list-group-item list-group-item-action channel-item"
              @click="openChannelModal(channel.channel_id)"
            >
              <div class="d-flex align-items-center justify-content-between">
                <div class="d-flex align-items-center">
                  <span style="font-size: 0.875rem; margin-right: 0.5rem;">💬</span>
                  <span class="channel-name">{{ channel.name }}</span>
                </div>
                <span class="badge bg-secondary member-count">{{ channel.member_count || 0 }}</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted } from 'vue'
import { useLegionStore } from '@/stores/legion'

const props = defineProps({
  project: {
    type: Object,
    required: true
  }
})

const legionStore = useLegionStore()

const channels = computed(() => {
  return legionStore.channelsByLegion.get(props.project.project_id) || []
})

const channelCount = computed(() => {
  return channels.value.length
})

function openChannelModal(channelId) {
  legionStore.selectChannel(channelId)
}

// Load channels when component mounts
onMounted(async () => {
  if (props.project.is_multi_agent) {
    try {
      await legionStore.loadChannels(props.project.project_id)
    } catch (error) {
      console.error('Failed to load channels:', error)
    }
  }
})
</script>

<style scoped>
.channel-item {
  cursor: pointer;
  padding: 0.5rem 0.75rem;
}

.channel-item:hover {
  background-color: #f8f9fa;
}

.channel-name {
  font-size: 0.875rem;
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 150px;
}

.member-count {
  font-size: 0.75rem;
  min-width: 24px;
  text-align: center;
}

.accordion-button {
  font-size: 0.9rem;
  padding: 0.5rem 0.75rem;
}

.accordion-button:not(.collapsed) {
  background-color: #e7f1ff;
  color: #0d6efd;
}
</style>
