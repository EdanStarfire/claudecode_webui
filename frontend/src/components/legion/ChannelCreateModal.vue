<template>
  <Teleport to="body">
    <div
      ref="modalElement"
      class="modal fade"
      tabindex="-1"
      aria-labelledby="createChannelModalLabel"
      aria-hidden="true"
    >
      <div class="modal-dialog modal-dialog-centered">
        <div class="modal-content">
          <div class="modal-header">
            <h5 id="createChannelModalLabel" class="modal-title">Create Channel</h5>
            <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
          </div>
          <div class="modal-body">
            <div v-if="error" class="alert alert-danger">{{ error }}</div>

            <form @submit.prevent="createChannel">
            <div class="mb-3">
              <label for="channel-name" class="form-label">
                Channel Name <span class="text-danger">*</span>
              </label>
              <input
                id="channel-name"
                v-model="formData.name"
                type="text"
                class="form-control"
                required
                maxlength="100"
                placeholder="e.g., security-team"
              />
              <div class="form-text">Short, descriptive name for the channel</div>
            </div>

            <div class="mb-3">
              <label for="channel-purpose" class="form-label">
                Purpose <span class="text-danger">*</span>
              </label>
              <textarea
                id="channel-purpose"
                v-model="formData.purpose"
                class="form-control"
                required
                maxlength="500"
                rows="3"
                placeholder="What is this channel for?"
              ></textarea>
              <div class="form-text">{{ formData.purpose.length }} / 500 characters</div>
            </div>

            <div class="mb-3">
              <label for="channel-description" class="form-label">Description (Optional)</label>
              <textarea
                id="channel-description"
                v-model="formData.description"
                class="form-control"
                maxlength="500"
                rows="2"
                placeholder="Additional details..."
              ></textarea>
            </div>

            <div class="mb-3">
              <label class="form-label">Initial Members (Optional)</label>
              <select
                v-model="formData.member_minion_ids"
                multiple
                class="form-select"
                size="5"
              >
                <option
                  v-for="minion in availableMinions"
                  :key="minion.session_id"
                  :value="minion.session_id"
                >
                  {{ minion.name }}{{ minion.role ? ` (${minion.role})` : '' }}
                </option>
              </select>
              <div class="form-text">Hold Ctrl/Cmd to select multiple minions</div>
            </div>
          </form>
        </div>
        <div class="modal-footer">
          <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button>
          <button
            type="button"
            class="btn btn-primary"
            @click="createChannel"
            :disabled="creating || !isValid"
          >
            {{ creating ? 'Creating...' : 'Create Channel' }}
          </button>
        </div>
      </div>
    </div>
    </div>
  </Teleport>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { Modal } from 'bootstrap'
import { useSessionStore } from '../../stores/session'
import { api } from '../../utils/api'

const props = defineProps({
  legionId: {
    type: String,
    required: true
  }
})

const emit = defineEmits(['created'])

const sessionStore = useSessionStore()
const modalElement = ref(null)
const modalInstance = ref(null)
const error = ref('')
const creating = ref(false)

const formData = ref({
  name: '',
  purpose: '',
  description: '',
  member_minion_ids: []
})

// Get available minions for this legion
const availableMinions = computed(() => {
  const minions = []
  for (const [id, session] of sessionStore.sessions.entries()) {
    if (session.is_minion && session.project_id === props.legionId) {
      minions.push(session)
    }
  }
  return minions
})

const isValid = computed(() => {
  return formData.value.name.trim() !== '' && formData.value.purpose.trim() !== ''
})

const createChannel = async () => {
  if (!isValid.value) {
    error.value = 'Name and purpose are required'
    return
  }

  creating.value = true
  error.value = ''

  try {
    const response = await api.post(`/api/legions/${props.legionId}/channels`, {
      name: formData.value.name.trim(),
      purpose: formData.value.purpose.trim(),
      description: formData.value.description.trim() || null,
      member_minion_ids: formData.value.member_minion_ids
    })

    emit('created', response.data.channel)
    modalInstance.value?.hide()

    // Reset form
    formData.value = {
      name: '',
      purpose: '',
      description: '',
      member_minion_ids: []
    }
  } catch (err) {
    console.error('Failed to create channel:', err)
    error.value = err.response?.data?.detail || 'Failed to create channel'
  } finally {
    creating.value = false
  }
}

const show = () => {
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

<style scoped>
.form-select[multiple] {
  min-height: 120px;
}
</style>
