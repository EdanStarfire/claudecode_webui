<template>
  <div class="comm-composer border-top p-3">
    <div class="row g-2">
      <!-- Recipient Selection -->
      <div class="col-md-4">
        <select v-model="recipient" class="form-select form-select-sm">
          <option value="">-- Select Minion or Channel --</option>
          <optgroup label="Minions">
            <option
              v-for="minion in minions"
              :key="minion.session_id"
              :value="`minion:${minion.session_id}`"
            >
              👤 {{ minion.name }}{{ minion.role ? ` (${minion.role})` : '' }}
            </option>
          </optgroup>
          <optgroup v-if="channels.length > 0" label="Channels">
            <option
              v-for="channel in channels"
              :key="channel.channel_id"
              :value="`channel:${channel.channel_id}`"
            >
              #️⃣ {{ channel.name }}
            </option>
          </optgroup>
        </select>
      </div>

      <!-- Comm Type -->
      <div class="col-md-2">
        <select v-model="commType" class="form-select form-select-sm">
          <option value="task">Task</option>
          <option value="question">Question</option>
          <option value="info">Info</option>
          <option value="report">Report</option>
        </select>
      </div>

      <!-- Message Content with Autocomplete -->
      <div class="col-md-6 position-relative">
        <textarea
          ref="contentTextarea"
          v-model="content"
          class="form-control form-control-sm"
          rows="2"
          placeholder="Type message... Use @ to mention minions"
          @input="handleAutocomplete"
          @keydown="handleKeydown"
        ></textarea>

        <!-- Autocomplete Dropdown -->
        <div
          v-if="showAutocomplete"
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
            {{ match.type === 'minion' ? '👤' : '#️⃣' }}
            {{ match.name }}{{ match.role ? ` (${match.role})` : '' }}
          </div>
        </div>
      </div>
    </div>

    <!-- Send Button -->
    <div class="row mt-2">
      <div class="col-12 text-end">
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
import { ref, computed, watch } from 'vue'
import { useLegionStore } from '../../stores/legion'
import { useSessionStore } from '../../stores/session'
import { useProjectStore } from '../../stores/project'

const props = defineProps({
  legionId: {
    type: String,
    required: true
  }
})

const legionStore = useLegionStore()
const sessionStore = useSessionStore()
const projectStore = useProjectStore()

const recipient = ref('')
const commType = ref('task')
const content = ref('')
const sending = ref(false)

// Autocomplete state
const showAutocomplete = ref(false)
const autocompleteMatches = ref([])
const selectedAutocompleteIndex = ref(0)
const mentionStartPos = ref(-1)
const contentTextarea = ref(null)

// Get minions for this legion
const minions = computed(() => {
  const project = projectStore.projects.get(props.legionId)
  if (!project || !project.session_ids) return []

  return project.session_ids
    .map(sid => sessionStore.sessions.get(sid))
    .filter(s => s && s.is_minion)
})

// Get channels (placeholder for future)
const channels = computed(() => {
  return legionStore.currentChannels || []
})

// Can send if recipient and content are provided
const canSend = computed(() => {
  return recipient.value && content.value.trim().length > 0
})

/**
 * Handle autocomplete on @ character
 */
function handleAutocomplete() {
  const textarea = contentTextarea.value
  if (!textarea) return

  const text = content.value
  const cursorPos = textarea.selectionStart

  // Find the last @ before cursor
  const textBeforeCursor = text.substring(0, cursorPos)
  const lastAtIndex = textBeforeCursor.lastIndexOf('@')

  if (lastAtIndex === -1) {
    showAutocomplete.value = false
    return
  }

  // Get the search query after @
  const searchQuery = textBeforeCursor.substring(lastAtIndex + 1).toLowerCase()

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
      id: minion.session_id
    }))

  if (matches.length === 0) {
    showAutocomplete.value = false
    return
  }

  autocompleteMatches.value = matches
  selectedAutocompleteIndex.value = 0
  mentionStartPos.value = lastAtIndex
  showAutocomplete.value = true
}

/**
 * Handle keyboard navigation in autocomplete
 */
function handleKeydown(event) {
  if (!showAutocomplete.value) {
    // Handle Enter to send
    if (event.key === 'Enter' && !event.shiftKey && canSend.value) {
      event.preventDefault()
      sendComm()
    }
    return
  }

  // Autocomplete navigation
  if (event.key === 'ArrowDown') {
    event.preventDefault()
    selectedAutocompleteIndex.value =
      (selectedAutocompleteIndex.value + 1) % autocompleteMatches.value.length
  } else if (event.key === 'ArrowUp') {
    event.preventDefault()
    selectedAutocompleteIndex.value =
      selectedAutocompleteIndex.value === 0
        ? autocompleteMatches.value.length - 1
        : selectedAutocompleteIndex.value - 1
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

  // Replace from @ to cursor with the mention
  const newText =
    text.substring(0, mentionStartPos.value) +
    '@' +
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
    } else if (recipientType === 'channel') {
      commData.to_channel_id = recipientId
    }

    // Send via legion store
    await legionStore.sendComm(props.legionId, commData)

    // Clear form
    content.value = ''
    // Keep recipient and type selected for convenience
  } catch (error) {
    console.error('Failed to send comm:', error)
    alert(`Failed to send comm: ${error.message}`)
  } finally {
    sending.value = false
  }
}

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

.autocomplete-dropdown {
  top: 100%;
  left: 0;
  right: 0;
  max-height: 200px;
  overflow-y: auto;
  z-index: 1000;
  margin-top: 2px;
}

.autocomplete-item {
  cursor: pointer;
  transition: background-color 0.15s;
}

.autocomplete-item:hover {
  background-color: #f8f9fa !important;
}
</style>
