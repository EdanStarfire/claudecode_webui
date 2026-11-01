<template>
  <div class="d-flex flex-column flex-grow-1 overflow-hidden position-relative">
    <!-- Archive Banner -->
    <div v-if="isArchiveMode" class="archive-banner">
      <span class="archive-badge">ARCHIVED</span>
      <span class="archive-label">Read-only archived session</span>
      <button
        type="button"
        class="btn btn-sm btn-link archive-info-btn"
        @click="uiStore.showModal('session-info', { sessionId: props.sessionId, isArchive: true })"
      >Info</button>
    </div>

    <!-- Archive viewer tabs (issue #1263) -->
    <ArchiveTabBar v-if="isArchiveMode" :session-id="props.sessionId" />

    <!-- Ephemeral Session Banner (schedule-managed, not yet fired) -->
    <div v-if="isEphemeralIdle" class="ephemeral-banner">
      <span class="ephemeral-badge">SCHEDULED</span>
      <span class="ephemeral-label">This agent is managed by a schedule and starts automatically when the schedule fires.</span>
    </div>

    <!-- Background subagent notifications (Issue #1676) -->
    <AgentNotificationStrip
      v-if="!isArchiveMode"
      :notifications="agentNotifications"
      @reply="replyToAgent"
      @dismiss="dismissAgentNotification"
    />

    <!-- Messages Area -->
    <div
      v-if="!isArchiveMode || currentArchiveTab === 'messages'"
      class="d-flex flex-column flex-grow-1 overflow-hidden"
    >
      <MessageList ref="messageListRef" />
    </div>

    <!-- Archive viewer tab panes (issue #1263) -->
    <div v-if="isArchiveMode && currentArchiveTab !== 'messages'" class="flex-grow-1 overflow-auto">
      <ArchiveProxyLogsTab
        v-if="currentArchiveTab === 'proxy'"
        :session-id="props.sessionId"
        :archive-id="effectiveArchiveId"
      />
      <ArchiveQueueTab
        v-else-if="currentArchiveTab === 'queue'"
        :session-id="props.sessionId"
        :archive-id="effectiveArchiveId"
      />
      <ArchiveAttachmentsTab
        v-else-if="currentArchiveTab === 'attachments'"
        :session-id="props.sessionId"
        :archive-id="effectiveArchiveId"
      />
      <ArchiveHistoryTab
        v-else-if="currentArchiveTab === 'history'"
        :session-id="props.sessionId"
        :archive-id="effectiveArchiveId"
      />
      <ArchiveSchedulesTab
        v-else-if="currentArchiveTab === 'schedules'"
        :session-id="props.sessionId"
        :archive-id="effectiveArchiveId"
      />
    </div>

    <!-- Bottom input-bar stack — measured so PermissionQueue's floating offset always clears
         its full live height (attachments, multiline drafts, archived banner, etc.), not a
         hardcoded distance from the frame edge. -->
    <div ref="bottomStackRef" class="bottom-input-stack">
      <InputArea ref="inputAreaRef" :is-archived="isArchiveMode" />
      <SessionStateStatusLine v-if="currentSession && !isArchiveMode" :session-id="props.sessionId" />
      <SessionStatusBar
        v-if="currentSession && !isArchiveMode"
        :session-id="props.sessionId"
        :virtual-nav="messageListRef"
      />
    </div>

    <!-- Floating permission queue (Issue #1746, stage: permissions) -->
    <PermissionQueue
      v-if="!isArchiveMode"
      :session-id="props.sessionId"
      :bottom-offset="bottomStackHeight"
      :virtual-nav="messageListRef"
    />
  </div>
</template>

<script>
export default { name: 'SessionView' }
</script>

<script setup>
import { computed, nextTick, onActivated, onDeactivated, onUnmounted, provide, readonly, ref, toRef, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useSessionStore } from '@/stores/session'
import { useMessageStore } from '@/stores/message'
import { useResourceStore } from '@/stores/resource'
import { useArchiveTabsStore } from '@/stores/archiveTabs'
import { historyBackfill } from '@/composables/useHistoryBackfill'
import { useUIStore } from '@/stores/ui'
import { usePollingStore } from '@/stores/polling'
import SessionStateStatusLine from './SessionStateStatusLine.vue'
import SessionStatusBar from '../statusbar/SessionStatusBar.vue'
import MessageList from '../messages/MessageList.vue'
import InputArea from '../messages/InputArea.vue'
import AgentNotificationStrip from '../messages/AgentNotificationStrip.vue'
import PermissionQueue from '../messages/PermissionQueue.vue'
import ArchiveTabBar from './ArchiveTabBar.vue'
import ArchiveProxyLogsTab from './ArchiveProxyLogsTab.vue'
import ArchiveQueueTab from './ArchiveQueueTab.vue'
import ArchiveAttachmentsTab from './ArchiveAttachmentsTab.vue'
import ArchiveHistoryTab from './ArchiveHistoryTab.vue'
import ArchiveSchedulesTab from './ArchiveSchedulesTab.vue'

const props = defineProps({
  sessionId: {
    type: String,
    required: true
  },
  archiveId: {
    type: String,
    default: null
  },
  isDeletedAgent: {
    type: Boolean,
    default: false
  }
})

// Provide per-instance session identity so cached descendants read their own session's data.
provide('viewSessionId', readonly(toRef(props, 'sessionId')))
provide('viewArchiveId', readonly(toRef(props, 'archiveId')))

const route = useRoute()
const inputAreaRef = ref(null)
// Issue #1748 (stage: offset-model): MessageList owns the virtualizer; PermissionQueue (a
// sibling, not a descendant) needs its exposed scrollToItemIndex/resolve* helpers for
// "view in context" — forwarded down as a prop since provide/inject only reaches descendants.
const messageListRef = ref(null)
const bottomStackRef = ref(null)
const bottomStackHeight = ref(0)
let bottomStackObserver = null
const sessionStore = useSessionStore()
const messageStore = useMessageStore()
const resourceStore = useResourceStore()
const archiveTabsStore = useArchiveTabsStore()
const uiStore = useUIStore()
const pollingStore = usePollingStore()

// Per-instance session lookup — safe under KeepAlive (reads this instance's own session).
const currentSession = computed(() => sessionStore.sessions.get(props.sessionId))

function focusInputWhenReady() {
  nextTick(() => inputAreaRef.value?.focusInput())
}

// Issue #1746 (stage: permissions): measures the InputArea/SessionStateStatusLine/
// SessionStatusBar stack's live rendered height, same ResizeObserver technique
// SubagentGlobalGutter.vue uses for its own measurement — PermissionQueue.vue needs the FULL
// stack's height (not just InputArea's), or it overlaps whichever of the latter two are visible.
function measureBottomStack() {
  if (bottomStackRef.value) bottomStackHeight.value = bottomStackRef.value.getBoundingClientRect().height
}

function setupBottomStackObserver() {
  if (bottomStackObserver || !bottomStackRef.value || typeof ResizeObserver === 'undefined') return
  bottomStackObserver = new ResizeObserver(measureBottomStack)
  bottomStackObserver.observe(bottomStackRef.value)
}

function teardownBottomStackObserver() {
  if (bottomStackObserver) {
    bottomStackObserver.disconnect()
    bottomStackObserver = null
  }
}

// Issue #1676: Background subagent notifications (agent_needs_input/agent_completed)
const agentNotifications = computed(() => messageStore.agentNotificationsForSession(props.sessionId))

function dismissAgentNotification(notificationId) {
  messageStore.dismissAgentNotification(props.sessionId, notificationId)
}

function replyToAgent(label) {
  const prefix = label ? `Please relay to ${label}: ` : 'Please relay to the background agent: '
  sessionStore.setInput(props.sessionId, prefix)
  focusInputWhenReady()
}

const isArchiveMode = computed(() => !!(props.archiveId || route.params.archiveId))
const effectiveArchiveId = computed(() => props.archiveId || route.params.archiveId)
const currentArchiveTab = computed(() => archiveTabsStore.activeTabForSession(props.sessionId))

// Per-instance ephemeral check — reads this instance's session state, not the global current.
const isEphemeralIdle = computed(() => {
  const session = currentSession.value
  return session?.is_ephemeral && !isArchiveMode.value &&
    (session.state === 'created' || session.state === 'terminated')
})

const resolveProjectId = () => sessionStore.getSession(props.sessionId)?.project_id || sessionStore.ghostAgents.get(props.sessionId)?.projectId

let cancelProjectWait = null
let archiveLoadToken = 0

function waitForProjectId(timeoutMs = 20000) {
  const known = resolveProjectId()
  if (known) return Promise.resolve(known)
  return new Promise((resolve) => {
    let stop = null
    const finish = (id) => { clearTimeout(timer); stop?.(); cancelProjectWait = null; resolve(id) }
    const timer = setTimeout(() => finish(null), timeoutMs)
    cancelProjectWait = () => finish(null) // leaving the view ends the wait
    stop = watch(resolveProjectId, (id) => { if (id) finish(id) })
  })
}

async function loadArchiveMessages() {
  const archiveId = effectiveArchiveId.value
  if (!archiveId) return

  // Find project ID for this session (a cold load of the archive route — a deep link — gets here
  // before the session list arrived: wait for it instead of silently showing nothing)
  const loadToken = ++archiveLoadToken
  const pid = await waitForProjectId()
  if (!pid || loadToken !== archiveLoadToken) return // timed out, or the view was left / re-entered meanwhile

  uiStore.showLoading('Loading archived session...')
  try {
    // Issue #2030: the archive opens on its newest window (the old first-1000 read silently
    // truncated long archives to their OLDEST records), and older history backfills like a live
    // session's. The view owns its signal — an archive load must not claim (and thereby abort)
    // the live session's shared hydration tracker for the same id.
    await messageStore.loadMessages(props.sessionId, null, 0, {
      window: 'tail',
      source: 'archive',
      baseUrl: `/api/projects/${pid}/archives/${props.sessionId}/${archiveId}/messages`,
      signal: new AbortController().signal,
    })
    historyBackfill.scheduleIndex(props.sessionId)
    // Load archived resources + the 5 archive-viewer tabs (issue #1263) concurrently —
    // independent fetches, and running them together shrinks the window where a tab
    // pane could render before loadAllTabs has even started.
    await Promise.all([
      resourceStore.loadArchiveResources(props.sessionId, pid, archiveId),
      archiveTabsStore.loadAllTabs(props.sessionId, pid, archiveId),
    ])
  } catch (e) {
    console.error('Failed to load archive messages:', e)
  } finally {
    uiStore.hideLoading()
  }
}

// Runs on first mount AND every KeepAlive reactivation.
// With :key="cacheKey", sessionId and archiveId never change within a single instance,
// so there is no need to watch them — each route change spawns or reactivates a distinct instance.
onActivated(async () => {
  if (isArchiveMode.value) {
    // Set currentSessionId so AgentOverview can display for deleted agents
    sessionStore.currentSessionId = props.sessionId
    sessionStore.lastViewedArchive.set(props.sessionId, effectiveArchiveId.value)
    // Epic #1990 stage 5b (AC3/UC3), consolidated stage 5c: an archived session view
    // never subscribes to live polling at all — mark the view explicitly archived
    // instead of leaving wsStore.currentSessionId/sessionState untouched (an implicit,
    // uninspectable absence the header could misread as either a stale "connected"
    // from a previously-viewed live session, or a spurious "disconnected").
    pollingStore.markSessionArchived(props.sessionId)
    // Guard: skip reload if archive messages are already in the store from the first visit.
    if (!messageStore.messagesBySession.has(props.sessionId)) {
      await loadArchiveMessages()
    }
  } else if (props.sessionId !== sessionStore.currentSessionId) {
    await sessionStore.selectSession(props.sessionId)
  }
  focusInputWhenReady()
  nextTick(() => {
    setupBottomStackObserver()
    measureBottomStack()
  })
})

// Clear archive data on deactivation so regular-session instances for the same sessionId
// do not inherit stale archive messages from messagesBySession when reactivated.
onDeactivated(() => {
  archiveLoadToken++
  cancelProjectWait?.()
  if (isArchiveMode.value) {
    messageStore.clearArchiveMessages(props.sessionId)
    resourceStore.clearResources(props.sessionId)
    resourceStore.clearArchiveContext(props.sessionId)
    archiveTabsStore.clearArchiveTabs(props.sessionId)
    // Builder-review finding: deliberately NOT calling pollingStore.disconnectSession()
    // here. The same sessionId can have a separate, concurrently KeepAlive-cached LIVE
    // instance (the user flips between "live" and "archive X" tabs for the same agent) —
    // disconnecting here would kill that live instance's still-running poll loop with
    // nothing to ever restart it (sessionStore.currentSessionId never changes across this
    // navigation, so the live instance's own onActivated early-returns and never
    // re-selects). markSessionArchived() already disconnects-before-marking on its own
    // next call, which is the correct place for this teardown to live.
  }
  teardownBottomStackObserver()
})

// Per-instance state watch: currentSession reads sessions.get(props.sessionId) — correct under KeepAlive.
watch(
  () => currentSession.value?.state,
  (newState, oldState) => {
    if (newState === 'active' && (oldState === 'starting' || oldState === 'created')) {
      focusInputWhenReady()
    }
  }
)

// Safety-net cleanup for LRU eviction (onDeactivated already handles the normal navigation case).
onUnmounted(() => {
  archiveLoadToken++
  cancelProjectWait?.()
  if (isArchiveMode.value) {
    messageStore.clearArchiveMessages(props.sessionId)
    resourceStore.clearResources(props.sessionId)
    resourceStore.clearArchiveContext(props.sessionId)
    archiveTabsStore.clearArchiveTabs(props.sessionId)
    // See onDeactivated's identical note above — same reasoning applies to LRU eviction.
  }
  teardownBottomStackObserver()
})
</script>

<style scoped>
.bottom-input-stack {
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
}

.archive-banner {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 16px;
  background: #fef3cd;
  border-bottom: 1px solid #ffc107;
  flex-shrink: 0;
}

.archive-badge {
  font-size: 10px;
  font-weight: 700;
  padding: 2px 8px;
  border-radius: 4px;
  background: #ffc107;
  color: #664d03;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.archive-label {
  font-size: 12px;
  color: #664d03;
}

.archive-info-btn {
  font-size: 12px;
  padding: 0 0 0 4px;
  color: #664d03;
  text-decoration: underline;
}

.ephemeral-banner {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 16px;
  background: #d1ecf1;
  border-bottom: 1px solid #bee5eb;
  flex-shrink: 0;
}

.ephemeral-badge {
  font-size: 10px;
  font-weight: 700;
  padding: 2px 8px;
  border-radius: 4px;
  background: #17a2b8;
  color: #fff;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.ephemeral-label {
  font-size: 12px;
  color: #0c5460;
}
</style>
