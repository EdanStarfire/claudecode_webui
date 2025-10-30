<template>
  <div
    ref="modalElement"
    class="modal fade"
    tabindex="-1"
    aria-labelledby="deleteChannelModalLabel"
    aria-hidden="true"
  >
    <div class="modal-dialog modal-dialog-centered">
      <div class="modal-content">
        <div class="modal-header">
          <h5 id="deleteChannelModalLabel" class="modal-title">Delete Channel</h5>
          <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
        </div>
        <div class="modal-body">
          <div v-if="error" class="alert alert-danger">{{ error }}</div>

          <div class="alert alert-warning">
            <strong>⚠️ Warning:</strong> This action cannot be undone.
          </div>

          <p>Are you sure you want to delete <strong>{{ channel?.name }}</strong>?</p>
          <p class="text-muted">
            This channel has {{ memberCount }} {{ memberCount === 1 ? 'member' : 'members' }}.
            All channel communications will be permanently deleted.
          </p>
        </div>
        <div class="modal-footer">
          <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button>
          <button
            type="button"
            class="btn btn-danger"
            @click="deleteChannel"
            :disabled="deleting"
          >
            {{ deleting ? 'Deleting...' : 'Delete Channel' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { Modal } from 'bootstrap'
import { api } from '../../utils/api'

const props = defineProps({
  legionId: {
    type: String,
    required: true
  },
  channel: {
    type: Object,
    default: null
  }
})

const emit = defineEmits(['deleted'])

const modalElement = ref(null)
const modalInstance = ref(null)
const error = ref('')
const deleting = ref(false)

const memberCount = computed(() => {
  return props.channel?.member_minion_ids?.length || 0
})

const deleteChannel = async () => {
  if (!props.channel) {
    error.value = 'No channel selected'
    return
  }

  deleting.value = true
  error.value = ''

  try {
    await api.delete(`/api/legions/${props.legionId}/channels/${props.channel.channel_id}`)

    emit('deleted', props.channel.channel_id)
    modalInstance.value?.hide()
  } catch (err) {
    console.error('Failed to delete channel:', err)
    error.value = err.response?.data?.detail || 'Failed to delete channel'
  } finally {
    deleting.value = false
  }
}

const show = () => {
  error.value = ''
  modalInstance.value?.show()
}

const hide = () => {
  modalInstance.value?.hide()
}

onMounted(() => {
  if (modalElement.value) {
    modalInstance.value = new Modal(modalElement.value)
  }
})

onUnmounted(() => {
  modalInstance.value?.dispose()
})

defineExpose({
  show,
  hide
})
</script>
