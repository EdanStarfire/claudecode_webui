<template>
  <div class="comm-composer border-top p-3">
    <!-- Top Row: Recipient and Comm Type -->
    <div v-if="!hideRecipientSelector" class="d-flex gap-2 mb-2">
      <!-- Recipient Selection -->
      <div class="flex-grow-1">
        <select v-model="recipient" class="form-select form-select-sm" :disabled="sending">
          <option value="">-- Select Recipient --</option>
          <optgroup label="Minions">
            <option
              v-for="minion in minions"
              :key="minion.session_id"
              :value="`minion:${minion.session_id}`"
            >
              {{ getStateIcon(minion) }} {{ getMinionLabel(minion) }}
            </option>
          </optgroup>
        </select>
      </div>

      <!-- Comm Type -->
      <div style="min-width: 150px;">
        <select v-model="commType" class="form-select form-select-sm" :disabled="sending">
          <option value="task">Task</option>
          <option value="question">Question</option>
          <option value="info">Info</option>
          <option value="report">Report</option>
        </select>
      </div>
    </div>

    <!-- Bottom Row: Message and Send Button -->
    <div class="d-flex gap-2 align-items-end position-relative">
      <!-- Message Content with Autocomplete -->
      <div class="flex-grow-1 position-relative">
        <textarea
          ref="contentTextarea"
          v-model="content"
          class="form-control form-control-sm"
          rows="1"
          placeholder="Type message... Use # to mention minions"
          :disabled="sending"
          @input="handleAutocomplete"
          @keydown="handleKeydown"
        ></textarea>

        <!-- Autocomplete Dropdown -->
        <div
          v-if="showAutocomplete"
          ref="autocompleteDropdown"
          class="autocomplete-dropdown position-absolute bg-white border rounded shadow-sm"
        >
          <div
            v-for="(match, index) in autocompleteMatches"
            :key="match.id"
            class="autocomplete-item px-2 py-1"
            :class="{ 'bg-light': index === selectedAutocompleteIndex }"
            @click="insertMention(match)"
            @mouseenter="selectedAutocompleteIndex = index"
          >
            {{ match.type === 'minion' ? getStateIcon(match) : '#️⃣' }}
            {{ match.name }}{{ match.role ? ` (${match.role})` : '' }}
          </div>
        </div>
      </div>

      <!-- Send Button -->
      <div>
        <button
          class="btn btn-primary btn-sm"
          :disabled="!canSend || sending"
          @click="sendComm"
        >
          <span v-if="sending">
            <span class="spinner-border spinner-border-sm me-1"></span>
            Sending...
          </span>
          <span v-else>Send</span>
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { useLegionStore } from '../../stores/legion'
import { useSessionStore } from '../../stores/session'
import { useProjectStore } from '../../stores/project'

const props = defineProps({
  legionId: {
    type: String,
    required: true
  },
  hideRecipientSelector: {
    type: Boolean,
    default: false
  }
})

const legionStore = useLegionStore()
const sessionStore = useSessionStore()
const projectStore = useProjectStore()

const recipient = ref('')
const commType = ref('task')
const content = ref('')
const sending = ref(false)
const windowWidth = ref(window.innerWidth)

// Autocomplete state
const showAutocomplete = ref(false)
const autocompleteMatches = ref([])
const selectedAutocompleteIndex = ref(0)
const mentionStartPos = ref(-1)
const contentTextarea = ref(null)
const autocompleteDropdown = ref(null)

// Mobile detection based on viewport width
const isMobile = computed(() => windowWidth.value < 768)

// Get minions for this legion
const minions = computed(() => {
  const project = projectStore.projects.get(props.legionId)
  if (!project || !project.session_ids) return []

  return project.session_ids
    .map(sid => sessionStore.sessions.get(sid))
    .filter(s => s && s.is_minion)
})

// Can send if recipient and content are provided
const canSend = computed(() => {
  return recipient.value && content.value.trim().length > 0
})

/**
 * Get state icon for minion (matching sidebar minion tree)
 */
function getStateIcon(session) {
  const icons = {
    'created': '○',
    'starting': '◐',
    'active': '●',
    'paused': '⏸',
    'terminating': '◍',
    'terminated': '✗',
    'error': '⚠'
  }
  return icons[session.state] || '○'
}

/**
 * Get minion label with role (matching sidebar minion tree)
 */
function getMinionLabel(session) {
  if (session.is_minion && session.role) {
    return `${session.name} (${session.role})`
  }
  return session.name
}

/**
 * Auto-resize textarea based on content
 */
function autoResizeTextarea() {
  const textarea = contentTextarea.value
  if (!textarea) return

  // Reset height to auto to get the correct scrollHeight
  textarea.style.height = 'auto'

  // Set height based on scrollHeight, respecting min and max
  const newHeight = Math.min(textarea.scrollHeight, parseInt(getComputedStyle(textarea).maxHeight))
  textarea.style.height = newHeight + 'px'
}

/**
 * Handle autocomplete on # character
 */
function handleAutocomplete() {
  const textarea = contentTextarea.value
  if (!textarea) return

  // Auto-resize on input
  autoResizeTextarea()

  const text = content.value
  const cursorPos = textarea.selectionStart

  // Find the last # before cursor
  const textBeforeCursor = text.substring(0, cursorPos)
  const lastHashIndex = textBeforeCursor.lastIndexOf('#')

  if (lastHashIndex === -1) {
    showAutocomplete.value = false
    return
  }

  // Get the search query after #
  const searchQuery = textBeforeCursor.substring(lastHashIndex + 1).toLowerCase()

  // Check if there's a space (means we're not autocompleting anymore)
  if (searchQuery.includes(' ')) {
    showAutocomplete.value = false
    return
  }

  // Filter minions by search query
  const matches = minions.value
    .filter(minion => minion.name.toLowerCase().includes(searchQuery))
    .map(minion => ({
      type: 'minion',
      name: minion.name,
      role: minion.role,
      id: minion.session_id,
      state: minion.state
    }))

  if (matches.length === 0) {
    showAutocomplete.value = false
    return
  }

  autocompleteMatches.value = matches
  selectedAutocompleteIndex.value = 0
  mentionStartPos.value = lastHashIndex
  showAutocomplete.value = true
}

/**
 * Scroll selected autocomplete item into view within the dropdown
 */
function scrollAutocompleteItemIntoView() {
  if (!autocompleteDropdown.value) return

  const dropdown = autocompleteDropdown.value
  const selectedItem = dropdown.children[selectedAutocompleteIndex.value]
  if (!selectedItem) return

  // Calculate item position relative to dropdown scroll
  const itemOffsetTop = selectedItem.offsetTop
  const itemHeight = selectedItem.offsetHeight
  const dropdownScrollTop = dropdown.scrollTop
  const dropdownHeight = dropdown.clientHeight

  // Scroll item into view if it's outside the visible area
  if (itemOffsetTop < dropdownScrollTop) {
    // Item is above visible area - scroll up
    dropdown.scrollTop = itemOffsetTop
  } else if (itemOffsetTop + itemHeight > dropdownScrollTop + dropdownHeight) {
    // Item is below visible area - scroll down
    dropdown.scrollTop = itemOffsetTop + itemHeight - dropdownHeight
  }
}

/**
 * Handle keyboard navigation in autocomplete and mobile-specific Enter behavior
 */
function handleKeydown(event) {
  if (!showAutocomplete.value) {
    // Handle Enter to send (mobile-specific behavior)
    if (event.key === 'Enter' && !event.shiftKey) {
      if (isMobile.value) {
        // Mobile: allow new line (do nothing, default behavior)
        return
      } else {
        // Desktop: send message if valid
        if (canSend.value) {
          event.preventDefault()
          sendComm()
        }
      }
    }
    return
  }

  // Autocomplete navigation
  if (event.key === 'ArrowDown') {
    event.preventDefault()
    selectedAutocompleteIndex.value =
      (selectedAutocompleteIndex.value + 1) % autocompleteMatches.value.length
    scrollAutocompleteItemIntoView()
  } else if (event.key === 'ArrowUp') {
    event.preventDefault()
    selectedAutocompleteIndex.value =
      selectedAutocompleteIndex.value === 0
        ? autocompleteMatches.value.length - 1
        : selectedAutocompleteIndex.value - 1
    scrollAutocompleteItemIntoView()
  } else if (event.key === 'Enter') {
    event.preventDefault()
    const match = autocompleteMatches.value[selectedAutocompleteIndex.value]
    if (match) {
      insertMention(match)
    }
  } else if (event.key === 'Escape') {
    showAutocomplete.value = false
  }
}

/**
 * Insert mention at cursor position
 */
function insertMention(match) {
  const textarea = contentTextarea.value
  if (!textarea) return

  const text = content.value
  const cursorPos = textarea.selectionStart

  // Replace from # to cursor with the mention
  const newText =
    text.substring(0, mentionStartPos.value) +
    '#' +
    match.name +
    ' ' +
    text.substring(cursorPos)

  content.value = newText

  // Set cursor after the inserted mention
  const newCursorPos = mentionStartPos.value + match.name.length + 2
  setTimeout(() => {
    textarea.setSelectionRange(newCursorPos, newCursorPos)
    textarea.focus()
  }, 0)

  showAutocomplete.value = false
}

/**
 * Send comm
 */
async function sendComm() {
  if (!canSend.value || sending.value) return

  sending.value = true

  try {
    // Parse recipient
    const [recipientType, recipientId] = recipient.value.split(':')

    const commData = {
      comm_type: commType.value,
      content: content.value.trim()
    }

    // Add recipient based on type
    if (recipientType === 'minion') {
      commData.to_minion_id = recipientId
    }

    // Send via legion store
    await legionStore.sendComm(props.legionId, commData)

    // Clear form
    content.value = ''
    // Reset textarea height
    autoResizeTextarea()
    // Keep recipient and type selected for convenience
  } catch (error) {
    console.error('Failed to send comm:', error)
    alert(`Failed to send comm: ${error.message}`)
  } finally {
    sending.value = false
  }
}

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

// Close autocomplete when clicking outside
watch(showAutocomplete, (show) => {
  if (show) {
    const handler = (event) => {
      if (!event.target.closest('.comm-composer')) {
        showAutocomplete.value = false
        document.removeEventListener('click', handler)
      }
    }
    setTimeout(() => document.addEventListener('click', handler), 0)
  }
})
</script>

<style scoped>
.comm-composer {
  background-color: #fff;
}

textarea {
  resize: vertical;
  min-height: calc(1.5em + 0.5rem + 2px); /* 1 row height */
  max-height: calc(9em + 0.5rem + 2px); /* 6 rows height */
}

.autocomplete-dropdown {
  bottom: 100%;
  left: 0;
  right: 0;
  max-height: 200px;
  overflow-y: auto;
  z-index: 1000;
  margin-bottom: 2px;
}

.autocomplete-item {
  cursor: pointer;
  transition: background-color 0.15s;
}

.autocomplete-item:hover {
  background-color: #f8f9fa !important;
}
</style>
