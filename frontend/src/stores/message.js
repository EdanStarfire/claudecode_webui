import { defineStore } from 'pinia'
import { ref, computed, readonly, watch } from 'vue'
import { api } from '../utils/api'
import { useSessionStore } from './session'
import { useTaskStore } from './task'
import { correlateHooks } from '../utils/hookCorrelation'

/**
 * Message Store - Manages messages and tool calls per session
 *
 * Issue #310: Backend-driven display metadata
 * When messages contain display metadata from the backend (DisplayProjection),
 * the store uses that instead of computing tool states locally. This reduces
 * frontend complexity and ensures consistent state across reconnections.
 */
export const useMessageStore = defineStore('message', () => {
  // ========== STATE ==========

  // Messages per session (sessionId -> Message[])
  const messagesBySession = ref(new Map())

  // Tool calls per session (sessionId -> ToolCall[])
  const toolCallsBySession = ref(new Map())

  // Tool call manager state (tracking tool lifecycle)
  // Per-session signature map: Map<sessionId, Map<signature, toolUseId>>
  // Issue #953: Deprecated — direct tool_use_id from backend preferred (SDK v0.1.52+)
  const toolSignatureToId = ref(new Map())
  const permissionToToolMap = ref(new Map())

  // Issue #1000: Event cursors returned by REST /messages endpoint.
  // Used by websocket.connectSession() to start polling from the correct position.
  const loadedEventCursors = new Map()

  // Orphaned tool tracking (sessionId -> Set<tool_use_id>)
  const activeToolUses = ref(new Map())
  const orphanedToolUses = ref(new Map()) // sessionId -> Map<tool_use_id -> {reason, message}>

  // Message sequence tracking for reconnection sync (sessionId -> ISO timestamp)
  const lastReceivedTimestamp = ref(new Map())

  // Launch timestamp tracking (sessionId -> Unix timestamp in seconds)
  // Populated from client_launched system messages for uptime calculation
  const launchTimestampBySession = ref(new Map())

  // Issue #310: Backend display metadata cache (sessionId -> Map<tool_use_id -> ToolDisplayInfo>)
  // When backend provides display metadata, we store it here for quick lookup
  const backendToolStates = ref(new Map())

  // Issue #662: Track last stop_reason per session for truncation banner
  const lastStopReasonBySession = ref(new Map())

  // Issue #1300: Track deferred tool use per session for deferral banner
  // Map<sessionId, { id, name, input } | null>
  const deferredToolUseBySession = ref(new Map())

  // Issue #689: Latest task activity per tool_use_id for SubagentTimeline display
  // Map<tool_use_id, { subtype, description, last_tool_name, status, summary, timestamp }>
  const taskActivityByToolUseId = ref(new Map())

  // Issue #1486: Per-session streaming delta buffers (in-memory only, never persisted)
  // Map<sessionId, { messageId, pendingText, pendingThinking, blockTypeByIndex, rafHandle }>
  const _deltaBuffers = new Map()

  // Issue #1350: Hook correlation cache — non-reactive, internal memoization only.
  // Shape: Map<sessionId, { result: HookCorrelationResult, messageCount: number, lastId: string|null }>
  const _hookCorrelationCache = new Map()

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

  /**
   * Load messages for a session from backend
   */
  async function loadMessages(sessionId, limit = null, offset = 0) {
    try {
      // If no limit specified, request all messages
      // Use a very high limit to get all messages in one request
      const effectiveLimit = limit || 10000

      const data = await api.get(
        `/api/sessions/${sessionId}/messages?limit=${effectiveLimit}&offset=${offset}`
      )

      const messages = data.messages || []
      const totalCount = data.total_count || messages.length
      const hasMore = data.has_more || false

      // Issue #1000: Store event cursor from REST response for poll alignment
      if (data.event_cursor !== undefined) {
        loadedEventCursors.set(sessionId, data.event_cursor)
      }

      console.log(`Loaded ${messages.length} of ${totalCount} messages for session ${sessionId}`)

      // Issue #491: Process messages through unified tool_call path.
      // Backend now generates interleaved tool_call messages in history,
      // so we route them through handleToolCall() — same as real-time WebSocket.
      const regularMessages = []

      messages.forEach(message => {
        // Route tool_call messages through unified handler (same path as real-time)
        // Pass silent: true to suppress notifications for historical data
        if (message.type === 'tool_call') {
          handleToolCall(sessionId, message, { silent: true })
          return // Don't add tool_call to message display list
        }

        // Issue #1575: drop internal SDK status/requesting messages — match real-time suppression
        if (message.type === 'system') {
          const subtype = message.subtype || message.metadata?.subtype
          const status = message.metadata?.init_data?.status
          if (subtype === 'status' && status === 'requesting') {
            return
          }
        }

        // Track launch timestamp for uptime calculation
        if (message.type === 'system' && message.metadata?.subtype === 'client_launched') {
          if (message.timestamp) {
            const ts = typeof message.timestamp === 'number'
              ? message.timestamp
              : new Date(message.timestamp).getTime() / 1000
            launchTimestampBySession.value.set(sessionId, ts)
          }
        }

        // Capture init data for session info modal
        if (message.type === 'system' &&
            (message.subtype === 'init' || message.metadata?.subtype === 'init') &&
            message.metadata?.init_data) {
          const sessionStore = useSessionStore()
          sessionStore.storeInitData(sessionId, message.metadata.init_data)
        }

        // Issue #894: Collapse api_retry sequences — keep only the latest for each retry_message_id
        if (message.metadata?.subtype === 'api_retry' && message.metadata?.retry_message_id) {
          const retryId = message.metadata.retry_message_id
          const existingIdx = regularMessages.findIndex(m => m.metadata?.retry_message_id === retryId)
          if (existingIdx !== -1) {
            regularMessages[existingIdx] = message // Replace with later (higher attempt) data
            return
          }
        }

        regularMessages.push(message)
      })

      // Store only regular messages (tool_call messages are handled separately)
      messagesBySession.value.set(sessionId, regularMessages)

      // Track last received timestamp from loaded messages (use last regular message)
      if (regularMessages.length > 0) {
        const lastMessage = regularMessages[regularMessages.length - 1]
        if (lastMessage.timestamp) {
          lastReceivedTimestamp.value.set(sessionId, lastMessage.timestamp)
        }
      }

      // Trigger reactivity
      messagesBySession.value = new Map(messagesBySession.value)

      // Issue #689: Reconstruct task activity from message history for SubagentTimeline
      regularMessages.forEach(msg => {
        if (msg.type === 'system') {
          const subtype = msg.metadata?.subtype
          const toolUseId = msg.metadata?.tool_use_id
          if (toolUseId && ['task_started', 'task_progress', 'task_notification'].includes(subtype)) {
            taskActivityByToolUseId.value.set(toolUseId, {
              subtype,
              description: msg.metadata?.description || msg.content,
              last_tool_name: msg.metadata?.last_tool_name || null,
              status: msg.metadata?.status || null,
              summary: msg.metadata?.summary || null,
              timestamp: msg.timestamp
            })
          }
        }
      })
      taskActivityByToolUseId.value = new Map(taskActivityByToolUseId.value)

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
   * Issue #689: Get task activity data for a tool_use_id (used by SubagentTimeline)
   */
  function getTaskActivity(toolUseId) {
    return taskActivityByToolUseId.value.get(toolUseId) || null
  }

  /**
   * Handle real-time tool use tracking for orphaned detection
   */
  function handleRealtimeToolTracking(sessionId, message) {
    const openTools = activeToolUses.value.get(sessionId) || new Set()

    // Track new tool uses
    if (message.type === 'assistant' && message.metadata?.tool_uses) {
      message.metadata.tool_uses.forEach(toolUse => {
        openTools.add(toolUse.id)
      })
    }

    // Close tool uses on results
    if (message.type === 'user' && message.metadata?.tool_results) {
      message.metadata.tool_results.forEach(result => {
        openTools.delete(result.tool_use_id)
        // Clear from orphaned if previously marked
        const orphaned = orphanedToolUses.value.get(sessionId)
        if (orphaned) {
          orphaned.delete(result.tool_use_id)
        }
      })
    }

    // Detect restart during real-time
    if (message.type === 'system' && message.metadata?.subtype === 'client_launched') {
      openTools.forEach(id => {
        markToolUseOrphaned(sessionId, id, 'Session was restarted')
      })
      openTools.clear()
      // Track launch timestamp for uptime calculation
      if (message.timestamp) {
        const ts = typeof message.timestamp === 'number'
          ? message.timestamp
          : new Date(message.timestamp).getTime() / 1000
        launchTimestampBySession.value.set(sessionId, ts)
      }
      // Issue #1486: cancel any in-flight streaming placeholder
      _stopStreamingPlaceholder(sessionId)
    }

    // Detect interrupt during real-time
    if (message.type === 'system' && message.metadata?.subtype === 'interrupt') {
      openTools.forEach(id => {
        markToolUseOrphaned(sessionId, id, 'Session was interrupted')
      })
      openTools.clear()
      // Issue #1486: flush pending deltas and stop streaming caret
      _stopStreamingPlaceholder(sessionId)
    }

    activeToolUses.value.set(sessionId, openTools)
  }

  /**
   * Add a message to a session (from WebSocket)
   * Now with deduplication to prevent duplicate messages on reconnection
   *
   * Issue #310: Also applies backend display metadata if present
   */
  function addMessage(sessionId, message) {
    if (!messagesBySession.value.has(sessionId)) {
      messagesBySession.value.set(sessionId, [])
    }

    // Issue #1486: drop internal SDK status/requesting messages — they are not displayable
    if (message.type === 'system') {
      const subtype = message.subtype || message.metadata?.subtype
      const status = message.metadata?.init_data?.status
      if (subtype === 'status' && status === 'requesting') {
        return
      }
    }

    const messages = messagesBySession.value.get(sessionId)

    // Issue #1614: Generalized defer — if a stream is active for this session,
    // any terminal assistant message must wait for message_stop to splice into the placeholder.
    // This is keyed on "stream active" (buffer present), not on message_id match, so it handles
    // terminals whose IDs were absent or mismatched (e.g. extended-thinking multi-AM turns).
    if (message.type === 'assistant' && !message.streaming && _deltaBuffers.has(sessionId)) {
      const buf = _deltaBuffers.get(sessionId)
      if (!buf.collectedTerminalMessages) buf.collectedTerminalMessages = []
      buf.collectedTerminalMessages.push(message)
      return
    }

    // Issue #1626 + #1601 + #1486: placeholder-merge precedence.
    // Match by message_id whether or not the placeholder is still streaming. This
    // closes a race where message_stop arrives before the terminal AM, leaving the
    // placeholder finalized (streaming:false) but missing metadata.tool_uses; the
    // subsequent dedup branch was silently dropping the terminal in that case.
    const placeholderKey = message.metadata?.message_id
    if (placeholderKey) {
      const placeholderIdx = messages.findIndex(m => m.message_id === placeholderKey)
      if (placeholderIdx !== -1) {
        const existing = messages[placeholderIdx]
        if (existing.streaming === true) {
          // Stream still active — collect into buffer for splice at message_stop.
          const buf = _deltaBuffers.get(sessionId)
          if (buf) {
            if (!buf.collectedTerminalMessages) buf.collectedTerminalMessages = []
            buf.collectedTerminalMessages.push(message)
            return
          }
          // No buffer (page reload mid-stream) — merge directly into placeholder.
          messages[placeholderIdx] = { ...existing, ...message, streaming: false }
          messagesBySession.value = new Map(messagesBySession.value)
          return
        }
        // Placeholder already finalized — merge terminal's metadata in.
        // Preserve any accumulated streaming text/thinking in case the terminal
        // arrives with empty content (some SDK paths do this).
        messages[placeholderIdx] = {
          ...existing,
          ...message,
          content: message.content || existing.content,
          thinking: message.thinking || existing.thinking,
          streaming: false,
        }
        messagesBySession.value = new Map(messagesBySession.value)
        applyDisplayMetadata(sessionId, message)
        handleRealtimeToolTracking(sessionId, message)
        if (message.timestamp) {
          lastReceivedTimestamp.value.set(sessionId, message.timestamp)
        }
        return
      }
    }

    // Issue #1000: Step 2 — backend-UUID dedup. Deduplicate by message_id (stable UUID from
    // backend) or id. The Anthropic-ID fallback was removed from dedupKey; that case is handled
    // above by the streaming-placeholder collection path.
    const dedupKey = message.message_id || message.id
    if (dedupKey) {
      const existingIndex = messages.findIndex(m => (m.message_id || m.id) === dedupKey)
      if (existingIndex !== -1) {
        console.log(`Skipping duplicate message ${dedupKey} (already exists at index ${existingIndex})`)
        return
      }
    }

    // Issue #1486: fallback dedup — when terminal assistant arrives without a dedupKey
    // (backend didn't propagate message_id), replace the last streaming placeholder so the
    // streamed content is not duplicated by the final assembled message.
    if (!dedupKey && message.type === 'assistant' && !message.streaming) {
      const streamingIdx = messages.findLastIndex(m => m.streaming && m.type === 'assistant')
      if (streamingIdx !== -1) {
        const existing = messages[streamingIdx]
        messages[streamingIdx] = {
          ...existing,
          ...message,
          content: message.content || existing.content,
          thinking: message.thinking || existing.thinking,
          streaming: false,
        }
        _deltaBuffers.delete(sessionId)
        messagesBySession.value = new Map(messagesBySession.value)
        return
      }
    }

    // Issue #894: api_retry in-place update — find existing message with same retry_message_id
    if (message.metadata?.subtype === 'api_retry' && message.metadata?.retry_message_id) {
      const retryId = message.metadata.retry_message_id
      const existingIdx = messages.findIndex(m => m.metadata?.retry_message_id === retryId)
      if (existingIdx !== -1) {
        messages[existingIdx] = { ...messages[existingIdx], ...message }
        messagesBySession.value = new Map(messagesBySession.value)
        return
      }
      // No existing message: fall through to normal push (first in sequence)
    }

    messages.push(message)

    // Track last received timestamp for reconnection sync
    if (message.timestamp) {
      lastReceivedTimestamp.value.set(sessionId, message.timestamp)
    }

    // Issue #662: Track stop_reason from result messages for truncation banner
    if (message.type === 'result') {
      const stopReason = message.metadata?.stop_reason || message.stop_reason || null
      lastStopReasonBySession.value.set(sessionId, stopReason)
      lastStopReasonBySession.value = new Map(lastStopReasonBySession.value)

      // Issue #1300: Track deferred_tool_use for deferral banner
      const dtu = message.metadata?.deferred_tool_use || null
      deferredToolUseBySession.value.set(sessionId, dtu)
      deferredToolUseBySession.value = new Map(deferredToolUseBySession.value)
    }

    // Issue #1300: Clear deferred tool use when user sends a new message
    if (message.type === 'user' && !message.metadata?.has_tool_results) {
      deferredToolUseBySession.value.set(sessionId, null)
      deferredToolUseBySession.value = new Map(deferredToolUseBySession.value)
    }

    // Issue #662: Clear stop_reason when user sends a new message
    if (message.type === 'user' && !message.metadata?.has_tool_results) {
      lastStopReasonBySession.value.set(sessionId, null)
      lastStopReasonBySession.value = new Map(lastStopReasonBySession.value)
    }

    // Issue #689: Intercept task lifecycle messages for SubagentTimeline activity display
    if (message.type === 'system') {
      const subtype = message.metadata?.subtype
      const toolUseId = message.metadata?.tool_use_id
      if (toolUseId && ['task_started', 'task_progress', 'task_notification'].includes(subtype)) {
        taskActivityByToolUseId.value.set(toolUseId, {
          subtype,
          description: message.metadata?.description || message.content,
          last_tool_name: message.metadata?.last_tool_name || null,
          status: message.metadata?.status || null,
          summary: message.metadata?.summary || null,
          timestamp: message.timestamp
        })
        taskActivityByToolUseId.value = new Map(taskActivityByToolUseId.value)
      }
    }

    // Issue #310: Apply backend display metadata if present
    // This updates tool states from the backend projection, reducing frontend computation
    applyDisplayMetadata(sessionId, message)

    // Track tool use lifecycle for orphaned detection (fallback if no backend metadata)
    handleRealtimeToolTracking(sessionId, message)

    // Trigger reactivity
    messagesBySession.value = new Map(messagesBySession.value)
  }

  /**
   * Add a tool call to a session
   */
  function addToolCall(sessionId, toolCall) {
    if (!toolCallsBySession.value.has(sessionId)) {
      toolCallsBySession.value.set(sessionId, [])
    }

    const toolCalls = toolCallsBySession.value.get(sessionId)
    toolCalls.push(toolCall)

    // Trigger reactivity
    toolCallsBySession.value = new Map(toolCallsBySession.value)
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
   * Handle permission request for a tool
   */
  function handlePermissionRequest(sessionId, permissionRequest) {
    console.log('handlePermissionRequest received:', permissionRequest)

    // Extract fields from metadata or top level
    const toolName = permissionRequest.metadata?.tool_name || permissionRequest.tool_name
    const inputParams = permissionRequest.metadata?.input_params || permissionRequest.input_params
    const requestId = permissionRequest.metadata?.request_id || permissionRequest.request_id
    const suggestions = permissionRequest.metadata?.suggestions || permissionRequest.suggestions || []

    console.log('Extracted data:', { toolName, inputParams, requestId, suggestions })

    // Issue #953: Prefer direct tool_use_id from backend (SDK v0.1.52+), fall back to signature
    let toolUseId = permissionRequest.metadata?.tool_use_id || permissionRequest.tool_use_id
    if (!toolUseId) {
      // Fallback: signature-based lookup (deprecated, kept for backward compatibility)
      const signature = createToolSignature(toolName, inputParams)
      toolUseId = toolSignatureToId.value.get(sessionId)?.get(signature)
    }

    if (toolUseId) {
      permissionToToolMap.value.set(requestId, toolUseId)

      updateToolCall(sessionId, toolUseId, {
        status: 'permission_required',
        permissionRequestId: requestId,
        suggestions: suggestions
      })

      console.log(`Permission required for tool ${toolUseId}`, { suggestions })
    } else {
      console.warn('No tool use ID found for permission request', {
        toolName,
        inputParams,
        tool_use_id: permissionRequest.metadata?.tool_use_id
      })
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
        // Issue #412: Also update backendStatus so effectiveStatus reflects the change immediately
        updates.backendStatus = 'running'
      } else {
        updates.status = 'completed'
        // Issue #412: Also update backendStatus so effectiveStatus reflects the change immediately
        updates.backendStatus = 'denied'
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
      console.log(`Permission ${permissionResponse.decision} for tool ${toolUseId}`)
    }
  }

  /**
   * Issue #324: Handle unified tool_call message from backend
   *
   * The backend now sends a single 'tool_call' message type that contains
   * the complete tool lifecycle state. This replaces the need to correlate
   * separate tool_use, permission_request, permission_response, and tool_result
   * messages.
   *
   * The toolCall object contains:
   * - tool_use_id: Unique identifier for this tool execution
   * - name: Tool name (Edit, Bash, Read, etc.)
   * - input: Tool input parameters
   * - status: Current lifecycle state (pending, awaiting_permission, running, completed, failed, denied, interrupted)
   * - permission: Embedded permission info (if applicable)
   * - permission_granted: Whether permission was granted
   * - result: Tool execution result (when completed/failed)
   * - error: Error message (when failed)
   * - display: Backend-computed display hints
   */
  function handleToolCall(sessionId, toolCall, options = {}) {
    console.log('handleToolCall received:', toolCall)

    const toolUseId = toolCall.tool_use_id
    if (!toolUseId) {
      console.warn('Received tool_call without tool_use_id:', toolCall)
      return
    }

    // Get or create tool calls array for this session
    if (!toolCallsBySession.value.has(sessionId)) {
      toolCallsBySession.value.set(sessionId, [])
    }

    const toolCalls = toolCallsBySession.value.get(sessionId)
    const existingIndex = toolCalls.findIndex(tc => tc.id === toolUseId)

    // Map backend status to frontend status
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

      existing.status = frontendStatus
      existing.backendStatus = toolCall.status

      // Issue #1486: always accept the incoming input — the first event for a tool may arrive
      // with input:{} (from an intermediate AssistantMessage emitted while input_json_delta
      // events are still streaming); a subsequent event carries the fully-assembled input.
      if (toolCall.input !== undefined && toolCall.input !== null) {
        existing.input = toolCall.input
      }

      // Update permission fields
      if (toolCall.permission) {
        existing.suggestions = toolCall.permission.suggestions || []
        existing.permissionRequestId = toolCall.request_id  // request_id is added by backend for correlation
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

      // Handle special statuses
      if (toolCall.status === 'denied') {
        existing.result = {
          error: true,
          message: 'Permission denied'
        }
        existing.isExpanded = false
      }
      if (toolCall.status === 'interrupted') {
        // Mark as orphaned
        markToolUseOrphaned(sessionId, toolUseId, 'Session was interrupted')
        existing.isExpanded = false
      }
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

      console.log(`Updated tool call ${toolUseId} to status: ${frontendStatus} (backend: ${toolCall.status})`)
    } else {
      // Create new tool call entry
      const signature = createToolSignature(toolCall.name, toolCall.input)
      if (!toolSignatureToId.value.has(sessionId)) {
        toolSignatureToId.value.set(sessionId, new Map())
      }
      toolSignatureToId.value.get(sessionId).set(signature, toolUseId)

      const newToolCall = {
        id: toolUseId,
        name: toolCall.name,
        input: toolCall.input,
        signature: signature,
        status: frontendStatus,
        backendStatus: toolCall.status,
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
      }

      if (toolCall.error) {
        newToolCall.result = {
          error: true,
          content: toolCall.error
        }
      }

      toolCalls.push(newToolCall)
      // Populate permissionToToolMap for handlePermissionResponse correlation
      if (toolCall.request_id && toolCall.status === 'awaiting_permission') {
        permissionToToolMap.value.set(toolCall.request_id, toolUseId)
        // Issue #699: Permission prompt notifications now driven by UI WebSocket
        // state_change → paused (see polling.js handleUIMessage)
      }
      console.log(`Created new tool call ${toolUseId} for ${toolCall.name} with status: ${frontendStatus}`)
    }

    // Trigger reactivity
    toolCallsBySession.value = new Map(toolCallsBySession.value)

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
   * Create tool signature for matching (tool name + params hash)
   * @deprecated Issue #953: Use tool_use_id from backend (SDK v0.1.52+) instead.
   * Retained as fallback for older SDK versions.
   */
  function createToolSignature(toolName, inputParams) {
    if (!toolName) return 'unknown:{}'
    if (!inputParams || typeof inputParams !== 'object') return `${toolName}:{}`

    const paramsHash = JSON.stringify(inputParams, Object.keys(inputParams).sort())
    return `${toolName}:${paramsHash}`
  }

  /**
   * Issue #310: Extract and apply display metadata from message
   *
   * When the backend provides display metadata, we cache it and use it
   * to update tool call states, replacing local state computation.
   */
  function applyDisplayMetadata(sessionId, message) {
    const display = message.metadata?.display || message.display
    if (!display) return

    // Get or create backend tool states cache for this session
    if (!backendToolStates.value.has(sessionId)) {
      backendToolStates.value.set(sessionId, new Map())
    }
    const toolStatesCache = backendToolStates.value.get(sessionId)

    // Update cached tool states from backend
    if (display.tool_states) {
      for (const [toolId, info] of Object.entries(display.tool_states)) {
        toolStatesCache.set(toolId, info)

        // Also update the tool call if it exists
        const toolCalls = toolCallsBySession.value.get(sessionId)
        if (toolCalls) {
          const toolCall = toolCalls.find(tc => tc.id === toolId)
          if (toolCall) {
            // Map backend state to frontend status
            const stateToStatus = {
              'pending': 'pending',
              'permission_required': 'permission_required',
              'executing': 'executing',
              'completed': 'completed',
              'failed': 'error',
              'orphaned': 'completed'  // Orphaned shows as completed with special styling
            }
            toolCall.status = stateToStatus[info.state] || info.state
            toolCall.backendState = info  // Store full backend state for reference
          }
        }
      }
    }

    // Update orphaned tools from backend
    if (display.orphaned_tools && display.orphaned_tools.length > 0) {
      const orphaned = orphanedToolUses.value.get(sessionId) || new Map()
      for (const toolId of display.orphaned_tools) {
        if (!orphaned.has(toolId)) {
          orphaned.set(toolId, {
            reason: 'denied',
            message: 'Session was interrupted'  // Default message
          })
        }
      }
      orphanedToolUses.value.set(sessionId, orphaned)
    }

    // Update permission-to-tool mapping from backend
    if (display.linked_permissions) {
      for (const [requestId, toolId] of Object.entries(display.linked_permissions)) {
        permissionToToolMap.value.set(requestId, toolId)
      }
    }

    // Trigger reactivity if we updated tool calls
    if (display.tool_states && Object.keys(display.tool_states).length > 0) {
      toolCallsBySession.value = new Map(toolCallsBySession.value)
    }

    console.log(`Applied backend display metadata for session ${sessionId}:`, {
      toolStates: Object.keys(display.tool_states || {}).length,
      orphanedTools: (display.orphaned_tools || []).length,
      linkedPermissions: Object.keys(display.linked_permissions || {}).length
    })
  }

  /**
   * Issue #310: Get backend-provided tool state if available
   */
  function getBackendToolState(sessionId, toolUseId) {
    return backendToolStates.value.get(sessionId)?.get(toolUseId)
  }

  /**
   * Issue #310: Check if we have backend display metadata for a session
   */
  function hasBackendDisplayMetadata(sessionId) {
    const cache = backendToolStates.value.get(sessionId)
    return cache && cache.size > 0
  }

  /**
   * Mark a tool use as orphaned (denied due to session restart/interrupt/termination)
   */
  function markToolUseOrphaned(sessionId, toolUseId, message) {
    const info = { reason: 'denied', message: message }

    const orphaned = orphanedToolUses.value.get(sessionId) || new Map()
    orphaned.set(toolUseId, info)
    orphanedToolUses.value.set(sessionId, orphaned)

    // Stamp directly on tool call object for reliable reactivity
    const toolCalls = toolCallsBySession.value.get(sessionId)
    if (toolCalls) {
      const toolCall = toolCalls.find(tc => tc.id === toolUseId)
      if (toolCall) {
        toolCall._isOrphaned = true
        toolCall._orphanedInfo = info
        toolCall.isExpanded = false
      }
    }
    // Trigger reactivity
    toolCallsBySession.value = new Map(toolCallsBySession.value)

    console.log(`Marked tool use ${toolUseId} as orphaned: ${message}`)
  }

  /**
   * Check if a tool use is orphaned
   *
   * Issue #310: First checks backend-provided state, then falls back to local tracking
   */
  function isToolUseOrphaned(sessionId, toolUseId) {
    // Check backend state first (Issue #310)
    const backendState = getBackendToolState(sessionId, toolUseId)
    if (backendState && backendState.state === 'orphaned') {
      return true
    }

    // Fall back to local tracking
    return orphanedToolUses.value.get(sessionId)?.has(toolUseId) || false
  }

  /**
   * Get orphaned tool information
   *
   * Issue #310: Returns backend-provided info if available
   */
  function getOrphanedInfo(sessionId, toolUseId) {
    // Check backend state first (Issue #310)
    const backendState = getBackendToolState(sessionId, toolUseId)
    if (backendState && backendState.state === 'orphaned') {
      return {
        reason: 'denied',
        message: 'Session was interrupted',
        backendState: backendState
      }
    }

    // Fall back to local tracking
    return orphanedToolUses.value.get(sessionId)?.get(toolUseId)
  }

  /**
   * Clear orphaned tool uses for a session (mark all open tools as orphaned)
   */
  function clearOrphanedToolUses(sessionId, message) {
    const openTools = activeToolUses.value.get(sessionId)
    if (openTools && openTools.size > 0) {
      openTools.forEach(id => markToolUseOrphaned(sessionId, id, message))
      openTools.clear()
    }
  }

  /**
   * Clear messages for a session (for reset)
   */
  function clearMessages(sessionId) {
    _deltaBuffers.delete(sessionId)  // Issue #1486: discard any in-flight streaming buffer
    messagesBySession.value.delete(sessionId)
    toolCallsBySession.value.delete(sessionId)
    toolSignatureToId.value.delete(sessionId)
    lastReceivedTimestamp.value.delete(sessionId)

    // Issue #689: Clear task activity (ephemeral, reconstructs from history)
    taskActivityByToolUseId.value = new Map()

    // Trigger reactivity
    messagesBySession.value = new Map(messagesBySession.value)
    toolCallsBySession.value = new Map(toolCallsBySession.value)
  }

  /**
   * Get last received message timestamp for a session
   */
  function getLastReceivedTimestamp(sessionId) {
    return lastReceivedTimestamp.value.get(sessionId)
  }

  /**
   * Sync missed messages since last received (for reconnection recovery)
   * Returns: {syncedCount, hasMore, error}
   */
  async function syncMessages(sessionId) {
    const lastTimestamp = lastReceivedTimestamp.value.get(sessionId)

    // No sync needed if no previous messages
    if (!lastTimestamp) {
      console.log(`No sync needed for session ${sessionId} (no previous messages)`)
      return { syncedCount: 0, hasMore: false }
    }

    try {
      console.log(`Syncing messages for session ${sessionId} since ${lastTimestamp}`)

      // Fetch all messages and filter client-side by timestamp
      // Backend doesn't have 'since' parameter yet, so we load recent messages and filter
      const data = await api.get(
        `/api/sessions/${sessionId}/messages?limit=1000&offset=0`
      )

      const allMessages = data.messages || []
      const hasMore = data.has_more || false

      // Filter messages newer than last received timestamp
      // Handle both Unix timestamp (float seconds) and ISO 8601 string formats
      const normalizeTimestamp = (ts) => {
        if (!ts) return 0
        if (typeof ts === 'number') {
          // Unix timestamp in seconds - convert to milliseconds
          return ts * 1000
        }
        // ISO 8601 string
        return new Date(ts).getTime()
      }

      const lastTimestampMs = normalizeTimestamp(lastTimestamp)
      const newMessages = allMessages.filter(m => {
        if (!m.timestamp) return false
        const msgTimestampMs = normalizeTimestamp(m.timestamp)
        return msgTimestampMs > lastTimestampMs
      })

      if (newMessages.length === 0) {
        console.log(`No missed messages for session ${sessionId}`)
        return { syncedCount: 0, hasMore: false }
      }

      console.log(`Synced ${newMessages.length} missed messages for session ${sessionId}`)

      // Get existing messages
      const existingMessages = messagesBySession.value.get(sessionId) || []

      // Deduplicate by message ID (in case of overlap)
      const existingIds = new Set(existingMessages.map(m => m.id).filter(Boolean))
      const uniqueNewMessages = newMessages.filter(m => !m.id || !existingIds.has(m.id))

      console.log(`After deduplication: ${uniqueNewMessages.length} unique new messages`)

      // Issue #491: Separate tool_call messages from regular messages.
      // Route tool_call through handleToolCall(), merge regular messages into history.
      const newToolCallMessages = []
      const newRegularMessages = []

      uniqueNewMessages.forEach(message => {
        if (message.type === 'tool_call') {
          newToolCallMessages.push(message)
          return
        }

        // Issue #1575: drop internal SDK status/requesting messages — match real-time suppression
        if (message.type === 'system') {
          const subtype = message.subtype || message.metadata?.subtype
          const status = message.metadata?.init_data?.status
          if (subtype === 'status' && status === 'requesting') {
            return
          }
        }

        newRegularMessages.push(message)
      })

      // Route tool_call messages through unified handler (silent: skip notifications for synced history)
      newToolCallMessages.forEach(message => {
        handleToolCall(sessionId, message, { silent: true })
      })

      // Only merge and sort regular messages if there are any
      if (newRegularMessages.length > 0) {
        // Sort only the new messages by timestamp (using normalized timestamps)
        const sortedNewMessages = newRegularMessages.sort((a, b) => {
          const timeA = normalizeTimestamp(a.timestamp)
          const timeB = normalizeTimestamp(b.timestamp)
          return timeA - timeB
        })

        // CRITICAL: Get the ACTUAL last message from existingMessages (which may have been
        // updated by real-time WebSocket messages during disconnect), not the cached timestamp
        const currentMessages = messagesBySession.value.get(sessionId) || []
        const lastExistingTimestamp = currentMessages.length > 0
          ? normalizeTimestamp(currentMessages[currentMessages.length - 1].timestamp)
          : 0
        const firstNewTimestamp = normalizeTimestamp(sortedNewMessages[0].timestamp)

        if (firstNewTimestamp > lastExistingTimestamp) {
          // Simple append - new messages are all newer than existing ones
          const mergedMessages = [...currentMessages, ...sortedNewMessages]
          messagesBySession.value.set(sessionId, mergedMessages)
        } else {
          // Need to merge and sort - some new messages are older than existing ones
          const mergedMessages = [...currentMessages, ...sortedNewMessages].sort((a, b) => {
            const timeA = normalizeTimestamp(a.timestamp)
            const timeB = normalizeTimestamp(b.timestamp)
            return timeA - timeB
          })
          messagesBySession.value.set(sessionId, mergedMessages)
        }
      }

      // Process new regular messages for non-tool concerns
      newRegularMessages.forEach(message => {
        // Track tool use lifecycle for real-time orphan detection
        handleRealtimeToolTracking(sessionId, message)

        // Capture init data
        if (message.type === 'system' &&
            (message.subtype === 'init' || message.metadata?.subtype === 'init') &&
            message.metadata?.init_data) {
          const sessionStore = useSessionStore()
          sessionStore.storeInitData(sessionId, message.metadata.init_data)
        }
      })

      // Update last received timestamp from the updated message list
      const updatedMessages = messagesBySession.value.get(sessionId)
      if (updatedMessages && updatedMessages.length > 0) {
        const lastMessage = updatedMessages[updatedMessages.length - 1]
        if (lastMessage.timestamp) {
          lastReceivedTimestamp.value.set(sessionId, lastMessage.timestamp)
        }
      }

      // Trigger reactivity
      messagesBySession.value = new Map(messagesBySession.value)

      return { syncedCount: uniqueNewMessages.length, hasMore }
    } catch (error) {
      console.error(`Failed to sync messages for session ${sessionId}:`, error)
      // Don't throw - reconnection should succeed even if sync fails
      return { syncedCount: 0, hasMore: false, error: error.message }
    }
  }

  // ========== STREAMING (Issue #1486) ==========

  function _createStreamingPlaceholder(sessionId, messageId) {
    if (!messageId) return
    if (!messagesBySession.value.has(sessionId)) {
      messagesBySession.value.set(sessionId, [])
    }
    const messages = messagesBySession.value.get(sessionId)
    if (messages.find(m => m.message_id === messageId)) return  // guard: no duplicate placeholder

    messages.push({
      id: messageId,
      message_id: messageId,
      type: 'assistant',
      content: '',
      thinking: '',
      streaming: true,
      timestamp: Date.now() / 1000,  // Unix seconds, same convention as backend messages
    })

    _deltaBuffers.set(sessionId, {
      messageId,
      pendingText: '',
      pendingThinking: '',
      blockTypeByIndex: {},
      rafHandle: null,
    })

    messagesBySession.value = new Map(messagesBySession.value)
  }

  function _flushDeltaBuffer(sessionId) {
    const buf = _deltaBuffers.get(sessionId)
    if (!buf) return

    const messages = messagesBySession.value.get(sessionId)
    if (!messages) return

    const idx = messages.findIndex(m => m.message_id === buf.messageId)
    if (idx < 0) { buf.rafHandle = null; return }
    if (buf.pendingText === '' && buf.pendingThinking === '') { buf.rafHandle = null; return }

    messages[idx] = {
      ...messages[idx],
      content: messages[idx].content + buf.pendingText,
      thinking: messages[idx].thinking + buf.pendingThinking,
    }
    buf.pendingText = ''
    buf.pendingThinking = ''
    buf.rafHandle = null

    messagesBySession.value = new Map(messagesBySession.value)
  }

  function _stopStreamingPlaceholder(sessionId) {
    const buf = _deltaBuffers.get(sessionId)
    if (!buf) return
    if (buf.rafHandle) { cancelAnimationFrame(buf.rafHandle); buf.rafHandle = null }
    _flushDeltaBuffer(sessionId)
    // Clear streaming flag so the caret disappears
    const messages = messagesBySession.value.get(sessionId)
    if (messages) {
      const idx = messages.findIndex(m => m.message_id === buf.messageId)
      if (idx >= 0) {
        messages[idx] = { ...messages[idx], streaming: false }
        messagesBySession.value = new Map(messagesBySession.value)
      }
    }
    _deltaBuffers.delete(sessionId)
  }

  function handleAssistantDelta(sessionId, data) {
    // data = { uuid, event } where event is the raw Anthropic streaming event dict
    const eventType = data?.event?.type
    if (!eventType) return

    switch (eventType) {
      case 'message_start':
        // Use Anthropic message ID (stable across all streaming events for this message)
        // data.uuid is a per-event CLI envelope UUID and must NOT be used as message identity
        _createStreamingPlaceholder(sessionId, data.event?.message?.id)
        break

      case 'content_block_start': {
        const buf = _deltaBuffers.get(sessionId)
        if (buf) buf.blockTypeByIndex[data.event.index] = data.event.content_block?.type
        break
      }

      case 'content_block_delta': {
        const buf = _deltaBuffers.get(sessionId)
        if (!buf) break
        const deltaType = data.event.delta?.type
        if (deltaType === 'text_delta') {
          buf.pendingText += data.event.delta.text || ''
          if (!buf.rafHandle) buf.rafHandle = requestAnimationFrame(() => _flushDeltaBuffer(sessionId))
        } else if (deltaType === 'thinking_delta') {
          buf.pendingThinking += data.event.delta.thinking || ''
          if (!buf.rafHandle) buf.rafHandle = requestAnimationFrame(() => _flushDeltaBuffer(sessionId))
        }
        // input_json_delta: out of scope per §2, ignore
        break
      }

      case 'message_stop': {
        const buf = _deltaBuffers.get(sessionId)
        if (buf) {
          if (buf.rafHandle) { cancelAnimationFrame(buf.rafHandle); buf.rafHandle = null }
          _flushDeltaBuffer(sessionId)
          // Issue #1601: replace placeholder with collected terminal AMs via splice so each
          // AM becomes its own bubble, matching the page-load (JSONL) bubble layout exactly.
          const messages = messagesBySession.value.get(sessionId)
          if (messages) {
            const idx = messages.findIndex(m => m.message_id === buf.messageId)
            if (idx >= 0 && messages[idx].streaming) {
              if (buf.collectedTerminalMessages?.length > 0) {
                messages.splice(idx, 1, ...buf.collectedTerminalMessages.map(m => ({ ...m, streaming: false })))
                // Issue #1614: apply side effects skipped by the generalized defer in addMessage.
                buf.collectedTerminalMessages.forEach(m => {
                  if (m.timestamp) lastReceivedTimestamp.value.set(sessionId, m.timestamp)
                  applyDisplayMetadata(sessionId, m)
                  handleRealtimeToolTracking(sessionId, m)
                })
              } else {
                // No terminal AM collected — just stop the streaming caret
                messages[idx] = { ...messages[idx], streaming: false }
              }
              messagesBySession.value = new Map(messagesBySession.value)
            }
          }
          _deltaBuffers.delete(sessionId)
        }
        break
      }
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
          clearOrphanedToolUses(newState.id, 'Session was terminated')
          // Issue #1486: stop any in-flight streaming placeholder so the caret doesn't linger
          _stopStreamingPlaceholder(newState.id)
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
    const displayMessages = []

    rawMessages.forEach(msg => {
      // Route tool_call messages through unified handler (silent: archive data)
      if (msg.type === 'tool_call') {
        handleToolCall(sessionId, msg, { silent: true })
        return // Don't add to display list
      }

      // Filter UserMessage entries that are purely tool results (no displayable text)
      if (msg.type === 'user' && Array.isArray(msg.content)) {
        const hasDisplayableContent = msg.content.some(
          block => block.type !== 'tool_result'
        )
        if (!hasDisplayableContent) {
          return
        }
      }

      // Filter SystemMessage entries with no displayable content
      if (msg.type === 'system') {
        const subtype = msg.subtype || msg.metadata?.subtype
        if (subtype === 'init' && !msg.content) {
          return
        }
        // Issue #1486: internal SDK state transitions are not displayable
        if (subtype === 'status' && msg.metadata?.init_data?.status === 'requesting') {
          return
        }
      }

      displayMessages.push(msg)
    })

    messagesBySession.value.set(sessionId, displayMessages)
    // Trigger reactivity
    messagesBySession.value = new Map(messagesBySession.value)
  }

  /**
   * Clear archive messages when leaving archive view.
   */
  function clearArchiveMessages(sessionId) {
    messagesBySession.value.delete(sessionId)
    toolCallsBySession.value.delete(sessionId)
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

  // ========== RETURN ==========
  return {
    // State
    messagesBySession,
    toolCallsBySession,
    activeToolUses: readonly(activeToolUses),
    orphanedToolUses: readonly(orphanedToolUses),

    // Computed
    currentMessages,
    currentToolCalls,

    // Actions
    loadMessages,
    addMessage,
    addToolCall,
    updateToolCall,
    handleToolCall,  // Issue #324/#491: unified tool call handler (single path for real-time and history)
    handlePermissionRequest,
    handlePermissionResponse,
    toggleToolExpansion,
    clearMessages,
    // Issue #1486: streaming delta handler
    handleAssistantDelta,

    // Orphaned tool tracking
    markToolUseOrphaned,
    isToolUseOrphaned,
    getOrphanedInfo,
    clearOrphanedToolUses,

    // Reconnection sync
    getLastReceivedTimestamp,
    syncMessages,

    // Issue #310: Backend display metadata
    getBackendToolState,
    hasBackendDisplayMetadata,
    backendToolStates: readonly(backendToolStates),

    // Launch timestamp tracking (Issue #473)
    launchTimestampBySession: readonly(launchTimestampBySession),

    // Archive support (Issue #577)
    setArchiveMessages,
    clearArchiveMessages,

    // Issue #662: Stop reason tracking for truncation banner
    lastStopReasonBySession: readonly(lastStopReasonBySession),

    // Issue #1300: Deferred tool use tracking for deferral banner
    deferredToolUseBySession: readonly(deferredToolUseBySession),

    // Issue #689: Task activity tracking for SubagentTimeline
    taskActivityByToolUseId: readonly(taskActivityByToolUseId),
    getTaskActivity,

    // Issue #1000: Event cursors from REST /messages, consumed by connectSession()
    loadedEventCursors,

    // Issue #1350: Hook correlation helpers
    hooksForToolCall,
    hooksForMessageId,
    hooksForCompaction,
    isHookMessageAttached,
  }
})
