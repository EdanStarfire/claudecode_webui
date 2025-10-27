<template>
  <div class="messages-area flex-grow-1 overflow-auto p-3" ref="messagesArea">
    <div v-if="displayableMessages.length === 0" class="text-muted text-center py-5">
      No messages yet. Start a conversation!
    </div>

    <!-- Messages using new component architecture -->
    <!-- Tool cards are embedded within AssistantMessage components -->
    <MessageItem
      v-for="(message, index) in displayableMessages"
      :key="`msg-${message.timestamp}-${index}`"
      :message="normalizeMessage(message)"
    />
  </div>
</template>

<script setup>
import { computed, ref, watch, nextTick, onMounted } from 'vue'
import { useMessageStore } from '@/stores/message'
import { useUIStore } from '@/stores/ui'
import MessageItem from './MessageItem.vue'

const messageStore = useMessageStore()
const uiStore = useUIStore()

const messagesArea = ref(null)

const displayableMessages = computed(() => {
  return messageStore.currentMessages.filter(msg => shouldDisplayMessage(msg))
})

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

// Auto-scroll on new messages
watch(() => messageStore.currentMessages.length, scrollToBottom)

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
