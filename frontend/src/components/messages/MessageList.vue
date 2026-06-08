<template>
  <div class="messages-area-wrapper flex-grow-1">
    <div class="messages-area overflow-auto" :class="{ 'theme-red': uiStore.isRedBackground }" ref="messagesArea" role="log" aria-live="polite" aria-label="Conversation messages" @scroll="onScroll" data-testid="message-list">
      <div class="messages-content" ref="messagesContent">
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
            :orphanedPermissionTools="item.orphanedPermissionTools || []"
          />

          <!-- Compaction event group -->
          <CompactionEventGroup
            v-else-if="item.type === 'compaction'"
            :messages="item.messages"
            :compaction-group-index="item.groupIndex"
          />

          <!-- Date separator -->
          <div
            v-else-if="item.type === 'date_separator'"
            class="date-separator"
            role="separator"
            aria-label="Date divider"
          >
            <span class="date-separator-label">{{ item.label }}</span>
          </div>
        </template>

        <!-- Issue #662: Truncation banner after last assistant message when response was truncated -->
        <TruncationBanner v-if="showTruncationBanner" :key="'truncation-' + viewSessionId" />

        <!-- Issue #1300: Deferred tool banner when a PreToolUse hook deferred a tool -->
        <DeferredToolBanner
          v-if="deferredToolUse"
          :deferredToolUse="deferredToolUse"
          :key="'deferred-' + viewSessionId"
        />
      </div>
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
import { computed, inject, onActivated, onBeforeUnmount, onDeactivated, provide, ref, watch, nextTick } from 'vue'
import { useMessageStore } from '@/stores/message'
import { useSessionStore } from '@/stores/session'
import { useUIStore } from '@/stores/ui'
import MessageItem from './MessageItem.vue'
import CompactionEventGroup from './CompactionEventGroup.vue'
import TruncationBanner from './TruncationBanner.vue'
import DeferredToolBanner from './DeferredToolBanner.vue'
import { useTTSReadAloud } from '@/composables/useTTSReadAloud'
import { parseTimestamp, formatDateSeparatorLabel } from '@/utils/time'
import { getEffectiveStatusForTool } from '@/composables/useToolStatus'

const messageStore = useMessageStore()
const sessionStore = useSessionStore()
const uiStore = useUIStore()

// Injected per-instance session id (provided by SessionView).
// Every computed in this component reads from this id, not the global currentSessionId,
// so cached instances under KeepAlive never display another session's data.
const viewSessionId = inject('viewSessionId', ref(null))

const messagesArea = ref(null)
const messagesContent = ref(null)
const isProgrammaticScroll = ref(false)
const isInitialLoad = ref(false)
const lastScrollTop = ref(0)
const stickyToBottom = ref(true)
const pendingRestoreTarget = ref(null)
let resizeObserver = null
const STICKY_THRESHOLD_PX = 24

// Per-instance message and tool-call sources derived from the injected session id.
const sessionMessages = computed(() => messageStore.messagesBySession.get(viewSessionId.value) || [])
const sessionToolCalls = computed(() => messageStore.toolCallsBySession.get(viewSessionId.value) || [])

// TTS Read Aloud (issue #735)
const tts = useTTSReadAloud()
provide('ttsReadAloud', tts)

// Provide all messages for play-from-here navigation
provide('allMessages', sessionMessages)

/**
 * Group messages into displayable items, detecting compaction event sequences
 * and attaching tools to their parent assistant messages
 * Returns array of:
 * - Regular messages: { type: 'message', message: {...}, attachedTools: [...] }
 * - Compaction events: { type: 'compaction', messages: [{...}, {...}, {...}, {...}] }
 */
const displayableItems = computed(() => {
  const messages = sessionMessages.value
  const items = []
  let i = 0

  let compactionGroupCount = 0

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
        messages: compactionMessages,
        groupIndex: compactionGroupCount,  // Issue #1350: ordinal for PreCompact hook lookup
      })
      compactionGroupCount++
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
  // Third pass: Inject date separators between items on different calendar dates
  // Fourth pass: Attach any permission_required tools not yet anchored to a bubble
  return attachOrphanedPermissionTools(
    injectDateSeparators(groupToolsToParentMessages(items)),
    viewSessionId.value,
  )
})

function timestampForItem(item) {
  if (item.type === 'compaction') return item.messages[0]?.timestamp
  return item.message?.timestamp
}

function localDateKey(timestamp) {
  return parseTimestamp(timestamp).toDateString()
}

function injectDateSeparators(items) {
  if (items.length === 0) return items
  const out = []
  let prevKey = null
  for (const item of items) {
    const ts = timestampForItem(item)
    const key = ts ? localDateKey(ts) : null
    if (prevKey !== null && key !== null && key !== prevKey) {
      out.push({ type: 'date_separator', label: formatDateSeparatorLabel(ts) })
    }
    out.push(item)
    if (key !== null) prevKey = key
  }
  return out
}

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

// Issue #662: Show truncation banner when last stop_reason is max_tokens
const showTruncationBanner = computed(() => {
  const stopReason = messageStore.lastStopReasonBySession.get(viewSessionId.value)
  return stopReason === 'max_tokens'
})

// Issue #1300: Deferred tool use for deferral banner
const deferredToolUse = computed(() =>
  messageStore.deferredToolUseBySession.get(viewSessionId.value) || null
)

function setScrollTop(position) {
  const prevFocused = document.activeElement
  isProgrammaticScroll.value = true
  lastScrollTop.value = Math.round(position)
  messagesArea.value.scrollTop = position
  if (
    prevFocused &&
    prevFocused !== document.body &&
    document.activeElement !== prevFocused &&
    typeof prevFocused.focus === 'function'
  ) {
    prevFocused.focus({ preventScroll: true })
  }
  requestAnimationFrame(() => { isProgrammaticScroll.value = false })
}

function scrollToBottomNow() {
  if (!messagesArea.value) return
  setScrollTop(messagesArea.value.scrollHeight)
}

function applyPendingRestore() {
  if (pendingRestoreTarget.value === null || !messagesArea.value) return
  const el = messagesArea.value
  const target = pendingRestoreTarget.value
  const maxScroll = Math.max(0, el.scrollHeight - el.clientHeight)
  const clamped = Math.min(target, maxScroll)
  setScrollTop(clamped)
  if (clamped >= target) pendingRestoreTarget.value = null
}

function recomputeStickyFromScroll(el) {
  const distance = el.scrollHeight - el.scrollTop - el.clientHeight
  stickyToBottom.value = distance < STICKY_THRESHOLD_PX
  uiStore.setStickyToBottom(viewSessionId.value, stickyToBottom.value)
}

function teardownObserver() {
  if (resizeObserver) {
    resizeObserver.disconnect()
    resizeObserver = null
  }
}

// Auto-scroll function
async function scrollToBottom() {
  if (!uiStore.autoScrollEnabled) return
  if (!stickyToBottom.value) return
  await nextTick()
  scrollToBottomNow()
}

// Scroll event handler: track user-initiated scroll position and guard programmatic scrolls
function onScroll() {
  if (isProgrammaticScroll.value) return
  const el = messagesArea.value
  if (!el) return
  lastScrollTop.value = el.scrollTop
  if (pendingRestoreTarget.value !== null) {
    pendingRestoreTarget.value = null
  }
  recomputeStickyFromScroll(el)
}

// TTS: Auto-queue new assistant messages when read aloud is enabled.
// Watch raw message list (not displayableItems) because groupToolsToParentMessages
// can merge/hide messages, making displayableItems.length unreliable for detection.
// Use ttsInitialized flag to skip the initial message load (page reload / session switch)
// so historical messages don't get queued for reading.
const lastSeenMessageCount = ref(sessionMessages.value.length)
const ttsInitialized = ref(false)
watch(() => sessionMessages.value.length, (newLen) => {
  if (!ttsInitialized.value) {
    // First change after mount or reactivation — treat as initial load, don't queue.
    lastSeenMessageCount.value = newLen
    ttsInitialized.value = true
    return
  }
  // Only read-aloud for the currently active session; skip for cached-but-hidden instances.
  if (viewSessionId.value !== sessionStore.currentSessionId) {
    lastSeenMessageCount.value = newLen
    return
  }
  if (newLen > lastSeenMessageCount.value && uiStore.ttsReadAloudEnabled) {
    const msgs = sessionMessages.value
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

onActivated(async () => {
  lastSeenMessageCount.value = sessionMessages.value.length
  ttsInitialized.value = false

  isInitialLoad.value = true  // PERF GUARD: suppress watcher burst during bulk message load

  await nextTick()
  await new Promise(resolve => requestAnimationFrame(resolve))

  if (!messagesArea.value || !messagesContent.value) {
    isInitialLoad.value = false
    return
  }

  if (uiStore.autoScrollEnabled) {
    stickyToBottom.value = true
    uiStore.setStickyToBottom(viewSessionId.value, true)
    pendingRestoreTarget.value = null
    scrollToBottomNow()
  } else {
    stickyToBottom.value = false
    uiStore.setStickyToBottom(viewSessionId.value, false)
    const saved = sessionStore.scrollPositions.get(viewSessionId.value)
    if (typeof saved === 'number' && saved > 0) {
      pendingRestoreTarget.value = saved
      applyPendingRestore()
    } else {
      setScrollTop(0)
    }
  }

  resizeObserver = new ResizeObserver(() => {
    if (!messagesArea.value) return
    if (pendingRestoreTarget.value !== null) {
      applyPendingRestore()
      return
    }
    if (uiStore.autoScrollEnabled && stickyToBottom.value) {
      scrollToBottomNow()
    }
  })
  resizeObserver.observe(messagesContent.value)

  isInitialLoad.value = false  // PERF GUARD released
})

onDeactivated(() => {
  if (viewSessionId.value) {
    sessionStore.saveScrollPosition(viewSessionId.value, lastScrollTop.value)
  }
  teardownObserver()
})

onBeforeUnmount(teardownObserver)

// Auto-scroll on new messages
watch(() => displayableItems.value.length, () => {
  if (isInitialLoad.value) return
  scrollToBottom()
})

// Auto-scroll on tool call updates (for permission requests, status changes, etc.)
watch(() => sessionToolCalls.value.length, () => {
  if (isInitialLoad.value) return
  scrollToBottom()
})

// Watch for tool call status changes (e.g., permission_required)
watch(
  () => sessionToolCalls.value.map(tc => `${tc.id}-${tc.status}`).join(','),
  () => {
    if (isInitialLoad.value) return
    scrollToBottom()
  }
)

// Issue #1631: Immediate scroll-to-bottom on token increment (triggered by SessionStatusBar re-enable click)
watch(
  () => uiStore.scrollToBottomTokenBySession.get(viewSessionId.value),
  (token, prev) => {
    if (token !== prev && token != null) {
      stickyToBottom.value = true
      uiStore.setStickyToBottom(viewSessionId.value, true)
      scrollToBottomNow()
    }
  }
)

/**
 * Issue #1626 Fix B: Attach any permission_required tools from toolCallsBySession that are
 * not yet referenced by any displayed bubble to the last assistant item. This ensures the
 * permission prompt renders even when the bubble's metadata.tool_uses is empty due to the
 * streaming dedup race (before Fix A fully resolves the issue across all SDK variants).
 */
function attachOrphanedPermissionTools(items, sessionId) {
  if (!sessionId) return items
  const liveTools = messageStore.toolCallsBySession.get(sessionId) || []
  if (liveTools.length === 0) return items

  // Collect tool_use_ids already referenced by any displayed bubble.
  const referenced = new Set()
  for (const item of items) {
    if (item.type !== 'message') continue
    const msg = item.message
    if (msg.type !== 'assistant') continue
    for (const t of msg.metadata?.tool_uses || []) referenced.add(t.id)
    for (const t of item.attachedTools || []) referenced.add(t.id)
  }

  // Find permission_required tools not yet referenced.
  const orphans = []
  for (const tc of liveTools) {
    if (referenced.has(tc.id)) continue
    if (getEffectiveStatusForTool(tc) !== 'permission_required') continue
    orphans.push({ id: tc.id, name: tc.name, input: tc.input })
  }
  if (orphans.length === 0) return items

  // Attach to the last assistant item.
  for (let i = items.length - 1; i >= 0; i--) {
    if (items[i].type === 'message' && items[i].message?.type === 'assistant') {
      items[i].orphanedPermissionTools = [...(items[i].orphanedPermissionTools || []), ...orphans]
      return items
    }
  }

  // No assistant bubble yet — push a minimal anchor so the prompt has a slot.
  // This path is defensive only; in practice the model always emits an assistant
  // message before a tool_use. Log a warning if it ever fires.
  console.warn('[issue-1626] attachOrphanedPermissionTools: no assistant bubble found, creating anchor')
  items.push({
    type: 'message',
    message: { type: 'assistant', content: '', metadata: { tool_uses: [] }, timestamp: Date.now() / 1000 },
    attachedTools: [],
    orphanedPermissionTools: orphans,
  })
  return items
}

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
    if (['task_started', 'task_progress', 'task_notification', 'thinking_tokens'].includes(taskSubtype)) {
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

  // Issue #1350: Hide hook system messages that have been successfully correlated to a
  // parent element (tool node, user/assistant bubble, compaction group). Messages that
  // could NOT be correlated stay visible and render through the SystemMessage pill path.
  if (message.type === 'system') {
    const subtype2 = message.metadata?.subtype
    if (subtype2 === 'hook_started' || subtype2 === 'hook_response') {
      const msgId = message.id || message.message_id
      if (msgId && messageStore.isHookMessageAttached(viewSessionId.value, msgId)) {
        return false
      }
    }
  }

  // Issue #1242: Hide signature-only assistant messages emitted in Auto permission mode.
  // These contain a ThinkingBlock with empty thinking text plus a signature blob, no text,
  // no tool_use. Keeping them fragments tool timelines because the grouping walk-back in
  // groupToolsToParentMessages stops at the first empty assistant it finds.
  // Issue #1486: streaming placeholders are always shown — content is being built up.
  if (message.type === 'assistant' && !message.streaming) {
    const meta = message.metadata || {}
    const text = (message.content || '').trim()
    const hasText = text.length > 0 && text !== 'Assistant response'
    const hasThinkingText = (meta.thinking_content || '').trim().length > 0
    const hasTools = (meta.tool_uses || []).length > 0
    if (!hasText && !hasThinkingText && !hasTools) {
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
    // Issue #1486: preserve streaming placeholder fields — stripping them breaks the caret and
    // thinking-block display because AssistantMessage.vue reads these directly off the message.
    streaming: message.streaming || false,
    thinking: message.thinking || '',
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
  background: var(--bs-body-bg);
  padding: 8px 0;
}

.messages-content {
  min-height: 100%;
}

.date-separator {
  display: flex;
  align-items: center;
  justify-content: center;
  margin: 12px 0;
  position: relative;
}

.date-separator::before,
.date-separator::after {
  content: '';
  flex: 1;
  height: 1px;
  background: var(--bs-border-color, rgba(0, 0, 0, 0.1));
}

.date-separator-label {
  padding: 0 12px;
  font-size: 0.8125rem;
  color: var(--bs-secondary-color, #6c757d);
  text-transform: none;
  white-space: nowrap;
  user-select: none;
}
</style>
