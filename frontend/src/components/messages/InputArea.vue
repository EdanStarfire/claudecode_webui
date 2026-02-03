<template>
  <div class="border-top bg-light">
    <!-- Connection warning banner -->
    <div
      v-if="!isConnected"
      class="alert alert-danger mb-0 py-2 px-3 small d-flex align-items-center gap-2"
      role="alert"
    >
      <span class="flex-shrink-0">❗</span>
      <strong>Disconnected</strong>
      <span class="text-muted">—</span>
      <span>Cannot send messages while reconnecting...</span>
    </div>

    <!-- Attachment list -->
    <AttachmentList
      :attachments="attachments"
      @remove="removeAttachment"
    />

    <!-- Drop zone overlay -->
    <div
      v-if="isDragging"
      class="drop-zone-overlay"
      @dragover.prevent
      @dragleave="isDragging = false"
      @drop.prevent="handleDrop"
    >
      <div class="drop-zone-content">
        <span class="drop-icon">📎</span>
        <span>Drop files here</span>
      </div>
    </div>

    <div
      class="d-flex gap-2 align-items-end px-2 py-1 input-container"
      @dragover.prevent="isDragging = true"
      @dragenter.prevent="isDragging = true"
    >
      <!-- Hidden file input -->
      <input
        ref="fileInput"
        type="file"
        multiple
        class="d-none"
        @change="handleFileSelect"
      />

      <!-- File picker button -->
      <button
        type="button"
        class="btn btn-outline-secondary btn-sm file-picker-btn"
        title="Attach files"
        :disabled="isStarting || !isConnected"
        @click="openFilePicker"
      >
        📎
      </button>

      <textarea
        id="message-input"
        ref="messageTextarea"
        v-model="inputText"
        class="form-control"
        :placeholder="inputPlaceholder"
        :disabled="isStarting || !isConnected"
        rows="1"
        @input="autoResizeTextarea"
        @keydown="handleKeyPress"
        @paste="handlePaste"
      ></textarea>

      <button
        v-if="isProcessing"
        class="btn btn-warning"
        title="Stop current processing"
        @click="interruptSession"
      >
        Stop
      </button>

      <button
        v-else
        class="btn btn-primary"
        :disabled="(!inputText.trim() && attachments.length === 0) || !isConnected || isStarting || isUploading"
        @click="sendMessage"
      >
        {{ isUploading ? 'Uploading...' : 'Send' }}
      </button>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useSessionStore } from '@/stores/session'
import { useWebSocketStore } from '@/stores/websocket'
import AttachmentList from './AttachmentList.vue'

const sessionStore = useSessionStore()
const wsStore = useWebSocketStore()

const messageTextarea = ref(null)
const fileInput = ref(null)
const windowWidth = ref(window.innerWidth)

// File upload state
const attachments = ref([])
const isDragging = ref(false)
const isUploading = ref(false)

// Maximum file size: 5MB
const MAX_FILE_SIZE = 5 * 1024 * 1024

const inputText = computed({
  get: () => sessionStore.currentInput,
  set: (value) => { sessionStore.currentInput = value }
})

const isProcessing = computed(() => sessionStore.currentSession?.is_processing || false)
const isConnected = computed(() => wsStore.sessionConnected)
const isStarting = computed(() => sessionStore.currentSession?.state === 'starting')
const currentSessionId = computed(() => sessionStore.currentSessionId)

// Mobile detection based on viewport width
const isMobile = computed(() => windowWidth.value < 768)

const inputPlaceholder = computed(() => {
  if (isStarting.value) {
    return 'Session is starting...'
  }
  if (!isConnected.value) {
    return 'Waiting for connection...'
  }
  return 'Type your message to Claude Code...'
})

// Update window width on resize
function handleResize() {
  windowWidth.value = window.innerWidth
}

onMounted(() => {
  window.addEventListener('resize', handleResize)
})

onUnmounted(() => {
  window.removeEventListener('resize', handleResize)
})

/**
 * Handle Enter key press with mobile-specific behavior
 * Mobile (< 768px): Enter creates new line, Shift+Enter also creates new line
 * Desktop (>= 768px): Enter sends message, Shift+Enter creates new line
 */
function handleKeyPress(event) {
  if (event.key === 'Enter' && !event.shiftKey) {
    if (isMobile.value) {
      // Mobile: allow new line (do nothing, default behavior)
      return
    } else {
      // Desktop: send message
      event.preventDefault()
      sendMessage()
    }
  }
}

/**
 * Auto-resize textarea based on content (matching CommComposer behavior)
 */
function autoResizeTextarea() {
  const textarea = messageTextarea.value
  if (!textarea) return

  // Reset height to auto to get the correct scrollHeight
  textarea.style.height = 'auto'

  // Set height based on scrollHeight, respecting min and max
  const newHeight = Math.min(textarea.scrollHeight, parseInt(getComputedStyle(textarea).maxHeight))
  textarea.style.height = newHeight + 'px'
}

/**
 * Open file picker dialog
 */
function openFilePicker() {
  fileInput.value?.click()
}

/**
 * Handle files selected via file picker
 */
function handleFileSelect(event) {
  const files = Array.from(event.target.files || [])
  addFiles(files)
  // Reset input so same file can be selected again
  event.target.value = ''
}

/**
 * Handle files dropped on the input area
 */
function handleDrop(event) {
  isDragging.value = false
  const files = Array.from(event.dataTransfer?.files || [])
  addFiles(files)
}

/**
 * Handle paste event for clipboard images
 */
function handlePaste(event) {
  const items = event.clipboardData?.items
  if (!items) return

  const imageFiles = []
  for (const item of items) {
    if (item.type.startsWith('image/')) {
      const file = item.getAsFile()
      if (file) {
        // Generate a name for clipboard images
        const ext = item.type.split('/')[1] || 'png'
        const name = `clipboard-${Date.now()}.${ext}`
        // Create a new file with the generated name
        const namedFile = new File([file], name, { type: file.type })
        imageFiles.push(namedFile)
      }
    }
  }

  if (imageFiles.length > 0) {
    event.preventDefault()
    addFiles(imageFiles)
  }
}

/**
 * Add files to attachments list with validation
 */
function addFiles(files) {
  for (const file of files) {
    // Check file size
    if (file.size > MAX_FILE_SIZE) {
      // Add with error state
      attachments.value.push({
        id: generateId(),
        name: file.name,
        size: file.size,
        type: file.type,
        file: file,
        error: `File too large (max ${MAX_FILE_SIZE / (1024 * 1024)}MB)`
      })
      continue
    }

    // Create preview for images
    let preview = null
    if (file.type.startsWith('image/')) {
      preview = URL.createObjectURL(file)
    }

    attachments.value.push({
      id: generateId(),
      name: file.name,
      size: file.size,
      type: file.type,
      file: file,
      preview: preview,
      uploading: false,
      progress: 0,
      uploaded: false,
      uploadedInfo: null,
      error: null
    })
  }
}

/**
 * Remove attachment at index
 */
function removeAttachment(index) {
  const attachment = attachments.value[index]
  // Revoke object URL if it was created
  if (attachment.preview) {
    URL.revokeObjectURL(attachment.preview)
  }
  attachments.value.splice(index, 1)
}

/**
 * Generate unique ID for attachment
 */
function generateId() {
  return `${Date.now()}-${Math.random().toString(36).substr(2, 9)}`
}

/**
 * Upload a single file to the backend
 */
async function uploadFile(attachment) {
  const sessionId = currentSessionId.value
  if (!sessionId) return null

  attachment.uploading = true
  attachment.progress = 0
  attachment.error = null

  try {
    const formData = new FormData()
    formData.append('file', attachment.file)

    const response = await fetch(`/api/sessions/${sessionId}/files`, {
      method: 'POST',
      body: formData
    })

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}))
      throw new Error(errorData.detail || `Upload failed: ${response.status}`)
    }

    const result = await response.json()
    attachment.uploaded = true
    attachment.uploadedInfo = result.file
    attachment.progress = 100
    return result.file
  } catch (error) {
    attachment.error = error.message
    return null
  } finally {
    attachment.uploading = false
  }
}

/**
 * Upload all pending files
 */
async function uploadAllFiles() {
  const pendingFiles = attachments.value.filter(a => !a.uploaded && !a.error)
  if (pendingFiles.length === 0) return true

  isUploading.value = true

  try {
    const results = await Promise.all(pendingFiles.map(uploadFile))
    // Check if any uploads failed
    return results.every(r => r !== null)
  } finally {
    isUploading.value = false
  }
}

/**
 * Send message with attachments
 */
async function sendMessage() {
  const hasText = inputText.value.trim()
  const hasAttachments = attachments.value.filter(a => !a.error).length > 0

  if (!hasText && !hasAttachments) return

  // Upload any pending files first
  if (hasAttachments) {
    const uploadSuccess = await uploadAllFiles()
    if (!uploadSuccess) {
      // Some uploads failed - don't send the message
      return
    }
  }

  // Build message with file references
  let messageContent = inputText.value

  // Get successfully uploaded files
  const uploadedFiles = attachments.value
    .filter(a => a.uploaded && a.uploadedInfo)
    .map(a => a.uploadedInfo)

  if (uploadedFiles.length > 0) {
    // Append file paths to message for Claude to read
    const fileSection = uploadedFiles.map(f => {
      const sizeKB = (f.size_bytes / 1024).toFixed(1)
      return `- ${f.original_name} (${sizeKB} KB): ${f.stored_path}`
    }).join('\n')

    if (messageContent.trim()) {
      messageContent = `${messageContent}\n\n---\nAttached files (use Read tool to access):\n${fileSection}`
    } else {
      messageContent = `Please analyze these attached files:\n\n---\nAttached files (use Read tool to access):\n${fileSection}`
    }
  }

  // Send via WebSocket
  wsStore.sendMessage(messageContent)

  // Clear input and attachments
  inputText.value = ''
  clearAttachments()

  // Reset textarea height after sending
  if (messageTextarea.value) {
    messageTextarea.value.style.height = 'auto'
  }
}

/**
 * Clear all attachments
 */
function clearAttachments() {
  // Revoke all object URLs
  for (const attachment of attachments.value) {
    if (attachment.preview) {
      URL.revokeObjectURL(attachment.preview)
    }
  }
  attachments.value = []
}

function interruptSession() {
  wsStore.interruptSession()
}
</script>

<style scoped>
textarea {
  resize: vertical;
  min-height: calc(1.5em + 0.5rem + 2px); /* 1 row height */
  max-height: calc(9em + 0.5rem + 2px); /* 6 rows height */
}

.input-container {
  position: relative;
}

.file-picker-btn {
  padding: 0.375rem 0.5rem;
  font-size: 1rem;
  line-height: 1;
  flex-shrink: 0;
}

.drop-zone-overlay {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(var(--bs-primary-rgb), 0.1);
  border: 2px dashed var(--bs-primary);
  border-radius: 4px;
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 10;
}

.drop-zone-content {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.5rem;
  color: var(--bs-primary);
  font-weight: 500;
}

.drop-icon {
  font-size: 2rem;
}
</style>
