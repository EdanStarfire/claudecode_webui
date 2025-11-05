<template>
  <div
    class="modal fade folder-browser-modal"
    id="folderBrowserModal"
    tabindex="-1"
    aria-labelledby="folderBrowserModalLabel"
    aria-hidden="true"
    ref="modalElement"
  >
    <div class="modal-dialog modal-lg">
      <div class="modal-content">
        <div class="modal-header">
          <h5 class="modal-title" id="folderBrowserModalLabel">Browse Folders</h5>
          <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
        </div>
        <div class="modal-body">
          <!-- Current path display -->
          <div class="mb-3">
            <label class="form-label">Current Path:</label>
            <div class="input-group">
              <input
                type="text"
                class="form-control"
                v-model="currentPath"
                @keyup.enter="loadDirectory"
                placeholder="Enter or select a directory path"
              />
              <button class="btn btn-outline-secondary" @click="loadDirectory" :disabled="isLoading">
                🔄 Refresh
              </button>
            </div>
          </div>

          <!-- Loading indicator -->
          <div v-if="isLoading" class="text-center py-4">
            <div class="spinner-border" role="status">
              <span class="visually-hidden">Loading...</span>
            </div>
          </div>

          <!-- Error message -->
          <div v-else-if="errorMessage" class="alert alert-danger" role="alert">
            {{ errorMessage }}
          </div>

          <!-- Directory listing -->
          <div v-else class="folder-list">
            <!-- Parent directory -->
            <div
              v-if="parentPath"
              class="folder-item d-flex align-items-center p-2 border-bottom"
              @click="navigateToParent"
              role="button"
            >
              <span class="me-2 text-secondary">⬆️</span>
              <span class="text-secondary">..</span>
            </div>

            <!-- Subdirectories -->
            <div
              v-for="folder in folders"
              :key="folder.path"
              class="folder-item d-flex align-items-center justify-content-between p-2 border-bottom"
              :class="{ 'selected': folder.path === selectedPath }"
              @click="selectFolder(folder)"
              @dblclick="navigateToFolder(folder)"
              role="button"
            >
              <div class="d-flex align-items-center flex-grow-1">
                <span class="me-2">📁</span>
                <span>{{ folder.name }}</span>
              </div>
              <button
                class="btn btn-sm btn-outline-primary"
                @click.stop="navigateToFolder(folder)"
              >
                Open
              </button>
            </div>

            <!-- Empty directory message -->
            <div v-if="folders.length === 0" class="text-center text-muted py-3">
              No subdirectories found
            </div>
          </div>
        </div>
        <div class="modal-footer">
          <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button>
          <button
            type="button"
            class="btn btn-primary"
            @click="confirmSelection"
            :disabled="!selectedPath"
          >
            Select "{{ formatPath(selectedPath || currentPath) }}"
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useUIStore } from '@/stores/ui'
import { api } from '@/utils/api'

const uiStore = useUIStore()

// State
const currentPath = ref('')
const selectedPath = ref('')
const folders = ref([])
const isLoading = ref(false)
const errorMessage = ref('')
const modalElement = ref(null)
let modalInstance = null
let onSelectCallback = null

// Computed
const parentPath = computed(() => {
  if (!currentPath.value) return null
  const parts = currentPath.value.split(/[\\/]/).filter(Boolean)
  if (parts.length <= 1) return null
  parts.pop()
  return parts.length > 0 ? parts.join('/') : '/'
})

// Format path for display (show last 2 segments)
function formatPath(path) {
  if (!path) return ''
  const parts = path.split(/[\\/]/).filter(Boolean)
  if (parts.length <= 2) return path
  return '.../' + parts.slice(-2).join('/')
}

// Load directory contents
async function loadDirectory() {
  isLoading.value = true
  errorMessage.value = ''
  folders.value = []
  selectedPath.value = ''

  try {
    // Backend defaults to home directory if path is empty
    const params = currentPath.value ? { path: currentPath.value } : {}
    const response = await api.get('/api/filesystem/browse', { params })

    // Update current path with the actual path from backend (in case it was empty)
    if (response.current_path) {
      currentPath.value = response.current_path
    }

    // Backend returns 'directories' not 'folders'
    folders.value = response.directories || []
  } catch (error) {
    console.error('Failed to load directory:', error)
    errorMessage.value = error.message || 'Failed to load directory'
  } finally {
    isLoading.value = false
  }
}

// Select folder (single click)
function selectFolder(folder) {
  selectedPath.value = folder.path
}

// Navigate to folder (double click or Open button)
function navigateToFolder(folder) {
  currentPath.value = folder.path
  selectedPath.value = ''
  loadDirectory()
}

// Navigate to parent directory
function navigateToParent() {
  if (parentPath.value) {
    currentPath.value = parentPath.value
    selectedPath.value = ''
    loadDirectory()
  }
}

// Confirm selection
function confirmSelection() {
  const pathToSelect = selectedPath.value || currentPath.value
  if (pathToSelect && onSelectCallback) {
    onSelectCallback(pathToSelect)
  }
  if (modalInstance) {
    modalInstance.hide()
  }
}

// Handle modal shown event
function onModalShown() {
  // Always load directory (will use home directory if currentPath is empty)
  loadDirectory()
}

// Handle modal hidden event
function onModalHidden() {
  // Reset state
  folders.value = []
  selectedPath.value = ''
  errorMessage.value = ''
  onSelectCallback = null
  uiStore.hideModal()
}

// Watch for modal show requests from UI store
watch(
  () => uiStore.currentModal,
  (modal) => {
    if (modal?.name === 'folder-browser' && modalInstance) {
      const data = modal.data || {}
      currentPath.value = data.currentPath || ''
      onSelectCallback = data.onSelect || null
      modalInstance.show()
    }
  }
)

// Initialize Bootstrap modal
onMounted(() => {
  if (modalElement.value) {
    import('bootstrap/js/dist/modal').then(({ default: Modal }) => {
      modalInstance = new Modal(modalElement.value)

      modalElement.value.addEventListener('shown.bs.modal', onModalShown)
      modalElement.value.addEventListener('hidden.bs.modal', onModalHidden)
    })
  }
})

// Cleanup
onUnmounted(() => {
  if (modalElement.value) {
    modalElement.value.removeEventListener('shown.bs.modal', onModalShown)
    modalElement.value.removeEventListener('hidden.bs.modal', onModalHidden)
  }
  if (modalInstance) {
    modalInstance.dispose()
  }
})
</script>

<style>
/* Higher z-index to appear above other modals */
.folder-browser-modal {
  z-index: 1060 !important;
}

.folder-browser-modal ~ .modal-backdrop {
  z-index: 1055 !important;
}
</style>

<style scoped>
.folder-list {
  max-height: 400px;
  overflow-y: auto;
  border: 1px solid #dee2e6;
  border-radius: 0.25rem;
}

.folder-item {
  cursor: pointer;
  transition: background-color 0.15s ease-in-out;
}

.folder-item:hover {
  background-color: #f8f9fa;
}

.folder-item.selected {
  background-color: #e7f3ff;
}
</style>
