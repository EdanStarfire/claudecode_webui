import { defineStore } from 'pinia'
import { ref, computed, readonly, watch } from 'vue'
import { api } from '../utils/api'
import { useSessionStore } from './session'

/**
 * Message Store - Manages messages and tool calls per session
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
            handleToolUse(sessionId, toolUse)
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
   */
  function addMessage(sessionId, message) {
    if (!messagesBySession.value.has(sessionId)) {
      messagesBySession.value.set(sessionId, [])
    }

    const messages = messagesBySession.value.get(sessionId)
    messages.push(message)

    // Track tool use lifecycle for orphaned detection
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
   */
  function handleToolUse(sessionId, toolUseBlock) {
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
      timestamp: new Date().toISOString(),
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

      updateToolCall(sessionId, toolUseId, updates)
      console.log(`Permission ${permissionResponse.decision} for tool ${toolUseId}`)
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
   */
  function isToolUseOrphaned(sessionId, toolUseId) {
    return orphanedToolUses.value.get(sessionId)?.has(toolUseId) || false
  }

  /**
   * Get orphaned tool information
   */
  function getOrphanedInfo(sessionId, toolUseId) {
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
    clearOrphanedToolUses
  }
})
