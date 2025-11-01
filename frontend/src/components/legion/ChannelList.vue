<template>
  <div class="accordion mt-2" :id="accordionId">
    <div class="accordion-item">
      <h2 class="accordion-header">
        <button
          :class="['accordion-button', { collapsed: !isExpanded }]"
          type="button"
          data-bs-toggle="collapse"
          :data-bs-target="`#${collapsePanelId}`"
          :aria-expanded="isExpanded"
          :aria-controls="collapsePanelId"
        >
          <span style="font-size: 1rem; margin-right: 0.5rem;">💬</span>
          <span class="fw-semibold">Channels</span>
          <small class="text-muted ms-2">({{ channelCount }})</small>
        </button>
      </h2>
      <div
        :id="collapsePanelId"
        :class="['accordion-collapse', 'collapse', { show: isExpanded }]"
        :data-bs-parent="`#${accordionId}`"
      >
        <div class="accordion-body p-0">
          <!-- New Channel button -->
          <div class="p-2 border-bottom">
            <button
              class="btn btn-sm btn-primary w-100"
              @click="showCreateModal"
            >
              + New Channel
            </button>
          </div>

          <!-- Empty state -->
          <div v-if="channels.length === 0" class="text-muted text-center py-3" style="font-size: 0.875rem;">
            No channels yet. Click "New Channel" to create one.
          </div>

          <!-- Channel list -->
          <div v-else class="list-group list-group-flush">
            <div
              v-for="channel in channels"
              :key="channel.channel_id"
              class="list-group-item list-group-item-action channel-item"
            >
              <div class="d-flex align-items-center justify-content-between">
                <div class="d-flex align-items-center flex-grow-1" @click="openChannelModal(channel.channel_id)">
                  <span style="font-size: 0.875rem; margin-right: 0.5rem;">💬</span>
                  <span class="channel-name">{{ channel.name }}</span>
                </div>
                <div class="d-flex align-items-center gap-2">
                  <span class="badge bg-secondary member-count">{{ channel.member_minion_ids?.length || 0 }}</span>
                  <button
                    class="btn btn-sm btn-link text-danger p-0"
                    @click.stop="confirmDelete(channel)"
                    title="Delete channel"
                    style="font-size: 1.1rem; line-height: 1;"
                  >
                    🗑️
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>

  <!-- Modals -->
  <ChannelCreateModal
    ref="createModal"
    :legion-id="project.project_id"
    @created="handleChannelCreated"
  />
  <ChannelDeleteModal
    ref="deleteModal"
    :legion-id="project.project_id"
    :channel="channelToDelete"
    @deleted="handleChannelDeleted"
  />
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useLegionStore } from '@/stores/legion'
import ChannelCreateModal from './ChannelCreateModal.vue'
import ChannelDeleteModal from './ChannelDeleteModal.vue'

const props = defineProps({
  project: {
    type: Object,
    required: true
  }
})

const router = useRouter()
const legionStore = useLegionStore()
const createModal = ref(null)
const deleteModal = ref(null)
const channelToDelete = ref(null)

// Channels expanded by default (issue #157)
const isExpanded = ref(true)

// Unique IDs for this project's accordion (prevent collisions with multiple legions)
const accordionId = computed(() => `channelsAccordion-${props.project.project_id}`)
const collapsePanelId = computed(() => `channelsCollapsePanel-${props.project.project_id}`)

const channels = computed(() => {
  return legionStore.channelsByLegion.get(props.project.project_id) || []
})

const channelCount = computed(() => {
  return channels.value.length
})

function openChannelModal(channelId) {
  // Navigate to channel view
  router.push(`/channel/${props.project.project_id}/${channelId}`)
}

function showCreateModal() {
  createModal.value?.show()
}

function confirmDelete(channel) {
  channelToDelete.value = channel
  deleteModal.value?.show()
}

function handleChannelCreated(channel) {
  // Reload channels list
  legionStore.loadChannels(props.project.project_id)
}

function handleChannelDeleted(channelId) {
  // Remove from store
  const channels = legionStore.channelsByLegion.get(props.project.project_id) || []
  const filtered = channels.filter(c => c.channel_id !== channelId)
  legionStore.channelsByLegion.set(props.project.project_id, filtered)

  // Navigate away if currently viewing the deleted channel
  if (router.currentRoute.value.path.includes(channelId)) {
    router.push(`/timeline/${props.project.project_id}`)
  }
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

  // Sync Vue state with Bootstrap collapse events (issue #157)
  const collapseElement = document.getElementById(collapsePanelId.value)
  if (collapseElement) {
    collapseElement.addEventListener('shown.bs.collapse', () => {
      isExpanded.value = true
    })
    collapseElement.addEventListener('hidden.bs.collapse', () => {
      isExpanded.value = false
    })
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
