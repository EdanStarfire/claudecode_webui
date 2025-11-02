<template>
  <div class="messages-area flex-grow-1 overflow-auto" ref="messagesArea">
    <div v-if="displayableItems.length === 0" class="text-muted text-center py-5">
      No messages yet. Start a conversation!
    </div>

    <!-- Messages and compaction events using new component architecture -->
    <!-- Tool cards are embedded within AssistantMessage components -->
    <template v-for="(item, index) in displayableItems" :key="`item-${index}`">
      <!-- Regular message -->
      <MessageItem
        v-if="item.type === 'message'"
        :message="normalizeMessage(item.message)"
      />

      <!-- Compaction event group -->
      <CompactionEventGroup
        v-else-if="item.type === 'compaction'"
        :messages="item.messages"
      />
    </template>
  </div>
</template>

<script setup>
import { computed, ref, watch, nextTick, onMounted } from 'vue'
import { useMessageStore } from '@/stores/message'
import { useUIStore } from '@/stores/ui'
import MessageItem from './MessageItem.vue'
import CompactionEventGroup from './CompactionEventGroup.vue'

const messageStore = useMessageStore()
const uiStore = useUIStore()

const messagesArea = ref(null)

/**
 * Group messages into displayable items, detecting compaction event sequences
 * Returns array of:
 * - Regular messages: { type: 'message', message: {...} }
 * - Compaction events: { type: 'compaction', messages: [{...}, {...}, {...}, {...}] }
 */
const displayableItems = computed(() => {
  const messages = messageStore.currentMessages
  const items = []
  let i = 0

  while (i < messages.length) {
    const msg = messages[i]

    // Check if this is the start of a compaction event (4-message sequence)
    if (isCompactionStart(msg, messages, i)) {
      const compactionMessages = messages.slice(i, i + 4)
      items.push({
        type: 'compaction',
        messages: compactionMessages
      })
      i += 4 // Skip the 4 compaction messages
    } else if (shouldDisplayMessage(msg)) {
      // Regular message
      items.push({
        type: 'message',
        message: msg
      })
      i++
    } else {
      // Skip hidden messages
      i++
    }
  }

  return items
})

/**
 * Check if message at index i is the start of a compaction event
 * Pattern:
 * 1. System (subtype=status) - status = 'compacting'
 * 2. System (subtype=status) - status = null
 * 3. System (subtype=compact_boundary)
 * 4. User (starts with "This session is being continued...")
 */
function isCompactionStart(msg, messages, index) {
  // Need at least 4 messages remaining
  if (index + 3 >= messages.length) return false

  const msg1 = messages[index]
  const msg2 = messages[index + 1]
  const msg3 = messages[index + 2]
  const msg4 = messages[index + 3]

  // Message 1: System status=compacting
  const isMsg1Valid =
    msg1.type === 'system' &&
    msg1.metadata?.subtype === 'status' &&
    msg1.metadata?.init_data?.status === 'compacting'

  // Message 2: System status=null
  const isMsg2Valid =
    msg2.type === 'system' &&
    msg2.metadata?.subtype === 'status' &&
    (msg2.metadata?.init_data?.status === null || msg2.metadata?.init_data?.status === undefined)

  // Message 3: System compact_boundary
  const isMsg3Valid =
    msg3.type === 'system' &&
    msg3.metadata?.subtype === 'compact_boundary'

  // Message 4: User continuation message
  const isMsg4Valid =
    msg4.type === 'user' &&
    msg4.content?.startsWith('This session is being continued from a previous conversation')

  return isMsg1Valid && isMsg2Valid && isMsg3Valid && isMsg4Valid
}

const currentToolCalls = computed(() => messageStore.currentToolCalls)

// Auto-scroll function
async function scrollToBottom() {
  if (uiStore.autoScrollEnabled) {
    await nextTick()
    if (messagesArea.value) {
      messagesArea.value.scrollTop = messagesArea.value.scrollHeight
    }
  }
}

// Auto-scroll on new messages (watch displayableItems length changes)
watch(() => displayableItems.value.length, scrollToBottom)

// Auto-scroll on tool call updates (for permission requests, status changes, etc.)
watch(() => messageStore.currentToolCalls.length, scrollToBottom)

// Watch for tool call status changes (e.g., permission_required)
watch(
  () => messageStore.currentToolCalls.map(tc => `${tc.id}-${tc.status}`).join(','),
  scrollToBottom
)

// Scroll to bottom on initial mount (for when messages are already loaded)
onMounted(() => {
  // Add a small delay to ensure content is fully rendered
  setTimeout(() => {
    scrollToBottom()
  }, 100)
})

function shouldDisplayMessage(message) {
  // Filter messages that shouldn't be displayed
  const subtype = message.subtype || message.metadata?.subtype

  // Hide system init messages
  if (message.type === 'system' && subtype === 'init') return false

  // Show client_launched, interrupt, and other system messages
  // (These inform the user about session state changes)

  // Hide result messages (they update session state, don't display)
  if (message.type === 'result') return false

  // Hide permission messages (handled by modal)
  if (message.type === 'permission_request' || message.type === 'permission_response') return false

  // Hide user messages that ONLY contain tool results (no actual user text)
  // These messages exist to deliver tool results which update tool cards inline
  if (message.type === 'user' && message.metadata?.has_tool_results) {
    // Check if this is ONLY tool results (content is just "Tool results: N results")
    const content = message.content || ''
    if (content.match(/^Tool results?: \d+ results?$/i) || content.trim() === '') {
      return false
    }
  }

  // Hide skill-related user messages (skill running notification and skill content)
  // These are displayed within the SkillToolHandler component instead
  if (message.type === 'user') {
    const content = message.content || ''
    // Hide message with <command-message> tag (skill running notification)
    if (content.includes('<command-message>') && content.includes('skill is running')) {
      return false
    }
    // Hide message with skill content (starts with "Base directory for this skill:")
    if (content.startsWith('Base directory for this skill:')) {
      return false
    }
  }

  // Hide slash command-related user messages (command running notification and command content)
  // These are displayed within the SlashCommandToolHandler component instead
  if (message.type === 'user') {
    const content = message.content || ''
    // Hide message with <command-message>, <command-name>, and <command-args> tags (slash command running notification)
    if (content.includes('<command-message>') &&
        content.includes('<command-name>') &&
        content.includes('<command-args>')) {
      return false
    }
    // Hide message with slash command content (contains "ARGUMENTS:" trailer)
    if (content.includes('ARGUMENTS:') &&
        (content.includes('<command-name>') || content.match(/\nARGUMENTS:/))) {
      return false
    }
  }

  return true
}

/**
 * Normalize message structure with safe defaults to prevent crashes
 * Ensures all messages have required fields even if backend sends malformed data
 */
function normalizeMessage(message) {
  return {
    type: message.type || 'unknown',
    content: message.content || '',
    timestamp: message.timestamp || Date.now() / 1000,
    metadata: {
      has_tool_uses: false,
      has_tool_results: false,
      has_thinking: false,
      has_permission_requests: false,
      has_permission_responses: false,
      tool_uses: [],
      tool_results: [],
      thinking_content: '',
      thinking_blocks: [],
      ...message.metadata
    }
  }
}
</script>

<style scoped>
.messages-area {
  scroll-behavior: smooth;
}

.message-content {
  white-space: pre-wrap;
  word-wrap: break-word;
}
</style>
