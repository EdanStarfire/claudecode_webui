<template>
  <div
    ref="modalElement"
    class="modal fade"
    tabindex="-1"
    aria-labelledby="globalConfigModalLabel"
    aria-hidden="true"
  >
    <div class="modal-dialog modal-dialog-centered">
      <div class="modal-content">
        <div class="modal-header">
          <h5 id="globalConfigModalLabel" class="modal-title">Application Settings</h5>
          <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
        </div>
        <div class="modal-body">
          <div v-if="loading" class="text-center py-3">
            <div class="spinner-border spinner-border-sm" role="status"></div>
            <span class="ms-2">Loading configuration...</span>
          </div>
          <div v-else-if="loadError" class="alert alert-danger">
            Failed to load configuration: {{ loadError }}
          </div>
          <div v-else>
            <!-- Tab navigation -->
            <ul class="nav nav-tabs mb-3">
              <li class="nav-item">
                <button
                  class="nav-link"
                  :class="{ active: activeTab === 'features' }"
                  @click="activeTab = 'features'"
                >
                  Features
                </button>
              </li>
              <li class="nav-item">
                <button
                  class="nav-link"
                  :class="{ active: activeTab === 'notifications' }"
                  @click="activeTab = 'notifications'"
                >
                  Notifications
                </button>
              </li>
              <li class="nav-item">
                <button
                  class="nav-link"
                  :class="{ active: activeTab === 'readaloud' }"
                  @click="activeTab = 'readaloud'"
                >
                  Read Aloud
                </button>
              </li>
            </ul>

            <!-- Tab content -->
            <div v-if="activeTab === 'features'">
              <FeaturesTab :config="config.features" @update:config="onFeaturesUpdate" />

              <hr class="my-3">

              <h6 class="mb-2">
                Templates
                <span class="badge bg-secondary ms-1">{{ templateCount }}</span>
              </h6>
              <p class="text-muted small mb-3">
                Manage minion templates &mdash; create, edit, and delete templates
                without starting a session.
              </p>
              <button class="btn btn-outline-primary btn-sm" @click="openTemplateManager">
                Manage Templates
              </button>
            </div>
            <div v-else-if="activeTab === 'notifications'">
              <NotificationsTab :config="notificationConfig" @update:config="onNotificationsUpdate" />
            </div>
            <div v-else-if="activeTab === 'readaloud'">
              <ReadAloudTab :config="readAloudConfig" @update:config="onReadAloudUpdate" />
            </div>
          </div>
        </div>
        <div class="modal-footer">
          <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button>
          <button
            type="button"
            class="btn btn-primary"
            @click="saveConfig"
            :disabled="!dirty || saving || loading"
          >
            <span v-if="saving">
              <span class="spinner-border spinner-border-sm" role="status"></span>
              Saving...
            </span>
            <span v-else>Save</span>
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { useUIStore } from '@/stores/ui'
import { apiGet, apiPut } from '@/utils/api'
import { getSettings as getNotificationSettings, updateSettings as saveNotificationSettings } from '@/composables/useNotifications'
import FeaturesTab from './FeaturesTab.vue'
import NotificationsTab from './NotificationsTab.vue'
import ReadAloudTab from './ReadAloudTab.vue'
import { getReadAloudSettings, updateReadAloudSettings } from '@/composables/useTTSReadAloud'

const uiStore = useUIStore()

const modalElement = ref(null)
let modalInstance = null

const activeTab = ref('features')
const loading = ref(false)
const loadError = ref(null)
const saving = ref(false)
const config = ref({})
const originalConfig = ref({})
const templateCount = ref(0)
const notificationConfig = ref({})
const originalNotificationConfig = ref({})
const readAloudConfig = ref({})
const originalReadAloudConfig = ref({})

const dirty = computed(() => {
  return JSON.stringify(config.value) !== JSON.stringify(originalConfig.value) ||
    JSON.stringify(notificationConfig.value) !== JSON.stringify(originalNotificationConfig.value) ||
    JSON.stringify(readAloudConfig.value) !== JSON.stringify(originalReadAloudConfig.value)
})

function onFeaturesUpdate(features) {
  config.value = { ...config.value, features }
}

function onNotificationsUpdate(notifications) {
  notificationConfig.value = notifications
}

function onReadAloudUpdate(settings) {
  readAloudConfig.value = settings
}

async function loadConfig() {
  loading.value = true
  loadError.value = null
  try {
    const data = await apiGet('/api/config')
    config.value = data.config
    originalConfig.value = JSON.parse(JSON.stringify(data.config))
    notificationConfig.value = getNotificationSettings()
    originalNotificationConfig.value = JSON.parse(JSON.stringify(notificationConfig.value))
    readAloudConfig.value = getReadAloudSettings()
    originalReadAloudConfig.value = JSON.parse(JSON.stringify(readAloudConfig.value))
    try {
      const templates = await apiGet('/api/templates')
      templateCount.value = templates.length
    } catch {
      templateCount.value = 0
    }
  } catch (e) {
    loadError.value = e.message || 'Unknown error'
  } finally {
    loading.value = false
  }
}

async function saveConfig() {
  saving.value = true
  try {
    const data = await apiPut('/api/config', config.value)
    config.value = data.config
    originalConfig.value = JSON.parse(JSON.stringify(data.config))
    // Persist notification settings to localStorage
    saveNotificationSettings(notificationConfig.value)
    originalNotificationConfig.value = JSON.parse(JSON.stringify(notificationConfig.value))
    // Persist read aloud settings to localStorage
    updateReadAloudSettings(readAloudConfig.value)
    originalReadAloudConfig.value = JSON.parse(JSON.stringify(readAloudConfig.value))
    if (modalInstance) {
      modalInstance.hide()
    }
  } catch (e) {
    loadError.value = e.message || 'Failed to save'
  } finally {
    saving.value = false
  }
}

function openTemplateManager() {
  if (modalInstance) {
    modalInstance.hide()
  }
  uiStore.showModal('configuration', { mode: 'template-list' })
}

function resetState() {
  activeTab.value = 'features'
  config.value = {}
  originalConfig.value = {}
  notificationConfig.value = {}
  originalNotificationConfig.value = {}
  readAloudConfig.value = {}
  originalReadAloudConfig.value = {}
  loadError.value = null
  saving.value = false
}

function onModalHidden() {
  resetState()
  uiStore.hideModal()
}

watch(
  () => uiStore.currentModal,
  (modal) => {
    if (modal?.name === 'global-config' && modalInstance) {
      resetState()
      modalInstance.show()
      loadConfig()
    }
  }
)

onMounted(() => {
  if (modalElement.value) {
    import('bootstrap/js/dist/modal').then(({ default: Modal }) => {
      modalInstance = new Modal(modalElement.value)
      modalElement.value.addEventListener('hidden.bs.modal', onModalHidden)
    })
  }
})

onUnmounted(() => {
  if (modalElement.value) {
    modalElement.value.removeEventListener('hidden.bs.modal', onModalHidden)
  }
  if (modalInstance) {
    modalInstance.dispose()
  }
})
</script>
