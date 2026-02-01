import { defineStore } from 'pinia'
import { ref, computed, readonly, watch } from 'vue'
import { api } from '../utils/api'
import { useSessionStore } from './session'
import { useTaskStore } from './task'

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
  const toolSignatureToId = ref(new Map())
  const permissionToToolMap = ref(new Map())

  // Orphaned tool tracking (sessionId -> Set<tool_use_id>)
  const activeToolUses = ref(new Map())
  const orphanedToolUses = ref(new Map()) // sessionId -> Map<tool_use_id -> {reason, message}>

  // Message sequence tracking for reconnection sync (sessionId -> ISO timestamp)
  const lastReceivedTimestamp = ref(new Map())

  // Issue #310: Backend display metadata cache (sessionId -> Map<tool_use_id -> ToolDisplayInfo>)
  // When backend provides display metadata, we store it here for quick lookup
  const backendToolStates = ref(new Map())

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

      console.log(`Loaded ${messages.length} of ${totalCount} messages for session ${sessionId}`)

      // Store messages
      messagesBySession.value.set(sessionId, messages)

      // Track last received timestamp from loaded messages
      if (messages.length > 0) {
        const lastMessage = messages[messages.length - 1]
        if (lastMessage.timestamp) {
          lastReceivedTimestamp.value.set(sessionId, lastMessage.timestamp)
          console.log(`Tracked last received timestamp for session ${sessionId}: ${lastMessage.timestamp}`)
        }
      }

      // Track open tool uses for orphaned tool detection
      const openTools = new Set()

      // Process messages to extract tool uses, results, permission requests, and init data
      messages.forEach(message => {
        // Track tool uses (for orphaned detection)
        if (message.type === 'assistant' && message.metadata?.tool_uses) {
          message.metadata.tool_uses.forEach(toolUse => {
            openTools.add(toolUse.id)
          })
        }

        // Remove tool uses on results (for orphaned detection)
        if (message.type === 'user' && message.metadata?.tool_results) {
          message.metadata.tool_results.forEach(result => {
            openTools.delete(result.tool_use_id)
            // Also clear from orphaned if it was previously marked
            const orphaned = orphanedToolUses.value.get(sessionId)
            if (orphaned) {
              orphaned.delete(result.tool_use_id)
            }
          })
        }

        // Detect session restart - mark open tools as orphaned
        if (message.type === 'system' && message.metadata?.subtype === 'client_launched') {
          openTools.forEach(id => {
            markToolUseOrphaned(sessionId, id, 'Session was restarted')
          })
          openTools.clear()
        }

        // Detect session interrupt - mark open tools as orphaned
        if (message.type === 'system' && message.metadata?.subtype === 'interrupt') {
          openTools.forEach(id => {
            markToolUseOrphaned(sessionId, id, 'Session was interrupted')
          })
          openTools.clear()
        }
        // Extract tool uses from assistant messages
        if (message.metadata?.has_tool_uses && message.metadata.tool_uses) {
          message.metadata.tool_uses.forEach(toolUse => {
            handleToolUse(sessionId, toolUse, message.timestamp)
          })
        }

        // Extract tool results from user messages
        if (message.metadata?.has_tool_results && message.metadata.tool_results) {
          message.metadata.tool_results.forEach(toolResult => {
            handleToolResult(sessionId, toolResult)
          })
        }

        // Extract permission requests (for restoring permission prompts on page refresh)
        if (message.type === 'permission_request' || message.metadata?.has_permission_requests) {
          handlePermissionRequest(sessionId, message)
        }

        // Extract permission responses (for restoring AskUserQuestion answers on page refresh)
        if (message.type === 'permission_response') {
          handlePermissionResponse(sessionId, {
            request_id: message.metadata?.request_id,
            decision: message.metadata?.decision,
            updated_input: message.metadata?.updated_input,
            reasoning: message.metadata?.reasoning,
            applied_updates: message.metadata?.applied_updates
          })
        }

        // Capture init data for session info modal
        if (message.type === 'system' &&
            (message.subtype === 'init' || message.metadata?.subtype === 'init') &&
            message.metadata?.init_data) {
          const sessionStore = useSessionStore()
          sessionStore.storeInitData(sessionId, message.metadata.init_data)
        }
      })

      // Check session state for any remaining open tools
      const sessionStore = useSessionStore()
      const session = sessionStore.sessions.get(sessionId)
      if (session && !['active', 'paused', 'starting'].includes(session.state)) {
        openTools.forEach(id => {
          markToolUseOrphaned(sessionId, id, 'Session was terminated')
        })
        openTools.clear()
      }

      // Store active tools for real-time tracking
      activeToolUses.value.set(sessionId, openTools)

      // Trigger reactivity
      messagesBySession.value = new Map(messagesBySession.value)

      // Reconstruct task state from message history
      try {
        const taskStore = useTaskStore()
        taskStore.reconstructFromMessages(sessionId, messages)
      } catch (e) {
        console.warn('Failed to reconstruct task state:', e)
      }

      // Warn if there are more messages we didn't load
      if (hasMore) {
        console.warn(`Warning: Only loaded ${messages.length} of ${totalCount} messages. Some messages may be missing.`)
      }

      return { messages, totalCount, hasMore }
    } catch (error) {
      console.error('Failed to load messages:', error)
      throw error
    }
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
    }

    // Detect interrupt during real-time
    if (message.type === 'system' && message.metadata?.subtype === 'interrupt') {
      openTools.forEach(id => {
        markToolUseOrphaned(sessionId, id, 'Session was interrupted')
      })
      openTools.clear()
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

    const messages = messagesBySession.value.get(sessionId)

    // Deduplicate: Check if message with this ID already exists
    if (message.id) {
      const existingIndex = messages.findIndex(m => m.id === message.id)
      if (existingIndex !== -1) {
        console.log(`Skipping duplicate message ${message.id} (already exists at index ${existingIndex})`)
        return // Skip duplicate message
      }
    }

    messages.push(message)

    // Track last received timestamp for reconnection sync
    if (message.timestamp) {
      lastReceivedTimestamp.value.set(sessionId, message.timestamp)
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
   * Handle tool use message (create tool call)
   * @param {string} sessionId - Session ID
   * @param {Object} toolUseBlock - Tool use block from SDK
   * @param {string|null} messageTimestamp - Backend message timestamp (for chronological ordering)
   */
  function handleToolUse(sessionId, toolUseBlock, messageTimestamp = null) {
    const signature = createToolSignature(toolUseBlock.name, toolUseBlock.input)
    toolSignatureToId.value.set(signature, toolUseBlock.id)

    const toolCall = {
      id: toolUseBlock.id,
      name: toolUseBlock.name,
      input: toolUseBlock.input,
      signature: signature,
      status: 'pending',
      permissionRequestId: null,
      permissionDecision: null,
      result: null,
      explanation: null,
      timestamp: messageTimestamp || new Date().toISOString(),
      isExpanded: true
    }

    addToolCall(sessionId, toolCall)
    console.log(`Created tool call ${toolCall.id} for ${toolCall.name}`)
    return toolCall
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

    const signature = createToolSignature(toolName, inputParams)
    const toolUseId = toolSignatureToId.value.get(signature)

    if (toolUseId) {
      permissionToToolMap.value.set(requestId, toolUseId)

      updateToolCall(sessionId, toolUseId, {
        status: 'permission_required',
        permissionRequestId: requestId,
        suggestions: suggestions
      })

      console.log(`Permission required for tool ${toolUseId}`, { suggestions })
    } else {
      console.warn('No tool use ID found for permission request signature:', signature, {
        toolName,
        inputParams,
        availableSignatures: Array.from(toolSignatureToId.value.keys())
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
  function handleToolCall(sessionId, toolCall) {
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
      existing.status = frontendStatus
      existing.backendStatus = toolCall.status

      // Update permission fields
      if (toolCall.permission) {
        existing.suggestions = toolCall.permission.suggestions || []
        existing.permissionRequestId = toolCall.request_id  // request_id is added by backend for correlation
      }
      if (toolCall.permission_granted !== null && toolCall.permission_granted !== undefined) {
        existing.permissionDecision = toolCall.permission_granted ? 'allow' : 'deny'
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

      console.log(`Updated tool call ${toolUseId} to status: ${frontendStatus} (backend: ${toolCall.status})`)
    } else {
      // Create new tool call entry
      const signature = createToolSignature(toolCall.name, toolCall.input)
      toolSignatureToId.value.set(signature, toolUseId)

      const newToolCall = {
        id: toolUseId,
        name: toolCall.name,
        input: toolCall.input,
        signature: signature,
        status: frontendStatus,
        backendStatus: toolCall.status,
        permissionRequestId: toolCall.request_id,
        permissionDecision: toolCall.permission_granted !== undefined
          ? (toolCall.permission_granted ? 'allow' : 'deny')
          : null,
        suggestions: toolCall.permission?.suggestions || [],
        result: toolCall.result ? {
          error: toolCall.status === 'failed',
          content: toolCall.result
        } : null,
        explanation: null,
        timestamp: toolCall.created_at || new Date().toISOString(),
        isExpanded: !['completed', 'failed', 'denied', 'interrupted'].includes(toolCall.status),
        backendState: toolCall.display
      }

      if (toolCall.error) {
        newToolCall.result = {
          error: true,
          content: toolCall.error
        }
      }

      toolCalls.push(newToolCall)
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
   * Handle tool result (completion)
   */
  function handleToolResult(sessionId, toolResultBlock) {
    const toolUseId = toolResultBlock.tool_use_id

    updateToolCall(sessionId, toolUseId, {
      status: toolResultBlock.is_error ? 'error' : 'completed',
      result: {
        error: toolResultBlock.is_error,
        content: toolResultBlock.content
      },
      isExpanded: false // Auto-collapse on completion
    })

    console.log(`Tool ${toolUseId} ${toolResultBlock.is_error ? 'failed' : 'completed'}`)

    // Note: ExitPlanMode mode reset logic is handled by backend to prevent race conditions
    // with multiple connected frontends. Backend tracks setMode suggestions and makes the
    // decision to reset or not.

    // Handle task tool results - update task store
    const toolCalls = toolCallsBySession.value.get(sessionId)
    if (toolCalls) {
      const toolCall = toolCalls.find(tc => tc.id === toolUseId)
      if (toolCall && ['TaskCreate', 'TaskUpdate', 'TaskList', 'TaskGet'].includes(toolCall.name)) {
        try {
          const taskStore = useTaskStore()
          taskStore.handleTaskToolResult(sessionId, toolCall.name, toolCall.input, {
            error: toolResultBlock.is_error,
            content: toolResultBlock.content
          })
        } catch (e) {
          console.warn('Failed to update task store:', e)
        }
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
    const orphaned = orphanedToolUses.value.get(sessionId) || new Map()
    orphaned.set(toolUseId, {
      reason: 'denied',
      message: message
    })
    orphanedToolUses.value.set(sessionId, orphaned)

    // Collapse the tool card when marking as orphaned
    const toolCalls = toolCallsBySession.value.get(sessionId)
    if (toolCalls) {
      const toolCall = toolCalls.find(tc => tc.id === toolUseId)
      if (toolCall && toolCall.isExpanded) {
        toolCall.isExpanded = false
        // Trigger reactivity
        toolCallsBySession.value = new Map(toolCallsBySession.value)
      }
    }

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
    messagesBySession.value.delete(sessionId)
    toolCallsBySession.value.delete(sessionId)

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

      // Only merge and sort if there are new messages
      // This prevents re-sorting existing messages on every reconnection
      if (uniqueNewMessages.length > 0) {
        // Sort only the new messages by timestamp (using normalized timestamps)
        const sortedNewMessages = uniqueNewMessages.sort((a, b) => {
          const timeA = normalizeTimestamp(a.timestamp)
          const timeB = normalizeTimestamp(b.timestamp)
          return timeA - timeB
        })

        // CRITICAL: Get the ACTUAL last message from existingMessages (which may have been
        // updated by real-time WebSocket messages during disconnect), not the cached timestamp
        // Re-fetch existingMessages to ensure we have the latest state
        const currentMessages = messagesBySession.value.get(sessionId) || []
        const lastExistingTimestamp = currentMessages.length > 0
          ? normalizeTimestamp(currentMessages[currentMessages.length - 1].timestamp)
          : 0
        const firstNewTimestamp = normalizeTimestamp(sortedNewMessages[0].timestamp)
        const lastNewTimestamp = normalizeTimestamp(sortedNewMessages[sortedNewMessages.length - 1].timestamp)

        console.log(`Timestamp comparison:`, {
          existingCount: currentMessages.length,
          lastExisting: lastExistingTimestamp,
          lastExistingDate: currentMessages.length > 0 ? currentMessages[currentMessages.length - 1].timestamp : 'none',
          firstNew: firstNewTimestamp,
          firstNewDate: sortedNewMessages[0].timestamp,
          lastNew: lastNewTimestamp,
          lastNewDate: sortedNewMessages[sortedNewMessages.length - 1].timestamp,
          shouldAppend: firstNewTimestamp > lastExistingTimestamp
        })

        if (firstNewTimestamp > lastExistingTimestamp) {
          // Simple append - new messages are all newer than existing ones
          const mergedMessages = [...currentMessages, ...sortedNewMessages]
          messagesBySession.value.set(sessionId, mergedMessages)
          console.log(`✅ Appended ${sortedNewMessages.length} new messages (no full sort needed)`)
        } else {
          // Need to merge and sort - some new messages are older than existing ones
          const mergedMessages = [...currentMessages, ...sortedNewMessages].sort((a, b) => {
            const timeA = normalizeTimestamp(a.timestamp)
            const timeB = normalizeTimestamp(b.timestamp)
            return timeA - timeB
          })
          messagesBySession.value.set(sessionId, mergedMessages)
          console.log(`⚠️ Merged and sorted ${sortedNewMessages.length} new messages (interleaved with existing)`)
        }
      } else {
        // No new messages - don't touch existing array to avoid triggering re-render
        console.log(`No new messages to merge, keeping existing ${existingMessages.length} messages unchanged`)
      }

      // Process new messages for tool uses, results, etc.
      uniqueNewMessages.forEach(message => {
        // Track tool use lifecycle
        handleRealtimeToolTracking(sessionId, message)

        // Extract tool uses
        if (message.metadata?.has_tool_uses && message.metadata.tool_uses) {
          message.metadata.tool_uses.forEach(toolUse => {
            handleToolUse(sessionId, toolUse, message.timestamp)
          })
        }

        // Extract tool results
        if (message.metadata?.has_tool_results && message.metadata.tool_results) {
          message.metadata.tool_results.forEach(toolResult => {
            handleToolResult(sessionId, toolResult)
          })
        }

        // Extract permission requests
        if (message.type === 'permission_request' || message.metadata?.has_permission_requests) {
          handlePermissionRequest(sessionId, message)
        }

        // Extract permission responses (for restoring AskUserQuestion answers)
        if (message.type === 'permission_response') {
          handlePermissionResponse(sessionId, {
            request_id: message.metadata?.request_id,
            decision: message.metadata?.decision,
            updated_input: message.metadata?.updated_input,
            reasoning: message.metadata?.reasoning,
            applied_updates: message.metadata?.applied_updates
          })
        }

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
        }
      })
    },
    { deep: true }
  )

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
    handleToolCall,  // Issue #324: unified tool call handler
    handleToolUse,
    handlePermissionRequest,
    handlePermissionResponse,
    handleToolResult,
    toggleToolExpansion,
    clearMessages,

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
    backendToolStates: readonly(backendToolStates)
  }
})
