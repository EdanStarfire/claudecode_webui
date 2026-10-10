import { defineStore } from 'pinia'
import { ref, computed, readonly, watch } from 'vue'
import { api } from '../utils/api'
import { useSessionStore } from './session'
import { useTaskStore } from './task'
import { correlateHooks } from '../utils/hookCorrelation'
import { getAgentColor, getAssistantRowColor, slugifyAgentName } from '../composables/useAgentColor'
import { pushDebugEvent, flushDebugBuffer } from '../composables/useDebugBuffer'

/**
 * Message Store - Manages messages and tool calls per session
 */
export const useMessageStore = defineStore('message', () => {
  // ========== STATE ==========

  // Messages per session (sessionId -> Message[])
  const messagesBySession = ref(new Map())

  // Tool calls per session (sessionId -> ToolCall[])
  const toolCallsBySession = ref(new Map())

  const permissionToToolMap = ref(new Map())

  // Issue #1000: Event cursors returned by REST /messages endpoint.
  // Used by websocket.connectSession() to start polling from the correct position.
  const loadedEventCursors = new Map()

  // Launch timestamp tracking (sessionId -> Unix timestamp in seconds)
  // Populated from client_launched system messages for uptime calculation
  const launchTimestampBySession = ref(new Map())

  // Issue #662: Track last stop_reason per session for truncation banner
  const lastStopReasonBySession = ref(new Map())

  // Issue #1300: Track deferred tool use per session for deferral banner
  // Map<sessionId, { id, name, input } | null>
  const deferredToolUseBySession = ref(new Map())

  // Issue #1746 (stage: subagents) / #1765: task_id-first background-agent (subagent)
  // tracking. Replaces the old tool_use_id-keyed taskActivityByToolUseId, which could not
  // bridge a resumed leg (its own tool_use_id) back to the agent's earlier leg(s).
  // Map<task_id, { task_id, session_id, legs: TaskLeg[] }> — mirrors the backend
  // TaskLegRegistry's TaskLegEntry shape (src/task_registry.py).
  const taskLegsByTaskId = ref(new Map())
  // Map<tool_use_id, task_id> — bridges a launch/resume Task tool_use to its task_id once
  // task_started arrives (one entry per leg, since every leg has its own launching tool_use).
  // Before task_started arrives for a brand new launch, a launch anchor keys provisionally
  // on its own tool_use_id (this map simply has no entry for it yet).
  const taskIdByLaunchToolUseId = ref(new Map())
  // Map<task_id, Map<legIndex, Message[]>> — narration (thinking/text) captured from subagent
  // assistant messages (metadata.parent_tool_use_id set) that MessageList.vue would otherwise
  // silently drop (issue #1671). Consumed by SubagentLegTranscript.vue.
  const narrationByTaskIdAndLeg = ref(new Map())

  const TASK_LIFECYCLE_SUBTYPES = new Set(['task_started', 'task_progress', 'task_notification', 'task_updated'])
  const TERMINAL_TASK_STATUSES = new Set(['completed', 'failed', 'stopped', 'killed'])
  const TASK_STATUS_DISPLAY_MAP = { killed: 'stopped' }
  // Mirrors backend NON_AGENT_TASK_TYPES (src/task_registry.py): task_started frames with one
  // of these task_types are not background agents and must not be registered as a leg. Missing/
  // undefined task_type still defaults to "is an agent" for backward compatibility.
  const NON_AGENT_TASK_TYPES = new Set(['local_bash'])

  // Issue #1955: cosmetic-only live-typing preview, per session (in-memory only, never
  // persisted, never a messagesBySession entry, never identity-bearing). `active` is true from
  // message_start until message_stop (drives the caret); content/thinking are display
  // accumulators only, never read by dedup/identity logic. pendingText/pendingThinking/rafHandle
  // are the rAF-batched flush scratch state.
  // Map<sessionId, { active, content, thinking, pendingText, pendingThinking, rafHandle }>
  const streamingPreviewBySession = ref(new Map())

  // Issue #1350: Hook correlation cache — non-reactive, internal memoization only.
  // Shape: Map<sessionId, { result: HookCorrelationResult, messageCount: number, lastId: string|null }>
  const _hookCorrelationCache = new Map()

  // Issue #1676: Dismissed background-agent notifications, per session.
  // Not persisted — dismissal resets on reload (session-local UI state only).
  const dismissedAgentNotifications = ref(new Map())

  // ========== COMPUTED ==========

  // Current session's messages
  const currentMessages = computed(() => {
    const sessionStore = useSessionStore()
    return messagesBySession.value.get(sessionStore.currentSessionId) || []
  })

  // Current session's tool calls
  const currentToolCalls = computed(() => {
    const sessionStore = useSessionStore()
    return toolCallsBySession.value.get(sessionStore.currentSessionId) || []
  })

  // ========== ACTIONS ==========

  // Issue #1747: defensive ceiling on fetchAllMessagePages() looping — order of
  // hundreds of pages, purely to prevent a runaway request storm if the backend
  // ever violates its has_more contract. Not expected to trigger under real usage.
  const MAX_PAGINATION_ITERATIONS = 500

  /**
   * Issue #1747: page through GET /api/sessions/{id}/messages, accumulating
   * results until the backend reports has_more === false, so callers never
   * silently truncate history at a single page's size.
   */
  async function fetchAllMessagePages(sessionId, pageSize, startOffset = 0) {
    const messages = []
    let offset = startOffset
    let totalCount = 0
    let eventCursor

    for (let iteration = 0; iteration < MAX_PAGINATION_ITERATIONS; iteration++) {
      const data = await api.get(
        `/api/sessions/${sessionId}/messages?limit=${pageSize}&offset=${offset}`
      )

      const pageMessages = data.messages || []
      totalCount = data.total_count || (offset + pageMessages.length)
      if (data.event_cursor !== undefined) {
        eventCursor = data.event_cursor
      }
      messages.push(...pageMessages)

      const hasMore = data.has_more || false
      if (!hasMore) {
        return { messages, totalCount, hasMore: false, eventCursor }
      }

      // Advance by the requested page size, not pageMessages.length: the backend's
      // offset/limit pagination applies to raw stored lines, but the returned
      // `messages` array can contain more entries than raw lines consumed (synthetic
      // tool_call messages are interleaved). Advancing by the response length would
      // desync from the backend's raw-line cursor and skip messages.
      offset += pageSize
    }

    console.error(
      `fetchAllMessagePages: exceeded ${MAX_PAGINATION_ITERATIONS} pages for session ${sessionId} ` +
      `(${messages.length} messages loaded so far). Stopping to avoid a runaway request storm — ` +
      `this indicates the backend has_more contract was violated.`
    )
    return { messages, totalCount, hasMore: true, eventCursor }
  }

  /**
   * Load messages for a session from backend
   */
  async function loadMessages(sessionId, limit = null, offset = 0) {
    try {
      let messages, totalCount, hasMore, eventCursor

      if (limit) {
        // Explicit limit requested: preserve today's single-shot-fetch behavior.
        const data = await api.get(
          `/api/sessions/${sessionId}/messages?limit=${limit}&offset=${offset}`
        )
        messages = data.messages || []
        totalCount = data.total_count || messages.length
        hasMore = data.has_more || false
        eventCursor = data.event_cursor
      } else {
        // Issue #1747: no limit specified — page until has_more is false so the
        // full history loads, instead of silently truncating at one page.
        const result = await fetchAllMessagePages(sessionId, 10000, offset)
        messages = result.messages
        totalCount = result.totalCount
        hasMore = result.hasMore
        eventCursor = result.eventCursor
      }

      // Issue #1000: Store event cursor from REST response for poll alignment
      if (eventCursor !== undefined) {
        loadedEventCursors.set(sessionId, eventCursor)
      }

      console.log(`Loaded ${messages.length} of ${totalCount} messages for session ${sessionId}`)

      // Issue #1955: any live streaming preview for this session is purely cosmetic and was
      // never part of the canonical message array — discard it outright instead of merging.
      // The freshly-fetched history is authoritative; there is nothing to reconcile it against.
      _discardStreamingPreview(sessionId, 'reload')

      // Issue #2110 (stage 4b-B): thin fetch-then-apply-per-record wrapper — the one place
      // this session's display list is reset to a fresh empty array before applyRecord()
      // incrementally rebuilds it (replacing the old bulk-array-replace semantics).
      messagesBySession.value.set(sessionId, [])

      messages.forEach(message => {
        // Capture init data for session info modal — not a per-record store side effect, so it
        // stays here rather than in applyRecord (same split as polling.js's own init-data
        // capture ahead of its applyRecord() call for the live path).
        if (message.type === 'system' &&
            (message.subtype === 'init' || message.metadata?.subtype === 'init') &&
            message.metadata?.init_data) {
          const sessionStore = useSessionStore()
          sessionStore.storeInitData(sessionId, message.metadata.init_data)
        }

        // notify:false — see applyRecord()'s own comment: avoid O(N) reactive broadcasts while
        // replaying a whole session's history, one final reassignment below covers all of them
        // (including when `messages` is empty, so this session's reset to [] is still observed).
        applyRecord(sessionId, message, 'load', { notify: false })
      })
      messagesBySession.value = new Map(messagesBySession.value)
      toolCallsBySession.value = new Map(toolCallsBySession.value)
      lastStopReasonBySession.value = new Map(lastStopReasonBySession.value)
      deferredToolUseBySession.value = new Map(deferredToolUseBySession.value)

      const regularMessages = messagesBySession.value.get(sessionId) || []

      // Reconstruct task state from message history
      try {
        const taskStore = useTaskStore()
        taskStore.reconstructFromMessages(sessionId, regularMessages)
      } catch (e) {
        console.warn('Failed to reconstruct task state:', e)
      }

      // Warn if there are more messages we didn't load
      if (hasMore) {
        console.warn(`Warning: Only loaded ${regularMessages.length} of ${totalCount} messages. Some messages may be missing.`)
      }

      return { messages: regularMessages, totalCount, hasMore }
    } catch (error) {
      console.error('Failed to load messages:', error)
      throw error
    }
  }

  /**
   * Issue #1746 (stage: subagents) / #1765: apply one Task lifecycle frame
   * (task_started/task_progress/task_notification/task_updated) to taskLegsByTaskId.
   * Mirrors src/task_registry.py's TaskLegRegistry.apply_frame() exactly, so live streaming
   * and (via hydrateBackgroundAgents' backend snapshot) reload converge on the same state.
   */
  function applyTaskLifecycleFrame(sessionId, subtype, metadata, timestamp) {
    const taskId = metadata?.task_id
    if (!taskId || !TASK_LIFECYCLE_SUBTYPES.has(subtype)) return

    if (subtype === 'task_started') {
      if (NON_AGENT_TASK_TYPES.has(metadata.task_type)) return
      let entry = taskLegsByTaskId.value.get(taskId)
      if (!entry) {
        entry = { task_id: taskId, session_id: sessionId, legs: [] }
        taskLegsByTaskId.value.set(taskId, entry)
      }
      const toolUseId = metadata.tool_use_id || null
      // Idempotency guard: a reconnect/replay must not duplicate a leg already recorded
      // (e.g. hydrateBackgroundAgents' snapshot already included it).
      if (toolUseId && entry.legs.some(leg => leg.tool_use_id === toolUseId)) return
      entry.legs.push({
        tool_use_id: toolUseId,
        description: metadata.description || null,
        started_at: timestamp,
        last_progress_at: timestamp,
        ended_at: null,
        status: 'running',
      })
      if (toolUseId) taskIdByLaunchToolUseId.value.set(toolUseId, taskId)
      taskLegsByTaskId.value = new Map(taskLegsByTaskId.value)
      taskIdByLaunchToolUseId.value = new Map(taskIdByLaunchToolUseId.value)
      return
    }

    const entry = taskLegsByTaskId.value.get(taskId)
    if (!entry) return
    const leg = entry.legs[entry.legs.length - 1]
    if (!leg) return

    if (subtype === 'task_progress') {
      if (leg.status !== 'running') return
      leg.last_progress_at = timestamp
      if (metadata.description && !leg.description) leg.description = metadata.description
      taskLegsByTaskId.value = new Map(taskLegsByTaskId.value)
      return
    }

    // task_notification / task_updated: terminal status carrier. First-terminal-wins.
    if (leg.status !== 'running') return
    const rawStatus = subtype === 'task_notification'
      ? metadata.status
      : (metadata.status || metadata.patch?.status)
    if (!TERMINAL_TASK_STATUSES.has(rawStatus)) return
    leg.status = TASK_STATUS_DISPLAY_MAP[rawStatus] || rawStatus
    leg.ended_at = timestamp
    // Mirrors src/task_registry.py: only task_notification carries the subagent's own
    // final report (`summary`) — task_updated (TaskStop-only termination) has no such field.
    if (subtype === 'task_notification' && metadata.summary) leg.result = metadata.summary
    taskLegsByTaskId.value = new Map(taskLegsByTaskId.value)
  }

  /**
   * Issue #1746 (stage: subagents): seed taskLegsByTaskId/taskIdByLaunchToolUseId from the
   * backend's already-reduced snapshot (GET /api/sessions/{id}/background_agents) — the
   * reload/reconnect source of truth, so gutter/anchor state is correct from a cold load
   * without waiting for live frames to replay. Call before loadMessages() for a session.
   */
  async function hydrateBackgroundAgents(sessionId) {
    try {
      const data = await api.get(`/api/sessions/${sessionId}/background_agents`)
      const agents = data.agents || []
      for (const agent of agents) {
        const taskId = agent.task_id
        if (!taskId) continue
        const legs = (agent.legs || []).map(leg => ({ ...leg }))
        taskLegsByTaskId.value.set(taskId, { task_id: taskId, session_id: sessionId, legs })
        for (const leg of legs) {
          if (leg.tool_use_id) taskIdByLaunchToolUseId.value.set(leg.tool_use_id, taskId)
        }
      }
      taskLegsByTaskId.value = new Map(taskLegsByTaskId.value)
      taskIdByLaunchToolUseId.value = new Map(taskIdByLaunchToolUseId.value)
    } catch (error) {
      console.warn(`Failed to hydrate background agents for session ${sessionId}:`, error)
    }
  }

  /** Issue #1746 (stage: subagents): all known legs for a task_id, or null. */
  function getTaskLegEntry(taskId) {
    if (!taskId) return null
    return taskLegsByTaskId.value.get(taskId) || null
  }

  /**
   * Issue #1746 (stage: subagents) follow-up: every known task's leg entry for a session, so
   * MessageList.vue can surface leg-terminal (completed/failed/stopped) events as their own
   * main-timeline signals — spec §4.2's causal-moment list, not just the subagent's own nested
   * card. taskLegsByTaskId itself is intentionally not exposed raw (see getTaskLegEntry).
   */
  function allTaskLegEntriesForSession(sessionId) {
    if (!sessionId) return []
    return Array.from(taskLegsByTaskId.value.values()).filter(e => e.session_id === sessionId)
  }

  /** Issue #1746 (stage: subagents): resolve a launch/resume Task tool_use_id to its task_id. */
  function getTaskIdForLaunchToolUse(toolUseId) {
    if (!toolUseId) return null
    return taskIdByLaunchToolUseId.value.get(toolUseId) || null
  }

  /**
   * Issue #1746 (stage: subagents) follow-up (real repro data): parent_tool_use_id on ALL of a
   * subagent's child activity — its original run AND every subsequent resume — stays pinned to
   * the very FIRST leg's own launching tool_use_id. A resume (triggered via
   * `SendMessage(to: "<agent name>")`, not a fresh Task/Agent call) never becomes any child's
   * parent_tool_use_id itself; the underlying SDK continues reporting the original launch's id
   * for the whole nested session it's waking back up. So which LEG a piece of activity belongs
   * to can't be resolved by matching parent_tool_use_id against a specific leg's own id (that
   * always resolves to leg 0) — it has to be resolved by WHEN the activity happened: the most
   * recent leg whose started_at is at or before the activity's own timestamp.
   */
  function _resolveLegIndexForTimestamp(entry, timestamp) {
    if (!entry || !entry.legs.length) return -1
    if (timestamp == null) return entry.legs.length - 1
    let idx = 0
    for (let i = 0; i < entry.legs.length; i++) {
      const startedAt = entry.legs[i].started_at
      if (startedAt == null || startedAt <= timestamp) idx = i
      else break
    }
    return idx
  }

  /**
   * Issue #1746 (stage: subagents): route a subagent assistant message (narration) into
   * narrationByTaskIdAndLeg, resolved via its parent_tool_use_id (bridges to the task_id — see
   * _resolveLegIndexForTimestamp for why the LEG itself is then resolved by timestamp, not by
   * matching parent_tool_use_id against a specific leg's own id).
   */
  function _routeSubagentNarration(message) {
    const parentId = message.metadata?.parent_tool_use_id
    if (!parentId) return false
    const taskId = taskIdByLaunchToolUseId.value.get(parentId)
    if (!taskId) return false
    const entry = taskLegsByTaskId.value.get(taskId)
    const legIndex = _resolveLegIndexForTimestamp(entry, message.timestamp)
    if (legIndex === -1) return false

    if (!narrationByTaskIdAndLeg.value.has(taskId)) {
      narrationByTaskIdAndLeg.value.set(taskId, new Map())
    }
    const perLeg = narrationByTaskIdAndLeg.value.get(taskId)
    if (!perLeg.has(legIndex)) perLeg.set(legIndex, [])
    perLeg.get(legIndex).push(message)
    narrationByTaskIdAndLeg.value = new Map(narrationByTaskIdAndLeg.value)
    return true
  }

  /**
   * Issue #1746 (stage: subagents) follow-up: child tool calls for one specific leg, resolved
   * the same timestamp-window way as narration (see _resolveLegIndexForTimestamp) — NOT by
   * matching parent_tool_use_id against that leg's own launch/resume tool_use_id, since only
   * the very first leg's id ever appears as a child's parent_tool_use_id.
   */
  function childToolCallsForLeg(sessionId, taskId, legIndex) {
    if (!sessionId || !taskId) return []
    const entry = taskLegsByTaskId.value.get(taskId)
    if (!entry || !entry.legs.length) return []
    const rootToolUseId = entry.legs[0].tool_use_id
    if (!rootToolUseId) return []

    const windowStart = entry.legs[legIndex]?.started_at ?? -Infinity
    const windowEnd = entry.legs[legIndex + 1]?.started_at ?? Infinity

    const toolCalls = toolCallsBySession.value.get(sessionId) || []
    return toolCalls.filter(tc => {
      if (tc.parent_tool_use_id !== rootToolUseId) return false
      const ts = typeof tc.timestamp === 'number' ? tc.timestamp : new Date(tc.timestamp).getTime() / 1000
      return ts >= windowStart && ts < windowEnd
    })
  }

  /** Issue #1746 (stage: subagents): narration messages for one leg, oldest first. */
  function narrationForLeg(taskId, legIndex) {
    if (!taskId) return []
    return narrationByTaskIdAndLeg.value.get(taskId)?.get(legIndex) || []
  }

  // Issue #1746 (stage: subagents) follow-up: per-leg transcript expand state, shared here
  // (not a local component ref) — the global gutter chip (SubagentGlobalGutter.vue, rendered
  // once in MessageList.vue) and the inline anchor row (rendered deep inside AssistantMessage
  // -> SubagentTimeline) are separate DOM subtrees that both need to control/read the SAME
  // leg's expand state.
  const expandedLegs = ref(new Map()) // Map<`${taskId}:${legIndex}`, boolean>

  function isLegExpanded(taskId, legIndex) {
    if (!taskId) return false
    return !!expandedLegs.value.get(`${taskId}:${legIndex}`)
  }

  function setLegExpanded(taskId, legIndex, value) {
    if (!taskId) return
    expandedLegs.value.set(`${taskId}:${legIndex}`, value)
    expandedLegs.value = new Map(expandedLegs.value)
  }

  function toggleLegExpanded(taskId, legIndex) {
    setLegExpanded(taskId, legIndex, !isLegExpanded(taskId, legIndex))
  }

  // Issue #1748 (stage: windowing): which tool detail card is expanded within a given
  // ActivityTimeline instance — shared here (not a local component ref), same reason as
  // expandedLegs above: at a real (non-full) overscan value, a row scrolled out of the mounted
  // range fully unmounts and remounts with a fresh setup() on scroll-back, which would silently
  // collapse a tool card the user had deliberately expanded to read. `scopeKey` is whatever the
  // caller uses to identify one ActivityTimeline's own tool group (a messageId, or a fallback);
  // only one tool is ever expanded per scope, matching the pre-#1748 single-ref behavior.
  //
  // Issue #1748 (stage: windowing) review fix: `autoPermission` tracks whether the CURRENT
  // expansion was auto-triggered by a tool entering permission_required (vs. a manual click) —
  // ActivityTimeline's own watch uses this to auto-collapse only what it auto-expanded, never a
  // manual expand. This must be store-backed too (not a second local ref, as first written):
  // if the permission resolves off-screen (e.g. via PermissionQueue's always-mounted floating
  // panel) while the row is unmounted, a fresh mount with a local `expandedForPermission = false`
  // would forget the row was auto-expanded and never auto-collapse it on remount, leaving a
  // resolved permission's detail panel stuck open. Reproduced and confirmed during review.
  const expandedTimelineTool = ref(new Map()) // Map<scopeKey, toolId>
  const expandedTimelineToolAutoPermission = ref(new Map()) // Map<scopeKey, boolean>

  function getExpandedTimelineTool(scopeKey) {
    if (!scopeKey) return null
    return expandedTimelineTool.value.get(scopeKey) ?? null
  }

  function isExpandedTimelineToolAutoPermission(scopeKey) {
    if (!scopeKey) return false
    return !!expandedTimelineToolAutoPermission.value.get(scopeKey)
  }

  function setExpandedTimelineTool(scopeKey, toolId, { autoPermission = false } = {}) {
    if (!scopeKey) return
    if (toolId == null) {
      expandedTimelineTool.value.delete(scopeKey)
    } else {
      expandedTimelineTool.value.set(scopeKey, toolId)
    }
    expandedTimelineTool.value = new Map(expandedTimelineTool.value)
    expandedTimelineToolAutoPermission.value.set(scopeKey, toolId != null && autoPermission)
    expandedTimelineToolAutoPermission.value = new Map(expandedTimelineToolAutoPermission.value)
  }

  // Issue #1748 (stage: windowing) review fix: prune stale entries on session reset/archive-clear
  // — mirrors expandedLegs' per-session cleanup a few lines up. scopeKey is a message id or a
  // tool_use id (optionally suffixed `-run-N` for SubagentLegTranscript's multi-run case), so
  // matching against every message id / tool_use id still known for this session (before it's
  // deleted) catches both forms without needing to recompute exact run indices.
  function pruneExpandedTimelineToolForSession(sessionId) {
    const messages = messagesBySession.value.get(sessionId) || []
    const knownIds = new Set()
    for (const msg of messages) {
      if (msg.id) knownIds.add(msg.id)
      if (msg.message_id) knownIds.add(msg.message_id)
      for (const t of msg.metadata?.tool_uses || []) {
        if (t.id) knownIds.add(t.id)
      }
    }
    if (knownIds.size === 0) return
    let changed = false
    for (const key of expandedTimelineTool.value.keys()) {
      const runSuffixIndex = key.indexOf('-run-')
      const baseId = runSuffixIndex === -1 ? key : key.slice(0, runSuffixIndex)
      if (knownIds.has(key) || knownIds.has(baseId)) {
        expandedTimelineTool.value.delete(key)
        expandedTimelineToolAutoPermission.value.delete(key)
        changed = true
      }
    }
    if (changed) {
      expandedTimelineTool.value = new Map(expandedTimelineTool.value)
      expandedTimelineToolAutoPermission.value = new Map(expandedTimelineToolAutoPermission.value)
    }
  }

  // Issue #1748 (stage: windowing) review fix: ThinkingBlock's expand/collapse toggle is
  // store-backed for the same remount-survival reason as expandedTimelineTool above — flagged
  // during review as the same class of risk left unaddressed for this specific component (a
  // Thinking block is rendered per-segment inside AssistantMessage, itself inside a virtualized
  // row, so it unmounts/remounts exactly like ActivityTimeline does). `scopeKey` is the owning
  // segment's own message id (matching the :key already used for that segment in
  // AssistantMessage.vue), a simple boolean toggle — mirrors expandedLegs/isLegExpanded exactly.
  const thinkingBlockExpanded = ref(new Map()) // Map<scopeKey, boolean>

  function isThinkingBlockExpanded(scopeKey) {
    if (scopeKey == null) return false
    return !!thinkingBlockExpanded.value.get(scopeKey)
  }

  function toggleThinkingBlockExpanded(scopeKey) {
    if (scopeKey == null) return
    thinkingBlockExpanded.value.set(scopeKey, !isThinkingBlockExpanded(scopeKey))
    thinkingBlockExpanded.value = new Map(thinkingBlockExpanded.value)
  }

  function pruneThinkingBlockExpandedForSession(sessionId) {
    const messages = messagesBySession.value.get(sessionId) || []
    const knownIds = new Set()
    for (const msg of messages) {
      if (msg.id) knownIds.add(msg.id)
      if (msg.message_id) knownIds.add(msg.message_id)
    }
    if (knownIds.size === 0) return
    let changed = false
    for (const key of thinkingBlockExpanded.value.keys()) {
      if (knownIds.has(key)) {
        thinkingBlockExpanded.value.delete(key)
        changed = true
      }
    }
    if (changed) thinkingBlockExpanded.value = new Map(thinkingBlockExpanded.value)
  }

  // Issue #1843: collapsible CommCard expand/collapse toggle, store-backed for the same
  // remount-survival reason as thinkingBlockExpanded/expandedTimelineTool above (a comm
  // card is rendered inside a virtualized message row and unmounts/remounts on scroll).
  // scopeKey is toolCall.id for outbound comms, message.id || message.message_id for
  // inbound comms — a simple boolean toggle, mirrors thinkingBlockExpanded exactly.
  const expandedComms = ref(new Map()) // Map<scopeKey, boolean>

  function isCommExpanded(scopeKey) {
    if (scopeKey == null) return false
    return !!expandedComms.value.get(scopeKey)
  }

  function toggleCommExpanded(scopeKey) {
    if (scopeKey == null) return
    expandedComms.value.set(scopeKey, !isCommExpanded(scopeKey))
    expandedComms.value = new Map(expandedComms.value)
  }

  // Prunes against both message ids and tool_use ids (like pruneExpandedTimelineToolForSession)
  // since expandedComms scope keys come from both id shapes depending on direction.
  function pruneExpandedCommsForSession(sessionId) {
    const messages = messagesBySession.value.get(sessionId) || []
    const knownIds = new Set()
    for (const msg of messages) {
      if (msg.id) knownIds.add(msg.id)
      if (msg.message_id) knownIds.add(msg.message_id)
      for (const t of msg.metadata?.tool_uses || []) {
        if (t.id) knownIds.add(t.id)
      }
    }
    if (knownIds.size === 0) return
    let changed = false
    for (const key of expandedComms.value.keys()) {
      if (knownIds.has(key)) {
        expandedComms.value.delete(key)
        changed = true
      }
    }
    if (changed) expandedComms.value = new Map(expandedComms.value)
  }

  /**
   * Issue #1746 (stage: subagents): "needs attention" — true when a leg has an open permission
   * request on one of its own child tool calls. Resolved from already-available store data (no
   * new backend surface needed): any tool call whose parent_tool_use_id is the task's root
   * launch tool_use_id (see childToolCallsForLeg for why child activity always keys off the
   * root, never a specific leg's own id) and is currently awaiting a permission decision.
   */
  function hasOpenPermissionForTask(sessionId, taskId) {
    if (!sessionId || !taskId) return false
    const entry = taskLegsByTaskId.value.get(taskId)
    const rootToolUseId = entry?.legs?.[0]?.tool_use_id
    if (!rootToolUseId) return false
    const toolCalls = toolCallsBySession.value.get(sessionId) || []
    return toolCalls.some(tc =>
      tc.parent_tool_use_id === rootToolUseId && tc.status === 'permission_required'
    )
  }

  /**
   * Issue #1746 (stage: permissions): every open permission request in a session — main-session
   * and subagent alike — enriched for the floating PermissionQueue.vue surface. Resolved from
   * the same store data hasOpenPermissionForTask already reads; no new backend surface needed.
   * A tool call's parent_tool_use_id always pins to a task's FIRST leg's own launch tool_use_id
   * (see _resolveLegIndexForTimestamp's comment), so the task itself resolves in one lookup —
   * the OPEN leg is then whichever of that task's legs is currently 'running' (legs are
   * sequential, never concurrent, so this is unambiguous).
   */
  function openPermissionsForSession(sessionId) {
    if (!sessionId) return []
    const toolCalls = toolCallsBySession.value.get(sessionId) || []
    return toolCalls
      .filter(tc => tc.status === 'permission_required')
      .map(tc => {
        const taskId = tc.parent_tool_use_id ? getTaskIdForLaunchToolUse(tc.parent_tool_use_id) : null

        if (!taskId) {
          return {
            requestId: tc.permissionRequestId,
            toolCall: tc,
            taskId: null,
            legIndex: null,
            agentColor: getAssistantRowColor(),
            isSubagent: false,
            label: 'Main session',
          }
        }

        const entry = taskLegsByTaskId.value.get(taskId)
        const runningIdx = entry?.legs?.findIndex(leg => leg.status === 'running') ?? -1
        // Fall back to the most recent leg when none is currently 'running' (e.g. a terminal
        // frame lands before this leg's own child permission has resolved) — never leave
        // legIndex null just because the snapshot briefly has no running leg, or "view in
        // context" silently no-ops on a valid, still-open permission.
        const legIndex = runningIdx >= 0 ? runningIdx : (entry?.legs?.length ? entry.legs.length - 1 : -1)
        const leg = legIndex >= 0 ? entry.legs[legIndex] : null
        const launchToolCall = leg ? toolCalls.find(t => t.id === leg.tool_use_id) : null
        const launchInput = launchToolCall?.input || {}
        const label = leg?.description || launchInput.description || launchInput.prompt ||
          launchInput.summary || launchInput.message || 'Subagent task'

        return {
          requestId: tc.permissionRequestId,
          toolCall: tc,
          taskId,
          legIndex: legIndex >= 0 ? legIndex : null,
          agentColor: getAgentColor(slugifyAgentName(taskId)),
          isSubagent: true,
          label,
        }
      })
  }

  /**
   * Issue #2110 (stage 4b-B): the one entry point for every message/tool_call record,
   * regardless of where it came from (`source` ∈ 'live' | 'load' | 'archive'). `source`
   * gates ONLY notification/preview behavior (AC1) — every other side effect below runs
   * uniformly, closing the load/archive gaps the pre-refactor side-effect matrix documented
   * (truncation/deferral banners, subagent narration routing).
   *
   * `notify` (review fix): whether to trigger Vue reactivity immediately after applying this
   * one record — defaults to true for every normal caller (live dispatch, the content_block_
   * start pending-card synthesis, tests). `loadMessages()`/`setArchiveMessages()` pass `false`
   * while replaying a whole session's history in a tight loop and do ONE reassignment after the
   * loop instead: without this, each of N replayed records would individually reassign
   * messagesBySession.value (and friends), turning a full session load into O(N) reactive
   * broadcasts over an array of growing size — O(N²) render/diff work for the component
   * currently watching that session, where the old bulk-array-replace code was O(N). The DATA
   * mutation itself (push/set) always happens regardless of `notify` — only the "tell Vue"
   * step is deferred.
   */
  function applyRecord(sessionId, record, source, { notify = true } = {}) {
    if (!record || !record.type) return
    if (record.type === 'tool_call') {
      _applyToolCallRecord(sessionId, record, notify)
      return
    }
    _applyMessageRecord(sessionId, record, source, notify)
  }

  /**
   * Issue #1955: the one dedup rule — is there already an entry with this backend id? No ->
   * push. Yes -> skip. `messagesBySession` is a pure mirror of the canonical/backend channel;
   * nothing else is ever spliced, merged, or replaced-in-place for streaming purposes (the
   * live-typing preview lives entirely in `streamingPreviewBySession` instead).
   */
  function _applyMessageRecord(sessionId, message, source, notify = true) {
    // Issue #1486/#1575: drop internal SDK status/requesting messages — not displayable,
    // regardless of source.
    if (message.type === 'system') {
      const subtype = message.subtype || message.metadata?.subtype
      const status = message.metadata?.init_data?.status
      if (subtype === 'status' && status === 'requesting') {
        return
      }
    }

    if (!messagesBySession.value.has(sessionId)) {
      messagesBySession.value.set(sessionId, [])
    }
    const messages = messagesBySession.value.get(sessionId)

    // The one rule for identity/dedup: everything else below this point (api_retry collapse,
    // the push itself, and its side effects) is unconditional bookkeeping, not a second
    // identity check — this is the only place a message can be skipped as a duplicate.
    const dedupKey = message.message_id || message.id
    if (dedupKey && messages.some(m => (m.message_id || m.id) === dedupKey)) {
      pushDebugEvent('message', 'dedup-skip', { sessionId, dedupKey })
      return
    }

    // Issue #894: api_retry in-place update — find existing message with same retry_message_id.
    // Open item (4b-B investigation): retry_message_id is injected into the live message_data
    // dict in SessionCoordinator._create_message_callback AFTER storage.append_message() has
    // already persisted the record (backend/claude_sdk.py::_process_sdk_message stores, then
    // invokes the callback with the same dict) — so a stored/archived api_retry record never
    // carries retry_message_id at all, by construction, not just by current absence. Unlike
    // subagent narration routing below (a gap that can close if archive task-leg hydration is
    // ever added), this one structurally can never fire for 'load'/'archive' — gated to 'live'
    // explicitly instead of left "uniform" for an invariant that will never hold otherwise.
    if (source === 'live' && message.metadata?.subtype === 'api_retry' && message.metadata?.retry_message_id) {
      const retryId = message.metadata.retry_message_id
      const existingIdx = messages.findIndex(m => m.metadata?.retry_message_id === retryId)
      if (existingIdx !== -1) {
        messages[existingIdx] = { ...messages[existingIdx], ...message }
        if (notify) messagesBySession.value = new Map(messagesBySession.value)
        return
      }
      // No existing message: fall through to normal push (first in sequence)
    }

    messages.push(message)

    // Issue #1955 (AC1: source gates preview only): any canonical TOP-LEVEL assistant append
    // clears whatever the live preview was showing. Excludes subagent narration
    // (metadata.parent_tool_use_id set): those messages share this session's id but belong to a
    // background Task/Agent leg, not the top-level stream the preview mirrors. Scoped to 'live'
    // — 'load'/'archive' have no in-flight cosmetic preview to reconcile against.
    if (source === 'live' && message.type === 'assistant' && !message.metadata?.parent_tool_use_id) {
      const canonicalToolIds = (message.metadata?.tool_uses || []).map(t => t.id)
      _clearStreamingPreviewContent(sessionId, canonicalToolIds)
    }

    // Issue #662/#1300 (open item 2): truncation/deferral banners are independent of the
    // deleted orphan-tracking subsystem — applied uniformly now (closes a real load/archive
    // gap: reloading or archive-viewing a session truncated by max_tokens, or one with an open
    // deferred tool, previously never showed the corresponding banner).
    if (message.type === 'result') {
      const stopReason = message.metadata?.stop_reason || message.stop_reason || null
      lastStopReasonBySession.value.set(sessionId, stopReason)
      const dtu = message.metadata?.deferred_tool_use || null
      deferredToolUseBySession.value.set(sessionId, dtu)
      if (notify) {
        lastStopReasonBySession.value = new Map(lastStopReasonBySession.value)
        deferredToolUseBySession.value = new Map(deferredToolUseBySession.value)
      }
    }
    if (message.type === 'user' && !message.metadata?.has_tool_results) {
      deferredToolUseBySession.value.set(sessionId, null)
      lastStopReasonBySession.value.set(sessionId, null)
      if (notify) {
        deferredToolUseBySession.value = new Map(deferredToolUseBySession.value)
        lastStopReasonBySession.value = new Map(lastStopReasonBySession.value)
      }
    }

    // Issue #1746 (stage: subagents) / #1765: apply live Task lifecycle frames to the
    // task_id-first store. Scoped to 'live' — 'load' relies on the caller's own
    // hydrateBackgroundAgents() backend snapshot instead (see that function's own comment).
    // 'archive' has NO equivalent hydration call anywhere today (SessionView.vue's
    // loadArchiveMessages() calls neither hydrateBackgroundAgents() nor this frame application) —
    // archived sessions' subagent-timeline legs are a pre-existing, out-of-scope gap this stage
    // does not close, not something this uniform-vs-live split is actually covering for archive.
    // Left gated to 'live' (rather than made unconditional like subagent narration below) because
    // applying it uniformly here alone, without also adding the missing hydration/replay path for
    // archive, would not actually close that gap — it would just be unreachable code for 'archive'
    // the same way it is today, so there's no benefit to changing the gate without the real fix.
    if (source === 'live' && message.type === 'system') {
      const subtype = message.metadata?.subtype
      if (TASK_LIFECYCLE_SUBTYPES.has(subtype)) {
        applyTaskLifecycleFrame(sessionId, subtype, message.metadata, message.timestamp)
      }
    }

    // Issue #1746 (stage: subagents) / open item: subagent narration routing, now applied
    // uniformly (closes the archive gap in the pre-refactor side-effect matrix). For archive
    // specifically this remains a no-op today — taskIdByLaunchToolUseId is never pre-seeded for
    // archive views (a separate, pre-existing gap outside this stage's scope) — but it costs
    // nothing to apply uniformly and stops being a no-op automatically if that gap is ever closed.
    if (message.type === 'assistant' && message.metadata?.parent_tool_use_id) {
      _routeSubagentNarration(message)
    }

    // Issue #1955 (AC1: source gates preview only) / AC2: restart/interrupt no longer sweep the
    // (deleted) browser-side orphan set — the backend's own tool_call record for an interrupted
    // tool already carries that terminal status directly. What's left here is purely the
    // cosmetic preview discard, scoped to 'live'.
    if (source === 'live' && message.type === 'system') {
      const subtype = message.metadata?.subtype
      if (subtype === 'client_launched') {
        _discardStreamingPreview(sessionId, 'restart')
      } else if (subtype === 'interrupt') {
        _discardStreamingPreview(sessionId, 'interrupt')
      }
    }

    // Launch timestamp tracking (issue #473) — uniform across sources (live and load already
    // did this identically before this stage).
    if (message.type === 'system' && message.metadata?.subtype === 'client_launched' && message.timestamp) {
      const ts = typeof message.timestamp === 'number'
        ? message.timestamp
        : new Date(message.timestamp).getTime() / 1000
      launchTimestampBySession.value.set(sessionId, ts)
    }

    // Trigger reactivity
    if (notify) messagesBySession.value = new Map(messagesBySession.value)
  }

  /**
   * Update a tool call (from WebSocket updates)
   */
  function updateToolCall(sessionId, toolUseId, updates) {
    const toolCalls = toolCallsBySession.value.get(sessionId)
    if (toolCalls) {
      const toolCall = toolCalls.find(tc => tc.id === toolUseId)
      if (toolCall) {
        Object.assign(toolCall, updates)

        // Trigger reactivity
        toolCallsBySession.value = new Map(toolCallsBySession.value)
      }
    }
  }

  /**
   * Handle permission response (user decision)
   */
  function handlePermissionResponse(sessionId, permissionResponse) {
    const toolUseId = permissionToToolMap.value.get(permissionResponse.request_id)

    if (toolUseId) {
      const updates = {
        permissionDecision: permissionResponse.decision,
        appliedUpdates: permissionResponse.applied_updates || []
      }

      if (permissionResponse.decision === 'allow') {
        updates.status = 'executing'
      } else {
        updates.status = 'completed'
        updates.result = {
          error: true,
          message: permissionResponse.reasoning || 'Permission denied'
        }
        updates.isExpanded = false
      }

      // For AskUserQuestion, update the input with answers from updated_input
      if (permissionResponse.updated_input) {
        updates.input = permissionResponse.updated_input
      }

      updateToolCall(sessionId, toolUseId, updates)
    }
  }

  /**
   * Issue #2110 (stage 4b-B): denied/interrupted terminal-resolution side effects, shared by
   * both the create and update branches of _applyToolCallRecord so a tool whose very FIRST
   * observed record is already denied/interrupted (common on 'load'/'archive', which can
   * replay a tool's only-ever event) gets the same result message / orphaned stamp as one that
   * transitions into that state live.
   */
  function _applyTerminalResolution(toolCallObj, toolCall) {
    if (toolCall.status === 'denied') {
      toolCallObj.result = { error: true, message: 'Permission denied' }
      toolCallObj.isExpanded = false
    }
    if (toolCall.status === 'interrupted') {
      toolCallObj._isOrphaned = true
      toolCallObj._orphanedInfo = { reason: 'denied', message: 'Session was interrupted' }
      toolCallObj.isExpanded = false
      toolCallObj.backendState = {
        state: 'interrupted',
        visible: true,
        collapsed: false,
        style: 'orphaned',
        linked_permission_id: toolCallObj.backendState?.linked_permission_id ?? null,
      }
    }
  }

  /**
   * Issue #324/#2110 (stage 4b-B): apply a unified tool_call record from the backend — the
   * complete tool lifecycle state in one payload, superseding separate tool_use/permission_
   * request/permission_response/tool_result correlation.
   *
   * `toolCall` fields: tool_use_id, name, input, status (pending/awaiting_permission/running/
   * completed/failed/denied/interrupted — the backend's own 7-value vocabulary), permission,
   * permission_granted, result, error, display.
   *
   * The backend status is normalized to the single frontend-display `status` field (5 values:
   * pending/permission_required/executing/completed/error — denied and interrupted both read as
   * 'completed', matching every direct `.status` consumer's expectation). Orphaned-ness (from
   * interrupted) is carried separately via the `_isOrphaned`/`_orphanedInfo` stamp, which
   * `useToolStatus.js`'s effectiveStatus/isOrphaned read directly — no backendStatus field, no
   * session-scoped orphan Map.
   */
  function _applyToolCallRecord(sessionId, toolCall, notify = true) {
    const toolUseId = toolCall.tool_use_id
    if (!toolUseId) {
      console.warn('Received tool_call without tool_use_id:', toolCall)
      return
    }

    if (!toolCallsBySession.value.has(sessionId)) {
      toolCallsBySession.value.set(sessionId, [])
    }

    const toolCalls = toolCallsBySession.value.get(sessionId)
    const existingIndex = toolCalls.findIndex(tc => tc.id === toolUseId)

    const statusMap = {
      'pending': 'pending',
      'awaiting_permission': 'permission_required',
      'running': 'executing',
      'completed': 'completed',
      'failed': 'error',
      'denied': 'completed',  // Denied shows as completed with special styling
      'interrupted': 'completed'  // Interrupted shows as completed with orphaned styling
    }
    const frontendStatus = statusMap[toolCall.status] || toolCall.status

    if (existingIndex !== -1) {
      // Update existing tool call
      const existing = toolCalls[existingIndex]

      // Guard: prevent regressing a terminal status to a non-terminal status
      const terminalStatuses = ['completed', 'error']
      const nonTerminalStatuses = ['pending', 'executing', 'permission_required']
      if (terminalStatuses.includes(existing.status) && nonTerminalStatuses.includes(frontendStatus)) {
        console.warn(`Ignoring status regression for tool ${toolUseId}: ${existing.status} → ${frontendStatus}`)
        return
      }
      // A tool already resolved via denial or interruption is terminal in a stronger sense than
      // plain 'completed'/'error' — a later conflicting tool_call update (e.g. a duplicate/stale
      // "failed" event) must not override the specific resolution already recorded. Locked via
      // the fields that already carry that specific resolution (permissionDecision/_isOrphaned)
      // rather than a separate backendStatus field.
      const lockedResolution = existing.permissionDecision === 'deny' ? 'denied'
        : existing._isOrphaned ? 'interrupted' : null
      if (lockedResolution && toolCall.status !== lockedResolution) {
        console.warn(`Ignoring conflicting update for ${lockedResolution} tool ${toolUseId}: → ${toolCall.status}`)
        return
      }

      existing.status = frontendStatus

      // Issue #1486: always accept the incoming input — the first event for a tool may arrive
      // with input:{} (from an intermediate AssistantMessage emitted while input_json_delta
      // events are still streaming); a subsequent event carries the fully-assembled input.
      if (toolCall.input !== undefined && toolCall.input !== null) {
        existing.input = toolCall.input
      }
      // Gated on turn_id like existing.messageId just below: a genuine live ToolCallUpdate
      // always carries turn_id, but the REST reload path can synthesize extra bookend
      // tool_call events (e.g. around an interrupted tool) that lack turn_id and carry a
      // stale/fallback created_at — those must not clobber the real captured timestamp.
      if (toolCall.created_at && toolCall.turn_id) {
        existing.timestamp = toolCall.created_at
      }

      // Issue #1774: persist AskUserQuestion answers independent of `.input`, which gets
      // overwritten by the terminal `completed` event (no `updated_input` on that event).
      if (toolCall.updated_input?.answers) {
        existing.answers = toolCall.updated_input.answers
      }

      // Issue #1694/#1958: owning assistant turn id, for permission-prompt anchoring
      if (toolCall.turn_id) {
        existing.messageId = toolCall.turn_id
      }

      // Update permission fields
      if (toolCall.permission) {
        existing.suggestions = toolCall.permission.suggestions || []
        existing.permissionRequestId = toolCall.request_id ?? existing.permissionRequestId  // request_id is added by backend for correlation
      }
      // Populate permissionToToolMap for handlePermissionResponse correlation
      if (toolCall.request_id && toolCall.status === 'awaiting_permission') {
        permissionToToolMap.value.set(toolCall.request_id, toolUseId)
        // Issue #699: Permission prompt notifications now driven by UI WebSocket
        // state_change → paused (see polling.js handleUIMessage)
      }
      if (toolCall.permission_granted !== null && toolCall.permission_granted !== undefined) {
        existing.permissionDecision = toolCall.permission_granted ? 'allow' : 'deny'
      }
      // Populate appliedUpdates from backend ToolCallUpdate data
      if (toolCall.applied_updates && toolCall.applied_updates.length > 0) {
        existing.appliedUpdates = toolCall.applied_updates
      }

      // Update result fields
      if (toolCall.result !== undefined) {
        existing.result = {
          error: toolCall.status === 'failed',
          content: toolCall.result
        }
      }
      if (toolCall.error) {
        existing.result = {
          error: true,
          content: toolCall.error
        }
      }

      _applyTerminalResolution(existing, toolCall)
      if (toolCall.status === 'completed' || toolCall.status === 'failed') {
        existing.isExpanded = false  // Auto-collapse on completion
      }

      // Store backend display hints if provided
      if (toolCall.display) {
        existing.backendState = toolCall.display
      }
      // Issue #707: Auto-approval indicator
      if (toolCall.auto_approved_reason) {
        existing.autoApprovedReason = toolCall.auto_approved_reason
      }
      // Issue #1593: Sender attachment resource IDs for outbound comm chips
      if (toolCall.sender_attachments != null) {
        existing.senderAttachments = toolCall.sender_attachments
      }
    } else {
      // Create new tool call entry
      const newToolCall = {
        id: toolUseId,
        name: toolCall.name,
        input: toolCall.input,
        status: frontendStatus,
        permissionRequestId: toolCall.request_id,
        permissionDecision: toolCall.permission_granted != null
          ? (toolCall.permission_granted ? 'allow' : 'deny')
          : null,
        appliedUpdates: toolCall.applied_updates || [],
        suggestions: toolCall.permission?.suggestions || [],
        result: toolCall.result ? {
          error: toolCall.status === 'failed',
          content: toolCall.result
        } : null,
        explanation: null,
        timestamp: toolCall.created_at || new Date().toISOString(),
        isExpanded: !['completed', 'failed', 'denied', 'interrupted'].includes(toolCall.status),
        backendState: toolCall.display,
        // Issue #195: Track parent Task tool for subagent grouping
        parent_tool_use_id: toolCall.parent_tool_use_id || null,
        // Issue #707: Auto-approval indicator
        autoApprovedReason: toolCall.auto_approved_reason || null,
        // Issue #953: Sub-agent ID for parallel permission disambiguation
        agentId: toolCall.agent_id || null,
        // Issue #1593: Sender attachment resource IDs for outbound comm chips
        senderAttachments: toolCall.sender_attachments || null,
        // Issue #1694/#1958: owning assistant turn id, for permission-prompt anchoring
        messageId: toolCall.turn_id || null,
        // Issue #1774: persist AskUserQuestion answers independent of `.input` churn
        answers: toolCall.updated_input?.answers || null,
      }

      if (toolCall.error) {
        newToolCall.result = {
          error: true,
          content: toolCall.error
        }
      }

      _applyTerminalResolution(newToolCall, toolCall)
      toolCalls.push(newToolCall)
      // Populate permissionToToolMap for handlePermissionResponse correlation
      if (toolCall.request_id && toolCall.status === 'awaiting_permission') {
        permissionToToolMap.value.set(toolCall.request_id, toolUseId)
        // Issue #699: Permission prompt notifications now driven by UI WebSocket
        // state_change → paused (see polling.js handleUIMessage)
      }
    }

    // Trigger reactivity
    if (notify) toolCallsBySession.value = new Map(toolCallsBySession.value)

    // Handle task tool results
    if (['completed', 'failed'].includes(toolCall.status) &&
        ['TaskCreate', 'TaskUpdate', 'TaskList', 'TaskGet'].includes(toolCall.name)) {
      try {
        const taskStore = useTaskStore()
        taskStore.handleTaskToolResult(sessionId, toolCall.name, toolCall.input, {
          error: toolCall.status === 'failed',
          content: toolCall.result
        })
      } catch (e) {
        console.warn('Failed to update task store:', e)
      }
    }
  }

  /**
   * Toggle tool call expansion
   */
  function toggleToolExpansion(sessionId, toolUseId) {
    const toolCalls = toolCallsBySession.value.get(sessionId)
    if (toolCalls) {
      const toolCall = toolCalls.find(tc => tc.id === toolUseId)
      if (toolCall) {
        toolCall.isExpanded = !toolCall.isExpanded

        // Trigger reactivity
        toolCallsBySession.value = new Map(toolCallsBySession.value)
      }
    }
  }

  /**
   * Clear messages for a session (for reset)
   */
  function clearMessages(sessionId) {
    _discardStreamingPreview(sessionId, 'clearMessages')  // Issue #1955: discard any in-flight preview
    // Issue #1748 review fix: prune before messages are gone (both read messagesBySession)
    pruneExpandedTimelineToolForSession(sessionId)
    pruneThinkingBlockExpandedForSession(sessionId)
    pruneExpandedCommsForSession(sessionId)
    messagesBySession.value.delete(sessionId)
    toolCallsBySession.value.delete(sessionId)

    // Issue #1746 (stage: subagents): clear only this session's task_id-scoped state
    // (ephemeral, reconstructs via hydrateBackgroundAgents + live frames on reload/reconnect).
    for (const [taskId, entry] of taskLegsByTaskId.value) {
      if (entry.session_id !== sessionId) continue
      taskLegsByTaskId.value.delete(taskId)
      narrationByTaskIdAndLeg.value.delete(taskId)
      for (const legIndex of entry.legs.keys()) {
        expandedLegs.value.delete(`${taskId}:${legIndex}`)
      }
      for (const leg of entry.legs) {
        if (leg.tool_use_id) taskIdByLaunchToolUseId.value.delete(leg.tool_use_id)
      }
    }
    taskLegsByTaskId.value = new Map(taskLegsByTaskId.value)
    taskIdByLaunchToolUseId.value = new Map(taskIdByLaunchToolUseId.value)
    narrationByTaskIdAndLeg.value = new Map(narrationByTaskIdAndLeg.value)
    expandedLegs.value = new Map(expandedLegs.value)

    // Trigger reactivity
    messagesBySession.value = new Map(messagesBySession.value)
    toolCallsBySession.value = new Map(toolCallsBySession.value)
  }

  // ========== STREAMING PREVIEW (Issue #1955) ==========
  // Cosmetic-only live-typing preview. Never a messagesBySession entry, never persisted, never
  // identity-bearing — see streamingPreviewBySession's own declaration comment for the model.

  function _startStreamingPreview(sessionId) {
    // No redelivery/overlap guarding needed (unlike the old placeholder model): a stray extra
    // message_start just resets cosmetic state — there is no data-loss risk because the preview
    // was never authoritative.
    const existing = streamingPreviewBySession.value.get(sessionId)
    pushDebugEvent('message', 'preview-start', {
      sessionId, hadExisting: !!existing, existingContentLen: existing?.content?.length || 0
    })
    streamingPreviewBySession.value.set(sessionId, {
      active: true,
      content: '',
      thinking: '',
      pendingText: '',
      pendingThinking: '',
      rafHandle: null,
      // Issue #1955 review fix: tracks whether a canonical frame for the CURRENTLY-OPEN turn
      // has already been observed, regardless of arrival order relative to deltas — see
      // _clearStreamingPreviewContent()/_endStreamingPreview() for why this makes dismissal
      // level-triggered instead of edge-triggered.
      canonicalSeen: false,
      // Issue #1573: tool_use ids (content_block_start) registered for THIS still-open turn
      // that have no rendering surface of their own yet — see _registerPendingToolInPreview().
      pendingTools: [],
      // Issue #1573 (review fix): tool_use ids already claimed by a canonical assistant message
      // for this still-open turn — see _clearStreamingPreviewContent(). Deliberately NOT the
      // same thing as `canonicalSeen`: _applyMessageRecord()'s own #1955 comment documents that a single
      // still-open turn can carry MULTIPLE canonical assistant messages ("multi-canonical-
      // message turn"), so `canonicalSeen` flips true after the FIRST one and then stays true
      // for the rest of the turn — gating registration on it would silently drop the indicator
      // for any tool whose content_block_start arrives after that first canonical message but
      // before ITS OWN. Gating per-id on `claimedToolIds` instead means each tool is judged only
      // against whether its own canonical message has actually landed yet.
      claimedToolIds: new Set(),
    })
    streamingPreviewBySession.value = new Map(streamingPreviewBySession.value)
  }

  /**
   * Issue #1573: record a newly-started tool_use so StreamingPreview.vue can show a transient
   * "Starting: <name>..." indicator for it. This follows the same level-triggered pattern as
   * `canonicalSeen` (see _clearStreamingPreviewContent()'s comment for the full rationale):
   * rather than assuming content_block_start always arrives before the canonical assistant
   * message for this turn, check the CURRENT state at the time this fires —
   * - `!preview.active`: this turn has already ended (message_stop seen) — a content_block_start
   *   this late is a stray/out-of-order delivery; the turn it belonged to is over, so there is
   *   nothing left to show a live indicator for.
   * - `preview.claimedToolIds.has(toolId)`: THIS tool's own canonical message already landed —
   *   the real tool card is already rendering via AssistantMessage.vue's normal segment-based
   *   path, so surfacing a duplicate "Starting..." indicator here would be a stale duplicate,
   *   not a lead (covers both a stray redelivery of this same content_block_start, and the
   *   general out-of-order-across-channels case).
   * Clearing happens in _clearStreamingPreviewContent(), scoped to exactly the tool ids the
   * arriving canonical message actually claims — i.e. each tool's indicator is handed off to
   * its own real card at the instant THAT card gains a rendering surface, never on an earlier
   * edge-triggered guess, and never blocked by an unrelated tool's canonical message landing
   * first in a multi-canonical-message turn.
   */
  function _registerPendingToolInPreview(sessionId, toolId, toolName) {
    const preview = streamingPreviewBySession.value.get(sessionId)
    if (!preview || !preview.active || preview.claimedToolIds.has(toolId)) return
    if (preview.pendingTools.some(t => t.id === toolId)) return
    preview.pendingTools = [...preview.pendingTools, { id: toolId, name: toolName }]
    streamingPreviewBySession.value = new Map(streamingPreviewBySession.value)
  }

  function _flushPreviewDelta(sessionId) {
    const preview = streamingPreviewBySession.value.get(sessionId)
    if (!preview) return
    if (preview.pendingText === '' && preview.pendingThinking === '') { preview.rafHandle = null; return }

    preview.content += preview.pendingText
    preview.thinking += preview.pendingThinking
    const addedChars = preview.pendingText.length + preview.pendingThinking.length
    preview.pendingText = ''
    preview.pendingThinking = ''
    preview.rafHandle = null

    streamingPreviewBySession.value = new Map(streamingPreviewBySession.value)
    pushDebugEvent('message', 'preview-delta', {
      sessionId, textLen: preview.content.length, thinkingLen: preview.thinking.length, addedChars
    })
  }

  /**
   * Issue #1955 (review fix, found via live testing: single-frame turns could leave a
   * permanent duplicate preview): resets the preview's displayed content whenever a canonical
   * assistant message appends (called from _applyMessageRecord(), live source only) — leaves `active`
   * untouched so a still-open stream keeps its caret. This is the mechanism resolving
   * "multi-canonical-message-per-turn": any canonical append clears whatever the preview was
   * showing, with no identity matching involved.
   *
   * Also marks `canonicalSeen = true` unconditionally (even when this call is a no-op because
   * content was already empty) — the canonical and delta channels are delivered independently
   * and CAN arrive out of order: a single-frame turn's canonical frame occasionally lands
   * before its own deltas, so this clear fires against an empty preview, the deltas then fill
   * it in afterward, and with only one frame in the turn there is no SECOND canonical append to
   * clear it again. `canonicalSeen` lets _endStreamingPreview() (message_stop) catch that case
   * and dismiss the preview itself once the canonical has definitely landed, regardless of
   * which arrived first — making dismissal level-triggered instead of edge-triggered.
   *
   * Issue #1573: also hands off `pendingTools` here, scoped to exactly the tool_use ids the
   * arriving canonical message carries (`canonicalToolIds`) — NOT a wholesale wipe of the whole
   * array. A wholesale wipe gated on this same call site would be wrong for a multi-canonical-
   * message turn: `canonicalSeen` is a one-way flag (true for the rest of the turn after the
   * FIRST canonical message), but a SECOND tool_use in the same still-open turn can start
   * streaming after that first message and before its OWN canonical message arrives — a
   * wholesale-wipe-on-first-canonical would silently drop that second tool's indicator with no
   * real card yet to replace it (reproducing the exact invisible-card gap this feature exists to
   * close). Recording claimed ids into `claimedToolIds` and filtering by id keeps each tool's
   * handoff independent of every other tool's in the same turn.
   */
  function _clearStreamingPreviewContent(sessionId, canonicalToolIds = []) {
    const preview = streamingPreviewBySession.value.get(sessionId)
    if (!preview) return
    pushDebugEvent('message', 'preview-clear', { sessionId, lenBefore: preview.content.length })
    preview.content = ''
    preview.thinking = ''
    preview.canonicalSeen = true
    if (canonicalToolIds.length) {
      for (const id of canonicalToolIds) preview.claimedToolIds.add(id)
      if (preview.pendingTools.length) {
        preview.pendingTools = preview.pendingTools.filter(t => !preview.claimedToolIds.has(t.id))
      }
    }
    streamingPreviewBySession.value = new Map(streamingPreviewBySession.value)
  }

  /**
   * Issue #1955: called from message_stop. Flushes any pending delta and ends the caret. Does
   * NOT clear content/thinking on its own — the preview persists (frozen) until the canonical
   * message actually appends (see _clearStreamingPreviewContent), so the swap from preview to
   * canonical message happens as one atomic visual transition instead of a gap.
   *
   * EXCEPTION (review fix): if the canonical frame for this turn was already observed BEFORE
   * message_stop (preview.canonicalSeen), clearing now is safe — the canonical bubble is
   * already showing, so there is no gap to create — and is in fact required, since a
   * single-frame turn has no future canonical append left to do it.
   */
  function _endStreamingPreview(sessionId) {
    const preview = streamingPreviewBySession.value.get(sessionId)
    if (!preview) return
    if (preview.rafHandle) { cancelAnimationFrame(preview.rafHandle); preview.rafHandle = null }
    _flushPreviewDelta(sessionId)
    preview.active = false
    if (preview.canonicalSeen) {
      preview.content = ''
      preview.thinking = ''
    }
    streamingPreviewBySession.value = new Map(streamingPreviewBySession.value)
    const nonEmpty = !!(preview.content || preview.thinking)
    pushDebugEvent('message', 'preview-end', {
      sessionId, finalLen: preview.content.length, nonEmpty, canonicalSeen: preview.canonicalSeen
    })
    // A non-empty preview at message_stop is by definition a leftover — auto-capture the
    // preceding timeline immediately rather than relying on a user noticing and reporting it.
    if (nonEmpty) flushDebugBuffer('preview-leftover')
  }

  /**
   * Issue #1955: discard the preview outright — used by interrupt, restart, session-terminated,
   * and the top of loadMessages()/clearMessages(). The preview never holds authoritative data,
   * so there is nothing to finalize or splice; simply deleting the map entry is always correct.
   */
  function _discardStreamingPreview(sessionId, reason = 'unspecified') {
    if (!streamingPreviewBySession.value.has(sessionId)) return
    pushDebugEvent('message', 'preview-discard', { sessionId, reason })
    streamingPreviewBySession.value.delete(sessionId)
    streamingPreviewBySession.value = new Map(streamingPreviewBySession.value)
  }

  function handleAssistantDelta(sessionId, data) {
    // data = { uuid, event } where event is the raw Anthropic streaming event dict
    const eventType = data?.event?.type
    if (!eventType) return

    switch (eventType) {
      case 'message_start':
        _startStreamingPreview(sessionId)
        break

      case 'content_block_start': {
        // Issue #1573: render a pending tool card as soon as the model commits to a tool
        // call, instead of waiting for the full turn to arrive.
        const block = data.event.content_block
        if (block?.type === 'tool_use' && block.id) {
          applyRecord(sessionId, {
            type: 'tool_call',
            tool_use_id: block.id,
            name: block.name,
            input: {},
            status: 'pending'
          }, 'live')
          // Skip the preview indicator below when the card is already terminal:
          // _applyToolCallRecord's status-regression guard silently no-ops on a stray/late
          // content_block_start for an already-completed tool, and showing a "starting..."
          // indicator for it would be wrong.
          const currentCard = toolCallsBySession.value.get(sessionId)?.find(tc => tc.id === block.id)
          if (currentCard && !['completed', 'error'].includes(currentCard.status)) {
            // The card itself has no rendering surface yet — AssistantMessage.vue only shows
            // tool cards that belong to a message "segment", and none exists until the
            // canonical assistant message for this turn lands. Mirror it into the streaming
            // preview's own pendingTools list so StreamingPreview.vue can show a transient
            // "Starting: <name>..." indicator in the meantime.
            _registerPendingToolInPreview(sessionId, block.id, block.name)
          }
        }
        break
      }

      case 'content_block_delta': {
        const preview = streamingPreviewBySession.value.get(sessionId)
        if (!preview) break
        const deltaType = data.event.delta?.type
        if (deltaType === 'text_delta') {
          preview.pendingText += data.event.delta.text || ''
          if (!preview.rafHandle) preview.rafHandle = requestAnimationFrame(() => _flushPreviewDelta(sessionId))
        } else if (deltaType === 'thinking_delta') {
          preview.pendingThinking += data.event.delta.thinking || ''
          if (!preview.rafHandle) preview.rafHandle = requestAnimationFrame(() => _flushPreviewDelta(sessionId))
        }
        // input_json_delta: out of scope per §2, ignore
        break
      }

      case 'message_stop':
        _endStreamingPreview(sessionId)
        break
    }
  }

  // ========== SESSION STATE WATCHER ==========
  // Watch for session state changes to detect post-load terminations
  const sessionStore = useSessionStore()
  watch(
    () => {
      const sessions = Array.from(sessionStore.sessions.values())
      return sessions.map(s => ({ id: s.session_id, state: s.state }))
    },
    (newStates, oldStates) => {
      if (!oldStates) return

      // Check each session for state transitions
      newStates.forEach((newState, idx) => {
        const oldState = oldStates[idx]
        if (!oldState || oldState.id !== newState.id) return

        // Session transitioned from active/paused/starting to terminated/error
        const wasActive = ['active', 'paused', 'starting'].includes(oldState.state)
        const isInactive = !['active', 'paused', 'starting'].includes(newState.state)

        if (wasActive && isInactive) {
          // Issue #1955: discard any in-flight cosmetic preview so the caret doesn't linger.
          // AC2: no browser-side orphan sweep needed anymore — the backend's own tool_call
          // record for an interrupted tool already carries that terminal status directly.
          _discardStreamingPreview(newState.id, 'session-terminated')
        }
      })
    },
    { deep: true }
  )

  // ========== ARCHIVE SUPPORT (Issue #577) ==========

  /**
   * Set messages for an archived session view.
   * Issue #621: Process through the same unified tool pipeline as loadMessages()
   * so that tool_call messages populate toolCallsBySession and are filtered
   * from the display list. This ensures archived tools show correct completion
   * status and no blank message bubbles appear.
   */
  function setArchiveMessages(sessionId, rawMessages) {
    // Issue #2110 (stage 4b-B): thin apply-per-record wrapper — reset the display list, then
    // let applyRecord() do the uniform work (identity dedup, api_retry collapse, truncation/
    // deferral banners, subagent narration routing, tool_call upsert).
    messagesBySession.value.set(sessionId, [])

    rawMessages.forEach(msg => {
      // Note: tool_call records need no special-case here — applyRecord() itself routes them
      // to the tool_call branch internally and never pushes them to the display list, so they
      // fall through the user/system filters below (neither matches type 'tool_call') straight
      // to the uniform applyRecord() call at the end of this callback.

      // Filter UserMessage entries that are purely tool results (no displayable text) —
      // archive-specific; live/load show these (they correlate the result against its tool
      // card inline instead of hiding it).
      if (msg.type === 'user' && Array.isArray(msg.content)) {
        const hasDisplayableContent = msg.content.some(
          block => block.type !== 'tool_result'
        )
        if (!hasDisplayableContent) {
          return
        }
      }

      // Filter SystemMessage entries with no displayable content — archive-specific.
      // (The 'status'/'requesting' suppression applyRecord/addMessage/loadMessages already
      // apply uniformly covers that case for every source, including this one.)
      if (msg.type === 'system') {
        const subtype = msg.subtype || msg.metadata?.subtype
        if (subtype === 'init') {
          // Issue #1829: capture init data for the archive-scoped Info modal view
          // before the (possibly early) return below discards this message.
          if (msg.metadata?.init_data) {
            sessionStore.storeArchiveInitData(sessionId, msg.metadata.init_data)
          }
          if (!msg.content) {
            return
          }
        }
      }

      // notify:false — see applyRecord()'s own comment: avoid O(N) reactive broadcasts while
      // replaying a whole archived session in one shot; one final reassignment below covers it.
      applyRecord(sessionId, msg, 'archive', { notify: false })
    })
    // Ensure reactivity even when `rawMessages` was empty or fully filtered out.
    messagesBySession.value = new Map(messagesBySession.value)
    toolCallsBySession.value = new Map(toolCallsBySession.value)
    lastStopReasonBySession.value = new Map(lastStopReasonBySession.value)
    deferredToolUseBySession.value = new Map(deferredToolUseBySession.value)
  }

  /**
   * Clear archive messages when leaving archive view.
   */
  function clearArchiveMessages(sessionId) {
    pruneExpandedTimelineToolForSession(sessionId)  // Issue #1748 review fix
    pruneThinkingBlockExpandedForSession(sessionId)
    pruneExpandedCommsForSession(sessionId)
    messagesBySession.value.delete(sessionId)
    toolCallsBySession.value.delete(sessionId)
    // Issue #2110 (stage 4b-B review fix): applyRecord's uniform side effects now also write
    // lastStopReasonBySession/deferredToolUseBySession/launchTimestampBySession for the
    // 'archive' source — these are keyed only by sessionId, which an archive view shares with
    // its live counterpart (SessionView.vue reuses props.sessionId for both routes). Without
    // clearing them here too, an archived snapshot's stale truncation/deferral-banner or launch-
    // timestamp state would leak into the live session view when it next reactivates, exactly
    // like messagesBySession/toolCallsBySession above already guard against.
    lastStopReasonBySession.value.delete(sessionId)
    deferredToolUseBySession.value.delete(sessionId)
    launchTimestampBySession.value.delete(sessionId)
    sessionStore.clearArchiveInitData(sessionId)
  }

  // ========== HOOK CORRELATION (Issue #1350) ==========

  /**
   * Return the cached HookCorrelationResult for a session, recomputing only when
   * the message stream has actually advanced (different count or last message ID).
   */
  function getHookCorrelation(sessionId) {
    const messages = messagesBySession.value.get(sessionId) || []
    const count = messages.length
    const lastId = count > 0 ? (messages[count - 1].message_id || messages[count - 1].id || null) : null

    const cached = _hookCorrelationCache.get(sessionId)
    if (cached && cached.messageCount === count && cached.lastId === lastId) {
      return cached.result
    }

    const toolCalls = toolCallsBySession.value.get(sessionId) || []
    const result = correlateHooks(messages, toolCalls)
    _hookCorrelationCache.set(sessionId, { result, messageCount: count, lastId })
    return result
  }

  /** Returns hooks correlated to a specific tool call (PreToolUse + PostToolUse). */
  function hooksForToolCall(sessionId, toolId) {
    if (!sessionId || !toolId) return []
    return getHookCorrelation(sessionId).hooksByToolId.get(toolId) || []
  }

  /** Returns hooks correlated to a user or assistant message (UserPromptSubmit / Stop / etc.). */
  function hooksForMessageId(sessionId, messageId) {
    if (!sessionId || !messageId) return []
    return getHookCorrelation(sessionId).hooksByMessageId.get(messageId) || []
  }

  /** Returns hooks correlated to a compaction event group by ordinal index. */
  function hooksForCompaction(sessionId, groupIndex) {
    if (!sessionId || groupIndex == null || groupIndex < 0) return []
    return getHookCorrelation(sessionId).hooksByCompactionIndex.get(groupIndex) || []
  }

  /**
   * Returns true when a hook system message has been successfully correlated to a
   * parent element and should be hidden from the top-level displayable list.
   */
  function isHookMessageAttached(sessionId, messageId) {
    if (!sessionId || !messageId) return false
    return getHookCorrelation(sessionId).attachedHookMessageIds.has(messageId)
  }

  // ========== AGENT NOTIFICATIONS (Issue #1676) ==========
  // Background subagent notifications (agent_needs_input / agent_completed), tagged
  // by the backend as system messages with metadata.subtype === 'agent_notification'.
  // Rendered session-level via AgentNotificationStrip.vue rather than anchored to a
  // specific message/tool card (no reliable tool_use_id correlation — see #1676 plan).

  /**
   * Returns active (non-dismissed) agent notifications for a session, oldest first.
   *
   * Issue #1676: the CLI's include_hook_events plumbing is not confirmed to emit exactly
   * one message per Notification event (hook lifecycle events generally arrive as a
   * hook_started/hook_response pair) — dedupe by (notificationType, message) so an
   * unconfirmed duplicate phase never renders as two rows for the same event.
   */
  function agentNotificationsForSession(sessionId) {
    if (!sessionId) return []
    const messages = messagesBySession.value.get(sessionId) || []
    const dismissed = dismissedAgentNotifications.value.get(sessionId)

    const results = []
    const seen = new Set()
    for (const msg of messages) {
      if (msg.type !== 'system' || msg.metadata?.subtype !== 'agent_notification') continue
      // HookEventMessage.uuid is the only stable per-event id the backend surfaces for
      // this subtype; message_id/id are never populated for system messages.
      const id = msg.metadata?.uuid || msg.message_id || msg.id
      if (id && dismissed?.has(id)) continue

      const notificationType = msg.metadata?.notification_type || null
      const message = msg.metadata?.message || msg.content || ''
      const dedupeKey = `${notificationType}::${message}`
      if (seen.has(dedupeKey)) continue
      seen.add(dedupeKey)

      results.push({
        id,
        notificationType,
        label: msg.metadata?.label || null,
        message,
        title: msg.metadata?.title || null,
        timestamp: msg.timestamp || null,
        dismissed: false,
      })
    }
    return results
  }

  /** Dismisses a single agent notification for a session (session-local, not persisted). */
  function dismissAgentNotification(sessionId, notificationId) {
    if (!sessionId || !notificationId) return
    if (!dismissedAgentNotifications.value.has(sessionId)) {
      dismissedAgentNotifications.value.set(sessionId, new Set())
    }
    dismissedAgentNotifications.value.get(sessionId).add(notificationId)
    dismissedAgentNotifications.value = new Map(dismissedAgentNotifications.value)
  }

  // ========== RETURN ==========
  return {
    // State
    messagesBySession,
    toolCallsBySession,

    // Computed
    currentMessages,
    currentToolCalls,

    // Actions
    loadMessages,
    // Issue #2110 (stage 4b-B): the one entry point for every message/tool_call record,
    // regardless of source ('live' | 'load' | 'archive') — supersedes addMessage/handleToolCall.
    applyRecord,
    updateToolCall,
    handlePermissionResponse,
    toggleToolExpansion,
    clearMessages,
    // Issue #1486/#1955: streaming delta handler
    handleAssistantDelta,
    // Issue #1955: cosmetic-only live-typing preview, read reactively by StreamingPreview.vue
    streamingPreviewBySession: readonly(streamingPreviewBySession),

    // Launch timestamp tracking (Issue #473)
    launchTimestampBySession: readonly(launchTimestampBySession),

    // Archive support (Issue #577)
    setArchiveMessages,
    clearArchiveMessages,

    // Issue #662: Stop reason tracking for truncation banner
    lastStopReasonBySession: readonly(lastStopReasonBySession),

    // Issue #1300: Deferred tool use tracking for deferral banner
    deferredToolUseBySession: readonly(deferredToolUseBySession),

    // Issue #1746 (stage: subagents) / #1765: task_id-first background-agent tracking
    applyTaskLifecycleFrame,
    hydrateBackgroundAgents,
    getTaskLegEntry,
    getTaskIdForLaunchToolUse,
    narrationForLeg,
    childToolCallsForLeg,
    allTaskLegEntriesForSession,
    isLegExpanded,
    setLegExpanded,
    toggleLegExpanded,
    getExpandedTimelineTool,
    isExpandedTimelineToolAutoPermission,
    setExpandedTimelineTool,
    isThinkingBlockExpanded,
    toggleThinkingBlockExpanded,
    isCommExpanded,
    toggleCommExpanded,
    hasOpenPermissionForTask,
    openPermissionsForSession,

    // Issue #1000: Event cursors from REST /messages, consumed by connectSession()
    loadedEventCursors,

    // Issue #1350: Hook correlation helpers
    hooksForToolCall,
    hooksForMessageId,
    hooksForCompaction,
    isHookMessageAttached,

    // Issue #1676: Background subagent notifications
    agentNotificationsForSession,
    dismissAgentNotification,
  }
})
