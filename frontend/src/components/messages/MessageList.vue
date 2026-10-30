<template>
  <div class="messages-area-wrapper flex-grow-1" :style="{ '--history-banner-height': `${historyBannerHeight}px` }">
    <!-- Issue #2030 (stage 1b): the list opens on the newest window; older history backfills in
         the background. Lives OUTSIDE the scroll container so its appearance/disappearance can
         never shift the virtualizer's scroll coordinates. -->
    <div
      v-if="historyBanner"
      class="history-banner d-flex align-items-center gap-2 px-3 py-1 small"
      :class="{ 'history-banner-error': historyBanner.kind === 'error' || historyBanner.kind === 'jump-error' }"
      role="status"
      data-testid="history-banner"
      ref="historyBannerEl"
    >
      <span v-if="historyBanner.kind === 'loading' || historyBanner.kind === 'jump'" class="spinner-border spinner-border-sm flex-shrink-0" aria-hidden="true"></span>
      <span class="flex-grow-1">{{ historyBanner.text }}</span>
      <button v-if="historyBanner.kind === 'manual'" class="btn btn-sm btn-outline-secondary py-0" data-testid="history-load-older" @click="loadOlderHistory">Load older history</button>
      <button v-if="historyBanner.kind === 'manual'" class="btn btn-sm btn-outline-secondary py-0" data-testid="history-load-full" @click="loadFullHistory">Load full history</button>
      <button v-if="historyBanner.kind === 'jump'" class="btn btn-sm btn-outline-secondary py-0" data-testid="history-jump-cancel" @click="cancelTurnJump">Cancel</button>
      <button v-if="historyBanner.kind === 'error'" class="btn btn-sm btn-outline-danger py-0" @click="historyBackfill.retry(viewSessionId)">Retry</button>
    </div>
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
          @jump-launch="jumpToLaunch"
        />

        <!-- Resync stall banner (issue #2140): independent of hydrationStage/displayableItems
             — a resync happens to an already-ready, already-populated session. Only shown once
             a resync has actually timed out or thrown (see resyncStall's own comment). -->
        <div
          v-if="resyncStall.active"
          class="alert alert-danger mb-0 py-2 px-3 small d-flex align-items-center gap-2"
          role="alert"
        >
          <span class="flex-shrink-0">⚠</span>
          <span>Resync stalled{{ resyncStall.terminal ? ' — gave up automatically' : ', retrying...' }}</span>
          <button class="btn btn-sm btn-outline-danger ms-auto" @click="pollingStore.retryResync(viewSessionId)">Retry</button>
        </div>

        <div v-if="displayableItems.length === 0 && isHydrationLoading" class="text-muted text-center py-5">
          <div class="spinner-border spinner-border-sm mb-2" role="status"></div>
          <div>{{ hydrationStageLabel }}</div>
        </div>
        <div v-else-if="displayableItems.length === 0 && hydrationStage === 'error'" class="text-center py-5">
          <div class="text-danger mb-2">⚠ {{ hydrationErrorMessage }}</div>
          <button class="btn btn-sm btn-outline-danger" @click="sessionStore.retryHydration(viewSessionId)">Retry</button>
        </div>
        <div v-else-if="displayableItems.length === 0" class="text-muted text-center py-5">
          No messages yet. Start a conversation!
        </div>

        <!-- Issue #1748 (stage: windowing): virtualizer-driven rendering. Item TYPE dispatch
             (MessageItem/CompactionEventGroup/date-separator/SubagentAnchorRow) is unchanged from
             before — only "which indices get a real DOM node right now" is new. `overscan` (see
             virtualizerOptions below) is now a real, small value, so rows outside the viewport
             (plus the overscan buffer) genuinely unmount — the perf win this whole issue exists
             to deliver. -->
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
              :isMessageIdContinuation="!!row.item.isMessageIdContinuation"
              :hasMessageIdContinuationFollowing="!!row.item.hasMessageIdContinuationFollowing"
            />

            <!-- Compaction event group -->
            <CompactionEventGroup
              v-else-if="row.item.type === 'compaction'"
              :messages="row.item.messages"
              :group-key="row.item.groupKey"
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

            <!-- Issue #2030 (stage 3 PR 3C-1): the not-loaded stretch between a jumped-to island and the
                 live end of the history. -->
            <HistoryGapRow
              v-else-if="row.item.type === 'gap'"
              :approx-bytes="row.item.approxBytes"
              :loading="islandLoading === 'after'"
              :error="islandError"
              :full="islandIsFull(currentIsland)"
              @load-newer="loadNewerHistory"
              @jump-latest="jumpToLatest"
            />

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

        <!-- Issue #1955: cosmetic-only live-typing preview, rendered as a normal-flow sibling
             AFTER the virtualizer's tracked rows — same pattern as TruncationBanner/
             DeferredToolBanner below, never a tracked virtual row itself. -->
        <StreamingPreview :sessionId="viewSessionId" />

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

    <!-- In-app message search (issue #2012) — outside scroll container, top-right corner -->
    <MessageSearchBar
      :search="messageSearch"
      @navigate="scrollToMessageMatch"
    />
  </div>
</template>

<script setup>
import { computed, inject, onActivated, onDeactivated, onMounted, onUnmounted, provide, ref, watch, nextTick } from 'vue'
import { useVirtualizer } from '@tanstack/vue-virtual'
import { useMessageStore } from '@/stores/message'
import { useSessionStore } from '@/stores/session'
import { useUIStore } from '@/stores/ui'
import { usePollingStore } from '@/stores/polling'
import MessageItem from './MessageItem.vue'
import CompactionEventGroup from './CompactionEventGroup.vue'
import StreamingPreview from './StreamingPreview.vue'
import TruncationBanner from './TruncationBanner.vue'
import DeferredToolBanner from './DeferredToolBanner.vue'
import SubagentAnchorRow from './SubagentAnchorRow.vue'
import SubagentGlobalGutter from './SubagentGlobalGutter.vue'
import MessageSearchBar from './MessageSearchBar.vue'
import HistoryGapRow from './HistoryGapRow.vue'
import { useTTSReadAloud } from '@/composables/useTTSReadAloud'
import { useMessageSearch } from '@/composables/useMessageSearch'
import { resolveMessageId } from '@/search/messageSearch'
import { applyHighlight, clearHighlight } from '@/search/highlightMatch'
import { useVirtualNavigation, forceFreshMeasurements } from '@/composables/useVirtualNavigation'
import { computeItemKeys } from '@/utils/displayableItems'
import { buildLoadedRows } from '@/utils/loadedRows'
import { historyBackfill } from '@/composables/useHistoryBackfill'
import { useHydrationStage } from '@/composables/useHydrationStage'
import { HYDRATION_STAGE_LABELS } from '@/utils/hydrationStage'
import { cursorOffset, formatJumpSize, jumpNeedsConfirm, planTurnStep } from '@/utils/turnNavigation'
import { parseTimestamp } from '@/utils/time'
import { AROUND_WINDOW_FILE_BYTES, islandIsFull } from '@/utils/historyIntervals'

const messageStore = useMessageStore()
const sessionStore = useSessionStore()
const uiStore = useUIStore()
const pollingStore = usePollingStore()

// Injected per-instance session id (provided by SessionView).
// Every computed in this component reads from this id, not the global currentSessionId,
// so cached instances under KeepAlive never display another session's data.
const viewSessionId = inject('viewSessionId', ref(null))

// Issue #2035: session data-hydration stage visibility for the empty-state area.
const { hydrationStage, isHydrationLoading, hydrationErrorMessage, resyncStall } = useHydrationStage(viewSessionId)
const hydrationStageLabel = computed(() => HYDRATION_STAGE_LABELS[hydrationStage.value] || '')

const messagesArea = ref(null)
const messagesContent = ref(null)
const isProgrammaticScroll = ref(false)
const isInitialLoad = ref(false)
const stickyToBottom = ref(true)
const STICKY_THRESHOLD_PX = 24

// Issue #2100: a 'scroll' event alone doesn't tell us whether the user actually caused it —
// browser scroll anchoring fires a genuine native 'scroll' event when the virtualizer corrects
// a row's estimated height to its real rendered size, with zero user input. userGestureActive
// gates onScroll()'s sticky-state recompute on "was there a real user input gesture recently."
// GESTURE_WINDOW_MS: discrete gestures (wheel/keydown/mousedown-mouseup) either cause the
// resulting scroll in the same/next frame or keep re-firing for as long as the gesture
// continues, so a short fixed window renewed on every event is enough — generous relative to a
// frame budget, tight enough to expire once the gesture truly stops.
// TOUCH_MOMENTUM_CAP_MS: touch is different — after touchend, native momentum scrolling keeps
// producing 'scroll' events with no further JS gesture events, for a duration that varies by
// flick strength. The window is kept open and renewed by GESTURE_WINDOW_MS on every subsequent
// scroll event (tracking actual momentum instead of guessing its duration), bounded by this hard
// cap so a scroll long after momentum has settled doesn't get misattributed.
const GESTURE_WINDOW_MS = 250
const TOUCH_MOMENTUM_CAP_MS = 2000
const userGestureActive = ref(false)
let gestureExpiryTimer = null
let touchDown = false
let touchMoved = false // whether a touchmove occurred since the current touchstart
let touchMomentumDeadline = null // timestamp (ms); set at touchend, cleared at touchstart/expiry

// Per-instance message and tool-call sources derived from the injected session id.
// (Stage 3 PR 3C-1: the loaded* getters return the live suffix's own arrays unless a jumped-to island is loaded.)
const sessionMessages = computed(() => messageStore.loadedMessages(viewSessionId.value))
const sessionToolCalls = computed(() => messageStore.loadedToolCalls(viewSessionId.value))
const currentIsland = computed(() => messageStore.getIsland(viewSessionId.value))

// TTS Read Aloud (issue #735)
const tts = useTTSReadAloud()
provide('ttsReadAloud', tts)

// In-app message search (issue #2012)
const SLOW_LINK_SEARCH_PAGE = 20
// Smaller server search pages on a slow link (nothing there loads more than it must).
const messageSearch = useMessageSearch(viewSessionId, {
  messageStore,
  getPageLimit: () => (historyBackfill.getMode(viewSessionId.value) === 'manual' ? SLOW_LINK_SEARCH_PAGE : undefined),
})

// Provide all messages for play-from-here navigation
provide('allMessages', sessionMessages)

// Pure pipeline lives in utils/displayableItems.js (issue #2030 stage 1b). The ctx is built
// inside the computed so every reactive read stays tracked by it.
const displayableItems = computed(() => buildLoadedRows(messageStore, viewSessionId.value))

// Unique, identity-based key per row (see computeItemKeys).
const itemKeys = computed(() => computeItemKeys(displayableItems.value))

function itemAt(index) {
  return displayableItems.value[index]
}

// Issue #1748 (stage: windowing): per-item-TYPE size estimates (not one flat global average),
// per plan §5.4 — bounds a virtualizer's pre-measurement error to "this item vs. its own type's
// typical height" rather than "any row vs. any other row." Values are rough starting points, not
// yet tuned against real large-session height distributions (§5.4 defers that to a follow-up
// empirical pass). These matter much more now than in stage 1: at a real overscan value, most of
// a large session's items are never mounted, so their layout position is driven by these
// estimates (corrected the instant the user actually scrolls a region into view) rather than by
// a brief pre-ResizeObserver window.
const ITEM_TYPE_SIZE_ESTIMATE = {
  message: 160,
  compaction: 120,
  date_separator: 40,
  subagent_signal: 90,
}
function estimateItemSize(index) {
  return ITEM_TYPE_SIZE_ESTIMATE[itemAt(index)?.type] ?? 160
}

// Issue #1748 (stage: windowing): virtualizer driving MessageList's own rendering AND
// (via laneOffsets/virtualNav below) SubagentGlobalGutter's lane positions and the shared
// jump-navigation helper. `overscan` is a real, small value now — verified against
// @tanstack/virtual-core's actual source (defaultRangeExtractor clamps
// `[start - overscan, end + overscan]` to `[0, count-1]`), so rows genuinely outside
// [start-OVERSCAN_ROWS, end+OVERSCAN_ROWS] unmount, which is the whole point of this stage:
// DOM node count for the message list stays bounded regardless of session size instead of
// growing with it. See OVERSCAN_ROWS below for the value chosen and why.
//
// OVERSCAN_ROWS: a typical viewport in this app shows roughly 4-8 rows at once (rows vary
// widely in height — a date separator is ~40px, a large merged assistant run with tool calls
// can be 500px+ — so "rows per viewport" is itself an estimate, not a fixed number). 10 extra
// rows mounted above AND below the visible range is chosen as a buffer generous enough that:
// (a) a normal wheel/trackpad scroll doesn't visibly pop content in/out at the viewport edge
// before the next measurement pass catches up, and (b) the two-phase jump-navigation pattern
// (plan §5.4/§5.5 — jump to estimate, wait for real mount, then correct) usually lands close
// enough to its target that only a small number of rows need to mount before the target itself
// does. This is deliberately not tuned against real large-session height/scroll-velocity data
// (that empirical pass is future work per §5.4) — it's a reasonable starting buffer, not a
// number with a specific derivation.
const OVERSCAN_ROWS = 10
const rowVirtualizer = useVirtualizer(computed(() => ({
  count: displayableItems.value.length,
  getScrollElement: () => messagesArea.value,
  estimateSize: estimateItemSize,
  overscan: OVERSCAN_ROWS,
  // Issue #2030 (stage 1b): stable row identity so a backfill prepend keeps the measurement
  // cache attached to the right rows (index keys would shift every cached height), and
  // anchorTo:'end' makes the virtualizer preserve the on-screen anchor row across a prepend
  // (it resolves the first visible row's key at its new index in the same render pass).
  getItemKey: (index) => itemKeys.value[index] ?? `i:${index}`,
  anchorTo: 'end',
  // Fires on every virtualizer measurement change — in particular, a mounted tail row's own
  // height changing (streaming growth) while no new item was added, which today's whole-container
  // ResizeObserver caught incidentally. This is the explicit wiring point plan §7 calls out as
  // easy to silently regress without a dedicated test (see MessageList.test.js).
  onChange: scheduleStickyScroll,
})))

const virtualNav = useVirtualNavigation(rowVirtualizer)

// Pairs each virtual row with its displayableItems entry ONCE per render, instead of the template
// re-indexing displayableItems on every v-if branch and prop binding for the same row (up to 8
// lookups/row otherwise, across however many rows are currently mounted).
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
  const messageIdIndex = new Map() // message id (incl. merged segments) -> displayableItems index

  displayableItems.value.forEach((item, index) => {
    if (item.type === 'message') {
      for (const t of item.message?.metadata?.tool_uses || []) toolUseIndex.set(t.id, index)
      for (const t of item.attachedTools || []) toolUseIndex.set(t.id, index)
      const msgId = resolveMessageId(item.message)
      if (msgId != null) messageIdIndex.set(msgId, index)
      for (const seg of item.mergedMessages || []) {
        for (const t of seg.metadata?.tool_uses || []) toolUseIndex.set(t.id, index)
        for (const t of seg.attachedTools || []) toolUseIndex.set(t.id, index)
        const segId = resolveMessageId(seg)
        if (segId != null) messageIdIndex.set(segId, index)
      }
    } else if (item.type === 'subagent_signal' && item.domId) {
      signalIndex.set(item.domId, index)
    }
  })

  return { toolUseIndex, signalIndex, messageIdIndex }
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

// Issue #1848: turn-boundary indices for the status bar's prev/next jump buttons. A "turn
// boundary" is any displayableItems entry whose underlying message is user-authored — this
// already covers both real user messages and inbound comms (comms are delivered as user-role
// messages carrying metadata.comm, see UserMessage.vue's isComm check), and it runs over the
// same post-filter array everything else here reads, so hidden items (e.g. subagent-prompt
// user messages, already filtered out by shouldDisplayMessage) are automatically excluded.
const turnBoundaryIndices = computed(() => {
  const arr = []
  displayableItems.value.forEach((item, index) => {
    if (item.type === 'message' && item.message?.type === 'user') arr.push(index)
  })
  return arr
})

// Current on-screen range, derived from renderedRows (not a second getVirtualItems() call) —
// same mounted-rows source, reactive to scroll for free.
const visibleIndexRange = computed(() => {
  const rows = renderedRows.value
  if (!rows.length) return null
  return { top: rows[0].virtualRow.index, bottom: rows[rows.length - 1].virtualRow.index }
})

// Issue #2030 (stage 1b): history banner + scroll-up sentinel. Everything here reads the same
// "history complete" flag the gated features (turn navigation, PreCompact indicators, search) read.
const historyStatus = computed(() => historyBackfill.statusBySession.value.get(viewSessionId.value) || null)
const historyBanner = computed(() => {
  const sessionId = viewSessionId.value
  if (!sessionId || messageStore.isHistoryComplete(sessionId)) return null
  const status = historyStatus.value
  const fraction = messageStore.historyProgress(sessionId)?.fraction
  const percent = fraction != null ? ` ${Math.round(fraction * 100)} %` : ''
  if (status?.error) return { kind: 'error', text: `Couldn't load older history: ${status.error}` }
  if (status?.jumpError) return { kind: 'jump-error', text: status.jumpError }
  if (status?.jump) {
    return { kind: 'jump', text: `Loading ${status.jump.label || `turn ${status.jump.ordinal}`}…` }
  }
  if (status?.mode === 'manual') {
    return { kind: 'manual', text: `Showing newest messages — older history isn't loaded automatically on this connection.${percent && ` (${percent.trim()} loaded)`}` }
  }
  return { kind: 'loading', text: `Showing newest messages — loading older history…${percent}${status?.paused ? ' (paused)' : ''}` }
})

// The floating search bar sits below the banner: publish the banner's rendered height as a CSS
// variable (it wraps to several lines on a phone).
const historyBannerEl = ref(null)
const historyBannerHeight = ref(0)
let bannerObserver = null
watch(historyBannerEl, (el) => {
  bannerObserver?.disconnect()
  bannerObserver = null
  if (!el) { historyBannerHeight.value = 0; return }
  const update = () => { historyBannerHeight.value = el.offsetHeight }
  update()
  if (typeof ResizeObserver !== 'undefined') {
    bannerObserver = new ResizeObserver(update)
    bannerObserver.observe(el)
  }
}, { flush: 'post' })
onUnmounted(() => bannerObserver?.disconnect())

// Island state for the gap row (issue #2030 stage 3 PR 3C-1)
const islandLoading = computed(() => historyStatus.value?.island?.loading || null)
const islandError = computed(() => historyStatus.value?.island?.error || null)

function loadNewerHistory() {
  historyBackfill.requestIslandNewer(viewSessionId.value)
}

// "Jump to latest": scroll to the live end; the island stays loaded until the next jump.
function jumpToLatest() {
  stickyToBottom.value = true
  uiStore.setStickyToBottom(viewSessionId.value, true)
  scrollToBottomViaVirtualizer()
}

function loadOlderHistory() {
  historyBackfill.requestOlder(viewSessionId.value)
}

// The status bar owns the jump (confirm guard, scroll once loaded); the banner only cancels it.
function cancelTurnJump() {
  historyBackfill.cancelJump(viewSessionId.value)
}

function loadFullHistory() {
  if (typeof window !== 'undefined' && typeof window.confirm === 'function' &&
      !window.confirm('Load the full conversation history? On a slow or metered connection this can use a lot of data.')) {
    return
  }
  historyBackfill.loadAll(viewSessionId.value)
}

// Scrolling within one screen of the loaded top requests the next older page immediately (it
// bypasses the inter-page gap, not the pause-while-live-catching-up rule). Also tells the
// scheduler whether a gesture is active so a prepend never lands mid-gesture.
function reportHistoryScrollState() {
  const sessionId = viewSessionId.value
  const el = messagesArea.value
  const range = visibleIndexRange.value
  if (!sessionId || !el || !range) return
  const nearTop = range.top <= 3 || el.scrollTop < el.clientHeight
  historyBackfill.setScrollInteraction(sessionId, { gestureActive: userGestureActive.value, nearTop })
  // Slow-link mode promises no unrequested loading: there, only a real user gesture near the top
  // asks for the (single) next page — a short list must not keep re-requesting by itself.
  const manual = historyStatus.value?.mode === 'manual'
  if (nearTop && (!manual || userGestureActive.value) && !isInitialLoad.value && !messageStore.isHistoryComplete(sessionId)) {
    // The topmost interval pages upward: the island when one is loaded (the suffix keeps its own
    // background backfill), the suffix otherwise.
    const island = currentIsland.value
    if (!island) historyBackfill.requestOlder(sessionId)
    else if (island.hasMoreBefore) historyBackfill.requestIslandOlder(sessionId)
  }
}
watch(visibleIndexRange, reportHistoryScrollState)
watch(userGestureActive, reportHistoryScrollState)

function findPrevTurnIndex() {
  const range = visibleIndexRange.value
  if (!range) return null
  const arr = turnBoundaryIndices.value
  for (let i = arr.length - 1; i >= 0; i--) {
    if (arr[i] < range.top) return arr[i]
  }
  return null
}

// Row index of a LOADED turn by message id (a user row), or null.
function rowOfTurn(messageId) {
  const index = indexMaps.value.messageIdIndex.get(messageId)
  const item = index != null ? displayableItems.value[index] : null
  return item?.type === 'message' && item.message?.type === 'user' ? index : null
}

// Issue #2030 (stage 2 PR B, stage 3 PR 3C-1): previous / next turn over the WHOLE history. A turn
// in the same loaded interval as the viewport edge is a displayable row; otherwise the neighbour
// comes from the server history index — it may be unloaded (a jump opens a window around it) or
// the adjacent interval's edge turn. Result:
//   { kind: 'loaded', index }                                    a displayable row to scroll to
//   { kind: 'unloaded', cursor, messageId, ordinal, sizeHint }   needs a window around it first
//   { kind: 'pending', reason: 'idle'|'loading'|'unavailable' }  no usable index (yet)
//   null                                                         no such turn
function planTurn(direction) {
  const range = visibleIndexRange.value
  if (!range) return null
  const sessionId = viewSessionId.value
  const history = messageStore.getHistoryState(sessionId)
  const island = currentIsland.value
  const entry = history && !messageStore.isHistoryComplete(sessionId) ? messageStore.getHistoryIndex(sessionId) : null
  const ready = entry?.status === 'ready'
  // Across a gap the rows actually on screen decide which interval the viewport is in.
  let viewTop = range.top
  let viewBottom = range.bottom
  if (island) {
    const virtualizer = rowVirtualizer.value
    const offset = virtualizer.scrollOffset ?? 0
    viewTop = virtualizer.getVirtualItemForOffset(offset)?.index ?? range.top
    viewBottom = virtualizer.getVirtualItemForOffset(offset + (messagesArea.value?.clientHeight || 0))?.index ?? range.bottom
  }
  return planTurnStep(direction, {
    boundaries: turnBoundaryIndices.value,
    gapIndex: island ? displayableItems.value.findIndex(item => item.type === 'gap') : -1,
    top: range.top,
    bottom: range.bottom,
    viewTop,
    viewBottom,
    turns: ready ? entry.turns : null,
    indexStatus: entry?.status || null,
    complete: messageStore.isHistoryComplete(sessionId),
    suffixStartOffset: cursorOffset(history?.startCursor),
    islandStartOffset: island ? cursorOffset(island.startCursor) : null,
    islandEndOffset: island ? cursorOffset(island.endCursor) : null,
    rowOfTurn,
  })
}

function findNextTurnIndex() {
  const range = visibleIndexRange.value
  if (!range) return null
  const arr = turnBoundaryIndices.value
  for (let i = 0; i < arr.length; i++) {
    if (arr[i] > range.bottom) return arr[i]
  }
  return null
}

const findPrevTurn = () => planTurn('prev')
const findNextTurn = () => planTurn('next')

// A turn jump to unloaded history is a deliberate navigation away from the bottom: drop the
// sticky-to-bottom pin up front (otherwise the prepended page's relayout snaps the list back to
// the bottom before the jump lands) and re-derive it from the real position when the jump ends.
let turnJumpsOpen = 0
function beginTurnJump() {
  turnJumpsOpen++
  renewGestureWindow()
  stickyToBottom.value = false
  uiStore.setStickyToBottom(viewSessionId.value, false)
}

function endTurnJump() {
  // Jumps can overlap (a newer one supersedes an older one): re-derive the sticky state only when the last one ends.
  if (turnJumpsOpen > 0) turnJumpsOpen--
  if (turnJumpsOpen > 0) return
  if (messagesArea.value) recomputeStickyFromScroll(messagesArea.value)
}

// Scroll to a loaded turn by message id (after a jump loaded it). Resolves false when the row
// isn't (or is no longer) in the list. The jump just prepended a page: let the list and the
// virtualizer's count/keys catch up first, or the scroll offset is computed for the old rows.
async function scrollToTurnMessage(messageId) {
  await nextTick()
  await new Promise(resolve => requestAnimationFrame(resolve))
  const index = displayableItems.value.findIndex(
    item => item.type === 'message' && item.message?.type === 'user' && resolveMessageId(item.message) === messageId
  )
  if (index < 0) return false
  return scrollToItemIndexTrusted(index, { align: 'start', behavior: 'auto' })
}

// Issue #1748 (stage: windowing) follow-up (user feedback): a row is the virtualization
// UNIT, but a launch/terminal anchor can sit anywhere inside its row (e.g. a Task call below a
// Thinking block, or partway down a merged multi-turn run) — using the row's own start/end
// alone visibly misplaced the gutter chip at the row's edge instead of the actual anchor.
// When the row happens to be mounted (within the overscan range, or recently visited), the real
// DOM position of the anchor WITHIN its row is measurable — a small, cheap delta between two
// elements that scroll together (not the old full-container getBoundingClientRect() math), not
// the O(session size) "no DOM at all" ideal Section 5.2 describes for content that might not be
// mounted. Falls back to the row's own bounds when the anchor/row isn't found (now the common
// case for any leg outside the mounted window) — same graceful-degradation shape as everywhere
// else here, and exactly the estimate-until-visited tolerance plan §5.4 designs for.
function anchorOffsetWithinRow(rowIndex, anchorDomId) {
  if (!messagesContent.value || !anchorDomId) return null
  const rowEl = messagesContent.value.querySelector(`.virtual-item-row[data-index="${rowIndex}"]`)
  const anchorEl = document.getElementById(anchorDomId)
  if (!rowEl || !anchorEl) return null
  const rowTop = rowEl.getBoundingClientRect().top
  const anchorRect = anchorEl.getBoundingClientRect()
  return { top: anchorRect.top - rowTop, bottom: anchorRect.bottom - rowTop }
}

// Issue #2030 (stage 3 PR 3C-2): the loaded intervals' row ranges and time spans, oldest first —
// `[{ start, end, first, last }]` (row indices, ms timestamps). A `gap` row separates an island from
// the suffix. Only read when a lane's launch row is not loaded.
const intervalSpans = computed(() => {
  const items = displayableItems.value
  const gapAt = items.findIndex(item => item.type === 'gap')
  const ranges = gapAt >= 0 ? [[0, gapAt - 1], [gapAt + 1, items.length - 1]] : [[0, items.length - 1]]
  const spans = []
  for (const [start, end] of ranges) {
    let first = null
    let last = null
    for (let i = start; i <= end; i++) {
      const item = items[i]
      const ts = item.type === 'compaction' ? item.messages[0]?.timestamp : item.type === 'message' ? item.message?.timestamp : null
      if (ts == null) continue
      const ms = parseTimestamp(ts).getTime()
      if (first == null) first = ms
      last = ms
    }
    if (first != null) spans.push({ start, end, first, last })
  }
  return spans
})

// First row of the first loaded interval whose time span overlaps the leg's [started_at, ended_at or
// now]; null when none does (a leg wholly before, between or after the loaded intervals draws no lane).
function clampedLegRow(leg, spans) {
  if (leg.started_at == null) return null
  const from = parseTimestamp(leg.started_at).getTime()
  const to = leg.status === 'running' || leg.ended_at == null ? Infinity : parseTimestamp(leg.ended_at).getTime()
  for (const span of spans) {
    if (from <= span.last && to >= span.first) return span.start
  }
  return null
}

// The lane chip of a leg launched before the loaded intervals: one bounded window around its
// launching message (around_id=<tool_use_id>), through the same island path as every other jump.
// A slow link confirms first (it names the window size); failures surface in the history banner.
async function jumpToLaunch(lane) {
  const sessionId = viewSessionId.value
  const toolUseId = messageStore.getTaskLegEntry(lane.taskId)?.legs?.[lane.legIndex]?.tool_use_id
  if (!sessionId || !toolUseId) return
  if (jumpNeedsConfirm({ slowLink: historyBackfill.getMode(sessionId) === 'manual' }) &&
      typeof window !== 'undefined' && typeof window.confirm === 'function' &&
      !window.confirm(`Jump to the launching message? Loads about ${formatJumpSize(AROUND_WINDOW_FILE_BYTES)} of history`)) {
    return
  }
  beginTurnJump()
  try {
    const jump = historyBackfill.jumpAround(sessionId, { toolUseId }, { label: 'the launching message', expectParent: true })
    await jump.promise
    const index = await launchRowAfterJump(lane)
    if (index != null) await scrollToItemIndexTrusted(index, { align: 'start', behavior: 'auto' })
  } catch {
    // failed (the banner says why), cancelled or superseded: the loaded intervals are left as is
  } finally {
    endTurnJump()
  }
}

// The launch row of a lane once the jump loaded it (after the list and virtualizer caught up).
async function launchRowAfterJump(lane) {
  await nextTick()
  await new Promise(resolve => requestAnimationFrame(resolve))
  return resolveSubagentPrimaryIndex(lane.taskId, lane.legIndex)
}

// Issue #1748 (stage: windowing): SubagentGlobalGutter's lane top/bottom, computed here (not
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

  // Issue #2030 (stage 3 PR 3C-2): while the history is partial, a leg whose launch row is not
  // loaded is clamped to the top of the first loaded interval its activity overlaps (see clampedLegRow).
  const partial = !messageStore.isHistoryComplete(viewSessionId.value)
  let spans = null

  for (const entry of messageStore.allTaskLegEntriesForSession(viewSessionId.value)) {
    entry.legs.forEach((leg, legIndex) => {
      const primaryIndex = indexMaps.value.toolUseIndex.get(leg.tool_use_id)
      const startItem = primaryIndex != null ? measurements[primaryIndex] : null
      if (!startItem) {
        if (!partial || !leg.tool_use_id) return // history complete: matches old `if (!startEl) continue`
        // A launch tool call that IS loaded but has no row (a hidden/filtered message) is not "launched earlier".
        if (messageStore.findLoadedToolCall(viewSessionId.value, leg.tool_use_id)) return
        spans ??= intervalSpans.value
        const clampIndex = clampedLegRow(leg, spans)
        if (clampIndex == null) return // the leg's activity is entirely outside the loaded intervals
        const clampItem = measurements[clampIndex]
        if (!clampItem) return
        const top = clampItem.start
        let bottom
        if (leg.status === 'running') {
          bottom = totalSize
        } else {
          const terminalIndex = indexMaps.value.signalIndex.get(`subagent-anchor-terminal-${entry.task_id}-${legIndex}`)
          const endItem = terminalIndex != null ? measurements[terminalIndex] : null
          bottom = endItem ? Math.max(endItem.end, top + 26) : top + 26 // no terminal row loaded: a short stub
        }
        offsets.set(`${entry.task_id}:${legIndex}`, { top, bottom, height: Math.max(0, bottom - top), launchedEarlier: true })
        return
      }
      const primaryDomId = `subagent-anchor-primary-${entry.task_id}-${legIndex}`
      const primaryDelta = anchorOffsetWithinRow(primaryIndex, primaryDomId)
      const top = startItem.start + (primaryDelta ? primaryDelta.top : 0)

      let bottom
      if (leg.status === 'running') {
        bottom = totalSize
      } else {
        const domId = `subagent-anchor-terminal-${entry.task_id}-${legIndex}`
        const terminalIndex = indexMaps.value.signalIndex.get(domId)
        const endItem = terminalIndex != null ? measurements[terminalIndex] : null
        const terminalDelta = terminalIndex != null ? anchorOffsetWithinRow(terminalIndex, domId) : null
        bottom = terminalDelta
          ? endItem.start + terminalDelta.bottom
          : endItem
            ? endItem.end
            : top + 26 // defensive fallback (terminal row not found)
      }

      offsets.set(`${entry.task_id}:${legIndex}`, { top, bottom, height: Math.max(0, bottom - top) })
    })
  }

  return offsets
})

// Issue #1748 (stage: windowing) follow-up (user feedback): `align: 'auto'` (not 'center')
// means the virtualizer only moves the scroll position when the target isn't already fully
// visible, and aligns to whichever edge it's nearest — clicking a chip that's already on screen
// must not move anything. `block: 'nearest'` (not 'center') gives the DOM fine-correction step
// the same "don't move unless necessary" behavior.
// Issue #1748 (stage: windowing): the big virtualizer-level jump now uses `behavior: 'auto'`
// (instant), not 'smooth' — per plan §5.5, never smooth-scroll through thousands of unrendered
// intermediate rows. That was safe at Stage 1 only because overscan=count kept every row mounted
// (no blank gap to animate through); now that overscan is a real value, a smooth scroll across a
// long unmounted span would visibly animate past mostly-empty spacer space. The final
// `scrollIntoView` correction below stays smooth — by the time it runs the target is confirmed
// mounted, so it's a small, real-content distance where smooth easing reads as intentional.
// Issue #2100: a jump-navigation scroll (gutter chip click, turn jump button, deep-link) is a
// deliberate user-triggered action even though it fires no wheel/touch/keydown/mousedown event
// of its own — without this, the resulting 'scroll' event would be gated out as untrusted, so
// stickyToBottom would never update away from `true`, and the virtualizer's own onChange (which
// drives scheduleStickyScroll) would then auto-scroll straight back to the bottom, fighting the
// jump. Not wrapped in guardProgrammaticScroll instead: that path intentionally SKIPS the sticky
// recompute entirely (trusted as "this component's own bookkeeping write"), whereas a jump needs
// the opposite — a real recompute against wherever it actually landed.
function scrollToItemIndexTrusted(index, opts) {
  renewGestureWindow()
  return virtualNav.scrollToItemIndex(index, opts)
}

async function onGutterChipClick(lane) {
  const index = resolveSubagentPrimaryIndex(lane.taskId, lane.legIndex)
  if (index == null) return
  const mounted = await scrollToItemIndexTrusted(index, { align: 'auto', behavior: 'auto' })
  if (!mounted) return
  document.getElementById(`subagent-anchor-primary-${lane.taskId}-${lane.legIndex}`)
    ?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
}

// Issue #2012: jump to a search match. Mirrors onGutterChipClick's shape — resolve a
// displayableItems index, scroll+confirm-mount via the shared virtual-nav helper, then run the
// DOM highlight pass once mount is confirmed. For a tool match, the tool's detail panel must be
// expanded via the store (same scopeKey resolution ActivityTimeline.vue uses:
// messageId || tools[0]?.id) BEFORE scrolling, since the panel only mounts when expanded — by
// the time scrollToItemIndexTrusted's mount-poll resolves, the now-reactively-triggered detail
// panel has had several nextTick/rAF cycles to mount alongside the row itself.
// The highlight container is the whole mounted row element (not a narrower tool-panel element):
// an expanded TimelineDetail is nested inside its row's DOM subtree, so searching the row's text
// nodes finds a tool-field match there too, without this function needing to separately locate
// the detail panel.
let highlightedEl = null

function clearActiveHighlight() {
  if (highlightedEl) {
    clearHighlight(highlightedEl)
    highlightedEl = null
  }
}

// Issue #2012 (flagged open sub-case): resolve the scopeKey ActivityTimeline.vue's own
// `messageId || tools[0]?.id` computed would land on for the owning message's main timeline.
// Stored message records never carry `.id` (see resolveMessageId above), so that fallback
// always bottoms out at the first MAIN-timeline tool's id in practice — mirrors
// AssistantMessage.vue's `mainTimelineTools` filter (`!isSubagentLaunchAnchor(tc) &&
// tc.name !== 'mcp__legion__send_comm' && !tc.parent_tool_use_id`) via the same three checks,
// since a message with a send_comm or child tool ahead of the matched tool would otherwise
// resolve to a scopeKey ActivityTimeline's own `tools[0]?.id` never lands on, leaving the
// matched tool's detail panel unexpanded even though the match itself is real.
function resolveMainTimelineScopeKey(messageId) {
  const msg = sessionMessages.value.find(m => resolveMessageId(m) === messageId)
  const toolUses = msg?.metadata?.tool_uses || []
  for (const t of toolUses) {
    if (messageStore.getTaskIdForLaunchToolUse(t.id)) continue
    if (t.name === 'mcp__legion__send_comm') continue
    const fullToolCall = messageStore.findLoadedToolCall(viewSessionId.value, t.id)
    if (fullToolCall?.parent_tool_use_id) continue
    return t.id
  }
  return null
}

// Issue #2012: guards against a slower, stale scrollToMessageMatch call (e.g. two rapid
// next()/prev() presses, each jumping to a currently-unmounted row) clobbering a faster, more
// recent call's already-applied highlight once its own await finally resolves.
let navigationSeq = 0

function matchRowIndex({ messageId, toolId }) {
  return toolId != null
    ? indexMaps.value.toolUseIndex.get(toolId)
    : indexMaps.value.messageIdIndex.get(messageId)
}

// Issue #2030 (stage 3 PR 3C-1): a server search hit that is not loaded opens through the same
// jump as turn navigation: ONE bounded `around=<hit cursor>` window (about 0.5 MB of history) that
// becomes the island, with progress banner and cancel; only a slow link asks first (the window
// size is in the question). Resolves 'loaded' | 'declined' | 'cancelled' | 'not-found'.
let activeSearchJump = null

function cancelSearchJump() {
  activeSearchJump?.cancel()
  activeSearchJump = null
}

// Closing the search or switching session abandons a pending search jump (and any navigation in flight).
watch(() => messageSearch.isOpen.value, (open) => {
  if (!open) { navigationSeq++; cancelSearchJump(); stopKeeper() }
})
watch(viewSessionId, () => { navigationSeq++; cancelSearchJump(); stopKeeper() })
watch(() => messageSearch.query.value, () => { stopKeeper(); clearActiveHighlight() })

function isUnloadedHit(match) {
  const sessionId = viewSessionId.value
  return (match.cursor != null || match.messageId != null) &&
    !!messageStore.getHistoryState(sessionId)?.hasMoreBefore &&
    !messageStore.isMessageLoaded(sessionId, match.messageId)
}

// `began` (set once the jump really started, i.e. after the confirm) tells the caller whether it owes an endTurnJump().
async function jumpToUnloadedHit(match, began) {
  const sessionId = viewSessionId.value
  if (jumpNeedsConfirm({ slowLink: historyBackfill.getMode(sessionId) === 'manual' }) &&
      typeof window !== 'undefined' && typeof window.confirm === 'function' &&
      !window.confirm(`Jump to search result? Loads about ${formatJumpSize(AROUND_WINDOW_FILE_BYTES)} of history`)) {
    return 'declined'
  }
  beginTurnJump()
  began.value = true
  const jump = historyBackfill.jumpAround(
    sessionId,
    match.cursor != null ? { cursor: match.cursor } : { messageId: match.messageId },
    { label: 'search result' },
  )
  activeSearchJump = jump
  try {
    await jump.promise
    return messageStore.isMessageLoaded(sessionId, match.messageId) ? 'loaded' : 'not-found'
  } catch (err) {
    return err?.reason === 'not-found' ? 'not-found' : 'cancelled'
  } finally {
    if (activeSearchJump === jump) activeSearchJump = null
  }
}

async function scrollToMessageMatch(match) {
  const seq = ++navigationSeq
  const startedAt = performance.now()
  cancelSearchJump()

  if (!match) {
    clearActiveHighlight()
    return false
  }

  let index = matchRowIndex(match)
  const began = { value: false }
  if (index == null && isUnloadedHit(match)) {
    const outcome = await jumpToUnloadedHit(match, began)
    if (outcome !== 'loaded') {
      if (began.value) endTurnJump()
      if (outcome === 'not-found' && seq === navigationSeq) {
        clearActiveHighlight()
        messageSearch.markNotFound()
      }
      return false
    }
    // The jump just prepended pages and the banner changed height (the list area resizes a few
    // frames later): let the list and the virtualizer settle before resolving the row, or the
    // re-render that follows would drop the highlight applied below.
    await nextTick()
    for (let frame = 0; frame < 4; frame++) await new Promise(resolve => requestAnimationFrame(resolve))
    if (seq !== navigationSeq) { endTurnJump(); return false }
    index = matchRowIndex(match)
  }
  try {
    const landed = await scrollToLoadedMatch(match, index, seq)
    // background: re-applies the highlight if the row re-renders (only for the newest navigation, only when it was applied)
    if (landed && seq === navigationSeq && highlightedEl && lastUserScrollAt < startedAt) keepHighlight(match, seq)
    return landed
  } finally {
    if (began.value) endTurnJump()
  }
}

// The DOM highlight is applied imperatively to the mounted row, so any re-render of that row drops
// it: the virtualizer remounting it after a far jump (rows measured for the first time), or a row
// re-render each time an older history page is prepended around it. While this match stays the
// current one, a keeper repairs it: a MutationObserver re-applies the mark in the same frame, and a
// cheap 250 ms tick brings a row that left the mounted window back and reveals a mark hidden under
// the floating search bar. At most one keeper exists; it ends (observer disconnected, loop exited)
// on a new navigation, closing the search, a session switch, a query edit, the user scrolling by
// hand, unmount/deactivate, or after 30 s.
let activeKeeper = null

function stopKeeper() {
  activeKeeper?.stop()
  activeKeeper = null
}

// Time of the last wheel / touch / mouse / scroll-key input: a keeper never starts for a navigation the user scrolled away from.
let lastUserScrollAt = 0
function noteUserScroll() {
  lastUserScrollAt = performance.now()
  stopKeeper()
}

async function keepHighlight(match, seq) {
  stopKeeper()
  let alive = true
  const container = messagesContent.value
  // Mark of the current match in its mounted row, (re-)applied when missing; null when the row is not mounted.
  const restore = () => {
    if (!alive || seq !== navigationSeq) return null
    if (highlightedEl?.isConnected && highlightedEl.querySelector('mark.message-search-highlight')) return highlightedEl.querySelector('mark.message-search-highlight')
    const index = matchRowIndex(match)
    const rowEl = index == null ? null : messagesContent.value?.querySelector(`.virtual-item-row[data-index="${index}"]`)
    if (!rowEl) return null
    let mark = rowEl.querySelector('mark.message-search-highlight')
    if (!mark) {
      clearActiveHighlight()
      if (!applyHighlight(rowEl, messageSearch.query.value, match.occurrenceIndex ?? 0, { scroll: false })) return null
      highlightedEl = rowEl
      mark = rowEl.querySelector('mark.message-search-highlight')
    }
    return mark
  }
  // A re-render is repaired in the same frame (no visible flicker) ...
  const observer = container && typeof MutationObserver !== 'undefined' ? new MutationObserver(() => { restore() }) : null
  observer?.observe(container, { childList: true, subtree: true, characterData: true })
  const keeper = { stop() { alive = false; observer?.disconnect() } }
  activeKeeper = keeper
  try {
    // ... and a slower tick covers a row that left the mounted window or sits under the bar.
    let revealed = null
    for (let tick = 0; tick < 120 && alive; tick++) {
      await new Promise(resolve => setTimeout(resolve, 250))
      if (!alive || seq !== navigationSeq) return
      const mark = restore()
      if (mark) {
        // layout reads only for a mark that is new (first landing, or re-applied after a re-render)
        if (mark !== revealed) { revealed = mark; revealMark(mark) }
      } else {
        const index = matchRowIndex(match)
        if (index != null) {
          await scrollToItemIndexTrusted(index, { align: 'center', behavior: 'auto' })
        }
      }
    }
  } catch {
    // the list was torn down under the keeper (unmount): nothing left to keep
  } finally {
    keeper.stop()
    if (activeKeeper === keeper) activeKeeper = null
  }
}

// Scrolls a highlighted mark to the middle of the list when it is outside the visible area or under
// the floating search bar.
function revealMark(mark) {
  const area = messagesArea.value
  if (!mark || !area) return
  const areaRect = area.getBoundingClientRect()
  const barBottom = document.querySelector('.message-search-bar')?.getBoundingClientRect().bottom ?? areaRect.top
  const rect = mark.getBoundingClientRect()
  if (rect.top < Math.max(areaRect.top, barBottom) + 8 || rect.bottom > areaRect.bottom) {
    renewGestureWindow() // a trusted scroll, like the other search navigations
    mark.scrollIntoView({ behavior: 'auto', block: 'center' })
  }
}

async function scrollToLoadedMatch(match, index, seq) {
  const { messageId, toolId } = match
  if (index == null) {
    clearActiveHighlight()
    messageSearch.markNotFound()
    return false
  }

  if (toolId != null) {
    const launchTaskId = messageStore.getTaskIdForLaunchToolUse(toolId)
    if (launchTaskId) {
      // This tool IS a subagent's own launch/resume Task call — its prompt/result render
      // inside SubagentLegTranscript's own scroll boxes, one layer deeper than the main
      // ActivityTimeline. Expand the leg transcript itself, not setExpandedTimelineTool
      // (Task tools never render into the main-timeline detail panel that drives).
      // KNOWN LIMITATION: this only reveals the leg CARD — SubagentLegTranscript.vue's own
      // prompt/result sub-panels are gated behind separate, local `ref(true)` collapse state
      // with no external prop/store hook to force open, so a match inside tool_input/
      // tool_result text for a Task launch can still fail to find a highlight target below.
      // That failure is NOT silent: the applyHighlight() check below calls markNotFound()
      // whenever it can't find the substring in the now-mounted DOM, so the UI surfaces
      // "couldn't locate match" instead of falsely reporting success with no visible mark.
      const legEntry = messageStore.getTaskLegEntry(launchTaskId)
      const legIndex = legEntry?.legs.findIndex(leg => leg.tool_use_id === toolId) ?? -1
      if (legIndex !== -1) messageStore.setLegExpanded(launchTaskId, legIndex, true)
    } else {
      const scopeKey = resolveMainTimelineScopeKey(messageId)
      if (scopeKey) messageStore.setExpandedTimelineTool(scopeKey, toolId)
    }
  }

  const mounted = await scrollToItemIndexTrusted(index, { align: 'center', behavior: 'auto' })
  if (seq !== navigationSeq) return mounted // superseded by a newer navigation; don't touch shared highlight state

  clearActiveHighlight()
  if (!mounted) {
    messageSearch.markNotFound()
    return false
  }

  const rowEl = messagesContent.value?.querySelector(`.virtual-item-row[data-index="${index}"]`)
  const highlighted = rowEl && applyHighlight(rowEl, messageSearch.query.value, match.occurrenceIndex ?? 0)
  if (highlighted) {
    highlightedEl = rowEl
  } else {
    // The row mounted but the raw-text highlighter couldn't find the substring in it — e.g.
    // the match is inside a still-collapsed subagent leg prompt/result panel (see comment
    // above), or a markdown-rendering mismatch (see messageSearch.js's Risks notes). Report
    // this honestly rather than leaving the "X of Y" counter implying a highlight that isn't
    // actually visible anywhere on screen.
    messageSearch.markNotFound()
  }
  return mounted
}

defineExpose({
  scrollToItemIndex: scrollToItemIndexTrusted,
  scrollToMessageMatch,
  resolveToolAnchorIndex,
  resolveSubagentPrimaryIndex,
  findPrevTurnIndex,
  findPrevTurn,
  findNextTurn,
  findNextTurnIndex,
  scrollToTurnMessage,
  beginTurnJump,
  endTurnJump,
})


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
      // Issue #1748 (stage: windowing) fix: re-verify `stickyToBottom` fresh at execution time,
      // not just trust it having been true back when this was scheduled. At a real overscan
      // value, rowVirtualizer's onChange (this function's other caller) now fires on nearly
      // every scroll tick that crosses a mounted row's boundary — not just on genuine content
      // growth, as it effectively only did at stage 1's overscan=count. Both the virtualizer's
      // own internal scroll listener and this component's `onScroll` (which recomputes
      // `stickyToBottom`) are attached to the same 'scroll' event; if the virtualizer's listener
      // happens to run first, `scheduleStickyScroll` can be called with a still-stale `true`
      // before `onScroll` corrects it. By the time this deferred (nextTick+rAF) callback runs,
      // though, `onScroll` — a synchronous handler for that same event — has already had its
      // turn, so re-reading the ref here (not a fresh DOM measurement) reflects the correction.
      // NOTE: a raw DOM re-check (`scrollHeight - scrollTop - clientHeight`) was tried first and
      // reverted — it broke the very first scroll-to-bottom on initial load, where scrollTop is
      // legitimately still 0 (nothing has scrolled yet) while scrollHeight already reflects the
      // virtualizer's estimated total size, so the DOM alone can't distinguish "never scrolled
      // down yet" from "user scrolled away" the way the ref (correctly defaulting to `true`) can.
      if (!stickyToBottom.value) return
      scrollToBottomViaVirtualizer()
    })
  })
}


// Issue #2100: opens/renews the userGestureActive window. Shared by every discrete gesture
// (wheel, scroll-relevant keydown, mousedown/mouseup, touchmove) and by touch-momentum renewal
// in onScroll below — all of them just need "stay active, decaying after GESTURE_WINDOW_MS of
// silence." The expiry check defers to `touchDown` so the window never closes while a finger is
// physically still on the screen, even if GESTURE_WINDOW_MS elapses without a touchmove.
function renewGestureWindow() {
  userGestureActive.value = true
  clearTimeout(gestureExpiryTimer)
  gestureExpiryTimer = setTimeout(() => {
    if (touchDown) return
    userGestureActive.value = false
    // touchMomentumDeadline is NOT cleared here — it has its own independent, longer-lived
    // (up to TOUCH_MOMENTUM_CAP_MS) lifecycle, consumed only by onScroll's own momentum check
    // below. Clearing it on this 250ms discrete decay would kill momentum tracking almost
    // immediately after every touchend, defeating its entire purpose.
  }, GESTURE_WINDOW_MS)
}

function isTextEntryTarget(el) {
  if (!el) return false
  if (el.isContentEditable) return true
  return el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT'
}

const SCROLL_RELEVANT_KEYS = new Set(['ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 'End', ' '])

// InputArea.vue is a sibling component on the same page — without this exclusion, typing
// Home/End/Arrow/Space in its textarea (all common text-editing keys) would spuriously open a
// gesture window with no actual scroll happening.
function onGestureKeydown(e) {
  if (!SCROLL_RELEVANT_KEYS.has(e.key)) return
  if (isTextEntryTarget(document.activeElement)) return
  renewGestureWindow()
  noteUserScroll()
}

function onGestureTouchStart() {
  touchDown = true
  touchMoved = false
  touchMomentumDeadline = null
  renewGestureWindow()
}

function onGestureTouchMove() {
  touchMoved = true
  renewGestureWindow()
}

// Only a flick (touchstart -> at least one touchmove -> touchend) gets the momentum-cap grace
// period — a plain tap (e.g. tapping a button inside the message list) never moved, so native
// momentum scrolling can't follow it. Without this check, any tap would open a 2-second window
// in which an unrelated later scroll (e.g. a scroll-anchoring correction) gets misattributed —
// a much wider false-positive surface than this component's other gestures.
function onGestureTouchEnd() {
  touchDown = false
  touchMomentumDeadline = touchMoved ? Date.now() + TOUCH_MOMENTUM_CAP_MS : null
  renewGestureWindow()
}

// Scroll event handler: track user-initiated scroll position and guard programmatic scrolls
function onScroll() {
  if (isProgrammaticScroll.value) return
  // Issue #2100: during the post-touchend momentum phase, there are no further JS gesture
  // events — only the 'scroll' events momentum itself produces. Renew the window from here,
  // bounded by the hard cap, so momentum tracks its own actual duration instead of a guess.
  if (touchMomentumDeadline != null) {
    if (Date.now() < touchMomentumDeadline) {
      renewGestureWindow()
    } else {
      touchMomentumDeadline = null
    }
  }
  if (!userGestureActive.value) return
  // A jump in flight (turn / search hit / launch chip) re-derives the sticky pin once when it ends: a
  // target near the live end is clamped to the bottom while the rows below are still being measured,
  // and that transient position must not re-pin the list to the bottom when they grow.
  if (turnJumpsOpen > 0) return
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
let lastSeenTailId = null
watch(() => sessionMessages.value.length, (newLen) => {
  // Issue #2030: a backfilled older page grows the list at the HEAD — the tail is unchanged and
  // nothing new should be read aloud.
  const tailId = sessionMessages.value[newLen - 1]?.message_id ?? null
  const tailUnchanged = tailId !== null && tailId === lastSeenTailId
  lastSeenTailId = tailId
  if (tailUnchanged) {
    lastSeenMessageCount.value = newLen
    return
  }
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

// Issue #2100: gesture listeners are scoped to this component's own scroll container (not
// window), so a gesture over an unrelated page region (e.g. scrolling the sidebar) doesn't
// falsely open a window here. keydown is the one exception — keyboard scroll can fire while
// focus is on document.body, not necessarily on .messages-area itself — so it's attached to
// window and relies on onGestureKeydown's own activeElement/key filtering. Attached once per
// real mount (onMounted/onUnmounted, not onActivated/onDeactivated) since KeepAlive preserves
// this component instance across activate/deactivate cycles — matching how the virtualizer's
// own measurement cache already persists across those cycles (see onDeactivated's comment).
onMounted(() => {
  const el = messagesArea.value
  if (el) {
    // wheel/mousedown/mouseup need no logic beyond "renew the window" — registered directly.
    // mousedown/mouseup cover scrollbar-thumb drag and scrollbar-track clicks, which fire
    // 'scroll' events but no wheel/touchmove events — needed so "manually scrolling via any
    // input method" (issue acceptance criteria) also covers dragging the native scrollbar.
    el.addEventListener('wheel', renewGestureWindow, { passive: true })
    el.addEventListener('touchstart', onGestureTouchStart, { passive: true })
    el.addEventListener('touchmove', onGestureTouchMove, { passive: true })
    el.addEventListener('touchend', onGestureTouchEnd, { passive: true })
    el.addEventListener('mousedown', renewGestureWindow, { passive: true })
    el.addEventListener('mouseup', renewGestureWindow, { passive: true })
    for (const type of ['wheel', 'touchstart', 'mousedown']) el.addEventListener(type, noteUserScroll, { passive: true })
  }
  window.addEventListener('keydown', onGestureKeydown)
})

onUnmounted(() => {
  const el = messagesArea.value
  if (el) {
    el.removeEventListener('wheel', renewGestureWindow)
    el.removeEventListener('touchstart', onGestureTouchStart)
    el.removeEventListener('touchmove', onGestureTouchMove)
    el.removeEventListener('touchend', onGestureTouchEnd)
    el.removeEventListener('mousedown', renewGestureWindow)
    el.removeEventListener('mouseup', renewGestureWindow)
    for (const type of ['wheel', 'touchstart', 'mousedown']) el.removeEventListener(type, noteUserScroll)
  }
  window.removeEventListener('keydown', onGestureKeydown)
  clearTimeout(gestureExpiryTimer)
  navigationSeq++
  cancelSearchJump()
  stopKeeper()
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
  // Issue #2012: a highlighted row's content could change via background live updates while
  // this instance is deactivated (recently-viewed sessions keep polling) — don't risk a <mark>
  // sitting around a since-changed text node once reactivated.
  navigationSeq++
  cancelSearchJump()
  stopKeeper()
  clearActiveHighlight()
})

// Auto-scroll on new messages
watch(() => displayableItems.value.length, () => scheduleStickyScroll())

// Auto-scroll on tool call updates (for permission requests, status changes, etc.)
watch(() => sessionToolCalls.value.length, () => scheduleStickyScroll())

// Issue #1955: the live-typing preview renders OUTSIDE the virtualizer's tracked rows (a
// normal-flow sibling, like TruncationBanner), so its growth no longer trips the virtualizer's
// own onChange (which only fires for tracked-row measurement changes). Watching the preview's
// own content/thinking length directly is the replacement trigger for the streaming-growth
// sticky-scroll case that onChange used to catch incidentally.
watch(() => {
  const preview = messageStore.streamingPreviewBySession.get(viewSessionId.value)
  return preview ? preview.content.length + preview.thinking.length : 0
}, () => scheduleStickyScroll())

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

.history-banner {
  flex: 0 0 auto;
  flex-wrap: wrap;
  /* clear the floating search toggle that sits at the top-right of the list */
  padding-right: 3.25rem !important;
  background: var(--bs-tertiary-bg);
  border-bottom: 1px solid var(--bs-border-color);
  color: var(--bs-secondary-color);
}

.history-banner-error {
  color: var(--bs-danger);
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
