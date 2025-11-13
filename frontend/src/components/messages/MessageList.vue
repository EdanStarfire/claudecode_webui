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
        :attachedTools="item.attachedTools || []"
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
 * and attaching tools to their parent assistant messages
 * Returns array of:
 * - Regular messages: { type: 'message', message: {...}, attachedTools: [...] }
 * - Compaction events: { type: 'compaction', messages: [{...}, {...}, {...}, {...}] }
 */
const displayableItems = computed(() => {
  const messages = messageStore.currentMessages
  const items = []
  let i = 0

  while (i < messages.length) {
    const msg = messages[i]

    // Check for compaction event FIRST (before shouldDisplayMessage filter)
    // This is critical because the status messages may be filtered by shouldDisplayMessage
    if (isCompactionStart(msg, messages, i)) {
      // Determine if this is a 4-message or 5-message pattern
      const msg3 = messages[i + 2]
      const hasInitMessage = msg3?.type === 'system' && msg3?.metadata?.subtype === 'init'
      const messageCount = hasInitMessage ? 5 : 4

      const compactionMessages = messages.slice(i, i + messageCount)
      items.push({
        type: 'compaction',
        messages: compactionMessages
      })
      i += messageCount // Skip the compaction messages
      continue
    }

    // Regular message filtering
    if (shouldDisplayMessage(msg)) {
      items.push({
        type: 'message',
        message: msg,
        attachedTools: [] // Will be populated by tool grouping
      })
    }

    i++
  }

  // Second pass: Group tools to parent assistant messages
  return groupToolsToParentMessages(items)
})

/**
 * Group tools from content-less assistant messages to the most recent
 * assistant message with content, BUT only if no non-assistant message
 * is encountered during the search.
 *
 * Algorithm:
 * - First pass (forward): Build attachment map
 * - Second pass (forward): Apply attachments and filter
 *
 * This ensures tools appear in chronological order after triggering messages,
 * and consecutive empty assistants consolidate into one message row.
 */
function groupToolsToParentMessages(items) {
  // First pass: Determine where each item's tools should attach
  const attachMap = new Map() // index -> array of tool_uses to attach
  const hideSet = new Set() // indices to hide

  for (let i = 0; i < items.length; i++) {
    const item = items[i]

    // Skip compaction events
    if (item.type === 'compaction') {
      continue
    }

    const msg = item.message

    // Check if this is an assistant message with tools but no content
    const hasContent = msg.content && msg.content.trim().length > 0 && msg.content !== 'Assistant response'
    const hasTools = msg.type === 'assistant' && msg.metadata?.has_tool_uses && msg.metadata?.tool_uses?.length > 0

    if (msg.type === 'assistant' && hasTools && !hasContent) {
      // This is a content-less assistant message with tools
      // Search backwards for assistant with content OR first empty assistant that's NOT hidden
      let parentIndex = -1
      let hitNonAssistant = false

      for (let j = i - 1; j >= 0; j--) {
        const candidateItem = items[j]

        // Skip items already marked for hiding
        if (hideSet.has(j)) {
          continue
        }

        // If we hit a non-assistant message (user/system), stop searching
        if (candidateItem.type === 'message' && candidateItem.message.type !== 'assistant') {
          hitNonAssistant = true
          break
        }

        // Check if this is an assistant message
        if (candidateItem.type === 'message' && candidateItem.message.type === 'assistant') {
          const candidateMsg = candidateItem.message
          const candidateHasContent = candidateMsg.content && candidateMsg.content.trim().length > 0 && candidateMsg.content !== 'Assistant response'

          if (candidateHasContent) {
            // Found assistant with content - use it
            parentIndex = j
            break
          } else {
            // Found empty assistant - use it as consolidation point
            parentIndex = j
            break
          }
        }
      }

      if (hitNonAssistant && parentIndex === -1) {
        // Hit non-assistant but found no assistant after it
        // Keep this empty assistant message - it's the first after the blocker
        // Tools stay on this message
      } else if (hitNonAssistant && parentIndex >= 0) {
        // Hit non-assistant and found empty assistant after it
        // Consolidate tools into that first empty assistant
        if (!attachMap.has(parentIndex)) {
          attachMap.set(parentIndex, [])
        }
        attachMap.get(parentIndex).push(...msg.metadata.tool_uses)
        hideSet.add(i) // Hide this message
      } else if (!hitNonAssistant && parentIndex >= 0) {
        // No blocker, found parent assistant (with or without content)
        // Attach tools to parent, hide this empty message
        if (!attachMap.has(parentIndex)) {
          attachMap.set(parentIndex, [])
        }
        attachMap.get(parentIndex).push(...msg.metadata.tool_uses)
        hideSet.add(i) // Hide this message
      }
      // else: No parent found at all - keep this message with its tools
    }
  }

  // Second pass: Apply attachments and build result
  const processedItems = []

  for (let i = 0; i < items.length; i++) {
    // Skip items marked for hiding
    if (hideSet.has(i)) {
      continue
    }

    const item = items[i]

    // Apply attachments if any
    if (attachMap.has(i)) {
      item.attachedTools = [...(item.attachedTools || []), ...attachMap.get(i)]
    }

    processedItems.push(item)
  }

  return processedItems
}

/**
 * Check if message at index i is the start of a compaction event
 * Pattern (with optional init message):
 * 1. System (subtype=status) - status = 'compacting'
 * 2. System (subtype=status) - status = null
 * 3. [OPTIONAL] System (subtype=init) - new session init after compaction
 * 4. System (subtype=compact_boundary)
 * 5. User (starts with "This session is being continued...")
 */
function isCompactionStart(msg, messages, index) {
  // Need at least 4 messages remaining (5 if init is present)
  if (index + 3 >= messages.length) return false

  const msg1 = messages[index]
  const msg2 = messages[index + 1]
  let msg3 = messages[index + 2]
  let msg4 = messages[index + 3]
  let msg5 = messages[index + 4]

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

  // Check if msg3 is an optional init message
  const hasInitMessage = msg3?.type === 'system' && msg3?.metadata?.subtype === 'init'

  // If init message present, shift the expected positions
  if (hasInitMessage) {
    // Pattern becomes: compacting, null, init, compact_boundary, continuation
    if (index + 4 >= messages.length) return false

    const isMsg3Init = true // We already checked this
    const isMsg4Boundary =
      msg4?.type === 'system' &&
      msg4?.metadata?.subtype === 'compact_boundary'
    const isMsg5Continuation =
      msg5?.type === 'user' &&
      msg5?.content?.startsWith('This session is being continued from a previous conversation')

    // Debug logging
    if (msg1.type === 'system' && msg1.metadata?.subtype === 'status') {
      console.log('[Compaction Debug] Checking 5-message pattern (with init) at index', index)
      console.log('  Msg1 (compacting):', isMsg1Valid)
      console.log('  Msg2 (null status):', isMsg2Valid)
      console.log('  Msg3 (init):', isMsg3Init)
      console.log('  Msg4 (boundary):', isMsg4Boundary)
      console.log('  Msg5 (continuation):', isMsg5Continuation)
    }

    return isMsg1Valid && isMsg2Valid && isMsg3Init && isMsg4Boundary && isMsg5Continuation
  } else {
    // Standard 4-message pattern: compacting, null, compact_boundary, continuation
    const isMsg3Boundary =
      msg3?.type === 'system' &&
      msg3?.metadata?.subtype === 'compact_boundary'
    const isMsg4Continuation =
      msg4?.type === 'user' &&
      msg4?.content?.startsWith('This session is being continued from a previous conversation')

    // Debug logging
    if (msg1.type === 'system' && msg1.metadata?.subtype === 'status') {
      console.log('[Compaction Debug] Checking 4-message pattern (no init) at index', index)
      console.log('  Msg1 (compacting):', isMsg1Valid)
      console.log('  Msg2 (null status):', isMsg2Valid)
      console.log('  Msg3 (boundary):', isMsg3Boundary)
      console.log('  Msg4 (continuation):', isMsg4Continuation)
    }

    return isMsg1Valid && isMsg2Valid && isMsg3Boundary && isMsg4Continuation
  }
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

  // Hide system init messages (but NOT status or compact_boundary messages)
  if (message.type === 'system' && subtype === 'init') return false

  // Note: We do NOT hide 'status' or 'compact_boundary' messages here
  // because they are handled by the compaction event grouping logic above

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
