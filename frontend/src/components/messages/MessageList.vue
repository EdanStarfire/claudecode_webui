<template>
  <div class="messages-area-wrapper flex-grow-1">
    <div class="messages-area overflow-auto" :class="{ 'theme-red': uiStore.isRedBackground }" ref="messagesArea" role="log" aria-live="polite" aria-label="Conversation messages" @scroll="onScroll" data-testid="message-list">
      <div class="messages-content" ref="messagesContent">
        <!-- Issue #1746 (stage: subagents) follow-up: persistent gutter OUTSIDE the message
             flow — a lane spans from a leg's launch position to wherever the conversation
             currently is while it runs, surviving however many unrelated turns happen in
             between. Must live here (a single instance covering the whole message list), not
             nested inside any individual message.
             Issue #1748 (stage: offset-model): lane top/bottom now come from the virtualizer's
             offset model (laneOffsets, computed below) instead of getBoundingClientRect() —
             see SubagentGlobalGutter.vue's measure()-replacement comment. -->
        <SubagentGlobalGutter
          :sessionId="viewSessionId"
          :laneOffsets="laneOffsets"
          @chip-click="onGutterChipClick"
        />

        <div v-if="displayableItems.length === 0" class="text-muted text-center py-5">
          No messages yet. Start a conversation!
        </div>

        <!-- Issue #1748 (stage: offset-model): virtualizer-driven rendering. Item TYPE dispatch
             (MessageItem/CompactionEventGroup/date-separator/SubagentAnchorRow) is unchanged from
             before — only "which indices get a real DOM node right now" is new. Stage 1 pins
             `overscan` to the full item count (see virtualizerOptions below), so every item is
             always in range and this renders identically to the old plain v-for; only the
             underlying layout mechanism (sized spacer + translateY per row, instead of normal
             document flow) has changed. -->
        <div class="virtual-spacer" :style="{ height: `${rowVirtualizer.getTotalSize()}px` }">
          <div
            v-for="row in renderedRows"
            :key="row.virtualRow.key"
            :data-index="row.virtualRow.index"
            :ref="el => rowVirtualizer.measureElement(el)"
            class="virtual-item-row"
            :style="{ transform: `translateY(${row.virtualRow.start}px)` }"
          >
            <!-- Regular message -->
            <MessageItem
              v-if="row.item.type === 'message'"
              :message="normalizeMessage(row.item.message)"
              :attachedTools="row.item.attachedTools || []"
              :orphanedPermissionTools="row.item.orphanedPermissionTools || []"
              :mergedMessages="row.item.mergedMessages || []"
            />

            <!-- Compaction event group -->
            <CompactionEventGroup
              v-else-if="row.item.type === 'compaction'"
              :messages="row.item.messages"
              :compaction-group-index="row.item.groupIndex"
            />

            <!-- Date separator -->
            <div
              v-else-if="row.item.type === 'date_separator'"
              class="date-separator"
              role="separator"
              aria-label="Date divider"
            >
              <span class="date-separator-label">{{ row.item.label }}</span>
            </div>

            <!-- Issue #1746 (stage: subagents) follow-up / spec §4.2: subagent signals that
                 interact with the main session (pushed a message to main, or a leg ended) render
                 inline in the MAIN timeline itself, not only inside the subagent's own nested
                 card — otherwise the main assistant's reaction to one looks unprompted. -->
            <div v-else-if="row.item.type === 'subagent_signal'" class="subagent-signal-wrapper">
              <SubagentAnchorRow
                :id="row.item.domId"
                :anchorType="row.item.anchorType"
                :agentColor="row.item.agentColor"
                :description="row.item.description"
                :markdownBody="row.item.markdownBody"
                :statusText="row.item.statusText"
                :timestamp="row.item.timestamp"
              />
            </div>
          </div>
        </div>

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
import { computed, inject, onActivated, onDeactivated, provide, ref, watch, nextTick } from 'vue'
import { useVirtualizer } from '@tanstack/vue-virtual'
import { useMessageStore } from '@/stores/message'
import { useSessionStore } from '@/stores/session'
import { useUIStore } from '@/stores/ui'
import MessageItem from './MessageItem.vue'
import CompactionEventGroup from './CompactionEventGroup.vue'
import TruncationBanner from './TruncationBanner.vue'
import DeferredToolBanner from './DeferredToolBanner.vue'
import SubagentAnchorRow from './SubagentAnchorRow.vue'
import SubagentGlobalGutter from './SubagentGlobalGutter.vue'
import { useTTSReadAloud } from '@/composables/useTTSReadAloud'
import { useVirtualNavigation, forceFreshMeasurements } from '@/composables/useVirtualNavigation'
import { parseTimestamp, formatDateSeparatorLabel } from '@/utils/time'
import { getEffectiveStatusForTool } from '@/composables/useToolStatus'
import { getAgentColor, slugifyAgentName } from '@/composables/useAgentColor'

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
const stickyToBottom = ref(true)
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
  // Fourth pass: Inject subagent-signal anchors (pushed-to-main, leg-terminal) at their own
  //   chronological position — BEFORE merging, so a signal correctly breaks a would-be merge
  //   between two assistant turns it causally sits between (Issue #1746 follow-up)
  // Fifth pass: Merge consecutive assistant turns into one visual block (Issue #1746)
  // Sixth pass: Attach any permission_required tools not yet anchored to a bubble
  return attachOrphanedPermissionTools(
    mergeConsecutiveAssistantTurns(
      injectSubagentSignals(injectDateSeparators(groupToolsToParentMessages(items)), viewSessionId.value)
    ),
    viewSessionId.value,
  )
})

function itemAt(index) {
  return displayableItems.value[index]
}

// Issue #1748 (stage: offset-model): per-item-TYPE size estimates (not one flat global average),
// per plan §5.4 — bounds a virtualizer's pre-measurement error to "this item vs. its own type's
// typical height" rather than "any row vs. any other row." Values are rough starting points, not
// yet tuned against real large-session height distributions (§5.4 defers that to a follow-up
// empirical pass); they only matter for the brief window before ResizeObserver measures the real
// height, since Stage 1 mounts every item immediately (overscan = count, see virtualizerOptions).
const ITEM_TYPE_SIZE_ESTIMATE = {
  message: 160,
  compaction: 120,
  date_separator: 40,
  subagent_signal: 90,
}
function estimateItemSize(index) {
  return ITEM_TYPE_SIZE_ESTIMATE[itemAt(index)?.type] ?? 160
}

// Issue #1748 (stage: offset-model): virtualizer driving MessageList's own rendering AND
// (via laneOffsets/virtualNav below) SubagentGlobalGutter's lane positions and the shared
// jump-navigation helper. Stage 1 pins `overscan` to the full item count — verified against
// @tanstack/virtual-core's actual source (defaultRangeExtractor clamps
// `[start - overscan, end + overscan]` to `[0, count-1]`), so every item stays mounted exactly
// as today; only the offset/measurement model changes in this stage. Lowering `overscan` to
// enable real DOM culling is Stage 2 — a separate, later change.
const rowVirtualizer = useVirtualizer(computed(() => ({
  count: displayableItems.value.length,
  getScrollElement: () => messagesArea.value,
  estimateSize: estimateItemSize,
  overscan: displayableItems.value.length,
  // Fires on every virtualizer measurement change — in particular, a mounted tail row's own
  // height changing (streaming growth) while no new item was added, which today's whole-container
  // ResizeObserver caught incidentally. This is the explicit wiring point plan §7 calls out as
  // easy to silently regress without a dedicated test (see MessageList.test.js).
  onChange: scheduleStickyScroll,
})))

const virtualNav = useVirtualNavigation(rowVirtualizer)

// Pairs each virtual row with its displayableItems entry ONCE per render, instead of the template
// re-indexing displayableItems on every v-if branch and prop binding for the same row (up to 8
// lookups/row otherwise, across however many rows Stage 1 mounts — the full item count).
const renderedRows = computed(() =>
  rowVirtualizer.value.getVirtualItems().map(virtualRow => ({
    virtualRow,
    item: itemAt(virtualRow.index),
  }))
)

// Issue #1748 (stage: offset-model): reverse indices from a tool_use id (or a subagent-signal's
// stable domId) to its containing displayableItems index — a pure data lookup, no DOM, rebuilt
// whenever displayableItems recomputes. This is what lets the gutter and navigation helper
// resolve a jump target's position without ever touching the DOM (plan §5.2/§5.5).
const indexMaps = computed(() => {
  const toolUseIndex = new Map() // tool_use id -> displayableItems index
  const signalIndex = new Map()  // subagent_signal domId -> displayableItems index

  displayableItems.value.forEach((item, index) => {
    if (item.type === 'message') {
      for (const t of item.message?.metadata?.tool_uses || []) toolUseIndex.set(t.id, index)
      for (const t of item.attachedTools || []) toolUseIndex.set(t.id, index)
      for (const seg of item.mergedMessages || []) {
        for (const t of seg.metadata?.tool_uses || []) toolUseIndex.set(t.id, index)
        for (const t of seg.attachedTools || []) toolUseIndex.set(t.id, index)
      }
    } else if (item.type === 'subagent_signal' && item.domId) {
      signalIndex.set(item.domId, index)
    }
  })

  return { toolUseIndex, signalIndex }
})

function resolveToolAnchorIndex(toolId) {
  return indexMaps.value.toolUseIndex.get(toolId) ?? null
}

function resolveSubagentPrimaryIndex(taskId, legIndex) {
  const legEntry = messageStore.getTaskLegEntry(taskId)
  const toolUseId = legEntry?.legs?.[legIndex]?.tool_use_id
  if (!toolUseId) return null
  return indexMaps.value.toolUseIndex.get(toolUseId) ?? null
}

// Issue #1748 (stage: offset-model): SubagentGlobalGutter's lane top/bottom, computed here (not
// in the gutter itself) because it must live inside a reactive scope that reads
// rowVirtualizer.value directly — the virtualizer instance is a mutated-in-place class, so a
// prop passed down to a child component would not reliably re-trigger the child's own reactivity
// on every internal update. `assignGutterSlots`/rendering stay in SubagentGlobalGutter.vue
// unchanged (plan §5.2 point 3) — only the top/bottom SOURCE moves here.
const laneOffsets = computed(() => {
  const offsets = new Map() // `${taskId}:${legIndex}` -> { top, bottom, height }
  if (!viewSessionId.value) return offsets

  const totalSize = forceFreshMeasurements(rowVirtualizer.value)
  const measurements = rowVirtualizer.value.measurementsCache

  for (const entry of messageStore.allTaskLegEntriesForSession(viewSessionId.value)) {
    entry.legs.forEach((leg, legIndex) => {
      const primaryIndex = indexMaps.value.toolUseIndex.get(leg.tool_use_id)
      const startItem = primaryIndex != null ? measurements[primaryIndex] : null
      if (!startItem) return // matches old `if (!startEl) continue`
      const top = startItem.start

      let bottom
      if (leg.status === 'running') {
        bottom = totalSize
      } else {
        const domId = `subagent-anchor-terminal-${entry.task_id}-${legIndex}`
        const terminalIndex = indexMaps.value.signalIndex.get(domId)
        const endItem = terminalIndex != null ? measurements[terminalIndex] : null
        bottom = endItem ? endItem.end : top + 26 // defensive fallback (terminal row not found)
      }

      offsets.set(`${entry.task_id}:${legIndex}`, { top, bottom, height: Math.max(0, bottom - top) })
    })
  }

  return offsets
})

async function onGutterChipClick(lane) {
  const index = resolveSubagentPrimaryIndex(lane.taskId, lane.legIndex)
  if (index == null) return
  const mounted = await virtualNav.scrollToItemIndex(index, { align: 'center' })
  if (!mounted) return
  document.getElementById(`subagent-anchor-primary-${lane.taskId}-${lane.legIndex}`)
    ?.scrollIntoView({ behavior: 'smooth', block: 'center' })
}

defineExpose({
  scrollToItemIndex: virtualNav.scrollToItemIndex,
  resolveToolAnchorIndex,
  resolveSubagentPrimaryIndex,
})

function timestampForItem(item) {
  if (item.type === 'compaction') return item.messages[0]?.timestamp
  return item.message?.timestamp
}

function localDateKey(timestamp) {
  return parseTimestamp(timestamp).toDateString()
}

/**
 * Issue #1746 (stage: subagents) follow-up / spec §4.2: subagent events that "interact with
 * the main session" — a message pushed to main, or a leg ending (completed/failed/stopped) —
 * must render inline in the MAIN timeline itself, not only inside the subagent's own nested
 * card, or the main assistant's reaction to one reads as unprompted (no visible causality).
 *
 * Collects these as lightweight anchor items and inserts each one at the position of the first
 * existing item whose own timestamp is at or after the signal's — i.e. immediately before
 * whatever main-thread item first reacts to it, chronologically. Runs BEFORE
 * mergeConsecutiveAssistantTurns() so a signal correctly interrupts a would-be merge between
 * two assistant turns it causally sits between.
 */
function collectSubagentSignals(sessionId) {
  if (!sessionId) return []
  const signals = []

  // "Pushed to main": a subagent delivering a message to main. Not a leg boundary — the leg
  // keeps running. Scoped to the SendMessage tool for now (the shape confirmed by real repro
  // data); mcp__legion__send_comm targeting has different semantics and isn't wired here.
  const toolCalls = messageStore.toolCallsBySession.get(sessionId) || []
  for (const tc of toolCalls) {
    if (tc.name !== 'SendMessage') continue
    if (!tc.parent_tool_use_id) continue // only subagent-originated (main's own calls stay in ActivityTimeline)
    if (tc.input?.to !== 'main') continue
    if (tc.status !== 'completed') continue // only once delivery is confirmed
    const taskId = messageStore.getTaskIdForLaunchToolUse(tc.parent_tool_use_id)
    if (!taskId) continue
    const ts = tc.timestamp
    if (ts == null) continue
    signals.push({
      type: 'subagent_signal',
      anchorType: 'pushed',
      taskId,
      agentColor: getAgentColor(slugifyAgentName(taskId)),
      // Issue #1746 follow-up (user feedback): this content guides the main session — it must
      // render with full markdown support, not a single truncated line. Header keeps a short
      // static label; the actual message goes in markdownBody (SubagentAnchorRow.vue).
      description: 'Message to main',
      markdownBody: tc.input?.message || tc.input?.content || tc.input?.summary || 'Message sent to main',
      statusText: '',
      timestamp: ts,
      _sortTs: parseTimestamp(ts).getTime(),
      key: `subagent-signal-pushed-${tc.id}`,
    })
  }

  // Leg-terminal: each leg that reached a terminal status (completed/failed/stopped). Uses
  // THAT leg's own description (set at its own task_started) — entry-level plain objects here
  // don't carry a TaskLegEntry-style "latest leg" description getter the way the backend
  // dataclass does, and the per-leg value is more precise anyway.
  for (const entry of messageStore.allTaskLegEntriesForSession(sessionId)) {
    entry.legs.forEach((leg, legIndex) => {
      if (leg.status === 'running' || leg.ended_at == null) return
      signals.push({
        type: 'subagent_signal',
        anchorType: 'completed',
        taskId: entry.task_id,
        agentColor: getAgentColor(slugifyAgentName(entry.task_id)),
        description: leg.description || 'Agent',
        // Issue #1746 follow-up (user feedback): the subagent's own final report (leg.result,
        // from task_notification's summary) must be shown, not just a bare status badge —
        // with the same full markdown support as the header's pushed-to-main content.
        markdownBody: leg.result || null,
        statusText: leg.status,
        timestamp: leg.ended_at,
        _sortTs: parseTimestamp(leg.ended_at).getTime(),
        key: `subagent-signal-terminal-${entry.task_id}-${legIndex}`,
        // Issue #1746 follow-up: SubagentGlobalGutter.vue measures a leg's END position from
        // this id — this row IS that leg's terminal anchor now (SubagentTimeline.vue no longer
        // renders one glued to the launch position; doing so there made every historical lane's
        // end sit right next to its start regardless of how much happened in between).
        domId: `subagent-anchor-terminal-${entry.task_id}-${legIndex}`,
      })
    })
  }

  return signals
}

function injectSubagentSignals(items, sessionId) {
  const signals = collectSubagentSignals(sessionId)
  if (signals.length === 0) return items

  const itemTimestamps = items.map(item => {
    const ts = timestampForItem(item)
    return ts ? parseTimestamp(ts).getTime() : null
  })

  // Insert in ascending timestamp order so multiple signals landing in the same gap keep their
  // own relative order instead of each independently re-scanning from index 0.
  signals.sort((a, b) => a._sortTs - b._sortTs)
  for (const signal of signals) {
    let insertAt = items.length
    for (let i = 0; i < items.length; i++) {
      if (itemTimestamps[i] != null && itemTimestamps[i] >= signal._sortTs) { insertAt = i; break }
    }
    items.splice(insertAt, 0, signal)
    itemTimestamps.splice(insertAt, 0, signal._sortTs)
  }
  return items
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
 * Issue #1746 (stage: layout): merge consecutive assistant turns into one visual block.
 *
 * A run starts at any 'assistant' message item and extends onto the next item only if:
 * - the next item is also an 'assistant' message item, AND
 * - the current run's last member is not itself a merge boundary (see isMergeBoundary below).
 *
 * A user item, subagent-related item, date separator, or compaction group always closes the
 * run, since none of those satisfy isAssistantMessageItem().
 *
 * Runs of length 1 pass through unchanged. Runs of length >= 2 collapse into the first item,
 * with the rest attached as `mergedMessages` (each a shallow copy of its message plus its own
 * attachedTools) and dropped from the top-level list — mirroring how groupToolsToParentMessages()
 * already hides consolidated items above.
 */
function isMergeBoundary(item) {
  // Task/Agent/send_comm calls render SubagentTimeline/SendCommToolHandler as siblings AFTER
  // the bubble — a message carrying one of these may be the LAST member of a run, but nothing
  // may visually merge past it (matches AssistantMessage.vue's last-segment-only sourcing).
  // Must check attachedTools too: groupToolsToParentMessages() (the pass just before this one)
  // routinely moves a Task/Agent/send_comm tool_use from a content-less trailing message onto
  // this item's attachedTools rather than leaving it in item.message.metadata.tool_uses —
  // missing that here would silently drop the tool call from rendering (AssistantMessage.vue
  // only sources taskToolCalls/sendCommToolCalls from a run's LAST segment).
  const tools = [
    ...(item.message?.metadata?.tool_uses || []),
    ...(item.attachedTools || []),
  ]
  return tools.some(t => t.name === 'Task' || t.name === 'Agent' || t.name === 'mcp__legion__send_comm')
}

function isAssistantMessageItem(item) {
  return item.type === 'message' && item.message?.type === 'assistant'
}

function mergeConsecutiveAssistantTurns(items) {
  const result = []
  let i = 0

  while (i < items.length) {
    const item = items[i]

    if (!isAssistantMessageItem(item)) {
      result.push(item)
      i++
      continue
    }

    const run = [item]
    let j = i + 1
    while (
      !isMergeBoundary(run[run.length - 1]) &&
      j < items.length &&
      isAssistantMessageItem(items[j])
    ) {
      run.push(items[j])
      j++
    }

    if (run.length === 1) {
      result.push(item)
    } else {
      const [head, ...rest] = run
      head.mergedMessages = rest.map(it => ({
        ...it.message,
        attachedTools: it.attachedTools || [],
      }))
      result.push(head)
    }

    i = j
  }

  return result
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

// Issue #1748 (stage: offset-model) — §7: sticky-to-bottom and scroll-position save/restore now
// go through the virtualizer's own offset model (scrollToIndex/scrollToOffset/scrollOffset)
// instead of raw `scrollTop =` writes, since the virtualizer (not `scrollHeight` growth alone)
// is the source of truth for "where is the true bottom" once rows are absolutely positioned.
// Issue #1630/#1632: a programmatic scrollTop write can blur the focused element (e.g. the
// InputArea textarea while the user is typing during an auto-scroll) — restore focus afterward
// so keystrokes don't silently stop landing anywhere. Centralized here so every virtualizer-driven
// scroll path (setScrollOffset, scrollToBottomViaVirtualizer, the banner-nudge below) gets it.
function guardProgrammaticScroll() {
  const prevFocused = document.activeElement
  isProgrammaticScroll.value = true
  requestAnimationFrame(() => {
    isProgrammaticScroll.value = false
    if (
      prevFocused &&
      prevFocused !== document.body &&
      document.activeElement !== prevFocused &&
      typeof prevFocused.focus === 'function'
    ) {
      prevFocused.focus({ preventScroll: true })
    }
  })
}

function setScrollOffset(offset) {
  const virtualizer = rowVirtualizer.value
  if (!virtualizer) return
  guardProgrammaticScroll()
  virtualizer.scrollToOffset(offset, { align: 'start', behavior: 'auto' })
}

function scrollToBottomViaVirtualizer() {
  const virtualizer = rowVirtualizer.value
  const count = displayableItems.value.length
  if (!virtualizer || count === 0) return
  forceFreshMeasurements(virtualizer)
  guardProgrammaticScroll()
  virtualizer.scrollToIndex(count - 1, { align: 'end', behavior: 'auto' })
  // TruncationBanner/DeferredToolBanner render as normal-flow siblings AFTER the virtualizer's
  // spacer (§11) and aren't part of its tracked row set, so scrollToIndex alone can leave them
  // below the fold. messagesArea.scrollHeight still reflects the TRUE total layout height
  // (spacer + banners) since only the spacer's children are absolutely positioned — nudge to it
  // once the DOM has caught up with the scrollToIndex write, matching the old scrollTop=scrollHeight
  // behavior exactly for this specific "go to the very end" case.
  nextTick(() => {
    if (!messagesArea.value) return
    guardProgrammaticScroll()
    messagesArea.value.scrollTop = messagesArea.value.scrollHeight
  })
}

// §8: logical scroll-position save/restore ({itemIndex, offsetWithinItem}) via the virtualizer's
// own offset model, replacing the old raw-pixel scrollTop representation.
function computeTopmostVisiblePosition() {
  const virtualizer = rowVirtualizer.value
  if (!virtualizer) return null
  const offset = virtualizer.scrollOffset ?? 0
  const item = virtualizer.getVirtualItemForOffset(offset)
  if (!item) return null
  return { itemIndex: item.index, offsetWithinItem: Math.max(0, offset - item.start) }
}

function restoreScrollPosition(position) {
  const virtualizer = rowVirtualizer.value
  if (!virtualizer || !position) { setScrollOffset(0); return }
  forceFreshMeasurements(virtualizer)
  const item = virtualizer.measurementsCache[position.itemIndex]
  setScrollOffset(item ? item.start + (position.offsetWithinItem || 0) : 0)
}

function recomputeStickyFromScroll(el) {
  const distance = el.scrollHeight - el.scrollTop - el.clientHeight
  stickyToBottom.value = distance < STICKY_THRESHOLD_PX
  uiStore.setStickyToBottom(viewSessionId.value, stickyToBottom.value)
}

// Coalesces every sticky-to-bottom trigger (new item, tool-call burst, virtualizer measurement
// change) into a single rAF-batched call — each call is now a scrollToIndex computation rather
// than a plain scrollTop write, so firing several per burst tick (as the old independent watchers
// did) is worth avoiding under virtualization (plan §7, §10).
let stickyScrollScheduled = false
function scheduleStickyScroll() {
  if (isInitialLoad.value) return
  if (!uiStore.autoScrollEnabled || !stickyToBottom.value) return
  if (stickyScrollScheduled) return
  stickyScrollScheduled = true
  // nextTick first: when triggered by a Vue-reactive change (e.g. a new item appended), the
  // virtualizer's own internal options watcher (inside useVirtualizer, reacting to the same
  // count change) needs its flush turn too, or scrollToIndex resolves against a still-stale
  // measurementsCache for the new count. A resize-driven trigger (streaming growth) mutates the
  // virtualizer directly and doesn't need this, but paying for it unconditionally is cheap.
  nextTick(() => {
    requestAnimationFrame(() => {
      stickyScrollScheduled = false
      scrollToBottomViaVirtualizer()
    })
  })
}


// Scroll event handler: track user-initiated scroll position and guard programmatic scrolls
function onScroll() {
  if (isProgrammaticScroll.value) return
  const el = messagesArea.value
  if (!el) return
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
    scrollToBottomViaVirtualizer()
  } else {
    stickyToBottom.value = false
    uiStore.setStickyToBottom(viewSessionId.value, false)
    const saved = sessionStore.scrollPositions.get(viewSessionId.value)
    if (saved && typeof saved.itemIndex === 'number') {
      restoreScrollPosition(saved)
    } else {
      setScrollOffset(0)
    }
  }

  isInitialLoad.value = false  // PERF GUARD released
})

// The virtualizer's measurement cache — and thus scroll state — survives deactivate/reactivate
// for free: KeepAlive preserves the whole component instance (including this setup() closure),
// so there is nothing to tear down here the way the old content-box ResizeObserver needed to be
// (plan §8). useVirtualizer() disposes its own internal observers via onScopeDispose on real
// unmount; no manual teardown is needed for either case.
onDeactivated(() => {
  if (viewSessionId.value) {
    sessionStore.saveScrollPosition(viewSessionId.value, computeTopmostVisiblePosition())
  }
})

// Auto-scroll on new messages
watch(() => displayableItems.value.length, () => scheduleStickyScroll())

// Auto-scroll on tool call updates (for permission requests, status changes, etc.)
watch(() => sessionToolCalls.value.length, () => scheduleStickyScroll())

// Watch for tool call status changes (e.g., permission_required)
watch(
  () => sessionToolCalls.value.map(tc => `${tc.id}-${tc.status}`).join(','),
  () => scheduleStickyScroll()
)

// Issue #1631: Immediate scroll-to-bottom on token increment (triggered by SessionStatusBar re-enable click)
watch(
  () => uiStore.scrollToBottomTokenBySession.get(viewSessionId.value),
  (token, prev) => {
    if (token !== prev && token != null) {
      stickyToBottom.value = true
      uiStore.setStickyToBottom(viewSessionId.value, true)
      scrollToBottomViaVirtualizer()
    }
  }
)

/**
 * Issue #1626 Fix B: Attach any permission_required tools from toolCallsBySession that are
 * not yet referenced by any displayed bubble. This ensures the permission prompt renders
 * even when the bubble's metadata.tool_uses is empty due to the streaming dedup race
 * (before Fix A fully resolves the issue across all SDK variants).
 *
 * Issue #1694: Anchor each orphan to the assistant bubble that produced it by matching
 * tc.messageId against item.message.message_id, searching the whole displayed list —
 * not just the last bubble. This is correct regardless of realtime emission timing
 * because it matches on identity, not recency/position. Falls back to the pre-#1694
 * last-assistant-bubble heuristic only when messageId is absent (legacy stored data) or
 * unresolved (owning bubble not currently displayed/paginated in).
 */
function attachOrphanedPermissionTools(items, sessionId) {
  if (!sessionId) return items
  const liveTools = messageStore.toolCallsBySession.get(sessionId) || []
  if (liveTools.length === 0) return items

  // Collect tool_use_ids already referenced by any displayed bubble — including tool_uses
  // that live on a merged-away continuation segment (Issue #1746: mergeConsecutiveAssistantTurns
  // stamps those onto item.mergedMessages rather than leaving them as top-level items).
  const referenced = new Set()
  for (const item of items) {
    if (item.type !== 'message') continue
    const msg = item.message
    if (msg.type === 'assistant') {
      for (const t of msg.metadata?.tool_uses || []) referenced.add(t.id)
      for (const t of item.attachedTools || []) referenced.add(t.id)
    }
    for (const seg of item.mergedMessages || []) {
      for (const t of seg.metadata?.tool_uses || []) referenced.add(t.id)
      for (const t of seg.attachedTools || []) referenced.add(t.id)
    }
  }

  // Find permission_required tools not yet referenced.
  const orphans = []
  for (const tc of liveTools) {
    if (referenced.has(tc.id)) continue
    if (getEffectiveStatusForTool(tc) !== 'permission_required') continue
    orphans.push({ id: tc.id, name: tc.name, input: tc.input, messageId: tc.messageId || null })
  }
  if (orphans.length === 0) return items

  // Anchor by message_id first; collect anything that can't be resolved that way.
  const unanchored = []
  for (const orphan of orphans) {
    if (!orphan.messageId) {
      unanchored.push(orphan)
      continue
    }
    const targetIndex = items.findIndex(
      it => it.type === 'message' && it.message?.type === 'assistant' && it.message.message_id === orphan.messageId
    )
    if (targetIndex !== -1) {
      items[targetIndex].orphanedPermissionTools = [...(items[targetIndex].orphanedPermissionTools || []), orphan]
      continue
    }

    // Issue #1746: the owning message may have been merged into a preceding row as a
    // continuation segment — search item.mergedMessages before giving up on message_id anchoring.
    let attachedToSegment = false
    for (const it of items) {
      if (it.type !== 'message' || !it.mergedMessages) continue
      const seg = it.mergedMessages.find(s => s.message_id === orphan.messageId)
      if (seg) {
        seg.orphanedPermissionTools = [...(seg.orphanedPermissionTools || []), orphan]
        attachedToSegment = true
        break
      }
    }
    if (!attachedToSegment) unanchored.push(orphan)
  }

  if (unanchored.length === 0) return items

  // Fallback: attach to the last assistant item — and, if it's the head of a merged run
  // (Issue #1746), to that run's LAST segment specifically, since "last bubble" means the
  // most recently rendered turn, not the first segment the top-level item happens to be.
  for (let i = items.length - 1; i >= 0; i--) {
    if (items[i].type === 'message' && items[i].message?.type === 'assistant') {
      const mergedMessages = items[i].mergedMessages
      if (mergedMessages && mergedMessages.length > 0) {
        const lastSeg = mergedMessages[mergedMessages.length - 1]
        lastSeg.orphanedPermissionTools = [...(lastSeg.orphanedPermissionTools || []), ...unanchored]
      } else {
        items[i].orphanedPermissionTools = [...(items[i].orphanedPermissionTools || []), ...unanchored]
      }
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
    orphanedPermissionTools: unanchored,
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
    if (['task_started', 'task_progress', 'task_notification', 'task_updated', 'thinking_tokens'].includes(taskSubtype)) {
      return false
    }
  }

  // Issue #1676: Suppress background subagent notifications from the inline message
  // list — they render exclusively in the session-level AgentNotificationStrip.
  if (message.type === 'system' && message.metadata?.subtype === 'agent_notification') {
    return false
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
    // Issue #1724: Hide skill re-invocation notice (synthetic bookkeeping shown when
    // a skill's instructions were already loaded earlier in the conversation)
    if (content.startsWith('(Re-invocation of /') && content.includes('previously loaded')) {
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

  // Issue #1671: Hide assistant messages forwarded from a subagent (CLAUDE_CODE_FORWARD_SUBAGENT_TEXT).
  // These carry parent_tool_use_id and would otherwise appear as stray top-level bubbles;
  // subagent activity already surfaces via the Task tool card.
  if (message.type === 'assistant') {
    const metadata = message.metadata || {}
    if (metadata.parent_tool_use_id) {
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
  /* Issue #1746 (stage: subagents) follow-up: reserve space for the persistent global gutter
     (26px lane + 4px gap), which is absolutely positioned against this element — position:
     relative here is what makes its height:100% resolve against this box's own
     normal-flow-established height rather than some further ancestor's. */
  position: relative;
  padding-left: 30px;
}

/* Issue #1748 (stage: offset-model): a normal-flow box sized to the virtualizer's own
   getTotalSize() — this IS the "sized spacer" the virtual rows below position themselves
   against via translateY. Its real (non-absolute) height is what keeps TruncationBanner/
   DeferredToolBanner (siblings after it in the template) sitting exactly where they did before,
   and what keeps .messages-area's scrollHeight/scrollTop math valid unchanged. */
.virtual-spacer {
  position: relative;
  width: 100%;
}

.virtual-item-row {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
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

/* Issue #1746 (stage: subagents) follow-up: main-timeline subagent signals (pushed-to-main,
   leg-terminal) — same horizontal rhythm as a regular message row. */
.subagent-signal-wrapper {
  padding: 2px 16px;
}

@media (max-width: 768px) {
  .subagent-signal-wrapper {
    padding: 2px 12px;
  }
}
</style>
