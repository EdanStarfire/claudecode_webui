<template>
  <div class="messages-area-wrapper flex-grow-1">
    <div class="messages-area overflow-auto" :class="{ 'theme-red': uiStore.isRedBackground }" ref="messagesArea" role="log" aria-live="polite" aria-label="Conversation messages" @scroll="onScroll">
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

      <!-- Issue #662: Truncation banner after last assistant message when response was truncated -->
      <TruncationBanner v-if="showTruncationBanner" :key="'truncation-' + sessionStore.currentSessionId" />
    </div>

    <!-- TTS Floating Controls (issue #735) — outside scroll container to avoid layout shift -->
    <div v-if="tts.isPlaying.value" class="tts-floating-controls">
      <button
        class="btn btn-sm btn-outline-secondary"
        @click="tts.isPaused.value ? tts.resume() : tts.pause()"
        :aria-label="tts.isPaused.value ? 'Resume reading' : 'Pause reading'"
      >
        {{ tts.isPaused.value ? '\u25B6' : '\u23F8' }}
      </button>
      <button
        class="btn btn-sm btn-outline-danger"
        @click="tts.stop()"
        aria-label="Stop reading"
      >&#x23F9;</button>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch, nextTick, onMounted, onUnmounted, provide } from 'vue'
import { useMessageStore } from '@/stores/message'
import { useSessionStore } from '@/stores/session'
import { useUIStore } from '@/stores/ui'
import MessageItem from './MessageItem.vue'
import CompactionEventGroup from './CompactionEventGroup.vue'
import TruncationBanner from './TruncationBanner.vue'
import { useTTSReadAloud } from '@/composables/useTTSReadAloud'

const messageStore = useMessageStore()
const sessionStore = useSessionStore()
const uiStore = useUIStore()

const messagesArea = ref(null)
const isProgrammaticScroll = ref(false)
const scrollRafId = ref(null)

// TTS Read Aloud (issue #735)
const tts = useTTSReadAloud()
provide('ttsReadAloud', tts)

// Provide all messages for play-from-here navigation
const allMessages = computed(() => messageStore.currentMessages)
provide('allMessages', allMessages)

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

// Issue #662: Show truncation banner when last stop_reason is max_tokens
const showTruncationBanner = computed(() => {
  const stopReason = messageStore.lastStopReasonBySession.get(sessionStore.currentSessionId)
  return stopReason === 'max_tokens'
})

/**
 * Capture current scroll position as a message index + isAtBottom flag.
 * Called by session store before switching away from a session.
 */
function getScrollPosition() {
  if (!messagesArea.value || displayableItems.value.length === 0) return null
  const container = messagesArea.value
  const scrollTop = container.scrollTop
  const scrollHeight = container.scrollHeight
  const clientHeight = container.clientHeight

  // Check if at bottom (within 50px threshold)
  if (scrollTop + clientHeight >= scrollHeight - 50) {
    return { index: displayableItems.value.length - 1, isAtBottom: true }
  }

  // Find topmost visible direct child
  const children = Array.from(container.children)
  for (let i = 0; i < children.length; i++) {
    if (children[i].offsetTop + children[i].offsetHeight > scrollTop) {
      return { index: i, isAtBottom: false }
    }
  }
  return { index: 0, isAtBottom: false }
}

/**
 * Restore scroll position for the current session from saved state.
 */
async function restoreScrollPosition() {
  const pos = sessionStore.scrollPositions.get(sessionStore.currentSessionId)
  if (!pos || !messagesArea.value) return

  await nextTick()
  await new Promise(resolve => requestAnimationFrame(resolve))

  if (!messagesArea.value) return
  const container = messagesArea.value

  if (pos.isAtBottom || pos.index >= displayableItems.value.length - 1) {
    // Was at bottom — scroll to bottom, keep auto-scroll enabled
    isProgrammaticScroll.value = true
    container.scrollTop = container.scrollHeight
    requestAnimationFrame(() => { isProgrammaticScroll.value = false })
    uiStore.setAutoScroll(true)
  } else {
    // Restore to specific message index
    const children = Array.from(container.children)
    if (pos.index < children.length) {
      isProgrammaticScroll.value = true
      container.scrollTop = children[pos.index].offsetTop
      requestAnimationFrame(() => { isProgrammaticScroll.value = false })
    }
    uiStore.setAutoScroll(false)
  }
}

// Auto-scroll function
async function scrollToBottom() {
  // Skip auto-scroll when a scroll position restore is pending
  if (sessionStore.pendingScrollRestoreSessionId === sessionStore.currentSessionId) {
    return
  }
  if (uiStore.autoScrollEnabled) {
    await nextTick()
    if (messagesArea.value) {
      isProgrammaticScroll.value = true
      messagesArea.value.scrollTop = messagesArea.value.scrollHeight
      requestAnimationFrame(() => { isProgrammaticScroll.value = false })
    }
  }
}

// Scroll event handler: auto-toggle autoscroll based on user scroll position
function onScroll() {
  if (isProgrammaticScroll.value) return
  if (scrollRafId.value) return
  scrollRafId.value = requestAnimationFrame(() => {
    scrollRafId.value = null
    if (!messagesArea.value) return
    const { scrollTop, scrollHeight, clientHeight } = messagesArea.value
    const isAtBottom = scrollTop + clientHeight >= scrollHeight - 10
    if (uiStore.autoScrollEnabled && !isAtBottom) {
      uiStore.setAutoScroll(false)
    } else if (!uiStore.autoScrollEnabled && isAtBottom) {
      uiStore.setAutoScroll(true)
    }
  })
}

// TTS: Auto-queue new assistant messages when read aloud is enabled.
// Watch raw message list (not displayableItems) because groupToolsToParentMessages
// can merge/hide messages, making displayableItems.length unreliable for detection.
// Use ttsInitialized flag to skip the initial message load (page reload / session switch)
// so historical messages don't get queued for reading.
const lastSeenMessageCount = ref(messageStore.currentMessages.length)
const ttsInitialized = ref(false)
watch(() => messageStore.currentMessages.length, (newLen) => {
  if (!ttsInitialized.value) {
    // First change after mount or session switch — treat as initial load, don't queue
    lastSeenMessageCount.value = newLen
    ttsInitialized.value = true
    return
  }
  if (newLen > lastSeenMessageCount.value && uiStore.ttsReadAloudEnabled) {
    const msgs = messageStore.currentMessages
    for (let i = lastSeenMessageCount.value; i < newLen; i++) {
      const msg = msgs[i]
      if (msg?.type === 'assistant') {
        const content = msg.content || ''
        if (content.trim() && content !== 'Assistant response') {
          tts.queueNewMessage(msg)
        }
      }
    }
  }
  lastSeenMessageCount.value = newLen
})

// Reset TTS message counter on session switch — mark as uninitialized
// so the next batch of messages is treated as initial load
watch(() => sessionStore.currentSessionId, () => {
  lastSeenMessageCount.value = messageStore.currentMessages.length
  ttsInitialized.value = false
})

// Auto-scroll on new messages, or restore scroll position if pending
watch(() => displayableItems.value.length, async (newLen) => {
  if (newLen > 0 && sessionStore.pendingScrollRestoreSessionId === sessionStore.currentSessionId) {
    sessionStore.clearScrollRestorePending()
    await restoreScrollPosition()
    return
  }
  scrollToBottom()
})

// Auto-scroll on tool call updates (for permission requests, status changes, etc.)
watch(() => messageStore.currentToolCalls.length, scrollToBottom)

// Watch for tool call status changes (e.g., permission_required)
watch(
  () => messageStore.currentToolCalls.map(tc => `${tc.id}-${tc.status}`).join(','),
  scrollToBottom
)

// Register scroll position getter and scroll to bottom on mount
onMounted(() => {
  sessionStore.registerScrollPositionGetter(getScrollPosition)
  // Add a small delay to ensure content is fully rendered
  setTimeout(() => {
    scrollToBottom()
  }, 100)
})

// Unregister scroll position getter and cancel pending rAF on unmount
onUnmounted(() => {
  sessionStore.registerScrollPositionGetter(null)
  if (scrollRafId.value) {
    cancelAnimationFrame(scrollRafId.value)
    scrollRafId.value = null
  }
})

function shouldDisplayMessage(message) {
  // Filter messages that shouldn't be displayed
  const subtype = message.subtype || message.metadata?.subtype

  // Hide system init messages (but NOT status or compact_boundary messages)
  if (message.type === 'system' && subtype === 'init') return false

  // Issue #684: Hide SDK "Tool loaded." messages (internal ToolSearch plumbing)
  if (message.type === 'user' && (message.content || '').trim() === 'Tool loaded.') return false

  // Issue #689: Suppress task lifecycle messages — now displayed inside SubagentTimeline cards
  if (message.type === 'system') {
    const taskSubtype = message.metadata?.subtype
    if (['task_started', 'task_progress', 'task_notification'].includes(taskSubtype)) {
      return false
    }
  }

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

  // Hide user messages that are task prompts sent to subagents
  // These have parent_tool_use_id set but are not tool results
  // The prompt content is already visible in the Task tool card
  if (message.type === 'user') {
    const metadata = message.metadata || {}
    if (metadata.parent_tool_use_id && !metadata.has_tool_results) {
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
    id: message.id,
    message_id: message.message_id,
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
.messages-area-wrapper {
  position: relative;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.messages-area {
  flex: 1;
  overflow: auto;
  background: #ffffff;
  padding: 8px 0;
}

.messages-area.theme-red {
  background: #fff5f5;
}
</style>
